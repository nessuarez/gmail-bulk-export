import inspect

import pytest

import auth.service as service
import config


def test_check_authentication_signature():
    sig = inspect.signature(service.check_authentication)
    assert list(sig.parameters) == ["username"]


def test_missing_credentials_file_raises(tmp_path):
    """A missing credentials file must fail before any network call.

    This previously patched `service.TOKEN_FILE`, a name the module does not
    define, so the patch did nothing: with a real client_secret.json on disk the
    test reached `creds.refresh()` and tried to talk to Google. It passed for the
    wrong reason.
    """
    original = config.get_config("credentials_filename")
    config.update_config(credentials_filename=str(tmp_path / "does_not_exist.json"))
    try:
        with pytest.raises(service.AuthenticationError, match="not found"):
            service.check_authentication("user@example.com")
    finally:
        config.update_config(credentials_filename=original)
