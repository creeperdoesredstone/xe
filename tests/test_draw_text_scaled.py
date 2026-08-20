from __future__ import annotations

import pytest

from runtime import RuntimeContext, run
from xe_lang.compiler_service import capability_for_syscall, compile_source, syscall_name
from xe_lang.devices.graphics import GraphicsDevice
from xe_lang.stdlib.specs import GRAPHICS_SPEC
from xe_lang.syscall_abi import APP_GRAPHICS_DRAW_TEXT_SCALED


def _pixels(device: GraphicsDevice) -> bytes:
	return b"".join(device.front_buffer)


def _render_screen(call: str) -> bytes:
	frames = []
	context = RuntimeContext(frame_handler=frames.append)
	_, error, _ = run(
		"draw-text-scaled-screen.xe",
		f"""
var screen: graphics::Screen
call graphics::begin_draw(screen)
call graphics::clear(screen, graphics::COLOR_3)
{call}
call graphics::update(screen)
""".strip(),
		context,
	)
	assert error is None, error
	assert len(frames) == 1
	return _pixels(context.vm.devices.graphics)


def _render_window(*, draw_text: bool) -> tuple[bytes, RuntimeContext]:
	frames = []
	context = RuntimeContext(frame_handler=frames.append)
	text_call = (
		'call graphics::draw_text_scaled(win, 2, 3, "A", graphics::COLOR_ICON_A, 2)'
		if draw_text else ""
	)
	_, error, _ = run(
		"draw-text-scaled-window.xe",
		f"""
var win: graphics::Window
win.x = 10
win.y = 10
win.width = 160
win.height = 110
win.title = "Scaled"
win.ui_scale = 2
call graphics::begin_draw(win)
call graphics::clear(win, graphics::COLOR_3)
{text_call}
call graphics::update(win)
""".strip(),
		context,
	)
	assert error is None, error
	assert len(frames) == 1
	return _pixels(context.vm.devices.graphics), context


def test_draw_text_scaled_public_api_and_icon_palette_aliases() -> None:
	artifact = compile_source(
		"""
var screen: graphics::Screen
var win: graphics::Window
call graphics::draw_text_scaled(screen, 4, 5, "Text", graphics::COLOR_ICON_A, 2)
call graphics::draw_text_scaled(win, 4, 5, "Text", graphics::COLOR_ICON_F, 2)
""".strip(),
		"draw-text-scaled-api.xe",
	)
	assert artifact.success, artifact.diagnostics
	assert APP_GRAPHICS_DRAW_TEXT_SCALED in artifact.required_syscalls
	assert capability_for_syscall(APP_GRAPHICS_DRAW_TEXT_SCALED) == "app.graphics"
	assert syscall_name(APP_GRAPHICS_DRAW_TEXT_SCALED) == "APP_GRAPHICS_DRAW_TEXT_SCALED"

	constants = {constant.name: constant.value for constant in GRAPHICS_SPEC.constants}
	assert [constants[f"COLOR_ICON_{index}"] for index in range(10)] == list(range(10))
	assert [constants[f"COLOR_ICON_{letter}"] for letter in "ABCDEF"] == list(range(10, 16))


def test_draw_text_scaled_matches_existing_text_at_scale_one() -> None:
	regular = _render_screen(
		'call graphics::draw_text(screen, 7, 9, "Text", graphics::COLOR_ICON_A)'
	)
	scaled = _render_screen(
		'call graphics::draw_text_scaled(screen, 7, 9, "Text", graphics::COLOR_ICON_A, 1)'
	)
	assert scaled == regular


def test_draw_text_scaled_matches_proportional_font_scaling_and_clipping() -> None:
	actual = _render_screen(
		'call graphics::draw_text_scaled(screen, -2, -1, "A\\n\\t!", graphics::COLOR_ICON_A, 2)'
	)
	oracle = GraphicsDevice()
	oracle.clear_both(3)
	oracle.draw_text(-2, -1, "A\n\t!", 10, pixel_scale=2)
	assert actual == b"".join(oracle.back_buffer)


@pytest.mark.parametrize("scale", (0, -1, -2_147_483_648))
def test_draw_text_scaled_nonpositive_scale_is_a_noop(scale: int) -> None:
	actual = _render_screen(
		f'call graphics::draw_text_scaled(screen, 2, 3, "A", graphics::COLOR_ICON_F, {scale})'
	)
	assert actual == bytes((3,)) * (480 * 360)


def test_draw_text_scaled_caps_scale_at_sixteen() -> None:
	maximum = _render_screen(
		'call graphics::draw_text_scaled(screen, 1, 1, "A", graphics::COLOR_ICON_F, 16)'
	)
	oversized = _render_screen(
		'call graphics::draw_text_scaled(screen, 1, 1, "A", graphics::COLOR_ICON_F, 2147483647)'
	)
	assert oversized == maximum


def test_draw_text_scaled_composes_with_window_ui_scale() -> None:
	baseline, _ = _render_window(draw_text=False)
	actual, context = _render_window(draw_text=True)
	windows = context.vm.devices.windows
	assert windows.ui_scale(1) == 2

	oracle = GraphicsDevice()
	for row in range(oracle.height):
		start = row * oracle.width
		oracle.back_buffer[row][:] = baseline[start:start + oracle.width]
	oracle.set_clip(
		windows.content_x(1),
		windows.content_y(1),
		windows.content_width(1),
		windows.content_height(1),
	)
	oracle.draw_text(
		windows.content_x(1) + 4,
		windows.content_y(1) + 6,
		"A",
		10,
		pixel_scale=4,
	)
	assert actual == b"".join(oracle.back_buffer)
