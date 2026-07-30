"""Module for handling CSV file reading."""

import csv
from typing import List, Optional, Tuple

from config_logger import get_app_logger

logger = get_app_logger()


def read_email_ids_from_metadata_csv(
    csv_file: str, mailbox: str
) -> List[Tuple[str, str, Optional[str]]]:
    """Reads (id, mailbox, date) from a per-day metadata CSV.

    The CSVs written by the metadata phase have no `mailbox` column — the
    mailbox is encoded in the directory path instead. Use this when the caller
    already knows which mailbox the file belongs to; use
    `read_email_ids_and_mailbox_from_csv` for the consolidated CSV, which does
    carry the column.
    """
    result = []
    with open(csv_file, "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or "id" not in reader.fieldnames:
            raise ValueError(f"{csv_file} no tiene columna 'id'")
        for row in reader:
            msg_id = row.get("id")
            if msg_id:
                result.append((msg_id, mailbox, row.get("date")))
    return result


def read_email_ids_and_mailbox_from_csv(
    csv_file: str,
) -> List[Tuple[str, str, Optional[str]]]:
    """Reads email IDs and mailbox from a CSV file and returns them as a list of tuples."""
    result = []
    with open(csv_file, "r", encoding="utf-8") as file:
        reader = csv.reader(file, delimiter=",")
        header = next(reader, None)
        if not header:
            return result
        try:
            id_idx = header.index("id")
            mailbox_idx = header.index("mailbox")
            date_idx = header.index("date") if "date" in header else None
        except ValueError as exc:
            raise ValueError("CSV must contain 'id' and 'mailbox' columns") from exc

        for row in reader:
            if len(row) > max(id_idx, mailbox_idx):
                if date_idx is not None and len(row) > date_idx:
                    result.append((row[id_idx], row[mailbox_idx], row[date_idx]))
                else:
                    result.append((row[id_idx], row[mailbox_idx], None))
    return result
