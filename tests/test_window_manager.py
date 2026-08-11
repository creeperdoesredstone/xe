from __future__ import annotations

from xe_lang.design_tokens import WINDOW_COMPONENT_TOKENS
from xe_lang.devices.graphics import GraphicsDevice
from xe_lang.devices.input import InputDevice, LEFT_BUTTON
from xe_lang.devices.os_state import OSDevice
from xe_lang.devices.windows import EVENT_MAXIMIZED, EVENT_RESIZED, Rect, WindowManager, WindowState


def _frame(manager: WindowManager, input_device: InputDevice, handle: int) -> int:
	event = manager.update(handle)
	input_device.finish_frame()
	return event


def test_resize_uses_outline_and_commits_only_on_release() -> None:
	graphics = GraphicsDevice(480, 360)
	input_device = InputDevice(480, 360)
	manager = WindowManager(graphics, input_device)
	handle = manager.create(40, 30, 180, 120, "Resize")
	original = manager.record(handle).bounds.copy()

	input_device.move_pointer(original.x + original.width - 1, original.y + 60)
	input_device.set_button(LEFT_BUTTON, True)
	_frame(manager, input_device, handle)
	input_device.move_pointer(original.x + original.width + 47, original.y + 60)
	_frame(manager, input_device, handle)
	assert manager.record(handle).bounds == original
	assert manager._resize is not None
	assert manager._resize.outline.width == original.width + 48

	input_device.set_button(LEFT_BUTTON, False)
	event = _frame(manager, input_device, handle)
	assert event & EVENT_RESIZED
	assert manager.record(handle).bounds.width == original.width + 48


def test_top_snap_maximizes_with_one_eased_transition_and_restores_under_pointer() -> None:
	clock = [0.0]
	graphics = GraphicsDevice(480, 360)
	input_device = InputDevice(480, 360)
	manager = WindowManager(graphics, input_device, clock=lambda: clock[0])
	handle = manager.create(60, 45, 220, 150, "Snap")
	original = manager.record(handle).bounds.copy()

	input_device.move_pointer(140, 52)
	input_device.set_button(LEFT_BUTTON, True)
	_frame(manager, input_device, handle)
	input_device.move_pointer(220, 0)
	_frame(manager, input_device, handle)
	assert manager._drag is not None and manager._drag.snap_maximize
	assert manager._drag.outline == Rect(0, 0, 480, 360)
	input_device.set_button(LEFT_BUTTON, False)
	_frame(manager, input_device, handle)
	assert manager.is_transitioning(handle)

	clock[0] = 0.3
	event = _frame(manager, input_device, handle)
	assert event & EVENT_MAXIMIZED
	assert manager.record(handle).state == WindowState.MAXIMIZED
	assert manager.record(handle).bounds == Rect(0, 0, 480, 360)

	input_device.move_pointer(360, 8)
	input_device.set_button(LEFT_BUTTON, True)
	_frame(manager, input_device, handle)
	input_device.move_pointer(300, 60)
	event = _frame(manager, input_device, handle)
	assert manager.record(handle).state == WindowState.NORMAL
	assert manager.record(handle).bounds.width == original.width
	assert manager.record(handle).bounds.contains(300, 60)
	assert manager.is_dragging(handle)
	assert event


def test_legacy_window_transparency_is_normalized_and_content_stays_opaque() -> None:
	appearance = OSDevice()
	appearance.set_window_transparency(50)
	assert appearance.window_transparency == 0
	graphics = GraphicsDevice(120, 90)
	graphics.clear(2)
	input_device = InputDevice(120, 90)
	manager = WindowManager(graphics, input_device, appearance=appearance)
	handle = manager.create(10, 8, 90, 70, "Glass", ui_scale=1)
	manager.draw(handle)
	manager.clear_content(handle, 5)

	bounds = manager.record(handle).bounds
	content = [
		graphics.back_buffer[y][x]
		for y in range(manager.content_y(handle), bounds.y + bounds.height - manager.theme.border_width)
		for x in range(manager.content_x(handle), bounds.x + bounds.width - manager.theme.border_width)
	]
	assert set(content) == {5}


def test_default_window_theme_is_derived_from_component_tokens() -> None:
	manager = WindowManager(GraphicsDevice(120, 90), InputDevice(120, 90))
	for name in (
		"title_height",
		"border_width",
		"border_color",
		"title_color",
		"content_color",
		"text_color",
		"button_color",
		"control_size",
		"control_gap",
	):
		assert getattr(manager.theme, name) == getattr(WINDOW_COMPONENT_TOKENS, name)


def test_button_labels_stay_on_the_logical_pixel_grid_through_hover() -> None:
	def render_label(*, hovered: bool, height: int) -> tuple[int, int, Rect]:
		graphics = GraphicsDevice(240, 160)
		input_device = InputDevice(240, 160)
		manager = WindowManager(graphics, input_device)
		handle = manager.create(12, 10, 200, 130, "Buttons", ui_scale=2)
		manager.draw(handle)
		origin_x, origin_y = manager.draw_origin(handle)
		if hovered:
			input_device.move_pointer(origin_x + 20, origin_y + 10)
		else:
			input_device.move_pointer(0, 0)

		coordinates: list[tuple[int, int]] = []
		if height <= 9:
			original = graphics.draw_text_small

			def record(x: int, y: int, *args, **kwargs) -> None:
				coordinates.append((x, y))
				original(x, y, *args, **kwargs)

			graphics.draw_text_small = record  # type: ignore[method-assign]
		else:
			original = graphics.draw_text

			def record(x: int, y: int, *args, **kwargs) -> None:
				coordinates.append((x, y))
				original(x, y, *args, **kwargs)

			graphics.draw_text = record  # type: ignore[method-assign]

		manager.button(handle, 5, 5, 31, height, "Delete", 4, False)
		assert len(coordinates) == 1
		return coordinates[0][0], coordinates[0][1], Rect(origin_x + 10, origin_y + 10, 62, height * 2)

	for height in (9, 12):
		normal_x, normal_y, rect = render_label(hovered=False, height=height)
		hover_x, hover_y, _ = render_label(hovered=True, height=height)
		assert (normal_x, normal_y) == (hover_x, hover_y)
		assert (normal_x - rect.x) % 2 == 0
		assert (normal_y - rect.y) % 2 == 0
