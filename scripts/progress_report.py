"""Coverage report for the incremental download.

A multi-hour backfill needs an answer to "what is still missing?" that does not
depend on scrolling through logs. This walks the checkpoints and the CSVs on
disk and prints a mailbox x month grid, plus whatever is left to retry.

    python -m scripts.progress_report
    python -m scripts.progress_report --start 2016-01 --end 2025-12 --detail
"""

import argparse
import os
import sys
from collections import defaultdict

from config import output_dir
from core.checkpoints import load_checkpoint
from scripts.chunking import PHASE_METADATA, clamp_to_today, month_chunks

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Compact status glyphs for the grid.
MARK_DONE = "#"
MARK_PARTIAL = "!"
MARK_ERROR = "x"
MARK_PENDING = "."


def discover_mailboxes():
    """Mailbox directories present under output_dir()."""
    if not os.path.isdir(output_dir()):
        return []
    return sorted(
        name
        for name in os.listdir(output_dir())
        if os.path.isdir(os.path.join(output_dir(), name)) and "@" in name
    )


def count_rows(csv_path):
    try:
        with open(csv_path, newline="", encoding="utf-8") as handle:
            return max(0, sum(1 for _ in handle) - 1)
    except OSError:
        return 0


def month_row_counts(mailbox):
    """Rows on disk per 'YYYY-MM', read from the consolidated day CSVs."""
    counts = defaultdict(int)
    days = 0
    base = os.path.join(output_dir(), mailbox)
    if not os.path.isdir(base):
        return counts, days
    for entry in os.listdir(base):
        day_dir = os.path.join(base, entry)
        if not os.path.isdir(day_dir) or len(entry) != 10:
            continue
        day_csv = os.path.join(day_dir, f"{entry}.csv")
        if os.path.exists(day_csv):
            counts[entry[:7]] += count_rows(day_csv)
            days += 1
    return counts, days


def dead_letters(mailbox):
    """Pending per-message failures, keyed by dead-letter file."""
    directory = os.path.join(output_dir(), mailbox, ".checkpoints")
    results = {}
    if not os.path.isdir(directory):
        return results
    for name in sorted(os.listdir(directory)):
        if name.startswith("failed_") and name.endswith(".jsonl"):
            path = os.path.join(directory, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    lines = sum(1 for line in handle if line.strip())
            except OSError:
                lines = 0
            if lines:
                results[name] = lines
    return results


def labels_present(mailbox):
    return os.path.exists(os.path.join(output_dir(), mailbox, "labels.csv"))


def run(args):
    if args.mailboxes:
        mailboxes = [m.strip() for m in args.mailboxes.split(",") if m.strip()]
    elif args.mailboxes_file:
        with open(args.mailboxes_file, encoding="utf-8") as handle:
            mailboxes = [
                line.strip() for line in handle if line.strip() and not line.startswith("#")
            ]
    else:
        mailboxes = discover_mailboxes()
    if not mailboxes:
        print(f"No hay buzones con datos en {output_dir()}.")
        return 1

    end = clamp_to_today(args.end)
    chunks = month_chunks(args.start, end)
    years = sorted({chunk.year for chunk in chunks})

    print(f"Cobertura de metadata en {output_dir()}  ({args.start} → {end})")
    print(
        f"  {MARK_DONE} completo   {MARK_PARTIAL} con fallos   "
        f"{MARK_ERROR} error   {MARK_PENDING} pendiente\n"
    )

    header = "  ".join(f"{year}" for year in years)
    print(f"{'buzón':28s} {'labels':7s} {header}")
    print(f"{'':28s} {'':7s} " + "  ".join("EFMAMJJASOND" for _ in years))

    grand_total = 0
    totals_by_status = defaultdict(int)
    pending_work = []

    for mailbox in mailboxes:
        counts, _ = month_row_counts(mailbox)
        grand_total += sum(counts.values())
        cells = []
        for year in years:
            year_cells = []
            for month in range(1, 13):
                chunk = next((c for c in chunks if c.year == year and c.month == month), None)
                if chunk is None:
                    year_cells.append(" ")
                    continue
                state = load_checkpoint(mailbox, chunk.checkpoint_key(PHASE_METADATA))
                status = (state or {}).get("status")
                if status == "done":
                    mark = MARK_DONE
                elif status == "partial":
                    mark = MARK_PARTIAL
                    pending_work.append((mailbox, chunk.label, "fallos por mensaje"))
                elif status == "error":
                    mark = MARK_ERROR
                    pending_work.append((mailbox, chunk.label, (state or {}).get("error", "error")))
                else:
                    mark = MARK_PENDING
                    pending_work.append((mailbox, chunk.label, "sin descargar"))
                totals_by_status[mark] += 1
                year_cells.append(mark)
            cells.append("".join(year_cells))
        labels_mark = "sí" if labels_present(mailbox) else "NO"
        print(f"{mailbox:28s} {labels_mark:7s} " + "  ".join(cells))

    print()
    total_chunks = sum(totals_by_status.values())
    print(
        f"Chunks: {totals_by_status[MARK_DONE]}/{total_chunks} completos, "
        f"{totals_by_status[MARK_PARTIAL]} con fallos, "
        f"{totals_by_status[MARK_ERROR]} en error, "
        f"{totals_by_status[MARK_PENDING]} pendientes"
    )
    print(f"Filas de metadata en disco: {grand_total:,}")

    any_dead = False
    for mailbox in mailboxes:
        letters = dead_letters(mailbox)
        if letters:
            any_dead = True
            total = sum(letters.values())
            print(f"\nDead-letter en {mailbox}: {total} mensajes")
            if args.detail:
                for name, count in letters.items():
                    print(f"    {name}: {count}")
    if not any_dead:
        print("Dead-letters: ninguno pendiente")

    if pending_work and args.detail:
        print(f"\nPendiente ({len(pending_work)}):")
        for mailbox, label, reason in pending_work[:60]:
            print(f"  {mailbox:28s} {label}  {reason}")
        if len(pending_work) > 60:
            print(f"  ... y {len(pending_work) - 60} más")

    return 0 if not pending_work else 2


def main():
    parser = argparse.ArgumentParser(
        prog="progress_report", description="Cobertura de la descarga de metadata"
    )
    parser.add_argument("--start", default="2016-01", help="Mes inicial YYYY-MM")
    parser.add_argument("--end", default="2025-12", help="Mes final YYYY-MM")
    parser.add_argument("--mailboxes", help="Lista separada por comas (por defecto: todos)")
    parser.add_argument("--mailboxes-file", help="Fichero con un buzón por línea")
    parser.add_argument("--detail", action="store_true", help="Listar lo pendiente")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
