"""Editable Xenon visual tokens, ordered primitive -> semantic -> component.

The VM and exporters consume these values as integer palette indices and logical
pixels.  Keeping the layers here makes a visual redesign a data change while the
window renderer, Settings preview, and portable backends retain identical rules.
"""

from __future__ import annotations

from dataclasses import dataclass


# Primitive tokens: raw indexed palettes and logical pixel measurements.
PALETTE_PRIMITIVES: tuple[tuple[str, ...], ...] = (
	(
		"#020716", "#102552", "#006b67", "#00a7a0",
		"#7f1d1d", "#43008f", "#a16207", "#b8bcd8",
		"#273b63", "#6c18d8", "#4dffae", "#aefcff",
		"#fb7185", "#ad55ef", "#fde047", "#f4f0ff",
	),
	(
		"#09030f", "#21113a", "#183447", "#147d92",
		"#9f2445", "#5112a8", "#c26c22", "#d8c9eb",
		"#5b496c", "#7c35e8", "#39d98a", "#66e3ff",
		"#ff6685", "#c45cff", "#ffcf5c", "#fff8ff",
	),
	(
		"#000000", "#101820", "#005a46", "#00a88f",
		"#8b0000", "#660099", "#9a5b00", "#c0c0c0",
		"#555555", "#246bfe", "#44d544", "#00ffff",
		"#ff4040", "#d060ff", "#ffff00", "#ffffff",
	),
	(
		"#f7f5ff", "#dce5ff", "#d5f5ef", "#86ded6",
		"#ffd9df", "#e8dcff", "#fff0b8", "#42475d",
		"#b9c8e8", "#b084ef", "#157a59", "#096a78",
		"#b62949", "#6d2daf", "#8a6500", "#151525",
	),
	(
		"#fff8ff", "#eadff5", "#d8ecf1", "#99d5df",
		"#ffd0dc", "#e1cffc", "#ffe4bd", "#514a60",
		"#c7b9d4", "#a87bea", "#147957", "#087081",
		"#b5284b", "#7134b5", "#826000", "#1b1422",
	),
	(
		"#f6f7f8", "#dce3e8", "#d1eee4", "#83d8c7",
		"#ffd7d7", "#e9d8f0", "#f5e4bb", "#4d5359",
		"#bcc4ca", "#87a9ec", "#31845b", "#147982",
		"#b83535", "#75438c", "#746000", "#16191c",
	),
)

WINDOW_MEASURE_PRIMITIVES = {
	"title_height": 18,
	"border_width": 2,
	"title_text_offset": 4,
	"control_size": 12,
	"control_gap": 2,
	"minimum_width": 72,
	"minimum_height": 54,
	"resize_grab": 5,
	"resize_outer_grab": 5,
	"maximize_snap_margin": 6,
	"rounded_corner_inset": 2,
}


# Semantic tokens: meaning assigned to palette indices and primitive measures.
PALETTES = PALETTE_PRIMITIVES
BACKGROUND_NAMES = ("Black", "Navy", "Slate")
BACKGROUND_COLOR_INDICES = (0, 1, 8)
WINDOW_COLOR_SEMANTICS = {
	"border": 13,
	"title": 13,
	"content": 0,
	"text": 15,
	"outline": 15,
	"control": 5,
	"control_hover": 13,
	"control_pressed": 9,
	"slider_track": 8,
	"slider_fill": 11,
}

THEME_DARK = 0
THEME_LIGHT = 1
WINDOW_CORNER_SQUARE = 0
WINDOW_CORNER_ROUNDED = 1
LEGACY_WINDOW_CORNER_SOFT = 2


# Component tokens: the complete default window chrome contract.
@dataclass(frozen=True)
class WindowComponentTokens:
	title_height: int = WINDOW_MEASURE_PRIMITIVES["title_height"]
	border_width: int = WINDOW_MEASURE_PRIMITIVES["border_width"]
	border_color: int = WINDOW_COLOR_SEMANTICS["border"]
	title_color: int = WINDOW_COLOR_SEMANTICS["title"]
	content_color: int = WINDOW_COLOR_SEMANTICS["content"]
	text_color: int = WINDOW_COLOR_SEMANTICS["text"]
	outline_color: int = WINDOW_COLOR_SEMANTICS["outline"]
	button_color: int = WINDOW_COLOR_SEMANTICS["control"]
	button_hover_color: int = WINDOW_COLOR_SEMANTICS["control_hover"]
	button_pressed_color: int = WINDOW_COLOR_SEMANTICS["control_pressed"]
	slider_track_color: int = WINDOW_COLOR_SEMANTICS["slider_track"]
	slider_fill_color: int = WINDOW_COLOR_SEMANTICS["slider_fill"]
	title_text_offset: int = WINDOW_MEASURE_PRIMITIVES["title_text_offset"]
	control_size: int = WINDOW_MEASURE_PRIMITIVES["control_size"]
	control_gap: int = WINDOW_MEASURE_PRIMITIVES["control_gap"]
	minimum_width: int = WINDOW_MEASURE_PRIMITIVES["minimum_width"]
	minimum_height: int = WINDOW_MEASURE_PRIMITIVES["minimum_height"]
	resize_grab: int = WINDOW_MEASURE_PRIMITIVES["resize_grab"]
	resize_outer_grab: int = WINDOW_MEASURE_PRIMITIVES["resize_outer_grab"]
	maximize_snap_margin: int = WINDOW_MEASURE_PRIMITIVES["maximize_snap_margin"]
	rounded_corner_inset: int = WINDOW_MEASURE_PRIMITIVES["rounded_corner_inset"]


WINDOW_COMPONENT_TOKENS = WindowComponentTokens()


def normalize_window_corner_style(value: object) -> int | None:
	"""Return a supported corner style, migrating the removed soft style."""

	try:
		style = int(value)
	except (TypeError, ValueError):
		return None
	if style == WINDOW_CORNER_SQUARE:
		return WINDOW_CORNER_SQUARE
	if style in (WINDOW_CORNER_ROUNDED, LEGACY_WINDOW_CORNER_SOFT):
		return WINDOW_CORNER_ROUNDED
	return None


def normalize_theme_palette(palette_id: int, theme_mode: int) -> int | None:
	"""Keep a palette variant while moving it into the requested theme group."""

	palette_id = int(palette_id)
	theme_mode = int(theme_mode)
	if not 0 <= palette_id < len(PALETTES) or theme_mode not in (THEME_DARK, THEME_LIGHT):
		return None
	group_size = len(PALETTES) // 2
	if theme_mode == THEME_LIGHT and palette_id < group_size:
		return palette_id + group_size
	if theme_mode == THEME_DARK and palette_id >= group_size:
		return palette_id - group_size
	return palette_id
