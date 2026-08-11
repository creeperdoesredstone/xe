from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from runtime import RuntimeContext, run


ROOT = Path(__file__).resolve().parents[1]
IDE_SOURCE = (ROOT / "apps" / "xenon_ide.xe").read_text(encoding="utf-8")
IDE_LOOP_ANCHOR = "while (ide_window.state != graphics::WINDOW_CLOSED) {"
PARENT_MARKER_X = 252
PARENT_MARKER_Y = 210


def _run_ide_probe(probe: str, filesystem_root: Path) -> str:
    assert IDE_LOOP_ANCHOR in IDE_SOURCE
    modified = IDE_SOURCE.replace(IDE_LOOP_ANCHOR, f"{probe}\nif (false) {{", 1)
    output: list[str] = []
    context = RuntimeContext(filesystem_root=filesystem_root)
    context.output_handler = output.append
    with redirect_stdout(StringIO()):
        _, error, _ = run("xenon-ide-regression-probe.xe", modified, context)
    assert error is None
    return "".join(output)


def test_horizontal_scroll_clamps_and_survives_tab_round_trip(tmp_path: Path) -> None:
    long_line = "# " + "wide-source-" * 96
    output = _run_ide_probe(
        f'''call ide_set(source, {json.dumps(long_line)})
call ide_invalidate_source_metrics()
cursor_index = 0
selection_start = 0
selection_end = 0
source_follow_cursor = false
source_scroll_column = 999999
call ide_clamp_source_view(120, 80)
var clamped_scroll: int
var maximum_width: int
clamped_scroll = source_scroll_column
maximum_width = ide_source_maximum_width()
call ide_store_active_document()
call ide_activate_view(1)
call ide_activate_view(0)
out << clamped_scroll
out << ":"
out << source_scroll_column
out << ":"
out << view_scroll_column[0]
out << ":"
out << maximum_width
out << ":"
source_scroll_column = -999999
call ide_clamp_source_view(120, 80)
out << source_scroll_column''',
        tmp_path,
    )

    clamped, restored, saved, maximum_width, minimum = map(int, output.split(":"))
    assert clamped == maximum_width - (120 - 22)
    assert restored == clamped
    assert saved == clamped
    assert minimum == 0


def _run_graphical_child(
    tmp_path: Path,
    child_source: str,
    *,
    cancel_on_first_child_frame: bool = False,
) -> tuple[list, str, object | None]:
    drive = tmp_path / "drive"
    drive.mkdir()
    (drive / "workspace.xe").write_text(child_source, encoding="utf-8")

    frames = []
    output: list[str] = []
    context: RuntimeContext

    def on_frame(frame) -> None:
        frames.append(frame)
        if cancel_on_first_child_frame and len(frames) == 2:
            context.cancel()

    context = RuntimeContext(filesystem_root=drive, frame_handler=on_frame)
    context.output_handler = output.append
    _, error, _ = run(
        "graphical-parent-regression.xe",
        '''var parent: graphics::Window
var child_output: string
parent.x = 230
parent.y = 170
parent.width = 200
parent.height = 140
parent.title = "Virtual IDE"
parent.ui_scale = 1
parent.state = graphics::WINDOW_NORMAL
call graphics::begin_draw(parent)
call graphics::clear(parent, graphics::COLOR_1)
call graphics::fill_rect(parent, 20, 20, 36, 16, graphics::COLOR_12)
call graphics::update(parent)
child_output = compiler::run_workspace("workspace.xe")
out << child_output''',
        context,
    )
    return frames, "".join(output), error


def _assert_parent_survives_child(frames: list) -> None:
    assert len(frames) >= 3
    parent = frames[0]
    marker = PARENT_MARKER_Y * parent.width + PARENT_MARKER_X
    assert parent.indices[marker] == 12
    assert any(frame.indices != parent.indices for frame in frames[1:-1])
    assert all(frame.indices[marker] == 12 for frame in frames[1:-1])
    assert frames[-1].indices == parent.indices


def test_graphical_child_runtime_error_restores_parent_frame(tmp_path: Path) -> None:
    frames, output, error = _run_graphical_child(
        tmp_path,
        '''var child: graphics::Window
var zero: int
var crash: int
child.x = 24
child.y = 20
child.width = 120
child.height = 80
child.title = "Child"
child.ui_scale = 1
child.state = graphics::WINDOW_NORMAL
call graphics::begin_draw(child)
call graphics::clear(child, graphics::BLACK)
call graphics::fill_rect(child, 8, 8, 40, 18, graphics::COLOR_5)
call graphics::update(child)
zero = 0
crash = 1 / zero''',
    )

    assert error is None
    assert "Runtime error: Division by 0" in output
    _assert_parent_survives_child(frames)


def test_canceled_graphical_child_restores_parent_frame(tmp_path: Path) -> None:
    frames, output, error = _run_graphical_child(
        tmp_path,
        '''var child: graphics::Window
child.x = 24
child.y = 20
child.width = 120
child.height = 80
child.title = "Child"
child.ui_scale = 1
child.state = graphics::WINDOW_NORMAL
while (child.state != graphics::WINDOW_CLOSED) {
    call graphics::begin_draw(child)
    call graphics::clear(child, graphics::BLACK)
    call graphics::fill_rect(child, 8, 8, 40, 18, graphics::COLOR_5)
    call graphics::update(child)
}''',
        cancel_on_first_child_frame=True,
    )

    assert error is None
    assert output == "Runtime canceled."
    _assert_parent_survives_child(frames)
