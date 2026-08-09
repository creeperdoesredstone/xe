"""Small, deterministic host-rendering helpers with no widget ownership."""

from __future__ import annotations

import math
from collections.abc import Iterator

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtWidgets import QLineEdit, QWidget


class ElidingPathLineEdit(QLineEdit):
	"""Read-only, copyable path display that elides its middle when space is tight."""

	def __init__(self, text: str = "", parent: QWidget | None = None):
		super().__init__(parent)
		self._full_text = ""
		self.setReadOnly(True)
		self.setText(text)

	def setText(self, text: str) -> None:
		self._full_text = str(text)
		self._refresh_elision()

	def fullText(self) -> str:
		return self._full_text

	def resizeEvent(self, event) -> None:
		super().resizeEvent(event)
		self._refresh_elision()

	def _refresh_elision(self) -> None:
		available = max(0, self.contentsRect().width() - 12)
		display = self.fontMetrics().elidedText(
			self._full_text,
			Qt.TextElideMode.ElideMiddle,
			available,
		)
		QLineEdit.setText(self, display)
		self.setCursorPosition(0)
		self.setToolTip(self._full_text if display != self._full_text else "")


def visible_checker_cells(
	canvas: QRectF,
	viewport: QRectF,
	cell_size: float,
) -> Iterator[tuple[int, int, QRectF]]:
	"""Yield checker cells intersecting *viewport*, aligned to *canvas*.

	The alignment remains stable while panning, while iteration cost is bounded by
	the visible widget rather than the potentially enormous zoomed canvas.
	"""
	cell = max(1.0, float(cell_size))
	visible = canvas.intersected(viewport)
	if visible.isEmpty():
		return
	first_col = max(0, math.floor((visible.left() - canvas.left()) / cell))
	first_row = max(0, math.floor((visible.top() - canvas.top()) / cell))
	last_col = max(first_col, math.ceil((visible.right() - canvas.left()) / cell) - 1)
	last_row = max(first_row, math.ceil((visible.bottom() - canvas.top()) / cell) - 1)
	for row in range(first_row, last_row + 1):
		for col in range(first_col, last_col + 1):
			x = canvas.left() + col * cell
			y = canvas.top() + row * cell
			yield row, col, QRectF(x, y, cell, cell)
