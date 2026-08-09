from __future__ import annotations

from array import array
from dataclasses import dataclass
from itertools import repeat
from operator import index as integer_index
from typing import Iterable, Iterator, overload


WORD_BITS = 32
WORD_MASK = (1 << WORD_BITS) - 1
SCRATCH_ITEMS_PER_WORD = 1
SCRATCH_BANK_WORDS = 200_000
WORKING_BANK_COUNT = 5
STANDBY_BANK_COUNT = 5
SCRATCH_BANK_COUNT = WORKING_BANK_COUNT + STANDBY_BANK_COUNT
WORKING_SET_WORDS = WORKING_BANK_COUNT * SCRATCH_BANK_WORDS
STANDBY_WORDS = STANDBY_BANK_COUNT * SCRATCH_BANK_WORDS
MAX_ADDRESS_COUNT = WORKING_SET_WORDS + STANDBY_WORDS


@dataclass(frozen=True, slots=True)
class MemoryBankLayout:
	index: int
	start: int
	length: int
	tier: str

	@property
	def stop(self) -> int:
		return self.start + self.length

	@property
	def scratch_list_name(self) -> str:
		return f"MEM_DATA_{self.index}"


class BankedMemory:
	"""A fixed-size, list-like 32-bit memory split into Scratch-sized banks.

	The logical layout is always address based: an address selects a bank and then
	a zero-based item inside that bank. Python banks are materialized lazily so an
	idle VM does not allocate two million Python integer references. The eventual
	Scratch representation still uses one list item for every logical word.
	"""

	__slots__ = ("_length", "_layouts", "_storage")

	def __init__(self, length: int = MAX_ADDRESS_COUNT) -> None:
		length = integer_index(length)
		if length < 0 or length > MAX_ADDRESS_COUNT:
			raise ValueError(f"Memory length must be between 0 and {MAX_ADDRESS_COUNT}")
		if array("I").itemsize != WORD_BITS // 8:
			raise RuntimeError("This Python build does not provide a 32-bit unsigned array type")

		self._length = length
		self._layouts = tuple(
			MemoryBankLayout(
				index=bank,
				start=bank * SCRATCH_BANK_WORDS,
				length=min(SCRATCH_BANK_WORDS, length - bank * SCRATCH_BANK_WORDS),
				tier="working" if bank < WORKING_BANK_COUNT else "standby",
			)
			for bank in range((length + SCRATCH_BANK_WORDS - 1) // SCRATCH_BANK_WORDS)
		)
		self._storage: list[array[int] | None] = [None] * len(self._layouts)

	def __len__(self) -> int:
		return self._length

	@property
	def bank_layout(self) -> tuple[MemoryBankLayout, ...]:
		return self._layouts

	@property
	def bank_count(self) -> int:
		return len(self._layouts)

	@property
	def materialized_bank_count(self) -> int:
		return sum(bank is not None for bank in self._storage)

	@property
	def materialized_words(self) -> int:
		return sum(
			layout.length
			for layout, bank in zip(self._layouts, self._storage, strict=True)
			if bank is not None
		)

	def split_address(self, address: int) -> tuple[int, int]:
		address = integer_index(address)
		if not 0 <= address < self._length:
			raise IndexError("memory address out of range")
		return divmod(address, SCRATCH_BANK_WORDS)

	def _normalize_index(self, address: int) -> int:
		address = integer_index(address)
		if address < 0:
			address += self._length
		if not 0 <= address < self._length:
			raise IndexError("memory index out of range")
		return address

	@staticmethod
	def _normalize_word(value: int) -> int:
		value = integer_index(value)
		if not -(1 << (WORD_BITS - 1)) <= value <= WORD_MASK:
			raise OverflowError("memory word is outside the 32-bit register range")
		return value & WORD_MASK

	def _materialize(self, bank_index: int) -> array[int]:
		bank = self._storage[bank_index]
		if bank is None:
			bank = array("I", repeat(0, self._layouts[bank_index].length))
			self._storage[bank_index] = bank
		return bank

	@overload
	def __getitem__(self, address: int) -> int: ...

	@overload
	def __getitem__(self, address: slice) -> list[int]: ...

	def __getitem__(self, address: int | slice) -> int | list[int]:
		if isinstance(address, slice):
			start, stop, step = address.indices(self._length)
			if step != 1:
				return [self[index] for index in range(start, stop, step)]
			return self._read_contiguous(start, stop)

		logical_address = self._normalize_index(address)
		bank_index, offset = divmod(logical_address, SCRATCH_BANK_WORDS)
		bank = self._storage[bank_index]
		return 0 if bank is None else int(bank[offset])

	def _read_contiguous(self, start: int, stop: int) -> list[int]:
		values: list[int] = []
		cursor = start
		while cursor < stop:
			bank_index, offset = divmod(cursor, SCRATCH_BANK_WORDS)
			bank_stop = min(stop, self._layouts[bank_index].stop)
			count = bank_stop - cursor
			bank = self._storage[bank_index]
			if bank is None:
				values.extend(repeat(0, count))
			else:
				values.extend(bank[offset : offset + count])
			cursor = bank_stop
		return values

	def __setitem__(self, address: int | slice, value: int | Iterable[int]) -> None:
		if isinstance(address, slice):
			self._write_slice(address, value)
			return

		logical_address = self._normalize_index(address)
		word = self._normalize_word(value)  # type: ignore[arg-type]
		bank_index, offset = divmod(logical_address, SCRATCH_BANK_WORDS)
		bank = self._storage[bank_index]
		if bank is None and word == 0:
			return
		self._materialize(bank_index)[offset] = word

	def _write_slice(self, address: slice, values: int | Iterable[int]) -> None:
		if isinstance(values, int):
			raise TypeError("can only assign an iterable to a memory slice")
		normalized = [self._normalize_word(value) for value in values]
		start, stop, step = address.indices(self._length)
		indices = range(start, stop, step)
		if len(normalized) != len(indices):
			raise ValueError("BankedMemory has a fixed length")
		if step != 1:
			for index, word in zip(indices, normalized, strict=True):
				self[index] = word
			return

		cursor = start
		value_offset = 0
		while cursor < stop:
			bank_index, offset = divmod(cursor, SCRATCH_BANK_WORDS)
			bank_stop = min(stop, self._layouts[bank_index].stop)
			count = bank_stop - cursor
			chunk = normalized[value_offset : value_offset + count]
			bank = self._storage[bank_index]
			if bank is not None or any(chunk):
				self._materialize(bank_index)[offset : offset + count] = array("I", chunk)
			cursor = bank_stop
			value_offset += count

	def __iter__(self) -> Iterator[int]:
		for layout, bank in zip(self._layouts, self._storage, strict=True):
			if bank is None:
				yield from repeat(0, layout.length)
			else:
				yield from bank

	def iter_nonzero(self, start: int = 0, stop: int | None = None) -> Iterator[int]:
		"""Yield nonzero words in a logical span without expanding untouched banks."""
		if stop is None:
			stop = self._length
		start, stop, _ = slice(start, stop, 1).indices(self._length)
		cursor = start
		while cursor < stop:
			bank_index, offset = divmod(cursor, SCRATCH_BANK_WORDS)
			bank_stop = min(stop, self._layouts[bank_index].stop)
			bank = self._storage[bank_index]
			if bank is not None:
				for word in bank[offset : offset + bank_stop - cursor]:
					if word:
						yield int(word)
			cursor = bank_stop

	def __repr__(self) -> str:
		return (
			f"BankedMemory(length={self._length}, banks={self.bank_count}, "
			f"materialized={self.materialized_bank_count})"
		)
