from __future__ import annotations

from collections.abc import Callable

import pytest

from runtime import RuntimeContext
from xe_lang.compiler_service import compile_source
from xe_lang import graphics_commands as gc
from xe_lang.devices.syscalls import (
	SCREEN_FIELD_HEIGHT,
	SCREEN_FIELD_WIDTH,
	WINDOW_HEIGHT,
	WINDOW_STATE,
	WINDOW_TITLE,
	WINDOW_UI_SCALE,
	WINDOW_WIDTH,
	WINDOW_X,
	WINDOW_Y,
)
from xe_lang.helper import Result
from xe_lang.stdlib.specs import GRAPHICS_SPEC
from xe_lang.syscall_abi import GRAPHICS_SCREEN_REFERENCE_TAG


STREAM = 2_000
SCREEN = 100
NAMES = 1_000
SHORT_NAMES = 1_064
SELECTED = 1_128
OUT_X = 4_000
OUT_Y = 4_064
OUT_DEPTH = 4_128
OUT_RADIUS = 4_192
DEPTH_ORDER = 4_256
DESCRIPTORS = (5_000, 5_003, 5_006, 5_009)


def _signed(value: int) -> int:
	return value - 0x1_0000_0000 if value > 0x7FFF_FFFF else value


def _write_descriptor(memory: list[int], descriptor: int, chars: int, value: str) -> None:
	memory[descriptor] = chars
	memory[descriptor + 1] = len(value) + 1
	memory[descriptor + 2] = len(value) + 1
	for index, character in enumerate(value):
		memory[chars + index] = ord(character)
	memory[chars + len(value)] = 0


def _scene(*, tilt: int = 35, roll: int = -17, rotation: int = -43) -> tuple[RuntimeContext, int]:
	context = RuntimeContext()
	vm = context.vm
	memory = vm.data_memory
	memory[SCREEN + SCREEN_FIELD_WIDTH] = context.vm.devices.graphics.width
	memory[SCREEN + SCREEN_FIELD_HEIGHT] = context.vm.devices.graphics.height

	# The string* arguments are arrays of one-word descriptor handles. Each
	# referenced descriptor itself is exactly three contiguous words.
	_write_descriptor(memory, DESCRIPTORS[0], 6_000, "")
	_write_descriptor(memory, DESCRIPTORS[1], 6_100, "folder-" + "x" * 193)
	_write_descriptor(memory, DESCRIPTORS[2], 6_400, "")
	_write_descriptor(memory, DESCRIPTORS[3], 6_500, "fold")
	memory[NAMES:NAMES + 2] = [DESCRIPTORS[0], DESCRIPTORS[1]]
	memory[SHORT_NAMES:SHORT_NAMES + 2] = [DESCRIPTORS[2], DESCRIPTORS[3]]
	memory[SELECTED:SELECTED + 2] = [0, 1]
	memory[OUT_X:OUT_X + 2] = [777, 777]
	memory[OUT_Y:OUT_Y + 2] = [777, 777]
	memory[OUT_DEPTH:OUT_DEPTH + 2] = [777, 777]
	memory[OUT_RADIUS:OUT_RADIUS + 2] = [777, 777]
	memory[DEPTH_ORDER:DEPTH_ORDER + 2] = [0, 1]

	command = 8
	shell_table = command + 36
	item_table = shell_table + 2
	word_count = item_table + 2 * 6
	stream = [0] * word_count
	stream[:8] = [0x58474331, 1, word_count, 1, command, 0, 0, 0]
	stream[command:command + 36] = [
		1, 36, 2, 1,
		0, 0, 120, 90, 240, 180, 0,
		100, 60, 0, 18, 3,
		tilt, roll, rotation,
		8, 7, 10, 8, 15,
		-100, -100, 0, 0,
		100, 32,
		item_table, 6, shell_table, 2,
		1, 20,
	]
	stream[shell_table:shell_table + 2] = [13, 2]
	stream[item_table:item_table + 6] = [0, 0, 0, 0, 0, 0]
	stream[item_table + 6:item_table + 12] = [0, 1, 1, 3, 1, 1]
	memory[STREAM:STREAM + word_count] = stream
	return context, word_count


def _call(
	context: RuntimeContext,
	word_count: int,
	*,
	target: int | None = None,
	replace_argument: tuple[int, int] | None = None,
) -> int:
	vm = context.vm
	arguments = [
		(GRAPHICS_SCREEN_REFERENCE_TAG | SCREEN) if target is None else target,
		STREAM,
		word_count,
		NAMES,
		SHORT_NAMES,
		SELECTED,
		OUT_X,
		OUT_Y,
		OUT_DEPTH,
		OUT_RADIUS,
		DEPTH_ORDER,
	]
	if replace_argument is not None:
		arguments[replace_argument[0]] = replace_argument[1]
	for argument in arguments:
		vm.push(argument)
	result = Result()
	vm.devices._graphics_draw_commands(vm, result)
	assert result.error is None
	return _signed(vm.pop().value)


def _back_pixels(context: RuntimeContext) -> bytes:
	return b"".join(context.vm.devices.graphics.back_buffer)


def test_public_command_offsets_match_the_canonical_host_layout() -> None:
	public = {constant.name: constant.value for constant in GRAPHICS_SPEC.constants}
	assert public["COMMAND_MAGIC"] == gc.MAGIC
	assert public["COMMAND_HEADER_WORDS"] == gc.HEADER_WORDS
	assert public["COMMAND_ORBIT_WORDS"] == gc.ORBIT_WORDS
	assert public["COMMAND_ORBIT_ITEM_WORDS"] == gc.ORBIT_ITEM_WORDS
	assert public["COMMAND_ORBIT_SHELL_WORDS"] == gc.ORBIT_SHELL_WORDS
	assert public["COMMAND_ORBIT_ITEM_TABLE_OFFSET"] == gc.ORBIT_ITEM_TABLE_OFFSET
	assert public["COMMAND_ORBIT_SHELL_TABLE_OFFSET"] == gc.ORBIT_SHELL_TABLE_OFFSET
	assert public["COMMAND_ORBIT_SHELL_POINTS_OFFSET"] == gc.ORBIT_SHELL_POINTS_OFFSET


def test_public_command_stream_signature_accepts_screen_and_typed_arrays() -> None:
	artifact = compile_source(
		"""var target: graphics::Screen
array stream: int[8]
array names: string[1]
array short_names: string[1]
array values: int[1]
var result: int
result = graphics::draw_commands(target, stream, 8, names, short_names, values, values, values, values, values, values)
""",
		"screen-command-stream.xe",
	)
	assert artifact.success, artifact.diagnostics


def test_orbit_command_supports_screen_window_hover_and_exact_output_geometry() -> None:
	context, words = _scene()
	context.vm.devices.graphics.clear_both(2)
	assert _call(context, words) == 0
	memory = context.vm.data_memory
	assert memory[OUT_RADIUS:OUT_RADIUS + 2] == [3, 3]
	assert sorted(memory[DEPTH_ORDER:DEPTH_ORDER + 2]) == [0, 1]
	assert _back_pixels(context) != bytes((2,)) * len(_back_pixels(context))

	# Hit testing uses the same projected center and base node radius written to
	# the output arrays, even though folders render one pixel larger.
	command = STREAM + 8
	memory[command + 24] = memory[OUT_X]
	memory[command + 25] = memory[OUT_Y]
	packed = _call(context, words)
	assert packed & 0xFF == 1
	assert (packed // 256) & 0xFF == 1

	# A real Window target uses its content origin and target UI scale.
	window = 300
	_write_descriptor(memory, 5_100, 6_700, "Batch")
	memory[window + WINDOW_X] = 12
	memory[window + WINDOW_Y] = 9
	memory[window + WINDOW_WIDTH] = 260
	memory[window + WINDOW_HEIGHT] = 210
	memory[window + WINDOW_TITLE] = 5_100
	memory[window + WINDOW_STATE] = 0
	memory[window + WINDOW_UI_SCALE] = 1
	assert _call(context, words, target=window) >= 0


def test_negative_tilt_keeps_front_back_depth_and_projection_stable() -> None:
	positive, words = _scene(tilt=57, roll=31, rotation=-119)
	assert _call(positive, words) >= 0
	positive_values = [
		positive.vm.data_memory[address:address + 2]
		for address in (OUT_X, OUT_Y, OUT_DEPTH, OUT_RADIUS)
	]
	negative, words = _scene(tilt=-57, roll=31, rotation=-119)
	assert _call(negative, words) >= 0
	negative_values = [
		negative.vm.data_memory[address:address + 2]
		for address in (OUT_X, OUT_Y, OUT_DEPTH, OUT_RADIUS)
	]
	assert negative_values == positive_values


def test_positive_orbit_sine_is_near_half_beneath_the_final_nucleus() -> None:
	context, words = _scene(tilt=70, roll=0, rotation=0)
	memory = context.vm.data_memory
	shell_table = STREAM + gc.HEADER_WORDS + gc.ORBIT_WORDS
	item_table = shell_table + gc.ORBIT_SHELL_WORDS
	memory[shell_table + gc.ORBIT_SHELL_PHASE_OFFSET] = 90
	memory[item_table + gc.ORBIT_ITEM_DIRECTORY_OFFSET] = 1
	memory[item_table + gc.ORBIT_ITEM_WORDS + gc.ORBIT_ITEM_DIRECTORY_OFFSET] = 0
	context.vm.devices.graphics.clear_both(0)
	assert _call(context, words) >= 0
	depths = tuple(_signed(value) for value in memory[OUT_DEPTH:OUT_DEPTH + 2])
	assert depths[0] > 0
	assert depths[1] < 0
	assert tuple(memory[DEPTH_ORDER:DEPTH_ORDER + 2]) == (1, 0)
	near_highlight = (memory[OUT_X] - 2, memory[OUT_Y] - 2)
	assert (near_highlight[0] - 120) ** 2 + (near_highlight[1] - 90) ** 2 < 18 ** 2
	pixels = _back_pixels(context)
	# The nucleus is the final focal layer, including over near-side entries.
	assert pixels[near_highlight[1] * context.vm.devices.graphics.width + near_highlight[0]] == 8


def test_drag_tilt_sequence_never_reverses_occlusion_and_highlight_is_pixel_fixed() -> None:
	context, words = _scene(tilt=70, roll=31, rotation=-119)
	memory = context.vm.data_memory
	memory[SELECTED:SELECTED + 2] = [0, 0]
	command = STREAM + gc.HEADER_WORDS
	item_table = command + gc.ORBIT_WORDS + gc.ORBIT_SHELL_WORDS
	memory[item_table + gc.ORBIT_ITEM_WORDS + gc.ORBIT_ITEM_DIRECTORY_OFFSET] = 0
	memory[item_table + gc.ORBIT_ITEM_WORDS + gc.ORBIT_ITEM_CHILD_COUNT_OFFSET] = 0
	width = context.vm.devices.graphics.width
	expected_highlight = (120 - 18 // 2, 90 - 18 // 2)
	positive: dict[int, tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]] = {}

	for tilt in (70, 48, 24, 8, 1, 0, -1, -8, -24, -48, -70):
		memory[command + gc.ORBIT_TILT_OFFSET] = tilt & 0xFFFFFFFF
		context.vm.devices.graphics.clear_both(0)
		assert _call(context, words) >= 0
		depths = tuple(_signed(value) for value in memory[OUT_DEPTH:OUT_DEPTH + 2])
		order = tuple(memory[DEPTH_ORDER:DEPTH_ORDER + 2])
		positions = tuple(memory[OUT_X:OUT_X + 2] + memory[OUT_Y:OUT_Y + 2])
		magnitude = abs(tilt)
		if magnitude in positive:
			assert (depths, order, positions) == positive[magnitude]
		else:
			positive[magnitude] = (depths, order, positions)

		pixels = _back_pixels(context)
		hx, hy = expected_highlight
		assert pixels[hy * width + hx] == 15
		interior_highlights = [
			(x, y)
			for y in range(90 - 14, 90 + 15)
			for x in range(120 - 14, 120 + 15)
			if (x - 120) ** 2 + (y - 90) ** 2 < 14 ** 2 and pixels[y * width + x] == 15
		]
		assert interior_highlights == [expected_highlight]


Mutation = Callable[[list[int], int], None]


@pytest.mark.parametrize(
	"mutation",
	[
		lambda memory, words: memory.__setitem__(STREAM, 0),
		lambda memory, words: memory.__setitem__(STREAM + 1, 2),
		lambda memory, words: memory.__setitem__(STREAM + 2, words + 1),
		lambda memory, words: memory.__setitem__(STREAM + 3, 2),
		lambda memory, words: memory.__setitem__(STREAM + 4, 9),
		lambda memory, words: memory.__setitem__(STREAM + 5, 1),
		lambda memory, words: memory.__setitem__(STREAM + 8, 99),
		lambda memory, words: memory.__setitem__(STREAM + 9, 35),
		lambda memory, words: memory.__setitem__(STREAM + 8 + 31, 7),
		lambda memory, words: memory.__setitem__(STREAM + 8 + 32, 45),
		lambda memory, words: memory.__setitem__(STREAM + 44 + 1, 1),
		lambda memory, words: memory.__setitem__(STREAM + 46, 1),
		lambda memory, words: memory.__setitem__(STREAM + 46 + 1, 2),
		lambda memory, words: memory.__setitem__(STREAM + 46 + 2, 2),
		lambda memory, words: memory.__setitem__(STREAM + 46 + 3, 65),
		lambda memory, words: memory.__setitem__(STREAM + 46 + 4, 2),
		lambda memory, words: memory.__setitem__(NAMES, len(memory) - 2),
		lambda memory, words: memory.__setitem__(DESCRIPTORS[0], len(memory)),
	],
)
def test_malformed_streams_are_rejected_before_output_or_pixel_mutation(mutation: Mutation) -> None:
	context, words = _scene()
	memory = context.vm.data_memory
	context.vm.devices.graphics.clear_both(6)
	before_pixels = _back_pixels(context)
	before_outputs = tuple(memory[address] for address in (OUT_X, OUT_Y, OUT_DEPTH, OUT_RADIUS))
	mutation(memory, words)
	assert _call(context, words) < 0
	assert _back_pixels(context) == before_pixels
	assert tuple(memory[address] for address in (OUT_X, OUT_Y, OUT_DEPTH, OUT_RADIUS)) == before_outputs


def test_bad_target_pointer_span_and_overlapping_outputs_are_rejected() -> None:
	context, words = _scene()
	assert _call(context, words, target=len(context.vm.data_memory) - 1) == -1
	assert _call(context, 7) == -2
	context.vm.data_memory[STREAM + 2] += 1
	assert _call(context, words + 1) == -6

	context, words = _scene()
	context.vm.push(GRAPHICS_SCREEN_REFERENCE_TAG | SCREEN)
	for argument in (STREAM, words, NAMES, SHORT_NAMES, SELECTED, STREAM, OUT_Y, OUT_DEPTH, OUT_RADIUS, DEPTH_ORDER):
		context.vm.push(argument)
	result = Result()
	context.vm.devices._graphics_draw_commands(context.vm, result)
	assert _signed(context.vm.pop().value) == -6


@pytest.mark.parametrize("argument_index", [3, 4, 5, 6, 7, 8, 9, 10])
def test_external_array_spans_are_checked(argument_index: int) -> None:
	context, words = _scene()
	bad_span = len(context.vm.data_memory) - 1
	assert _call(context, words, replace_argument=(argument_index, bad_span)) == -6
