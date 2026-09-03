"""
Gmail Bulk Export — Central CLI with subcommands.

This is the main entry point for the gmail-bulk-export package.
It provides a unified interface for downloading emails, attachments, labels, and payloads.
"""

import argparse
import logging
import sys
from datetime import datetime

from gmail_bulk_export.auth.service import AuthenticationError, check_authentication
from gmail_bulk_export.config import (
    BATCH_SIZE,
    MAX_WORKERS,
    PAYLOAD_BATCH_SIZE,
    credentials_file,
    output_dir,
    update_config,
)
from gmail_bulk_export.config_logger import get_app_logger

from . import bulk_emails_downloader, gmail_labels_downloader, gmail_payloads_downloader

# Every scripts/ module carries this block; main.py did not, and it prints ✓ and
# ❌ on both the success and failure paths. On a stock Windows console (cp1252)
# that raised UnicodeEncodeError, so `gmail-bulk-export token` crashed even when the
# credentials were perfectly valid, and real errors were masked by the failure
# to print them.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logger = get_app_logger()


def verify_auth(username: str) -> None:
    """
    Verify authentication before running download commands, or exit.

    Args:
        username (str): Gmail account to verify

    Raises:
        SystemExit: If the credentials are missing or invalid.
    """
    try:
        # check_authentication never returns False — it raises. There is no
        # failure branch to test for here.
        check_authentication(username)
        logger.info("✓ Authentication verified for %s", username)
    except AuthenticationError as e:
        logger.error("✗ Authentication error: %s", e)
        print(f"❌ {e}")
        print("Run 'gmail-bulk-export token' to check the credentials file.")
        sys.exit(1)


def cmd_metadata(args):
    """Download email metadata (headers, subject, from, to, etc)."""
    verify_auth(args.username)

    logger.info(
        "Starting metadata download for %s from %s to %s",
        args.username,
        args.start_date,
        args.end_date,
    )
    bulk_emails_downloader.fetch_email_metadata(args.username, (args.start_date, args.end_date))
    logger.info("Metadata download completed for %s", args.username)


def cmd_payloads(args):
    """Download email bodies (content) and optional attachments."""
    verify_auth(args.username)

    logger.info("Starting payload download for user: %s", args.username)

    if args.csv_file:
        logger.info("Reading email IDs from CSV: %s", args.csv_file)
    elif args.start_date and args.end_date:
        logger.info("Date range: %s to %s", args.start_date, args.end_date)

    emails_info = gmail_payloads_downloader.retrieve_emails_data(args, logger)

    if not emails_info:
        logger.error("No emails found to download.")
        sys.exit(1)

    logger.info("Found %d emails to download", len(emails_info))

    email_ids_by_mailbox = {}
    for email_id, mailbox, date in emails_info:
        if mailbox not in email_ids_by_mailbox:
            email_ids_by_mailbox[mailbox] = []
        email_ids_by_mailbox[mailbox].append((email_id, date))

    for mailbox, values in email_ids_by_mailbox.items():
        logger.info("Downloading payloads for mailbox: %s", mailbox)
        result = gmail_payloads_downloader.download_email_bodies(
            mailbox,
            values,
            args.allowed_attachment_types,
            save_attachment_files=getattr(args, "with_attachments", False),
        )
        print(
            f"{mailbox}: {result['downloaded']} descargados, "
            f"{result['skipped']} ya existían, {result['failed']} fallos"
        )

    logger.info("Payload download completed")


def cmd_labels(args):
    """Download Gmail labels and their statistics."""
    verify_auth(args.username)

    logger.info("Fetching labels for user: %s", args.username)
    labels = gmail_labels_downloader.get_users_label(
        args.username, args.output_dir, args.label_filename
    )

    if labels:
        logger.info("Successfully fetched %d labels", len(labels))
        print(f"Labels for {args.username}: {len(labels)}")
    else:
        logger.warning("No labels found for %s", args.username)
        print(f"No labels found for {args.username}.")


def cmd_token(args):
    """Verify that service account credentials file exists and is valid."""
    import json
    import os

    logger.info("Checking service account credentials")
    # Resolved here, after main() has applied any --credentials-file override.
    creds_path = credentials_file()
    if not os.path.exists(creds_path):
        logger.error("Service account file not found: %s", creds_path)
        print(f"❌ Service account file not found: {creds_path}")
        print(f"Download it from Google Cloud Console and save as '{creds_path}'")
        sys.exit(1)

    # Try to validate the file by loading it
    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "type" not in data or data.get("type") != "service_account":
                raise ValueError("File does not appear to be a service account JSON")
        logger.info("Service account credentials file is valid")
        print(f"✓ Service account credentials file found and valid: {creds_path}")
    except json.JSONDecodeError:
        logger.error("Invalid JSON in credentials file")
        print(f"❌ Invalid JSON in {creds_path}")
        sys.exit(1)
    except Exception as e:
        logger.error("Error validating credentials file: %s", e)
        print(f"❌ Error validating credentials file: {e}")
        sys.exit(1)


def main():
    """Main entry point with subcommand dispatcher."""
    parser = argparse.ArgumentParser(
        prog="gmail-bulk-export",
        description="Download Gmail messages in bulk using the Gmail API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s metadata --username user@example.com --start_date 20230101 --end_date 20230131
  %(prog)s payloads --username user@example.com --csv-file emails.csv
  %(prog)s payloads --username user@example.com --csv-file emails.csv --with-attachments
  %(prog)s labels --username user@example.com
  %(prog)s token  # Verify service account credentials

These operate on one mailbox and one date range, without checkpoints. For a real
multi-mailbox backfill use the orchestrators: python -m scripts.download_metadata
        """,
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose (DEBUG) logging"
    )

    # Global configuration arguments that can override config.json and .env
    parser.add_argument(
        "--output-dir",
        help=f"Output directory (overrides config.json and .env, default: {output_dir()})",
    )
    parser.add_argument(
        "--credentials-file",
        help=f"Service account credentials file (overrides config.json and .env, \
            default: {credentials_file()})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        help=f"Batch size for metadata downloads (overrides config.json and .env, \
            default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--payload-batch-size",
        type=int,
        help=f"Batch size for payload downloads (overrides config.json and .env, \
            default: {PAYLOAD_BATCH_SIZE})",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        help=f"Maximum worker threads (overrides config.json and .env, default: \
            {MAX_WORKERS})",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    subparsers.required = True

    # Subcommand: metadata
    parser_metadata = subparsers.add_parser(
        "metadata", help="Download email metadata (headers, subject, sender, etc.)"
    )
    parser_metadata.add_argument("--username", required=True, help="Gmail account email address")
    parser_metadata.add_argument(
        "--start_date", required=True, help="Start date in format YYYYMMDD"
    )
    parser_metadata.add_argument("--end_date", required=True, help="End date in format YYYYMMDD")
    parser_metadata.set_defaults(func=cmd_metadata)

    # Subcommand: payloads
    parser_payloads = subparsers.add_parser(
        "payloads", help="Download email bodies (content) and optional attachments"
    )
    parser_payloads.add_argument("--username", help="Gmail account email address")
    parser_payloads.add_argument("--csv-file", help="CSV file with email IDs to download")
    parser_payloads.add_argument("--start_date", help="Start date in format YYYYMMDD")
    parser_payloads.add_argument("--end_date", help="End date in format YYYYMMDD")
    parser_payloads.add_argument("--email_ids", nargs="+", help="Specific email IDs to download")
    parser_payloads.add_argument(
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
        help="Allowed MIME types for attachments",
    )
    parser_payloads.add_argument(
        "--with-attachments",
        action="store_true",
        help="Also save attachment binaries next to each body (much more disk)",
    )
    parser_payloads.set_defaults(func=cmd_payloads)

    # Subcommand: labels
    parser_labels = subparsers.add_parser("labels", help="Download and export Gmail labels")
    parser_labels.add_argument("--username", required=True, help="Gmail account email address")
    # No --output-dir here: it used to shadow the global one. argparse applies a
    # subparser's default after the main parser has run, so `--output-dir X
    # labels` silently lost X. The global flag now governs every subcommand.
    parser_labels.add_argument(
        "--label-filename", default="labels", help="Filename for labels CSV (default: labels)"
    )
    parser_labels.set_defaults(func=cmd_labels)

    # Subcommand: token
    parser_token = subparsers.add_parser(
        "token", help="Verify the service account credentials file"
    )
    parser_token.set_defaults(func=cmd_token)

    # Parse arguments
    args = parser.parse_args()

    # Update configuration with command-line overrides (before any command execution)
    # Priority: subcommand args > global args > config.json/.env
    config_updates = {}

    # Check for global arguments first
    if hasattr(args, "output_dir") and args.output_dir:
        config_updates["output_directory"] = args.output_dir
    if hasattr(args, "credentials_file") and args.credentials_file:
        config_updates["credentials_filename"] = args.credentials_file
    if hasattr(args, "batch_size") and args.batch_size is not None:
        config_updates["batch_size"] = args.batch_size
    if hasattr(args, "payload_batch_size") and args.payload_batch_size is not None:
        config_updates["payload_batch_size"] = args.payload_batch_size
    if hasattr(args, "max_workers") and args.max_workers is not None:
        config_updates["max_workers"] = args.max_workers

    # Note: Subcommand-specific --output-dir arguments are handled in their respective
    # command functions (e.g., cmd_labels uses args.output_dir directly)

    if config_updates:
        update_config(**config_updates)
        logger.info("Configuration overridden from command-line: %s", config_updates)

    # Configure logging level
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")

    logger.info("Command: %s | Time: %s", args.command, datetime.now().isoformat())
    logger.debug("Arguments: %s", args)

    # Execute the subcommand
    try:
        args.func(args)
    except Exception as e:
        logger.exception("Error executing command: %s", args.command)
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
