from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import unittest

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source


ROOT = Path(__file__).resolve().parents[1]
CALCULATOR_PATH = ROOT / "apps" / "calculator.xe"
SETTINGS_PATH = ROOT / "apps" / "settings.xe"


def run_probe(source: str, anchor: str, probe: str, filename: str) -> str:
	if anchor not in source:
		raise AssertionError(f"Probe anchor is missing from {filename}: {anchor}")
	modified = source.replace(anchor, f"{probe}\nif (false) {{", 1)
	parts: list[str] = []
	context = RuntimeContext()
	context.output_handler = parts.append
	with redirect_stdout(StringIO()):
		_, error, _ = run(filename, modified, context)
	if error is not None:
		raise AssertionError(str(error))
	return "".join(parts)


def render_one_frame(
	source: str,
	filename: str,
	target: str,
	default_size: tuple[int, int],
	size: tuple[int, int],
) -> int:
	default_width, default_height = default_size
	width, height = size
	loop = f"while ({target}.state != graphics::WINDOW_CLOSED) {{"
	update = f"call graphics::update({target})"
	modified = source.replace(f"{target}.width = {default_width}", f"{target}.width = {width}", 1)
	modified = modified.replace(f"{target}.height = {default_height}", f"{target}.height = {height}", 1)
	modified = modified.replace(loop, "var probe_frame: int\nprobe_frame = 0\nwhile (probe_frame < 1) {", 1)
	modified = modified.replace(update, f"{update}\n\t\tprobe_frame += 1", 1)
	frames = []
	context = RuntimeContext(frame_handler=frames.append)
	with redirect_stdout(StringIO()):
		_, error, _ = run(filename, modified, context)
	if error is not None:
		raise AssertionError(str(error))
	if len(frames) != 1:
		raise AssertionError(f"Expected one rendered frame, received {len(frames)}")
	return sum(1 for color in frames[0].indices if color)


class CalculatorTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls) -> None:
		cls.source = CALCULATOR_PATH.read_text(encoding="utf-8")

	def probe(self, code: str) -> str:
		return run_probe(self.source, "if (graphical_mode) {", code, "calculator-probe.xe")

	def test_source_compiles(self) -> None:
		artifact = compile_source(self.source, str(CALCULATOR_PATH))
		self.assertTrue(artifact.success, "\n".join(map(str, artifact.diagnostics)))

	def test_percentage_power_precedence_and_scientific_contract(self) -> None:
		output = self.probe(
			'''var probe_error: int
var probe_position: int
var probe_value: float
probe_value = evaluate_expression("9%", true, &probe_error, &probe_position)
out << probe_error
out << ":"
out << (int)(probe_value * 1000.0)
out << ","
probe_value = evaluate_expression("9%2", true, &probe_error, &probe_position)
out << probe_error
out << ":"
out << (int)probe_value
out << ","
probe_value = evaluate_expression("-2**2", true, &probe_error, &probe_position)
out << probe_error
out << ":"
out << (int)probe_value
out << ","
probe_value = evaluate_expression("2**3**2", true, &probe_error, &probe_position)
out << probe_error
out << ":"
out << (int)probe_value
out << ","
probe_value = evaluate_expression("asin(1) + acos(1) + atan(1)", true, &probe_error, &probe_position)
out << probe_error
out << ":"
out << (int)probe_value
out << ","
probe_value = evaluate_expression("sqrt(81) + pow(2, 3)", true, &probe_error, &probe_position)
out << probe_error
out << ":"
out << (int)probe_value'''
		)
		self.assertEqual("0:90,0:1,0:-4,0:512,0:135,0:17", output)

	def test_domains_and_positional_errors_remain_deterministic(self) -> None:
		output = self.probe(
			'''var probe_error: int
var probe_position: int
var probe_value: float
probe_value = evaluate_expression("sqrt(-1)", true, &probe_error, &probe_position)
out << probe_error
out << ","
probe_value = evaluate_expression("1/0", true, &probe_error, &probe_position)
out << probe_error
out << ","
probe_value = evaluate_expression("unknown(2)", true, &probe_error, &probe_position)
out << probe_error
out << ","
probe_value = evaluate_expression("1 + )", true, &probe_error, &probe_position)
out << probe_error
out << ","
out << probe_position'''
		)
		self.assertEqual("6,5,3,1,4", output)

	def test_ctrl_selection_clipboard_operations_are_lossless(self) -> None:
		output = self.probe(
			'''call set_calculator_text(expression, "sqrt(81)")
call calculator_select_all()
call calculator_copy_selection()
call clear_editor(expression)
call calculator_paste_clipboard()
out << expression
out << "|"
call calculator_select_all()
call calculator_cut_selection()
out << expression
out << "|"
call calculator_paste_clipboard()
out << expression'''
		)
		self.assertEqual("sqrt(81)||sqrt(81)", output)

	def test_currency_formatter_never_exceeds_its_assigned_column(self) -> None:
		output = self.probe(
			'''call fit_currency_number(calculator_number_text, 987654336.0, 18)
out << calculator_small_text_width(calculator_number_text)
out << ":"
out << calculator_number_text
out << ","
call fit_currency_number(calculator_number_text, 0.00000034, 22)
out << calculator_small_text_width(calculator_number_text)
out << ":"
out << calculator_number_text'''
		)
		large, small = output.split(",", 1)
		large_width, large_label = large.split(":", 1)
		small_width, small_label = small.split(":", 1)
		self.assertLessEqual(int(large_width), 18)
		self.assertLessEqual(int(small_width), 22)
		self.assertTrue(large_label)
		self.assertTrue(small_label)

	def test_ui_has_one_power_key_and_stable_responsive_density(self) -> None:
		self.assertEqual(1, self.source.count('key_height, "**",'))
		self.assertIn("call draw_scientific_keypad", self.source)
		self.assertIn("panel_width > 360", self.source)
		self.assertNotIn("calculator_window.is_fullscreen()", self.source)
		self.assertEqual(1, self.source.count("calculator_window.ui_scale = 1"))
		self.assertIn("hover_x = pointer_x", self.source)
		self.assertIn("call fit_currency_number", self.source)
		self.assertIn("*menu_open && *progress == 100", self.source)

	def test_normal_and_large_windows_render_complete_frames(self) -> None:
		normal = render_one_frame(self.source, "calculator-frame.xe", "calculator_window", (240, 190), (240, 190))
		large = render_one_frame(self.source, "calculator-frame.xe", "calculator_window", (240, 190), (400, 300))
		self.assertGreater(normal, 20_000)
		self.assertGreater(large, normal * 2)


class SettingsTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls) -> None:
		cls.source = SETTINGS_PATH.read_text(encoding="utf-8")

	def probe(self, code: str) -> str:
		return run_probe(
			self.source,
			"while (settings_window.state != graphics::WINDOW_CLOSED) {",
			code,
			"settings-probe.xe",
		)

	def test_source_compiles(self) -> None:
		artifact = compile_source(self.source, str(SETTINGS_PATH))
		self.assertTrue(artifact.success, "\n".join(map(str, artifact.diagnostics)))

	def test_preferences_are_staged_then_applied_atomically(self) -> None:
		output = self.probe(
			'''out << os::window_transparency
out << ","
staged_transparency = 73
out << os::window_transparency
out << ","
applied = os::apply_preferences(staged_master, staged_music, staged_effects, staged_background, staged_palette, staged_theme, staged_transparency, staged_corners, staged_icons, staged_clock, staged_enabled)
out << applied
out << ","
out << os::window_transparency
out << ","
call reload_os_settings(&staged_master, &staged_music, &staged_effects, &staged_background, &staged_palette, &staged_theme, &staged_transparency, &staged_corners, &staged_icons, &staged_clock, &staged_enabled)
out << staged_transparency'''
		)
		self.assertEqual("0,0,-1,73,73", output)

	def test_cancel_reload_discards_staged_values(self) -> None:
		output = self.probe(
			'''staged_master = 1
staged_transparency = 99
call reload_os_settings(&staged_master, &staged_music, &staged_effects, &staged_background, &staged_palette, &staged_theme, &staged_transparency, &staged_corners, &staged_icons, &staged_clock, &staged_enabled)
out << staged_master
out << ","
out << staged_transparency'''
		)
		self.assertEqual("70,0", output)

	def test_navigation_is_a_push_drawer_not_an_overlay_dropdown(self) -> None:
		self.assertIn("panel_x = drawer_width + 8", self.source)
		self.assertIn("drawer_target_width = 100", self.source)
		self.assertIn("drawer_eased = ease_menu(drawer_progress)", self.source)
		self.assertIn("drawer_step = delta_ms * 100 / 200", self.source)
		self.assertIn("0 opaque - 100 clear", self.source)
		self.assertNotIn("active_tab = 0; drawer_open = false", self.source)
		self.assertNotIn("active_tab = 4; drawer_open = false", self.source)
		self.assertIn("drawer_surface = graphics::COLOR_7", self.source)

	def test_normal_and_large_windows_render_complete_frames(self) -> None:
		normal = render_one_frame(self.source, "settings-frame.xe", "settings_window", (280, 210), (280, 210))
		large = render_one_frame(self.source, "settings-frame.xe", "settings_window", (280, 210), (400, 300))
		self.assertGreater(normal, 30_000)
		self.assertGreater(large, normal * 2)


if __name__ == "__main__":
	unittest.main()
