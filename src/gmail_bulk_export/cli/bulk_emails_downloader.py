"""Bulk Email Downloader for Gmail using Gmail API v1."""

import argparse
import json
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dateutil import parser as date_parser
from tqdm import tqdm

from gmail_bulk_export.auth.service import get_cached_gmail_service, get_gmail_service
from gmail_bulk_export.config import get_config, output_dir
from gmail_bulk_export.config_logger import get_app_logger
from gmail_bulk_export.core.rate_limit import gmail_retry, is_retryable, pace
from gmail_bulk_export.core.save_to_csv import save_emails_to_csv
from gmail_bulk_export.core.utils import parse_email_date

logger = get_app_logger()

# messages.list and messages.get both cost 5 Gmail quota units.
_LIST_COST = 1

# How many times a batch will re-issue its rate-limited sub-requests.
MAX_BATCH_ROUNDS = 6


@gmail_retry
def run_batch_requests(batch):
    """
    Executes a batch request. Retries transient failures (429/5xx/quota 403).

    Args:
        batch: An object representing a batch request. It should have an `execute`
        method that performs the batch operation.
    """
    batch.execute()


@gmail_retry
def fetch_emails_list(service, date_range, next_page_token=None, mailbox=None):
    """Fetches a list of emails from Gmail service.
    Date range should be a tuple of two strings in the format 'YYYY-MM-DD'.

    Note that Gmail's `before:` operator is exclusive, so the caller is expected
    to pass an end date one day past the range it actually wants.
    """
    start_date = date_parser.parse(date_range[0])
    end_date = date_parser.parse(date_range[1])
    query = f"after:{start_date.strftime('%Y-%m-%d')} before:{end_date.strftime('%Y-%m-%d')}"
    pace(_LIST_COST, name=mailbox)
    if next_page_token:
        response = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=500, pageToken=next_page_token)
            .execute()
        )
    else:
        response = service.users().messages().list(userId="me", q=query, maxResults=500).execute()

    return response


def _dead_letter_path(username, date_range):
    """Where per-message failures for a chunk are recorded."""
    directory = os.path.join(output_dir(), username, ".checkpoints")
    os.makedirs(directory, exist_ok=True)
    chunk = f"{date_range[0]}_{date_range[1]}".replace("-", "")
    return os.path.join(directory, f"failed_metadata_{chunk}.jsonl")


def _record_failures(path, failures):
    """Appends per-message failures so a chunk is never silently incomplete."""
    if not failures:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for failure in failures:
            handle.write(json.dumps(failure, ensure_ascii=False) + "\n")


def _clear_dead_letter(username, date_range):
    """Removes a chunk's dead-letter file after a clean run."""
    path = _dead_letter_path(username, date_range)
    if os.path.exists(path):
        try:
            os.remove(path)
            logger.info("Dead-letter resuelto y eliminado: %s", path)
        except OSError as exc:
            logger.warning("No se pudo borrar %s: %s", path, exc)


# Fase 1: Obtener solo metadata de emails
def fetch_email_metadata(username, date_range):
    """Fetches email metadata for a given date range and saves it to a CSV file.

    Returns:
        dict: {"processed": int, "failed": int, "dead_letter": str | None}
    """
    service = get_gmail_service(username)

    logger.info(
        "%s: Iniciando análisis de emails desde %s hasta %s",
        username,
        date_range[0],
        date_range[1],
    )
    return fetch_email_messages(service, date_range, username)


def fetch_email_messages(service, date_range, username):
    """Fetches email messages for a given date range.

    Returns:
        dict: {"processed": int, "failed": int, "dead_letter": str | None}
    """
    max_workers = int(get_config("max_workers", 5) or 5)
    batch_size = int(get_config("batch_size", 100) or 100)

    failures = []
    failures_lock = threading.Lock()

    def collect_failures(new_failures):
        if not new_failures:
            return
        with failures_lock:
            failures.extend(new_failures)

    response = fetch_emails_list(service, date_range, mailbox=username)
    emails = response.get("messages", [])
    processed = 0

    # disable=None => tqdm se calla si la salida no es una terminal. Con nueve
    # buzones en paralelo escribiendo a un log, las barras lo hacían ilegible.
    with tqdm(
        total=len(emails), desc="Obteniendo mensajes", unit=" mensajes", disable=None
    ) as pbar:
        pbar.update(len(emails))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            # Procesar la primera página de mensajes
            futures.append(
                executor.submit(
                    process_emails, service, emails, username, batch_size, collect_failures
                )
            )
            while "nextPageToken" in response:
                response = fetch_emails_list(
                    service, date_range, response["nextPageToken"], mailbox=username
                )

                emails = response.get("messages", [])
                futures.append(
                    executor.submit(
                        process_emails,
                        service,
                        emails,
                        username,
                        batch_size,
                        collect_failures,
                    )
                )
                pbar.total += len(emails)
                pbar.update(len(emails))

            # Draining outside the pagination loop is what lets the pages
            # actually overlap; draining inside it serialized every page.
            for future in as_completed(futures):
                emails_by_date = future.result()
                processed += sum(len(rows) for rows in emails_by_date.values())

        logger.info(
            "%s: Procesados %d emails en total entre %s y %s (listados %d, fallos %d)",
            username,
            processed,
            date_range[0],
            date_range[1],
            pbar.total,
            len(failures),
        )

        pbar.close()

    dead_letter = None
    if failures:
        dead_letter = _dead_letter_path(username, date_range)
        _record_failures(dead_letter, failures)
        logger.warning(
            "%s: %d mensajes fallaron entre %s y %s. Registrados en %s",
            username,
            len(failures),
            date_range[0],
            date_range[1],
            dead_letter,
        )
    else:
        # The chunk completed cleanly, so any failures recorded by a previous
        # attempt have since been recovered. Leaving the file behind would make
        # progress_report keep reporting work that no longer exists.
        _clear_dead_letter(username, date_range)

    return {"processed": processed, "failed": len(failures), "dead_letter": dead_letter}


def process_emails(service, emails, username, batch_size=100, on_failures=None) -> dict:
    """
    Processes a list of emails by fetching their metadata and saving the results to a CSV file.

    Args:
        service (googleapiclient.discovery.Resource): The Gmail API service instance.
        emails (list): A list of email message objects to process.
        username (str): The username of the email account being processed.
        batch_size (int, optional): The number of emails to process in each batch. Defaults to 100.
        on_failures (callable, optional): Receives the list of per-message failures.

    Returns:
        dict: emails keyed by date string.
    """
    emails_by_date = {}
    # Gmail caps batch requests at 100 sub-requests, and separately limits how
    # many requests a single user may have in flight at once.
    batch_size = max(1, min(int(batch_size), 100))

    # `service` belongs to the caller's thread; httplib2 is not thread-safe, so
    # each worker resolves its own (cached) service instead of sharing this one.
    service = get_cached_gmail_service(username)

    msg_ids = [message["id"] for message in emails]
    failures = _fetch_metadata_with_retries(service, msg_ids, username, emails_by_date, batch_size)

    save_emails_to_csv(username, emails_by_date, output_dir())
    if on_failures is not None:
        on_failures(failures)
    return emails_by_date


def _fetch_metadata_with_retries(
    service, msg_ids, username, emails_by_date, batch_size, max_rounds=MAX_BATCH_ROUNDS
):
    """Fetches metadata for `msg_ids`, retrying sub-requests that Gmail rejected.

    A 429 raised *inside* a batch is handed to the per-request callback rather
    than raised from `batch.execute()`, so the retry decorator on the execute
    call never sees it. Without this loop those messages are simply absent from
    the CSV — which is precisely how a "successful" run loses a third of a month.
    """
    pending = list(msg_ids)
    failures = []

    for round_number in range(max_rounds):
        if not pending:
            break

        round_failures = []
        with tqdm(
            total=len(pending),
            desc="Obteniendo metadatos" if round_number == 0 else f"Reintento {round_number}",
            leave=False,
            disable=None,
        ) as pbar:
            for offset in range(0, len(pending), batch_size):
                slice_ids = pending[offset : offset + batch_size]
                batch = service.new_batch_http_request()
                for msg_id in slice_ids:
                    batch.add(
                        service.users().messages().get(userId="me", id=msg_id, format="metadata"),
                        callback=lambda request_id, response, exception, mid=msg_id: (
                            batch_email_response_handler(
                                request_id,
                                response,
                                exception,
                                emails_by_date,
                                round_failures,
                                mid,
                            )
                        ),
                    )
                pace(len(slice_ids), name=username)
                run_batch_requests(batch)
                pbar.update(len(slice_ids))

        retryable = [f for f in round_failures if f.get("retryable")]
        failures.extend(f for f in round_failures if not f.get("retryable"))

        if not retryable:
            pending = []
            break

        pending = [f["msg_id"] for f in retryable if f.get("msg_id")]
        if round_number < max_rounds - 1:
            backoff = min(2**round_number, 30) + random.uniform(0, 1)
            logger.info(
                "%s: %d mensajes con rate limit, reintento %d en %.1fs",
                username,
                len(pending),
                round_number + 1,
                backoff,
            )
            time.sleep(backoff)
        else:
            # Out of rounds: whatever is left is a real failure.
            failures.extend(retryable)
            pending = []

    return failures


def batch_email_response_handler(
    request_id, response, exception, emails_by_date, failures=None, msg_id=None
):
    """
    Callback function to process the response of a batch request to download emails.
    Args:
        request_id (str): The ID of the request.
        response (dict): The response from the email server.
        exception (Exception): The exception raised during the request, if any.
        emails_by_date (dict): A dictionary to store emails categorized by their date.
        failures (list, optional): Collects failed messages so the caller can tell
            an incomplete chunk from a complete one instead of losing rows silently.
        msg_id (str, optional): The Gmail message id behind this sub-request.
    Returns:
        None
    The function processes the response, extracts email headers and other relevant information,
    and categorizes the emails by their date. If an exception occurs, it logs the error.
    """
    if exception is not None:
        retryable = is_retryable(exception)
        logger.log(
            logging.INFO if retryable else logging.WARNING,
            "Error en la solicitud %s (%s): %s",
            request_id,
            msg_id,
            exception,
        )
        if failures is not None:
            failures.append(
                {
                    "request_id": request_id,
                    "msg_id": msg_id,
                    "error": str(exception),
                    "retryable": retryable,
                }
            )
    else:
        headers = {h["name"]: h["value"] for h in response.get("payload", {}).get("headers", [])}
        date = headers.get("Date", "")
        date_str = parse_email_date(date, logger)

        email_data = {
            "id": response["id"],
            "threadId": response["threadId"],
            "labelIds": response.get("labelIds", []),
            "sizeEstimate": response.get("sizeEstimate", 0),
            "historyId": response.get("historyId", ""),
            "internalDate": response.get("internalDate", ""),
            "deliveredTo": headers.get("Delivered-To", ""),
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "cc": headers.get("Cc", ""),
            "bcc": headers.get("Bcc", ""),
            "date": date,
            "contentType": headers.get("Content-Type", ""),
            # `format=metadata` already returns the first ~200 characters of the
            # body. It costs nothing extra here and is the only body text the
            # metadata phase can capture without a second pass over every message.
            "snippet": response.get("snippet", ""),
        }

        if date_str not in emails_by_date:
            emails_by_date[date_str] = []
        emails_by_date[date_str].append(email_data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gmail Bulk Export")
    parser.add_argument(
        "--fase", choices=["metadata", "emails", "attachments"], help="Fase a ejecutar"
    )
    parser.add_argument("--username", required=True, help="Nombre de usuario/email a descargar")
    parser.add_argument("--start_date", help="Fecha de inicio en formato YYYYMMDD")
    parser.add_argument("--end_date", help="Fecha de fin en formato YYYYMMDD")

    args = parser.parse_args()

    logger.info("args: %s", args)

    if args.fase == "metadata":
        if not args.start_date or not args.end_date:
            print("Debes especificar start_date y end_date para la fase 1")
        else:
            fetch_email_metadata(args.username, (args.start_date, args.end_date))
