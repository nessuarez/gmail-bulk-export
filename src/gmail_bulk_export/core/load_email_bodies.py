"""Load email bodies from gzipped JSONL files into a Pandas DataFrame."""

import gzip
import json
import os

import pandas as pd
from tqdm import tqdm

from gmail_bulk_export.config import output_dir
from gmail_bulk_export.config_logger import get_app_logger

# Configure basic logging
logger = get_app_logger("logs")


def load_email_data(directory: str) -> pd.DataFrame:
    """
    Loads email data from gzipped JSONL files in a directory and returns a Pandas DataFrame.

    Args:
        directory (str): The directory containing the .jsonl.gz files.

    Returns:
        pd.DataFrame: A DataFrame containing the loaded email data.
    """
    data = []
    file_paths = []

    # Walk through directory to find all .jsonl.gz files
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".jsonl.gz"):
                file_paths.append(os.path.join(root, file))

    logger.info("Found %d .jsonl.gz files in %s", len(file_paths), directory)

    # Load data from each file
    for file_path in tqdm(file_paths, desc="Loading email data"):
        try:
            with gzip.open(file_path, "rt", encoding="utf-8") as f:
                for line in f:
                    try:
                        email_data = json.loads(line)
                        data.append(email_data)
                    except json.JSONDecodeError as e:
                        logger.error("Error decoding JSON from line in %s: %s", file_path, e)
        except Exception as e:
            logger.error("Error reading file %s: %s", file_path, e)

    # Create DataFrame
    df = pd.DataFrame(data)
    logger.info("Loaded %d email records into DataFrame", len(df))
    return df


if __name__ == "__main__":
    # Specify the directory containing the .jsonl.gz files (use central config)
    email_data_directory = output_dir()  # Replace with a specific path if needed

    # Load the email data into a Pandas DataFrame
    try:
        email_df = load_email_data(email_data_directory)

        # Print some info about the DataFrame
        print(email_df.info())

        # Display the first few rows of the DataFrame
        print(email_df.head())
    except Exception as e:
        logger.error(f"An error occurred: {e}")
