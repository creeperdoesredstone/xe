from __future__ import annotations

from dataclasses import dataclass

from xe_lang.devices.currency_snapshot import (
	CURRENCY_CODES,
	DAILY_RATES,
	MONTHLY_RATES,
	SNAPSHOT_DATE,
	WEEKLY_RATES,
)


@dataclass(frozen=True)
class CurrencySnapshot:
	rate: float
	points: tuple[float, ...]


class CurrencyDevice:
	STATUS_IDLE = 0
	STATUS_LOADING = 1
	STATUS_READY = 2
	STATUS_ERROR = 3

	def __init__(self) -> None:
		self._cache: dict[tuple[int, int, int], CurrencySnapshot] = {}
		self._active = CurrencySnapshot(0.0, ())
		self._status = self.STATUS_IDLE

	@property
	def count(self) -> int:
		return len(CURRENCY_CODES)

	@property
	def status(self) -> int:
		return self._status

	@property
	def rate(self) -> float:
		return self._active.rate

	@property
	def point_count(self) -> int:
		return len(self._active.points)

	@property
	def snapshot_date(self) -> str:
		return SNAPSHOT_DATE

	def code(self, index: int) -> str:
		return CURRENCY_CODES[index] if 0 <= index < self.count else ""

	def point(self, index: int) -> float:
		if 0 <= index < len(self._active.points):
			return self._active.points[index]
		return 0.0

	def load(self, base_index: int, quote_index: int, range_id: int) -> bool:
		key = (int(base_index), int(quote_index), int(range_id))
		if not (
			0 <= key[0] < self.count
			and 0 <= key[1] < self.count
			and 0 <= key[2] <= 5
		):
			self._status = self.STATUS_ERROR
			return False

		snapshot = self._cache.get(key)
		if snapshot is None:
			snapshot = self._make_snapshot(*key)
			self._cache[key] = snapshot
		self._active = snapshot
		self._status = self.STATUS_READY
		return True

	@staticmethod
	def _range_rows(range_id: int) -> tuple[tuple[str, tuple[float, ...]], ...]:
		if range_id == 0:
			return DAILY_RATES[-2:]
		if range_id == 1:
			return DAILY_RATES[-6:]
		if range_id == 2:
			return DAILY_RATES[-8:]
		if range_id == 3:
			return DAILY_RATES[-31:]
		if range_id == 4:
			return WEEKLY_RATES
		return MONTHLY_RATES

	def _make_snapshot(
		self,
		base_index: int,
		quote_index: int,
		range_id: int,
	) -> CurrencySnapshot:
		rows = self._range_rows(range_id)
		if base_index == quote_index:
			return CurrencySnapshot(1.0, tuple(1.0 for _ in rows))

		def cross_rate(rates: tuple[float, ...]) -> float:
			return rates[quote_index] / rates[base_index]

		current_rate = cross_rate(DAILY_RATES[-1][1])
		points = tuple(cross_rate(rates) for _, rates in rows)
		return CurrencySnapshot(current_rate, points)
