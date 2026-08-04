from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .graphics import GraphicsDevice
from .input import InputDevice, InputFrame


EVENT_NONE = 0
EVENT_MOVED = 1
EVENT_CLOSED = 2
EVENT_MINIMIZED = 4
EVENT_MAXIMIZED = 8
EVENT_RESTORED = 16
EVENT_RESIZED = 32


class WindowState(IntEnum):
	NORMAL = 0
	MINIMIZED = 1
	MAXIMIZED = 2
	CLOSED = 3


@dataclass
class Rect:
	x: int
	y: int
	width: int
	height: int

	def contains(self, x: int, y: int) -> bool:
		return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

	def copy(self) -> "Rect":
		return Rect(self.x, self.y, self.width, self.height)


@dataclass(frozen=True)
class WindowTheme:
	title_height: int = 18
	border_width: int = 2
	border_color: int = 13
	title_color: int = 13
	content_color: int = 0
	text_color: int = 15
	outline_color: int = 15
	button_color: int = 5
	button_hover_color: int = 13
	button_pressed_color: int = 9
	slider_track_color: int = 8
	slider_fill_color: int = 11
	title_text_offset: int = 4
	control_size: int = 12
	control_gap: int = 2
	minimum_width: int = 96
	minimum_height: int = 72
	resize_grab: int = 5
	resize_outer_grab: int = 5


@dataclass
class WindowRecord:
	handle: int
	bounds: Rect
	title: str
	state: WindowState = WindowState.NORMAL
	restore_bounds: Rect | None = None
	control_capture: str | None = None


@dataclass
class DragSession:
	handle: int
	offset_x: int
	offset_y: int
	outline: Rect


@dataclass
class ResizeSession:
	handle: int
	region: str
	start_x: int
	start_y: int
	start_bounds: Rect
	outline: Rect


class WindowManager:
	def __init__(
		self,
		graphics: GraphicsDevice,
		input_device: InputDevice,
		theme: WindowTheme | None = None,
		appearance: object | None = None,
	) -> None:
		self.graphics = graphics
		self.input = input_device
		self.theme = theme or WindowTheme()
		self.appearance = appearance
		self.work_width = graphics.width
		self.work_height = graphics.height
		self._windows: dict[int, WindowRecord] = {}
		self._z_order: list[int] = []
		self._next_handle = 1
		self._drag: DragSession | None = None
		self._resize: ResizeSession | None = None
		self._widget_capture: tuple | None = None

	def create(self, x: int, y: int, width: int, height: int, title: str) -> int:
		width = max(self.theme.minimum_width, min(self.work_width, int(width)))
		height = max(self.theme.minimum_height, min(self.work_height, int(height)))
		x = max(0, min(self.work_width - width, int(x)))
		y = max(0, min(self.work_height - height, int(y)))
		handle = self._next_handle
		self._next_handle += 1
		self._windows[handle] = WindowRecord(
			handle,
			Rect(x, y, width, height),
			title,
		)
		self._z_order.append(handle)
		return handle

	def destroy(self, handle: int) -> None:
		record = self._windows.get(handle)
		if record:
			record.state = WindowState.CLOSED
		if self._drag and self._drag.handle == handle:
			self._drag = None
		if self._resize and self._resize.handle == handle:
			self._resize = None
		if self._widget_capture and len(self._widget_capture) > 1:
			if self._widget_capture[1] == handle:
				self._widget_capture = None

	def record(self, handle: int) -> WindowRecord | None:
		return self._windows.get(handle)

	def configure(
		self, handle: int, x: int, y: int, width: int, height: int, title: str
	) -> None:
		record = self._windows.get(handle)
		if not record or record.state == WindowState.CLOSED:
			return
		record.title = title
		# A drag owns the geometry until release. Fullscreen geometry is also
		# controlled by the window manager and restored atomically.
		if record.state != WindowState.NORMAL or (
			(self._drag and self._drag.handle == handle)
			or (self._resize and self._resize.handle == handle)
		):
			return
		width = max(self.theme.minimum_width, min(self.work_width, int(width)))
		height = max(self.theme.minimum_height, min(self.work_height, int(height)))
		x = max(0, min(self.work_width - width, int(x)))
		y = max(0, min(self.work_height - height, int(y)))
		record.bounds = Rect(x, y, width, height)

	def _raise(self, handle: int) -> None:
		if handle in self._z_order:
			self._z_order.remove(handle)
			self._z_order.append(handle)

	def _control_rects(self, record: WindowRecord) -> dict[str, Rect]:
		bounds = record.bounds
		size = self.theme.control_size
		step = size + self.theme.control_gap
		return {
			"close": Rect(bounds.x + bounds.width - size - 1, bounds.y + 1, size, size),
			"maximize": Rect(bounds.x + bounds.width - size - step - 1, bounds.y + 1, size, size),
		}

	def _resize_region(self, record: WindowRecord, x: int, y: int) -> str | None:
		bounds = record.bounds
		inner = self.theme.resize_grab
		outer = self.theme.resize_outer_grab
		right = bounds.x + bounds.width
		bottom = bounds.y + bounds.height
		if not (
			bounds.x - outer <= x < right + outer
			and bounds.y - outer <= y < bottom + outer
		):
			return None
		west = x < bounds.x + inner
		east = x >= right - inner
		north = y < bounds.y + inner
		south = y >= bottom - inner
		vertical = "n" if north else ("s" if south else "")
		horizontal = "w" if west else ("e" if east else "")
		if not vertical and not horizontal:
			return None
		return "resize_" + vertical + horizontal

	def _hit_region(self, record: WindowRecord, x: int, y: int) -> str | None:
		inside = record.bounds.contains(x, y)
		resize_region = None
		if record.state == WindowState.NORMAL:
			resize_region = self._resize_region(record, x, y)
			if resize_region and (
				not inside
				or x < record.bounds.x + self.theme.border_width
				or x >= record.bounds.x + record.bounds.width - self.theme.border_width
				or y < record.bounds.y + self.theme.border_width
				or y >= record.bounds.y + record.bounds.height - self.theme.border_width
			):
				return resize_region
		if not inside:
			return None
		for name, rect in self._control_rects(record).items():
			if rect.contains(x, y):
				return name
		if resize_region:
			return resize_region
		if y < record.bounds.y + self.theme.title_height:
			return "title"
		return "content"

	def _update_drag_outline(self, frame: InputFrame) -> None:
		if not self._drag:
			return
		outline = self._drag.outline
		outline.x = max(
			0,
			min(self.work_width - outline.width, frame.x - self._drag.offset_x),
		)
		outline.y = max(
			0,
			min(self.work_height - outline.height, frame.y - self._drag.offset_y),
		)

	def _update_resize_outline(self, frame: InputFrame) -> None:
		if not self._resize:
			return
		session = self._resize
		start = session.start_bounds
		delta_x = frame.x - session.start_x
		delta_y = frame.y - session.start_y
		left = start.x
		right = start.x + start.width
		top = start.y
		bottom = start.y + start.height
		region = session.region.removeprefix("resize_")

		if "w" in region:
			left = max(0, min(right - self.theme.minimum_width, start.x + delta_x))
		elif "e" in region:
			right = min(
				self.work_width,
				max(left + self.theme.minimum_width, start.x + start.width + delta_x),
			)
		if "n" in region:
			top = max(0, min(bottom - self.theme.minimum_height, start.y + delta_y))
		elif "s" in region:
			bottom = min(
				self.work_height,
				max(top + self.theme.minimum_height, start.y + start.height + delta_y),
			)

		session.outline = Rect(left, top, right - left, bottom - top)

	def update(self, handle: int) -> int:
		record = self._windows.get(handle)
		if not record or record.state == WindowState.CLOSED:
			return EVENT_CLOSED
		frame = self.input.frame()
		event = EVENT_NONE
		if record.state == WindowState.MINIMIZED:
			return event

		if frame.left_pressed:
			region = self._hit_region(record, frame.x, frame.y)
			if region:
				self._raise(handle)
			if region in ("close", "maximize"):
				record.control_capture = region
			elif region and region.startswith("resize_"):
				self._resize = ResizeSession(
					handle,
					region,
					frame.x,
					frame.y,
					record.bounds.copy(),
					record.bounds.copy(),
				)
			elif region == "title" and record.state == WindowState.NORMAL:
				self._drag = DragSession(
					handle,
					frame.x - record.bounds.x,
					frame.y - record.bounds.y,
					record.bounds.copy(),
				)

		if self._drag and self._drag.handle == handle:
			if frame.left_down or frame.left_released:
				self._update_drag_outline(frame)
			if frame.left_released:
				record.bounds.x = self._drag.outline.x
				record.bounds.y = self._drag.outline.y
				self._drag = None
				event |= EVENT_MOVED

		if self._resize and self._resize.handle == handle:
			if frame.left_down or frame.left_released:
				self._update_resize_outline(frame)
			if frame.left_released:
				record.bounds = self._resize.outline.copy()
				self._resize = None
				event |= EVENT_RESIZED

		if record.control_capture and frame.left_released:
			capture = record.control_capture
			record.control_capture = None
			if self._hit_region(record, frame.x, frame.y) == capture:
				if capture == "close":
					record.state = WindowState.CLOSED
					event |= EVENT_CLOSED
				elif capture == "maximize":
					if record.state == WindowState.MAXIMIZED:
						record.bounds = (record.restore_bounds or record.bounds).copy()
						record.restore_bounds = None
						record.state = WindowState.NORMAL
						event |= EVENT_RESTORED
					else:
						record.restore_bounds = record.bounds.copy()
						record.bounds = Rect(0, 0, self.work_width, self.work_height)
						record.state = WindowState.MAXIMIZED
						event |= EVENT_MAXIMIZED
		return event

	def _draw_frame(self, rect: Rect, color: int, width: int) -> None:
		width = max(1, width)
		g = self.graphics
		g.fill_rect(rect.x, rect.y, rect.width, width, color)
		g.fill_rect(rect.x, rect.y + rect.height - width, rect.width, width, color)
		g.fill_rect(rect.x, rect.y, width, rect.height, color)
		g.fill_rect(rect.x + rect.width - width, rect.y, width, rect.height, color)

	def _appearance_value(self, name: str, default: int) -> int:
		if self.appearance is None:
			return default
		try:
			return int(getattr(self.appearance, name))
		except (AttributeError, TypeError, ValueError):
			return default

	def _fill_window_rect(self, rect: Rect, color: int, transparency: int) -> None:
		transparency = max(0, min(100, transparency))
		if transparency == 0:
			self.graphics.fill_rect(rect.x, rect.y, rect.width, rect.height, color)
			return
		if transparency == 100:
			return
		opaque_slots = max(1, 4 - int(round(transparency * 4 / 100)))
		self.graphics.fill_dithered_rect(
			rect.x,
			rect.y,
			rect.width,
			rect.height,
			color,
			opaque_slots,
		)

	def draw(self, handle: int) -> None:
		record = self._windows.get(handle)
		if not record or record.state == WindowState.CLOSED:
			return
		g = self.graphics
		t = self.theme
		if record.state == WindowState.MINIMIZED:
			return
		b = record.bounds
		transparency = self._appearance_value("window_transparency", 0)
		corner_style = self._appearance_value("window_corner_style", 0)
		corner_inset = 0 if corner_style == 0 else corner_style * 2
		if corner_inset:
			self._fill_window_rect(
				Rect(b.x + corner_inset, b.y, b.width - corner_inset * 2, b.height),
				t.content_color,
				transparency,
			)
			self._fill_window_rect(
				Rect(b.x, b.y + corner_inset, b.width, b.height - corner_inset * 2),
				t.content_color,
				transparency,
			)
		else:
			self._fill_window_rect(b, t.content_color, transparency)
		title_rect = Rect(b.x + corner_inset, b.y, b.width - corner_inset * 2, t.title_height)
		self._fill_window_rect(title_rect, t.title_color, transparency // 2)
		if corner_inset:
			g.fill_rect(b.x + corner_inset, b.y, b.width - corner_inset * 2, t.border_width, t.border_color)
			g.fill_rect(b.x + corner_inset, b.y + b.height - t.border_width, b.width - corner_inset * 2, t.border_width, t.border_color)
			g.fill_rect(b.x, b.y + corner_inset, t.border_width, b.height - corner_inset * 2, t.border_color)
			g.fill_rect(b.x + b.width - t.border_width, b.y + corner_inset, t.border_width, b.height - corner_inset * 2, t.border_color)
		else:
			self._draw_frame(b, t.border_color, t.border_width)
		text_advance = 6 * g.text_scale
		controls_width = (t.control_size + t.control_gap) * 2 + 4
		max_chars = max(0, (b.width - t.title_text_offset - controls_width) // text_advance)
		title = record.title[:max_chars]
		g.draw_text(b.x + t.title_text_offset, b.y + 2, title, t.text_color)
		controls = self._control_rects(record)
		maximum = controls["maximize"]
		self._draw_frame(Rect(maximum.x + 2, maximum.y + 2, 8, 8), 5, 2)
		close = controls["close"]
		g.draw_line(close.x + 2, close.y + 2, close.x + 9, close.y + 9, 5)
		g.draw_line(close.x + 3, close.y + 2, close.x + 10, close.y + 9, 5)
		g.draw_line(close.x + 9, close.y + 2, close.x + 2, close.y + 9, 5)
		g.draw_line(close.x + 10, close.y + 2, close.x + 3, close.y + 9, 5)

	def draw_drag_outline(self, handle: int) -> None:
		outline = None
		if self._drag and self._drag.handle == handle:
			outline = self._drag.outline
		elif self._resize and self._resize.handle == handle:
			outline = self._resize.outline
		if outline:
			self.graphics.draw_rect(
				outline.x,
				outline.y,
				outline.width,
				outline.height,
				self.theme.outline_color,
			)

	def _record(self, handle: int) -> WindowRecord | None:
		return self._windows.get(handle)

	def x(self, handle: int) -> int:
		record = self._record(handle)
		return record.bounds.x if record else 0

	def y(self, handle: int) -> int:
		record = self._record(handle)
		return record.bounds.y if record else 0

	def content_x(self, handle: int) -> int:
		return self.x(handle) + self.theme.border_width

	def content_y(self, handle: int) -> int:
		return self.y(handle) + self.theme.title_height

	def content_width(self, handle: int) -> int:
		record = self._record(handle)
		return max(0, record.bounds.width - self.theme.border_width * 2) if record else 0

	def content_height(self, handle: int) -> int:
		record = self._record(handle)
		if not record:
			return 0
		return max(0, record.bounds.height - self.theme.title_height - self.theme.border_width)

	def ui_scale(self, handle: int) -> int:
		return max(1, self.graphics.text_scale) if self._record(handle) else 1

	def draw_origin(self, handle: int) -> tuple[int, int]:
		return self.content_x(handle), self.content_y(handle)

	def is_open(self, handle: int) -> bool:
		record = self._record(handle)
		return bool(record and record.state != WindowState.CLOSED)

	def is_dragging(self, handle: int) -> bool:
		return bool(self._drag and self._drag.handle == handle)

	def is_resizing(self, handle: int) -> bool:
		return bool(self._resize and self._resize.handle == handle)

	def is_minimized(self, handle: int) -> bool:
		record = self._record(handle)
		return bool(record and record.state == WindowState.MINIMIZED)

	def is_maximized(self, handle: int) -> bool:
		record = self._record(handle)
		return bool(record and record.state == WindowState.MAXIMIZED)

	def button(
		self,
		handle: int,
		x: int,
		y: int,
		width: int,
		height: int,
		label: str,
		base_color: int | None = None,
		framed: bool = True,
	) -> bool:
		record = self._record(handle)
		if not record or record.state in (WindowState.CLOSED, WindowState.MINIMIZED):
			return False
		width = max(1, width)
		height = max(1, height)
		scale = self.ui_scale(handle)
		origin_x, origin_y = self.draw_origin(handle)
		rect = Rect(
			origin_x + x * scale,
			origin_y + y * scale,
			width * scale,
			height * scale,
		)
		frame = self.input.frame()
		key = ("button", handle, x, y, width, height, label)
		hovered = rect.contains(frame.x, frame.y)
		if frame.left_pressed and hovered and self._drag is None and self._resize is None:
			self._widget_capture = key
		pressed = self._widget_capture == key and frame.left_down
		clicked = False
		if self._widget_capture == key and frame.left_released:
			clicked = hovered
			self._widget_capture = None
		normal_color = self.theme.button_color if base_color is None else base_color % 16
		if not framed and label == "":
			color = normal_color
		elif pressed:
			color = self.theme.button_pressed_color
		elif hovered:
			if not framed and self._appearance_value("theme_mode", 0) == 1 and normal_color == 1:
				color = 8
			elif not framed and self._appearance_value("theme_mode", 0) == 0 and normal_color == 8:
				color = 1
			elif base_color is None:
				color = self.theme.button_hover_color
			elif normal_color == 8:
				color = 7
			elif 0 < normal_color < 8:
				color = normal_color + 8
			else:
				color = normal_color
		else:
			color = normal_color
		self.graphics.fill_rect(rect.x, rect.y, rect.width, rect.height, color)
		if framed:
			self._draw_frame(rect, self.theme.text_color, scale)
		if height <= 7:
			advance = 4 * scale
			glyph_height = 5 * scale
			max_chars = max(0, (rect.width - 2 * scale) // advance)
			visible_label = label[:max_chars]
			text_width = len(visible_label) * advance - (scale if visible_label else 0)
			text_x = rect.x + max(scale, (rect.width - text_width) // 2)
			text_y = rect.y + max(scale, (rect.height - glyph_height) // 2)
			self.graphics.draw_text_small(
				text_x,
				text_y,
				visible_label,
				self.theme.text_color,
				pixel_scale=scale,
			)
		else:
			advance = 6 * scale
			glyph_height = 7 * scale
			max_chars = max(0, (rect.width - 2 * scale) // advance)
			visible_label = label[:max_chars]
			text_width = len(visible_label) * advance - (scale if visible_label else 0)
			text_x = rect.x + max(scale, (rect.width - text_width) // 2)
			text_y = rect.y + max(scale, (rect.height - glyph_height) // 2)
			self.graphics.draw_text(
				text_x,
				text_y,
				visible_label,
				self.theme.text_color,
				pixel_scale=scale,
			)
		return clicked

	def slider(
		self,
		handle: int,
		x: int,
		y: int,
		width: int,
		value: int,
		minimum: int,
		maximum: int,
	) -> int:
		record = self._record(handle)
		if not record or record.state in (WindowState.CLOSED, WindowState.MINIMIZED):
			return value
		if minimum > maximum:
			minimum, maximum = maximum, minimum
		value = max(minimum, min(maximum, value))
		width = max(6, width)
		scale = self.ui_scale(handle)
		origin_x, origin_y = self.draw_origin(handle)
		rect = Rect(
			origin_x + x * scale,
			origin_y + y * scale,
			width * scale,
			9 * scale,
		)
		frame = self.input.frame()
		key = ("slider", handle, x, y, width)
		if (
			frame.left_pressed
			and rect.contains(frame.x, frame.y)
			and self._drag is None
			and self._resize is None
		):
			self._widget_capture = key
		active = self._widget_capture == key
		if active and (frame.left_down or frame.left_released):
			range_size = maximum - minimum
			if range_size > 0:
				ratio = max(0.0, min(1.0, (frame.x - rect.x) / max(1, rect.width - 1)))
				value = minimum + int(round(ratio * range_size))
			else:
				value = minimum
		if active and frame.left_released:
			self._widget_capture = None
		range_size = maximum - minimum
		ratio = 0.0 if range_size == 0 else (value - minimum) / range_size
		knob_x = rect.x + int(round(ratio * (rect.width - 1)))
		self.graphics.fill_rect(rect.x, rect.y + 3 * scale, rect.width, 3 * scale, self.theme.slider_track_color)
		self.graphics.fill_rect(rect.x, rect.y + 3 * scale, max(scale, knob_x - rect.x + scale), 3 * scale, self.theme.slider_fill_color)
		self.graphics.fill_rect(knob_x - scale, rect.y + scale, 3 * scale, 7 * scale, self.theme.text_color)
		return value
