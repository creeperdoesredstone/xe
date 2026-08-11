from __future__ import annotations

import math
import random
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable

from xe_lang import graphics_commands as gc
from xe_lang.helper import Position, VMError
from xe_lang.syscall_abi import (
	GRAPHICS_REFERENCE_ADDRESS_MASK,
	GRAPHICS_SCREEN_REFERENCE_TAG,
	ImageFormat,
	SyscallID,
)

from .currency import CurrencyDevice
from .compiler import CompilerDevice
from .assets import AudioDevice, AudioState, ImageAssetStore
from .filesystem import FileSystemDevice
from .graphics import FrameSnapshot, GraphicsBufferSnapshot, GraphicsDevice
from .input import InputDevice
from .os_state import OSDevice
from .theme import SCREEN_HEIGHT, SCREEN_WIDTH
from .windows import WindowManager, WindowState


TRUE = 0xFFFFFFFF
FALSE = 0
COMPILER_RUN_INSTRUCTION_LIMIT = 500_000
COMPILER_RUN_OUTPUT_LIMIT = 8_192
COMPILER_RUN_SOURCE_LIMIT = 32_768
COMPILER_WORKSPACE_FILE_LIMIT = 128
COMPILER_WORKSPACE_SOURCE_LIMIT = 131_072
COMPILER_RUN_TIME_LIMIT = 2.0
COMPILER_RUN_TRUNCATION_MARKER = "\n[output truncated]"
GRAPHICS_FRAME_INTERVAL = 1.0 / 60.0
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
		audio_handler: Callable[[AudioState], None] | None = None,
	) -> None:
		self.input = InputDevice(width, height)
		self.graphics = GraphicsDevice(width, height, frame_handler)
		self.os = os_device or OSDevice()
		self.files = FileSystemDevice(filesystem_root)
		self.images = ImageAssetStore(self.files)
		self.audio = AudioDevice(
			self.files,
			audio_handler,
			lambda: self.os.volume * self.os.music_volume // 100,
		)
		self.currency = CurrencyDevice()
		self.compiler = CompilerDevice()
		self.windows = WindowManager(self.graphics, self.input, appearance=self.os)
		self._rng = random.Random()
		self._raw_slider_capture: tuple[int, int, int] | None = None
		self._frame_window_pointer: int | None = None
		self._frame_window_handle = 0
		self._frame_is_screen = False
		self._next_frame_at = 0.0
		self._graphics_backdrop: GraphicsBufferSnapshot | None = None
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
			SyscallID.APP_OS_OPEN_APPEND: self._os_open_append,
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
			SyscallID.APP_OS_PREVIEW_PREFERENCES: self._os_preview_preferences,
			SyscallID.APP_OS_CLEAR_PREVIEW: self._os_clear_preview,
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
			SyscallID.APP_OS_IS_DIRECTORY: self._os_is_directory,
			SyscallID.APP_OS_COPY: self._os_copy,
			SyscallID.APP_OS_FILE_SIZE: self._os_file_size,
			SyscallID.APP_OS_MODIFIED_TICKS: self._os_modified_ticks,
			SyscallID.APP_OS_REVISION: self._os_revision,
			SyscallID.APP_OS_NORMALIZE_PATH: self._os_normalize_path,
			SyscallID.APP_OS_CLIPBOARD_READ: self._os_clipboard_read,
			SyscallID.APP_OS_CLIPBOARD_WRITE: self._os_clipboard_write,
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
			SyscallID.APP_COMPILER_CHECK_WORKSPACE: self._compiler_check_workspace,
			SyscallID.APP_COMPILER_RUN_WORKSPACE: self._compiler_run_workspace,
			SyscallID.APP_GRAPHICS_LOAD_IMAGE: self._graphics_load_image,
			SyscallID.APP_GRAPHICS_IMAGE_WIDTH: self._graphics_image_width,
			SyscallID.APP_GRAPHICS_IMAGE_HEIGHT: self._graphics_image_height,
			SyscallID.APP_GRAPHICS_IMAGE_FRAME_COUNT: self._graphics_image_frame_count,
			SyscallID.APP_GRAPHICS_IMAGE_FRAME_DURATION: self._graphics_image_frame_duration,
			SyscallID.APP_GRAPHICS_DRAW_IMAGE: self._graphics_draw_image,
			SyscallID.APP_GRAPHICS_DRAW_COMMANDS: self._graphics_draw_commands,
			SyscallID.APP_AUDIO_LOAD_TRACK: self._audio_load_track,
			SyscallID.APP_AUDIO_PLAY: self._audio_play,
			SyscallID.APP_AUDIO_PAUSE: self._audio_pause,
			SyscallID.APP_AUDIO_STOP: self._audio_stop,
			SyscallID.APP_AUDIO_SEEK: self._audio_seek,
			SyscallID.APP_AUDIO_POSITION: self._audio_position,
			SyscallID.APP_AUDIO_DURATION: self._audio_duration,
			SyscallID.APP_AUDIO_IS_PLAYING: self._audio_is_playing,
			SyscallID.APP_AUDIO_UPDATE: self._audio_update,
			SyscallID.APP_AUDIO_ACTIVE_PITCH: self._audio_active_pitch,
		}

	def set_frame_handler(self, handler: Callable[[FrameSnapshot], None] | None) -> None:
		self.graphics.set_frame_handler(handler)

	def set_graphics_backdrop(self, snapshot: GraphicsBufferSnapshot | None) -> None:
		self._graphics_backdrop = snapshot

	def _draw_graphics_background(self) -> None:
		if self._graphics_backdrop is None:
			self.os.draw_background(self.graphics)
		else:
			self.graphics.restore_backdrop(self._graphics_backdrop)

	def dispatch(self, syscall_id: int, vm: Any, result: Any) -> bool:
		handler = self._handlers.get(syscall_id)
		if handler is None:
			return False
		try:
			handler(vm, result)
		except Exception as error:
			self._fail(
				result,
				f"Syscall {int(syscall_id)} failed safely: {type(error).__name__}: {error}",
			)
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
		self._pace_frame(vm)
		self.graphics.present(self.os.palette)

	def _raw_flip(self, vm: Any, result: Any) -> None:
		self._pace_frame(vm)
		self.graphics.present(self.os.palette)
		self.graphics.clear(0)

	def _raw_append(self, vm: Any, result: Any) -> None:
		self._pace_frame(vm)
		self.graphics.append(self.os.palette)

	def _raw_dump(self, vm: Any, result: Any) -> None:
		self._pace_frame(vm)
		self.graphics.append(self.os.palette)
		self.graphics.clear(0)

	def _pace_frame(self, vm: Any) -> None:
		now = time.perf_counter()
		if self._next_frame_at <= 0.0 or now - self._next_frame_at > GRAPHICS_FRAME_INTERVAL * 4:
			self._next_frame_at = now
		delay = self._next_frame_at - now
		if delay > 0.0:
			cancel_event = getattr(vm, "cancel_event", None)
			if cancel_event is not None:
				cancel_event.wait(delay)
			else:
				time.sleep(delay)
		now = time.perf_counter()
		self._next_frame_at = max(self._next_frame_at + GRAPHICS_FRAME_INTERVAL, now)

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
		if not math.isfinite(scale_value) or scale_value <= 0 or scale_value > 4096.0:
			self._fail(result, "Invalid image scale")
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
		vm.push(self.graphics.get_chr_width(char & 0xFF))

	def _raw_width_small(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is None:
			return
		char = args[0]
		vm.push(self.graphics.get_chr_width_small(char & 0xFF))

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
		self._draw_graphics_background()
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
			self._pace_frame(vm)
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
			_, handle, values = entry
			color = _signed(values[0])
			if handle == SCREEN_TARGET_HANDLE:
				self.graphics.clear(color)
			elif handle:
				self.windows.clear_content(handle, color)

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

	def _os_open_append(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.files.open_append(self._read_string(vm, args[0])))

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

	def _os_is_directory(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.files.is_directory(self._read_string(vm, args[0])))

	def _os_copy(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			self._push_bool(vm, self.files.copy(self._read_string(vm, args[0]), self._read_string(vm, args[1])))

	def _os_file_size(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			entry = self.files.stat(self._read_string(vm, args[0]))
			vm.push((entry.size if entry is not None else -1) & TRUE)

	def _os_modified_ticks(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			entry = self.files.stat(self._read_string(vm, args[0]))
			vm.push(((entry.modified_ns // 1_000_000) if entry is not None else -1) & TRUE)

	def _os_revision(self, vm: Any, result: Any) -> None:
		vm.push(self.files.revision & TRUE)

	def _os_normalize_path(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_string(vm, result, self.files.normalize(self._read_string(vm, args[0])))

	def _os_clipboard_read(self, vm: Any, result: Any) -> None:
		self._push_string(vm, result, self.os.clipboard_read())

	def _os_clipboard_write(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.os.clipboard_write(self._read_string(vm, args[0])))

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

	def _os_preview_preferences(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 4)
		if args is not None:
			values = [_signed(value) for value in args]
			self._push_bool(vm, self.os.preview_preferences(*values))

	def _os_clear_preview(self, vm: Any, result: Any) -> None:
		self.os.clear_preview()

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

	def _compiler_workspace_sources(self, entry_path: str) -> tuple[dict[str, str], str]:
		entry = self.files.normalize(entry_path)
		if not entry or entry == "." or not entry.lower().endswith(".xe"):
			return {}, "Workspace entry must be a portable .xe file path"
		workspace_root = entry.rsplit("/", 1)[0] if "/" in entry else "."
		workspace_prefix = "" if workspace_root == "." else workspace_root + "/"
		sources: dict[str, str] = {}
		pending = [workspace_root]
		visited: set[str] = set()
		while pending:
			folder = pending.pop()
			if folder in visited:
				continue
			visited.add(folder)
			for item in self.files.entries(folder):
				path = item.name if folder == "." else f"{folder}/{item.name}"
				normalized = self.files.normalize(path)
				if not normalized:
					continue
				if item.is_directory:
					pending.append(normalized)
				elif normalized.lower().endswith(".xe"):
					if len(sources) >= COMPILER_WORKSPACE_FILE_LIMIT:
						return {}, f"Workspace exceeds {COMPILER_WORKSPACE_FILE_LIMIT} Xe files"
					text = self.files.read_text(normalized)
					if text is not None:
						sources[normalized] = text
		for document in self.compiler.documents:
			if not document.name:
				continue
			normalized = self.files.normalize(document.name)
			inside_workspace = bool(normalized) and (
				workspace_root == "." or normalized.startswith(workspace_prefix)
			)
			if inside_workspace and normalized.lower().endswith(".xe"):
				sources[normalized] = document.source
		if len(sources) > COMPILER_WORKSPACE_FILE_LIMIT:
			return {}, f"Workspace exceeds {COMPILER_WORKSPACE_FILE_LIMIT} Xe files"
		if sum(len(source) for source in sources.values()) > COMPILER_WORKSPACE_SOURCE_LIMIT:
			return {}, f"Workspace exceeds {COMPILER_WORKSPACE_SOURCE_LIMIT} source characters"
		if entry not in sources:
			return {}, f"Workspace entry file not found: {entry}"
		return sources, ""

	def _compiler_check_workspace(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is None:
			return
		entry = self._read_string(vm, args[0])
		sources, error = self._compiler_workspace_sources(entry)
		if error:
			self.compiler.set_runtime_error(error)
			self._push_bool(vm, False)
			return
		self._push_bool(vm, self.compiler.compile_workspace(sources, self.files.normalize(entry)))

	def _compiler_run(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is None:
			return
		source = self._read_string(vm, args[0])
		self._compiler_execute(vm, result, lambda: self.compiler.compile(source), len(source), COMPILER_RUN_SOURCE_LIMIT)

	def _compiler_run_workspace(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is None:
			return
		entry = self._read_string(vm, args[0])
		sources, error = self._compiler_workspace_sources(entry)
		if error:
			self.compiler.set_runtime_error(error)
			self._push_string(vm, result, error)
			return
		normalized_entry = self.files.normalize(entry)
		self._compiler_execute(
			vm,
			result,
			lambda: self.compiler.compile_workspace(sources, normalized_entry),
			sum(len(source) for source in sources.values()),
			COMPILER_WORKSPACE_SOURCE_LIMIT,
		)

	def _compiler_execute(
		self,
		vm: Any,
		result: Any,
		compile_action: Callable[[], bool],
		source_size: int,
		source_limit: int,
	) -> None:

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
		if source_size > source_limit:
			message = f"Compile error: source exceeds {source_limit} characters"
			self.compiler.set_runtime_error(message)
			self._push_string(vm, result, message)
			return
		if vm.cancel_event.is_set():
			message = "Runtime canceled."
			self.compiler.set_runtime_error(message)
			self._push_string(vm, result, message)
			return
		if not compile_action():
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

		parent_buffers: GraphicsBufferSnapshot | None = None
		try:
			from xe_lang.vm import VM

			_compiler_run_state.depth = depth + 1
			interactive_graphics = any(
				capability in {"core.graphics", "app.graphics", "app.window"}
				for capability in self.compiler.required_capabilities
			)
			interactive_audio = "app.audio" in self.compiler.required_capabilities
			if interactive_graphics:
				parent_buffers = self.graphics.capture_buffers()
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
			if interactive_graphics:
				# Child windows share host presentation and input, but their manager owns
				# only child handles. A fixed parent backdrop keeps the invoking app
				# visible while its synchronous child is running.
				child.devices.graphics = self.graphics
				child.devices.input = self.input
				child.devices.set_graphics_backdrop(parent_buffers)
				child.devices.windows = WindowManager(
					self.graphics,
					self.input,
					appearance=self.os,
				)
				child.devices.images = self.images
			if interactive_audio:
				child.devices.audio = self.audio
			run_result = child.run(
				instruction_limit=None if interactive_graphics else COMPILER_RUN_INSTRUCTION_LIMIT,
				wall_time_limit=None if interactive_graphics else COMPILER_RUN_TIME_LIMIT,
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
			if parent_buffers is not None:
				self.graphics.restore_buffers(parent_buffers)
				self.graphics.reset_clip()
				self.graphics.publish(self.os.palette)

		text = "".join(output)
		if truncated:
			text += COMPILER_RUN_TRUNCATION_MARKER
		if not text:
			text = "Program completed."
		self._push_string(vm, result, text)

	def _graphics_load_image(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.images.load(self._read_string(vm, args[0])))

	def _graphics_image_width(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			asset = self.images.get(args[0])
			vm.push(asset.image.width if asset else 0)

	def _graphics_image_height(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			asset = self.images.get(args[0])
			vm.push(asset.image.height if asset else 0)

	def _graphics_image_frame_count(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			asset = self.images.get(args[0])
			vm.push(len(asset.image.frames) if asset else 0)

	def _graphics_image_frame_duration(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			asset = self.images.get(args[0])
			index = _signed(args[1])
			vm.push(asset.image.frames[index].duration_ms if asset and 0 <= index < len(asset.image.frames) else 0)

	def _graphics_draw_image(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 6)
		if not entry or not entry[1]:
			return
		_, handle, args = entry
		image_handle, x, y, frame_index, image_scale = args
		asset = self.images.get(image_handle)
		frame_index = _signed(frame_index)
		if asset is None or not 0 <= frame_index < len(asset.image.frames):
			return
		origin_x, origin_y = self._origin(handle)
		target_scale = self._target_scale(handle)
		scale = max(1, min(64, _signed(image_scale))) * target_scale
		left = origin_x + _signed(x) * target_scale
		top = origin_y + _signed(y) * target_scale
		self.graphics.draw_indexed_pixels(
			left,
			top,
			asset.image.width,
			asset.image.height,
			asset.image.frames[frame_index].pixels,
			scale,
			16,
		)

	def _graphics_draw_commands(self, vm: Any, result: Any) -> None:
		entry = self._window_args(vm, result, 11)
		if not entry or not entry[1]:
			vm.push((-1) & TRUE)
			return
		_, handle, args = entry
		(
			stream_address,
			word_count,
			names_address,
			short_names_address,
			selected_address,
			out_x,
			out_y,
			out_depth,
			out_radius,
			depth_order_address,
		) = args
		word_count = _signed(word_count)
		if word_count < gc.HEADER_WORDS or word_count > gc.MAX_STREAM_WORDS or not self._valid_span(vm, stream_address, word_count):
			vm.push((-2) & TRUE)
			return
		memory = vm.data_memory
		if (
			memory[stream_address + gc.HEADER_MAGIC_OFFSET] != gc.MAGIC
			or memory[stream_address + gc.HEADER_VERSION_OFFSET] != gc.VERSION
		):
			vm.push((-3) & TRUE)
			return
		if memory[stream_address + gc.HEADER_TOTAL_WORDS_OFFSET] != word_count:
			vm.push((-4) & TRUE)
			return
		command_count = _signed(memory[stream_address + gc.HEADER_COMMAND_COUNT_OFFSET])
		command_offset = _signed(memory[stream_address + gc.HEADER_FIRST_COMMAND_OFFSET])
		if (
			command_count != 1
			or command_offset != gc.HEADER_WORDS
			or any(
				memory[stream_address + gc.HEADER_RESERVED_OFFSET + offset] != 0
				for offset in range(gc.HEADER_RESERVED_WORDS)
			)
		):
			vm.push((-4) & TRUE)
			return
		command = stream_address + command_offset
		if command_offset + 2 > word_count:
			vm.push((-4) & TRUE)
			return
		opcode = _signed(memory[command + gc.ORBIT_OPCODE_OFFSET])
		command_words = _signed(memory[command + gc.ORBIT_WORDS_OFFSET])
		if opcode != gc.ORBIT_SCENE:
			vm.push((-5) & TRUE)
			return
		if command_words != gc.ORBIT_WORDS or command_offset + command_words > word_count:
			vm.push((-4) & TRUE)
			return
		packed_hover = self._draw_orbit_scene_command(
			vm,
			handle,
			stream_address,
			word_count,
			command,
			names_address,
			short_names_address,
			selected_address,
			out_x,
			out_y,
			out_depth,
			out_radius,
			depth_order_address,
		)
		vm.push(packed_hover & TRUE)

	def _draw_orbit_scene_command(
		self,
		vm: Any,
		handle: int,
		stream_address: int,
		word_count: int,
		command: int,
		names_address: int,
		short_names_address: int,
		selected_address: int,
		out_x: int,
		out_y: int,
		out_depth: int,
		out_radius: int,
		depth_order_address: int,
	) -> int:
		memory = vm.data_memory
		if memory[command + gc.ORBIT_WORDS_OFFSET] != gc.ORBIT_WORDS:
			return -6
		entry_count = _signed(memory[command + gc.ORBIT_ENTRY_COUNT_OFFSET])
		shell_count = _signed(memory[command + gc.ORBIT_SHELL_COUNT_OFFSET])
		if not 0 <= entry_count <= gc.MAX_ORBIT_ENTRIES or not 1 <= shell_count <= gc.MAX_ORBIT_SHELLS:
			return -6
		scene_x = _signed(memory[command + gc.ORBIT_SCENE_X_OFFSET])
		scene_y = _signed(memory[command + gc.ORBIT_SCENE_Y_OFFSET])
		center_x = _signed(memory[command + gc.ORBIT_CENTER_X_OFFSET])
		center_y = _signed(memory[command + gc.ORBIT_CENTER_Y_OFFSET])
		area_width = _signed(memory[command + gc.ORBIT_AREA_WIDTH_OFFSET])
		area_height = _signed(memory[command + gc.ORBIT_AREA_HEIGHT_OFFSET])
		sidebar_width = _signed(memory[command + gc.ORBIT_SIDEBAR_WIDTH_OFFSET])
		render_scale = _signed(memory[command + gc.ORBIT_RENDER_SCALE_OFFSET])
		outer_radius = _signed(memory[command + gc.ORBIT_OUTER_RADIUS_OFFSET])
		shell_gap = _signed(memory[command + gc.ORBIT_SHELL_GAP_OFFSET])
		center_radius = _signed(memory[command + gc.ORBIT_CENTER_RADIUS_OFFSET])
		node_radius = _signed(memory[command + gc.ORBIT_NODE_RADIUS_OFFSET])
		tilt = _signed(memory[command + gc.ORBIT_TILT_OFFSET])
		roll = _signed(memory[command + gc.ORBIT_ROLL_OFFSET])
		rotation = _signed(memory[command + gc.ORBIT_ROTATION_OFFSET])
		surface = _signed(memory[command + gc.ORBIT_SURFACE_COLOR_OFFSET])
		outline = _signed(memory[command + gc.ORBIT_OUTLINE_COLOR_OFFSET])
		accent = _signed(memory[command + gc.ORBIT_ACCENT_COLOR_OFFSET])
		shell_color = _signed(memory[command + gc.ORBIT_SHELL_COLOR_OFFSET])
		highlight = _signed(memory[command + gc.ORBIT_HIGHLIGHT_COLOR_OFFSET])
		pointer_x = _signed(memory[command + gc.ORBIT_POINTER_X_OFFSET])
		pointer_y = _signed(memory[command + gc.ORBIT_POINTER_Y_OFFSET])
		shell_button_word = memory[command + gc.ORBIT_SHELL_BUTTON_HOVERED_OFFSET]
		zoom_controls_word = memory[command + gc.ORBIT_ZOOM_CONTROLS_HOVERED_OFFSET]
		shell_button_hovered = bool(shell_button_word)
		zoom_controls_hovered = bool(zoom_controls_word)
		camera_zoom = _signed(memory[command + gc.ORBIT_CAMERA_ZOOM_OFFSET])
		label_char_limit = _signed(memory[command + gc.ORBIT_LABEL_CHAR_LIMIT_OFFSET])
		item_offset = _signed(memory[command + gc.ORBIT_ITEM_TABLE_OFFSET])
		item_stride = _signed(memory[command + gc.ORBIT_ITEM_STRIDE_OFFSET])
		shell_offset = _signed(memory[command + gc.ORBIT_SHELL_TABLE_OFFSET])
		shell_stride = _signed(memory[command + gc.ORBIT_SHELL_STRIDE_OFFSET])
		flags = memory[command + gc.ORBIT_FLAGS_OFFSET]
		shell_points = _signed(memory[command + gc.ORBIT_SHELL_POINTS_OFFSET])
		expected_shell_offset = gc.HEADER_WORDS + gc.ORBIT_WORDS
		expected_item_offset = expected_shell_offset + shell_count * gc.ORBIT_SHELL_WORDS
		expected_word_count = expected_item_offset + entry_count * gc.ORBIT_ITEM_WORDS
		expected_shell_gap = 0 if shell_count == 1 else (outer_radius - 30) // (shell_count - 1)
		if (
			not 1 <= area_width <= 4096 or not 1 <= area_height <= 4096
			or not 0 <= sidebar_width < area_width
			or not 25 <= render_scale <= 500
			or not 30 <= outer_radius <= 2048
			or shell_gap != expected_shell_gap
			or not 1 <= center_radius <= 128 or not 1 <= node_radius <= 32
			or any(not 0 <= color < 16 for color in (surface, outline, accent, shell_color, highlight))
			or shell_button_word not in (0, 1) or zoom_controls_word not in (0, 1)
			or not 25 <= camera_zoom <= 500 or not 1 <= label_char_limit <= 1024
			or flags & ~gc.ORBIT_FLAG_DRAW_LABELS or not 4 <= shell_points <= 64
			or item_stride != gc.ORBIT_ITEM_WORDS or shell_stride != gc.ORBIT_SHELL_WORDS
			or shell_offset != expected_shell_offset or item_offset != expected_item_offset
			or word_count != expected_word_count
			or not self._valid_span(vm, names_address, entry_count)
			or not self._valid_span(vm, short_names_address, entry_count)
			or not self._valid_span(vm, selected_address, entry_count)
			or not self._valid_span(vm, out_x, entry_count)
			or not self._valid_span(vm, out_y, entry_count)
			or not self._valid_span(vm, out_depth, entry_count)
			or not self._valid_span(vm, out_radius, entry_count)
			or not self._valid_span(vm, depth_order_address, entry_count)
		):
			return -6

		def overlaps(first: int, first_words: int, second: int, second_words: int) -> bool:
			return first_words > 0 and second_words > 0 and first < second + second_words and second < first + first_words

		for input_address in (names_address, short_names_address, selected_address):
			if overlaps(input_address, entry_count, stream_address, word_count):
				return -6
		if (
			overlaps(selected_address, entry_count, names_address, entry_count)
			or overlaps(selected_address, entry_count, short_names_address, entry_count)
		):
			return -6

		mutable_spans = (out_x, out_y, out_depth, out_radius, depth_order_address)
		for index, address in enumerate(mutable_spans):
			if overlaps(address, entry_count, stream_address, word_count):
				return -6
			if any(
				overlaps(address, entry_count, input_address, entry_count)
				for input_address in (names_address, short_names_address, selected_address)
			):
				return -6
			if any(overlaps(address, entry_count, other, entry_count) for other in mutable_spans[index + 1:]):
				return -6

		# Validate every table record and every three-word string descriptor before
		# either the framebuffer or caller-owned output arrays are changed.
		shell_phases: list[int] = []
		shell_populations: list[int] = []
		for shell in range(shell_count):
			record = stream_address + shell_offset + shell * shell_stride
			phase = _signed(memory[record + gc.ORBIT_SHELL_PHASE_OFFSET])
			population = _signed(memory[record + gc.ORBIT_SHELL_POPULATION_OFFSET])
			if not 0 <= population <= gc.MAX_ORBIT_SHELL_POPULATION:
				return -6
			shell_phases.append(phase)
			shell_populations.append(population)
		if sum(shell_populations) != entry_count:
			return -6

		raw_entries: list[tuple[int, int, int, int, str, str, bool]] = []
		seen_positions: list[set[int]] = [set() for _ in range(shell_count)]
		for index in range(entry_count):
			record = stream_address + item_offset + index * item_stride
			shell = _signed(memory[record + gc.ORBIT_ITEM_SHELL_OFFSET])
			position = _signed(memory[record + gc.ORBIT_ITEM_POSITION_OFFSET])
			is_directory = _signed(memory[record + gc.ORBIT_ITEM_DIRECTORY_OFFSET])
			child_count = _signed(memory[record + gc.ORBIT_ITEM_CHILD_COUNT_OFFSET])
			name_index = _signed(memory[record + gc.ORBIT_ITEM_NAME_INDEX_OFFSET])
			short_name_index = _signed(memory[record + gc.ORBIT_ITEM_SHORT_NAME_INDEX_OFFSET])
			if (
				not 0 <= shell < shell_count
				or not 0 <= position < shell_populations[shell]
				or position in seen_positions[shell]
				or is_directory not in (0, 1)
				or not 0 <= child_count <= 64
				or not 0 <= name_index < entry_count
				or not 0 <= short_name_index < entry_count
			):
				return -6
			try:
				full_name = vm.read_string_descriptor(memory[names_address + name_index])
				short_name = vm.read_string_descriptor(memory[short_names_address + short_name_index])
			except ValueError:
				return -6
			if len(full_name) > 1024 or len(short_name) > 64:
				return -6
			seen_positions[shell].add(position)
			raw_entries.append(
				(shell, position, is_directory, child_count, full_name, short_name, bool(memory[selected_address + index]))
			)

		def f32(value: float) -> float:
			return _float(_float_bits(value))

		def addf(left: float, right: float) -> float:
			return f32(f32(left) + f32(right))

		def subf(left: float, right: float) -> float:
			return f32(f32(left) - f32(right))

		def mulf(left: float, right: float) -> float:
			return f32(f32(left) * f32(right))

		def cos_degrees(value: float) -> float:
			return f32(math.cos(math.radians(f32(value))))

		def sin_degrees(value: float) -> float:
			return f32(math.sin(math.radians(f32(value))))

		def trunc_div(numerator: int, denominator: int) -> int:
			if numerator < 0:
				return -((-numerator) // denominator)
			return numerator // denominator

		tilt_cos = cos_degrees(float(tilt))
		depth_scale = abs(sin_degrees(float(tilt)))
		roll_cos = cos_degrees(float(roll))
		roll_sin = sin_degrees(float(roll))
		rotation_cos = cos_degrees(float(rotation))
		rotation_sin = sin_degrees(float(rotation))

		def project(radius: int, cosine: float, sine: float) -> tuple[int, int, int]:
			float_radius = f32(float(radius))
			plane_x = mulf(cosine, float_radius)
			plane_y = mulf(mulf(sine, float_radius), tilt_cos)
			x = int(subf(mulf(plane_x, roll_cos), mulf(plane_y, roll_sin)))
			y = int(addf(mulf(plane_x, roll_sin), mulf(plane_y, roll_cos)))
			depth = int(mulf(mulf(mulf(sine, float_radius), depth_scale), 100.0))
			return center_x + x, center_y + y, depth

		slot_components: list[list[tuple[float, float]]] = []
		for shell in range(shell_count):
			population = shell_populations[shell]
			divisor = max(1, population)
			base_angle = f32(float(rotation + shell_phases[shell] + shell * 17))
			cosine = cos_degrees(base_angle)
			sine = sin_degrees(base_angle)
			step_angle = f32(f32(360.0) / f32(float(divisor)))
			step_cosine = cos_degrees(step_angle)
			step_sine = sin_degrees(step_angle)
			slots: list[tuple[float, float]] = []
			for _ in range(population):
				slots.append((cosine, sine))
				next_cosine = subf(mulf(cosine, step_cosine), mulf(sine, step_sine))
				sine = addf(mulf(sine, step_cosine), mulf(cosine, step_sine))
				cosine = next_cosine
			slot_components.append(slots)

		entries: list[tuple[int, int, int, int, int, int, int, str, str, bool]] = []
		for index, (shell, position, is_directory, child_count, full_name, short_name, selected) in enumerate(raw_entries):
			radius = (30 + shell * shell_gap) * render_scale // 100
			cosine, sine = slot_components[shell][position]
			x, y, depth = project(radius, cosine, sine)
			entries.append((depth, index, x, y, shell, is_directory, child_count, full_name, short_name, selected))

		previous_order = [_signed(memory[depth_order_address + index]) for index in range(entry_count)]
		if sorted(previous_order) != list(range(entry_count)):
			previous_order = list(range(entry_count))
		previous_rank = {entry_index: rank for rank, entry_index in enumerate(previous_order)}
		ordered = sorted(entries, key=lambda item: (item[0], previous_rank[item[1]]))

		# All validation and projection have completed. Output mutation starts here.
		for depth, index, x, y, *_ in entries:
			memory[out_x + index] = x & TRUE
			memory[out_y + index] = y & TRUE
			memory[out_depth + index] = depth & TRUE
			memory[out_radius + index] = node_radius & TRUE
		for position, entry_data in enumerate(ordered):
			memory[depth_order_address + position] = entry_data[1] & TRUE

		hovered_entry = -1
		hovered_shell = -1
		if (
			pointer_x > scene_x + sidebar_width
			and pointer_x < scene_x + area_width
			and scene_y <= pointer_y < scene_y + area_height
			and not shell_button_hovered
			and not zoom_controls_hovered
		):
			outside_nucleus = (pointer_x - center_x) ** 2 + (pointer_y - center_y) ** 2 > center_radius ** 2
			for depth, index, x, y, shell, *_ in entries:
				dx = pointer_x - x
				dy = pointer_y - y
				hit_radius = node_radius + 10
				if dx * dx + dy * dy < hit_radius * hit_radius and (depth >= 0 or outside_nucleus):
					hovered_entry = index
					hovered_shell = shell

		origin_x, origin_y = self._origin(handle)
		target_scale = self._target_scale(handle)

		def pixel(x: int, y: int, color: int) -> None:
			self.graphics.fill_rect(
				origin_x + x * target_scale,
				origin_y + y * target_scale,
				target_scale,
				target_scale,
				color,
			)

		def circle(x: int, y: int, radius: int, color: int) -> None:
			self.graphics.draw_circle_scaled(origin_x, origin_y, x, y, radius, color, target_scale)

		def fill_rect(x: int, y: int, width: int, height: int, color: int) -> None:
			self.graphics.fill_rect(
				origin_x + x * target_scale,
				origin_y + y * target_scale,
				width * target_scale,
				height * target_scale,
				color,
			)

		def draw_file(x: int, y: int, radius: int) -> None:
			fill_rect(x - radius, y - radius - 1, radius * 2 + 1, radius * 2 + 3, surface)
			self.graphics.draw_rect_scaled(
				origin_x, origin_y, x - radius, y - radius - 1,
				radius * 2 + 1, radius * 2 + 3, outline, target_scale,
			)
			pixel(x + radius, y - radius - 1, 0)
			pixel(x + radius - 1, y - radius - 1, accent)
			pixel(x + radius, y - radius, accent)

		def draw_folder(x: int, y: int, radius: int, child_count: int, phase: int) -> None:
			inner_half = max(1, radius * 7 // 10)
			fill_rect(x - inner_half, y - inner_half, inner_half * 2 + 1, inner_half * 2 + 1, surface)
			circle(x, y, radius, outline)
			pixel(x - radius // 2, y - radius // 2, highlight)
			circle(x, y, radius - 2, accent)
			dot_count = max(0, min(8, child_count))
			dot_radius = radius + 3
			if dot_count > 0:
				cosine = cos_degrees(float(phase))
				sine = sin_degrees(float(phase))
				step_angle = f32(f32(360.0) / f32(float(dot_count)))
				step_cosine = cos_degrees(step_angle)
				step_sine = sin_degrees(step_angle)
				for _ in range(dot_count):
					pixel(x + int(mulf(cosine, float(dot_radius))), y + int(mulf(sine, float(dot_radius))), accent)
					next_cosine = subf(mulf(cosine, step_cosine), mulf(sine, step_sine))
					sine = addf(mulf(sine, step_cosine), mulf(cosine, step_sine))
					cosine = next_cosine

		# Twenty samples preserve the original orbit-ring density while one syscall
		# keeps the per-point loop out of Xe bytecode.
		step_angle = f32(f32(360.0) / f32(float(shell_points)))
		step_cos = cos_degrees(step_angle)
		step_sin = sin_degrees(step_angle)
		for shell in range(shell_count):
			radius = (30 + shell * shell_gap) * render_scale // 100
			cosine = rotation_cos
			sine = rotation_sin
			for _ in range(shell_points):
				x, y, _ = project(radius, cosine, sine)
				pixel(x, y, shell_color)
				next_cosine = subf(mulf(cosine, step_cos), mulf(sine, step_sin))
				sine = addf(mulf(sine, step_cos), mulf(cosine, step_sin))
				cosine = next_cosine

		def draw_entry(entry: tuple[int, int, int, int, int, int, int, str, str, bool]) -> None:
			depth, index, x, y, shell, is_directory, child_count, full_name, short_name, selected = entry
			if is_directory:
				draw_folder(x, y, node_radius + 1, child_count, rotation + shell_phases[shell] + index * 31)
			else:
				draw_file(x, y, node_radius)
			if selected:
				circle(x, y, node_radius + 4, accent)
			if hovered_entry == index:
				pixel(x - 2, y + node_radius + 5, highlight)
				pixel(x, y + node_radius + 5, highlight)
				pixel(x + 2, y + node_radius + 5, highlight)

			if not flags & gc.ORBIT_FLAG_DRAW_LABELS:
				return
			show_full = camera_zoom >= 125 or selected or hovered_entry == index
			label = full_name if show_full else short_name
			color = (highlight if selected or hovered_entry == index else outline) if show_full else shell_color
			if show_full and len(label) > label_char_limit:
				label = label[:label_char_limit]
			name_width = sum(self.graphics.text_advance(char, 1, small=True) for char in label)
			if name_width > 0:
				name_width -= 1
			name_width = max(1, name_width)
			dx = x - center_x
			dy = y - center_y
			abs_x = abs(dx)
			abs_y = abs(dy)
			norm = (abs_x + abs_y * 3 // 8) if abs_x > abs_y else (abs_y + abs_x * 3 // 8)
			if norm < 1:
				dx, dy, norm = 0, -1, 1
			offset = node_radius + 4 + (abs_x * name_width // 2 + abs_y * 3) // norm
			label_center_x = x + trunc_div(dx * offset, norm)
			label_center_y = y + trunc_div(dy * offset, norm)
			self.graphics.draw_text_small(
				origin_x + (label_center_x - name_width // 2) * target_scale,
				origin_y + (label_center_y - 2) * target_scale,
				label,
				color,
				pixel_scale=target_scale,
			)

		for entry in ordered:
			if entry[0] < 0:
				draw_entry(entry)
		self.graphics.fill_circle_scaled(origin_x, origin_y, center_x, center_y, center_radius, surface, target_scale)
		circle(center_x, center_y, center_radius, outline)
		circle(center_x, center_y, center_radius - 2, accent)
		pixel(center_x - center_radius // 2, center_y - center_radius // 2, highlight)
		for entry in ordered:
			if entry[0] >= 0:
				draw_entry(entry)

		return (hovered_entry + 1) | ((hovered_shell + 1) << 8)

	def _audio_load_track(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			vm.push(self.audio.load(self._read_string(vm, args[0])))

	def _audio_play(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.audio.play(args[0]))

	def _audio_pause(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.audio.pause(args[0]))

	def _audio_stop(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			self._push_bool(vm, self.audio.stop(args[0]))

	def _audio_seek(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			self._push_bool(vm, self.audio.seek(args[0], _signed(args[1])))

	def _audio_position(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			asset = self.audio.get(args[0])
			vm.push(int(asset.sequencer.position_ticks) if asset else 0)

	def _audio_duration(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			asset = self.audio.get(args[0])
			vm.push(asset.sequencer.duration_ticks if asset else 0)

	def _audio_is_playing(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			asset = self.audio.get(args[0])
			self._push_bool(vm, bool(asset and asset.sequencer.playing))

	def _audio_update(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 2)
		if args is not None:
			self.audio.update(args[0], _signed(args[1]))

	def _audio_active_pitch(self, vm: Any, result: Any) -> None:
		args = self._args(vm, result, 1)
		if args is not None:
			asset = self.audio.get(args[0])
			active = asset.sequencer.active_notes() if asset else ()
			vm.push((active[0].pitch if active else -1) & TRUE)

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
