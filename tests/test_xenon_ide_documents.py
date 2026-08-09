from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source
from xe_lang.devices.filesystem import FileSystemDevice


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "apps" / "xenon_ide.xe").read_text(encoding="utf-8")


def test_virtual_ide_compiles() -> None:
    artifact = compile_source(SOURCE, "apps/xenon_ide.xe")
    assert artifact.success, artifact.diagnostics


def test_each_tab_has_independent_edit_state() -> None:
    for declaration in (
        "array view_source: string[16]",
        "array view_dirty: int[16]",
        "array view_cursor: int[16]",
        "array view_selection_start: int[16]",
        "array view_selection_end: int[16]",
    ):
        assert declaration in SOURCE
    assert "proc ide_store_active_document" in SOURCE
    assert "proc ide_restore_document" in SOURCE


def test_folders_navigate_without_becoming_tabs() -> None:
    body = SOURCE.split("proc ide_open_folder", 1)[1].split("proc ide_open_file", 1)[0]
    assert "ide_add_view" not in body
    assert "Folder selected" in body
    assert "explorer_nested_expanded" in SOURCE
    assert "arrow_x = x + 2 + depth * 8" in SOURCE
    assert "explorer_nested_expanded[nested_slot] = 1 - explorer_nested_expanded[nested_slot]" in SOURCE


def test_blank_workspace_and_help_tab_are_bundled() -> None:
    assert 'source = ""' in SOURCE
    assert 'view_path[0] = xestring::concat("", source_path)' in SOURCE
    assert 'view_path[1] = "Help"' in SOURCE
    assert "proc draw_ide_help_view" in SOURCE
    assert "proc draw_ide_help_line" in SOURCE
    assert '"Xe source basics"' in SOURCE
    assert '"Control flow"' in SOURCE
    assert '"Functions and procedures"' in SOURCE
    assert '"Standard libraries"' in SOURCE
    assert '"Scratch-ready habits"' in SOURCE
    assert "help_scroll -= ide_scroll_delta" in SOURCE
    assert "creeperdoesredstone.github.io/xe-docs/" in SOURCE


def test_close_prompt_and_run_scope_are_explicit() -> None:
    assert "Save changes before closing?" in SOURCE
    assert "Run workspace" in SOURCE
    assert "Run active file" in SOURCE
    assert "proc ide_check_and_run" in SOURCE
    assert "proc ide_run_active_file" in SOURCE


def test_main_is_the_nucleus_not_an_electron() -> None:
    assert 'draw_text_small(ide_window, center_x - 8, center_y - 2, "main"' in SOURCE
    assert 'compiler::document_script_name(slot, index) != "main"' in SOURCE


def test_shell_hover_slows_and_direct_hover_stops() -> None:
    assert "if (hovered_view == view_update) { view_orbit_target[view_update] = 6 }" in SOURCE
    assert "hovered_view == view_update && hovered_atom >= 0" in SOURCE
    assert "view_orbit_target[view_update] = 0" in SOURCE


def test_explorer_uses_revision_keyed_batched_metadata() -> None:
    assert "explorer_cache_revision != os::revision()" in SOURCE
    assert "proc ide_rebuild_explorer_cache" in SOURCE
    assert "proc ide_cache_explorer_entry" in SOURCE
    draw_body = SOURCE.split("proc draw_workspace_explorer", 1)[1].split(
        "# Placeholder asset boundary", 1
    )[0]
    assert "os::entry_count" not in draw_body
    assert "os::entry_name" not in draw_body
    assert "os::entry_is_directory" not in draw_body


def test_explorer_cache_invalidates_after_create_rename_move_and_delete(
    tmp_path: Path, monkeypatch
) -> None:
    calls = {"count": 0, "name": 0, "directory": 0}
    original_count = FileSystemDevice.entry_count
    original_name = FileSystemDevice.entry_name
    original_directory = FileSystemDevice.entry_is_directory

    def counted_count(self, name="."):
        calls["count"] += 1
        return original_count(self, name)

    def counted_name(self, name, index):
        calls["name"] += 1
        return original_name(self, name, index)

    def counted_directory(self, name, index):
        calls["directory"] += 1
        return original_directory(self, name, index)

    monkeypatch.setattr(FileSystemDevice, "entry_count", counted_count)
    monkeypatch.setattr(FileSystemDevice, "entry_name", counted_name)
    monkeypatch.setattr(FileSystemDevice, "entry_is_directory", counted_directory)

    drive = tmp_path / "drive"
    drive.mkdir()
    (drive / "target").mkdir()
    for index in range(8):
        (drive / f"file{index}.xe").write_text("out << 1", encoding="utf-8")

    snapshots: list[int] = []
    context: RuntimeContext

    def on_frame(_frame) -> None:
        snapshots.append(sum(calls.values()))
        files = context.vm.devices.files
        if len(snapshots) == 1:
            assert files.make_file("added.xe")
        elif len(snapshots) == 2:
            assert files.rename("added.xe", "renamed.xe")
        elif len(snapshots) == 3:
            assert files.rename("renamed.xe", "target/renamed.xe")
        elif len(snapshots) == 4:
            assert files.delete("target/renamed.xe")
        elif len(snapshots) >= 6:
            context.cancel()

    context = RuntimeContext(frame_handler=on_frame, filesystem_root=drive)
    with redirect_stdout(StringIO()):
        _, error, _ = run("apps/xenon_ide.xe", SOURCE, context)

    assert error is None
    assert len(snapshots) == 6
    assert all(snapshots[index] > snapshots[index - 1] for index in range(1, 5))
    assert snapshots[5] == snapshots[4]
