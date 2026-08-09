from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import struct
import tempfile
import zipfile
import zlib


XIMG_MAGIC = 0x58494D32  # XIM2
XIMG_VERSION = 2
XIMG_HEADER_WORDS = 12
XIMG_FRAME_WORDS = 5
XIMG_TRANSPARENT = 16
XIMG_MAX_COLOR = 16
XIMG_MAX_FRAMES = 4096
XIMG_MAX_DIMENSION = 4096
XIMG_MAX_WORDS = 200_000
XIMG_MAX_DELTA_CHAIN = 16

ENCODING_RAW = 0
ENCODING_RLE = 1
ENCODING_DELTA_RLE = 2
NO_BASE_FRAME = 0xFFFFFFFF
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class XIMGError(ValueError):
	pass


@dataclass(frozen=True)
class ImageFrame:
	pixels: tuple[int, ...]
	duration_ms: int = 100


@dataclass(frozen=True)
class PortableImage:
	width: int
	height: int
	frames: tuple[ImageFrame, ...]
	loop_count: int = 0


def _word_bytes(words: list[int]) -> bytes:
	return b"".join(struct.pack(">I", int(word) & 0xFFFFFFFF) for word in words)


def _crc(words: list[int]) -> int:
	copy = list(words)
	copy[10] = 0
	return zlib.crc32(_word_bytes(copy)) & 0xFFFFFFFF


def _validate_image(image: PortableImage) -> int:
	width = int(image.width)
	height = int(image.height)
	if not 1 <= width <= XIMG_MAX_DIMENSION or not 1 <= height <= XIMG_MAX_DIMENSION:
		raise XIMGError(f"Image dimensions must be 1..{XIMG_MAX_DIMENSION}")
	pixel_count = width * height
	if pixel_count > XIMG_MAX_WORDS * 6:
		raise XIMGError("Decoded image exceeds the portable decode budget")
	if not 1 <= len(image.frames) <= XIMG_MAX_FRAMES:
		raise XIMGError(f"Frame count must be 1..{XIMG_MAX_FRAMES}")
	if not 0 <= int(image.loop_count) <= 0xFFFFFFFF:
		raise XIMGError("Loop count is outside the 32-bit range")
	for frame in image.frames:
		if len(frame.pixels) != pixel_count:
			raise XIMGError(f"Frame has {len(frame.pixels)} pixels; expected {pixel_count}")
		if not 1 <= int(frame.duration_ms) <= 0xFFFFFFFF:
			raise XIMGError("Frame duration must be a positive 32-bit millisecond value")
		if any(not 0 <= int(pixel) <= XIMG_MAX_COLOR for pixel in frame.pixels):
			raise XIMGError("Pixels must be palette indices 0..15 or transparent index 16")
	return pixel_count


def _pack_pixels(pixels: tuple[int, ...]) -> list[int]:
	words: list[int] = []
	for offset in range(0, len(pixels), 6):
		word = 0
		for slot, value in enumerate(pixels[offset:offset + 6]):
			word |= (int(value) & 0x1F) << (slot * 5)
		words.append(word)
	return words


def _unpack_pixels(words: list[int], count: int) -> tuple[int, ...]:
	pixels: list[int] = []
	for word in words:
		for slot in range(6):
			pixels.append((word >> (slot * 5)) & 0x1F)
			if len(pixels) == count:
				if any(value > XIMG_MAX_COLOR for value in pixels):
					raise XIMGError("Raw frame contains an invalid palette index")
				return tuple(pixels)
	if len(pixels) != count:
		raise XIMGError("Raw frame ended before all pixels were decoded")
	return tuple(pixels)


def _encode_rle(pixels: tuple[int, ...]) -> list[int]:
	encoded: list[int] = []
	index = 0
	while index < len(pixels):
		value = pixels[index]
		run = 1
		while index + run < len(pixels) and pixels[index + run] == value and run < 0x07FFFFFF:
			run += 1
		encoded.append((run << 5) | value)
		index += run
	return encoded


def _decode_rle(words: list[int], count: int) -> tuple[int, ...]:
	pixels: list[int] = []
	for word in words:
		value = word & 0x1F
		run = word >> 5
		if value > XIMG_MAX_COLOR or run <= 0 or len(pixels) + run > count:
			raise XIMGError("Invalid RLE frame")
		pixels.extend((value,) * run)
	if len(pixels) != count:
		raise XIMGError("RLE frame ended before all pixels were decoded")
	return tuple(pixels)


def _encode_delta(previous: tuple[int, ...], pixels: tuple[int, ...]) -> list[int]:
	encoded: list[int] = []
	index = 0
	while index < len(pixels):
		skip = 0
		while index < len(pixels) and pixels[index] == previous[index] and skip < 0xFFFFFFFF:
			index += 1
			skip += 1
		if index == len(pixels):
			break
		value = pixels[index]
		run = 1
		while (
			index + run < len(pixels)
			and pixels[index + run] == value
			and pixels[index + run] != previous[index + run]
			and run < 0x07FFFFFF
		):
			run += 1
		encoded.extend((skip, (run << 5) | value))
		index += run
	return encoded


def _decode_delta(words: list[int], previous: tuple[int, ...]) -> tuple[int, ...]:
	if len(words) % 2:
		raise XIMGError("Delta frame has a partial record")
	pixels = list(previous)
	index = 0
	for offset in range(0, len(words), 2):
		index += words[offset]
		packed = words[offset + 1]
		value = packed & 0x1F
		run = packed >> 5
		if value > XIMG_MAX_COLOR or run <= 0 or index + run > len(pixels):
			raise XIMGError("Invalid delta frame")
		pixels[index:index + run] = [value] * run
		index += run
	return tuple(pixels)


def encode_ximg(image: PortableImage, *, max_words: int = XIMG_MAX_WORDS) -> tuple[int, ...]:
	_validate_image(image)
	frame_records: list[tuple[int, int, int, int, int]] = []
	data: list[int] = []
	previous: tuple[int, ...] | None = None
	delta_chain = 0
	for index, frame in enumerate(image.frames):
		raw = _pack_pixels(frame.pixels)
		rle = _encode_rle(frame.pixels)
		choices: list[tuple[int, int, list[int], int]] = [
			(len(raw), ENCODING_RAW, raw, NO_BASE_FRAME),
			(len(rle), ENCODING_RLE, rle, NO_BASE_FRAME),
		]
		if previous is not None and delta_chain < XIMG_MAX_DELTA_CHAIN - 1:
			delta = _encode_delta(previous, frame.pixels)
			choices.append((len(delta), ENCODING_DELTA_RLE, delta, index - 1))
		_, encoding, encoded, base_frame = min(choices, key=lambda item: (item[0], item[1]))
		if encoding == ENCODING_DELTA_RLE:
			delta_chain += 1
		else:
			delta_chain = 0
		offset = len(data)
		data.extend(encoded)
		frame_records.append((int(frame.duration_ms), encoding, offset, len(encoded), base_frame))
		previous = frame.pixels

	table_offset = XIMG_HEADER_WORDS
	data_offset = table_offset + len(frame_records) * XIMG_FRAME_WORDS
	total_words = data_offset + len(data)
	if total_words > int(max_words):
		raise XIMGError(f"Encoded image requires {total_words} words; limit is {max_words}")
	words = [
		XIMG_MAGIC,
		XIMG_VERSION,
		0,
		int(image.width),
		int(image.height),
		len(image.frames),
		int(image.loop_count),
		table_offset,
		data_offset,
		total_words,
		0,
		0,
	]
	for record in frame_records:
		words.extend(record)
	words.extend(data)
	words[10] = _crc(words)
	return tuple(words)


def decode_ximg(words: tuple[int, ...] | list[int], *, max_words: int = XIMG_MAX_WORDS) -> PortableImage:
	values = [int(word) & 0xFFFFFFFF for word in words]
	if len(values) < XIMG_HEADER_WORDS:
		raise XIMGError("XIMG header is truncated")
	if values[0] != XIMG_MAGIC or values[1] != XIMG_VERSION:
		raise XIMGError("Unsupported XIMG magic or version")
	width, height, frame_count, loop_count = values[3:7]
	table_offset, data_offset, total_words, expected_crc = values[7:11]
	if total_words != len(values) or total_words > int(max_words):
		raise XIMGError("XIMG length is invalid or exceeds the decode budget")
	if expected_crc != _crc(values):
		raise XIMGError("XIMG checksum mismatch")
	if table_offset != XIMG_HEADER_WORDS or data_offset != table_offset + frame_count * XIMG_FRAME_WORDS:
		raise XIMGError("XIMG table offsets are invalid")
	if data_offset > total_words:
		raise XIMGError("XIMG data offset is outside the stream")
	placeholder = PortableImage(width, height, (ImageFrame((), 1),) * max(1, frame_count), loop_count)
	pixel_count = _validate_image_dimensions_only(placeholder, frame_count)
	frames: list[ImageFrame] = []
	delta_chain = 0
	for index in range(frame_count):
		start = table_offset + index * XIMG_FRAME_WORDS
		duration, encoding, offset, length, base_frame = values[start:start + XIMG_FRAME_WORDS]
		if duration <= 0 or offset > total_words - data_offset or length > total_words - data_offset - offset:
			raise XIMGError("XIMG frame table contains an invalid range")
		payload = values[data_offset + offset:data_offset + offset + length]
		if encoding == ENCODING_RAW:
			pixels = _unpack_pixels(payload, pixel_count)
			delta_chain = 0
		elif encoding == ENCODING_RLE:
			pixels = _decode_rle(payload, pixel_count)
			delta_chain = 0
		elif encoding == ENCODING_DELTA_RLE:
			if index == 0 or base_frame != index - 1 or delta_chain >= XIMG_MAX_DELTA_CHAIN - 1:
				raise XIMGError("XIMG delta chain is invalid")
			pixels = _decode_delta(payload, frames[-1].pixels)
			delta_chain += 1
		else:
			raise XIMGError(f"Unknown XIMG frame encoding {encoding}")
		frames.append(ImageFrame(pixels, duration))
	return PortableImage(width, height, tuple(frames), loop_count)


def _validate_image_dimensions_only(image: PortableImage, frame_count: int) -> int:
	if not 1 <= image.width <= XIMG_MAX_DIMENSION or not 1 <= image.height <= XIMG_MAX_DIMENSION:
		raise XIMGError("XIMG dimensions are invalid")
	if not 1 <= frame_count <= XIMG_MAX_FRAMES:
		raise XIMGError("XIMG frame count is invalid")
	pixel_count = image.width * image.height
	if pixel_count > XIMG_MAX_WORDS * 6:
		raise XIMGError("Decoded image exceeds the portable decode budget")
	return pixel_count


def _safe_xip_name(name: str) -> str:
	value = PurePosixPath(name)
	if value.is_absolute() or ".." in value.parts or str(value) in {"", "."}:
		raise XIMGError(f"Unsafe XIP member name: {name!r}")
	return str(value)


def write_xip(path: str | Path, manifest: dict[str, object], members: dict[str, bytes], *, overwrite: bool = False) -> None:
	output = Path(path)
	if output.exists() and not overwrite:
		raise XIMGError(f"Output already exists: {output}")
	payloads = {_safe_xip_name(name): bytes(value) for name, value in members.items()}
	if "manifest.json" in payloads:
		raise XIMGError("manifest.json is reserved")
	payloads["manifest.json"] = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
	output.parent.mkdir(parents=True, exist_ok=True)
	fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
	os.close(fd)
	temporary = Path(temporary_name)
	try:
		with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
			for name in sorted(payloads):
				info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
				info.compress_type = zipfile.ZIP_DEFLATED
				info.create_system = 0
				archive.writestr(info, payloads[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
		os.replace(temporary, output)
	finally:
		if temporary.exists():
			temporary.unlink()


def read_xip(path: str | Path, *, member_limit: int = 4096, byte_limit: int = 64 * 1024 * 1024) -> tuple[dict[str, object], dict[str, bytes]]:
	result: dict[str, bytes] = {}
	with zipfile.ZipFile(path, "r") as archive:
		infos = archive.infolist()
		if len(infos) > member_limit:
			raise XIMGError("XIP has too many members")
		total = 0
		for info in infos:
			name = _safe_xip_name(info.filename)
			if name in result:
				raise XIMGError(f"Duplicate XIP member: {name}")
			total += info.file_size
			if total > byte_limit:
				raise XIMGError("XIP exceeds the decoded byte limit")
			result[name] = archive.read(info)
	manifest_data = result.pop("manifest.json", None)
	if manifest_data is None:
		raise XIMGError("XIP has no manifest.json")
	try:
		manifest = json.loads(manifest_data)
	except (UnicodeDecodeError, json.JSONDecodeError) as error:
		raise XIMGError(f"Invalid XIP manifest: {error}") from error
	if not isinstance(manifest, dict):
		raise XIMGError("XIP manifest must be an object")
	return manifest, result
