from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Callable

from .graphics import GraphicsDevice


PALETTES = (
	(
		"#020716", "#102552", "#006b67", "#00a7a0",
		"#7f1d1d", "#43008f", "#a16207", "#b8bcd8",
		"#273b63", "#6c18d8", "#4dffae", "#aefcff",
		"#fb7185", "#ad55ef", "#fde047", "#f4f0ff",
	),
	(
		"#09030f", "#21113a", "#183447", "#147d92",
		"#9f2445", "#5112a8", "#c26c22", "#d8c9eb",
		"#5b496c", "#7c35e8", "#39d98a", "#66e3ff",
		"#ff6685", "#c45cff", "#ffcf5c", "#fff8ff",
	),
	(
		"#000000", "#101820", "#005a46", "#00a88f",
		"#8b0000", "#660099", "#9a5b00", "#c0c0c0",
		"#555555", "#246bfe", "#44d544", "#00ffff",
		"#ff4040", "#d060ff", "#ffff00", "#ffffff",
	),
	(
		"#f7f5ff", "#dce5ff", "#d5f5ef", "#86ded6",
		"#ffd9df", "#e8dcff", "#fff0b8", "#42475d",
		"#b9c8e8", "#b084ef", "#157a59", "#096a78",
		"#b62949", "#6d2daf", "#8a6500", "#151525",
	),
	(
		"#fff8ff", "#eadff5", "#d8ecf1", "#99d5df",
		"#ffd0dc", "#e1cffc", "#ffe4bd", "#514a60",
		"#c7b9d4", "#a87bea", "#147957", "#087081",
		"#b5284b", "#7134b5", "#826000", "#1b1422",
	),
	(
		"#f6f7f8", "#dce3e8", "#d1eee4", "#83d8c7",
		"#ffd7d7", "#e9d8f0", "#f5e4bb", "#4d5359",
		"#bcc4ca", "#87a9ec", "#31845b", "#147982",
		"#b83535", "#75438c", "#746000", "#16191c",
	),
)

BACKGROUND_NAMES = ("Black", "Navy", "Slate")
BACKGROUND_COLORS = (0, 1, 8)
SETTINGS_SCHEMA_VERSION = 1
CLIPBOARD_TEXT_LIMIT = 32_768


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
		self._settings = settings or self._load_settings() or OSSettings()
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

	@classmethod
	def _settings_from_payload(cls, payload: object) -> OSSettings | None:
		if not isinstance(payload, dict) or payload.get("version") != SETTINGS_SCHEMA_VERSION:
			return None
		defaults = OSSettings()
		background_id = cls._bounded(payload.get("background_id"), defaults.background_id, 0, len(BACKGROUND_NAMES) - 1)
		palette_id = cls._bounded(payload.get("palette_id"), defaults.palette_id, 0, len(PALETTES) - 1)
		theme_mode = cls._bounded(payload.get("theme_mode"), defaults.theme_mode, 0, 1)
		if theme_mode == 1 and palette_id < 3:
			palette_id += 3
		elif theme_mode == 0 and palette_id >= 3:
			palette_id -= 3
		enabled = payload.get("settings_enabled", defaults.settings_enabled)
		if not isinstance(enabled, bool):
			enabled = defaults.settings_enabled
		return OSSettings(
			cls._bounded(payload.get("volume"), defaults.volume, 0, 100),
			background_id,
			palette_id,
			cls._bounded(payload.get("music_volume"), defaults.music_volume, 0, 100),
			cls._bounded(payload.get("sound_effect_volume"), defaults.sound_effect_volume, 0, 100),
			theme_mode,
			cls._bounded(payload.get("window_transparency"), defaults.window_transparency, 0, 100),
			cls._bounded(payload.get("window_corner_style"), defaults.window_corner_style, 0, 2),
			cls._bounded(payload.get("icon_size"), defaults.icon_size, 0, 2),
			cls._bounded(payload.get("clock_format"), defaults.clock_format, 0, 1),
			enabled,
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
	def volume(self) -> int:
		return self.settings.volume

	@property
	def background_id(self) -> int:
		return self.settings.background_id

	@property
	def palette_id(self) -> int:
		return self.settings.palette_id

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
		return self.settings.theme_mode

	@property
	def window_transparency(self) -> int:
		return self.settings.window_transparency

	@property
	def window_corner_style(self) -> int:
		return self.settings.window_corner_style

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
			return self._store_locked(replace(
				self._settings,
				palette_id=palette_id,
				theme_mode=1 if palette_id >= 3 else 0,
			))

	def set_music_volume(self, value: int) -> bool:
		with self._lock:
			return self._store_locked(replace(self._settings, music_volume=max(0, min(100, int(value)))))

	def set_sound_effect_volume(self, value: int) -> bool:
		with self._lock:
			return self._store_locked(replace(self._settings, sound_effect_volume=max(0, min(100, int(value)))))

	def set_theme_mode(self, value: int) -> bool:
		value = int(value)
		if value not in (0, 1):
			return False
		with self._lock:
			palette_id = self._settings.palette_id
			if value == 1 and palette_id < 3:
				palette_id += 3
			elif value == 0 and palette_id >= 3:
				palette_id -= 3
			return self._store_locked(replace(self._settings, theme_mode=value, palette_id=palette_id))

	def set_window_transparency(self, value: int) -> bool:
		with self._lock:
			return self._store_locked(replace(self._settings, window_transparency=max(0, min(100, int(value)))))

	def set_window_corner_style(self, value: int) -> bool:
		value = int(value)
		if value not in (0, 1, 2):
			return False
		with self._lock:
			return self._store_locked(replace(self._settings, window_corner_style=value))

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

	def apply(self, volume: int, background_id: int, palette_id: int) -> bool:
		background_id = int(background_id)
		palette_id = int(palette_id)
		if not 0 <= background_id < self.background_count:
			return False
		if not 0 <= palette_id < self.palette_count:
			return False
		with self._lock:
			return self._store_locked(replace(
				self._settings,
				volume=max(0, min(100, int(volume))),
				background_id=background_id,
				palette_id=palette_id,
				theme_mode=1 if palette_id >= 3 else 0,
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
		background_id = int(background_id)
		palette_id = int(palette_id)
		theme_mode = int(theme_mode)
		window_corner_style = int(window_corner_style)
		icon_size = int(icon_size)
		clock_format = int(clock_format)
		if not 0 <= background_id < self.background_count:
			return False
		if not 0 <= palette_id < self.palette_count:
			return False
		if theme_mode not in (0, 1):
			return False
		if window_corner_style not in (0, 1, 2):
			return False
		if icon_size not in (0, 1, 2):
			return False
		if clock_format not in (0, 1):
			return False
		if theme_mode == 1 and palette_id < 3:
			palette_id += 3
		elif theme_mode == 0 and palette_id >= 3:
			palette_id -= 3
		with self._lock:
			return self._store_locked(OSSettings(
				max(0, min(100, int(volume))),
				background_id,
				palette_id,
				max(0, min(100, int(music_volume))),
				max(0, min(100, int(sound_effect_volume))),
				theme_mode,
				max(0, min(100, int(window_transparency))),
				window_corner_style,
				icon_size,
				clock_format,
				bool(settings_enabled),
			))

	def draw_background(self, graphics: GraphicsDevice) -> None:
		graphics.reset_clip()
		graphics.clear(BACKGROUND_COLORS[self.background_id])
