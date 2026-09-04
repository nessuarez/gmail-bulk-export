"""Module providing Gmail Email Body Downloader script."""

import argparse
import base64
import email
import gettext
import gzip
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from typing import List, Optional

from dateutil import parser as date_parser
from tqdm import tqdm

from gmail_bulk_export.auth.service import get_cached_gmail_service
from gmail_bulk_export.config import get_config, output_dir
from gmail_bulk_export.config_logger import get_app_logger
from gmail_bulk_export.core.attachment_handler import (
    generate_attachment_file_path,
    get_filtered_attachments,
    save_attachments,
)
from gmail_bulk_export.core.csv_handler import (
    read_email_ids_and_mailbox_from_csv,
    read_email_ids_from_metadata_csv,
)
from gmail_bulk_export.core.email_body_handler import process_email_raw_response
from gmail_bulk_export.core.file_handler import generate_email_file_path
from gmail_bulk_export.core.rate_limit import gmail_retry, pace
from gmail_bulk_export.core.utils import ensure_directory, generate_base_dir, parse_email_date

# Configurar gettext
gettext.bindtextdomain("messages", "locale")
gettext.textdomain("messages")
_ = gettext.gettext

logger = get_app_logger()


@gmail_retry
def _fetch_raw_message(service, msg_id: str, mailbox: str = None) -> dict:
    """Fetches one message in `raw` form, retrying only transient failures."""
    pace(1, name=mailbox)
    return service.users().messages().get(userId="me", id=msg_id, format="raw").execute()


def process_email(
    username: str,
    msg_id: str,
    allowed_attachment_types: Optional[List[str]] = None,
    save_attachment_files: bool = False,
    on_failure=None,
) -> bool:
    """Fetches one email's raw content, extracts the body and saves it gzipped.

    Returns:
        bool: True if the body was written.
    """
    # Per-thread cached service: building one per message meant a full OAuth
    # token refresh plus a discovery build for every single email.
    service = get_cached_gmail_service(username)
    try:
        response = _fetch_raw_message(service, msg_id, mailbox=username)

        email_data: dict = process_email_raw_response(
            response, allowed_attachment_types, mailbox=username
        )

        file_path = generate_email_file_path(
            username, msg_id, email_data.get("date", None), output_dir()
        )
        ensure_directory(os.path.dirname(file_path))

        # Write to a temporary file first: a half-written .jsonl.gz would be
        # indistinguishable from a complete one on the next resume.
        tmp_path = f"{file_path}.part"
        with gzip.open(tmp_path, "wt", encoding="utf-8") as gzfile:
            json.dump(email_data, gzfile, ensure_ascii=False)
        os.replace(tmp_path, file_path)

        logger.info(_(f"{username}: Email {msg_id} guardado en {file_path}"))

        # Save attachments
        if save_attachment_files:
            attachments = get_filtered_attachments(
                email.message_from_string(
                    base64.urlsafe_b64decode(response.get("raw")).decode("utf-8")
                ),
                allowed_attachment_types,
            )
            if attachments:
                # generate_attachment_file_path takes (base_dir, msg_id); the
                # date-partitioned directory is resolved separately.
                base_dir = generate_base_dir(username, email_data.get("date", None), output_dir())
                attachment_file_path = generate_attachment_file_path(base_dir, msg_id)
                save_attachments(attachment_file_path, attachments, msg_id)
        return True

    except Exception as e:
        logger.error(_(f"Error descargando email {msg_id}: {e}"))
        if on_failure is not None:
            on_failure({"msg_id": msg_id, "mailbox": username, "error": str(e)})
        return False


def download_email_bodies(
    username: str,
    msg_ids: list[tuple[str, str | None]],
    allowed_attachment_types: list = None,
    *,
    save_attachment_files: bool = False,
    overwrite: bool = False,
) -> dict:
    """Fetches the body of emails for a given user and saves them to a JSON file.

    `overwrite=True` re-downloads and replaces bodies already on disk instead
    of skipping them — needed once after a body schema change, since the skip
    check is by file existence and cannot otherwise tell an old schema from a
    current one.

    Returns:
        dict: {"downloaded": int, "skipped": int, "failed": int, "failures": list}
    """
    logger.info(_(f"{username}: Iniciando descarga del cuerpo de {len(msg_ids)} emails"))

    # Filter out already downloaded emails
    emails_to_download = []
    skipped = 0
    for msg_id, msg_date in msg_ids:
        date_str = parse_email_date(msg_date, logger) if msg_date else None
        file_path = generate_email_file_path(username, msg_id, date_str, output_dir())
        if overwrite or not os.path.exists(file_path):
            emails_to_download.append((msg_id, msg_date))
        else:
            skipped += 1
            logger.debug(_(f"El archivo {file_path} ya existe. Omitiendo descarga."))

    logger.info(
        _(f"{username}: Emails a descargar: {len(emails_to_download)} (omitidos: {skipped})")
    )

    failures = []
    failures_lock = threading.Lock()

    def record_failure(failure):
        with failures_lock:
            failures.append(failure)

    max_workers = int(get_config("max_workers", 2) or 2)
    downloaded = 0

    with tqdm(total=len(emails_to_download), desc=f"{username}: cuerpos", unit=" emails") as pbar:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    process_email,
                    username,
                    msg_id,
                    allowed_attachment_types,
                    save_attachment_files,
                    record_failure,
                )
                for msg_id, _msg_date in emails_to_download
            ]

            for future in as_completed(futures):
                if future.result():
                    downloaded += 1
                pbar.update(1)

        pbar.close()

    logger.info(
        _(f"{username}: Descarga completada. {downloaded} descargados, {len(failures)} fallos")
    )
    return {
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": len(failures),
        "failures": failures,
    }


def retrieve_emails_data(
    args: argparse.Namespace, logger: logging.Logger
) -> list[tuple[str, str, str | None]]:
    """
    Retrieve email data tuples based on input arguments.

    This function determines how to obtain email IDs and associated mailbox/user information
    according to the provided arguments. It supports three main modes of operation:

    1. If a CSV file is provided (`args.csv_file`), reads email IDs and mailbox
        information from the CSV.
    2. If a username and a date range (`args.start_date`, `args.end_date`,
        `args.username`) are provided, iterates through the date range, reading
        email IDs from the per-day metadata CSVs under output_dir().
    3. If a username and a list of email IDs (`args.username`, `args.email_ids`)
        are provided, generates tuples manually with the username and each email ID.

    Args:
         args (argparse.Namespace): Parsed command-line arguments containing input options.
         logger (logging.Logger): Logger instance for logging warnings and information.

    Returns:
         list[tuple[str, str, str | None]]: tuples of (email_id, mailbox, date).
    """
    retrieved_email_data: list[tuple[str, str, str | None]] = []
    # Determinar cómo obtener los email_ids según los argumentos de entrada
    if args.csv_file:
        # Caso 1: Si se proporciona un archivo CSV, leer los IDs y mailbox desde el CSV
        retrieved_email_data = read_email_ids_and_mailbox_from_csv(args.csv_file)
    elif args.start_date and args.end_date and args.username:
        # Caso 2: fechas + username, buscar los ficheros por ruta y extraer los IDs
        start_date = date_parser.parse(args.start_date)
        end_date = date_parser.parse(args.end_date)
        date_range = [
            (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range((end_date - start_date).days + 1)
        ]
        retrieved_email_data = []
        for date in date_range:
            # The metadata CSVs live under output_dir(); without the prefix this
            # branch never found anything and the command exited with code 1.
            csv_path = os.path.join(output_dir(), args.username, date, f"{date}.csv")
            if os.path.exists(csv_path):
                retrieved_email_data.extend(
                    read_email_ids_from_metadata_csv(csv_path, args.username)
                )
            else:
                logger.debug(_(f"No se encontró el archivo: {csv_path}"))
    elif args.username and args.email_ids:
        # Caso 3: Si se proporcionan username y email_ids, generar la tupla manualmente
        retrieved_email_data = [(email_id, args.username, None) for email_id in args.email_ids]
    else:
        retrieved_email_data = []
    return retrieved_email_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=_("Gmail Email Body Downloader"))
    parser.add_argument(
        "--username",
        help=_("Nombre de usuario para organizar la salida"),
    )
    parser.add_argument("--start_date", help="Fecha de inicio en formato YYYYMMDD")
    parser.add_argument("--end_date", help="Fecha de fin en formato YYYYMMDD")
    parser.add_argument(
        "--email_ids",
        nargs="+",
        required=False,
        help=_("Lista de IDs de emails a descargar"),
    )
    parser.add_argument(
        "--csv-file",
        help=_("Archivo CSV con IDs de emails a descargar"),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help=_("Activar modo de depuración"),
    )
    parser.add_argument(
        "--allowed_attachment_types",
        nargs="+",
        default=[
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
        ],
        help=_("Lista de tipos de adjuntos permitidos (ej: application/pdf image/png)"),
    )

    args = parser.parse_args()

    logger.info("args: %s", args)

    if args.debug:
        logger.setLevel("DEBUG")
        # Set logger console handler level to DEBUG if debug mode is enabled
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.DEBUG)
    else:
        logger.setLevel("INFO")

    emails_info = retrieve_emails_data(args, logger)

    # Print number of emails to download
    logger.info(_(f"Obteniendo {len(emails_info)} emails"))
    if not emails_info:
        logger.error(_("No se encontraron emails para descargar."))
        exit(1)

    # Agrupamos los emails por mailbox
    email_ids_by_mailbox = {}
    for email_id, mailbox, date in emails_info:
        if mailbox not in email_ids_by_mailbox:
            email_ids_by_mailbox[mailbox] = []
        email_ids_by_mailbox[mailbox].append((email_id, date))

    # Descargar el cuerpo de los emails para cada mailbox
    for mailbox, values in email_ids_by_mailbox.items():
        logger.info(_(f"Descargando emails para el mailbox: {mailbox}"))
        download_email_bodies(mailbox, values, args.allowed_attachment_types)
