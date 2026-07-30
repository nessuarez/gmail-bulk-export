"""The output root must be resolved at call time, not captured at import.

Every module used to do `from config import OUTPUT_DIR`, which binds the value
when the module is first imported — before `update_config()` has seen the
command line. `--output-dir` was accepted and then silently ignored, and the
only way to redirect output was the OUTPUT_DIR environment variable.

These tests fail against that design, so they are what stops it returning.
"""

import os

import pytest

import config
from core import checkpoints, file_handler


@pytest.fixture
def redirected_output(tmp_path):
    """Point the configured output root at tmp_path for the duration of a test."""
    original = config.get_config("output_directory")
    config.update_config(output_directory=str(tmp_path))
    yield tmp_path
    config.update_config(output_directory=original)


def test_output_dir_reflects_a_later_update():
    """The accessor must see a change made after import, not a captured value."""
    original = config.get_config("output_directory")
    try:
        config.update_config(output_directory="/somewhere/else")
        assert config.output_dir() == "/somewhere/else"
    finally:
        config.update_config(output_directory=original)


def test_credentials_file_reflects_a_later_update():
    original = config.get_config("credentials_filename")
    try:
        config.update_config(credentials_filename="other_secret.json")
        assert config.credentials_file() == "other_secret.json"
    finally:
        config.update_config(credentials_filename=original)


def test_checkpoints_follow_the_override(redirected_output):
    """Resumption state must land under the configured root.

    This is the one that matters most: checkpoints living somewhere other than
    the data they describe means a resumed run cannot find its own progress.
    """
    path = checkpoints.save_checkpoint("user@example.com", "metadata_20240101_20240131", {"a": 1})

    assert str(redirected_output) in path
    assert checkpoints.load_checkpoint("user@example.com", "metadata_20240101_20240131") == {"a": 1}


def test_email_paths_follow_the_override(redirected_output):
    path = file_handler.generate_email_file_path("user@example.com", "msg123", "2024-01-15")

    assert str(redirected_output) in path
    assert path.endswith("msg123.jsonl.gz")


def test_explicit_output_dir_still_wins(tmp_path):
    """An explicitly passed directory must override the configured one."""
    explicit = tmp_path / "explicit"
    path = file_handler.generate_email_file_path(
        "user@example.com", "msg123", "2024-01-15", output_dir=str(explicit)
    )

    assert str(explicit) in path


def test_no_module_captures_the_constant():
    """config must not re-export OUTPUT_DIR or CREDENTIALS_FILE.

    Their absence is deliberate: a `from config import OUTPUT_DIR` anywhere
    should fail loudly at import rather than quietly use a stale value.
    """
    assert not hasattr(config, "OUTPUT_DIR")
    assert not hasattr(config, "CREDENTIALS_FILE")


def test_environment_variable_still_works(monkeypatch, tmp_path):
    """OUTPUT_DIR=... must keep working; it is what the runbook documents."""
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    config.load_config()
    try:
        assert config.output_dir() == str(tmp_path)
    finally:
        monkeypatch.delenv("OUTPUT_DIR")
        config.load_config()


def test_checkpoint_directory_is_created_under_the_override(redirected_output):
    checkpoints.save_checkpoint("user@example.com", "k", {})
    expected = os.path.join(str(redirected_output), "user@example.com", ".checkpoints")

    assert os.path.isdir(expected)
