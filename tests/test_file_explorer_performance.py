from __future__ import annotations

from pathlib import Path
import re
from time import perf_counter

import pytest

from runtime import RuntimeContext
from xe_lang.compiler_service import compile_source
from xe_lang.devices.os_state import OSDevice, OSSettings


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "apps" / "file_explorer.xe").read_text(encoding="utf-8")


def _populated_program(frame_count: int = 5) -> tuple[int, ...]:
	populate = []
	for index in range(32):
		populate.append(f'operation_ok = os::make_file("file{index}.xe")')
		populate.append(f'operation_ok = os::make_directory("folder{index}")')
	modified = SOURCE.replace(
		"call refresh_explorer_style()\ncall reset_energy_shells()",
		"call refresh_explorer_style()\n" + "\n".join(populate) + "\ncall reset_energy_shells()",
		1,
	)
	modified = modified.replace(
		"while (explorer_window.state != graphics::WINDOW_CLOSED) {",
		f"var performance_frame: int\nperformance_frame = 0\nwhile (performance_frame < {frame_count}) {{",
		1,
	)
	modified = modified.replace(
		"call graphics::update(explorer_window)",
		"call graphics::update(explorer_window)\n\tperformance_frame += 1",
		1,
	)
	artifact = compile_source(modified, "file-explorer-performance.xe")
	assert artifact.success, artifact.diagnostics
	return artifact.program


def _populated_frame_seconds() -> float:
	timestamps: list[float] = []
	context = RuntimeContext(frame_handler=lambda _frame: timestamps.append(perf_counter()))
	context.create_vm(list(_populated_program()))
	context.vm.devices._pace_frame = lambda _vm: None
	context.vm.ip = 0
	result = context.vm.run()
	assert result.error is None, result.error
	assert len(timestamps) == 5
	return (timestamps[-1] - timestamps[0]) / (len(timestamps) - 1)


def test_populated_scene_frame_work_is_bounded() -> None:
	# The checked host batch keeps the 64-item steady scene below a 30 Hz budget;
	# the local reference run is also inside the tighter 60 Hz frame interval.
	assert _populated_frame_seconds() < 0.033


def test_frame_path_uses_cached_labels_slots_and_incremental_depth_order() -> None:
	assert "array entry_label_short_cache: string[64]" in SOURCE
	assert "array shell_slot_cosine: float[64]" in SOURCE
	assert "proc prepare_shell_slot_projection()" in SOURCE
	assert "proc update_incremental_depth_order(count: int)" in SOURCE


def _circle_pixels(radius: int) -> set[tuple[int, int]]:
	result: set[tuple[int, int]] = set()
	x = radius
	y = 0
	error = 1 - radius
	while x >= y:
		result.update(
		{
			(x, y), (y, x), (-y, x), (-x, y),
			(-x, -y), (-y, -x), (y, -x), (x, -y),
		}
		)
		y += 1
		if error < 0:
			error += 2 * y + 1
		else:
			x -= 1
			error += 2 * (y - x) + 1
	return result


def _folder_primitive_sprite(surface: str, outline: str) -> str:
	pixels: dict[tuple[int, int], str] = {}
	for y in range(-2, 3):
		for x in range(-2, 3):
			pixels[x, y] = surface
	for point in _circle_pixels(4):
		pixels[point] = outline
	pixels[-2, -2] = "F"
	for point in _circle_pixels(2):
		pixels[point] = "A"
	return "".join(pixels.get((x, y), ".") for y in range(-4, 5) for x in range(-4, 5))


def _file_primitive_sprite(surface: str, outline: str) -> str:
	pixels = {(x, y): surface for y in range(-4, 5) for x in range(-3, 4)}
	for x in range(-3, 4):
		pixels[x, -4] = outline
		pixels[x, 4] = outline
	for y in range(-4, 5):
		pixels[-3, y] = outline
		pixels[3, y] = outline
	pixels[3, -4] = "0"
	pixels[2, -4] = "A"
	pixels[3, -3] = "A"
	return "".join(pixels[x, y] for y in range(-4, 5) for x in range(-3, 4))


def test_batched_icons_are_pixel_equivalent_to_normal_size_primitives() -> None:
	folder_body = SOURCE.split("proc draw_placeholder_folder", 1)[1].split("proc draw_placeholder_file", 1)[0]
	file_body = SOURCE.split("proc draw_placeholder_file", 1)[1].split("proc draw_placeholder_shell", 1)[0]
	folder_icons = re.findall(r'draw_icon\([^\n]+"([.0-9A-F]+)"\)', folder_body)
	file_icons = re.findall(r'draw_icon\([^\n]+"([.0-9A-F]+)"\)', file_body)
	assert folder_icons == [_folder_primitive_sprite("1", "8"), _folder_primitive_sprite("8", "7")]
	assert file_icons == [_file_primitive_sprite("1", "8"), _file_primitive_sprite("8", "7")]
	assert "visual_force_opaque || visual_fade >= 75" in folder_body
	assert "call draw_placeholder_orb" in folder_body
	assert "visual_force_opaque || visual_fade >= 75" in file_body
	assert "call graphics::fill_rect" in file_body


def _representative_program(*, native: bool) -> tuple[int, ...]:
	populate: list[str] = []
	for index in range(8):
		populate.append(f'operation_ok = os::make_file("file-{index}.xe")')
		populate.append(f'operation_ok = os::make_directory("folder-{index}")')
		for child in range(index % 4):
			populate.append(f'operation_ok = os::make_file("folder-{index}/child-{child}")')
	modified = SOURCE.replace(
		"call refresh_explorer_style()\ncall reset_energy_shells()",
		"call refresh_explorer_style()\n" + "\n".join(populate) + "\ncall reset_energy_shells()",
		1,
	)
	modified = modified.replace(
		"view_roll = -12",
		"view_roll = 31\nrotation = -119\ntilt = -57\nentry_selected[1] = 1\n"
		"orbit_speed_setting = 0\nanimation_shell = 0\nwhile (animation_shell < 8) {\n"
		"\tshell_speed[animation_shell] = 0\n\tanimation_shell += 1\n}",
		1,
	)
	if not native:
		modified = modified.replace(
			"native_candidate = transition_progress == 0 && !slot_animation_active && deletion_ghost_count == 0 && drag_entry < 0 && drag_template == 0 && camera_zoom == camera_zoom_target && visual_fade >= 100",
			"native_candidate = false",
			1,
		)
	modified = modified.replace(
		"while (explorer_window.state != graphics::WINDOW_CLOSED) {",
		"var comparison_frame: int\ncomparison_frame = 0\nwhile (comparison_frame < 2) {",
		1,
	)
	modified = modified.replace(
		"call graphics::update(explorer_window)",
		"call graphics::update(explorer_window)\n\tcomparison_frame += 1",
		1,
	)
	artifact = compile_source(modified, "file-explorer-pixel-comparison.xe")
	assert artifact.success, artifact.diagnostics
	return artifact.program


def _representative_frame(*, native: bool, light: bool) -> bytes:
	frames = []
	settings = OSSettings(theme_mode=1, palette_id=3) if light else OSSettings()
	context = RuntimeContext(os_device=OSDevice(settings=settings), frame_handler=frames.append)
	context.create_vm(list(_representative_program(native=native)))
	context.vm.devices._pace_frame = lambda _vm: None
	result = context.vm.run()
	assert result.error is None, result.error
	assert len(frames) == 2
	return frames[-1].indices


@pytest.mark.parametrize("light", [False, True])
def test_native_orbit_pixels_match_fallback_for_tilt_occlusion_and_labels(light: bool) -> None:
	assert _representative_frame(native=True, light=light) == _representative_frame(native=False, light=light)
