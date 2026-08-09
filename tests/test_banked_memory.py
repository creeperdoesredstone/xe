from __future__ import annotations

import pytest

from xe_lang.memory import (
	BankedMemory,
	MAX_ADDRESS_COUNT,
	SCRATCH_BANK_COUNT,
	SCRATCH_BANK_WORDS,
	SCRATCH_ITEMS_PER_WORD,
	STANDBY_WORDS,
	WORKING_SET_WORDS,
)
from xe_lang.vm import MAGIC, VERSION, VM


EMPTY_PROGRAM = [MAGIC, VERSION, 0, 0]


def test_default_memory_has_ten_scratch_sized_logical_banks() -> None:
	memory = BankedMemory()

	assert len(memory) == MAX_ADDRESS_COUNT == 2_000_000
	assert memory.bank_count == SCRATCH_BANK_COUNT == 10
	assert SCRATCH_BANK_WORDS == 200_000
	assert SCRATCH_ITEMS_PER_WORD == 1
	assert WORKING_SET_WORDS == STANDBY_WORDS == 1_000_000
	assert sum(bank.length for bank in memory.bank_layout) == len(memory)
	assert all(bank.length == SCRATCH_BANK_WORDS for bank in memory.bank_layout)
	assert [bank.tier for bank in memory.bank_layout] == ["working"] * 5 + ["standby"] * 5
	assert [bank.scratch_list_name for bank in memory.bank_layout] == [
		f"MEM_DATA_{index}" for index in range(10)
	]
	assert memory.materialized_bank_count == 0
	assert memory.materialized_words == 0


@pytest.mark.parametrize(
	("address", "expected"),
	(
		(0, (0, 0)),
		(199_999, (0, 199_999)),
		(200_000, (1, 0)),
		(999_999, (4, 199_999)),
		(1_000_000, (5, 0)),
		(1_999_999, (9, 199_999)),
	),
)
def test_logical_addresses_map_deterministically_to_bank_items(
	address: int,
	expected: tuple[int, int],
) -> None:
	assert BankedMemory().split_address(address) == expected


def test_index_and_slice_operations_cross_bank_boundaries_like_a_list() -> None:
	memory = BankedMemory()
	memory[199_998:200_002] = [11, 12, 13, 14]
	memory[-1] = 99

	assert memory[199_997:200_003] == [0, 11, 12, 13, 14, 0]
	assert memory[199_998:200_002:2] == [11, 13]
	assert memory[-1] == 99
	assert memory.materialized_bank_count == 3
	memory[199_998:200_002:2] = [21, 23]
	assert memory[199_998:200_002] == [21, 12, 23, 14]


def test_zero_writes_do_not_materialize_idle_python_banks() -> None:
	memory = BankedMemory()
	memory[0] = 0
	memory[199_999:200_002] = [0, 0, 0]

	assert memory[0:3] == [0, 0, 0]
	assert memory.materialized_bank_count == 0


def test_memory_is_fixed_size_and_every_word_is_unsigned_32_bit() -> None:
	memory = BankedMemory()
	with pytest.raises(ValueError):
		memory[0:2] = [1]
	memory[0] = -1
	assert memory[0] == 0xFFFFFFFF
	with pytest.raises(OverflowError):
		memory[0] = -(1 << 31) - 1
	with pytest.raises(OverflowError):
		memory[0] = 1 << 32
	with pytest.raises(TypeError):
		memory[0] = 1.5  # type: ignore[assignment]
	with pytest.raises(IndexError):
		memory.split_address(-1)
	with pytest.raises(IndexError):
		memory.split_address(len(memory))


def test_vm_uses_working_memory_before_activating_standby() -> None:
	vm = VM(EMPTY_PROGRAM)
	working_capacity = WORKING_SET_WORDS - vm.heap_start

	assert isinstance(vm.data_memory, BankedMemory)
	assert vm.free_list == [(vm.heap_start, working_capacity)]
	assert vm.standby_active is False

	first = vm.malloc(working_capacity)
	assert first.error is None
	assert vm.pop().value == vm.heap_start
	assert vm.standby_active is False

	second = vm.malloc(1)
	assert second.error is None
	assert vm.pop().value == WORKING_SET_WORDS
	assert vm.standby_active is True
	assert vm.gc_runs == 1
	assert vm.data_memory.materialized_bank_count == 0


def test_standby_can_extend_a_contiguous_allocation_after_working_pressure() -> None:
	vm = VM(EMPTY_PROGRAM)
	words = WORKING_SET_WORDS - vm.heap_start + 1

	result = vm.malloc(words)
	assert result.error is None
	assert vm.pop().value == vm.heap_start
	assert vm.allocations[vm.heap_start] == words
	assert vm.standby_active is True


def test_custom_legacy_sized_memory_retains_no_standby_tier() -> None:
	vm = VM(EMPTY_PROGRAM, memory_words=65_536)
	available = len(vm.data_memory) - vm.heap_start

	assert vm.data_memory.bank_count == 1
	assert vm.malloc(available).error is None
	assert vm.pop().value == vm.heap_start
	result = vm.malloc(1)
	assert result.error is not None
	assert vm.standby_active is False


@pytest.mark.parametrize("invalid", (65_536.5, True, "65536"))
def test_vm_rejects_non_integer_memory_sizes(invalid: object) -> None:
	with pytest.raises(ValueError, match="integer address count"):
		VM(EMPTY_PROGRAM, memory_words=invalid)  # type: ignore[arg-type]
