from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from ide import VMGraphicsWidget
from xe_lang.devices.input import InputDevice


class _Delta:
	def __init__(self, x: int, y: int) -> None:
		self._x = x
		self._y = y

	def x(self) -> int:
		return self._x

	def y(self) -> int:
		return self._y


class _WheelEvent:
	def __init__(self, *, x: int, y: int, shift: bool) -> None:
		self._angle = _Delta(x, y)
		self._pixel = _Delta(0, 0)
		self._modifiers = (
			Qt.KeyboardModifier.ShiftModifier
			if shift
			else Qt.KeyboardModifier.NoModifier
		)
		self.accepted = False

	def angleDelta(self) -> _Delta:
		return self._angle

	def pixelDelta(self) -> _Delta:
		return self._pixel

	def modifiers(self) -> Qt.KeyboardModifier:
		return self._modifiers

	def accept(self) -> None:
		self.accepted = True

	def ignore(self) -> None:
		self.accepted = False


def test_scroll_modifiers_are_latched_at_event_time() -> None:
	input_device = InputDevice(480, 360)
	input_device.add_scroll_delta(2, modifiers=1)
	input_device.set_key(0, False, modifiers=0)

	frame = input_device.frame()
	assert frame.scroll_delta == 2
	assert frame.modifiers == 1

	input_device.finish_frame()
	following = input_device.frame()
	assert following.scroll_delta == 0
	assert following.modifiers == 0


def test_stage_shift_wheel_accepts_horizontal_native_delta() -> None:
	app = QApplication.instance() or QApplication([])
	widget = VMGraphicsWidget()
	input_device = InputDevice(480, 360)
	widget.active_vm = SimpleNamespace(
		devices=SimpleNamespace(input=input_device),
	)
	widget._update_pointer = lambda event: True
	event = _WheelEvent(x=120, y=0, shift=True)

	widget.wheelEvent(event)
	frame = input_device.frame()

	assert app is not None
	assert event.accepted
	assert frame.scroll_delta == 1
	assert frame.modifiers & 1
	widget.deleteLater()
