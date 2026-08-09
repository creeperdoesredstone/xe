from pathlib import Path

from xe_lang.devices.filesystem import FileSystemDevice


def test_filesystem_normalize_append_copy_stat_and_revision(tmp_path: Path) -> None:
	files = FileSystemDevice(tmp_path / "drive")
	start = files.revision
	handle = files.open_append("notes/../notes/log.txt")
	assert handle > 0
	assert files.write(handle, "one")
	files.close_path("notes/log.txt")
	handle = files.open_append("notes/log.txt")
	assert files.write(handle, " two")
	files.close_path("notes/log.txt")
	assert files.normalize("notes\\log.txt") == "notes/log.txt"
	assert files.normalize("../outside") == ""
	assert files.copy("notes/log.txt", "notes/copy.txt")
	copy_handle = files.open_read("notes/copy.txt")
	assert files.read(copy_handle) == "one two"
	files.close_path("notes/copy.txt")
	entry = files.stat("notes/copy.txt")
	assert entry is not None and entry.size == 7 and not entry.is_directory
	assert files.is_directory("notes")
	assert files.revision > start


def test_filesystem_copy_rejects_recursive_destination(tmp_path: Path) -> None:
	files = FileSystemDevice(tmp_path / "drive")
	assert files.make_directory("folder")
	assert not files.copy("folder", "folder/nested")
