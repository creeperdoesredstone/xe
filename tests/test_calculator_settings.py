from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import unittest

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source
from xe_lang.devices import OSDevice, OSSettings, PALETTES


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
	modified = source.replace(f"{target}.width = APP_DEFAULT_WIDTH", f"{target}.width = {width}", 1)
	modified = modified.replace(f"{target}.height = APP_DEFAULT_HEIGHT", f"{target}.height = {height}", 1)
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


def render_settings_frame(
	source: str,
	size: tuple[int, int],
	active_tab: int,
	staged_values: str = "",
	os_settings: OSSettings | None = None,
):
	width, height = size
	loop = "while (settings_window.state != graphics::WINDOW_CLOSED) {"
	modified = source.replace("settings_window.width = APP_DEFAULT_WIDTH", f"settings_window.width = {width}", 1)
	modified = modified.replace("settings_window.height = APP_DEFAULT_HEIGHT", f"settings_window.height = {height}", 1)
	modified = modified.replace(
		"active_tab = 0\n",
		f"active_tab = {active_tab}\n{staged_values}\n",
		1,
	)
	modified = modified.replace(loop, "var preview_probe_frame: int\npreview_probe_frame = 0\nwhile (preview_probe_frame < 1) {", 1)
	modified = modified.replace(
		"call graphics::update(settings_window)",
		"call graphics::update(settings_window)\n\tpreview_probe_frame += 1",
		1,
	)
	frames = []
	device = OSDevice(settings=os_settings) if os_settings is not None else None
	context = RuntimeContext(os_device=device, frame_handler=frames.append)
	with redirect_stdout(StringIO()):
		_, error, _ = run("settings-preview-frame.xe", modified, context)
	if error is not None:
		raise AssertionError(str(error))
	if len(frames) != 1:
		raise AssertionError(f"Expected one rendered frame, received {len(frames)}")
	return frames[0]


def frame_crop(frame, left: int, top: int, right: int, bottom: int) -> bytes:
	rows = []
	for y in range(top, bottom):
		start = y * frame.width + left
		rows.append(frame.indices[start:start + right - left])
	return b"".join(rows)


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
			'''out << os::background_id
out << ","
staged_background = 2
out << os::background_id
out << ","
applied = os::apply_preferences(staged_master, staged_music, staged_effects, staged_background, staged_palette, staged_theme, 0, staged_corners, staged_icons, staged_clock, staged_enabled)
out << applied
out << ","
out << os::background_id
out << ","
call reload_os_settings(&staged_master, &staged_music, &staged_effects, &staged_background, &staged_palette, &staged_theme, &staged_corners, &staged_icons, &staged_clock, &staged_enabled)
out << staged_background'''
		)
		self.assertEqual("0,0,-1,2,2", output)

	def test_cancel_reload_discards_staged_values(self) -> None:
		output = self.probe(
			'''staged_master = 1
staged_background = 2
call reload_os_settings(&staged_master, &staged_music, &staged_effects, &staged_background, &staged_palette, &staged_theme, &staged_corners, &staged_icons, &staged_clock, &staged_enabled)
out << staged_master
out << ","
out << staged_background'''
		)
		self.assertEqual("70,0", output)

	def test_navigation_is_a_push_drawer_not_an_overlay_dropdown(self) -> None:
		self.assertIn("panel_x = drawer_width + 8", self.source)
		self.assertIn("drawer_target_width = 100", self.source)
		self.assertIn("drawer_eased = ease_menu(drawer_progress)", self.source)
		self.assertIn("drawer_step = delta_ms * 100 / 200", self.source)
		self.assertNotIn('"Transparency', self.source)
		self.assertNotIn("graphics::slider(settings_window, control_x, 31, control_width", self.source)
		self.assertNotIn("active_tab = 0; drawer_open = false", self.source)
		self.assertNotIn("active_tab = 4; drawer_open = false", self.source)
		self.assertIn("drawer_surface = graphics::COLOR_7", self.source)

	def test_live_previews_use_staged_values_and_centered_settings_controls(self) -> None:
		self.assertIn("proc draw_window_preview", self.source)
		self.assertIn("call draw_preview_box(preview_x, preview_y, preview_width, preview_height, staged_corners", self.source)
		self.assertIn("proc draw_palette_background_preview", self.source)
		self.assertIn("background = settings_preview_background()", self.source)
		self.assertIn("variant = staged_palette % palette_group_count", self.source)
		self.assertIn("if (staged_background == 2)", self.source)
		self.assertIn("proc draw_icons_time_preview", self.source)
		self.assertIn("staged_icons == os::ICON_SMALL", self.source)
		self.assertIn("staged_clock == os::CLOCK_12_HOUR", self.source)
		self.assertIn("fn settings_flat_button_aligned", self.source)
		self.assertIn("call draw_centered_button_label", self.source)
		self.assertIn("pointer_x >= x && pointer_x < x + width", self.source)
		self.assertIn("os::theme_mode == os::THEME_LIGHT", self.source)

	def test_preview_frames_change_before_apply_and_stay_clear_of_actions(self) -> None:
		icons_24 = render_settings_frame(
			self.source,
			(280, 210),
			3,
			"staged_icons = os::ICON_SMALL\nstaged_clock = os::CLOCK_24_HOUR",
		)
		icons_12 = render_settings_frame(
			self.source,
			(280, 210),
			3,
			"staged_icons = os::ICON_LARGE\nstaged_clock = os::CLOCK_12_HOUR",
		)
		icon_preview_24 = frame_crop(icons_24, 80, 136, 314, 222)
		icon_preview_12 = frame_crop(icons_12, 80, 136, 314, 222)
		self.assertNotEqual(icon_preview_24, icon_preview_12)
		self.assertGreaterEqual(len(set(icon_preview_24)), 4)

		window_square = render_settings_frame(
			self.source,
			(280, 210),
			2,
			"staged_corners = os::CORNER_SQUARE",
		)
		window_soft = render_settings_frame(
			self.source,
			(280, 210),
			2,
			"staged_corners = os::CORNER_SOFT",
		)
		window_preview_square = frame_crop(window_square, 80, 102, 314, 222)
		window_preview_soft = frame_crop(window_soft, 80, 102, 314, 222)
		self.assertNotEqual(window_preview_square, window_preview_soft)
		personal_dark = render_settings_frame(
			self.source,
			(280, 210),
			1,
			"staged_background = 0\nstaged_palette = 0",
		)
		personal_slate = render_settings_frame(
			self.source,
			(280, 210),
			1,
			"staged_background = 2\nstaged_palette = 2",
		)
		self.assertNotEqual(frame_crop(personal_dark, 80, 134, 314, 222), frame_crop(personal_slate, 80, 134, 314, 222))
		# The action row starts at absolute y=226 for the normal logical window.
		self.assertEqual({1}, set(frame_crop(window_soft, 80, 222, 314, 226)))

	def test_previews_render_at_narrow_normal_and_large_sizes(self) -> None:
		for size in ((180, 130), (280, 210), (400, 300)):
			with self.subTest(size=size, tab="appearance"):
				frame = render_settings_frame(
					self.source,
					size,
					2,
					"staged_corners = os::CORNER_ROUNDED",
				)
				self.assertGreater(len(set(frame.indices)), 5)
			with self.subTest(size=size, tab="personalization"):
				frame = render_settings_frame(
					self.source,
					size,
					1,
					"staged_background = 2\nstaged_palette = 2",
				)
				self.assertGreater(len(set(frame.indices)), 5)
			with self.subTest(size=size, tab="icons"):
				frame = render_settings_frame(
					self.source,
					size,
					3,
					"staged_icons = os::ICON_MEDIUM\nstaged_clock = os::CLOCK_12_HOUR",
				)
				self.assertGreater(len(set(frame.indices)), 5)

	def test_ultra_short_compact_page_scroll_reaches_every_sound_control_and_actions(self) -> None:
		self.assertIn("proc draw_scrollable_compact_page", self.source)
		self.assertIn("page_scroll_maximum = page_total - (panel_height - 2)", self.source)
		self.assertIn("page_scroll -= scroll_value * 7", self.source)

		def render_at(scroll: int):
			modified = self.source.replace("settings_window.width = APP_DEFAULT_WIDTH", "settings_window.width = 160", 1)
			modified = modified.replace("settings_window.height = APP_DEFAULT_HEIGHT", "settings_window.height = 36", 1)
			modified = modified.replace("page_scroll_tab = -1", f"page_scroll_tab = 0\npage_scroll = {scroll}", 1)
			modified = modified.replace(
				"while (settings_window.state != graphics::WINDOW_CLOSED) {",
				"var short_frame: int\nshort_frame = 0\nwhile (short_frame < 1) {",
				1,
			)
			modified = modified.replace(
				"call graphics::update(settings_window)",
				"call graphics::update(settings_window)\n\tshort_frame += 1",
				1,
			)
			frames = []
			context = RuntimeContext(frame_handler=frames.append)
			with redirect_stdout(StringIO()):
				_, error, _ = run("settings-short-scroll.xe", modified, context)
			self.assertIsNone(error, str(error))
			self.assertEqual(1, len(frames))
			return frames[0]

		top = render_at(0)
		bottom = render_at(50)
		self.assertNotEqual(top.indices, bottom.indices)
		self.assertGreater(len(set(bottom.indices)), 4)

	def test_preview_uses_current_light_palette_without_applying_staged_edits(self) -> None:
		frame = render_settings_frame(
			self.source,
			(280, 210),
			2,
			"staged_corners = os::CORNER_SOFT",
			OSSettings(theme_mode=1, palette_id=3, background_id=2),
		)
		self.assertEqual(PALETTES[3], frame.palette)
		self.assertGreater(len(set(frame.indices)), 5)

	def test_normal_and_large_windows_render_complete_frames(self) -> None:
		normal = render_one_frame(self.source, "settings-frame.xe", "settings_window", (280, 210), (280, 210))
		large = render_one_frame(self.source, "settings-frame.xe", "settings_window", (280, 210), (400, 300))
		self.assertGreater(normal, 30_000)
		self.assertGreater(large, normal * 2)


if __name__ == "__main__":
	unittest.main()
