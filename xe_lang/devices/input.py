from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import RLock

from xe_lang.syscall_abi import KeyboardEvent, MouseEvent


LEFT_BUTTON = 1
RIGHT_BUTTON = 2
MIDDLE_BUTTON = 4


@dataclass(frozen=True)
class InputFrame:
	x: int
	y: int
	buttons: int
	pressed: int
	released: int
	keys_down: frozenset[int]
	modifiers: int

	@property
	def left_down(self) -> bool:
		return bool(self.buttons & LEFT_BUTTON)

	@property
	def left_pressed(self) -> bool:
		return bool(self.pressed & LEFT_BUTTON)

	@property
	def left_released(self) -> bool:
		return bool(self.released & LEFT_BUTTON)


class InputDevice:
	def __init__(self, width: int, height: int) -> None:
		self.width = width
		self.height = height
		self._lock = RLock()
		self._x = 0
		self._y = 0
		self._buttons = 0
		self._pending_pressed = 0
		self._pending_released = 0
		self._keys_down: set[int] = set()
		self._key_queue: deque[int] = deque()
		self._mouse_events: deque[tuple[int, int, int]] = deque()
		self._keyboard_events: deque[tuple[int, int, int]] = deque()
		self._last_mouse_event = int(MouseEvent.NONE)
		self._last_keyboard_event = int(KeyboardEvent.NONE)
		self._modifiers = 0
		self._latched: InputFrame | None = None

	@property
	def keys_down(self) -> set[int]:
		return self._keys_down

	@property
	def key_queue(self) -> deque[int]:
		return self._key_queue

	@property
	def modifiers(self) -> int:
		with self._lock:
			return self._modifiers

	def move_pointer(self, x: int, y: int) -> None:
		with self._lock:
			x = max(0, min(self.width - 1, int(x)))
			y = max(0, min(self.height - 1, int(y)))
			if (x, y) != (self._x, self._y):
				self._x = x
				self._y = y
				self._last_mouse_event = int(MouseEvent.MOVE)
				self._mouse_events.append((self._last_mouse_event, x, y))

	def pointer_position(self) -> tuple[int, int]:
		with self._lock:
			return self._x, self._y

	def set_button(self, button: int, down: bool) -> None:
		if button not in (LEFT_BUTTON, RIGHT_BUTTON, MIDDLE_BUTTON):
			return
		with self._lock:
			was_down = bool(self._buttons & button)
			if down and not was_down:
				self._buttons |= button
				self._pending_pressed |= button
				self._last_mouse_event = int(MouseEvent.PRESS)
				self._mouse_events.append((self._last_mouse_event, self._x, self._y))
			elif not down and was_down:
				self._buttons &= ~button
				self._pending_released |= button
				self._last_mouse_event = int(MouseEvent.RELEASE)
				self._mouse_events.append((self._last_mouse_event, self._x, self._y))

	def set_key(self, key: int, down: bool, modifiers: int = 0) -> None:
		key = int(key)
		with self._lock:
			self._modifiers = int(modifiers)
			if down:
				if key not in self._keys_down:
					self._keys_down.add(key)
					self._key_queue.append(key)
					self._last_keyboard_event = int(KeyboardEvent.PRESS)
					self._keyboard_events.append((self._last_keyboard_event, key, self._modifiers))
			else:
				if key in self._keys_down:
					self._keys_down.discard(key)
					self._last_keyboard_event = int(KeyboardEvent.RELEASE)
					self._keyboard_events.append((self._last_keyboard_event, key, self._modifiers))

	def release_all(self) -> None:
		with self._lock:
			self._pending_released |= self._buttons
			self._buttons = 0
			self._keys_down.clear()
			self._modifiers = 0

	def frame(self) -> InputFrame:
		with self._lock:
			if self._latched is None:
				self._latched = InputFrame(
					self._x,
					self._y,
					self._buttons,
					self._pending_pressed,
					self._pending_released,
					frozenset(self._keys_down),
					self._modifiers,
				)
				self._pending_pressed = 0
				self._pending_released = 0
			return self._latched

	def finish_frame(self) -> None:
		with self._lock:
			self._latched = None

	def read_key(self) -> int:
		with self._lock:
			return self._key_queue.popleft() if self._key_queue else 0

	def poll_mouse(self) -> tuple[int, int, int]:
		with self._lock:
			if self._mouse_events:
				return self._mouse_events.popleft()
			return int(MouseEvent.NONE), self._x, self._y

	def poll_keyboard(self) -> tuple[int, int, int]:
		with self._lock:
			if self._keyboard_events:
				return self._keyboard_events.popleft()
			return int(KeyboardEvent.NONE), 0, self._modifiers

	def previous_event(self, device_id: int) -> int:
		with self._lock:
			if device_id == 0:
				return self._last_mouse_event
			if device_id == 1:
				return self._last_keyboard_event
			return 0

	def is_key_down(self, key: int) -> bool:
		with self._lock:
			return int(key) in self._keys_down
