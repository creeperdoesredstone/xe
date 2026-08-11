from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source
from xe_lang.devices.input import LEFT_BUTTON


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "apps" / "xenon_daw.xe"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
LOOP_ANCHOR = "while (daw_window.state != graphics::WINDOW_CLOSED) {"
UPDATE_ANCHOR = "call graphics::update(daw_window)"


def _probe_source(body: str) -> str:
    assert LOOP_ANCHOR in SOURCE
    return SOURCE.replace(LOOP_ANCHOR, f"{body}\nwhile (false) {{", 1)


def _run_probe(body: str) -> str:
    output: list[str] = []
    context = RuntimeContext()
    context.output_handler = output.append
    with redirect_stdout(StringIO()):
        _, error, _ = run("xenon-daw-probe.xe", _probe_source(body), context)
    assert error is None, str(error)
    return "".join(output)


def _finite_source(frame_count: int, *, width: int, height: int, suffix: str = "") -> str:
    source = SOURCE.replace("daw_window.width = APP_DEFAULT_WIDTH", f"daw_window.width = {width}", 1)
    source = source.replace("daw_window.height = APP_DEFAULT_HEIGHT", f"daw_window.height = {height}", 1)
    source = source.replace(
        LOOP_ANCHOR,
        f"var probe_frame: int\nprobe_frame = 0\nwhile (probe_frame < {frame_count}) {{",
        1,
    )
    source = source.replace(
        UPDATE_ANCHOR,
        f"{UPDATE_ANCHOR}\n\tprobe_frame += 1",
        1,
    )
    return source + suffix


def _run_frames(source: str, frame_handler=None):
    frames = []

    def collect(frame) -> None:
        frames.append(frame)
        if frame_handler is not None:
            frame_handler(frame, len(frames), context)

    output: list[str] = []
    context = RuntimeContext(frame_handler=collect)
    context.output_handler = output.append
    artifact = compile_source(source, "xenon-daw-frame-probe.xe")
    assert artifact.success, artifact.diagnostics
    context.create_vm(list(artifact.program))
    context.vm.devices._pace_frame = lambda _vm: None
    result = context.vm.run()
    assert result.error is None, str(result.error)
    return frames, "".join(output), context


def _content_indices(frame, context: RuntimeContext) -> list[int]:
    windows = context.vm.devices.windows
    origin_x = windows.content_x(1)
    origin_y = windows.content_y(1)
    width = windows.content_width(1)
    height = windows.content_height(1)
    pixels: list[int] = []
    for y in range(origin_y, origin_y + height):
        row = y * frame.width
        pixels.extend(frame.indices[row + origin_x : row + origin_x + width])
    return pixels


def test_source_compiles_and_uses_standard_app_boundaries() -> None:
    artifact = compile_source(SOURCE, str(SOURCE_PATH))
    assert artifact.success, artifact.diagnostics
    assert 'const APP_TITLE = "Xenon DAW"' in SOURCE
    assert "const APP_DEFAULT_WIDTH = 300" in SOURCE
    assert "const APP_DEFAULT_HEIGHT = 230" in SOURCE
    for boundary in (
        "# Model and state.",
        "# Layout helpers.",
        "# Input and interaction.",
        "# Paint and replaceable asset hooks.",
        "# Application driver.",
    ):
        assert SOURCE.count(boundary) == 1


def test_all_channels_toggle_pitch_zero_and_reject_out_of_range_data() -> None:
    output = _run_probe(
        """out << note_at(0, 0)
out << ","
call toggle_note(0, 0, 2)
out << note_at(0, 2)
out << ","
call toggle_note(0, 0, 2)
out << note_at(0, 2)
out << ","
call toggle_note(-1, 10, 0)
call toggle_note(256, 10, 0)
call toggle_note(0, -1, 0)
call toggle_note(0, 96, 0)
call toggle_note(0, 10, 3)
out << note_at(0, 0)"""
    )
    assert output == "-1,0,-1,-1"


def test_layout_clamps_scroll_and_bounds_tiny_and_oversized_views() -> None:
    output = _run_probe(
        """scroll_x = 999
scroll_y = 999
call compute_layout(76, 34)
out << daw_visible_steps
out << ","
out << daw_visible_notes
out << ","
out << scroll_x
out << ","
out << scroll_y
out << "|"
scroll_x = 999
scroll_y = 999
call compute_layout(3000, 1000)
out << daw_visible_steps
out << ","
out << daw_visible_notes
out << ","
out << scroll_x
out << ","
out << scroll_y
out << ","
out << grid_view_width
out << ","
out << grid_view_height"""
    )
    tiny, oversized = output.split("|", 1)
    assert tuple(map(int, tiny.split(","))) == (4, 2, 252, 94)
    visible_steps, visible_notes, scroll_column, scroll_note, width, height = map(
        int, oversized.split(",")
    )
    assert (visible_steps, visible_notes, scroll_column, scroll_note) == (256, 96, 0, 0)
    assert 0 <= width <= 3000
    assert 0 <= height <= 1000


def test_default_and_absolute_minimum_windows_render_useful_frames() -> None:
    for width, height, minimum_colors, minimum_non_background in (
        (300, 230, 8, 800),
        (80, 48, 7, 80),
    ):
        frames, _, context = _run_frames(_finite_source(1, width=width, height=height))
        assert len(frames) == 1
        content = _content_indices(frames[0], context)
        assert len(set(content)) >= minimum_colors
        assert sum(color != 0 for color in content) >= minimum_non_background


def test_channel_click_and_grid_click_use_window_local_geometry() -> None:
    source = _finite_source(
        5,
        width=300,
        height=230,
        suffix=(
            "\nout << current_channel\nout << \",\"\n"
            "out << channel_1_grid[0]\nout << \",\"\n"
            "out << channel_3_grid[0]\n"
        ),
    )

    def interact(_frame, frame_number: int, context: RuntimeContext) -> None:
        windows = context.vm.devices.windows
        origin_x = windows.content_x(1)
        origin_y = windows.content_y(1)
        input_device = context.vm.devices.input
        if frame_number == 1:
            input_device.move_pointer(origin_x + 105, origin_y + 5)
            input_device.set_button(LEFT_BUTTON, True)
        elif frame_number == 2:
            input_device.set_button(LEFT_BUTTON, False)
        elif frame_number == 3:
            input_device.move_pointer(origin_x + 28, origin_y + 15)
            input_device.set_button(LEFT_BUTTON, True)
        elif frame_number == 4:
            input_device.set_button(LEFT_BUTTON, False)

    frames, output, context = _run_frames(source, interact)
    assert len(frames) == 5
    assert output == "2,-1,24"
    origin_x = context.vm.devices.windows.content_x(1)
    origin_y = context.vm.devices.windows.content_y(1)
    note_pixel = (origin_y + 14) * frames[3].width + origin_x + 27
    assert frames[3].indices[note_pixel] == 14


def test_visible_scrollbar_arrows_move_both_axes() -> None:
    source = _finite_source(
        5,
        width=300,
        height=230,
        suffix='\nout << scroll_y\nout << ","\nout << scroll_x\n',
    )

    def click_arrows(_frame, frame_number: int, context: RuntimeContext) -> None:
        windows = context.vm.devices.windows
        origin_x = windows.content_x(1)
        origin_y = windows.content_y(1)
        input_device = context.vm.devices.input
        if frame_number == 1:
            input_device.move_pointer(origin_x + 280, origin_y + 192)
            input_device.set_button(LEFT_BUTTON, True)
        elif frame_number == 2:
            input_device.set_button(LEFT_BUTTON, False)
        elif frame_number == 3:
            input_device.move_pointer(origin_x + 289, origin_y + 17)
            input_device.set_button(LEFT_BUTTON, True)
        elif frame_number == 4:
            input_device.set_button(LEFT_BUTTON, False)

    frames, output, _ = _run_frames(source, click_arrows)
    assert len(frames) == 5
    assert output == "1,1"


def test_plain_and_shift_wheel_scroll_the_expected_axes() -> None:
    source = _finite_source(
        4,
        width=300,
        height=230,
        suffix='\nout << scroll_y\nout << ","\nout << scroll_x\n',
    )

    def scroll(_frame, frame_number: int, context: RuntimeContext) -> None:
        windows = context.vm.devices.windows
        input_device = context.vm.devices.input
        input_device.move_pointer(windows.content_x(1) + 80, windows.content_y(1) + 80)
        if frame_number == 1:
            input_device.add_scroll_delta(2)
        elif frame_number == 2:
            input_device.set_key(16, True, modifiers=1)
            input_device.add_scroll_delta(-3)
        elif frame_number == 3:
            input_device.set_key(16, False, modifiers=0)

    frames, output, _ = _run_frames(source, scroll)
    assert len(frames) == 4
    assert output == "6,12"
