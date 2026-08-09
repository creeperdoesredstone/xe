from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source
from xe_lang.devices.input import LEFT_BUTTON


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "apps" / "xenon_music.xe"


class XenonMusicTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls) -> None:
		cls.source = SOURCE_PATH.read_text(encoding="utf-8")

	def run_probe(self, probe: str) -> str:
		anchor = "while (music_window.state != graphics::WINDOW_CLOSED)"
		self.assertIn(anchor, self.source)
		source = self.source.replace(anchor, f"{probe}\nwhile (false)", 1)
		parts: list[str] = []
		with tempfile.TemporaryDirectory() as drive:
			context = RuntimeContext(filesystem_root=drive)
			context.output_handler = parts.append
			with redirect_stdout(StringIO()):
				_, error, _ = run("xenon-music-probe.xe", source, context)
		self.assertIsNone(error, str(error))
		return "".join(parts)

	def test_source_compiles(self) -> None:
		artifact = compile_source(self.source, str(SOURCE_PATH))
		self.assertTrue(artifact.success, "\n".join(map(str, artifact.diagnostics)))

	def test_tracks_are_deterministic_sequenced_events(self) -> None:
		output = self.run_probe(
			'''out << music_manifest
out << "|"
out << music_track_totals[0]
out << ","
out << music_track_totals[1]
out << ","
out << music_track_totals[2]'''
		)
		self.assertTrue(output.startswith("XMUSIC1|ppq=96|encoding=note,duration"))
		totals = output.rsplit("|", 1)[1]
		self.assertEqual("4800,3200,5800", totals)
		self.assertNotRegex(self.source.lower(), r"\.(mp3|wav|ogg|flac)|audio[_ -]?chunk")

	def test_seek_wraps_in_both_directions(self) -> None:
		output = self.run_probe(
			'''call music_select_track(1)
call music_seek(4000)
out << music_playhead_ms
out << ","
call music_seek(-1000)
out << music_playhead_ms'''
		)
		self.assertEqual("800,3000", output)

	def test_demo_disc_is_checksum_valid_and_drives_portable_sequencer(self) -> None:
		output = self.run_probe(
			'''out << music_audio_available
out << ","
out << music_audio_duration
out << "|"
call music_set_playing(true)
music_delta_ms = 50
call music_update_sequencer()
out << music_playing
out << ","
out << audio::position(music_audio_track)'''
		)
		available, playback = output.split("|", 1)
		self.assertEqual("-1,960", available)
		playing, position = playback.split(",", 1)
		self.assertEqual("-1", playing)
		self.assertGreater(int(position), 0)

	def test_vinyl_interactions_and_portable_audio_are_present(self) -> None:
		for marker in (
			"music_pointer_angle",
			"music_normalize_angle",
			"music_scrubbing",
			"music_disc_dragging",
			"music_inventory_drag_track",
			"music_update_sequencer",
			"music_resume_after_scrub",
			"audio::load",
			"audio::update",
			"audio::seek",
		):
			self.assertIn(marker, self.source)
		self.assertIn('"XMusic note playback"', self.source)
		self.assertIn("music_small_text_width", self.source)
		self.assertIn('"Platter empty"', self.source)
		self.assertNotRegex(self.source.lower(), r"socket|request\(|http|thread|subprocess")

	def test_note_engine_remains_a_no_audio_fallback(self) -> None:
		output = self.run_probe(
			'''music_audio_available = false
music_disc_inserted = true
music_playing = true
music_playhead_ms = 0
music_delta_ms = 125
call music_update_sequencer()
out << music_playhead_ms
out << ","
out << music_current_step
out << ","
out << music_playing'''
		)
		self.assertEqual("125,0,-1", output)

	def test_whole_disc_motion_and_real_tonearm_geometry_are_explicit(self) -> None:
		for marker in (
			"music_draw_tonearm",
			"music_draw_empty_platter",
			"music_render_disc_x",
			"music_render_disc_y",
			"music_disc_drag_offset_x",
			"music_disc_drag_offset_y",
			"music_disc_drag_was_playing",
			"rotation + mark * 120",
			"release outside to shelve",
		):
			self.assertIn(marker, self.source)
		self.assertNotIn("marker_x", self.source)
		self.assertNotIn("marker_y", self.source)

		output = self.run_probe(
			'''var probe_angle: float
probe_angle = music_normalize_angle(350.0)
out << (int)probe_angle
out << ","
probe_angle = music_normalize_angle(-350.0)
out << (int)probe_angle'''
		)
		self.assertEqual("-10,10", output)

	def test_lift_drag_moves_the_full_record_with_the_pointer(self) -> None:
		source = self.source.replace(
			"while (music_window.state != graphics::WINDOW_CLOSED) {",
			"var probe_frame: int\nprobe_frame = 0\nwhile (probe_frame < 4) {",
			1,
		)
		source = source.replace(
			"call graphics::update(music_window)",
			"call graphics::update(music_window)\n\tprobe_frame += 1",
			1,
		)
		frames = []
		context: RuntimeContext

		def on_frame(frame) -> None:
			frames.append(frame)
			windows = context.vm.devices.windows
			origin_x = windows.content_x(1)
			origin_y = windows.content_y(1)
			if len(frames) == 1:
				context.vm.devices.input.move_pointer(origin_x + 149, origin_y + 105)
				context.vm.devices.input.set_button(LEFT_BUTTON, True)
			elif len(frames) == 2:
				context.vm.devices.input.move_pointer(origin_x + 204, origin_y + 105)
			elif len(frames) == 3:
				context.vm.devices.input.set_button(LEFT_BUTTON, False)

		with tempfile.TemporaryDirectory() as drive:
			context = RuntimeContext(filesystem_root=drive, frame_handler=on_frame)
			with redirect_stdout(StringIO()):
				_, error, _ = run("xenon-music-drag.xe", source, context)
		self.assertIsNone(error, str(error))
		self.assertEqual(4, len(frames))

		origin_x = context.vm.devices.windows.content_x(1)
		origin_y = context.vm.devices.windows.content_y(1)
		def accent_center(frame) -> float:
			xs = []
			for y in range(origin_y + 25, origin_y + 185):
				for x in range(origin_x + 3, origin_x + 290):
					if frame.indices[y * frame.width + x] == 10:
						xs.append(x)
			self.assertGreater(len(xs), 30)
			return sum(xs) / len(xs)

		self.assertGreater(accent_center(frames[2]) - accent_center(frames[0]), 35.0)

	def test_normal_compact_and_open_library_frames_render(self) -> None:
		for width, height, setup in (
			(300, 224, ""),
			(170, 106, ""),
			(300, 224, "music_inventory_open = true\n"),
		):
			with self.subTest(width=width, height=height, setup=bool(setup)):
				source = self.source.replace("music_window.width = APP_DEFAULT_WIDTH", f"music_window.width = {width}", 1)
				source = source.replace("music_window.height = APP_DEFAULT_HEIGHT", f"music_window.height = {height}", 1)
				source = source.replace(
					"while (music_window.state != graphics::WINDOW_CLOSED) {",
					f"{setup}var probe_frame: int\nprobe_frame = 0\nwhile (probe_frame < 1) {{",
					1,
				)
				source = source.replace(
					"call graphics::update(music_window)",
					"call graphics::update(music_window)\n\tprobe_frame += 1",
					1,
				)
				frames = []
				with tempfile.TemporaryDirectory() as drive:
					context = RuntimeContext(filesystem_root=drive, frame_handler=frames.append)
					with redirect_stdout(StringIO()):
						_, error, _ = run("xenon-music-frame.xe", source, context)
				self.assertIsNone(error, str(error))
				self.assertEqual(1, len(frames))
				self.assertGreater(sum(color != 0 for color in frames[0].indices), 300)

	def test_narrow_header_elides_track_before_reserved_play_status(self) -> None:
		self.assertIn("proc draw_music_text_elided", self.source)
		self.assertIn("music_header_right = music_deck_width - 42", self.source)
		self.assertIn("if (music_deck_width >= 140)", self.source)
		source = self.source.replace('return "Neon Orbit"', 'return "A Very Long Demonstration Record"', 1)
		source = source.replace("music_window.width = APP_DEFAULT_WIDTH", "music_window.width = 150", 1)
		source = source.replace("music_window.height = APP_DEFAULT_HEIGHT", "music_window.height = 106", 1)
		source = source.replace(
			"while (music_window.state != graphics::WINDOW_CLOSED) {",
			"var header_frame: int\nheader_frame = 0\nwhile (header_frame < 1) {",
			1,
		)
		source = source.replace(
			"call graphics::update(music_window)",
			"call graphics::update(music_window)\n\theader_frame += 1",
			1,
		)
		frames = []
		with tempfile.TemporaryDirectory() as drive:
			context = RuntimeContext(filesystem_root=drive, frame_handler=frames.append)
			with redirect_stdout(StringIO()):
				_, error, _ = run("xenon-music-header.xe", source, context)
		self.assertIsNone(error, str(error))
		self.assertEqual(1, len(frames))
		origin_x = context.vm.devices.windows.content_x(1)
		origin_y = context.vm.devices.windows.content_y(1)
		frame = frames[0]
		track_pixels = 0
		reserved_pixels = 0
		for y in range(origin_y + 4, origin_y + 11):
			for x in range(origin_x + 64, origin_x + 108):
				track_pixels += frame.indices[y * frame.width + x] == 10
			for x in range(origin_x + 108, origin_x + 146):
				reserved_pixels += frame.indices[y * frame.width + x] == 10
		self.assertGreater(track_pixels, 0)
		self.assertEqual(0, reserved_pixels)


if __name__ == "__main__":
	unittest.main()
