from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from time import perf_counter

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source
from xe_lang.devices.filesystem import FileSystemDevice
from xe_lang.devices.input import LEFT_BUTTON


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "apps" / "xenon_ide.xe").read_text(encoding="utf-8")


def run_ide_probe(probe: str, filesystem_root: Path | None = None) -> str:
    anchor = "while (ide_window.state != graphics::WINDOW_CLOSED) {"
    if anchor not in SOURCE:
        raise AssertionError("Xenon IDE probe anchor is missing")
    modified = SOURCE.replace(anchor, f"{probe}\nif (false) {{", 1)
    parts: list[str] = []
    context = RuntimeContext(filesystem_root=filesystem_root)
    context.output_handler = parts.append
    with redirect_stdout(StringIO()):
        _, error, _ = run("apps/xenon_ide-probe.xe", modified, context)
    if error is not None:
        raise AssertionError(str(error))
    return "".join(parts)


def test_virtual_ide_compiles() -> None:
    artifact = compile_source(SOURCE, "apps/xenon_ide.xe")
    assert artifact.success, artifact.diagnostics


def test_each_tab_preserves_its_unsaved_buffer(tmp_path: Path) -> None:
    drive = tmp_path / "drive"
    drive.mkdir()
    (drive / "alpha.xe").write_text("alpha disk", encoding="utf-8")
    (drive / "beta.xe").write_text("beta disk", encoding="utf-8")

    output = run_ide_probe(
        '''call ide_open_file("alpha.xe")
call ide_set(source, "unsaved alpha")
dirty = true
view_dirty[active_view] = 1
cursor_index = xestring::strlen(source)
call ide_open_file("beta.xe")
call ide_set(source, "unsaved beta")
dirty = true
view_dirty[active_view] = 1
cursor_index = xestring::strlen(source)
call ide_activate_view(2)
out << source
out << "|"
out << (int)dirty
call ide_activate_view(3)
out << "|"
out << source
out << "|"
out << (int)dirty''',
        drive,
    )

    assert output == "unsaved alpha|1|unsaved beta|1"


def test_folders_never_become_tabs_and_empty_nested_chevrons_toggle(tmp_path: Path) -> None:
    drive = tmp_path / "drive"
    (drive / "folder" / "empty").mkdir(parents=True)

    output = run_ide_probe(
        '''var probe_views: int
probe_views = view_count
call ide_open_folder("folder")
out << view_count - probe_views
out << ":"
out << ide_find_view("folder")
call ide_toggle_explorer_folder(0, 0, -1)
call ide_rebuild_explorer_cache(0, 64)
call ide_toggle_explorer_folder(1, 0, 0)
call ide_rebuild_explorer_cache(0, 64)
out << ":"
out << explorer_expanded[0]
out << ":"
out << explorer_nested_expanded[0]
out << ":"
out << ide_explorer_row_count()''',
        drive,
    )

    assert output == "0:-1:1:1:2"


def test_nested_chevron_pointer_clicks_reach_the_disclosure_state(tmp_path: Path) -> None:
    drive = tmp_path / "drive"
    (drive / "folder" / "empty").mkdir(parents=True)
    modified = SOURCE.replace(
        "while (ide_window.state != graphics::WINDOW_CLOSED) {",
        "var probe_frame: int\nprobe_frame = 0\nwhile (probe_frame < 8) {",
        1,
    )
    modified = modified.replace(
        "call graphics::update(ide_window)",
        "call graphics::update(ide_window)\n\tprobe_frame += 1",
        1,
    )
    modified += '\nout << explorer_expanded[0]\nout << ":"\nout << explorer_nested_expanded[0]\n'
    artifact = compile_source(modified, "xenon-ide-chevron-click.xe")
    assert artifact.success, artifact.diagnostics
    output: list[str] = []
    frame_number = 0
    context: RuntimeContext

    def on_frame(_frame) -> None:
        nonlocal frame_number
        frame_number += 1
        windows = context.vm.devices.windows
        handle = next(iter(windows._windows))
        input_device = context.vm.devices.input
        if frame_number == 1:
            input_device.move_pointer(windows.content_x(handle) + 8, windows.content_y(handle) + 26)
            input_device.set_button(LEFT_BUTTON, True)
        elif frame_number == 2:
            input_device.set_button(LEFT_BUTTON, False)
        elif frame_number == 3:
            input_device.move_pointer(windows.content_x(handle) + 16, windows.content_y(handle) + 34)
            input_device.set_button(LEFT_BUTTON, True)
        elif frame_number == 4:
            input_device.set_button(LEFT_BUTTON, False)

    context = RuntimeContext(frame_handler=on_frame, filesystem_root=drive)
    context.output_handler = output.append
    context.create_vm(list(artifact.program))
    context.vm.devices._pace_frame = lambda _vm: None
    result = context.vm.run()

    assert result.error is None
    assert "".join(output) == "1:1"


def test_blank_dirty_workspace_and_help_tab_are_bundled() -> None:
    output = run_ide_probe(
        '''out << xestring::strlen(source)
out << "|"
out << view_path[0]
out << "|"
out << view_dirty[0]
out << "|"
out << ide_tab_label(1)
out << "|"
out << view_count'''
    )
    assert output == "0|workspace.xe|1|Help|2"
    assert '"Xe source basics"' in SOURCE
    assert '"Control flow"' in SOURCE
    assert '"Functions and procedures"' in SOURCE
    assert '"Standard libraries"' in SOURCE
    assert '"Scratch-ready habits"' in SOURCE
    assert "help_scroll -= ide_scroll_delta" in SOURCE
    assert "creeperdoesredstone.github.io/xe-docs/" in SOURCE


def test_dirty_close_supports_cancel_save_and_discard(tmp_path: Path) -> None:
    cancel_drive = tmp_path / "cancel"
    cancel_drive.mkdir()
    cancel = run_ide_probe(
        '''call ide_close_view(0)
out << (int)close_prompt_open
out << ":"
out << close_prompt_slot
call ide_resolve_close_prompt(0)
out << ":"
out << (int)close_prompt_open
out << ":"
out << view_count''',
        cancel_drive,
    )
    assert cancel == "1:0:0:2"

    save_drive = tmp_path / "save"
    save_drive.mkdir()
    saved = run_ide_probe(
        '''call ide_close_view(0)
call ide_resolve_close_prompt(1)
out << view_count
out << ":"
out << ide_tab_label(active_view)''',
        save_drive,
    )
    assert saved == "1:Help"
    assert (save_drive / "workspace.xe").read_text(encoding="utf-8") == ""

    discard_drive = tmp_path / "discard"
    discard_drive.mkdir()
    discarded = run_ide_probe(
        '''call ide_close_view(0)
call ide_resolve_close_prompt(2)
out << view_count
out << ":"
out << ide_tab_label(active_view)''',
        discard_drive,
    )
    assert discarded == "1:Help"
    assert not (discard_drive / "workspace.xe").exists()


def test_file_picker_double_click_opens_file_immediately(tmp_path: Path) -> None:
    drive = tmp_path / "drive"
    drive.mkdir()
    (drive / "target.xe").write_text("out << 9", encoding="utf-8")

    output = run_ide_probe(
        '''call ide_begin_open_picker()
current_ticks = 100
call ide_select_open_picker_entry(0)
out << (int)open_picker_open
out << ":"
out << open_picker_selected
current_ticks = 350
call ide_select_open_picker_entry(0)
out << "|"
out << (int)open_picker_open
out << ":"
out << source
out << ":"
out << ide_tab_label(active_view)''',
        drive,
    )

    assert output == "1:0|0:out << 9:target.xe"


def test_primary_run_executes_workspace_and_active_run_remains_available(tmp_path: Path) -> None:
    workspace_drive = tmp_path / "workspace"
    workspace_drive.mkdir()
    (workspace_drive / "workspace.xe").write_text("out << 314159", encoding="utf-8")

    primary = run_ide_probe(
        '''call ide_set(source, "out << 314159")
dirty = true
call ide_run_primary()
out << terminal_text''',
        workspace_drive,
    )
    assert "Run workspace: workspace.xe" in primary
    assert "314159" in primary
    assert "active-only" not in primary

    active_drive = tmp_path / "active"
    active_drive.mkdir()
    (active_drive / "active.xe").write_text('out << "active-only"', encoding="utf-8")
    secondary = run_ide_probe(
        '''call ide_open_file("active.xe")
call ide_run_active_file()
out << terminal_text''',
        active_drive,
    )
    assert "Run active: active.xe" in secondary
    assert "active-only" in secondary


def test_primary_run_button_click_does_not_only_open_the_menu(tmp_path: Path) -> None:
    drive = tmp_path / "drive"
    drive.mkdir()
    modified = SOURCE.replace('source = ""', 'source = "out << 2718"', 1)
    modified = modified.replace(
        "while (ide_window.state != graphics::WINDOW_CLOSED) {",
        "var probe_frame: int\nprobe_frame = 0\nwhile (probe_frame < 6) {",
        1,
    )
    modified = modified.replace(
        "call graphics::update(ide_window)",
        "call graphics::update(ide_window)\n\tprobe_frame += 1",
        1,
    )
    modified += '\nout << (int)run_menu_open\nout << ":"\nout << terminal_text\n'
    artifact = compile_source(modified, "xenon-ide-run-click.xe")
    assert artifact.success, artifact.diagnostics
    output: list[str] = []
    frame_number = 0
    context: RuntimeContext

    def on_frame(_frame) -> None:
        nonlocal frame_number
        frame_number += 1
        windows = context.vm.devices.windows
        handle = next(iter(windows._windows))
        input_device = context.vm.devices.input
        if frame_number == 1:
            input_device.move_pointer(windows.content_x(handle) + 100, windows.content_y(handle) + 5)
            input_device.set_button(LEFT_BUTTON, True)
        elif frame_number == 2:
            input_device.set_button(LEFT_BUTTON, False)

    context = RuntimeContext(frame_handler=on_frame, filesystem_root=drive)
    context.output_handler = output.append
    context.create_vm(list(artifact.program))
    context.vm.devices._pace_frame = lambda _vm: None
    result = context.vm.run()
    rendered = "".join(output)

    assert result.error is None
    assert rendered.startswith("0:IDE terminal ready\nRun workspace: workspace.xe")
    assert "2718" in rendered


def test_main_is_the_nucleus_not_an_electron() -> None:
    output = run_ide_probe(
        '''var probe_index: int
var probe_main_count: int
call ide_set(source, "proc main() { }\\nfn helper() int { return 1 }")
dirty = true
call ide_store_active_document()
probe_index = 0
probe_main_count = 0
while (probe_index < view_script_count[0]) {
    if (view_script_name[ide_script_cache_index(0, probe_index)] == "main") { probe_main_count += 1 }
    probe_index += 1
}
out << view_script_count[0]
out << ":"
out << probe_main_count'''
    )
    assert output == "2:1"
    assert 'draw_text_small(ide_window, center_x - 8, center_y - 2, "main"' not in SOURCE
    assert 'if (dx * dx + dy * dy < 81)' in SOURCE
    assert 'draw_ide_text_clipped(x + 5, y + area_height - 9, "main"' in SOURCE
    assert 'view_script_name[ide_script_cache_index(slot, index)] != "main"' in SOURCE


def test_shell_hover_slows_and_direct_hover_stops() -> None:
    output = run_ide_probe(
        '''hovered_view = -1
hovered_atom = -1
rotation_dragging = false
out << ide_orbit_speed_target(0)
hovered_view = 0
out << ":"
out << ide_orbit_speed_target(0)
hovered_atom = 4
out << ":"
out << ide_orbit_speed_target(0)'''
    )
    assert output == "16:3:0"


def test_populated_visual_workspace_remains_responsive(tmp_path: Path) -> None:
    document = "proc main() { out << 1 }\n" + "\n".join(
        f"fn helper{index}() int {{ return {index} }}" for index in range(15)
    )
    literal = document.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    modified = SOURCE.replace('source = ""', f'source = "{literal}"', 1)
    before, separator, after = modified.rpartition("visual_mode = false")
    assert separator
    modified = before + "visual_mode = true" + after
    modified = modified.replace(
        "proc ide_rebuild_ring_cache(slot: int, outer_radius: int, inner_radius: int, shell_gap: int, shell_count: int) {",
        "var performance_ring_rebuilds: int\n"
        "proc ide_rebuild_ring_cache(slot: int, outer_radius: int, inner_radius: int, shell_gap: int, shell_count: int) {\n"
        "\tperformance_ring_rebuilds += 1",
        1,
    )
    modified = modified.replace(
        "while (ide_window.state != graphics::WINDOW_CLOSED) {",
        "var performance_frame: int\nperformance_frame = 0\nwhile (performance_frame < 6) {",
        1,
    )
    modified = modified.replace(
        "call graphics::update(ide_window)",
        "call graphics::update(ide_window)\n"
        "\tperformance_frame += 1\n"
        "\tif (performance_frame == 6) { out << performance_ring_rebuilds }",
        1,
    )
    artifact = compile_source(modified, "xenon-ide-visual-performance.xe")
    assert artifact.success, artifact.diagnostics

    drive = tmp_path / "drive"
    drive.mkdir()
    for index in range(64):
        (drive / f"file{index}.xe").write_text("", encoding="utf-8")
    timestamps: list[float] = []
    output: list[str] = []
    context = RuntimeContext(frame_handler=lambda _frame: timestamps.append(perf_counter()), filesystem_root=drive)
    context.output_handler = output.append
    context.create_vm(list(artifact.program))
    context.vm.devices._pace_frame = lambda _vm: None
    result = context.vm.run()

    assert result.error is None
    assert len(timestamps) == 6
    assert "".join(output) == "1"
    frame_seconds = (timestamps[-1] - timestamps[1]) / (len(timestamps) - 2)
    assert frame_seconds < 0.030


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
