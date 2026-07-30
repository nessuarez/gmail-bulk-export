"""Incremental, resumable metadata download across many mailboxes.

Runs `gmail-bulk-export metadata` month by month, in-process, recording a checkpoint
per (mailbox, month) so an interrupted run picks up exactly where it stopped.

    python -m scripts.download_metadata --mailboxes-file mailboxes.txt \
        --start 2016-01 --end 2025-12 --priority 2024,2023,2025

Design notes:

* Mailboxes that do not exist or lack domain-wide delegation are reported and
  skipped, not treated as fatal — a partial roster still produces useful data.
* Chunks are only marked done when they complete with zero per-message
  failures, so a re-run retries anything that was silently dropped.
* Everything runs in-process. Shelling out to `gmail-bulk-export` once per chunk would
  pay module import plus a token refresh over a thousand times.
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import List

from auth.service import AuthenticationError, check_authentication
from cli.bulk_emails_downloader import fetch_email_metadata
from cli.gmail_labels_downloader import get_users_label
from config import output_dir
from config_logger import get_app_logger
from core.checkpoints import load_checkpoint, save_checkpoint
from scripts.chunking import (
    PHASE_METADATA,
    clamp_to_today,
    iter_plan,
    month_chunks,
    order_by_priority,
)

logger = get_app_logger()

# The Windows console defaults to cp1252, which cannot encode the arrows and
# check marks used in the progress output.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Abort rather than hammer the API if everything is failing (expired key,
# revoked delegation, project-wide quota exhaustion).
MAX_CONSECUTIVE_FAILURES = 10


def read_mailboxes(args) -> List[str]:
    """Resolves the mailbox roster from --mailboxes or --mailboxes-file."""
    mailboxes: List[str] = []
    if args.mailboxes:
        mailboxes.extend(part.strip() for part in args.mailboxes.split(",") if part.strip())
    if args.mailboxes_file:
        with open(args.mailboxes_file, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    mailboxes.append(line)
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(mailboxes))


def verify_mailboxes(mailboxes: List[str]) -> List[str]:
    """Returns the mailboxes we can actually impersonate."""
    usable = []
    for mailbox in mailboxes:
        try:
            if check_authentication(mailbox):
                usable.append(mailbox)
                print(f"  ✓ {mailbox}")
            else:
                print(f"  ✗ {mailbox} — autenticación rechazada, se omite")
        except AuthenticationError as exc:
            print(f"  ✗ {mailbox} — {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - a bad mailbox must not abort the roster
            print(f"  ✗ {mailbox} — error inesperado: {exc}", file=sys.stderr)
    return usable


def download_labels(mailbox: str) -> bool:
    """Writes output/<mailbox>/labels.csv, where the EDA notebook looks for it.

    The `labels` subcommand defaults to output_dir() root, which means ten
    mailboxes would leave a single file from whichever ran last.
    """
    target_dir = os.path.join(output_dir(), mailbox)
    labels_path = os.path.join(target_dir, "labels.csv")
    if os.path.exists(labels_path):
        return True
    try:
        labels = get_users_label(mailbox, target_dir, "labels")
        if labels:
            print(f"  · labels: {len(labels)} → {labels_path}")
            return True
        print(f"  · labels: ninguna etiqueta devuelta para {mailbox}")
    except Exception as exc:  # noqa: BLE001
        logger.error("No se pudieron descargar labels de %s: %s", mailbox, exc)
        print(f"  · labels: ERROR para {mailbox}: {exc}", file=sys.stderr)
    return False


def run(args) -> int:
    mailboxes = read_mailboxes(args)
    if not mailboxes:
        print("No hay buzones. Usa --mailboxes o --mailboxes-file.", file=sys.stderr)
        return 2
    if args.only:
        mailboxes = [mailbox for mailbox in mailboxes if mailbox == args.only]
        if not mailboxes:
            print(f"'{args.only}' no está en la lista de buzones.", file=sys.stderr)
            return 2

    end = clamp_to_today(args.end)
    if end != args.end:
        print(f"Fin ajustado de {args.end} a {end} (no se descargan meses futuros).")

    chunks = month_chunks(args.start, end)
    priority_years = (
        [int(y) for y in args.priority.split(",") if y.strip()] if args.priority else []
    )
    chunks = order_by_priority(chunks, priority_years)

    print(f"\nVerificando acceso a {len(mailboxes)} buzones:")
    if args.skip_auth_check:
        usable = mailboxes
        print("  (omitido por --skip-auth-check)")
    else:
        usable = verify_mailboxes(mailboxes)
    if not usable:
        print("\nNingún buzón accesible. Nada que hacer.", file=sys.stderr)
        return 1

    plan = list(iter_plan(usable, chunks))
    jobs = args.jobs if args.jobs > 0 else len(usable)
    jobs = max(1, min(jobs, len(usable)))
    print(
        f"\nPlan: {len(usable)} buzones x {len(chunks)} meses = {len(plan)} chunks"
        f" ({args.start} → {end}, prioridad: {args.priority or 'ninguna'})"
    )
    print(
        f"Paralelismo: {jobs} buzones a la vez. El cupo de Gmail (250 unidades/s) "
        f"es por buzón, así que no compiten entre sí."
    )

    if args.dry_run:
        for mailbox, chunk in plan[:40]:
            state = load_checkpoint(mailbox, chunk.checkpoint_key(PHASE_METADATA))
            status = (state or {}).get("status", "pendiente")
            print(
                f"  {mailbox:32s} {chunk.label}  [{status}]  {chunk.query_start}..{chunk.query_end}"
            )
        if len(plan) > 40:
            print(f"  ... y {len(plan) - 40} más")
        return 0

    totals = {"done": 0, "skipped": 0, "failed": 0, "messages": 0}
    totals_lock = threading.Lock()
    started_at = time.monotonic()
    progress = {"completed": 0}

    def bump(**deltas):
        with totals_lock:
            for key, value in deltas.items():
                totals[key] += value
            return dict(totals)

    def process_mailbox(mailbox):
        """Recorre en serie los meses de un buzón. Un buzón por hilo."""
        labels_pending = True
        consecutive_failures = 0

        for chunk in chunks:
            key = chunk.checkpoint_key(PHASE_METADATA)
            state = load_checkpoint(mailbox, key)
            if state and state.get("status") == "done" and not args.force:
                bump(skipped=1)
                continue

            if labels_pending:
                download_labels(mailbox)
                labels_pending = False

            with totals_lock:
                progress["completed"] += 1
                index = progress["completed"]
                seen = totals["messages"]
            elapsed = time.monotonic() - started_at
            print(
                f"[{index}/{len(plan)}] {mailbox} {chunk.label} "
                f"({chunk.query_start}..{chunk.query_end}) "
                f"[{seen} msgs, {elapsed / 60:.0f} min]",
                flush=True,
            )

            try:
                result = fetch_email_metadata(mailbox, chunk.date_range)
            except Exception as exc:  # noqa: BLE001 - un mes malo no tumba el buzón
                logger.exception("Fallo el chunk %s %s", mailbox, chunk.label)
                print(f"    ERROR {mailbox} {chunk.label}: {exc}", file=sys.stderr)
                save_checkpoint(
                    mailbox,
                    key,
                    {
                        "status": "error",
                        "error": str(exc),
                        "updated": datetime.now(timezone.utc).isoformat(),
                    },
                )
                bump(failed=1)
                consecutive_failures += 1
                # El corte es por buzón: una credencial revocada en uno no debe
                # abortar los otros ocho, que van perfectamente.
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(
                        f"\n{mailbox}: {consecutive_failures} fallos consecutivos, "
                        f"se abandona este buzón.",
                        file=sys.stderr,
                    )
                    return
                continue

            consecutive_failures = 0
            processed = result.get("processed", 0)
            failed = result.get("failed", 0)

            # Un chunk con mensajes perdidos queda incompleto para que se reintente.
            status = "done" if failed == 0 else "partial"
            save_checkpoint(
                mailbox,
                key,
                {
                    "status": status,
                    "processed": processed,
                    "failed": failed,
                    "dead_letter": result.get("dead_letter"),
                    "query": list(chunk.date_range),
                    "updated": datetime.now(timezone.utc).isoformat(),
                },
            )
            if status == "done":
                bump(done=1, messages=processed)
            else:
                bump(failed=1, messages=processed)
                print(
                    f"    {mailbox} {chunk.label}: {processed} mensajes, "
                    f"{failed} fallos → se reintentará",
                    flush=True,
                )

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {executor.submit(process_mailbox, mailbox): mailbox for mailbox in usable}
        for future in as_completed(futures):
            mailbox = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001
                logger.exception("El buzón %s terminó con excepción", mailbox)
                print(f"  {mailbox}: ERROR fatal — {exc}", file=sys.stderr)
            else:
                print(f"  {mailbox}: terminado", flush=True)

    elapsed = time.monotonic() - started_at
    print(
        f"\nResumen: {totals['done']} chunks completos, {totals['skipped']} ya hechos, "
        f"{totals['failed']} con fallos, {totals['messages']} mensajes nuevos, "
        f"{elapsed / 60:.1f} min"
    )
    summary_path = os.path.join(output_dir(), "_last_metadata_run.json")
    os.makedirs(output_dir(), exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "mailboxes": usable,
                "range": [args.start, end],
                "totals": totals,
                "elapsed_minutes": round(elapsed / 60, 1),
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
    return 0 if totals["failed"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="download_metadata",
        description="Descarga incremental y reanudable de metadata de Gmail",
    )
    parser.add_argument("--mailboxes", help="Lista de buzones separada por comas")
    parser.add_argument("--mailboxes-file", help="Fichero con un buzón por línea")
    parser.add_argument("--start", default="2016-01", help="Mes inicial YYYY-MM")
    parser.add_argument("--end", default="2025-12", help="Mes final YYYY-MM")
    parser.add_argument(
        "--priority",
        default="2024,2023,2025",
        help="Años a descargar primero, separados por comas",
    )
    parser.add_argument("--only", help="Procesar solo este buzón de la lista")
    parser.add_argument(
        "--jobs",
        type=int,
        default=0,
        help=(
            "Buzones en paralelo (0 = todos). El cupo de Gmail es por buzón, "
            "así que esto multiplica el ritmo; más credenciales no."
        ),
    )
    parser.add_argument(
        "--force", action="store_true", help="Reprocesar chunks ya marcados como done"
    )
    parser.add_argument("--dry-run", action="store_true", help="Mostrar el plan sin descargar nada")
    parser.add_argument(
        "--skip-auth-check",
        action="store_true",
        help="No verificar los buzones antes de empezar",
    )
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
