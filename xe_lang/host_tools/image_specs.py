"""Extensible Image Studio tool and export metadata."""

from __future__ import annotations

from dataclasses import dataclass

from .image_document import ExportKind


@dataclass(frozen=True, slots=True)
class ImageToolSpec:
	key: str
	label: str
	compact_label: str
	shortcut: str


@dataclass(frozen=True, slots=True)
class ImageExportSpec:
	key: ExportKind
	label: str
	pattern: str
	suffix: str

	@property
	def dialog_filter(self) -> str:
		return f"{self.label} ({self.pattern})"


IMAGE_TOOLS: tuple[ImageToolSpec, ...] = (
	ImageToolSpec("pencil", "Pencil", "Pencil", "P"),
	ImageToolSpec("eraser", "Eraser", "Eraser", "E"),
	ImageToolSpec("fill", "Fill", "Fill", "F"),
	ImageToolSpec("eyedropper", "Pick", "Pick", "I"),
	ImageToolSpec("line", "Line", "Line", "L"),
	ImageToolSpec("rect", "Rectangle", "Rect", "R"),
	ImageToolSpec("ellipse", "Ellipse", "Ellipse", "O"),
	ImageToolSpec("select", "Select / move", "Select", "M"),
)


IMAGE_EXPORTS: tuple[ImageExportSpec, ...] = (
	ImageExportSpec("png", "PNG image", "*.png", ".png"),
	ImageExportSpec("gif", "Animated GIF", "*.gif", ".gif"),
	ImageExportSpec("sprite-sheet", "Sprite sheet", "*.png", ".png"),
	ImageExportSpec("scratch-sprite", "Scratch sprite", "*.sprite3", ".sprite3"),
	ImageExportSpec("xip", "Xe image project", "*.xip", ".xip"),
	ImageExportSpec("ximg", "Xe runtime image", "*.ximg", ".ximg"),
)


def export_dialog_filter() -> str:
	return ";;".join(spec.dialog_filter for spec in IMAGE_EXPORTS)


def export_spec_from_filter(selected_filter: str) -> ImageExportSpec:
	for spec in IMAGE_EXPORTS:
		if selected_filter == spec.dialog_filter:
			return spec
	return IMAGE_EXPORTS[0]
