from __future__ import annotations

from dataclasses import dataclass
import struct
import zlib


XMUSIC_MAGIC = 0x584D5531  # XMU1
XMUSIC_VERSION = 1
XMUSIC_HEADER_WORDS = 10
XMUSIC_EVENT_WORDS = 5
XMUSIC_MAX_EVENTS = 32_000
XMUSIC_MAX_WORDS = 200_000


class XMusicError(ValueError):
	pass


@dataclass(frozen=True, order=True)
class NoteEvent:
	tick: int
	duration: int
	pitch: int
	velocity: int = 100
	instrument: int = 0


@dataclass(frozen=True)
class Track:
	tempo_bpm: int
	ticks_per_beat: int
	events: tuple[NoteEvent, ...]
	loop_start: int = 0
	loop_end: int = 0


def _bytes(words: list[int]) -> bytes:
	return b"".join(struct.pack(">I", word & 0xFFFFFFFF) for word in words)


def _crc(words: list[int]) -> int:
	copy = list(words)
	copy[8] = 0
	return zlib.crc32(_bytes(copy)) & 0xFFFFFFFF


def _validate(track: Track) -> None:
	if not 20 <= int(track.tempo_bpm) <= 400:
		raise XMusicError("Tempo must be 20..400 BPM")
	if not 1 <= int(track.ticks_per_beat) <= 9600:
		raise XMusicError("Ticks per beat must be 1..9600")
	if len(track.events) > XMUSIC_MAX_EVENTS:
		raise XMusicError("Track has too many note events")
	previous = -1
	for event in track.events:
		if event.tick < previous or event.tick < 0 or event.duration <= 0:
			raise XMusicError("Events must be sorted and have positive duration")
		if not 0 <= event.pitch <= 127 or not 0 <= event.velocity <= 127 or not 0 <= event.instrument <= 127:
			raise XMusicError("Pitch, velocity, and instrument must be MIDI-range values")
		previous = event.tick
	if track.loop_start < 0 or track.loop_end < 0 or (track.loop_end and track.loop_end <= track.loop_start):
		raise XMusicError("Loop range is invalid")


def encode_xmusic(track: Track, *, max_words: int = XMUSIC_MAX_WORDS) -> tuple[int, ...]:
	_validate(track)
	total_words = XMUSIC_HEADER_WORDS + len(track.events) * XMUSIC_EVENT_WORDS
	if total_words > max_words:
		raise XMusicError("Encoded track exceeds the portable word budget")
	words = [
		XMUSIC_MAGIC,
		XMUSIC_VERSION,
		int(track.tempo_bpm),
		int(track.ticks_per_beat),
		len(track.events),
		int(track.loop_start),
		int(track.loop_end),
		total_words,
		0,
		0,
	]
	for event in track.events:
		words.extend((event.tick, event.duration, event.pitch, event.velocity, event.instrument))
	words[8] = _crc(words)
	return tuple(words)


def decode_xmusic(words: tuple[int, ...] | list[int], *, max_words: int = XMUSIC_MAX_WORDS) -> Track:
	values = [int(word) & 0xFFFFFFFF for word in words]
	if len(values) < XMUSIC_HEADER_WORDS or values[0] != XMUSIC_MAGIC or values[1] != XMUSIC_VERSION:
		raise XMusicError("Unsupported or truncated XMusic stream")
	tempo, ticks, count, loop_start, loop_end, total_words, expected_crc = values[2:9]
	if total_words != len(values) or total_words > max_words or count > XMUSIC_MAX_EVENTS:
		raise XMusicError("XMusic length is invalid")
	if total_words != XMUSIC_HEADER_WORDS + count * XMUSIC_EVENT_WORDS:
		raise XMusicError("XMusic event table length is invalid")
	if expected_crc != _crc(values):
		raise XMusicError("XMusic checksum mismatch")
	events = tuple(
		NoteEvent(*values[offset:offset + XMUSIC_EVENT_WORDS])
		for offset in range(XMUSIC_HEADER_WORDS, len(values), XMUSIC_EVENT_WORDS)
	)
	track = Track(tempo, ticks, events, loop_start, loop_end)
	_validate(track)
	return track


class Sequencer:
	"""Deterministic, delta-time-aware sequencer shared by native and Scratch hosts."""

	def __init__(self, track: Track) -> None:
		_validate(track)
		self.track = track
		self.position_ticks = 0.0
		self.playing = False
		self.finished = False

	@property
	def duration_ticks(self) -> int:
		return max((event.tick + event.duration for event in self.track.events), default=0)

	def play(self) -> None:
		if self.finished:
			self.position_ticks = 0.0
			self.finished = False
		self.playing = True

	def pause(self) -> None:
		self.playing = False

	def seek_ticks(self, tick: float) -> None:
		self.position_ticks = max(0.0, min(float(tick), float(self.duration_ticks)))
		self.finished = self.duration_ticks > 0 and self.position_ticks >= self.duration_ticks

	def seek_fraction(self, fraction: float) -> None:
		self.seek_ticks(max(0.0, min(1.0, float(fraction))) * self.duration_ticks)

	def advance(self, delta_ms: float) -> None:
		if not self.playing:
			return
		delta_ms = max(0.0, min(50.0, float(delta_ms)))
		ticks_per_ms = self.track.tempo_bpm * self.track.ticks_per_beat / 60_000.0
		self.position_ticks += delta_ms * ticks_per_ms
		loop_end = self.track.loop_end or self.duration_ticks
		if loop_end > self.track.loop_start and self.position_ticks >= loop_end and self.track.loop_end:
			span = loop_end - self.track.loop_start
			self.position_ticks = self.track.loop_start + (self.position_ticks - loop_end) % span
		elif self.position_ticks >= self.duration_ticks:
			self.position_ticks = float(self.duration_ticks)
			self.playing = False
			self.finished = True

	def active_notes(self) -> tuple[NoteEvent, ...]:
		position = self.position_ticks
		return tuple(event for event in self.track.events if event.tick <= position < event.tick + event.duration)
