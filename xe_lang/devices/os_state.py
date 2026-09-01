from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Callable

from xe_lang.design_tokens import (
	BACKGROUND_COLOR_INDICES,
	BACKGROUND_NAMES,
	PALETTES,
	THEME_DARK,
	THEME_LIGHT,
	normalize_theme_palette,
	normalize_window_corner_style,
)
from .graphics import GraphicsDevice


BACKGROUND_COLORS = BACKGROUND_COLOR_INDICES
SETTINGS_SCHEMA_VERSION = 1
CLIPBOARD_TEXT_LIMIT = 32_768
ANTI_ALIASING_OFF = 0
ANTI_ALIASING_FAST = 1
ANTI_ALIASING_QUALITY = 2
ANTI_ALIASING_MODES = (
	ANTI_ALIASING_OFF,
	ANTI_ALIASING_FAST,
	ANTI_ALIASING_QUALITY,
)


def default_settings_path() -> Path:
	base = os.environ.get("LOCALAPPDATA")
	if base:
		return (Path(base) / "XenonOS" / "settings.json").resolve()
	return (Path.home() / ".xenonos" / "settings.json").resolve()


@dataclass(frozen=True)
class OSSettings:
	volume: int = 70
	background_id: int = 0
	palette_id: int = 0
	music_volume: int = 70
	sound_effect_volume: int = 70
	theme_mode: int = 0
	window_transparency: int = 0
	window_corner_style: int = 0
	icon_size: int = 1
	clock_format: int = 1
	settings_enabled: bool = True
	motion_blur_enabled: bool = True
	anti_aliasing_mode: int = ANTI_ALIASING_QUALITY


@dataclass(frozen=True)
class AppearancePreview:
	background_id: int
	palette_id: int
	theme_mode: int
	window_corner_style: int


class OSDevice:
	def __init__(
		self,
		settings: OSSettings | None = None,
		now_provider: Callable[[], datetime] | None = None,
		settings_path: str | Path | None = None,
		clipboard_reader: Callable[[], str] | None = None,
		clipboard_writer: Callable[[str], bool] | None = None,
	) -> None:
		self._lock = RLock()
		self._settings_path = Path(settings_path).resolve() if settings_path is not None else None
		self._settings = self._normalize_settings(settings or self._load_settings() or OSSettings())
		self._preview: AppearancePreview | None = None
		self._now_provider = now_provider or datetime.now
		self._clipboard_reader = clipboard_reader
		self._clipboard_writer = clipboard_writer

	def set_clipboard_handlers(
		self,
		reader: Callable[[], str] | None,
		writer: Callable[[str], bool] | None,
	) -> None:
		with self._lock:
			self._clipboard_reader = reader
			self._clipboard_writer = writer

	def clipboard_read(self) -> str:
		with self._lock:
			reader = self._clipboard_reader
		if reader is None:
			return ""
		try:
			return str(reader())[:CLIPBOARD_TEXT_LIMIT]
		except Exception:
			return ""

	def clipboard_write(self, text: str) -> bool:
		with self._lock:
			writer = self._clipboard_writer
		if writer is None:
			return False
		try:
			return bool(writer(str(text)[:CLIPBOARD_TEXT_LIMIT]))
		except Exception:
			return False

	@staticmethod
	def _bounded(value: object, default: int, minimum: int, maximum: int) -> int:
		try:
			return max(minimum, min(maximum, int(value)))
		except (TypeError, ValueError):
			return default

	@staticmethod
	def _normalize_settings(settings: OSSettings) -> OSSettings:
		palette_id = normalize_theme_palette(settings.palette_id, settings.theme_mode)
		corner_style = normalize_window_corner_style(settings.window_corner_style)
		motion_blur = settings.motion_blur_enabled
		if not isinstance(motion_blur, bool):
			motion_blur = OSSettings().motion_blur_enabled
		anti_aliasing = settings.anti_aliasing_mode
		if type(anti_aliasing) is not int or anti_aliasing not in ANTI_ALIASING_MODES:
			anti_aliasing = OSSettings().anti_aliasing_mode
		return replace(
			settings,
			palette_id=settings.palette_id if palette_id is None else palette_id,
			window_transparency=0,
			window_corner_style=(
				OSSettings().window_corner_style if corner_style is None else corner_style
			),
			motion_blur_enabled=motion_blur,
			anti_aliasing_mode=anti_aliasing,
		)

	@classmethod
	def _settings_from_payload(cls, payload: object) -> OSSettings | None:
		if not isinstance(payload, dict) or payload.get("version") != SETTINGS_SCHEMA_VERSION:
			return None
		defaults = OSSettings()
		background_id = cls._bounded(payload.get("background_id"), defaults.background_id, 0, len(BACKGROUND_NAMES) - 1)
		palette_id = cls._bounded(payload.get("palette_id"), defaults.palette_id, 0, len(PALETTES) - 1)
		theme_mode = cls._bounded(payload.get("theme_mode"), defaults.theme_mode, THEME_DARK, THEME_LIGHT)
		palette_id = normalize_theme_palette(palette_id, theme_mode)
		if palette_id is None:
			palette_id = defaults.palette_id
		corner_style = normalize_window_corner_style(payload.get("window_corner_style"))
		if corner_style is None:
			corner_style = defaults.window_corner_style
		enabled = payload.get("settings_enabled", defaults.settings_enabled)
		if not isinstance(enabled, bool):
			enabled = defaults.settings_enabled
		motion_blur = payload.get("motion_blur_enabled", defaults.motion_blur_enabled)
		if not isinstance(motion_blur, bool):
			motion_blur = defaults.motion_blur_enabled
		anti_aliasing = payload.get("anti_aliasing_mode", defaults.anti_aliasing_mode)
		if type(anti_aliasing) is not int or anti_aliasing not in ANTI_ALIASING_MODES:
			anti_aliasing = defaults.anti_aliasing_mode
		return OSSettings(
			cls._bounded(payload.get("volume"), defaults.volume, 0, 100),
			background_id,
			palette_id,
			cls._bounded(payload.get("music_volume"), defaults.music_volume, 0, 100),
			cls._bounded(payload.get("sound_effect_volume"), defaults.sound_effect_volume, 0, 100),
			theme_mode,
			0,
			corner_style,
			cls._bounded(payload.get("icon_size"), defaults.icon_size, 0, 2),
			cls._bounded(payload.get("clock_format"), defaults.clock_format, 0, 1),
			enabled,
			motion_blur,
			anti_aliasing,
		)

	def _load_settings(self) -> OSSettings | None:
		if self._settings_path is None or not self._settings_path.is_file():
			return None
		try:
			payload = json.loads(self._settings_path.read_text(encoding="utf-8"))
		except (OSError, UnicodeError, json.JSONDecodeError):
			return None
		return self._settings_from_payload(payload)

	def _persist_locked(self) -> bool:
		if self._settings_path is None:
			return True
		settings = self._settings
		payload = {
			"version": SETTINGS_SCHEMA_VERSION,
			"volume": settings.volume,
			"background_id": settings.background_id,
			"palette_id": settings.palette_id,
			"music_volume": settings.music_volume,
			"sound_effect_volume": settings.sound_effect_volume,
			"theme_mode": settings.theme_mode,
			"window_transparency": settings.window_transparency,
			"window_corner_style": settings.window_corner_style,
			"icon_size": settings.icon_size,
			"clock_format": settings.clock_format,
			"settings_enabled": settings.settings_enabled,
			"motion_blur_enabled": settings.motion_blur_enabled,
			"anti_aliasing_mode": settings.anti_aliasing_mode,
		}
		temporary: Path | None = None
		fd: int | None = None
		try:
			self._settings_path.parent.mkdir(parents=True, exist_ok=True)
			fd, temporary_name = tempfile.mkstemp(
				prefix=f".{self._settings_path.name}.",
				suffix=".tmp",
				dir=self._settings_path.parent,
				text=True,
			)
			temporary = Path(temporary_name)
			stream = os.fdopen(fd, "w", encoding="utf-8", newline="")
			fd = None
			with stream:
				stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
				stream.flush()
				os.fsync(stream.fileno())
			os.replace(temporary, self._settings_path)
			return True
		except (OSError, UnicodeError):
			if fd is not None:
				try:
					os.close(fd)
				except OSError:
					pass
			if temporary is not None:
				try:
					temporary.unlink(missing_ok=True)
				except OSError:
					pass
			return False

	def _store_locked(self, settings: OSSettings) -> bool:
		previous = self._settings
		self._settings = settings
		if self._persist_locked():
			self._preview = None
			return True
		self._settings = previous
		return False

	def now(self) -> datetime:
		return self._now_provider()

	@property
	def settings(self) -> OSSettings:
		with self._lock:
			return self._settings

	@property
	def effective_settings(self) -> OSSettings:
		with self._lock:
			if self._preview is None:
				return self._settings
			return replace(
				self._settings,
				background_id=self._preview.background_id,
				palette_id=self._preview.palette_id,
				theme_mode=self._preview.theme_mode,
				window_corner_style=self._preview.window_corner_style,
			)

	@property
	def volume(self) -> int:
		return self.settings.volume

	@property
	def background_id(self) -> int:
		return self.effective_settings.background_id

	@property
	def palette_id(self) -> int:
		return self.effective_settings.palette_id

	@property
	def palette(self) -> tuple[str, ...]:
		return PALETTES[self.palette_id]

	@property
	def music_volume(self) -> int:
		return self.settings.music_volume

	@property
	def sound_effect_volume(self) -> int:
		return self.settings.sound_effect_volume

	@property
	def theme_mode(self) -> int:
		return self.effective_settings.theme_mode

	@property
	def window_transparency(self) -> int:
		return self.settings.window_transparency

	@property
	def window_corner_style(self) -> int:
		return self.effective_settings.window_corner_style

	@property
	def preview_active(self) -> bool:
		with self._lock:
			return self._preview is not None

	@property
	def icon_size(self) -> int:
		return self.settings.icon_size

	@property
	def clock_format(self) -> int:
		return self.settings.clock_format

	@property
	def settings_enabled(self) -> bool:
		return self.settings.settings_enabled

	@property
	def motion_blur_enabled(self) -> bool:
		return self.settings.motion_blur_enabled

	@property
	def anti_aliasing_mode(self) -> int:
		return self.settings.anti_aliasing_mode

	@property
	def background_count(self) -> int:
		return len(BACKGROUND_NAMES)

	@property
	def palette_count(self) -> int:
		return len(PALETTES)

	def set_volume(self, volume: int) -> bool:
		with self._lock:
			return self._store_locked(replace(self._settings, volume=max(0, min(100, int(volume)))))

	def set_background(self, background_id: int) -> bool:
		if not 0 <= int(background_id) < self.background_count:
			return False
		with self._lock:
			return self._store_locked(replace(self._settings, background_id=int(background_id)))

	def set_palette(self, palette_id: int) -> bool:
		if not 0 <= int(palette_id) < self.palette_count:
			return False
		with self._lock:
			palette_id = int(palette_id)
			group_size = self.palette_count // 2
			return self._store_locked(replace(
				self._settings,
				palette_id=palette_id,
				theme_mode=THEME_LIGHT if palette_id >= group_size else THEME_DARK,
			))

	def set_music_volume(self, value: int) -> bool:
		with self._lock:
			return self._store_locked(replace(self._settings, music_volume=max(0, min(100, int(value)))))

	def set_sound_effect_volume(self, value: int) -> bool:
		with self._lock:
			return self._store_locked(replace(self._settings, sound_effect_volume=max(0, min(100, int(value)))))

	def set_theme_mode(self, value: int) -> bool:
		value = int(value)
		if value not in (THEME_DARK, THEME_LIGHT):
			return False
		with self._lock:
			palette_id = normalize_theme_palette(self._settings.palette_id, value)
			if palette_id is None:
				return False
			return self._store_locked(replace(self._settings, theme_mode=value, palette_id=palette_id))

	def set_window_transparency(self, value: int) -> bool:
		with self._lock:
			return self._store_locked(replace(self._settings, window_transparency=0))

	def set_window_corner_style(self, value: int) -> bool:
		value = normalize_window_corner_style(value)
		if value is None:
			return False
		with self._lock:
			return self._store_locked(replace(self._settings, window_corner_style=value))

	def preview_preferences(
		self,
		background_id: int,
		palette_id: int,
		theme_mode: int,
		window_corner_style: int,
	) -> bool:
		background_id = int(background_id)
		theme_mode = int(theme_mode)
		corner_style = normalize_window_corner_style(window_corner_style)
		palette_id = normalize_theme_palette(int(palette_id), theme_mode)
		if (
			not 0 <= background_id < self.background_count
			or palette_id is None
			or corner_style is None
		):
			self.clear_preview()
			return False
		with self._lock:
			self._preview = AppearancePreview(
				background_id,
				palette_id,
				theme_mode,
				corner_style,
			)
		return True

	def clear_preview(self) -> None:
		with self._lock:
			self._preview = None

	def set_icon_size(self, value: int) -> bool:
		value = int(value)
		if value not in (0, 1, 2):
			return False
		with self._lock:
			return self._store_locked(replace(self._settings, icon_size=value))

	def set_clock_format(self, value: int) -> bool:
		value = int(value)
		if value not in (0, 1):
			return False
		with self._lock:
			return self._store_locked(replace(self._settings, clock_format=value))

	def set_settings_enabled(self, value: bool | int) -> bool:
		with self._lock:
			return self._store_locked(replace(self._settings, settings_enabled=bool(value)))

	def set_motion_blur_enabled(self, value: bool | int) -> bool:
		with self._lock:
			return self._store_locked(replace(self._settings, motion_blur_enabled=bool(value)))

	def set_anti_aliasing_mode(self, value: int) -> bool:
		value = int(value)
		if value not in ANTI_ALIASING_MODES:
			return False
		with self._lock:
			return self._store_locked(replace(self._settings, anti_aliasing_mode=value))

	def apply(self, volume: int, background_id: int, palette_id: int) -> bool:
		background_id = int(background_id)
		palette_id = int(palette_id)
		if not 0 <= background_id < self.background_count:
			return False
		if not 0 <= palette_id < self.palette_count:
			return False
		with self._lock:
			group_size = self.palette_count // 2
			return self._store_locked(replace(
				self._settings,
				volume=max(0, min(100, int(volume))),
				background_id=background_id,
				palette_id=palette_id,
				theme_mode=THEME_LIGHT if palette_id >= group_size else THEME_DARK,
			))

	def apply_preferences(
		self,
		volume: int,
		music_volume: int,
		sound_effect_volume: int,
		background_id: int,
		palette_id: int,
		theme_mode: int,
		window_transparency: int,
		window_corner_style: int,
		icon_size: int,
		clock_format: int,
		settings_enabled: bool | int,
	) -> bool:
		with self._lock:
			return self.apply_preferences_v2(
				volume,
				music_volume,
				sound_effect_volume,
				background_id,
				palette_id,
				theme_mode,
				window_transparency,
				window_corner_style,
				icon_size,
				clock_format,
				settings_enabled,
				self._settings.motion_blur_enabled,
				self._settings.anti_aliasing_mode,
			)

	def apply_preferences_v2(
		self,
		volume: int,
		music_volume: int,
		sound_effect_volume: int,
		background_id: int,
		palette_id: int,
		theme_mode: int,
		window_transparency: int,
		window_corner_style: int,
		icon_size: int,
		clock_format: int,
		settings_enabled: bool | int,
		motion_blur_enabled: bool | int,
		anti_aliasing_mode: int,
	) -> bool:
		background_id = int(background_id)
		palette_id = int(palette_id)
		theme_mode = int(theme_mode)
		window_corner_style = normalize_window_corner_style(window_corner_style)
		icon_size = int(icon_size)
		clock_format = int(clock_format)
		anti_aliasing_mode = int(anti_aliasing_mode)
		palette_id = normalize_theme_palette(palette_id, theme_mode)
		if (
			not 0 <= background_id < self.background_count
			or palette_id is None
			or window_corner_style is None
			or icon_size not in (0, 1, 2)
			or clock_format not in (0, 1)
			or anti_aliasing_mode not in ANTI_ALIASING_MODES
		):
			self.clear_preview()
			return False
		with self._lock:
			return self._store_locked(OSSettings(
				max(0, min(100, int(volume))),
				background_id,
				palette_id,
				max(0, min(100, int(music_volume))),
				max(0, min(100, int(sound_effect_volume))),
				theme_mode,
				0,
				window_corner_style,
				icon_size,
				clock_format,
				bool(settings_enabled),
				bool(motion_blur_enabled),
				anti_aliasing_mode,
			))

	def draw_background(self, graphics: GraphicsDevice) -> None:
		graphics.reset_clip()
		graphics.clear(BACKGROUND_COLORS[self.background_id])
