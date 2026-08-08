from __future__ import annotations

import math
import random
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable

from xe_lang.helper import Position, VMError
from xe_lang.syscall_abi import (
	GRAPHICS_REFERENCE_ADDRESS_MASK,
	GRAPHICS_SCREEN_REFERENCE_TAG,
	ImageFormat,
	SyscallID,
)

from .currency import CurrencyDevice
from .compiler import CompilerDevice
from .filesystem import FileSystemDevice
from .graphics import FrameSnapshot, GraphicsDevice
from .input import InputDevice
from .os_state import OSDevice
from .theme import SCREEN_HEIGHT, SCREEN_WIDTH
from .windows import WindowManager, WindowState


TRUE = 0xFFFFFFFF
FALSE = 0
COMPILER_RUN_INSTRUCTION_LIMIT = 500_000
COMPILER_RUN_OUTPUT_LIMIT = 8_192
COMPILER_RUN_SOURCE_LIMIT = 32_768
COMPILER_RUN_TIME_LIMIT = 2.0
COMPILER_RUN_TRUNCATION_MARKER = "\n[output truncated]"
_compiler_run_state = threading.local()

WINDOW_X = 0
WINDOW_Y = 1
WINDOW_WIDTH = 2
WINDOW_HEIGHT = 3
WINDOW_TITLE = 4
WINDOW_STATE = 5
WINDOW_HANDLE = 6
WINDOW_UI_SCALE = 7
WINDOW_WORDS = 10

SCREEN_FIELD_WIDTH = 0
SCREEN_FIELD_HEIGHT = 1
SCREEN_WORDS = 2
SCREEN_TARGET_HANDLE = -1


def _signed(value: int) -> int:
	return value - 0x100000000 if value > 0x7FFFFFFF else value


def _float(value: int) -> float:
	return struct.unpack(">f", struct.pack(">I", value & TRUE))[0]


def _float_bits(value: float) -> int:
	return struct.unpack(">I", struct.pack(">f", float(value)))[0]


class DeviceRuntime:
	def __init__(
		self,
		os_device: OSDevice | None = None,
		frame_handler: Callable[[FrameSnapshot], None] | None = None,
		width: int = SCREEN_WIDTH,
		height: int = SCREEN_HEIGHT,
		filesystem_root: str | Path | None = None,
	) -> None:
		self.input = InputDevice(width, height)
		self.graphics = GraphicsDevice(width, height, frame_handler)
		self.os = os_device or OSDevice()
		self.currency = CurrencyDevice()
		self.compiler = CompilerDevice()
		self.windows = WindowManager(self.graphics, self.input, appearance=self.os)
		self.files = FileSystemDevice(filesystem_root)
		self._rng = random.Random()
		self._raw_slider_capture: tuple[int, int, int] | None = None
		self._frame_window_pointer: int | None = None
		self._frame_window_handle = 0
		self._frame_is_screen = False
		self._handlers = {
			SyscallID.OS_RAND32: self._raw_rand32,
			SyscallID.OS_RANDF: self._raw_randf,
			SyscallID.OS_RSEED: self._raw_seed,
			SyscallID.OS_GET_HOUR: self._raw_get_hour,
			SyscallID.OS_GET_MINUTE: self._raw_get_min,
			SyscallID.GRAPHICS_CLEAR: self._raw_clear,
			SyscallID.GRAPHICS_CLEAR_BUFFER: self._raw_clear_buffer,
			SyscallID.GRAPHICS_CLEAR_SCREEN: self._raw_clear_screen,
			SyscallID.GRAPHICS_UPDATE: self._raw_update,
			SyscallID.GRAPHICS_FLIP: self._raw_flip,
			SyscallID.GRAPHICS_APPEND: self._raw_append,
			SyscallID.GRAPHICS_DUMP: self._raw_dump,
			SyscallID.GRAPHICS_SET_REGION: self._raw_set_region,
			SyscallID.GRAPHICS_RESET_REGION: self._raw_reset_region,
			SyscallID.GRAPHICS_SET_BRIGHTNESS_AFFECT: self._raw_set_brightness_affect,
			SyscallID.GRAPHICS_PIXEL: self._raw_pixel,
			SyscallID.GRAPHICS_LINE: self._raw_line,
			SyscallID.GRAPHICS_RECT: self._raw_rect,
			SyscallID.GRAPHICS_FILL_RECT: self._raw_fill_rect,
			SyscallID.GRAPHICS_CIRCLE: self._raw_circle,
			SyscallID.GRAPHICS_FILL_CIRCLE: self._raw_fill_circle,
			SyscallID.GRAPHICS_TRIANGLE: self._raw_triangle,
			SyscallID.GRAPHICS_FILL_TRIANGLE: self._raw_fill_triangle,
			SyscallID.GRAPHICS_IMAGE: self._raw_image,
			SyscallID.GRAPHICS_CHARACTER: self._raw_character,
			SyscallID.GRAPHICS_STRING: self._raw_string,
			SyscallID.GRAPHICS_WINDOW: self._raw_window,
			SyscallID.GRAPHICS_TASKBAR: self._raw_taskbar,
			SyscallID.GRAPHICS_TASK_ATOM: self._raw_task_atom,
			SyscallID.GRAPHICS_DRAW_BG: self._graphics_draw_background,
			SyscallID.GRAPHICS_BUTTON: self._raw_button,
			SyscallID.GRAPHICS_SLIDER: self._raw_slider,
			SyscallID.GRAPHICS_GET_CWIDTH: self._raw_width,
			SyscallID.GRAPHICS_GET_CWIDTH_SMALL: self._raw_width_small,
			SyscallID.MOUSE_POLL: self._raw_mouse_poll,
			SyscallID.INPUT_PREVIOUS_EVENT: self._raw_previous_event,
			SyscallID.KEYBOARD_POLL: self._raw_keyboard_poll,
			SyscallID.KEY_IS_DOWN: self._raw_key_is_down,
			SyscallID.BOUNDS_CHECK: self._raw_bounds_check,
			SyscallID.APP_GRAPHICS_WIDTH: self._graphics_width,
			SyscallID.APP_GRAPHICS_HEIGHT: self._graphics_height,
			SyscallID.APP_GRAPHICS_BEGIN_DRAW: self._graphics_begin_draw,
			SyscallID.APP_GRAPHICS_UPDATE: self._graphics_update,
			SyscallID.APP_GRAPHICS_CLEAR: self._graphics_clear,
			SyscallID.APP_GRAPHICS_SET_PIXEL: self._graphics_set_pixel,
			SyscallID.APP_GRAPHICS_DRAW_CIRCLE: self._graphics_draw_circle,
			SyscallID.APP_GRAPHICS_DRAW_LINE: self._graphics_draw_line,
			SyscallID.APP_GRAPHICS_DRAW_RECT: self._graphics_draw_rect,
			SyscallID.APP_GRAPHICS_FILL_RECT: self._graphics_fill_rect,
			SyscallID.APP_GRAPHICS_DRAW_TEXT: self._graphics_draw_text,
			SyscallID.APP_GRAPHICS_DRAW_INT: self._graphics_draw_int,
			SyscallID.APP_GRAPHICS_DRAW_FLOAT: self._graphics_draw_float,
			SyscallID.APP_GRAPHICS_BUTTON: self._graphics_button,
			SyscallID.APP_GRAPHICS_SLIDER: self._graphics_slider,
			SyscallID.APP_GRAPHICS_MOUSE_X: self._graphics_mouse_x,
			SyscallID.APP_GRAPHICS_MOUSE_Y: self._graphics_mouse_y,
			SyscallID.APP_GRAPHICS_MOUSE_DOWN: self._graphics_mouse_down,
			SyscallID.APP_GRAPHICS_MOUSE_PRESSED: self._graphics_mouse_pressed,
			SyscallID.APP_GRAPHICS_MOUSE_RELEASED: self._graphics_mouse_released,
			SyscallID.APP_GRAPHICS_SCROLL_DELTA: self._graphics_scroll_delta,
			SyscallID.APP_GRAPHICS_KEY_DOWN: self._graphics_key_down,
			SyscallID.APP_GRAPHICS_READ_KEY: self._graphics_read_key,
			SyscallID.APP_GRAPHICS_CONTENT_WIDTH: self._graphics_content_width,
			SyscallID.APP_GRAPHICS_CONTENT_HEIGHT: self._graphics_content_height,
			SyscallID.APP_GRAPHICS_DRAW_CHAR: self._graphics_draw_char,
			SyscallID.APP_GRAPHICS_BUTTON_TONE: self._graphics_button_tone,
			SyscallID.APP_GRAPHICS_POINTER_X: self._graphics_pointer_x,
			SyscallID.APP_GRAPHICS_POINTER_Y: self._graphics_pointer_y,
			SyscallID.APP_GRAPHICS_DRAW_TEXT_SMALL: self._graphics_draw_text_small,
			SyscallID.APP_GRAPHICS_DRAW_CHAR_SMALL: self._graphics_draw_char_small,
			SyscallID.APP_GRAPHICS_DRAW_INT_SMALL: self._graphics_draw_int_small,
			SyscallID.APP_GRAPHICS_DRAW_FLOAT_SMALL: self._graphics_draw_float_small,
			SyscallID.APP_GRAPHICS_BUTTON_FLAT: self._graphics_button_flat,
			SyscallID.APP_GRAPHICS_DRAW_ATOM: self._graphics_draw_atom,
			SyscallID.APP_GRAPHICS_DRAW_ICON: self._graphics_draw_icon,
			SyscallID.APP_GRAPHICS_DRAW_ICON_SCALED: self._graphics_draw_icon_scaled,
			SyscallID.APP_GRAPHICS_CHAR_ADVANCE: self._graphics_char_advance,
			SyscallID.APP_GRAPHICS_DRAW_CHAR_STYLED: self._graphics_draw_char_styled,
			SyscallID.APP_GRAPHICS_MODIFIERS: self._graphics_modifiers,
			SyscallID.APP_GRAPHICS_RIGHT_MOUSE_DOWN: self._graphics_right_mouse_down,
			SyscallID.APP_GRAPHICS_RIGHT_MOUSE_PRESSED: self._graphics_right_mouse_pressed,
			SyscallID.APP_GRAPHICS_RIGHT_MOUSE_RELEASED: self._graphics_right_mouse_released,
			SyscallID.APP_OS_GET_VOLUME: self._os_get_volume,
			SyscallID.APP_OS_SET_VOLUME: self._os_set_volume,
			SyscallID.APP_OS_GET_BACKGROUND: self._os_get_background,
			SyscallID.APP_OS_SET_BACKGROUND: self._os_set_background,
			SyscallID.APP_OS_GET_PALETTE: self._os_get_palette,
			SyscallID.APP_OS_SET_PALETTE: self._os_set_palette,
			SyscallID.APP_OS_SLEEP: self._os_sleep,
			SyscallID.APP_OS_EXIT: self._os_exit,
			SyscallID.APP_OS_APPLY_SETTINGS: self._os_apply_settings,
			SyscallID.APP_OS_BACKGROUND_COUNT: self._os_background_count,
			SyscallID.APP_OS_PALETTE_COUNT: self._os_palette_count,
			SyscallID.APP_OS_TICKS: self._os_ticks,
			SyscallID.APP_OS_YEAR: self._os_year,
			SyscallID.APP_OS_MONTH: self._os_month,
			SyscallID.APP_OS_DAY: self._os_day,
			SyscallID.APP_WINDOW_CLOSE: self._window_close,
			SyscallID.APP_WINDOW_IS_FULLSCREEN: self._window_is_fullscreen,
			SyscallID.APP_WINDOW_IS_MINIMIZED: self._window_is_minimized,
			SyscallID.APP_OS_OPEN_READ: self._os_open_read,
			SyscallID.APP_OS_OPEN_WRITE: self._os_open_write,
			SyscallID.APP_OS_READ: self._os_read,
			SyscallID.APP_OS_WRITE: self._os_write,
			SyscallID.APP_OS_CLOSE: self._os_close,
			SyscallID.APP_STRING_APPEND: self._string_append,
			SyscallID.APP_STRING_APPEND_CHAR: self._string_append_char,
			SyscallID.APP_OS_GET_MUSIC_VOLUME: self._os_get_music_volume,
			SyscallID.APP_OS_SET_MUSIC_VOLUME: self._os_set_music_volume,
			SyscallID.APP_OS_GET_SOUND_EFFECT_VOLUME: self._os_get_sound_effect_volume,
			SyscallID.APP_OS_SET_SOUND_EFFECT_VOLUME: self._os_set_sound_effect_volume,
			SyscallID.APP_OS_GET_THEME_MODE: self._os_get_theme_mode,
			SyscallID.APP_OS_SET_THEME_MODE: self._os_set_theme_mode,
			SyscallID.APP_OS_GET_WINDOW_TRANSPARENCY: self._os_get_window_transparency,
			SyscallID.APP_OS_SET_WINDOW_TRANSPARENCY: self._os_set_window_transparency,
			SyscallID.APP_OS_GET_WINDOW_CORNER_STYLE: self._os_get_window_corner_style,
			SyscallID.APP_OS_SET_WINDOW_CORNER_STYLE: self._os_set_window_corner_style,
			SyscallID.APP_OS_GET_ICON_SIZE: self._os_get_icon_size,
			SyscallID.APP_OS_SET_ICON_SIZE: self._os_set_icon_size,
			SyscallID.APP_OS_GET_CLOCK_FORMAT: self._os_get_clock_format,
			SyscallID.APP_OS_SET_CLOCK_FORMAT: self._os_set_clock_format,
			SyscallID.APP_OS_GET_SETTINGS_ENABLED: self._os_get_settings_enabled,
			SyscallID.APP_OS_SET_SETTINGS_ENABLED: self._os_set_settings_enabled,
			SyscallID.APP_OS_APPLY_PREFERENCES: self._os_apply_preferences,
			SyscallID.APP_CURRENCY_COUNT: self._currency_count,
			SyscallID.APP_CURRENCY_CODE: self._currency_code,
			SyscallID.APP_CURRENCY_LOAD: self._currency_load,
			SyscallID.APP_CURRENCY_STATUS: self._currency_status,
			SyscallID.APP_CURRENCY_RATE: self._currency_rate,
			SyscallID.APP_CURRENCY_POINT_COUNT: self._currency_point_count,
			SyscallID.APP_CURRENCY_POINT: self._currency_point,
			SyscallID.APP_CURRENCY_POINT_DATE: self._currency_point_date,
			SyscallID.APP_OS_ENTRY_COUNT: self._os_entry_count,
			SyscallID.APP_OS_ENTRY_NAME: self._os_entry_name,
			SyscallID.APP_OS_ENTRY_IS_DIRECTORY: self._os_entry_is_directory,
			SyscallID.APP_OS_PATH_EXISTS: self._os_path_exists,
			SyscallID.APP_OS_MAKE_FILE: self._os_make_file,
			SyscallID.APP_OS_MAKE_DIRECTORY: self._os_make_directory,
			SyscallID.APP_OS_RENAME: self._os_rename,
			SyscallID.APP_OS_DELETE: self._os_delete,
			SyscallID.APP_COMPILER_CHECK: self._compiler_check,
			SyscallID.APP_COMPILER_ERROR: self._compiler_error,
			SyscallID.APP_COMPILER_ERROR_LINE: self._compiler_error_line,
			SyscallID.APP_COMPILER_ERROR_COLUMN: self._compiler_error_column,
			SyscallID.APP_COMPILER_ASSEMBLY: self._compiler_assembly,
			SyscallID.APP_COMPILER_BYTECODE_SIZE: self._compiler_bytecode_size,
			SyscallID.APP_COMPILER_LOAD_VISUAL: self._compiler_load_visual,
			SyscallID.APP_COMPILER_ATOM_COUNT: self._compiler_atom_count,
			SyscallID.APP_COMPILER_ATOM_TEXT: self._compiler_atom_text,
			SyscallID.APP_COMPILER_ATOM_KIND: self._compiler_atom_kind,
			SyscallID.APP_COMPILER_ATOM_LINE: self._compiler_atom_line,
			SyscallID.APP_COMPILER_ATOM_ENABLED: self._compiler_atom_enabled,
			SyscallID.APP_COMPILER_SET_ATOM_ENABLED: self._compiler_set_atom_enabled,
			SyscallID.APP_COMPILER_VISUAL_SOURCE: self._compiler_visual_source,
			SyscallID.APP_COMPILER_SCRIPT_COUNT: self._compiler_script_count,
			SyscallID.APP_COMPILER_SCRIPT_NAME: self._compiler_script_name,
			SyscallID.APP_COMPILER_SCRIPT_SHELL: self._compiler_script_shell,
			SyscallID.APP_COMPILER_SCRIPT_LINE: self._compiler_script_line,
			SyscallID.APP_COMPILER_SCRIPT_ENABLED: self._compiler_script_enabled,
			SyscallID.APP_COMPILER_LOAD_DOCUMENT: self._compiler_load_document,
			SyscallID.APP_COMPILER_DOCUMENT_SCRIPT_COUNT: self._compiler_document_script_count,
			SyscallID.APP_COMPILER_DOCUMENT_SCRIPT_NAME: self._compiler_document_script_name,
			SyscallID.APP_COMPILER_DOCUMENT_SCRIPT_SHELL: self._compiler_document_script_shell,
			SyscallID.APP_COMPILER_DOCUMENT_SCRIPT_LINE: self._compiler_document_script_line,
			SyscallID.APP_COMPILER_DOCUMENT_SCRIPT_ENABLED: self._compiler_document_script_enabled,
			SyscallID.APP_COMPILER_DOCUMENT_SOURCE: self._compiler_document_source,
			SyscallID.APP_COMPILER_RUN: self._compiler_run,
		}

	def set_frame_handler(self, handler: Callable[[FrameSnapshot], None] | None) -> None:
		self.graphics.set_frame_handler(handler)

	def dispatch(self, syscall_id: int, vm: Any, result: Any) -> bool:
		handler = self._handlers.get(syscall_id)
		if handler is None:
			return False
		handler(vm, result)
		return True

	def _args(self, vm: Any, result: Any, count: int) -> list[int] | None:
		if not vm._require_stack(result, count):
			return None
		start = vm.sp - count
		values = vm.stack[start:vm.sp]
		vm.sp = start
		return values

	def _push_bool(self, vm: Any, value: bool) -> None:
		vm.push(TRUE if value else FALSE)

	def _fail(self, result: Any, message: str) -> None:
		position = Position(0, 0, 0, "<bin>", "")
		result.fail(VMError(message, position.copy(), position.copy()))

	def _valid_span(self, vm: Any, address: int, words: int = 1) -> bool:
		return words >= 0 and 0 <= address <= len(vm.data_memory) - words

	def _read_string(self, vm: Any, descriptor: int) -> str:
		if not 0 <= descriptor + 1 < len(vm.data_memory):
			return ""
		chars_address = vm.data_memory[descriptor]
		if not 0 <= chars_address < len(vm.data_memory):
			return ""
		return vm.read_mem_string(chars_address)

	def _push_string(self, vm: Any, result: Any, value: str) -> None:
		descriptor = vm.allocate_string(value, result)
		if not result.error:
			vm.push(descriptor)

	def _valid_window_pointer(self, vm: Any, pointer: int) -> bool:
		return 0 <= pointer <= len(vm.data_memory) - WINDOW_WORDS

	def _valid_screen_pointer(self, vm: Any, pointer: int) -> bool:
		return 0 <= pointer <= len(vm.data_memory) - SCREEN_WORDS

	def _ensure_window(self, vm: Any, pointer: int) -> int:
		if not self._valid_window_pointer(vm, pointer):
			return 0
		memory = vm.data_memory
		handle = memory[pointer + WINDOW_HANDLE]
		if handle and self.windows.record(handle):
			self.windows.configure(
				handle,
				_signed(memory[pointer + WINDOW_X]),
				_signed(memory[pointer + WINDOW_Y]),
				_signed(memory[pointer + WINDOW_WIDTH]),
				_signed(memory[pointer + WINDOW_HEIGHT]),
				self._read_string(vm, memory[pointer + WINDOW_TITLE]),
				_signed(memory[pointer + WINDOW_UI_SCALE]),
			)
			return handle
		if memory[pointer + WINDOW_STATE] == WindowState.CLOSED:
			return 0
		handle = self.windows.create(
			_signed(memory[pointer + WINDOW_X]),
			_signed(memory[pointer + WINDOW_Y]),
			_signed(memory[pointer + WINDOW_WIDTH]),
			_signed(memory[pointer + WINDOW_HEIGHT]),
			self._read_string(vm, memory[pointer + WINDOW_TITLE]),
			_signed(memory[pointer + WINDOW_UI_SCALE]),
		)
		memory[pointer + WINDOW_HANDLE] = handle
		return handle

	def _sync_window(self, vm: Any, pointer: int, handle: int) -> None:
		if not self._valid_window_pointer(vm, pointer):
			return
		record = self.windows.record(handle)
		if not record:
			vm.data_memory[pointer + WINDOW_STATE] = WindowState.CLOSED
			vm.data_memory[pointer + WINDOW_HANDLE] = 0
			return
		memory = vm.data_memory
		memory[pointer + WINDOW_X] = record.bounds.x & TRUE
		memory[pointer + WINDOW_Y] = record.bounds.y & TRUE
		memory[pointer + WINDOW_WIDTH] = record.bounds.width & TRUE
		memory[pointer + WINDOW_HEIGHT] = record.bounds.height & TRUE
		memory[pointer + WINDOW_STATE] = int(record.state)
		memory[pointer + WINDOW_HANDLE] = handle

	def _sync_screen(self, vm: Any, pointer: int) -> None:
		if not self._valid_screen_pointer(vm, pointer):
			return
		memory = vm.data_memory
		memory[pointer + SCREEN_FIELD_WIDTH] = self.graphics.width
		memory[pointer + SCREEN_FIELD_HEIGHT] = self.graphics.height

	def _window_args(
		self,
		vm: Any,
		result: Any,
		count: int,
		*,
		refresh: bool = False,
	) -> tuple[int, int, list[int]] | None:
		args = self._args(vm, result, count)
		if args is None:
			return None
		raw_pointer = args[0]
		is_screen = bool(raw_pointer & GRAPHICS_SCREEN_REFERENCE_TAG)
		pointer = (
			raw_pointer & GRAPHICS_REFERENCE_ADDRESS_MASK
			if is_screen
			else raw_pointer
		)
		if is_screen:
			handle = SCREEN_TARGET_HANDLE if self._valid_screen_pointer(vm, pointer) else 0
		elif not refresh and pointer == self._frame_window_pointer:
			handle = self._frame_window_handle
			if handle and not self.windows.record(handle):
				handle = self._ensure_window(vm, pointer)
				self._frame_window_handle = handle
		else:
			handle = self._ensure_window(vm, pointer)
		if refresh:
			self._frame_window_pointer = pointer
			self._frame_window_handle = handle
			self._frame_is_screen = is_screen
		return pointer, handle, args[1:]

	def _origin(self, handle: int) -> tuple[int, int]:
		if handle == SCREEN_TARGET_HANDLE:
			return 0, 0
		return self.windows.draw_origin(handle)

	def _target_scale(self, handle: int) -> int:
		return 1 if handle == SCREEN_TARGET_HANDLE else self.windows.ui_scale(handle)

	def _raw_rand32(self, vm: Any, result: Any) -> None:
		vm.push(self._rng.getrandbits(32))

	def _raw_randf(self, vm: Any, result: Any) -> None:
		vm.push(_float_bits(self._rng.random()))

	def _raw_seed(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._rng.seed(args[0] & TRUE)

	def _raw_get_hour(self, vm: Any, result: Any) -> None:
		vm.push(self.os.now().hour)

	def _raw_get_min(self, vm: Any, result: Any) -> None:
		vm.push(self.os.now().minute)

	@property
	def _raw_scale(self) -> int:
		return self.graphics.text_scale

	def _raw_clear(self, vm: Any, result: Any) -> None:
		self.graphics.clear_both(0)

	def _raw_clear_buffer(self, vm: Any, result: Any) -> None:
		self.graphics.clear(0)

	def _raw_clear_screen(self, vm: Any, result: Any) -> None:
		self.graphics.clear_screen(0)

	def _raw_update(self, vm: Any, result: Any) -> None:
		self.graphics.present(self.os.palette)

	def _raw_flip(self, vm: Any, result: Any) -> None:
		self.graphics.present(self.os.palette)
		self.graphics.clear(0)

	def _raw_append(self, vm: Any, result: Any) -> None:
		self.graphics.append(self.os.palette)

	def _raw_dump(self, vm: Any, result: Any) -> None:
		self.graphics.append(self.os.palette)
		self.graphics.clear(0)

	def _raw_set_region(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 4)
		if args is None:
			return
		x1, y1, x2, y2 = (_signed(value) for value in args)
		scale = self._raw_scale
		self.graphics.set_clip(x1 * scale, y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale)

	def _raw_reset_region(self, vm: Any, result: Any) -> None:
		self.graphics.reset_clip()

	def _raw_set_brightness_affect(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.graphics.brightness_affected = bool(args[0])

	def _raw_pixel(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 3)
		if args is None:
			return
		x, y, color = (_signed(value) for value in args)
		scale = self._raw_scale
		self.graphics.fill_rect(x * scale, y * scale, scale, scale, color)

	def _raw_line(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 5)
		if args is None:
			return
		x1, y1, x2, y2, color = (_signed(value) for value in args)
		self.graphics.draw_line_scaled(0, 0, x1, y1, x2, y2, color, self._raw_scale)

	def _raw_rect(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 5)
		if args is None:
			return
		x, y, width, height, color = (_signed(value) for value in args)
		self.graphics.draw_rect_scaled(0, 0, x, y, width, height, color, self._raw_scale)

	def _raw_fill_rect(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 5)
		if args is None:
			return
		x, y, width, height, color = (_signed(value) for value in args)
		scale = self._raw_scale
		self.graphics.fill_rect(x * scale, y * scale, width * scale, height * scale, color)

	def _raw_circle(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 4)
		if args is None:
			return
		x, y, radius, color = (_signed(value) for value in args)
		self.graphics.draw_circle_scaled(0, 0, x, y, radius, color, self._raw_scale)

	def _raw_fill_circle(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 4)
		if args is None:
			return
		x, y, radius, color = (_signed(value) for value in args)
		self.graphics.fill_circle_scaled(0, 0, x, y, radius, color, self._raw_scale)

	def _raw_triangle(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 7)
		if args is None:
			return
		values = tuple(_signed(value) for value in args)
		self.graphics.draw_triangle_scaled(0, 0, values[:6], values[6], self._raw_scale)

	def _raw_fill_triangle(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 7)
		if args is None:
			return
		values = tuple(_signed(value) for value in args)
		self.graphics.fill_triangle_scaled(0, 0, values[:6], values[6], self._raw_scale)

	def _raw_image(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 5)
		if args is None:
			return
		x, y, address, scale_bits, format_id = args
		if not self._valid_span(vm, address, 2):
			self._fail(result, "Invalid image address")
			return
		width = _signed(vm.data_memory[address])
		height = _signed(vm.data_memory[address + 1])
		if width < 0 or height < 0:
			self._fail(result, "Invalid image dimensions")
			return
		pixel_count = width * height
		if format_id == ImageFormat.PALETTE_WORDS:
			word_count = pixel_count
		elif format_id == ImageFormat.PACKED_PALETTE_BYTES:
			word_count = (pixel_count + 3) // 4
		else:
			self._fail(result, f"Unknown image format {format_id}")
			return
		if not self._valid_span(vm, address + 2, word_count):
			self._fail(result, "Image data extends beyond memory")
			return
		scale_value = _float(scale_bits)
		if scale_value <= 0:
			return
		left = _signed(x)
		top = _signed(y)
		for index in range(pixel_count):
			if format_id == ImageFormat.PALETTE_WORDS:
				color = vm.data_memory[address + 2 + index]
			else:
				word = vm.data_memory[address + 2 + index // 4]
				color = (word >> ((index % 4) * 8)) & 0xFF
			px0 = round((left + index % width * scale_value) * self._raw_scale)
			py0 = round((top + index // width * scale_value) * self._raw_scale)
			px1 = round((left + (index % width + 1) * scale_value) * self._raw_scale)
			py1 = round((top + (index // width + 1) * scale_value) * self._raw_scale)
			self.graphics.fill_rect(px0, py0, max(1, px1 - px0), max(1, py1 - py0), color)

	def _raw_character(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 3)
		if args is not None:
			x, y, value = (_signed(item) for item in args)
			self.graphics.draw_text(x * self._raw_scale, y * self._raw_scale, chr(value & 0xFF), 15, self._raw_scale)

	def _raw_string(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 4)
		if args is None:
			return
		x, y, address, wrap = args
		try:
			text = vm.read_mem_string(address)
		except ValueError as error:
			self._fail(result, str(error))
			return
		scale = self._raw_scale
		cursor_x = _signed(x)
		cursor_y = _signed(y)
		origin_x = cursor_x
		maximum_x = self.graphics.clip_rect[2] // scale
		for char in text:
			advance = self.graphics.text_advance(char, 1)
			if char == "\n" or (wrap and cursor_x + advance > maximum_x):
				cursor_x = origin_x
				cursor_y += 8
				if char == "\n":
					continue
			self.graphics.draw_text(cursor_x * scale, cursor_y * scale, char, 15, scale)
			cursor_x += advance

	def _raw_window(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is None:
			return
		pointer = args[0]
		handle = self._ensure_window(vm, pointer)
		if handle:
			self.windows.update(handle)
			self.windows.draw(handle)
			self._sync_window(vm, pointer, handle)

	def _raw_taskbar(self, vm: Any, result: Any) -> None:
		scale = self._raw_scale
		y = 168
		self.graphics.fill_rect(0, y * scale, 240 * scale, 12 * scale, 1)
		self.graphics.fill_rect(0, y * scale, 240 * scale, scale, 7)
		self.graphics.draw_rect_scaled(0, 0, 3, 170, 8, 7, 15, scale)
		self.graphics.draw_line_scaled(0, 0, 5, 173, 6, 175, 11, scale)
		self.graphics.draw_line_scaled(0, 0, 6, 175, 9, 171, 11, scale)
		self.graphics.draw_text_small(226 * scale, 171 * scale, "XE", 15, scale)

	def _raw_task_atom(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is None:
			return
		rotation, tilt = (_signed(value) for value in args)
		angle = math.radians(rotation)
		tilt_scale = max(0.15, abs(math.cos(math.radians(tilt))))
		cx = 120
		cy = 90
		radius = 12
		dx = round(math.cos(angle) * radius)
		dy = round(math.sin(angle) * radius * tilt_scale)
		scale = self._raw_scale
		self.graphics.draw_line_scaled(0, 0, cx - dx, cy - dy, cx + dx, cy + dy, 13, scale)
		self.graphics.draw_line_scaled(0, 0, cx - dy, cy + dx, cx + dy, cy - dx, 7, scale)
		self.graphics.fill_circle_scaled(0, 0, cx, cy, 3, 11, scale)

	def _raw_button(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 6)
		if args is None:
			return
		x, y, width, height, color, address = args
		values = [_signed(value) for value in (x, y, width, height, color)]
		x, y, width, height, color = values
		try:
			label = vm.read_mem_string(address)
		except ValueError as error:
			self._fail(result, str(error))
			return
		scale = self._raw_scale
		self.graphics.fill_rect(x * scale, y * scale, width * scale, height * scale, color)
		self.graphics.draw_rect_scaled(0, 0, x, y, width, height, 15, scale)
		text_width = self.graphics.measure_text(label, scale)
		self.graphics.draw_text(
			x * scale + max(scale, (width * scale - text_width) // 2),
			(y + max(1, (height - 7) // 2)) * scale,
			label,
			15,
			scale,
		)

	def _raw_slider(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 6)
		if args is None:
			return
		x, y, width, value, minimum, maximum = (_signed(item) for item in args)
		if maximum < minimum:
			minimum, maximum = maximum, minimum
		width = max(3, width)
		value = max(minimum, min(maximum, value))
		frame = self.input.frame()
		scale = self._raw_scale
		left = x * scale
		top = y * scale
		pixel_width = width * scale
		inside = left <= frame.x < left + pixel_width and top <= frame.y < top + 7 * scale
		key = (x, y, width)
		if frame.left_pressed and inside:
			self._raw_slider_capture = key
		if self._raw_slider_capture == key and (frame.left_down or frame.left_released):
			span = max(1, maximum - minimum)
			relative = max(0, min(pixel_width - 1, frame.x - left))
			value = minimum + round(relative * span / max(1, pixel_width - 1))
		if frame.left_released and self._raw_slider_capture == key:
			self._raw_slider_capture = None
		span = max(1, maximum - minimum)
		knob = x + round((value - minimum) * (width - 1) / span)
		self.graphics.fill_rect(left, (y + 3) * scale, pixel_width, scale, 8)
		self.graphics.fill_rect(left, (y + 3) * scale, max(scale, (knob - x + 1) * scale), scale, 11)
		self.graphics.fill_rect(knob * scale, (y + 1) * scale, scale, 5 * scale, 15)
		vm.push(value & TRUE)

	def _raw_width(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is None:
			return
		char = args[0]
		vm.push(self.graphics.get_chr_width(char))

	def _raw_width_small(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is None:
			return
		char = args[0]
		vm.push(self.graphics.get_chr_width_small(char))

	def _raw_mouse_poll(self, vm: Any, result: Any) -> None:
		event, x, y = self.input.poll_mouse()
		vm.push(event)
		vm.push(x // self._raw_scale)
		vm.push(y // self._raw_scale)

	def _raw_previous_event(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.input.previous_event(_signed(args[0])))

	def _raw_keyboard_poll(self, vm: Any, result: Any) -> None:
		event, key, modifiers = self.input.poll_keyboard()
		vm.push(event)
		vm.push(key & TRUE)
		vm.push(modifiers & TRUE)

	def _raw_key_is_down(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.input.is_key_down(_signed(args[0])))

	def _raw_bounds_check(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 4)
		if args is None:
			return
		x, y, width, height = (_signed(value) for value in args)
		mouse_x, mouse_y = self.input.pointer_position()
		mouse_x //= self._raw_scale
		mouse_y //= self._raw_scale
		vm.cr = TRUE if (
			width > 0
			and height > 0
			and x <= mouse_x < x + width
			and y <= mouse_y < y + height
		) else FALSE

	def _graphics_width(self, vm: Any, result: Any) -> None:
		vm.push(self.graphics.width)

	def _graphics_height(self, vm: Any, result: Any) -> None:
		vm.push(self.graphics.height)

	def _graphics_content_width(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 1)
		if not entry or not entry[1]:
			vm.push(0)
			return
		handle = entry[1]
		if handle == SCREEN_TARGET_HANDLE:
			vm.push(self.graphics.width)
		else:
			vm.push(self.windows.content_width(handle) // self.windows.ui_scale(handle))

	def _graphics_content_height(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 1)
		if not entry or not entry[1]:
			vm.push(0)
			return
		handle = entry[1]
		if handle == SCREEN_TARGET_HANDLE:
			vm.push(self.graphics.height)
		else:
			vm.push(self.windows.content_height(handle) // self.windows.ui_scale(handle))

	def _graphics_draw_background(self, vm: Any, result: Any) -> None:
		self.os.draw_background(self.graphics)

	def _graphics_begin_draw(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 1, refresh=True)
		if not entry:
			return
		pointer, handle, _ = entry
		self.graphics.reset_clip()
		self.os.draw_background(self.graphics)
		if not handle:
			self.graphics.set_clip(0, 0, 0, 0)
			return
		if self._frame_is_screen:
			self._sync_screen(vm, pointer)
			self.graphics.set_clip(0, 0, self.graphics.width, self.graphics.height)
			return
		self.windows.begin_widget_frame(handle)
		self.windows.update(handle)
		self._sync_window(vm, pointer, handle)
		self.windows.draw(handle)
		if (
			self.windows.is_open(handle)
			and not self.windows.is_minimized(handle)
		):
			self.graphics.set_clip(
				self.windows.content_x(handle),
				self.windows.content_y(handle),
				self.windows.content_width(handle),
				self.windows.content_height(handle),
			)
		else:
			self.graphics.set_clip(0, 0, 0, 0)

	def _graphics_update(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 1)
		if not entry:
			self._frame_window_pointer = None
			self._frame_window_handle = 0
			self._frame_is_screen = False
			return
		pointer, handle, _ = entry
		try:
			self.graphics.reset_clip()
			if handle == SCREEN_TARGET_HANDLE:
				self._sync_screen(vm, pointer)
			elif handle:
				self.windows.finish_draw(handle)
				self.windows.draw_drag_outline(handle)
				self._sync_window(vm, pointer, handle)
			self.graphics.present(self.os.palette)
			self.input.finish_frame()
		finally:
			self._frame_window_pointer = None
			self._frame_window_handle = 0
			self._frame_is_screen = False

	def _graphics_clear(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 2)
		if entry:
			self.graphics.clear(_signed(entry[2][0]))

	def _translate_draw(self, vm: Any, result: Any, count: int, draw: str) -> None:
		entry = self._window_args(vm, result, count)
		if not entry or not entry[1]:
			return
		_, handle, values = entry
		ox, oy = self._origin(handle)
		scale = self._target_scale(handle)
		values = [_signed(value) for value in values]
		if draw == "pixel":
			x, y, color = values
			self.graphics.fill_rect(ox + x * scale, oy + y * scale, scale, scale, color)
		elif draw == "circle":
			x, y, radius, color = values
			self.graphics.draw_circle_scaled(ox, oy, x, y, radius, color, scale)
		elif draw == "line":
			x1, y1, x2, y2, color = values
			self.graphics.draw_line_scaled(ox, oy, x1, y1, x2, y2, color, scale)
		elif draw == "rect":
			x, y, width, height, color = values
			self.graphics.draw_rect_scaled(ox, oy, x, y, width, height, color, scale)
		elif draw == "fill":
			x, y, width, height, color = values
			self.graphics.fill_rect(
				ox + x * scale,
				oy + y * scale,
				width * scale,
				height * scale,
				color,
			)

	def _graphics_set_pixel(self, vm: Any, result: Any) -> None:
		self._translate_draw(vm, result, 4, "pixel")

	def _graphics_draw_circle(self, vm: Any, result: Any) -> None:
		self._translate_draw(vm, result, 5, "circle")

	def _graphics_draw_line(self, vm: Any, result: Any) -> None:
		self._translate_draw(vm, result, 6, "line")

	def _graphics_draw_rect(self, vm: Any, result: Any) -> None:
		self._translate_draw(vm, result, 6, "rect")

	def _graphics_fill_rect(self, vm: Any, result: Any) -> None:
		self._translate_draw(vm, result, 6, "fill")

	def _graphics_text(self, vm: Any, result: Any, kind: str) -> None:
		entry = self._window_args(vm, result, 5)
		if not entry or not entry[1]:
			return
		_, handle, values = entry
		x, y, value, color = values
		ox, oy = self._origin(handle)
		scale = self._target_scale(handle)
		if kind == "text":
			text = self._read_string(vm, value)
		elif kind == "int":
			text = str(_signed(value))
		else:
			text = f"{_float(value):.6f}"
		self.graphics.draw_text(
			ox + _signed(x) * scale,
			oy + _signed(y) * scale,
			text,
			_signed(color),
			pixel_scale=scale,
		)

	def _graphics_draw_text(self, vm: Any, result: Any) -> None:
		self._graphics_text(vm, result, "text")

	def _graphics_draw_int(self, vm: Any, result: Any) -> None:
		self._graphics_text(vm, result, "int")

	def _graphics_draw_float(self, vm: Any, result: Any) -> None:
		self._graphics_text(vm, result, "float")

	def _graphics_text_small(self, vm: Any, result: Any, kind: str) -> None:
		entry = self._window_args(vm, result, 5)
		if not entry or not entry[1]:
			return
		_, handle, values = entry
		x, y, value, color = values
		ox, oy = self._origin(handle)
		scale = self._target_scale(handle)
		if kind == "text":
			text = self._read_string(vm, value)
		elif kind == "int":
			text = str(_signed(value))
		else:
			text = f"{_float(value):.4f}"
		self.graphics.draw_text_small(
			ox + _signed(x) * scale,
			oy + _signed(y) * scale,
			text,
			_signed(color),
			pixel_scale=scale,
		)

	def _graphics_draw_text_small(self, vm: Any, result: Any) -> None:
		self._graphics_text_small(vm, result, "text")

	def _graphics_draw_int_small(self, vm: Any, result: Any) -> None:
		self._graphics_text_small(vm, result, "int")

	def _graphics_draw_float_small(self, vm: Any, result: Any) -> None:
		self._graphics_text_small(vm, result, "float")

	def _graphics_draw_char(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 5)
		if not entry or not entry[1]:
			return
		_, handle, values = entry
		x, y, value, color = values
		ox, oy = self._origin(handle)
		scale = self._target_scale(handle)
		self.graphics.draw_text(
			ox + _signed(x) * scale,
			oy + _signed(y) * scale,
			chr(value & 0xFF),
			_signed(color),
			pixel_scale=scale,
		)

	def _graphics_draw_char_small(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 5)
		if not entry or not entry[1]:
			return
		_, handle, values = entry
		x, y, value, color = values
		ox, oy = self._origin(handle)
		scale = self._target_scale(handle)
		self.graphics.draw_text_small(
			ox + _signed(x) * scale,
			oy + _signed(y) * scale,
			chr(value & 0xFF),
			_signed(color),
			pixel_scale=scale,
		)

	def _graphics_char_advance(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is None:
			return
		value, font_size = args
		vm.push(self.graphics.styled_char_advance(chr(value & 0xFF), _signed(font_size)))

	def _graphics_draw_char_styled(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 7)
		if not entry or not entry[1]:
			return
		_, handle, values = entry
		x, y, value, color, font_size, style = values
		ox, oy = self._origin(handle)
		scale = self._target_scale(handle)
		self.graphics.draw_char_styled(
			ox + _signed(x) * scale,
			oy + _signed(y) * scale,
			chr(value & 0xFF),
			_signed(color),
			_signed(font_size),
			_signed(style),
			pixel_scale=scale,
		)

	def _graphics_button(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 6)
		if not entry or not entry[1]:
			self._push_bool(vm, False)
			return
		_, handle, values = entry
		x, y, width, height, descriptor = values
		self._push_bool(vm, self.windows.button(
			handle, _signed(x), _signed(y), _signed(width), _signed(height),
			self._read_string(vm, descriptor),
		))

	def _graphics_button_tone(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 7)
		if not entry or not entry[1]:
			self._push_bool(vm, False)
			return
		_, handle, values = entry
		x, y, width, height, descriptor, color = values
		self._push_bool(vm, self.windows.button(
			handle,
			_signed(x),
			_signed(y),
			_signed(width),
			_signed(height),
			self._read_string(vm, descriptor),
			_signed(color),
		))

	def _graphics_button_flat(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 7)
		if not entry or not entry[1]:
			self._push_bool(vm, False)
			return
		_, handle, values = entry
		x, y, width, height, descriptor, color = values
		self._push_bool(vm, self.windows.button(
			handle,
			_signed(x),
			_signed(y),
			_signed(width),
			_signed(height),
			self._read_string(vm, descriptor),
			_signed(color),
			False,
		))

	def _graphics_draw_atom(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 7)
		if not entry or not entry[1]:
			return
		_, handle, values = entry
		cx, cy, radius, state, ring_style, phase = (_signed(value) for value in values)
		radius = max(4, radius)
		scale = self._target_scale(handle)
		ox, oy = self._origin(handle)
		center_x = ox + cx * scale
		center_y = oy + cy * scale
		phase %= 360
		ring_color = 10
		if state == 1:
			ring_color = 14
		elif state == 2:
			ring_color = 11
			radius += 1 if phase % 24 < 12 else 0
		elif state == 3:
			ring_color = 12
		elif state == 4:
			ring_color = 8

		ring_radius = radius + 4
		if ring_style and state not in (1, 3):
			if ring_style == 1:
				self.graphics.draw_circle_scaled(ox, oy, cx, cy, ring_radius, ring_color, scale)
			else:
				for angle in range(0, 360, 30):
					x = center_x + round(math.cos(math.radians(angle)) * ring_radius * scale)
					y = center_y + round(math.sin(math.radians(angle)) * ring_radius * scale)
					self.graphics.fill_rect(x, y, scale, scale, ring_color)
		elif state in (1, 3):
			for angle in range(0, 360, 45):
				for offset in (0, 7):
					point_angle = angle + offset
					x = center_x + round(math.cos(math.radians(point_angle)) * ring_radius * scale)
					y = center_y + round(math.sin(math.radians(point_angle)) * ring_radius * scale)
					self.graphics.fill_rect(x, y, scale, scale, ring_color)

		orbit_radius = radius + 7
		previous: tuple[int, int] | None = None
		for angle in range(0, 361, 15):
			radians = math.radians(angle)
			x = center_x + round(math.cos(radians) * orbit_radius * scale)
			y = center_y + round(math.sin(radians) * orbit_radius * scale * 0.42)
			if previous is not None:
				self.graphics.draw_line(previous[0], previous[1], x, y, 8)
			previous = (x, y)

		nucleus_color = 8 if state == 4 else 13
		highlight_color = 8 if state == 4 else (12 if state == 3 else 11)
		self.graphics.fill_circle_scaled(ox, oy, cx + 1, cy + 1, max(2, radius - 1), 8, scale)
		self.graphics.fill_circle_scaled(ox, oy, cx, cy, max(2, radius - 2), nucleus_color, scale)
		self.graphics.fill_circle_scaled(ox, oy, cx, cy, max(1, radius - 5), highlight_color, scale)
		self.graphics.fill_rect(
			ox + (cx - max(1, radius // 3)) * scale,
			oy + (cy - max(1, radius // 3)) * scale,
			scale,
			scale,
			15 if state != 4 else 8,
		)
		for electron_index, electron_phase in enumerate((phase, phase + 180)):
			x = center_x + round(math.cos(math.radians(electron_phase)) * orbit_radius * scale)
			y = center_y + round(math.sin(math.radians(electron_phase)) * orbit_radius * scale * 0.42)
			electron_color = 14 if electron_index == 0 else 10
			if state == 4:
				electron_color = 8
			self.graphics.fill_rect(x - scale, y - scale, 2 * scale, 2 * scale, electron_color)

	def _graphics_draw_icon(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 6)
		if not entry or not entry[1]:
			return
		_, handle, values = entry
		x, y, width, height, descriptor = values
		self._draw_icon(vm, handle, x, y, width, height, descriptor, 1)

	def _graphics_draw_icon_scaled(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 7)
		if not entry or not entry[1]:
			return
		_, handle, values = entry
		x, y, width, height, descriptor, icon_scale = values
		self._draw_icon(vm, handle, x, y, width, height, descriptor, icon_scale)

	def _draw_icon(
		self,
		vm: Any,
		handle: int,
		x: int,
		y: int,
		width: int,
		height: int,
		descriptor: int,
		icon_scale: int,
	) -> None:
		x = _signed(x)
		y = _signed(y)
		width = _signed(width)
		height = _signed(height)
		icon_scale = _signed(icon_scale)
		if width <= 0 or height <= 0 or icon_scale <= 0:
			return
		icon_scale = min(icon_scale, 16)
		pixels: list[int | None] = []
		for char in self._read_string(vm, descriptor):
			if char in " \t\r\n":
				continue
			if char == ".":
				pixels.append(None)
			elif "0" <= char <= "9":
				pixels.append(ord(char) - ord("0"))
			elif "A" <= char <= "F":
				pixels.append(ord(char) - ord("A") + 10)
			elif "a" <= char <= "f":
				pixels.append(ord(char) - ord("a") + 10)
			else:
				pixels.append(None)
			if len(pixels) >= width * height:
				break
		ox, oy = self._origin(handle)
		target_scale = self._target_scale(handle)
		pixel_size = icon_scale * target_scale
		for index, color in enumerate(pixels):
			if color is None:
				continue
			self.graphics.fill_rect(
				ox + (x + (index % width) * icon_scale) * target_scale,
				oy + (y + (index // width) * icon_scale) * target_scale,
				pixel_size,
				pixel_size,
				color,
			)

	def _graphics_slider(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 7)
		if not entry or not entry[1]:
			vm.push(0)
			return
		_, handle, values = entry
		x, y, width, value, minimum, maximum = (_signed(v) for v in values)
		vm.push(self.windows.slider(handle, x, y, width, value, minimum, maximum) & TRUE)

	def _graphics_mouse_x(self, vm: Any, result: Any) -> None:
		vm.push(self.input.frame().x)

	def _graphics_mouse_y(self, vm: Any, result: Any) -> None:
		vm.push(self.input.frame().y)

	def _graphics_pointer_x(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 1)
		if not entry or not entry[1]:
			vm.push(-1 & TRUE)
			return
		handle = entry[1]
		if handle == SCREEN_TARGET_HANDLE:
			vm.push(self.input.frame().x & TRUE)
		else:
			vm.push(((self.input.frame().x - self.windows.content_x(handle)) // self.windows.ui_scale(handle)) & TRUE)

	def _graphics_pointer_y(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 1)
		if not entry or not entry[1]:
			vm.push(-1 & TRUE)
			return
		handle = entry[1]
		if handle == SCREEN_TARGET_HANDLE:
			vm.push(self.input.frame().y & TRUE)
		else:
			vm.push(((self.input.frame().y - self.windows.content_y(handle)) // self.windows.ui_scale(handle)) & TRUE)

	def _graphics_mouse_down(self, vm: Any, result: Any) -> None:
		self._push_bool(vm, self.input.frame().left_down)

	def _graphics_mouse_pressed(self, vm: Any, result: Any) -> None:
		self._push_bool(vm, self.input.frame().left_pressed)

	def _graphics_mouse_released(self, vm: Any, result: Any) -> None:
		self._push_bool(vm, self.input.frame().left_released)

	def _graphics_scroll_delta(self, vm: Any, result: Any) -> None:
		vm.push(self.input.frame().scroll_delta & TRUE)

	def _graphics_key_down(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, _signed(args[0]) in self.input.frame().keys_down)

	def _graphics_read_key(self, vm: Any, result: Any) -> None:
		vm.push(self.input.read_key() & TRUE)

	def _graphics_modifiers(self, vm: Any, result: Any) -> None:
		vm.push(self.input.frame().modifiers & TRUE)

	def _graphics_right_mouse_down(self, vm: Any, result: Any) -> None:
		self._push_bool(vm, self.input.frame().right_down)

	def _graphics_right_mouse_pressed(self, vm: Any, result: Any) -> None:
		self._push_bool(vm, self.input.frame().right_pressed)

	def _graphics_right_mouse_released(self, vm: Any, result: Any) -> None:
		self._push_bool(vm, self.input.frame().right_released)

	def _os_get_volume(self, vm: Any, result: Any) -> None:
		vm.push(self.os.volume)

	def _os_set_volume(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.os.set_volume(_signed(args[0]))

	def _os_get_background(self, vm: Any, result: Any) -> None:
		vm.push(self.os.background_id)

	def _os_set_background(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.os.set_background(_signed(args[0]))

	def _os_get_palette(self, vm: Any, result: Any) -> None:
		vm.push(self.os.palette_id)

	def _os_set_palette(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.os.set_palette(_signed(args[0]))

	def _os_sleep(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.wait_interruptibly(max(0, _signed(args[0])) / 1000.0)

	def _os_exit(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.exit_code = _signed(args[0])
			vm.halt_requested = True

	def _os_apply_settings(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 3)
		if args is not None:
			self._push_bool(vm, self.os.apply(*(_signed(v) for v in args)))

	def _os_background_count(self, vm: Any, result: Any) -> None:
		vm.push(self.os.background_count)

	def _os_palette_count(self, vm: Any, result: Any) -> None:
		vm.push(self.os.palette_count)

	def _os_ticks(self, vm: Any, result: Any) -> None:
		vm.push(int((time.monotonic() - vm.start_time) * 1000) & TRUE)

	def _os_year(self, vm: Any, result: Any) -> None:
		vm.push(self.os.now().year)

	def _os_month(self, vm: Any, result: Any) -> None:
		vm.push(self.os.now().month)

	def _os_day(self, vm: Any, result: Any) -> None:
		vm.push(self.os.now().day)

	def _window_close(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is None:
			return
		pointer = args[0]
		if not self._valid_window_pointer(vm, pointer):
			return
		if pointer == self._frame_window_pointer:
			self._frame_window_pointer = None
			self._frame_window_handle = 0
		handle = vm.data_memory[pointer + WINDOW_HANDLE]
		if handle:
			self.windows.destroy(handle)
			self._sync_window(vm, pointer, handle)
		else:
			vm.data_memory[pointer + WINDOW_STATE] = WindowState.CLOSED

	def _window_is_fullscreen(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			handle = self._ensure_window(vm, args[0])
			self._push_bool(vm, bool(handle and self.windows.is_maximized(handle)))

	def _window_is_minimized(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			handle = self._ensure_window(vm, args[0])
			self._push_bool(vm, bool(handle and self.windows.is_minimized(handle)))

	def _os_open_read(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.files.open_read(self._read_string(vm, args[0])))

	def _os_open_write(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.files.open_write(self._read_string(vm, args[0])))

	def _os_read(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_string(vm, result, self.files.read(args[0]))

	def _os_write(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			self._push_bool(vm, self.files.write(args[0], self._read_string(vm, args[1])))

	def _os_close(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.files.close_path(self._read_string(vm, args[0]))

	def _os_entry_count(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.files.entry_count(self._read_string(vm, args[0])))

	def _os_entry_name(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			self._push_string(vm, result, self.files.entry_name(self._read_string(vm, args[0]), _signed(args[1])))

	def _os_entry_is_directory(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			self._push_bool(vm, self.files.entry_is_directory(self._read_string(vm, args[0]), _signed(args[1])))

	def _os_path_exists(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.files.exists(self._read_string(vm, args[0])))

	def _os_make_file(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.files.make_file(self._read_string(vm, args[0])))

	def _os_make_directory(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.files.make_directory(self._read_string(vm, args[0])))

	def _os_rename(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			self._push_bool(vm, self.files.rename(self._read_string(vm, args[0]), self._read_string(vm, args[1])))

	def _os_delete(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.files.delete(self._read_string(vm, args[0])))

	def _os_get_music_volume(self, vm: Any, result: Any) -> None:
		vm.push(self.os.music_volume)

	def _os_set_music_volume(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.os.set_music_volume(_signed(args[0]))

	def _os_get_sound_effect_volume(self, vm: Any, result: Any) -> None:
		vm.push(self.os.sound_effect_volume)

	def _os_set_sound_effect_volume(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.os.set_sound_effect_volume(_signed(args[0]))

	def _os_get_theme_mode(self, vm: Any, result: Any) -> None:
		vm.push(self.os.theme_mode)

	def _os_set_theme_mode(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.os.set_theme_mode(_signed(args[0]))

	def _os_get_window_transparency(self, vm: Any, result: Any) -> None:
		vm.push(self.os.window_transparency)

	def _os_set_window_transparency(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.os.set_window_transparency(_signed(args[0]))

	def _os_get_window_corner_style(self, vm: Any, result: Any) -> None:
		vm.push(self.os.window_corner_style)

	def _os_set_window_corner_style(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.os.set_window_corner_style(_signed(args[0]))

	def _os_get_icon_size(self, vm: Any, result: Any) -> None:
		vm.push(self.os.icon_size)

	def _os_set_icon_size(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.os.set_icon_size(_signed(args[0]))

	def _os_get_clock_format(self, vm: Any, result: Any) -> None:
		vm.push(self.os.clock_format)

	def _os_set_clock_format(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.os.set_clock_format(_signed(args[0]))

	def _os_get_settings_enabled(self, vm: Any, result: Any) -> None:
		self._push_bool(vm, self.os.settings_enabled)

	def _os_set_settings_enabled(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self.os.set_settings_enabled(bool(args[0]))

	def _os_apply_preferences(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 11)
		if args is not None:
			values = [_signed(value) for value in args]
			self._push_bool(vm, self.os.apply_preferences(*values))

	def _currency_count(self, vm: Any, result: Any) -> None:
		vm.push(self.currency.count)

	def _currency_code(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_string(vm, result, self.currency.code(_signed(args[0])))

	def _currency_load(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 3)
		if args is not None:
			self._push_bool(vm, self.currency.load(*(_signed(value) for value in args)))

	def _currency_status(self, vm: Any, result: Any) -> None:
		vm.push(self.currency.status)

	def _currency_rate(self, vm: Any, result: Any) -> None:
		vm.push(_float_bits(self.currency.rate))

	def _currency_point_count(self, vm: Any, result: Any) -> None:
		vm.push(self.currency.point_count)

	def _currency_point(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(_float_bits(self.currency.point(_signed(args[0]))))

	def _currency_point_date(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_string(vm, result, self.currency.point_date(_signed(args[0])))

	def _compiler_check(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.compiler.compile(self._read_string(vm, args[0])))

	def _compiler_error(self, vm: Any, result: Any) -> None:
		self._push_string(vm, result, self.compiler.snapshot.error)

	def _compiler_error_line(self, vm: Any, result: Any) -> None:
		vm.push(self.compiler.snapshot.line)

	def _compiler_error_column(self, vm: Any, result: Any) -> None:
		vm.push(self.compiler.snapshot.column)

	def _compiler_assembly(self, vm: Any, result: Any) -> None:
		self._push_string(vm, result, self.compiler.snapshot.assembly)

	def _compiler_bytecode_size(self, vm: Any, result: Any) -> None:
		vm.push(self.compiler.snapshot.bytecode_size)

	def _compiler_load_visual(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.compiler.load_visual(self._read_string(vm, args[0])))

	def _compiler_atom_count(self, vm: Any, result: Any) -> None:
		vm.push(self.compiler.atom_count())

	def _compiler_atom_text(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_string(vm, result, self.compiler.atom_text(_signed(args[0])))

	def _compiler_atom_kind(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.compiler.atom_kind(_signed(args[0])))

	def _compiler_atom_line(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.compiler.atom_line(_signed(args[0])))

	def _compiler_atom_enabled(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.compiler.atom_enabled(_signed(args[0])))

	def _compiler_set_atom_enabled(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			self._push_bool(vm, self.compiler.set_atom_enabled(_signed(args[0]), bool(args[1])))

	def _compiler_visual_source(self, vm: Any, result: Any) -> None:
		self._push_string(vm, result, self.compiler.visual_source)

	def _compiler_script_count(self, vm: Any, result: Any) -> None:
		vm.push(self.compiler.script_count())

	def _compiler_script_name(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_string(vm, result, self.compiler.script_name(_signed(args[0])))

	def _compiler_script_shell(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.compiler.script_shell(_signed(args[0])))

	def _compiler_script_line(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.compiler.script_line(_signed(args[0])))

	def _compiler_script_enabled(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.compiler.script_enabled(_signed(args[0])))

	def _compiler_load_document(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 3)
		if args is not None:
			vm.push(self.compiler.load_document(
				_signed(args[0]), self._read_string(vm, args[1]), self._read_string(vm, args[2])
			))

	def _compiler_document_script_count(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.compiler.document_script_count(_signed(args[0])))

	def _compiler_document_script_name(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			self._push_string(vm, result, self.compiler.document_script_name(_signed(args[0]), _signed(args[1])))

	def _compiler_document_script_shell(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			vm.push(self.compiler.document_script_shell(_signed(args[0]), _signed(args[1])))

	def _compiler_document_script_line(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			vm.push(self.compiler.document_script_line(_signed(args[0]), _signed(args[1])))

	def _compiler_document_script_enabled(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			self._push_bool(vm, self.compiler.document_script_enabled(_signed(args[0]), _signed(args[1])))

	def _compiler_document_source(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_string(vm, result, self.compiler.document_source(_signed(args[0])))

	def _compiler_run(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is None:
			return
		source = self._read_string(vm, args[0])

		def bounded_text(value: str) -> str:
			text = str(value).replace("\x00", "\\0")
			if len(text) <= COMPILER_RUN_OUTPUT_LIMIT:
				return text
			limit = COMPILER_RUN_OUTPUT_LIMIT - len(COMPILER_RUN_TRUNCATION_MARKER)
			return text[:limit] + COMPILER_RUN_TRUNCATION_MARKER

		depth = getattr(_compiler_run_state, "depth", 0)
		if depth > 0:
			message = "Runtime error: nested compiler::run is not allowed"
			self.compiler.set_runtime_error(message)
			self._push_string(vm, result, message)
			return
		if len(source) > COMPILER_RUN_SOURCE_LIMIT:
			message = f"Compile error: source exceeds {COMPILER_RUN_SOURCE_LIMIT} characters"
			self.compiler.set_runtime_error(message)
			self._push_string(vm, result, message)
			return
		if vm.cancel_event.is_set():
			message = "Runtime canceled."
			self.compiler.set_runtime_error(message)
			self._push_string(vm, result, message)
			return
		if not self.compiler.compile(source):
			message = bounded_text(
				f"Compile error at {self.compiler.snapshot.line}:"
				f"{self.compiler.snapshot.column}: {self.compiler.snapshot.error}"
			)
			self._push_string(vm, result, message)
			return

		output: list[str] = []
		output_length = 0
		truncated = False
		capture_limit = COMPILER_RUN_OUTPUT_LIMIT - len(COMPILER_RUN_TRUNCATION_MARKER)

		def capture(text: str) -> None:
			nonlocal output_length, truncated
			if output_length >= capture_limit:
				truncated = True
				return
			piece = str(text).replace("\x00", "\\0")
			remaining = capture_limit - output_length
			if len(piece) > remaining:
				piece = piece[:remaining]
				truncated = True
			output.append(piece)
			output_length += len(piece)

		try:
			from xe_lang.vm import VM

			_compiler_run_state.depth = depth + 1
			child = VM(
				list(self.compiler.bytecode),
				output_handler=capture,
				os_device=self.os,
				frame_handler=lambda _snapshot: None,
				cancel_event=vm.cancel_event,
				filesystem_root=self.files.root,
				input_handler=lambda _prompt="": "",
				request_handler=None,
				memory_words=len(vm.data_memory),
			)
			run_result = child.run(
				instruction_limit=COMPILER_RUN_INSTRUCTION_LIMIT,
				wall_time_limit=COMPILER_RUN_TIME_LIMIT,
			)
			if vm.cancel_event.is_set():
				message = "Runtime canceled."
				self.compiler.set_runtime_error(message)
				self._push_string(vm, result, message)
				return
			if run_result.error is not None:
				description = getattr(run_result.error, "desc", str(run_result.error))
				message = bounded_text(f"Runtime error: {description}")
				self.compiler.set_runtime_error(message)
				self._push_string(vm, result, message)
				return
		except Exception as error:
			message = bounded_text(f"Runtime error: {error}")
			self.compiler.set_runtime_error(message)
			self._push_string(vm, result, message)
			return
		finally:
			_compiler_run_state.depth = depth

		text = "".join(output)
		if truncated:
			text += COMPILER_RUN_TRUNCATION_MARKER
		if not text:
			text = "Program completed."
		self._push_string(vm, result, text)

	def _string_append(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is None:
			return
		target, suffix = args
		try:
			value = vm.read_string_descriptor(target) + vm.read_string_descriptor(suffix)
		except ValueError as error:
			self._fail(result, str(error))
			return
		vm.write_string_descriptor(target, value, result)

	def _string_append_char(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is None:
			return
		target, value = args
		try:
			text = vm.read_string_descriptor(target) + chr(value & 0xFF)
		except ValueError as error:
			self._fail(result, str(error))
			return
		vm.write_string_descriptor(target, text, result)
