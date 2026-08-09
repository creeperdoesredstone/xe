from pathlib import Path

from runtime import RuntimeContext
from xe_lang.compiler_service import compile_source
from xe_lang.devices.input import LEFT_BUTTON


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "apps" / "file_explorer.xe").read_text(encoding="utf-8")


def test_file_explorer_compiles() -> None:
    artifact = compile_source(SOURCE, "apps/file_explorer.xe")
    assert artifact.success, artifact.diagnostics


def test_projection_uses_unsigned_tilt_depth() -> None:
    assert "projection_depth_scale = math::sin" in SOURCE
    assert "if (projection_depth_scale < 0.0)" in SOURCE
    assert "projected_depth = (int)(sine * (float)radius * projection_depth_scale" in SOURCE


def test_nucleus_highlight_is_fixed_in_screen_space() -> None:
    body = SOURCE.split("proc draw_placeholder_nucleus", 1)[1].split("proc draw_placeholder_folder", 1)[0]
    assert "draw_placeholder_pixel(nucleus_highlight_x, nucleus_highlight_y" in body
    assert "orbit_project_rotation_offset" not in body
    anchor = SOURCE.split("base_center_radius =", 1)[1].split("visual_fade =", 1)[0]
    assert "!nucleus_highlight_ready || !rotating" in anchor
    assert "nucleus_highlight_x = viewport_center_x - center_radius / 2" in anchor


def test_depth_order_and_overlay_layers_are_explicit() -> None:
    assert "array entry_depth_order: int[64]" in SOURCE
    assert "Stable painter order" in SOURCE
    scene = SOURCE.index("call draw_file_atom(3, 12")
    drawer = SOURCE.index("call draw_explorer_sidebar(3, 12", scene)
    drag = SOURCE.index("call draw_explorer_drag_overlay()", drawer)
    modal = SOURCE.index("if (viewer_open)", drag)
    assert scene < drawer < drag < modal


def test_trash_is_subdued_until_valid_drag_hover() -> None:
    sidebar = SOURCE.split("proc draw_explorer_sidebar", 1)[1].split("proc draw_explorer_drag_overlay", 1)[0]
    assert "valid_drag = drag_entry >= 0 && drag_moved && graphics::mouse_down()" in sidebar
    assert "danger_color = surface" in sidebar
    assert "danger_text_color = visual_outline" in sidebar
    assert "danger_color = graphics::COLOR_4" in sidebar


def test_view_rotation_and_direct_hover_are_delta_time_eased() -> None:
    assert "proc update_view_orientation(elapsed_ms: int)" in SOURCE
    assert "maximum_step = elapsed_ms * 240 / 1000" in SOURCE
    drag = SOURCE.split("if (rotating && graphics::mouse_down())", 1)[1].split("if (graphics::mouse_released())", 1)[0]
    assert "rotation_target +=" in drag
    assert "last_pointer_x) * 2" not in drag
    assert "tilt_target +=" in drag
    assert "rotation += (graphics::pointer_x" not in drag
    hover = SOURCE.split("if (transition_progress == 0)", 1)[1].split("nucleus_phase =", 1)[0]
    assert "orbit_speed_target = 0" in hover
    assert "shell_speed[animation_shell] = 0" not in hover
    assert "if (!rotating && sidebar_open && sidebar_progress < 100)" in SOURCE
    assert "if (zoom_difference != 0 && !rotating)" in SOURCE


def _bounded_explorer_program(
    *,
    setup: str,
    frame_count: int,
    directory: bool = False,
    debug_markers: bool = False,
) -> tuple[int, ...]:
    create = (
        'operation_ok = os::make_directory("motion-folder")'
        if directory
        else 'operation_ok = os::make_file("motion.xe")'
    )
    modified = SOURCE.replace(
        "call refresh_explorer_style()\ncall reset_energy_shells()",
        f"call refresh_explorer_style()\n{create}\ncall reset_energy_shells()",
        1,
    )
    modified = modified.replace(
        "call reset_energy_shells()\nshell_button_hovered = false",
        f"call reset_energy_shells()\n{setup}\nshell_button_hovered = false",
        1,
    )
    if debug_markers:
        modified = modified.replace(
            "call draw_file_atom(3, 12, width - 6, height - 24)",
            "call draw_file_atom(3, 12, width - 6, height - 24)\n"
            "\t\t\tcall graphics::set_pixel(explorer_window, entry_render_x[0], entry_render_y[0], graphics::COLOR_14)\n"
            "\t\t\tcall graphics::set_pixel(explorer_window, nucleus_highlight_x, nucleus_highlight_y, graphics::COLOR_12)",
            1,
        )
    modified = modified.replace(
        "while (explorer_window.state != graphics::WINDOW_CLOSED) {",
        f"var regression_frame: int\nregression_frame = 0\nwhile (regression_frame < {frame_count}) {{",
        1,
    )
    modified = modified.replace(
        "call graphics::update(explorer_window)",
        "call graphics::update(explorer_window)\n\tregression_frame += 1",
        1,
    )
    artifact = compile_source(modified, "file-explorer-motion-regression.xe")
    assert artifact.success, artifact.diagnostics
    return artifact.program


def _single_color_position(frame, color: int) -> tuple[int, int]:
    matches: list[tuple[int, int]] = []
    for y in range(62, 262):
        row = y * frame.width
        for x in range(68, 364):
            if frame.indices[row + x] == color:
                matches.append((x, y))
    assert len(matches) == 1, matches[:20]
    return matches[0]


def _run_explorer_probe(
    tmp_path: Path,
    probe: str,
    *,
    clipboard_read_handler=None,
    clipboard_write_handler=None,
) -> str:
    anchor = "while (explorer_window.state != graphics::WINDOW_CLOSED) {"
    assert anchor in SOURCE
    modified = SOURCE.replace(anchor, f"{probe}\nif (false) {{", 1)
    artifact = compile_source(modified, "file-explorer-vfs-regression.xe")
    assert artifact.success, artifact.diagnostics
    output: list[str] = []
    context = RuntimeContext(
        filesystem_root=tmp_path,
        clipboard_read_handler=clipboard_read_handler,
        clipboard_write_handler=clipboard_write_handler,
    )
    context.output_handler = output.append
    context.create_vm(list(artifact.program))
    result = context.vm.run()
    assert result.error is None, result.error
    return "".join(output)


def test_rename_clipboard_uses_host_and_keeps_local_fallback(tmp_path: Path) -> None:
    writes: list[str] = []
    host_output = _run_explorer_probe(
        tmp_path / "host",
        '''call explorer_set(rename_text, "copy-name.xe")
rename_cursor = xestring::strlen(rename_text)
call explorer_select_all_rename_text()
call explorer_copy_rename_selection()
call explorer_set(rename_text, "old.xe")
rename_cursor = xestring::strlen(rename_text)
call explorer_select_all_rename_text()
        call explorer_paste_rename_selection()
out << rename_text''',
        clipboard_read_handler=lambda: "host-name.xe",
        clipboard_write_handler=lambda value: not writes.append(value),
    )
    assert writes == ["copy-name.xe"]
    assert host_output == "host-name.xe"

    fallback_probe = '''call explorer_set(rename_clipboard, "fallback.xe")
call explorer_set(rename_text, "old.xe")
rename_cursor = xestring::strlen(rename_text)
call explorer_select_all_rename_text()
call explorer_paste_rename_selection()
out << rename_text'''
    assert _run_explorer_probe(tmp_path / "disabled", fallback_probe) == "fallback.xe"
    assert _run_explorer_probe(
        tmp_path / "invalid",
        fallback_probe,
        clipboard_read_handler=lambda: "bad/name",
    ) == "fallback.xe"
    assert _run_explorer_probe(
        tmp_path / "oversized",
        fallback_probe,
        clipboard_read_handler=lambda: "x" * 65,
    ) == "fallback.xe"


def test_multi_delete_create_and_navigation_keep_cache_bound_to_vfs_identity(tmp_path: Path) -> None:
    for directory in ("alpha-dir", "beta-dir", "keep-dir"):
        (tmp_path / directory).mkdir()
    for filename in ("alpha.xe", "delete-a.xe", "delete-b.xe", "keep.xe"):
        (tmp_path / filename).write_text(filename, encoding="utf-8")

    output = _run_explorer_probe(
        tmp_path,
        '''var probe_index: int
var probe_created: int
call clear_entry_selection()
probe_index = find_entry_named("beta-dir")
entry_selected[probe_index] = 1
drag_entry = probe_index
probe_index = find_entry_named("delete-a.xe")
entry_selected[probe_index] = 1
probe_index = find_entry_named("delete-b.xe")
entry_selected[probe_index] = 1
call delete_dragged_entries()
probe_index = find_entry_named("keep.xe")
entry_render_x[probe_index] = 777
entry_slot_progress[probe_index] = 37
entry_selected[probe_index] = 1
selected_entry = probe_index
selection_anchor = probe_index
operation_ok = os::make_directory("new-folder")
probe_created = cache_created_entry("new-folder", true, 0, 111, 77, 13, 220)
out << "root:"
probe_index = 0
while (probe_index < cached_entry_count) {
	out << entry_name_cache[probe_index]
	out << "="
	out << os::entry_name(current_path, probe_index)
	out << ";"
	probe_index += 1
}
probe_index = find_entry_named("keep.xe")
out << "identity:"
out << entry_render_x[probe_index]
out << ","
out << entry_slot_progress[probe_index]
out << ","
out << entry_selected[probe_index]
out << ","
out << (int)(selected_entry == probe_index)
out << ","
out << entry_slot_start_x[probe_created]
out << "|"
operation_ok = os::make_file("new-folder/child.xe")
call begin_folder_transition(find_entry_named("new-folder"), 1)
call commit_folder_transition()
out << "child:"
out << current_path
out << ":"
out << entry_name_cache[0]
call begin_folder_transition(-1, -1)
call commit_folder_transition()
out << "|back:"
probe_index = 0
while (probe_index < cached_entry_count) {
	out << entry_name_cache[probe_index]
	out << "="
	out << os::entry_name(current_path, probe_index)
	out << ";"
	probe_index += 1
}
operation_ok = os::make_file("aardvark.xe")
call refresh_compacted_entry_metadata()
out << "|revision:"
probe_index = 0
while (probe_index < cached_entry_count) {
	out << entry_name_cache[probe_index]
	out << "="
	out << os::entry_name(current_path, probe_index)
	out << ";"
	probe_index += 1
}
out << "|gone:"
out << (int)os::path_exists("beta-dir")
out << (int)os::path_exists("delete-a.xe")
out << (int)os::path_exists("delete-b.xe")''',
    )

    assert output == (
        "root:alpha-dir=alpha-dir;keep-dir=keep-dir;new-folder=new-folder;"
        "alpha.xe=alpha.xe;keep.xe=keep.xe;"
        "identity:777,37,1,1,111|"
        "child:new-folder:child.xe|"
        "back:alpha-dir=alpha-dir;keep-dir=keep-dir;new-folder=new-folder;"
        "alpha.xe=alpha.xe;keep.xe=keep.xe;|"
        "revision:alpha-dir=alpha-dir;keep-dir=keep-dir;new-folder=new-folder;"
        "aardvark.xe=aardvark.xe;alpha.xe=alpha.xe;keep.xe=keep.xe;|gone:000"
    )
    assert not (tmp_path / "beta-dir").exists()
    assert not (tmp_path / "delete-a.xe").exists()
    assert not (tmp_path / "delete-b.xe").exists()
    trash_names = [path.name for path in (tmp_path / ".xenon-trash").iterdir()]
    assert any(name.endswith("-beta-dir") for name in trash_names)
    assert any(name.endswith("-delete-a.xe") for name in trash_names)
    assert any(name.endswith("-delete-b.xe") for name in trash_names)


def test_drag_rotation_is_smooth_and_nucleus_highlight_is_frame_fixed(tmp_path: Path) -> None:
    program = _bounded_explorer_program(
        setup=(
            "sidebar_open = true\nsidebar_progress = 50\n"
            "orbit_speed_setting = 0\nanimation_shell = 0\n"
            "while (animation_shell < 8) {\n"
            "\tshell_speed[animation_shell] = 0\n"
            "\tanimation_shell += 1\n}"
        ),
        frame_count=12,
        debug_markers=True,
    )
    frames = []
    context: RuntimeContext

    def on_frame(frame) -> None:
        frames.append(frame)
        if len(frames) == 1:
            context.vm.devices.input.move_pointer(68 + 184, 62 + 118)

    context = RuntimeContext(frame_handler=on_frame, filesystem_root=tmp_path / "motion")
    context.create_vm(list(program))
    context.vm.devices.input.move_pointer(68 + 160, 62 + 100)
    context.vm.devices.input.set_button(LEFT_BUTTON, True)
    result = context.vm.run()
    assert result.error is None, result.error
    assert len(frames) == 12

    highlights = [_single_color_position(frame, 12) for frame in frames]
    assert len(set(highlights)) == 1

    positions = [_single_color_position(frame, 14) for frame in frames]
    displacements = [
        abs(right[0] - left[0]) + abs(right[1] - left[1])
        for left, right in zip(positions[1:], positions[2:])
    ]
    assert max(displacements) <= 4
    assert sum(step > 0 for step in displacements) >= 4
    assert abs(positions[-1][0] - positions[1][0]) + abs(positions[-1][1] - positions[1][1]) >= 6


def _render_sidebar_state(tmp_path: Path, setup: str, pointer: tuple[int, int]):
    program = _bounded_explorer_program(setup=setup, frame_count=1, directory=True)
    frames = []
    context = RuntimeContext(frame_handler=frames.append, filesystem_root=tmp_path)
    context.create_vm(list(program))
    context.vm.devices.input.move_pointer(68 + pointer[0], 62 + pointer[1])
    context.vm.devices.input.set_button(LEFT_BUTTON, True)
    result = context.vm.run()
    assert result.error is None, result.error
    assert len(frames) == 1
    return frames[0]


def test_dragged_atom_renders_above_drawer_and_trash_only_arms_for_a_valid_drag(tmp_path: Path) -> None:
    setup = "sidebar_open = true\nsidebar_progress = 100\ndrag_entry = 0\ndrag_moved = true"
    overlay = _render_sidebar_state(tmp_path / "overlay", setup, (20, 40))
    assert overlay.indices[(62 + 40) * overlay.width + (68 + 23)] == 10

    idle = _render_sidebar_state(
        tmp_path / "idle",
        "sidebar_open = true\nsidebar_progress = 100",
        (10, 55),
    )
    active = _render_sidebar_state(tmp_path / "active", setup, (10, 55))
    sample = (62 + 50) * idle.width + (68 + 79)
    assert idle.indices[sample] == 8
    assert active.indices[sample] == 4
