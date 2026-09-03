"""Lists the user's Gmail labels."""

import argparse
import logging

from googleapiclient.errors import HttpError
from tenacity import after_log, before_log, retry, stop_after_attempt, wait_exponential

from gmail_bulk_export.auth.service import get_gmail_service
from gmail_bulk_export.config import detailed_labels

# Aliased because the parameter below is also called output_dir.
from gmail_bulk_export.config import output_dir as configured_output_dir
from gmail_bulk_export.config_logger import get_app_logger
from gmail_bulk_export.core.save_to_csv import save_labels_csv

logger = get_app_logger()


def get_label_id(label_name, labels_list):
    """Search for the label in the mailbox's labels"""
    for label in labels_list:
        if label.get("name") == label_name:
            return label.get("id")
    return None


def get_users_label(username, output_dir=None, labelfilename="labels"):
    """Lists the user's Gmail labels.

    `output_dir` is resolved here rather than in the signature: a default is
    evaluated once, when the function is defined, which would capture the
    configured path before the command line was parsed.
    """
    output_dir = output_dir if output_dir is not None else configured_output_dir()

    try:
        # Call the Gmail API
        service = get_gmail_service(username)
        results = list_labels(service)
        labels = results.get("labels", [])

        # Fetch detailed information for the configured labels
        fetched = []
        for label_name in detailed_labels():
            label_id = get_label_id(label_name, labels)
            if label_id is None:
                # No todos los buzones tienen todas las etiquetas configuradas,
                # y con una lista configurable la ausencia es lo normal. Antes
                # se llamaba a la API con id=None: una petición que sólo servía
                # para fallar y registrar un error engañoso.
                logger.debug("El buzón no tiene la etiqueta %s; se omite", label_name)
                continue
            try:
                label = service.users().labels().get(userId="me", id=label_id).execute()
                # Merge label properties with expanded label details
                matching_label = next(
                    (item for item in labels if item.get("id") == label.get("id")), None
                )
                if matching_label:
                    matching_label.update(
                        {
                            "messagesTotal": label.get("messagesTotal"),
                            "messagesUnread": label.get("messagesUnread"),
                            "threadsTotal": label.get("threadsTotal"),
                            "threadsUnread": label.get("threadsUnread"),
                        }
                    )

                fetched.append(label)
            except HttpError as error:
                logger.error("Failed to fetch label %s: %s", label_name, error)

        # Log or process the detailed labels as needed
        logger.info("Fetched detailed information for %d labels", len(fetched))
        save_labels_csv(labels, output_dir, labelfilename)
        return labels

    except HttpError as error:
        # TODO(developer) - Handle errors from gmail API.
        logger.error("An error occurred: %s", error)
        return []


@retry(
    wait=wait_exponential(multiplier=1, min=4, max=10),
    stop=stop_after_attempt(10),
    before=before_log(logger, logging.INFO),
    after=after_log(logger, logging.INFO),
    reraise=True,
)
def list_labels(service):
    """
    Lists all labels in the user's Gmail account.

    Args:
        service: Authorized Gmail API service instance.

    Returns:
        dict: A dictionary containing the list of labels.
    """
    return service.users().labels().list(userId="me").execute()


def main():
    """List all labels for a user."""
    parser = argparse.ArgumentParser(description="Download Gmail labels for a user.")
    parser.add_argument(
        "--username", help="The username of the Gmail account.", type=str, required=True
    )
    parser.add_argument(
        "--output-dir",
        help="The directory to save the labels CSV file.",
        default=configured_output_dir(),
    )
    args = parser.parse_args()

    labels = get_users_label(args.username, args.output_dir)
    if labels:
        print(f"Labels for {args.username}: {len(labels)}")
    else:
        print(f"No labels found for {args.username}.")


if __name__ == "__main__":
    main()
