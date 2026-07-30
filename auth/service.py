"""Gmail API Service Authentication — Service Account Impersonation.

This module handles Gmail API authentication using Google service-account credentials
with domain-wide delegation for user impersonation.

## Architecture

**Service Account Impersonation** allows a service account to act on behalf of users
in a Google Workspace domain. This approach:

1. Avoids storing per-user credentials (more secure).
2. Enables batch operations across multiple users from a single service account.
3. Requires domain-wide delegation to be enabled in Google Admin Console.

## Usage

```python
from auth.service import get_gmail_service

# Create a service for a specific user
service = get_gmail_service("user@example.com")

# Use service to call Gmail API
results = service.users().messages().list(userId="me", q="is:unread").execute()
```

## Setup Requirements

1. **Create a service account** in Google Cloud Console
2. **Download service account key** as JSON and save as `client_secret.json`
3. **Enable domain-wide delegation** in Google Cloud Console
4. **Grant the service account access** in Google Workspace Admin Console:
   - Scope: `https://www.googleapis.com/auth/gmail.readonly`
"""

import logging
import os
import threading

from dotenv import load_dotenv
from google.auth import credentials as auth_credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import HttpRequest

from config import credentials_file

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
load_dotenv()


class AuthenticationError(Exception):
    """Raised when authentication or credential refresh fails."""

    pass


logger = logging.getLogger(__name__)


def get_gmail_credentials(username: str) -> auth_credentials.Credentials:
    """
    Authenticate a Gmail user using service-account impersonation.

    This function:
    1. Loads service account credentials from client_secret.json
    2. Impersonates the specified user via `with_subject()`
    3. Refreshes the credentials to obtain a valid token
    4. Returns the authenticated credentials

    Args:
        username (str): The email address of the user to impersonate.
                       Must be in the same Google Workspace domain as the service account.

    Returns:
        google.oauth2.service_account.Credentials: Authenticated credentials for the user.

    Raises:
        AuthenticationError: If the credentials file is missing, invalid, or refresh fails.

    Example:
        >>> creds = get_gmail_credentials("user@example.com")
        >>> print(creds.valid)
        True
    """
    creds_path = credentials_file()
    if not os.path.exists(creds_path):
        raise AuthenticationError(
            f"Service account file not found: {creds_path}. "
            f"Download it from Google Cloud Console and save as '{creds_path}'. "
            f"The file should contain the complete service account JSON with 'type', 'private_key',"
            f"'client_email', etc."
        )

    try:
        creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        creds = creds.with_subject(username)  # Impersonate the user
        creds.refresh(Request())
        logger.info("Using service account credentials from %s for user %s", creds_path, username)
    except Exception as e:
        raise AuthenticationError(
            f"Failed to load or refresh service account credentials from {creds_path}: {e}"
        )

    if not creds or not creds.valid:
        raise AuthenticationError(
            f"Credentials are invalid or could not be refreshed. "
            f"Verify {creds_path} contains valid service-account credentials."
        )

    return creds


def get_gmail_service(username: str) -> build:
    """
    Create an authenticated Gmail API service for the specified user.

    This function:
    1. Obtains credentials via `get_gmail_credentials()`
    2. Builds a Gmail API v1 service with custom gzip request builder
    3. Returns the ready-to-use service object

    Args:
        username (str): The email address of the user.

    Returns:
        googleapiclient.discovery.Resource: Authenticated Gmail API service.
                                            Use service.users().messages().list(...) etc.

    Raises:
        AuthenticationError: If credential retrieval fails.

    Example:
        >>> service = get_gmail_service("user@example.com")
        >>> messages = service.users().messages().list(userId="me").execute()
        >>> print(f"Found {len(messages.get('messages', []))} messages")
    """
    creds = get_gmail_credentials(username)
    service = build("gmail", "v1", credentials=creds, requestBuilder=gzip_request_builder)

    logger.info("Gmail API service created for user: %s", username)
    return service


_thread_local = threading.local()


def get_cached_gmail_service(username: str) -> build:
    """Return a Gmail service for `username`, cached per thread.

    `get_gmail_service` performs a full `creds.refresh()` (a network round-trip)
    plus a discovery build on every call, which is ruinous when called once per
    message. The cache is *per thread* rather than global on purpose: the
    underlying `httplib2.Http` object is not thread-safe, so sharing one service
    across a ThreadPoolExecutor corrupts responses under load.

    Args:
        username (str): The email address of the user to impersonate.

    Returns:
        googleapiclient.discovery.Resource: Authenticated Gmail API service.
    """
    cache = getattr(_thread_local, "services", None)
    if cache is None:
        cache = {}
        _thread_local.services = cache
    service = cache.get(username)
    if service is None:
        service = get_gmail_service(username)
        cache[username] = service
    return service


def gzip_request_builder(*args, **kwargs):
    """Custom request builder to enable gzip compression for API responses.

    Reduces bandwidth usage for large bulk downloads by compressing responses.

    Args:
        *args: Positional arguments for HttpRequest
        **kwargs: Keyword arguments for HttpRequest

    Returns:
        googleapiclient.http.HttpRequest: Modified request with gzip enabled.
    """
    request = HttpRequest(*args, **kwargs)
    request.headers["Accept-Encoding"] = "gzip"
    return request


def check_authentication(username: str) -> bool:
    """
    Verify that service account credentials are valid and accessible.

    This function checks:
    1. Service account file (client_secret.json) exists and is readable
    2. Credentials can be loaded and refreshed for the specified user

    Use this as a pre-flight check before starting bulk download operations: it
    turns a credential problem into a failure in milliseconds rather than half
    an hour into a download.

    Args:
        username (str): The email address of the user to impersonate.

    Returns:
        bool: Always True. It **never returns False** — an invalid credential
            raises instead, so callers must catch the exception rather than
            test the result.

    Raises:
        AuthenticationError: If the credentials file is missing or invalid, or
            if the user cannot be impersonated.

    Example:
        >>> try:
        ...     check_authentication("user@example.com")
        ... except AuthenticationError as exc:
        ...     print(f"✗ {exc}")
    """
    try:
        creds_path = credentials_file()
        if not os.path.exists(creds_path):
            raise AuthenticationError(
                f"Service account file not found: {creds_path}. "
                f"Download it from Google Cloud Console and save as '{creds_path}'."
            )

        # Try to load and validate credentials
        try:
            creds = get_gmail_credentials(username)
            if not creds or not creds.valid:
                raise AuthenticationError(
                    f"Credentials are invalid for user {username}. "
                    f"Verify {creds_path} contains valid service-account credentials."
                )
        except Exception as e:
            raise AuthenticationError(f"Failed to validate credentials for user {username}: {e}")

        logger.info("✓ Credentials verified for user: %s", username)
        return True

    except AuthenticationError:
        # Re-raise authentication errors without modification
        raise
    except Exception as e:
        logger.error("Authentication check failed: %s", e)
        raise AuthenticationError(f"Failed to verify credentials: {e}")
