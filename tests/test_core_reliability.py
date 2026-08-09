from __future__ import annotations

from io import BytesIO
import random
import zipfile

import pytest

from runtime import RuntimeContext, run
from xe_lang import archive_safety
from xe_lang.archive_safety import ArchiveSafetyError, load_safe_zip_members
from xe_lang.assembler import assemble
from xe_lang.compiler_service import compile_source
from xe_lang.devices.compiler import CompilerDevice
from xe_lang.devices.filesystem import FileSystemDevice, TRASH_DIRECTORY
from xe_lang.devices.graphics import GraphicsDevice
from xe_lang.devices.input import INPUT_QUEUE_LIMIT, InputDevice, KeyboardEvent, MouseEvent
from xe_lang.devices.os_state import OSDevice
from xe_lang.devices import os_state
from xe_lang.devices.windows import EVENT_MAXIMIZED, Rect, WindowManager, WindowState
from xe_lang.executable import MAX_STATIC_WORDS, decode_static_layout, static_layout_trailer
from xe_lang.helper import Result
from xe_lang.ir_optimize import fold_constants, remove_unreachable_after_halt
from xe_lang.syscall_abi import ImageFormat, SyscallID
from xe_lang.vm import MAGIC, VERSION, VM


@pytest.mark.parametrize(
	"source",
	(
		'out << "unterminated\\',
		"~=",
		"os.i",
		'out << "a\\0b"',
		"out << ''",
		'out << "\\x100"',
		"out << '\\x100'",
	),
)
def test_invalid_source_returns_a_diagnostic_instead_of_raising(source: str) -> None:
	artifact = compile_source(source, "invalid.xe")
	assert artifact.success is False
	assert artifact.diagnostics


def test_byte_escapes_preserve_valid_app_sentinels_and_escaped_quotes() -> None:
	assert compile_source("out << '\\0'", "nul-char.xe").success
	assert compile_source('out << "say \\"hello\\""', "quote.xe").success


def test_seeded_invalid_source_corpus_never_escapes_the_compiler_boundary() -> None:
	rng = random.Random(131)
	alphabet = "abcxyz0123~='\\\"{}[]()#\n\x00"
	for index in range(256):
		source = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 40)))
		artifact = compile_source(source, f"fuzz-{index}.xe")
		assert isinstance(artifact.success, bool)


def test_virtual_drive_rejects_normalized_internal_aliases(tmp_path) -> None:
	device = FileSystemDevice(tmp_path)
	secret = tmp_path / TRASH_DIRECTORY / "secret.txt"
	secret.write_text("secret", encoding="utf-8")
	assert device.read_text(f"folder/../{TRASH_DIRECTORY}/secret.txt") is None
	assert device.read_text(f"folder/../{TRASH_DIRECTORY.upper()}/secret.txt") is None
	assert device.normalize(f"folder\\..\\{TRASH_DIRECTORY}\\secret.txt") == ""


def test_virtual_drive_bounds_reads_and_rejects_rename_with_open_handles(tmp_path, monkeypatch) -> None:
	from xe_lang.devices import filesystem

	monkeypatch.setattr(filesystem, "MAX_TEXT_FILE_BYTES", 8)
	monkeypatch.setattr(filesystem, "MAX_TEXT_FILE_CHARACTERS", 8)
	device = FileSystemDevice(tmp_path)
	(tmp_path / "large.txt").write_text("123456789", encoding="utf-8")
	assert device.open_read("large.txt") == 0
	assert device.read_text("large.txt") is None
	assert device.make_file("open.txt")
	handle = device.open_read("open.txt")
	assert handle
	assert device.rename("open.txt", "renamed.txt") is False
	device.close_path("open.txt")
	assert device.rename("open.txt", "renamed.txt") is True


def test_extreme_graphics_primitives_are_clipped_to_the_stage() -> None:
	graphics = GraphicsDevice(16, 12)
	limit = 0x7FFFFFFF
	graphics.draw_line(-limit, -limit, limit, limit, 15)
	graphics.draw_circle(0, 0, limit, 3)
	graphics.fill_circle(0, 0, limit, 4)
	graphics.fill_triangle_scaled(0, 0, (-limit, -limit, limit, 0, 0, limit), 5, 1)
	graphics.draw_indexed_pixels(0, 0, 2, 1, [1, 2], limit)
	assert len(graphics.back_buffer) == 12
	assert all(len(row) == 16 for row in graphics.back_buffer)


def test_graphics_clipping_preserves_legacy_bresenham_edge_pixels() -> None:
	graphics = GraphicsDevice(16, 12)
	graphics.set_clip(3, 2, 10, 8)
	graphics.draw_line(-5, -5, 4, 7, 9)
	assert {
		(x, y)
		for y, row in enumerate(graphics.back_buffer)
		for x, color in enumerate(row)
		if color == 9
	} == {(3, 5), (3, 6), (4, 7)}

	graphics.clear_both(0)
	graphics.draw_line(-12, -12, 4, 10, 9)
	assert graphics.back_buffer[8][3] == 9

	graphics = GraphicsDevice(16, 12)
	graphics.set_clip(6, 1, 5, 3)
	graphics.draw_line(-9349, 5886, 9349, -5868, 9)
	assert {
		(x, y)
		for y, row in enumerate(graphics.back_buffer)
		for x, color in enumerate(row)
		if color == 9
	} == {(9, 3), (10, 3)}

	random_source = random.Random(0x58454E4F)
	for _ in range(32):
		x0, y0, x1, y1 = (random_source.randint(-20_000, 20_000) for _ in range(4))
		if max(abs(x1 - x0), abs(y1 - y0)) <= 8192:
			x1 += 12_000
		expected: set[tuple[int, int]] = set()
		x, y = x0, y0
		dx = abs(x1 - x0)
		sx = 1 if x0 < x1 else -1
		dy = -abs(y1 - y0)
		sy = 1 if y0 < y1 else -1
		error = dx + dy
		while True:
			if 0 <= x < 16 and 0 <= y < 12:
				expected.add((x, y))
			if x == x1 and y == y1:
				break
			e2 = error * 2
			if e2 >= dy:
				error += dy
				x += sx
			if e2 <= dx:
				error += dx
				y += sy
		graphics = GraphicsDevice(16, 12)
		graphics.draw_line(x0, y0, x1, y1, 9)
		actual = {
			(px, py)
			for py, row in enumerate(graphics.back_buffer)
			for px, color in enumerate(row)
			if color == 9
		}
		assert actual == expected


def test_indexed_sprite_blit_batches_rows_without_changing_pixels(monkeypatch) -> None:
	graphics = GraphicsDevice(256, 192)
	fill_calls = 0
	original_fill = graphics.fill_rect

	def counted_fill(*args) -> None:
		nonlocal fill_calls
		fill_calls += 1
		original_fill(*args)

	monkeypatch.setattr(graphics, "fill_rect", counted_fill)
	pixels = [index % 16 for index in range(256 * 192)]
	graphics.draw_indexed_pixels(0, 0, 256, 192, pixels)
	assert fill_calls == 0
	assert bytes(graphics.back_buffer[0]) == bytes(pixels[:256])
	assert bytes(graphics.back_buffer[-1]) == bytes(pixels[-256:])


def test_large_but_ordinary_circle_keeps_midpoint_raster_edges() -> None:
	graphics = GraphicsDevice(16, 12)
	graphics.draw_circle(-57, -7, 57, 1)
	assert graphics.back_buffer[0][0] == 1

	graphics = GraphicsDevice(13, 9)
	graphics.draw_circle(62, -144, 165, 1)
	expected: set[tuple[int, int]] = set()
	x, y, error = 165, 0, 1 - 165
	while x >= y:
		for px, py in (
			(62 + x, -144 + y), (62 + y, -144 + x),
			(62 - y, -144 + x), (62 - x, -144 + y),
			(62 - x, -144 - y), (62 - y, -144 - x),
			(62 + y, -144 - x), (62 + x, -144 - y),
		):
			if 0 <= px < 13 and 0 <= py < 9:
				expected.add((px, py))
		y += 1
		if error < 0:
			error += 2 * y + 1
		else:
			x -= 1
			error += 2 * (y - x) + 1
	actual = {
		(px, py)
		for py, row in enumerate(graphics.back_buffer)
		for px, color in enumerate(row)
		if color == 1
	}
	assert actual == expected


def test_raw_image_rejects_nonfinite_scale_without_host_exception(tmp_path) -> None:
	vm = VM([MAGIC, VERSION, 0, 0], filesystem_root=tmp_path)
	vm.data_memory[0:3] = [1, 1, 2]
	for value in (0, 0, 0, 0x7FC00000, int(ImageFormat.PALETTE_WORDS)):
		vm.push(value)
	result = Result()
	assert vm.devices.dispatch(int(SyscallID.GRAPHICS_IMAGE), vm, result)
	assert result.error is not None


def test_static_layout_rejects_out_of_range_metadata() -> None:
	with pytest.raises(ValueError):
		static_layout_trailer(MAX_STATIC_WORDS + 1)
	bad = [0, *static_layout_trailer(MAX_STATIC_WORDS)]
	bad[-2] = MAX_STATIC_WORDS + 1
	bad[-1] = (bad[-2] ^ 0xFFFFFFFF) & 0xFFFFFFFF
	with pytest.raises(ValueError):
		decode_static_layout(bad)


@pytest.mark.parametrize(
	"program",
	(
		[MAGIC, VERSION, -1, 1],
		[MAGIC, VERSION, 0, -1],
		[MAGIC, VERSION, 1.0, 0, 0],
		[MAGIC, VERSION, 1, 0, 1.5],
		[MAGIC, VERSION, 1, 0, True],
		[MAGIC, VERSION, 1, 0, 1 << 65],
		[MAGIC, VERSION, 0, 1, -1],
		[MAGIC, VERSION, MAX_STATIC_WORDS + 1, 0],
	),
)
def test_vm_rejects_invalid_executable_section_metadata(program) -> None:
	with pytest.raises(ValueError):
		VM(program)


def test_vm_rejects_legacy_program_data_beyond_static_address_space() -> None:
	data = [0] * (MAX_STATIC_WORDS + 1)
	with pytest.raises(ValueError):
		VM([MAGIC, VERSION, 0, len(data), *data])


def _instruction(kind: int, modifier: int = 0, argument: int = 0) -> int:
	return (kind << 32) | (modifier << 16) | argument


def _vm_for(*instructions: int, tmp_path) -> VM:
	return VM([MAGIC, VERSION, len(instructions), 0, *instructions], filesystem_root=tmp_path)


def test_vm_faults_invalid_instructions_branches_and_nonfinite_conversions(tmp_path) -> None:
	assert _vm_for(_instruction(9), tmp_path=tmp_path).run().error is not None
	assert _vm_for(_instruction(4, 0, 0xFFFF), tmp_path=tmp_path).run().error is not None
	vm = _vm_for(_instruction(0, 0x7FC0, 0), _instruction(2, 1, 0), tmp_path=tmp_path)
	assert vm.run().error is not None


def test_vm_preserves_a_branch_to_the_end_of_the_program(tmp_path) -> None:
	vm = _vm_for(_instruction(4, 0, 1), tmp_path=tmp_path)
	assert vm.run().error is None


def test_vm_executes_declared_setim_memcpy_and_memset_opcodes(tmp_path) -> None:
	program = assemble(
		"memory.xas",
		"SETIM 7\nPUSH 10\nPUSH 20\nMEMCPY 2\nPUSH 30\nPUSH 9\nMEMSET 2\nHALT\n",
		emit_file=False,
	)
	assert program.error is None
	vm = VM(program.value, filesystem_root=tmp_path)
	vm.data_memory[20:22] = [4, 5]
	result = vm.run()
	assert result.error is None
	assert vm.im == 7
	assert vm.data_memory[10:12] == [4, 5]
	assert vm.data_memory[30:32] == [9, 9]


def test_two_windows_keep_independent_eased_transitions() -> None:
	now = [0.0]
	graphics = GraphicsDevice(120, 90)
	manager = WindowManager(graphics, InputDevice(120, 90), clock=lambda: now[0])
	manager.transition_duration_ms = 100
	first = manager.create(2, 2, 72, 54, "first")
	second = manager.create(20, 20, 72, 54, "second")
	manager._start_transition(manager.record(first), Rect(0, 0, 120, 90), WindowState.MAXIMIZED, EVENT_MAXIMIZED)
	manager._start_transition(manager.record(second), Rect(0, 0, 120, 90), WindowState.MAXIMIZED, EVENT_MAXIMIZED)
	now[0] = 0.05
	manager.update(first)
	manager.update(second)
	assert manager.is_transitioning(first)
	assert manager.is_transitioning(second)
	assert manager.record(first).bounds.width > 72
	assert manager.record(second).bounds.width > 72


def test_focus_release_emits_events_and_input_queues_are_bounded() -> None:
	device = InputDevice(20, 20)
	device.set_button(1, True)
	device.set_key(65, True, 1)
	device.release_all()
	assert device.frame().left_released
	assert device.poll_mouse()[0] in (int(MouseEvent.PRESS), int(MouseEvent.RELEASE))
	assert device.poll_mouse()[0] == int(MouseEvent.RELEASE)
	assert device.poll_keyboard()[0] == int(KeyboardEvent.PRESS)
	assert device.poll_keyboard()[0] == int(KeyboardEvent.RELEASE)
	for key in range(INPUT_QUEUE_LIMIT + 100):
		device.set_key(key, True)
		device.set_key(key, False)
	assert len(device.key_queue) == INPUT_QUEUE_LIMIT
	assert len(device._keyboard_events) == INPUT_QUEUE_LIMIT
	queue_snapshot = device.key_queue
	queue_snapshot.clear()
	keys_snapshot = device.keys_down
	keys_snapshot.add(999)
	assert len(device.key_queue) == INPUT_QUEUE_LIMIT
	assert 999 not in device.keys_down


def test_os_persistence_failure_rolls_back_and_cleans_temporary_file(tmp_path, monkeypatch) -> None:
	settings_path = tmp_path / "settings.json"
	device = OSDevice(settings_path=settings_path)
	before = device.volume
	monkeypatch.setattr(os_state.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("blocked")))
	assert device.set_volume(before + 1) is False
	assert device.volume == before
	assert not tuple(tmp_path.glob("*.tmp"))


def test_archive_reader_rejects_backslash_traversal_and_unsafe_compression(monkeypatch) -> None:
	payload = BytesIO()
	with zipfile.ZipFile(payload, "w") as archive:
		archive.writestr("..\\outside", b"x")
	with pytest.raises(ArchiveSafetyError):
		load_safe_zip_members(payload.getvalue())

	monkeypatch.setattr(archive_safety, "MAX_COMPRESSION_RATIO", 1)
	payload = BytesIO()
	with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
		archive.writestr("project.json", b"{" + b" " * 64 + b"}")
	with pytest.raises(ArchiveSafetyError):
		load_safe_zip_members(payload.getvalue())


def test_optimizer_uses_real_bitwise_and_branch_opcodes() -> None:
	folded = fold_constants([(None, None, "PUSH", 6), (None, None, "PUSH", 3), (None, None, "AND")])
	assert folded == [(None, None, "PUSH", 2)]
	pruned = remove_unreachable_after_halt([
		(None, None, "JUMP", "end"),
		(None, None, "PUSH", 99),
		(None, None, ":end"),
	])
	assert (None, None, "PUSH", 99) not in pruned


def test_runtime_returns_the_same_bounded_stack_contract_for_xassembly(tmp_path) -> None:
	context = RuntimeContext(filesystem_root=tmp_path)
	stack, error, _ = run("contract.xas", "\n".join(["PUSH 1"] * 40 + ["HALT"]), context)
	assert error is None
	assert stack == [1] * 32


def test_visual_atom_toggle_preserves_line_endings_and_trailing_whitespace() -> None:
	device = CompilerDevice()
	source = "out << 1  \r\nout << 2\r\n"
	assert device.load_visual(source) == 2
	assert device.set_atom_enabled(0, False)
	assert device.set_atom_enabled(0, True)
	assert device.visual_source == source
