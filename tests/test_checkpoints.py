from pathlib import Path

from core.checkpoints import clear_checkpoint, load_checkpoint, save_checkpoint


def test_checkpoint_save_load_clear(tmp_path, monkeypatch):
    # point OUTPUT_DIR to tmp_path by patching config. We'll monkeypatch by creating the folder
    username = "user@example.com"
    key = "20250101_20250131"

    # Ensure directory exists indirectly by saving a checkpoint
    data = {"next_page_token": "ABC123", "processed": 42}
    path = save_checkpoint(username, key, data)

    assert Path(path).exists()

    loaded = load_checkpoint(username, key)
    assert loaded is not None
    assert loaded["next_page_token"] == "ABC123"
    assert loaded["processed"] == 42

    removed = clear_checkpoint(username, key)
    assert removed is True
    assert load_checkpoint(username, key) is None
