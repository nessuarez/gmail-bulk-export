"""This module provides functions to save to CSV files"""

import csv
import os
import threading
import uuid
from collections import defaultdict

from gmail_bulk_export.config_logger import get_app_logger
from gmail_bulk_export.core.file_handler import generate_base_dir
from gmail_bulk_export.core.utils import ensure_dir_exists

logger = get_app_logger()

# csv.field_size_limit defaults are fine for metadata, but subjects can be long.
csv.field_size_limit(10_000_000)

# Single source of truth for the metadata schema. `merge_csv_files` unions the
# columns it finds on disk with these, so adding a field here is enough to make
# it appear in days that were downloaded before the field existed.
METADATA_FIELDNAMES = [
    "id",
    "threadId",
    "labelIds",
    "sizeEstimate",
    "historyId",
    "internalDate",
    "deliveredTo",
    "subject",
    "from",
    "to",
    "cc",
    "bcc",
    "date",
    "contentType",
    "snippet",
]

# One lock per merged file. `process_emails` runs in several threads and two of
# them routinely land on the same date, so the read-modify-write below has to be
# serialized per destination file.
_merge_locks = defaultdict(threading.Lock)
_merge_locks_guard = threading.Lock()


def _lock_for(merged_file_path):
    with _merge_locks_guard:
        return _merge_locks[os.path.abspath(merged_file_path)]


def _read_rows(file_path):
    """Reads a CSV into (fieldnames, rows). Returns (None, []) if unreadable."""
    try:
        with open(file_path, mode="r", newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames is None:
                return None, []
            return list(reader.fieldnames), list(reader)
    except (OSError, csv.Error) as exc:
        logger.error("No se pudo leer %s: %s", file_path, exc)
        return None, []


def merge_csv_files(path, date):
    """
    Merges the per-batch CSV files of a day into the day's consolidated CSV.

    The merge is *incremental and idempotent*: the already existing `{date}.csv`
    is read first, the `{date}_*.csv` temporary files are folded on top of it,
    and rows are de-duplicated by their `id` column (last write wins). The
    result is written to a temporary file and moved into place atomically, so an
    interrupted run can never leave a truncated day behind.

    This is the primitive the whole incremental pipeline relies on: it is what
    makes re-running an overlapping date range safe.

    Args:
        path (str): The directory path where the CSV files are located.
        date (str): The date prefix used to identify the CSV files to be merged.

    Returns:
        path (str): The path to the merged CSV file.
    """
    merged_file_path = os.path.join(path, f"{date}.csv")

    with _lock_for(merged_file_path):
        temp_files = sorted(
            f for f in os.listdir(path) if f.startswith(date + "_") and f.endswith(".csv")
        )

        # Union rather than "first one wins": when a new column is added to the
        # schema, the pre-existing {date}.csv is read first and would otherwise
        # dictate the header, silently dropping the new column from every row
        # the current run just fetched.
        fieldnames: list[str] = []
        seen_fields = set()
        rows_by_id = {}
        rows_without_id = []

        sources = []
        if os.path.exists(merged_file_path):
            sources.append(merged_file_path)
        sources.extend(os.path.join(path, f) for f in temp_files)

        for source in sources:
            source_fieldnames, rows = _read_rows(source)
            for name in source_fieldnames or []:
                if name not in seen_fields:
                    seen_fields.add(name)
                    fieldnames.append(name)
            for row in rows:
                row_id = row.get("id")
                if row_id:
                    rows_by_id[row_id] = row
                else:
                    rows_without_id.append(row)

        if not fieldnames:
            # Nothing to merge (no existing file and no readable temp files).
            return merged_file_path

        merged_rows = list(rows_by_id.values()) + rows_without_id

        tmp_path = f"{merged_file_path}.tmp"
        with open(tmp_path, mode="w", newline="", encoding="utf-8") as merged_file:
            writer = csv.DictWriter(merged_file, fieldnames=fieldnames)
            writer.writeheader()
            for row in merged_rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})
        os.replace(tmp_path, merged_file_path)

        # Only drop the temporaries once the merged file is safely in place.
        for temp_file in temp_files:
            try:
                os.remove(os.path.join(path, temp_file))
            except OSError as exc:
                logger.warning("No se pudo borrar el temporal %s: %s", temp_file, exc)

    return merged_file_path


def save_emails_to_csv(username, emails_by_date, output_dir):
    """
    Saves emails to CSV files organized by date and merges them into a single file per date.
    Args:
        username (str): The username associated with the emails.
        emails_by_date (dict): A dictionary where the keys are dates (str)
                               and the values are lists of email dictionaries.
    The function performs the following steps:
        1. Creates a directory for each date under the user's directory.
        2. Generates a unique CSV file for each date with a unique suffix.
        3. Writes the emails to the CSV file with specified fieldnames.
        4. Merges all CSV files for the same date into a single file.
        5. Logs the number of emails processed for each date.

    Returns:
        int: number of email rows handed to the merge step.
    """
    written = 0
    for date, emails in emails_by_date.items():
        dir_path = generate_base_dir(username, date, output_dir)
        ensure_dir_exists(dir_path)
        unique_suffix = uuid.uuid4().hex
        file_path = os.path.join(dir_path, f"{date}_{unique_suffix}.csv")

        with open(file_path, mode="w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=METADATA_FIELDNAMES)
            writer.writeheader()
            writer.writerows(emails)

        merge_csv_files(dir_path, date)
        written += len(emails)
        logger.info("%s: %d emails", date, len(emails))

    return written


def save_labels_csv(labels, dir_path, file_name="labels"):
    """
    Save the Gmail labels to a CSV file.
    Args:
        labels (list): A list of Gmail labels.
        dir_path (str): The directory path where the CSV file will be saved.
        file_name (str): The name of the CSV file.
    The function writes the Gmail labels to a CSV file with the following fields:
        - id
        - name
        - type
    """
    if not labels:
        logger.warning("No labels to save.")
        return
    ensure_dir_exists(dir_path)
    file_path = os.path.join(dir_path, f"{file_name}.csv")
    fieldnames = [
        "id",
        "name",
        "type",
        "messageListVisibility",
        "labelListVisibility",
        "messagesTotal",
        "messagesUnread",
        "threadsTotal",
        "threadsUnread",
        "backgroundColor",
        "textColor",
    ]
    with open(file_path, mode="w+", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        for label in labels:
            writer.writerow(
                {
                    "id": label.get("id"),
                    "name": label.get("name"),
                    "type": label.get("type"),
                    "messageListVisibility": label.get("messageListVisibility"),
                    "labelListVisibility": label.get("labelListVisibility"),
                    "messagesTotal": label.get("messagesTotal"),
                    "messagesUnread": label.get("messagesUnread"),
                    "threadsTotal": label.get("threadsTotal"),
                    "threadsUnread": label.get("threadsUnread"),
                    "backgroundColor": label.get("color", {}).get("backgroundColor"),
                    "textColor": label.get("color", {}).get("textColor"),
                }
            )
    logger.info("Labels saved to %s", file_path)
