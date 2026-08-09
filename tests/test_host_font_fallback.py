from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

from ide import PREFERRED_MONOSPACE_FAMILIES, X26IDE, _host_monospace_font


@pytest.fixture(scope="module", autouse=True)
def app() -> QApplication:
	return QApplication.instance() or QApplication([])


def test_missing_preferred_fonts_uses_fixed_font_then_generic_fallback() -> None:
	fixed = QFont("Fixed Font Fixture")
	font = _host_monospace_font(
		available_families=("Proportional Fixture",),
		fixed_font=fixed,
	)
	assert font.families()[0] == "Fixed Font Fixture"
	assert font.families()[-1].casefold() == "monospace"
	assert not any(name in font.families() for name in PREFERRED_MONOSPACE_FAMILIES)
	assert font.fixedPitch()
	assert font.pointSizeF() == 11.0


def test_real_system_fixed_font_keeps_generic_fallback_in_fontless_hosts() -> None:
	font = _host_monospace_font(available_families=())
	system_fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
	assert font.families()[0] == (system_fixed.family() or "monospace")
	assert font.families()[-1].casefold() == "monospace"
	assert font.fixedPitch()
	assert font.pointSizeF() >= 8.0


def test_fresh_workbench_applies_readable_fixed_font() -> None:
	window = X26IDE()
	try:
		font = window.font()
		assert font.families()
		assert font.fixedPitch()
		assert font.pointSizeF() >= 8.0
	finally:
		window.close()
