from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import unittest

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "apps" / "minesweeper.xe"


class MinesweeperTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls) -> None:
		cls.source = SOURCE_PATH.read_text(encoding="utf-8")

	def run_probe(self, probe: str) -> str:
		anchor = "while (mine_window.state != graphics::WINDOW_CLOSED)"
		self.assertIn(anchor, self.source)
		source = self.source.replace(anchor, f"{probe}\nwhile (false)", 1)
		parts: list[str] = []
		context = RuntimeContext()
		context.output_handler = parts.append
		with redirect_stdout(StringIO()):
			_, error, _ = run("minesweeper-probe.xe", source, context)
		self.assertIsNone(error, str(error))
		return "".join(parts)

	def test_source_compiles(self) -> None:
		artifact = compile_source(self.source, str(SOURCE_PATH))
		self.assertTrue(artifact.success, "\n".join(map(str, artifact.diagnostics)))

	def test_every_preset_places_exact_mines_and_keeps_first_click_safe(self) -> None:
		output = self.run_probe(
			'''var probe_difficulty: int
var probe_index: int
var probe_count: int
probe_difficulty = 0
while (probe_difficulty < 3) {
	call mine_apply_difficulty(probe_difficulty)
	mine_seed = 131 + probe_difficulty
	call mine_reset()
	call mine_place_exact(mine_columns / 2, mine_rows / 2)
	probe_index = 0
	probe_count = 0
	while (probe_index < mine_total) {
		probe_count += mine_cells[probe_index]
		probe_index += 1
	}
	out << probe_count
	out << ","
	out << mine_in_safe_area(mine_index(mine_columns / 2, mine_rows / 2), mine_columns / 2, mine_rows / 2)
	out << ";"
	probe_difficulty += 1
}'''
		)
		self.assertEqual("10,-1;40,-1;99,-1;", output)

	def test_first_click_three_by_three_area_contains_no_mines(self) -> None:
		output = self.run_probe(
			'''var probe_x: int
var probe_y: int
var probe_safe_mines: int
call mine_apply_difficulty(MINE_EXPERT)
mine_seed = 777
call mine_reset()
call mine_place_exact(15, 8)
probe_y = 7
probe_safe_mines = 0
while (probe_y <= 9) {
	probe_x = 14
	while (probe_x <= 16) {
		probe_safe_mines += mine_cells[mine_index(probe_x, probe_y)]
		probe_x += 1
	}
	probe_y += 1
}
out << probe_safe_mines'''
		)
		self.assertEqual("0", output)

	def test_seed_is_deterministic_and_flags_are_bounded(self) -> None:
		output = self.run_probe(
			'''var probe_index: int
var probe_hash_a: int
var probe_hash_b: int
call mine_apply_difficulty(MINE_BEGINNER)
mine_seed = 2026
call mine_reset()
call mine_place_exact(4, 4)
probe_index = 0
probe_hash_a = 0
while (probe_index < mine_total) {
	probe_hash_a = (probe_hash_a * 17 + mine_cells[probe_index]) % 1000003
	probe_index += 1
}
call mine_reset()
call mine_place_exact(4, 4)
probe_index = 0
probe_hash_b = 0
while (probe_index < mine_total) {
	probe_hash_b = (probe_hash_b * 17 + mine_cells[probe_index]) % 1000003
	probe_index += 1
}
call mine_toggle_flag(0, 0)
call mine_toggle_flag(0, 0)
out << probe_hash_a
out << ","
out << probe_hash_b
out << ","
out << mine_flags_used'''
		)
		first, second, flags = output.split(",")
		self.assertEqual(first, second)
		self.assertEqual("0", flags)

	def test_flood_fill_is_iterative_and_scratch_portable(self) -> None:
		self.assertIn("array mine_queue: int[480]", self.source)
		self.assertIn("while (head < tail)", self.source)
		self.assertNotIn("call mine_reveal_cell(current_column", self.source)
		self.assertIn("mine_flag_mode", self.source)
		self.assertNotRegex(self.source.lower(), r"thread|socket|subprocess|numpy")

	def test_loss_reveals_mines_and_freezes_finish_time(self) -> None:
		output = self.run_probe(
			'''call mine_apply_difficulty(MINE_BEGINNER)
call mine_reset()
mine_first_reveal = false
mine_start_ticks = os::ticks()
mine_cells[0] = 1
call mine_reveal_cell(0, 0)
out << mine_state
out << ","
out << mine_revealed[0]
out << ","
out << (mine_finish_ticks >= mine_start_ticks)'''
		)
		self.assertEqual("2,1,-1", output)

	def test_first_reveal_builds_the_board_and_reveals_a_safe_cell(self) -> None:
		output = self.run_probe(
			'''call mine_apply_difficulty(MINE_BEGINNER)
mine_seed = 99
call mine_reset()
call mine_reveal_cell(4, 4)
out << mine_first_reveal
out << ","
out << mine_cells[mine_index(4, 4)]
out << ","
out << mine_revealed[mine_index(4, 4)]
out << ","
out << mine_state'''
		)
		self.assertEqual("0,0,1,0", output)

	def test_correct_chord_clears_neighbors_and_wrong_chord_loses(self) -> None:
		output = self.run_probe(
			'''call mine_apply_difficulty(MINE_BEGINNER)
call mine_reset()
mine_count = 1
mine_first_reveal = false
mine_cells[0] = 1
mine_revealed[1] = 1
mine_revealed_count = 1
mine_flagged[0] = 1
mine_flags_used = 1
call mine_chord(1, 0)
out << mine_state
out << ","
out << mine_revealed[2]
out << ";"
call mine_reset()
mine_count = 1
mine_first_reveal = false
mine_cells[0] = 1
mine_revealed[1] = 1
mine_revealed_count = 1
mine_flagged[2] = 1
mine_flags_used = 1
call mine_chord(1, 0)
out << mine_state
out << ","
out << mine_revealed[0]'''
		)
		self.assertEqual("1,1;2,1", output)

	def test_flag_limit_and_compact_frame_are_bounded(self) -> None:
		output = self.run_probe(
			'''var probe_flag: int
call mine_apply_difficulty(MINE_BEGINNER)
call mine_reset()
probe_flag = 0
while (probe_flag < 12) {
	call mine_toggle_flag(probe_flag % mine_columns, probe_flag / mine_columns)
	probe_flag += 1
}
out << mine_flags_used'''
		)
		self.assertEqual("10", output)

		compact = self.source.replace("mine_window.width = APP_DEFAULT_WIDTH", "mine_window.width = 120", 1)
		compact = compact.replace("mine_window.height = APP_DEFAULT_HEIGHT", "mine_window.height = 82", 1)
		compact = compact.replace(
			"while (mine_window.state != graphics::WINDOW_CLOSED) {",
			"var probe_frame: int\nprobe_frame = 0\nwhile (probe_frame < 1) {",
			1,
		)
		compact = compact.replace(
			"call graphics::update(mine_window)",
			"call graphics::update(mine_window)\n\tprobe_frame += 1",
			1,
		)
		frames = []
		context = RuntimeContext(frame_handler=frames.append)
		with redirect_stdout(StringIO()):
			_, error, _ = run("minesweeper-compact.xe", compact, context)
		self.assertIsNone(error, str(error))
		self.assertEqual(1, len(frames))
		self.assertEqual((480, 360), (frames[0].width, frames[0].height))
		self.assertGreater(sum(color != 0 for color in frames[0].indices), 250)

	def test_polished_tiles_and_keyboard_qol_are_present(self) -> None:
		for marker in (
			"mine_draw_flag",
			"mine_draw_mine",
			"mine_number_color",
			"graphics::KEY_ENTER",
			"graphics::KEY_SPACE",
			"mine_focus_visible",
			"mine_scroll_delta = graphics::scroll_delta()",
			"(graphics::modifiers() & graphics::MOD_SHIFT) != 0",
			"mine_focus_column -= mine_scroll_delta",
			"mine_focus_row -= mine_scroll_delta",
		):
			self.assertIn(marker, self.source)


if __name__ == "__main__":
	unittest.main()
