from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable, Sequence
from json import load

from .theme import SCREEN_HEIGHT, SCREEN_WIDTH


DEFAULT_PALETTE = (
	"#000000",
	"#0000AA",
	"#00AA00",
	"#00AAAA",
	"#AA0000",
	"#AA00AA",
	"#AA5500",
	"#AAAAAA",
	"#555555",
	"#5555FF",
	"#55FF55",
	"#55FFFF",
	"#FF5555",
	"#FF55FF",
	"#FFFF55",
	"#FFFFFF",
)


@dataclass(frozen=True)
class FrameSnapshot:
	width: int
	height: int
	indices: bytes
	palette: tuple[str, ...]
	sequence: int


with open("xe_lang/devices/font5x7.json", "r") as file:
	FONT_5X7 = load(file)

with open("xe_lang/devices/font3x5.json", "r") as file:
	FONT_3X5 = load(file)


class GraphicsDevice:
	def __init__(
		self,
		width: int = SCREEN_WIDTH,
		height: int = SCREEN_HEIGHT,
		frame_handler: Callable[[FrameSnapshot], None] | None = None,
	) -> None:
		self.width = width
		self.height = height
		self.text_scale = max(1, min(width // 240, height // 180))
		self.back_buffer = [[0 for _ in range(width)] for _ in range(height)]
		self.front_buffer = [[0 for _ in range(width)] for _ in range(height)]
		self.clip_rect = (0, 0, width, height)
		self.brightness_affected = True
		self.frame_handler = frame_handler
		self.sequence = 0
		self._lock = RLock()

	def set_frame_handler(
		self, handler: Callable[[FrameSnapshot], None] | None
	) -> None:
		self.frame_handler = handler

	def set_clip(self, x: int, y: int, width: int, height: int) -> None:
		x0 = max(0, int(x))
		y0 = max(0, int(y))
		x1 = min(self.width, int(x) + max(0, int(width)))
		y1 = min(self.height, int(y) + max(0, int(height)))
		self.clip_rect = (x0, y0, max(x0, x1), max(y0, y1))

	def reset_clip(self) -> None:
		self.clip_rect = (0, 0, self.width, self.height)

	def _clear_buffer(self, buffer: list[list[int]], color: int) -> None:
		color %= 16
		x0, y0, x1, y1 = self.clip_rect
		for y in range(y0, y1):
			buffer[y][x0:x1] = [color] * (x1 - x0)

	def clear(self, color: int) -> None:
		self._clear_buffer(self.back_buffer, color)

	def clear_screen(self, color: int) -> None:
		self._clear_buffer(self.front_buffer, color)

	def clear_both(self, color: int) -> None:
		self.clear(color)
		self.clear_screen(color)

	def set_pixel(self, x: int, y: int, color: int) -> None:
		x0, y0, x1, y1 = self.clip_rect
		if x0 <= x < x1 and y0 <= y < y1:
			self.back_buffer[y][x] = color % 16

	def fill_rect(self, x: int, y: int, width: int, height: int, color: int) -> None:
		if width <= 0 or height <= 0:
			return
		cx0, cy0, cx1, cy1 = self.clip_rect
		x0 = max(cx0, x)
		y0 = max(cy0, y)
		x1 = min(cx1, x + width)
		y1 = min(cy1, y + height)
		if x0 >= x1 or y0 >= y1:
			return
		row = [color % 16] * (x1 - x0)
		for py in range(y0, y1):
			self.back_buffer[py][x0:x1] = row

	def fill_dithered_rect(
		self,
		x: int,
		y: int,
		width: int,
		height: int,
		color: int,
		opaque_slots: int,
	) -> None:
		if width <= 0 or height <= 0:
			return
		opaque_slots = max(0, min(4, int(opaque_slots)))
		if opaque_slots == 0:
			return
		if opaque_slots == 4:
			self.fill_rect(x, y, width, height, color)
			return
		cx0, cy0, cx1, cy1 = self.clip_rect
		x0 = max(cx0, x)
		y0 = max(cy0, y)
		x1 = min(cx1, x + width)
		y1 = min(cy1, y + height)
		if x0 >= x1 or y0 >= y1:
			return
		color %= 16
		for py in range(y0, y1):
			row = self.back_buffer[py]
			for slot in range(opaque_slots):
				remainder = (slot - py * 2) % 4
				start = x0 + (remainder - x0) % 4
				if start < x1:
					count = (x1 - 1 - start) // 4 + 1
					row[start:x1:4] = [color] * count

	def draw_rect(self, x: int, y: int, width: int, height: int, color: int) -> None:
		if width <= 0 or height <= 0:
			return
		self.draw_line(x, y, x + width - 1, y, color)
		self.draw_line(x, y + height - 1, x + width - 1, y + height - 1, color)
		self.draw_line(x, y, x, y + height - 1, color)
		self.draw_line(x + width - 1, y, x + width - 1, y + height - 1, color)

	def draw_line(self, x0: int, y0: int, x1: int, y1: int, color: int) -> None:
		dx = abs(x1 - x0)
		sx = 1 if x0 < x1 else -1
		dy = -abs(y1 - y0)
		sy = 1 if y0 < y1 else -1
		error = dx + dy
		while True:
			self.set_pixel(x0, y0, color)
			if x0 == x1 and y0 == y1:
				break
			e2 = error * 2
			if e2 >= dy:
				error += dy
				x0 += sx
			if e2 <= dx:
				error += dx
				y0 += sy

	def draw_line_scaled(
		self,
		origin_x: int,
		origin_y: int,
		x0: int,
		y0: int,
		x1: int,
		y1: int,
		color: int,
		scale: int,
	) -> None:
		scale = max(1, int(scale))
		dx = abs(x1 - x0)
		sx = 1 if x0 < x1 else -1
		dy = -abs(y1 - y0)
		sy = 1 if y0 < y1 else -1
		error = dx + dy
		while True:
			self.fill_rect(
				origin_x + x0 * scale,
				origin_y + y0 * scale,
				scale,
				scale,
				color,
			)
			if x0 == x1 and y0 == y1:
				break
			e2 = error * 2
			if e2 >= dy:
				error += dy
				x0 += sx
			if e2 <= dx:
				error += dx
				y0 += sy

	def draw_circle(self, cx: int, cy: int, radius: int, color: int) -> None:
		if radius < 0:
			return
		x = radius
		y = 0
		error = 1 - radius
		while x >= y:
			for px, py in (
				(cx + x, cy + y), (cx + y, cy + x),
				(cx - y, cy + x), (cx - x, cy + y),
				(cx - x, cy - y), (cx - y, cy - x),
				(cx + y, cy - x), (cx + x, cy - y),
			):
				self.set_pixel(px, py, color)
			y += 1
			if error < 0:
				error += 2 * y + 1
			else:
				x -= 1
				error += 2 * (y - x) + 1

	def draw_circle_scaled(
		self,
		origin_x: int,
		origin_y: int,
		cx: int,
		cy: int,
		radius: int,
		color: int,
		scale: int,
	) -> None:
		if radius < 0:
			return
		scale = max(1, int(scale))
		x = radius
		y = 0
		error = 1 - radius
		while x >= y:
			for px, py in (
				(cx + x, cy + y), (cx + y, cy + x),
				(cx - y, cy + x), (cx - x, cy + y),
				(cx - x, cy - y), (cx - y, cy - x),
				(cx + y, cy - x), (cx + x, cy - y),
			):
				self.fill_rect(
					origin_x + px * scale,
					origin_y + py * scale,
					scale,
					scale,
					color,
				)
			y += 1
			if error < 0:
				error += 2 * y + 1
			else:
				x -= 1
				error += 2 * (y - x) + 1

	def draw_rect_scaled(
		self,
		origin_x: int,
		origin_y: int,
		x: int,
		y: int,
		width: int,
		height: int,
		color: int,
		scale: int,
	) -> None:
		if width <= 0 or height <= 0:
			return
		scale = max(1, int(scale))
		left = origin_x + x * scale
		top = origin_y + y * scale
		pixel_width = width * scale
		pixel_height = height * scale
		self.fill_rect(left, top, pixel_width, scale, color)
		self.fill_rect(left, top + pixel_height - scale, pixel_width, scale, color)
		self.fill_rect(left, top, scale, pixel_height, color)
		self.fill_rect(left + pixel_width - scale, top, scale, pixel_height, color)

	def fill_circle(self, cx: int, cy: int, radius: int, color: int) -> None:
		if radius < 0:
			return
		radius_sq = radius * radius
		for y in range(cy - radius, cy + radius + 1):
			delta = radius_sq - (y - cy) * (y - cy)
			if delta < 0:
				continue
			x_span = int(delta**0.5)
			self.fill_rect(cx - x_span, y, x_span * 2 + 1, 1, color)

	def fill_circle_scaled(
		self,
		origin_x: int,
		origin_y: int,
		cx: int,
		cy: int,
		radius: int,
		color: int,
		scale: int,
	) -> None:
		if radius < 0:
			return
		scale = max(1, int(scale))
		radius_sq = radius * radius
		for y in range(cy - radius, cy + radius + 1):
			delta = radius_sq - (y - cy) * (y - cy)
			if delta < 0:
				continue
			x_span = int(delta**0.5)
			self.fill_rect(
				origin_x + (cx - x_span) * scale,
				origin_y + y * scale,
				(x_span * 2 + 1) * scale,
				scale,
				color,
			)

	def draw_triangle_scaled(
		self,
		origin_x: int,
		origin_y: int,
		points: tuple[int, int, int, int, int, int],
		color: int,
		scale: int,
	) -> None:
		x1, y1, x2, y2, x3, y3 = points
		self.draw_line_scaled(origin_x, origin_y, x1, y1, x2, y2, color, scale)
		self.draw_line_scaled(origin_x, origin_y, x2, y2, x3, y3, color, scale)
		self.draw_line_scaled(origin_x, origin_y, x3, y3, x1, y1, color, scale)

	def fill_triangle_scaled(
		self,
		origin_x: int,
		origin_y: int,
		points: tuple[int, int, int, int, int, int],
		color: int,
		scale: int,
	) -> None:
		x1, y1, x2, y2, x3, y3 = points
		minimum_y = min(y1, y2, y3)
		maximum_y = max(y1, y2, y3)
		area = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
		if area == 0:
			self.draw_triangle_scaled(origin_x, origin_y, points, color, scale)
			return
		for y in range(minimum_y, maximum_y + 1):
			covered = []
			for x in range(min(x1, x2, x3), max(x1, x2, x3) + 1):
				a = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / area
				b = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / area
				c = 1.0 - a - b
				if a >= 0 and b >= 0 and c >= 0:
					covered.append(x)
			if covered:
				self.fill_rect(
					origin_x + covered[0] * scale,
					origin_y + y * scale,
					(covered[-1] - covered[0] + 1) * scale,
					scale,
					color,
				)

	def _draw_glyph(
		self,
		x: int,
		y: int,
		glyph: tuple[int, ...],
		glyph_width: int,
		color: int,
		pixel_scale: int,
	) -> None:
		scale = max(1, int(pixel_scale))
		for row, bits in enumerate(glyph):
			for column in range(glyph_width):
				if bits & (1 << (glyph_width - 1 - column)):
					self.fill_rect(
						x + column * scale,
						y + row * scale,
						scale,
						scale,
						color,
					)

	def draw_text(
		self,
		x: int,
		y: int,
		text: str,
		color: int,
		pixel_scale: int | None = None,
	) -> None:
		origin_x = x
		scale = self.text_scale if pixel_scale is None else max(1, int(pixel_scale))
		for char in text:
			if char == "\n":
				x = origin_x
				y += 8 * scale
				continue
			glyph = FONT_5X7.get(char, FONT_5X7.get(char.upper(), FONT_5X7["\u007f"]))
			remapped_glyph = tuple(map(lambda n: n >> (5 - glyph[0]), glyph[1:]))
			self._draw_glyph(x, y, remapped_glyph, glyph[0], color, scale)
			x += (glyph[0] + 1) * scale

	def draw_text_small(
		self,
		x: int,
		y: int,
		text: str,
		color: int,
		pixel_scale: int | None = None,
	) -> None:
		origin_x = x
		scale = self.text_scale if pixel_scale is None else max(1, int(pixel_scale))
		for char in text:
			if char == "\n":
				x = origin_x
				y += 6 * scale
				continue
			glyph = FONT_3X5.get(char, FONT_3X5["\u007f"])
			remapped_glyph = tuple(map(lambda n: n >> (5 - glyph[0]), glyph[1:]))
			self._draw_glyph(x, y, remapped_glyph, glyph[0], color, scale)
			x += (glyph[0] + 1) * scale

	def _snapshot(self, palette: Sequence[str]) -> FrameSnapshot:
		self.sequence += 1
		indices = bytes(pixel for row in self.front_buffer for pixel in row)
		return FrameSnapshot(
			self.width,
			self.height,
			indices,
			tuple(palette),
			self.sequence,
		)

	def publish(self, palette: Sequence[str]) -> FrameSnapshot:
		with self._lock:
			snapshot = self._snapshot(palette)
		if self.frame_handler:
			self.frame_handler(snapshot)
		return snapshot

	def present(self, palette: Sequence[str]) -> FrameSnapshot:
		with self._lock:
			for y in range(self.height):
				self.front_buffer[y][:] = self.back_buffer[y]
		return self.publish(palette)

	def append(self, palette: Sequence[str]) -> FrameSnapshot:
		with self._lock:
			x0, y0, x1, y1 = self.clip_rect
			for y in range(y0, y1):
				for x in range(x0, x1):
					if self.back_buffer[y][x] != 0:
						self.front_buffer[y][x] = self.back_buffer[y][x]
		return self.publish(palette)
