from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable

from xe_lang.media.image_format import PortableImage, XIMGError, decode_ximg
from xe_lang.media.music_format import Sequencer, Track, XMusicError, decode_xmusic

from .filesystem import FileSystemDevice


def parse_word_stream(text: str, *, max_words: int = 200_000) -> tuple[int, ...]:
	words: list[int] = []
	for line in text.splitlines():
		for raw in line.split("#", 1)[0].replace(",", " ").split():
			try:
				words.append(int(raw, 0))
			except ValueError as error:
				raise ValueError(f"Invalid portable asset word {raw!r}") from error
			if len(words) > max_words:
				raise ValueError(f"Portable asset exceeds {max_words} words")
	return tuple(words)


@dataclass(frozen=True)
class ImageAsset:
	handle: int
	name: str
	image: PortableImage
	revision: int


class ImageAssetStore:
	def __init__(self, files: FileSystemDevice) -> None:
		self.files = files
		self._next_handle = 1
		self._assets: dict[int, ImageAsset] = {}
		self._by_name: dict[str, int] = {}
		self._lock = RLock()

	def register(self, name: str, image: PortableImage) -> int:
		normalized = self.files.normalize(name) or str(name)
		with self._lock:
			existing = self._by_name.get(normalized)
			if existing:
				self._assets[existing] = ImageAsset(existing, normalized, image, self.files.revision)
				return existing
			handle = self._next_handle
			self._next_handle += 1
			self._by_name[normalized] = handle
			self._assets[handle] = ImageAsset(handle, normalized, image, self.files.revision)
			return handle

	def load(self, name: str) -> int:
		normalized = self.files.normalize(name)
		if not normalized:
			return 0
		with self._lock:
			handle = self._by_name.get(normalized)
			asset = self._assets.get(handle or 0)
			if asset is not None and asset.revision == self.files.revision:
				return asset.handle
		text = self.files.read_text(normalized)
		if text is None:
			return 0
		try:
			image = decode_ximg(parse_word_stream(text))
		except (ValueError, XIMGError):
			return 0
		return self.register(normalized, image)

	def get(self, handle: int) -> ImageAsset | None:
		with self._lock:
			return self._assets.get(int(handle))


@dataclass
class TrackAsset:
	handle: int
	name: str
	track: Track
	sequencer: Sequencer
	revision: int


@dataclass(frozen=True)
class AudioVoice:
	pitch: int
	velocity: int
	instrument: int


@dataclass(frozen=True)
class AudioState:
	voices: tuple[AudioVoice, ...]
	volume: int
	playing: bool


class AudioDevice:
	def __init__(
		self,
		files: FileSystemDevice,
		event_handler: Callable[[AudioState], None] | None = None,
		volume_provider: Callable[[], int] | None = None,
	) -> None:
		self.files = files
		self.event_handler = event_handler
		self.volume_provider = volume_provider or (lambda: 100)
		self._next_handle = 1
		self._assets: dict[int, TrackAsset] = {}
		self._by_name: dict[str, int] = {}
		self._lock = RLock()
		self._last_state: AudioState | None = None

	def _state(self) -> AudioState:
		voices: list[AudioVoice] = []
		playing = False
		with self._lock:
			for handle in sorted(self._assets):
				asset = self._assets[handle]
				if not asset.sequencer.playing:
					continue
				playing = True
				voices.extend(
					AudioVoice(note.pitch, note.velocity, note.instrument)
					for note in asset.sequencer.active_notes()
				)
		try:
			volume = int(self.volume_provider())
		except (TypeError, ValueError):
			volume = 100
		return AudioState(tuple(voices), max(0, min(100, volume)), playing)

	def _emit(self, *, force: bool = False) -> None:
		state = self._state()
		if not force and state == self._last_state:
			return
		self._last_state = state
		if self.event_handler is not None:
			try:
				self.event_handler(state)
			except Exception:
				pass

	def register(self, name: str, track: Track) -> int:
		normalized = self.files.normalize(name) or str(name)
		with self._lock:
			handle = self._by_name.get(normalized)
			if handle:
				self._assets[handle] = TrackAsset(handle, normalized, track, Sequencer(track), self.files.revision)
				return handle
			handle = self._next_handle
			self._next_handle += 1
			self._by_name[normalized] = handle
			self._assets[handle] = TrackAsset(handle, normalized, track, Sequencer(track), self.files.revision)
			return handle

	def load(self, name: str) -> int:
		normalized = self.files.normalize(name)
		if not normalized:
			return 0
		with self._lock:
			handle = self._by_name.get(normalized)
			asset = self._assets.get(handle or 0)
			if asset is not None and asset.revision == self.files.revision:
				return asset.handle
		text = self.files.read_text(normalized)
		if text is None:
			return 0
		try:
			track = decode_xmusic(parse_word_stream(text))
		except (ValueError, XMusicError):
			return 0
		return self.register(normalized, track)

	def get(self, handle: int) -> TrackAsset | None:
		with self._lock:
			return self._assets.get(int(handle))

	def play(self, handle: int) -> bool:
		asset = self.get(handle)
		if asset is None:
			return False
		asset.sequencer.play()
		self._emit()
		return True

	def pause(self, handle: int) -> bool:
		asset = self.get(handle)
		if asset is None:
			return False
		asset.sequencer.pause()
		self._emit()
		return True

	def stop(self, handle: int) -> bool:
		asset = self.get(handle)
		if asset is None:
			return False
		asset.sequencer.pause()
		asset.sequencer.seek_ticks(0)
		self._emit()
		return True

	def seek(self, handle: int, ticks: int) -> bool:
		asset = self.get(handle)
		if asset is None:
			return False
		asset.sequencer.seek_ticks(ticks)
		self._emit()
		return True

	def update(self, handle: int, delta_ms: int) -> bool:
		asset = self.get(handle)
		if asset is None:
			return False
		asset.sequencer.advance(delta_ms)
		self._emit()
		return True

	def stop_all(self) -> None:
		with self._lock:
			for asset in self._assets.values():
				asset.sequencer.pause()
		self._emit(force=True)
