from __future__ import annotations

from dataclasses import dataclass
from json import load
from pathlib import Path
from threading import RLock
from typing import Callable, Sequence

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


FONT_5X7 = {
	" ": (0, 0, 0, 0, 0, 0, 0),
	"!": (4, 4, 4, 4, 4, 0, 4),
	'"': (10, 10, 10, 0, 0, 0, 0),
	"#": (10, 31, 10, 10, 31, 10, 0),
	"$": (4, 15, 20, 14, 5, 30, 4),
	"%": (17, 2, 4, 8, 17, 0, 0),
	"&": (12, 18, 20, 8, 21, 18, 13),
	"'": (4, 4, 8, 0, 0, 0, 0),
	"(": (2, 4, 8, 8, 8, 4, 2),
	")": (8, 4, 2, 2, 2, 4, 8),
	"*": (0, 21, 14, 31, 14, 21, 0),
	"+": (0, 4, 4, 31, 4, 4, 0),
	",": (0, 0, 0, 0, 4, 4, 8),
	"-": (0, 0, 0, 31, 0, 0, 0),
	".": (0, 0, 0, 0, 0, 12, 12),
	"/": (1, 2, 4, 8, 16, 0, 0),
	"0": (14, 17, 19, 21, 25, 17, 14),
	"1": (4, 12, 4, 4, 4, 4, 14),
	"2": (14, 17, 1, 2, 4, 8, 31),
	"3": (30, 1, 1, 14, 1, 1, 30),
	"4": (2, 6, 10, 18, 31, 2, 2),
	"5": (31, 16, 16, 30, 1, 1, 30),
	"6": (6, 8, 16, 30, 17, 17, 14),
	"7": (31, 1, 2, 4, 8, 8, 8),
	"8": (14, 17, 17, 14, 17, 17, 14),
	"9": (14, 17, 17, 15, 1, 2, 12),
	":": (0, 12, 12, 0, 12, 12, 0),
	";": (0, 12, 12, 0, 4, 4, 8),
	"<": (2, 4, 8, 16, 8, 4, 2),
	"=": (0, 0, 31, 0, 31, 0, 0),
	">": (8, 4, 2, 1, 2, 4, 8),
	"?": (14, 17, 1, 2, 4, 0, 4),
	"@": (14, 17, 23, 21, 23, 16, 14),
	"A": (14, 17, 17, 31, 17, 17, 17),
	"B": (30, 17, 17, 30, 17, 17, 30),
	"C": (14, 17, 16, 16, 16, 17, 14),
	"D": (28, 18, 17, 17, 17, 18, 28),
	"E": (31, 16, 16, 30, 16, 16, 31),
	"F": (31, 16, 16, 30, 16, 16, 16),
	"G": (14, 17, 16, 23, 17, 17, 15),
	"H": (17, 17, 17, 31, 17, 17, 17),
	"I": (14, 4, 4, 4, 4, 4, 14),
	"J": (7, 2, 2, 2, 2, 18, 12),
	"K": (17, 18, 20, 24, 20, 18, 17),
	"L": (16, 16, 16, 16, 16, 16, 31),
	"M": (17, 27, 21, 21, 17, 17, 17),
	"N": (17, 25, 21, 19, 17, 17, 17),
	"O": (14, 17, 17, 17, 17, 17, 14),
	"P": (30, 17, 17, 30, 16, 16, 16),
	"Q": (14, 17, 17, 17, 21, 18, 13),
	"R": (30, 17, 17, 30, 20, 18, 17),
	"S": (15, 16, 16, 14, 1, 1, 30),
	"T": (31, 4, 4, 4, 4, 4, 4),
	"U": (17, 17, 17, 17, 17, 17, 14),
	"V": (17, 17, 17, 17, 17, 10, 4),
	"W": (17, 17, 17, 21, 21, 21, 10),
	"X": (17, 17, 10, 4, 10, 17, 17),
	"Y": (17, 17, 10, 4, 4, 4, 4),
	"Z": (31, 1, 2, 4, 8, 16, 31),
	"[": (14, 8, 8, 8, 8, 8, 14),
	"\\": (16, 8, 4, 2, 1, 0, 0),
	"]": (14, 2, 2, 2, 2, 2, 14),
	"^": (4, 10, 17, 0, 0, 0, 0),
	"_": (0, 0, 0, 0, 0, 0, 31),
	"`": (8, 4, 2, 0, 0, 0, 0),
	"{": (2, 4, 4, 8, 4, 4, 2),
	"|": (4, 4, 4, 4, 4, 4, 4),
	"}": (8, 4, 4, 2, 4, 4, 8),
	"~": (0, 0, 9, 22, 0, 0, 0),
}

FONT_5X7.update({
	"a": (0, 0, 14, 1, 15, 17, 15),
	"b": (16, 16, 30, 17, 17, 17, 30),
	"c": (0, 0, 14, 16, 16, 17, 14),
	"d": (1, 1, 15, 17, 17, 17, 15),
	"e": (0, 0, 14, 17, 31, 16, 14),
	"f": (6, 8, 30, 8, 8, 8, 8),
	"g": (0, 0, 15, 17, 15, 1, 14),
	"h": (16, 16, 30, 17, 17, 17, 17),
	"i": (4, 0, 12, 4, 4, 4, 14),
	"j": (2, 0, 6, 2, 2, 18, 12),
	"k": (16, 18, 20, 24, 20, 18, 17),
	"l": (12, 4, 4, 4, 4, 4, 14),
	"m": (0, 0, 26, 21, 21, 17, 17),
	"n": (0, 0, 30, 17, 17, 17, 17),
	"o": (0, 0, 14, 17, 17, 17, 14),
	"p": (0, 0, 30, 17, 30, 16, 16),
	"q": (0, 0, 15, 17, 15, 1, 1),
	"r": (0, 0, 22, 25, 16, 16, 16),
	"s": (0, 0, 15, 16, 14, 1, 30),
	"t": (8, 8, 30, 8, 8, 9, 6),
	"u": (0, 0, 17, 17, 17, 19, 13),
	"v": (0, 0, 17, 17, 17, 10, 4),
	"w": (0, 0, 17, 17, 21, 21, 10),
	"x": (0, 0, 17, 10, 4, 10, 17),
	"y": (0, 0, 17, 17, 15, 1, 14),
	"z": (0, 0, 31, 2, 4, 8, 31),
})

FONT_3X5 = {
	" ": (0, 0, 0, 0, 0), "!": (2, 2, 2, 0, 2),
	"(": (1, 2, 2, 2, 1), ")": (4, 2, 2, 2, 4),
	"*": (0, 5, 2, 5, 0), "+": (0, 2, 7, 2, 0),
	",": (0, 0, 0, 2, 4), "-": (0, 0, 7, 0, 0),
	".": (0, 0, 0, 0, 2), "/": (1, 1, 2, 4, 4),
	"0": (7, 5, 5, 5, 7), "1": (2, 6, 2, 2, 7),
	"2": (6, 1, 7, 4, 7), "3": (6, 1, 3, 1, 6),
	"4": (5, 5, 7, 1, 1), "5": (7, 4, 6, 1, 6),
	"6": (3, 4, 7, 5, 7), "7": (7, 1, 2, 2, 2),
	"8": (7, 5, 7, 5, 7), "9": (7, 5, 7, 1, 6),
	"<": (1, 2, 4, 2, 1), "=": (0, 7, 0, 7, 0),
	">": (4, 2, 1, 2, 4), "%": (5, 1, 2, 4, 5),
	"A": (2, 5, 7, 5, 5), "B": (6, 5, 6, 5, 6),
	"C": (3, 4, 4, 4, 3), "D": (6, 5, 5, 5, 6),
	"E": (7, 4, 6, 4, 7), "F": (7, 4, 6, 4, 4),
	"G": (3, 4, 5, 5, 3), "H": (5, 5, 7, 5, 5),
	"I": (7, 2, 2, 2, 7), "J": (1, 1, 1, 5, 2),
	"K": (5, 5, 6, 5, 5), "L": (4, 4, 4, 4, 7),
	"M": (5, 7, 7, 5, 5), "N": (5, 7, 7, 7, 5),
	"O": (2, 5, 5, 5, 2), "P": (6, 5, 6, 4, 4),
	"Q": (2, 5, 5, 3, 1), "R": (6, 5, 6, 5, 5),
	"S": (3, 4, 2, 1, 6), "T": (7, 2, 2, 2, 2),
	"U": (5, 5, 5, 5, 7), "V": (5, 5, 5, 5, 2),
	"W": (5, 5, 7, 7, 5), "X": (5, 5, 2, 5, 5),
	"Y": (5, 5, 2, 2, 2), "Z": (7, 1, 2, 4, 7),
}

FONT_3X5.update({
	'"': (5, 5, 0, 0, 0), "#": (5, 7, 5, 7, 5),
	"$": (2, 7, 6, 3, 7), "&": (2, 5, 2, 5, 3),
	"'": (2, 2, 0, 0, 0), ":": (0, 2, 0, 2, 0),
	";": (0, 2, 0, 2, 4), "?": (6, 1, 2, 0, 2),
	"@": (2, 5, 7, 4, 3),
	"[": (6, 4, 4, 4, 6), "]": (3, 1, 1, 1, 3),
	"\\": (4, 4, 2, 1, 1), "_": (0, 0, 0, 0, 7),
	"`": (4, 2, 0, 0, 0), "{": (1, 2, 6, 2, 1),
	"|": (2, 2, 2, 2, 2), "}": (4, 2, 3, 2, 4),
	"~": (0, 0, 3, 6, 0), "^": (2, 5, 0, 0, 0),
	"a": (0, 3, 5, 5, 3), "b": (4, 6, 5, 5, 6),
	"c": (0, 3, 4, 4, 3), "d": (1, 3, 5, 5, 3),
	"e": (0, 2, 5, 6, 3), "f": (1, 2, 7, 2, 2),
	"g": (0, 3, 5, 3, 6), "h": (4, 6, 5, 5, 5),
	"i": (2, 0, 2, 2, 2), "j": (1, 0, 1, 5, 2),
	"k": (4, 5, 6, 5, 5), "l": (2, 2, 2, 2, 1),
	"m": (0, 7, 7, 5, 5), "n": (0, 6, 5, 5, 5),
	"o": (0, 2, 5, 5, 2), "p": (0, 6, 5, 6, 4),
	"q": (0, 3, 5, 3, 1), "r": (0, 5, 6, 4, 4),
	"s": (0, 3, 6, 3, 6), "t": (2, 7, 2, 2, 1),
	"u": (0, 5, 5, 5, 3), "v": (0, 5, 5, 5, 2),
	"w": (0, 5, 7, 7, 5), "x": (0, 5, 2, 2, 5),
	"y": (0, 5, 5, 3, 6), "z": (0, 7, 1, 2, 7),
})


def _load_width_prefixed_font(filename: str, row_count: int) -> dict[str, tuple[int, ...]]:
	path = Path(__file__).with_name(filename)
	with path.open("r", encoding="utf-8") as font_file:
		encoded = load(font_file)

	font: dict[str, tuple[int, ...]] = {}
	for char, values in encoded.items():
		glyph = tuple(int(value) for value in values)
		width = glyph[0]
		rows = glyph[1:]
		if not 1 <= width <= 5 or len(rows) != row_count:
			raise ValueError(f"Invalid glyph {char!r} in {filename}")
		font[char] = glyph
	return font


# Every entry stores its width first, followed by the five or seven row masks.
# Keep the legacy nested aliases available for external tooling that inspected
# the former opt-in proportional fonts.
FONT_5X7 = _load_width_prefixed_font("font5x7.json", 7)
FONT_3X5 = _load_width_prefixed_font("font3x5.json", 5)
VARIABLE_FONT_5X7 = {
	char: (glyph[0], glyph[1:]) for char, glyph in FONT_5X7.items()
}
VARIABLE_FONT_3X5 = {
	char: (glyph[0], glyph[1:]) for char, glyph in FONT_3X5.items()
}


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
		self.back_buffer = [bytearray(width) for _ in range(height)]
		self.front_buffer = [bytearray(width) for _ in range(height)]
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

	def _clear_buffer(self, buffer: list[bytearray], color: int) -> None:
		color %= 16
		x0, y0, x1, y1 = self.clip_rect
		row = bytes((color,)) * (x1 - x0)
		for y in range(y0, y1):
			buffer[y][x0:x1] = row

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
		row = bytes((color % 16,)) * (x1 - x0)
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
					row[start:x1:4] = bytes((color,)) * count

	def draw_rect(self, x: int, y: int, width: int, height: int, color: int) -> None:
		if width <= 0 or height <= 0:
			return
		self.draw_line(x, y, x + width - 1, y, color)
		self.draw_line(x, y + height - 1, x + width - 1, y + height - 1, color)
		self.draw_line(x, y, x, y + height - 1, color)
		self.draw_line(x + width - 1, y, x + width - 1, y + height - 1, color)

	def draw_line(self, x0: int, y0: int, x1: int, y1: int, color: int) -> None:
		cx0, cy0, cx1, cy1 = self.clip_rect
		color %= 16
		if y0 == y1:
			if not cy0 <= y0 < cy1:
				return
			left = max(cx0, min(x0, x1))
			right = min(cx1, max(x0, x1) + 1)
			if left < right:
				self.back_buffer[y0][left:right] = bytes((color,)) * (right - left)
			return
		if x0 == x1:
			if not cx0 <= x0 < cx1:
				return
			top = max(cy0, min(y0, y1))
			bottom = min(cy1, max(y0, y1) + 1)
			for y in range(top, bottom):
				self.back_buffer[y][x0] = color
			return

		buffer = self.back_buffer
		dx = abs(x1 - x0)
		sx = 1 if x0 < x1 else -1
		dy = -abs(y1 - y0)
		sy = 1 if y0 < y1 else -1
		error = dx + dy
		while True:
			if cx0 <= x0 < cx1 and cy0 <= y0 < cy1:
				buffer[y0][x0] = color
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
		if scale == 1:
			self.draw_line(
				origin_x + x0,
				origin_y + y0,
				origin_x + x1,
				origin_y + y1,
				color,
			)
			return
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
		cx0, cy0, cx1, cy1 = self.clip_rect
		buffer = self.back_buffer
		color %= 16
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
				if cx0 <= px < cx1 and cy0 <= py < cy1:
					buffer[py][px] = color
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
		if scale == 1:
			self.draw_circle(origin_x + cx, origin_y + cy, radius, color)
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
		if scale == 1:
			self.fill_circle(origin_x + cx, origin_y + cy, radius, color)
			return
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
		if scale == 1:
			x0, y0, x1, y1 = self.clip_rect
			buffer = self.back_buffer
			color %= 16
			for row, bits in enumerate(glyph):
				py = y + row
				if not y0 <= py < y1:
					continue
				for column in range(glyph_width):
					px = x + column
					if x0 <= px < x1 and bits & (1 << (glyph_width - 1 - column)):
						buffer[py][px] = color
			return
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
			if char == "\r":
				continue
			if char == "\t":
				x += 4 * (FONT_5X7[" "][0] + 1) * scale
				continue
			glyph = FONT_5X7.get(char, FONT_5X7["\x7f"])
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
			if char == "\r":
				continue
			if char == "\t":
				x += 4 * (FONT_3X5[" "][0] + 1) * scale
				continue
			glyph = FONT_3X5.get(char, FONT_3X5["\x7f"])
			remapped_glyph = tuple(map(lambda n: n >> (5 - glyph[0]), glyph[1:]))
			self._draw_glyph(x, y, remapped_glyph, glyph[0], color, scale)
			x += (glyph[0] + 1) * scale

	def draw_text_proportional(
		self,
		x: int,
		y: int,
		text: str,
		color: int,
		pixel_scale: int | None = None,
	) -> None:
		self.draw_text(x, y, text, color, pixel_scale)

	def draw_text_small_proportional(
		self,
		x: int,
		y: int,
		text: str,
		color: int,
		pixel_scale: int | None = None,
	) -> None:
		self.draw_text_small(x, y, text, color, pixel_scale)

	def text_advance(
		self,
		char: str,
		pixel_scale: int | None = None,
		*,
		small: bool = False,
	) -> int:
		scale = self.text_scale if pixel_scale is None else max(1, int(pixel_scale))
		font = FONT_3X5 if small else FONT_5X7
		if char == "\r" or char == "\n":
			return 0
		if char == "\t":
			return 4 * (font[" "][0] + 1) * scale
		glyph = font.get(char, font["\x7f"])
		return (glyph[0] + 1) * scale

	def styled_char_advance(self, char: str, font_size: int) -> int:
		small = font_size <= 1
		font_scale = 1 if font_size <= 2 else 2
		return self.text_advance(char, font_scale, small=small)

	def draw_char_styled(
		self,
		x: int,
		y: int,
		char: str,
		color: int,
		font_size: int,
		style: int,
		pixel_scale: int | None = None,
	) -> None:
		if char in "\r\n":
			return
		small = font_size <= 1
		font_scale = 1 if font_size <= 2 else 2
		font = FONT_3X5 if small else FONT_5X7
		base_scale = self.text_scale if pixel_scale is None else max(1, int(pixel_scale))
		scale = base_scale * font_scale
		if char == "\t":
			return
		glyph = font.get(char, font["\x7f"])
		glyph_width = glyph[0]
		rows = tuple(bits >> (5 - glyph_width) for bits in glyph[1:])
		bold = bool(style & 1)
		italic = bool(style & 2)
		# Styled glyphs must remain inside the same proportional cell advertised
		# by styled_char_advance().  The final blank font column is available for
		# slant and weight, but neither effect may spill into the next character.
		maximum_column = glyph_width - 1 if bold else glyph_width
		for row, bits in enumerate(rows):
			slant = (len(rows) - 1 - row) // (2 if small else 3) if italic else 0
			for column in range(glyph_width):
				if bits & (1 << (glyph_width - 1 - column)):
					draw_column = min(column + slant, maximum_column)
					self.fill_rect(
						x + draw_column * scale,
						y + row * scale,
						scale * (2 if bold else 1),
						scale,
						color,
					)
		if style & 4:
			advance = self.styled_char_advance(char, font_size) * base_scale
			self.fill_rect(
				x,
				y + len(rows) * scale,
				max(base_scale, advance - base_scale),
				base_scale,
				color,
			)

	def measure_text(
		self,
		text: str,
		pixel_scale: int | None = None,
		*,
		small: bool = False,
		proportional: bool = True,
	) -> int:
		scale = self.text_scale if pixel_scale is None else max(1, int(pixel_scale))
		if not proportional:
			cell_width = 4 if small else 6
			return max((len(line) * cell_width for line in text.split("\n")), default=0) * scale

		maximum = 0
		for line in text.split("\n"):
			width = 0
			for char in line:
				width += self.text_advance(char, 1, small=small)
			maximum = max(maximum, max(0, width - (1 if line else 0)))
		return maximum * scale

	def _snapshot(self, palette: Sequence[str]) -> FrameSnapshot:
		self.sequence += 1
		indices = b"".join(self.front_buffer)
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
