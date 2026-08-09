from pathlib import Path

import pytest

from xe_lang.media import (
	ImageFrame,
	NoteEvent,
	PortableImage,
	Sequencer,
	Track,
	XIMGError,
	XMusicError,
	decode_ximg,
	decode_xmusic,
	encode_ximg,
	encode_xmusic,
	read_xip,
	write_xip,
)
from xe_lang.compiler_service import compile_source
from xe_lang.devices.assets import parse_word_stream
from xe_lang.devices.graphics import GraphicsDevice
from runtime import RuntimeContext, run


def test_ximg_round_trip_is_deterministic_and_preserves_transparency() -> None:
	frame_a = ImageFrame(tuple([0] * 24 + [16] * 24), 80)
	frame_b = ImageFrame(tuple([0] * 23 + [15] + [16] * 24), 120)
	image = PortableImage(8, 6, (frame_a, frame_b), loop_count=0)
	first = encode_ximg(image)
	second = encode_ximg(image)
	assert first == second
	assert len(first) < 12 + 10 + 16
	assert decode_ximg(first) == image


def test_ximg_rejects_corruption_and_invalid_pixels() -> None:
	image = PortableImage(2, 2, (ImageFrame((0, 1, 2, 16)),))
	words = list(encode_ximg(image))
	words[-1] ^= 1
	with pytest.raises(XIMGError, match="checksum"):
		decode_ximg(words)
	with pytest.raises(XIMGError, match="palette"):
		encode_ximg(PortableImage(1, 1, (ImageFrame((17,)),)))
	with pytest.raises(XIMGError, match="integers"):
		encode_ximg(PortableImage(1.5, 1, (ImageFrame((0,)),)))
	with pytest.raises(XIMGError, match="integer"):
		encode_ximg(PortableImage(1, 1, (ImageFrame((0,), 2.5),)))
	reserved = list(encode_ximg(image))
	reserved[11] = 1
	# Recompute the public-format checksum so this specifically exercises flags.
	from xe_lang.media.image_format import _crc
	reserved[10] = _crc(reserved)
	with pytest.raises(XIMGError, match="reserved"):
		decode_ximg(reserved)


def test_ximg_bounds_total_decoded_animation_pixels_and_word_types(monkeypatch) -> None:
	from xe_lang.media import image_format

	image = PortableImage(3, 2, (ImageFrame((0,) * 6), ImageFrame((1,) * 6)))
	words = encode_ximg(image)
	monkeypatch.setattr(image_format, "XIMG_MAX_DECODED_PIXELS", 8)
	with pytest.raises(XIMGError, match="decode budget"):
		encode_ximg(image)
	with pytest.raises(XIMGError, match="decode budget"):
		decode_ximg(words)
	with pytest.raises(XIMGError, match="integer words"):
		decode_ximg([0.0])


def test_xip_is_byte_deterministic_and_path_safe(tmp_path: Path) -> None:
	first = tmp_path / "first.xip"
	second = tmp_path / "second.xip"
	manifest = {"version": 1, "layers": ["ink"]}
	members = {"layers/ink.png": b"not-a-real-png"}
	write_xip(first, manifest, members)
	write_xip(second, manifest, members)
	assert first.read_bytes() == second.read_bytes()
	assert read_xip(first) == (manifest, members)
	with pytest.raises(XIMGError, match="Unsafe"):
		write_xip(tmp_path / "bad.xip", manifest, {"../escape": b"x"})
	for unsafe in (r"..\escape", "layers//ink.png", "./manifest-copy.json"):
		with pytest.raises(XIMGError, match="Unsafe"):
			write_xip(tmp_path / "bad.xip", manifest, {unsafe: b"x"})


def test_xmusic_round_trip_and_delta_time_sequencer() -> None:
	track = Track(
		120,
		480,
		(
			NoteEvent(0, 480, 60, 96, 0),
			NoteEvent(480, 480, 64, 96, 0),
		),
	)
	words = encode_xmusic(track)
	assert decode_xmusic(words) == track
	sequencer = Sequencer(track)
	sequencer.play()
	for _ in range(10):
		sequencer.advance(50)
	assert sequencer.position_ticks == pytest.approx(480)
	assert sequencer.active_notes() == (track.events[1],)
	sequencer.seek_fraction(1)
	assert sequencer.finished
	reserved = list(words)
	reserved[9] = 1
	from xe_lang.media.music_format import _crc
	reserved[8] = _crc(reserved)
	with pytest.raises(XMusicError, match="reserved"):
		decode_xmusic(reserved)


def test_xmusic_rejects_unsorted_events() -> None:
	track = Track(120, 480, (NoteEvent(10, 2, 60), NoteEvent(5, 2, 61)))
	with pytest.raises(XMusicError, match="sorted"):
		encode_xmusic(track)
	with pytest.raises(XMusicError, match="integers"):
		encode_xmusic(Track(120.5, 480, (NoteEvent(0, 2, 60),)))
	with pytest.raises(XMusicError, match="32-bit"):
		encode_xmusic(Track(120, 480, (NoteEvent(1 << 32, 1, 60),)))
	with pytest.raises(XMusicError, match="32-bit"):
		encode_xmusic(Track(120, 480, (NoteEvent(0xFFFFFFFF, 1, 60),)))
	with pytest.raises(XMusicError, match="integer words"):
		decode_xmusic([0.0])


def test_xmusic_uses_full_delta_and_rejects_unreachable_loops() -> None:
	track = Track(120, 480, (NoteEvent(0, 9_600, 60),))
	sequencer = Sequencer(track)
	sequencer.play()
	sequencer.advance(1_000)
	assert sequencer.position_ticks == pytest.approx(960)
	with pytest.raises(XMusicError, match="exceeds"):
		encode_xmusic(Track(120, 480, (NoteEvent(0, 480, 60),), 600, 900))
	with pytest.raises(XMusicError, match="requires"):
		encode_xmusic(Track(120, 480, (NoteEvent(0, 480, 60),), 100, 0))


def test_xmusic_active_note_index_handles_long_ended_prefix() -> None:
	events = tuple(NoteEvent(index * 10, 2, 60 + index % 12) for index in range(4_000))
	sequencer = Sequencer(Track(120, 480, events))
	sequencer.seek_ticks(39_991)
	assert sequencer.active_notes() == (events[-1],)


def test_portable_word_stream_supports_line_comments_and_enforces_budget() -> None:
	assert parse_word_stream("0x1, 2 # ignored words\n3") == (1, 2, 3)
	with pytest.raises(ValueError, match="exceeds 2"):
		parse_word_stream("1 2 3", max_words=2)


def test_indexed_sprite_blit_scales_clips_and_preserves_transparent_pixels() -> None:
	graphics = GraphicsDevice(7, 5)
	graphics.clear(3)
	graphics.set_clip(1, 1, 5, 3)
	graphics.draw_indexed_pixels(0, 0, 3, 2, (1, 16, 2, 4, 5, 6), scale=2)
	assert graphics.back_buffer[1][1] == 1
	assert graphics.back_buffer[1][2:4] == bytes((3, 3))
	assert graphics.back_buffer[1][4] == 2
	assert graphics.back_buffer[2][1] == 4
	assert graphics.back_buffer[3][1] == 4
	assert graphics.back_buffer[3][2:4] == bytes((5, 5))


def test_graphics_image_and_audio_track_are_first_class_xe_resources() -> None:
	image_source = '''
var image: graphics::Image
image = graphics::load_image("sprite.ximg")
out << graphics::image_width(image)
'''
	image_artifact = compile_source(image_source)
	assert image_artifact.success, image_artifact.diagnostics
	assert "app.graphics" in image_artifact.required_capabilities

	track_source = '''
var track: audio::Track
track = audio::load("demo.xmusic")
out << audio::duration(track)
'''
	track_artifact = compile_source(track_source)
	assert track_artifact.success, track_artifact.diagnostics
	assert "app.audio" in track_artifact.required_capabilities


def test_xe_runtime_loads_portable_image_and_music_assets(tmp_path: Path) -> None:
	drive = tmp_path / "drive"
	drive.mkdir()
	image = PortableImage(
		3,
		2,
		(
			ImageFrame((0, 1, 2, 3, 4, 16), 80),
			ImageFrame((5, 6, 7, 8, 9, 16), 125),
		),
	)
	(drive / "sprite.ximg").write_text("\n".join(hex(word) for word in encode_ximg(image)), encoding="utf-8")
	track = Track(120, 480, (NoteEvent(0, 960, 60),))
	(drive / "demo.xmusic").write_text("\n".join(hex(word) for word in encode_xmusic(track)), encoding="utf-8")

	context = RuntimeContext(filesystem_root=drive)
	output: list[str] = []
	context.output_handler = output.append
	image_source = '''
var image: graphics::Image
image = graphics::load_image("sprite.ximg")
out << graphics::image_width(image)
out << graphics::image_frame_count(image)
out << graphics::image_frame_duration(image, 1)
'''
	_, error, _ = run("image_test.xe", image_source, context)
	assert error is None
	assert "".join(output) == "32125"

	context = RuntimeContext(filesystem_root=drive)
	output = []
	context.output_handler = output.append
	track_source = '''
var track: audio::Track
track = audio::load("demo.xmusic")
out << audio::duration(track)
'''
	_, error, _ = run("audio_test.xe", track_source, context)
	assert error is None
	assert "".join(output) == "960"
