"""The list of labels to request detailed counts for is configurable.

It used to be hardcoded in `cli/gmail_labels_downloader.py` and included a
specific organization's custom label inside an otherwise generic module.
These tests pin down that the default carries no organization-specific label,
and that a label missing from a mailbox is skipped instead of being requested
with `id=None`.
"""

import config
from cli import gmail_labels_downloader as labels_cli


def test_default_carries_only_standard_gmail_labels():
    assert set(config.DEFAULT_DETAILED_LABELS) == {"INBOX", "SENT", "UNREAD", "CHAT"}


def test_config_can_add_its_own_labels():
    original = config.get_config("detailed_labels")
    try:
        config.update_config(detailed_labels=["INBOX", "MY_LABEL"])
        assert config.detailed_labels() == ["INBOX", "MY_LABEL"]
    finally:
        config.update_config(detailed_labels=original)


def test_environment_variable_is_comma_separated(monkeypatch):
    monkeypatch.setenv("DETAILED_LABELS", "INBOX, SENT ,CUSTOM")
    config.load_config()
    try:
        assert config.detailed_labels() == ["INBOX", "SENT", "CUSTOM"]
    finally:
        monkeypatch.delenv("DETAILED_LABELS")
        config.load_config()


def test_caller_cannot_mutate_the_configured_list():
    """`detailed_labels()` returns a copy; mutating it must not affect the config."""
    first = config.detailed_labels()
    first.append("CONTAMINATED")
    assert "CONTAMINATED" not in config.detailed_labels()


def test_missing_label_returns_none():
    """This is what triggers the skip: no id means no request to make."""
    labels = [{"id": "Label_1", "name": "INBOX"}]
    assert labels_cli.get_label_id("INBOX", labels) == "Label_1"
    assert labels_cli.get_label_id("DOES_NOT_EXIST", labels) is None


def test_module_no_longer_hardcodes_a_label_list():
    """The old constant must not come back: it was this module's only business leak."""
    assert not hasattr(labels_cli, "interesting_label_ids")
