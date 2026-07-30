import email
import gzip
from pathlib import Path

from core.attachment_handler import (
    generate_attachment_file_path,
    get_filtered_attachments,
    save_attachments,
)


def make_message_with_attachment(
    filename: str, content: bytes, content_type: str = "application/pdf"
):
    msg = email.message.EmailMessage()
    msg.set_content("Body")
    msg.add_attachment(
        content,
        maintype=content_type.split("/")[0],
        subtype=content_type.split("/")[1],
        filename=filename,
    )
    return msg


def test_get_filtered_attachments_and_save(tmp_path):
    msg = make_message_with_attachment("test.pdf", b"PDFDATA", "application/pdf")
    attachments = get_filtered_attachments(msg, allowed_content_types=["application/pdf"])
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "test.pdf"
    assert attachments[0]["payload"] == b"PDFDATA"

    base_dir = tmp_path / "outdir"
    base_dir.mkdir()
    file_path = generate_attachment_file_path(str(base_dir), "msg1")

    # save attachments
    save_attachments(str(file_path), attachments, "msg1")
    assert Path(file_path).exists()

    # read gz content to ensure filename marker present
    with gzip.open(file_path, "rb") as f:
        content = f.read()
    assert b"--filename:test.pdf--" in content
