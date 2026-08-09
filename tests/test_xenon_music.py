from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source


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
		self.assertIn('"No .xmusic discs"', self.source)
		self.assertNotRegex(self.source.lower(), r"socket|request\(|http|thread|subprocess")


if __name__ == "__main__":
	unittest.main()
