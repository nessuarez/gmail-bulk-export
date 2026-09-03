"""Incremental download of email bodies (and optionally attachment binaries).

Phase 3/4 of the pipeline. Reads the consolidated metadata produced by
`python -m core.load_metadatas`, slices it by mailbox and month, and downloads
the bodies month by month with a checkpoint per unit — the same resumable shape
as the metadata phase.

    # dry run: how much work is left, and how big it is
    python -m scripts.download_payloads --year 2024 --dry-run

    # bodies only (recommended first pass)
    python -m scripts.download_payloads --year 2024

    # bodies plus attachment binaries (much more disk)
    python -m scripts.download_payloads --year 2024 --with-attachments

Bodies are written as `output/<mailbox>/<YYYY-MM-DD>/<msg_id>.jsonl.gz`, and
attachments as `<msg_id>_attachments.gz` alongside them. Both are skipped if
already present, so re-running only fetches what is missing.
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import pandas as pd

from gmail_bulk_export.cli.gmail_payloads_downloader import download_email_bodies
from gmail_bulk_export.config import output_dir
from gmail_bulk_export.config_logger import get_app_logger
from gmail_bulk_export.core.checkpoints import load_checkpoint, save_checkpoint

logger = get_app_logger()

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

PHASE_BODIES = "bodies"
PHASE_BODIES_ATTACH = "bodies_attach"

DEFAULT_ATTACHMENT_TYPES = [
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
]

CONSOLIDATED = os.path.join(output_dir(), "emails_with_mailboxes.csv")


def load_index(path: str) -> pd.DataFrame:
    """Loads the consolidated metadata and derives the month partition key."""
    if not os.path.exists(path):
        raise SystemExit(f"No existe {path}.\nEjecuta primero: python -m core.load_metadatas")
    wanted = {"id", "mailbox", "date", "internalDate"}
    df = pd.read_csv(path, dtype=str, usecols=lambda column: column in wanted)
    # internalDate (epoch ms) is unambiguous; the Date header is not always parseable.
    stamps = pd.to_datetime(
        pd.to_numeric(df.get("internalDate"), errors="coerce"), unit="ms", errors="coerce"
    )
    fallback = pd.to_datetime(df.get("date"), errors="coerce", utc=True, format="mixed")
    if fallback is not None:
        stamps = stamps.fillna(fallback.dt.tz_localize(None))
    df["month"] = stamps.dt.strftime("%Y-%m")
    return df.dropna(subset=["id", "mailbox", "month"])


def partition(df: pd.DataFrame, years, mailboxes):
    """Groups work into (mailbox, month) units, newest month first."""
    if years:
        df = df[df["month"].str[:4].isin({str(y) for y in years})]
    if mailboxes:
        df = df[df["mailbox"].isin(set(mailboxes))]

    units = defaultdict(list)
    for mailbox, month, msg_id, date in zip(
        df["mailbox"], df["month"], df["id"], df.get("date", df["month"])
    ):
        units[(mailbox, month)].append((msg_id, date))
    return dict(sorted(units.items(), key=lambda item: (item[0][0], item[0][1])))


def unit_key(month: str, with_attachments: bool) -> str:
    phase = PHASE_BODIES_ATTACH if with_attachments else PHASE_BODIES
    return f"{phase}_{month.replace('-', '')}"


def run(args) -> int:
    df = load_index(args.index)
    years = [int(y) for y in args.year.split(",")] if args.year else []
    mailboxes = [m.strip() for m in args.mailbox.split(",")] if args.mailbox else []
    units = partition(df, years, mailboxes)

    if not units:
        print("No hay nada que coincida con el filtro.")
        return 1

    total_messages = sum(len(v) for v in units.values())
    print(
        f"{len(units)} unidades (buzón x mes), {total_messages:,} mensajes en el índice"
        f"{' + adjuntos' if args.with_attachments else ''}"
    )

    if args.dry_run:
        for (mailbox, month), messages in list(units.items())[:60]:
            state = load_checkpoint(mailbox, unit_key(month, args.with_attachments))
            status = (state or {}).get("status", "pendiente")
            print(f"  {mailbox:28s} {month}  {len(messages):6,} msgs  [{status}]")
        if len(units) > 60:
            print(f"  ... y {len(units) - 60} unidades más")
        print(
            "\nEstimación muy aproximada: los cuerpos rondan 30-80 KB comprimidos por email"
            f" → {total_messages * 55 / 1_000_000:.1f} GB para {total_messages:,} mensajes."
        )
        return 0

    attachment_types = None if args.all_attachment_types else DEFAULT_ATTACHMENT_TYPES
    totals = {"downloaded": 0, "skipped": 0, "failed": 0}
    started_at = time.monotonic()

    for index, ((mailbox, month), messages) in enumerate(units.items(), start=1):
        key = unit_key(month, args.with_attachments)
        state = load_checkpoint(mailbox, key)
        if state and state.get("status") == "done" and not args.force:
            totals["skipped"] += state.get("downloaded", 0)
            continue

        elapsed = (time.monotonic() - started_at) / 60
        print(
            f"[{index}/{len(units)}] {mailbox} {month} — {len(messages):,} mensajes "
            f"[{totals['downloaded']} hechos, {elapsed:.0f} min]"
        )

        try:
            result = download_email_bodies(
                mailbox,
                messages,
                attachment_types,
                save_attachment_files=args.with_attachments,
            )
        except Exception as exc:  # noqa: BLE001 - one bad month must not kill the run
            logger.exception("Fallo la unidad %s %s", mailbox, month)
            print(f"    ERROR: {exc}", file=sys.stderr)
            save_checkpoint(mailbox, key, {"status": "error", "error": str(exc)})
            continue

        totals["downloaded"] += result["downloaded"]
        totals["skipped"] += result["skipped"]
        totals["failed"] += result["failed"]

        if result["failures"]:
            path = os.path.join(output_dir(), mailbox, ".checkpoints", f"failed_{key}.jsonl")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                for failure in result["failures"]:
                    handle.write(json.dumps(failure, ensure_ascii=False) + "\n")

        save_checkpoint(
            mailbox,
            key,
            {
                "status": "done" if result["failed"] == 0 else "partial",
                "downloaded": result["downloaded"],
                "skipped": result["skipped"],
                "failed": result["failed"],
                "updated": datetime.now(timezone.utc).isoformat(),
            },
        )
        print(
            f"    {result['downloaded']} descargados, {result['skipped']} ya estaban, "
            f"{result['failed']} fallos"
        )

    elapsed = (time.monotonic() - started_at) / 60
    print(
        f"\nResumen: {totals['downloaded']} descargados, {totals['skipped']} ya estaban, "
        f"{totals['failed']} fallos, {elapsed:.1f} min"
    )
    return 0 if totals["failed"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="download_payloads",
        description="Descarga incremental de cuerpos de email (y adjuntos)",
    )
    parser.add_argument("--index", default=CONSOLIDATED, help="CSV consolidado de metadata")
    parser.add_argument("--year", help="Años a procesar, separados por comas (ej: 2024,2023)")
    parser.add_argument("--mailbox", help="Buzones a procesar, separados por comas")
    parser.add_argument(
        "--with-attachments",
        action="store_true",
        help="Guardar también los binarios de los adjuntos",
    )
    parser.add_argument(
        "--all-attachment-types",
        action="store_true",
        help="No filtrar adjuntos por MIME type",
    )
    parser.add_argument("--force", action="store_true", help="Reprocesar unidades ya hechas")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar el plan sin descargar")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
