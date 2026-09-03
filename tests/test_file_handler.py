import os
from pathlib import Path

from gmail_bulk_export.core.file_handler import (
    check_email_downloaded,
    find_email_file,
    generate_email_file_path,
)


def test_generate_email_file_path_and_check(tmp_path):
    username = "user@example.com"
    msg_id = "msg123"
    date = "2025-11-19"

    out_dir = tmp_path / "output"
    # generate path
    path = generate_email_file_path(username, msg_id, date, str(out_dir))
    assert str(out_dir) in path
    assert username in path
    assert msg_id in path

    # ensure file does not exist initially
    assert not check_email_downloaded(username, msg_id, date, str(out_dir))

    # create the file and check again
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("")
    assert check_email_downloaded(username, msg_id, date, str(out_dir))


def test_find_email_file(tmp_path):
    base = tmp_path / "output" / "user@example.com" / "2025-11-19"
    base.mkdir(parents=True)
    msg_file = base / "msgXYZ.json"
    msg_file.write_text("{}")

    found = find_email_file(str(tmp_path / "output"), "msgXYZ")
    assert found is not None
    assert found.endswith(os.path.join("2025-11-19", "msgXYZ.json"))
