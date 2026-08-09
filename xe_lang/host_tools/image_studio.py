"""Layered image and animation editor embedded in the desktop Xenon IDE."""

from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
	QColor,
	QIcon,
	QImage,
	QKeySequence,
	QMouseEvent,
	QPainter,
	QPen,
	QPixmap,
	QShortcut,
	QWheelEvent,
)
from PyQt6.QtWidgets import (
	QButtonGroup,
	QAbstractItemView,
	QCheckBox,
	QColorDialog,
	QComboBox,
	QFileDialog,
	QFrame,
	QHBoxLayout,
	QInputDialog,
	QLabel,
	QListWidget,
	QListWidgetItem,
	QMessageBox,
	QPushButton,
	QSlider,
	QSizePolicy,
	QSpinBox,
	QSplitter,
	QToolButton,
	QVBoxLayout,
	QWidget,
)

from .image_document import (
	ExportKind,
	ImageProject,
	ImageProjectCodec,
	ImageStudioDocument,
	load_default_image_codec,
	quantize_xvm_image,
)


TOOLS: tuple[tuple[str, str, str], ...] = (
	("pencil", "Pencil", "P"),
	("eraser", "Eraser", "E"),
	("fill", "Fill", "F"),
	("eyedropper", "Pick", "I"),
	("line", "Line", "L"),
	("rect", "Rectangle", "R"),
	("ellipse", "Ellipse", "O"),
	("select", "Select / move", "M"),
)


class ImageCanvas(QWidget):
	document_changed = pyqtSignal()
	color_picked = pyqtSignal(QColor)
	zoom_changed = pyqtSignal(float)

	def __init__(self, document: ImageStudioDocument, parent: QWidget | None = None):
		super().__init__(parent)
		self.document = document
		self.tool = "pencil"
		self.color = QColor("#36d9ff")
		self.brush_size = 1
		self.zoom = 6.0
		self.pan = QPointF()
		self.onion_skin = False
		self._pointer_down = False
		self._panning = False
		self._last_widget_pos = QPointF()
		self._start_image_pos: QPoint | None = None
		self._last_image_pos: QPoint | None = None
		self._preview_image_pos: QPoint | None = None
		self.selection_rect = QRect()
		self._moving_selection = None
		self._selection_anchor = QPoint()
		self._space_down = False
		self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
		self.setMouseTracking(True)
		self.setMinimumSize(280, 230)
		self.setAccessibleName("Image canvas")

	def set_document(self, document: ImageStudioDocument) -> None:
		self.document = document
		self.selection_rect = QRect()
		self.fit_to_view()

	def fit_to_view(self) -> None:
		project = self.document.project
		available_w = max(1, self.width() - 48)
		available_h = max(1, self.height() - 48)
		self.zoom = max(0.125, min(32.0, available_w / project.width, available_h / project.height))
		if project.width <= 128 and project.height <= 128:
			self.zoom = max(1.0, math.floor(self.zoom))
		self.pan = QPointF()
		self.zoom_changed.emit(self.zoom)
		self.update()

	def _canvas_origin(self) -> QPointF:
		project = self.document.project
		return QPointF(
			(self.width() - project.width * self.zoom) / 2 + self.pan.x(),
			(self.height() - project.height * self.zoom) / 2 + self.pan.y(),
		)

	def _canvas_rect(self) -> QRectF:
		origin = self._canvas_origin()
		project = self.document.project
		return QRectF(origin.x(), origin.y(), project.width * self.zoom, project.height * self.zoom)

	def _to_image(self, position: QPointF, *, clamp: bool = False) -> QPoint | None:
		origin = self._canvas_origin()
		x = math.floor((position.x() - origin.x()) / self.zoom)
		y = math.floor((position.y() - origin.y()) / self.zoom)
		project = self.document.project
		if clamp:
			x = min(max(x, 0), project.width - 1)
			y = min(max(y, 0), project.height - 1)
			return QPoint(x, y)
		if 0 <= x < project.width and 0 <= y < project.height:
			return QPoint(x, y)
		return None

	def paintEvent(self, event) -> None:
		painter = QPainter(self)
		painter.fillRect(self.rect(), QColor("#080b12"))
		canvas = self._canvas_rect()
		painter.save()
		painter.setClipRect(canvas)
		cell = max(4.0, min(12.0, self.zoom * 2))
		start_x = canvas.left()
		start_y = canvas.top()
		for row, y in enumerate(self._frange(start_y, canvas.bottom(), cell)):
			for col, x in enumerate(self._frange(start_x, canvas.right(), cell)):
				color = QColor("#232a37") if (row + col) % 2 else QColor("#1a202c")
				painter.fillRect(QRectF(x, y, cell, cell), color)
		painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
		project = self.document.project
		if self.onion_skin and project.frame_count > 1:
			if project.current_frame > 0:
				painter.setOpacity(0.22)
				painter.drawImage(canvas, project.composite(project.current_frame - 1))
			if project.current_frame + 1 < project.frame_count:
				painter.setOpacity(0.16)
				painter.drawImage(canvas, project.composite(project.current_frame + 1))
		painter.setOpacity(1.0)
		painter.drawImage(canvas, project.composite())
		painter.restore()

		painter.setPen(QPen(QColor("#4c596f"), 1))
		painter.drawRect(canvas.adjusted(-1, -1, 1, 1))
		self._paint_preview(painter)
		painter.end()

	@staticmethod
	def _frange(start: float, stop: float, step: float):
		value = start
		while value < stop:
			yield value
			value += step

	def _screen_rect(self, rect: QRect) -> QRectF:
		origin = self._canvas_origin()
		return QRectF(
			origin.x() + rect.x() * self.zoom,
			origin.y() + rect.y() * self.zoom,
			rect.width() * self.zoom,
			rect.height() * self.zoom,
		)

	def _paint_preview(self, painter: QPainter) -> None:
		origin = self._canvas_origin()
		if self._start_image_pos is not None and self._preview_image_pos is not None and self.tool in {"line", "rect", "ellipse"}:
			start = QPointF(
				origin.x() + (self._start_image_pos.x() + 0.5) * self.zoom,
				origin.y() + (self._start_image_pos.y() + 0.5) * self.zoom,
			)
			end = QPointF(
				origin.x() + (self._preview_image_pos.x() + 0.5) * self.zoom,
				origin.y() + (self._preview_image_pos.y() + 0.5) * self.zoom,
			)
			painter.setPen(QPen(self.color, max(1.0, self.brush_size * self.zoom)))
			if self.tool == "line":
				painter.drawLine(start, end)
			else:
				rect = QRectF(start, end).normalized()
				if self.tool == "rect":
					painter.drawRect(rect)
				else:
					painter.drawEllipse(rect)
		if self._moving_selection is not None and not self.selection_rect.isNull():
			painter.setOpacity(0.82)
			painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
			painter.drawImage(self._screen_rect(self.selection_rect), self._moving_selection)
			painter.setOpacity(1.0)
		if not self.selection_rect.isNull():
			painter.setPen(QPen(QColor("#ffffff"), 1, Qt.PenStyle.DashLine))
			painter.setBrush(Qt.BrushStyle.NoBrush)
			painter.drawRect(self._screen_rect(self.selection_rect))

	def wheelEvent(self, event: QWheelEvent) -> None:
		old_zoom = self.zoom
		factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
		new_zoom = min(64.0, max(0.125, old_zoom * factor))
		if abs(new_zoom - old_zoom) < 0.0001:
			return
		before = self._to_image_fraction(event.position())
		self.zoom = new_zoom
		after_origin = self._canvas_origin()
		self.pan += QPointF(
			event.position().x() - (after_origin.x() + before.x() * new_zoom),
			event.position().y() - (after_origin.y() + before.y() * new_zoom),
		)
		self.zoom_changed.emit(self.zoom)
		self.update()
		event.accept()

	def _to_image_fraction(self, position: QPointF) -> QPointF:
		origin = self._canvas_origin()
		return QPointF((position.x() - origin.x()) / self.zoom, (position.y() - origin.y()) / self.zoom)

	def mousePressEvent(self, event: QMouseEvent) -> None:
		self.setFocus(Qt.FocusReason.MouseFocusReason)
		self._last_widget_pos = event.position()
		if event.button() == Qt.MouseButton.MiddleButton or (
			event.button() == Qt.MouseButton.LeftButton and self._space_down
		):
			self._panning = True
			self.setCursor(Qt.CursorShape.ClosedHandCursor)
			event.accept()
			return
		if event.button() != Qt.MouseButton.LeftButton:
			return
		position = self._to_image(event.position())
		if position is None:
			return
		self._pointer_down = True
		self._start_image_pos = position
		self._last_image_pos = position
		self._preview_image_pos = position

		if self.tool in {"pencil", "eraser"}:
			self.document.checkpoint()
			self.document.draw_line(position, position, self.color, self.brush_size, self.tool == "eraser")
			self._emit_change()
		elif self.tool == "fill":
			image = self.document.current_image()
			if image.pixelColor(position).rgba() != self.color.rgba():
				self.document.checkpoint()
				if self.document.flood_fill(position, self.color):
					self._emit_change()
			self._pointer_down = False
		elif self.tool == "eyedropper":
			self.color_picked.emit(self.document.project.composite().pixelColor(position))
			self._pointer_down = False
		elif self.tool == "select":
			if self.selection_rect.contains(position) and not self.selection_rect.isNull():
				self.document.checkpoint()
				self._moving_selection = self.document.current_image().copy(self.selection_rect)
				self._selection_anchor = position - self.selection_rect.topLeft()
				painter = QPainter(self.document.current_image())
				painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
				painter.fillRect(self.selection_rect, Qt.GlobalColor.transparent)
				painter.end()
				self.document.project.invalidate()
			else:
				self.selection_rect = QRect(position, QSize(1, 1))
		self.update()

	def mouseMoveEvent(self, event: QMouseEvent) -> None:
		if self._panning:
			delta = event.position() - self._last_widget_pos
			self.pan += delta
			self._last_widget_pos = event.position()
			self.update()
			return
		if not self._pointer_down:
			return
		position = self._to_image(event.position(), clamp=True)
		if position is None:
			return
		self._preview_image_pos = position
		if self.tool in {"pencil", "eraser"} and self._last_image_pos is not None:
			self.document.draw_line(self._last_image_pos, position, self.color, self.brush_size, self.tool == "eraser")
			self._last_image_pos = position
			self._emit_change()
		elif self.tool == "select" and self._start_image_pos is not None:
			if self._moving_selection is not None:
				top_left = position - self._selection_anchor
				project = self.document.project
				top_left.setX(min(max(top_left.x(), 0), project.width - self._moving_selection.width()))
				top_left.setY(min(max(top_left.y(), 0), project.height - self._moving_selection.height()))
				self.selection_rect.moveTopLeft(top_left)
			else:
				self.selection_rect = QRect(self._start_image_pos, position).normalized()
			self.update()
		else:
			self.update()

	def mouseReleaseEvent(self, event: QMouseEvent) -> None:
		if self._panning:
			self._panning = False
			self.unsetCursor()
			return
		if event.button() != Qt.MouseButton.LeftButton or not self._pointer_down:
			return
		position = self._to_image(event.position(), clamp=True)
		if position is not None and self._start_image_pos is not None:
			if self.tool in {"line", "rect", "ellipse"}:
				self.document.checkpoint()
				self.document.draw_shape(self.tool, self._start_image_pos, position, self.color, self.brush_size)
				self._emit_change()
			elif self.tool == "select" and self._moving_selection is not None:
				painter = QPainter(self.document.current_image())
				painter.drawImage(self.selection_rect.topLeft(), self._moving_selection)
				painter.end()
				self._moving_selection = None
				self._emit_change()
		self._pointer_down = False
		self._start_image_pos = None
		self._last_image_pos = None
		self._preview_image_pos = None
		self.update()

	def keyPressEvent(self, event) -> None:
		if event.key() == Qt.Key.Key_Space:
			self._space_down = True
			self.setCursor(Qt.CursorShape.OpenHandCursor)
			return
		if event.key() == Qt.Key.Key_Escape:
			self.selection_rect = QRect()
			self.update()
			return
		super().keyPressEvent(event)

	def keyReleaseEvent(self, event) -> None:
		if event.key() == Qt.Key.Key_Space:
			self._space_down = False
			self.unsetCursor()
			return
		super().keyReleaseEvent(event)

	def _emit_change(self) -> None:
		self.document.modified = True
		self.document.project.invalidate()
		self.document_changed.emit()
		self.update()


class ImageStudioPane(QWidget):
	def __init__(
		self,
		codec: ImageProjectCodec | None = None,
		parent: QWidget | None = None,
	):
		super().__init__(parent)
		self.codec = codec or load_default_image_codec()
		self.document = ImageStudioDocument()
		self.current_path: Path | None = None
		self.play_timer = QTimer(self)
		self.play_timer.timeout.connect(self._advance_playback)
		self._refreshing = False
		self._build_ui()
		self._refresh_all()

	def _build_ui(self) -> None:
		root = QVBoxLayout(self)
		root.setContentsMargins(12, 10, 12, 12)
		root.setSpacing(8)

		header = QHBoxLayout()
		title = QLabel("Image Studio")
		title.setObjectName("ToolTitle")
		header.addWidget(title)
		self.document_label = QLabel("Untitled · 64 × 64")
		self.document_label.setObjectName("MutedText")
		header.addWidget(self.document_label)
		header.addStretch(1)
		for text, slot in (("New", self.new_project), ("Import…", self.import_file), ("Export…", self.export_file)):
			button = QPushButton(text)
			button.setObjectName("PrimaryButton" if text.startswith("Export") else "SecondaryButton")
			button.clicked.connect(slot)
			header.addWidget(button)
		root.addLayout(header)

		toolbar = QFrame()
		toolbar.setObjectName("ToolBarCard")
		toolbar_layout = QHBoxLayout(toolbar)
		toolbar_layout.setContentsMargins(8, 6, 8, 6)
		toolbar_layout.setSpacing(4)
		self.tool_group = QButtonGroup(self)
		self.tool_group.setExclusive(True)
		self.tool_buttons: dict[str, QToolButton] = {}
		for index, (tool, label, shortcut) in enumerate(TOOLS):
			button = QToolButton()
			button.setText(label)
			button.setCheckable(True)
			button.setMinimumWidth(34)
			button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
			button.setToolTip(f"{label} ({shortcut})")
			button.setAccessibleName(f"{label} tool")
			button.clicked.connect(lambda checked, name=tool: self._set_tool(name))
			self.tool_group.addButton(button, index)
			self.tool_buttons[tool] = button
			toolbar_layout.addWidget(button, 1)
		self.tool_buttons["pencil"].setChecked(True)
		toolbar_layout.addSpacing(8)

		self.color_button = QPushButton()
		self.color_button.setFixedSize(30, 26)
		self.color_button.setToolTip("Drawing colour")
		self.color_button.clicked.connect(self._choose_color)
		toolbar_layout.addWidget(self.color_button)
		toolbar_layout.addWidget(QLabel("Size"))
		self.brush_size = QSpinBox()
		self.brush_size.setRange(1, 64)
		self.brush_size.setValue(1)
		self.brush_size.setMaximumWidth(64)
		self.brush_size.valueChanged.connect(self._set_brush_size)
		toolbar_layout.addWidget(self.brush_size)
		toolbar_layout.addStretch(1)
		self.undo_button = QToolButton()
		self.undo_button.setText("Undo")
		self.undo_button.clicked.connect(self.undo)
		self.redo_button = QToolButton()
		self.redo_button.setText("Redo")
		self.redo_button.clicked.connect(self.redo)
		toolbar_layout.addWidget(self.undo_button)
		toolbar_layout.addWidget(self.redo_button)
		root.addWidget(toolbar)

		main = QSplitter(Qt.Orientation.Horizontal)
		self.canvas = ImageCanvas(self.document)
		self.canvas.document_changed.connect(self._refresh_all)
		self.canvas.color_picked.connect(self._set_color)
		self.canvas.zoom_changed.connect(self._show_zoom)
		main.addWidget(self.canvas)

		inspector = QWidget()
		inspector.setMinimumWidth(220)
		inspector.setMaximumWidth(330)
		inspector_layout = QVBoxLayout(inspector)
		inspector_layout.setContentsMargins(8, 0, 0, 0)
		inspector_layout.setSpacing(8)
		preview_header = QHBoxLayout()
		preview_header.addWidget(QLabel("Target preview"))
		self.target_combo = QComboBox()
		self.target_combo.addItem("Native", "native")
		self.target_combo.addItem("Scratch stage", "scratch")
		self.target_combo.addItem("Indexed 16-colour", "indexed")
		self.target_combo.currentIndexChanged.connect(self._refresh_preview)
		preview_header.addWidget(self.target_combo, 1)
		inspector_layout.addLayout(preview_header)
		self.preview = QLabel()
		self.preview.setObjectName("ImagePreview")
		self.preview.setMinimumHeight(138)
		self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
		inspector_layout.addWidget(self.preview)
		self.preview_note = QLabel()
		self.preview_note.setObjectName("MutedText")
		self.preview_note.setWordWrap(True)
		inspector_layout.addWidget(self.preview_note)

		layer_header = QHBoxLayout()
		layer_header.addWidget(QLabel("Layers"))
		layer_header.addStretch(1)
		add_layer = QToolButton()
		add_layer.setText("+")
		add_layer.setToolTip("Add layer")
		add_layer.clicked.connect(self.add_layer)
		remove_layer = QToolButton()
		remove_layer.setText("−")
		remove_layer.setToolTip("Remove selected layer")
		remove_layer.clicked.connect(self.remove_layer)
		layer_header.addWidget(add_layer)
		layer_header.addWidget(remove_layer)
		inspector_layout.addLayout(layer_header)
		self.layer_list = QListWidget()
		self.layer_list.setObjectName("LayerList")
		self.layer_list.setEditTriggers(
			QAbstractItemView.EditTrigger.DoubleClicked
			| QAbstractItemView.EditTrigger.EditKeyPressed
		)
		self.layer_list.currentRowChanged.connect(self._select_layer)
		self.layer_list.itemChanged.connect(self._layer_item_changed)
		inspector_layout.addWidget(self.layer_list, 1)

		opacity_row = QHBoxLayout()
		opacity_row.addWidget(QLabel("Opacity"))
		self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
		self.opacity_slider.setRange(0, 100)
		self.opacity_slider.setValue(100)
		self.opacity_slider.sliderPressed.connect(self._begin_layer_opacity)
		self.opacity_slider.valueChanged.connect(self._set_layer_opacity)
		opacity_row.addWidget(self.opacity_slider, 1)
		inspector_layout.addLayout(opacity_row)
		main.addWidget(inspector)
		main.setStretchFactor(0, 1)
		main.setStretchFactor(1, 0)
		root.addWidget(main, 1)

		timeline = QFrame()
		timeline.setObjectName("ToolCard")
		timeline_layout = QHBoxLayout(timeline)
		timeline_layout.setContentsMargins(8, 6, 8, 6)
		self.play_button = QToolButton()
		self.play_button.setText("▶")
		self.play_button.setToolTip("Play animation")
		self.play_button.setCheckable(True)
		self.play_button.toggled.connect(self._toggle_playback)
		timeline_layout.addWidget(self.play_button)
		self.onion_checkbox = QCheckBox("Onion skin")
		self.onion_checkbox.toggled.connect(self._toggle_onion)
		timeline_layout.addWidget(self.onion_checkbox)
		self.frame_list = QListWidget()
		self.frame_list.setFlow(QListWidget.Flow.LeftToRight)
		self.frame_list.setWrapping(False)
		self.frame_list.setMaximumHeight(58)
		self.frame_list.currentRowChanged.connect(self._select_frame)
		timeline_layout.addWidget(self.frame_list, 1)
		for text, tooltip, slot in (
			("+", "Add blank frame", self.add_frame),
			("Copy", "Duplicate current frame", self.copy_frame),
			("−", "Remove current frame", self.remove_frame),
		):
			button = QToolButton()
			button.setText(text)
			button.setToolTip(tooltip)
			button.clicked.connect(slot)
			timeline_layout.addWidget(button)
		self.zoom_label = QLabel("100%")
		self.zoom_label.setMinimumWidth(52)
		timeline_layout.addWidget(self.zoom_label)
		fit_button = QToolButton()
		fit_button.setText("Fit")
		fit_button.clicked.connect(self.canvas.fit_to_view)
		timeline_layout.addWidget(fit_button)
		timeline_layout.addWidget(QLabel("ms"))
		self.duration_spin = QSpinBox()
		self.duration_spin.setRange(20, 60_000)
		self.duration_spin.setSingleStep(10)
		self.duration_spin.setMaximumWidth(78)
		self.duration_spin.setToolTip("Current frame duration in milliseconds")
		self.duration_spin.editingFinished.connect(self._set_frame_duration)
		timeline_layout.addWidget(self.duration_spin)
		root.addWidget(timeline)

		QShortcut(QKeySequence.StandardKey.Undo, self.canvas, activated=self.undo)
		QShortcut(QKeySequence.StandardKey.Redo, self.canvas, activated=self.redo)
		for tool, _, shortcut in TOOLS:
			QShortcut(
				QKeySequence(shortcut),
				self.canvas,
				activated=lambda name=tool: self._activate_tool(name),
			)
		self._set_color(self.canvas.color)

	def set_codec(self, codec: ImageProjectCodec) -> None:
		self.codec = codec

	def _activate_tool(self, tool: str) -> None:
		self.tool_buttons[tool].setChecked(True)
		self._set_tool(tool)

	def _set_tool(self, tool: str) -> None:
		self.canvas.tool = tool
		self.canvas.setFocus(Qt.FocusReason.ShortcutFocusReason)

	def _set_brush_size(self, value: int) -> None:
		self.canvas.brush_size = value

	def _choose_color(self) -> None:
		color = QColorDialog.getColor(self.canvas.color, self, "Drawing colour", QColorDialog.ColorDialogOption.ShowAlphaChannel)
		if color.isValid():
			self._set_color(color)

	def _set_color(self, color: QColor) -> None:
		self.canvas.color = color
		text = "#000000" if color.lightness() > 150 else "#ffffff"
		self.color_button.setStyleSheet(
			f"background:{color.name(QColor.NameFormat.HexArgb)}; color:{text}; border:1px solid #718096;"
		)
		self.color_button.setAccessibleName(f"Drawing colour {color.name(QColor.NameFormat.HexArgb)}")

	def new_project(self) -> None:
		width, ok = QInputDialog.getInt(self, "New image", "Width", 64, 1, 4096)
		if not ok:
			return
		height, ok = QInputDialog.getInt(self, "New image", "Height", 64, 1, 4096)
		if not ok:
			return
		if not self._confirm_discard_changes():
			return
		self.play_button.setChecked(False)
		self.document.replace_project(ImageProject.blank(width, height))
		self.current_path = None
		self.canvas.selection_rect = QRect()
		self.canvas.fit_to_view()
		self._refresh_all()

	def import_file(self) -> None:
		path, _ = QFileDialog.getOpenFileName(
			self,
			"Import image or animation",
			"",
			"Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.xip *.ximg);;All files (*)",
		)
		if not path:
			return
		try:
			project = self.codec.import_file(Path(path))
		except Exception as exc:
			QMessageBox.warning(self, "Import failed", str(exc))
			return
		if not self._confirm_discard_changes():
			return
		self.play_button.setChecked(False)
		self.document.replace_project(project)
		self.current_path = Path(path)
		self.canvas.set_document(self.document)
		self._refresh_all()

	def export_file(self) -> None:
		path, selected = QFileDialog.getSaveFileName(
			self,
			"Export image",
			self.document.project.name,
			"PNG image (*.png);;Animated GIF (*.gif);;Sprite sheet (*.png);;Xe image project (*.xip);;Xe runtime image (*.ximg)",
		)
		if not path:
			return
		kind, suffix = self._export_selection(selected)
		output = Path(path)
		if output.suffix.lower() != suffix:
			output = output.with_suffix(suffix)
		try:
			self.codec.export_file(self.document.project, output, kind)
		except Exception as exc:
			QMessageBox.warning(
				self,
				"Export not written",
				f"{exc}\n\nThe existing destination, if any, was left unchanged.",
			)
			return
		if kind == "xip":
			self.current_path = output
			self.document.modified = False
		self._refresh_all()
		QMessageBox.information(self, "Export complete", f"Written to:\n{output}")

	def _confirm_discard_changes(self) -> bool:
		if not self.document.modified:
			return True
		answer = QMessageBox.question(
			self,
			"Discard image changes?",
			"The current Image Studio project has unsaved changes. "
			"Export an .xip project first if you want to keep its layers and frames.",
			QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
			QMessageBox.StandardButton.Cancel,
		)
		return answer == QMessageBox.StandardButton.Discard

	@staticmethod
	def _export_selection(selected_filter: str) -> tuple[ExportKind, str]:
		if "GIF" in selected_filter:
			return "gif", ".gif"
		if "Sprite" in selected_filter:
			return "sprite-sheet", ".png"
		if "project" in selected_filter:
			return "xip", ".xip"
		if "runtime" in selected_filter:
			return "ximg", ".ximg"
		return "png", ".png"

	def undo(self) -> None:
		if self.document.undo():
			self.canvas.set_document(self.document)
			self._refresh_all()

	def redo(self) -> None:
		if self.document.redo():
			self.canvas.set_document(self.document)
			self._refresh_all()

	def add_layer(self) -> None:
		self.document.add_layer()
		self._refresh_all()

	def remove_layer(self) -> None:
		if not self.document.remove_layer(self.document.project.current_layer):
			QMessageBox.information(self, "Layer kept", "An image must have at least one layer.")
		self._refresh_all()

	def add_frame(self) -> None:
		self.document.add_frame(copy_current=False)
		self._refresh_all()

	def copy_frame(self) -> None:
		self.document.add_frame(copy_current=True)
		self._refresh_all()

	def remove_frame(self) -> None:
		if not self.document.remove_frame(self.document.project.current_frame):
			QMessageBox.information(self, "Frame kept", "An image must have at least one frame.")
		self._refresh_all()

	def _select_layer(self, row: int) -> None:
		if self._refreshing or row < 0:
			return
		index = len(self.document.project.layers) - 1 - row
		if 0 <= index < len(self.document.project.layers):
			self.document.project.current_layer = index
			self.opacity_slider.setValue(round(self.document.project.layers[index].opacity * 100))
			self.canvas.update()

	def _layer_item_changed(self, item: QListWidgetItem) -> None:
		if self._refreshing:
			return
		row = self.layer_list.row(item)
		index = len(self.document.project.layers) - 1 - row
		if not 0 <= index < len(self.document.project.layers):
			return
		layer = self.document.project.layers[index]
		visible = item.checkState() == Qt.CheckState.Checked
		name = item.text().strip() or layer.name
		if visible != layer.visible or name != layer.name:
			self.document.checkpoint()
			layer.visible = visible
			layer.name = name
			self.document.project.invalidate()
			self._refresh_preview()
			self.canvas.update()

	def _set_layer_opacity(self, value: int) -> None:
		if self._refreshing:
			return
		layer = self.document.project.layers[self.document.project.current_layer]
		new_opacity = value / 100
		if abs(layer.opacity - new_opacity) > 0.001:
			if not self.opacity_slider.isSliderDown():
				self.document.checkpoint()
			layer.opacity = new_opacity
			self.document.project.invalidate()
			self.document.modified = True
			self.canvas.update()
			self._refresh_preview()

	def _begin_layer_opacity(self) -> None:
		if not self._refreshing:
			self.document.checkpoint()

	def _set_frame_duration(self) -> None:
		if self._refreshing:
			return
		project = self.document.project
		value = self.duration_spin.value()
		if project.frame_durations_ms[project.current_frame] != value:
			self.document.checkpoint()
			project.frame_durations_ms[project.current_frame] = value
			self._refresh_all()

	def _select_frame(self, row: int) -> None:
		if self._refreshing or not 0 <= row < self.document.project.frame_count:
			return
		self.document.project.current_frame = row
		self.canvas.selection_rect = QRect()
		self.canvas.update()
		self._refresh_preview()

	def _toggle_onion(self, enabled: bool) -> None:
		self.canvas.onion_skin = enabled
		self.canvas.update()

	def _toggle_playback(self, enabled: bool) -> None:
		self.play_button.setText("■" if enabled else "▶")
		self.play_button.setToolTip("Stop animation" if enabled else "Play animation")
		if enabled:
			duration = self.document.project.frame_durations_ms[self.document.project.current_frame]
			self.play_timer.start(max(20, duration))
		else:
			self.play_timer.stop()

	def _advance_playback(self) -> None:
		project = self.document.project
		project.current_frame = (project.current_frame + 1) % project.frame_count
		self._refresh_frames()
		self.canvas.update()
		self._refresh_preview()
		self.play_timer.start(max(20, project.frame_durations_ms[project.current_frame]))

	def _show_zoom(self, zoom: float) -> None:
		self.zoom_label.setText(f"{round(zoom * 100)}%")

	def _refresh_all(self) -> None:
		self._refreshing = True
		try:
			project = self.document.project
			marker = "*" if self.document.modified else ""
			self.document_label.setText(f"{project.name}{marker} · {project.width} × {project.height}")
			self._refresh_layers()
			self._refresh_frames()
			self.undo_button.setEnabled(self.document.can_undo)
			self.redo_button.setEnabled(self.document.can_redo)
			self._refresh_preview()
			self.canvas.update()
		finally:
			self._refreshing = False

	def _refresh_layers(self) -> None:
		project = self.document.project
		self.layer_list.clear()
		for layer in reversed(project.layers):
			item = QListWidgetItem(layer.name)
			item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsUserCheckable)
			item.setCheckState(Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked)
			self.layer_list.addItem(item)
		row = len(project.layers) - 1 - project.current_layer
		self.layer_list.setCurrentRow(row)
		self.opacity_slider.setValue(round(project.layers[project.current_layer].opacity * 100))

	def _refresh_frames(self) -> None:
		project = self.document.project
		self.frame_list.clear()
		for index in range(project.frame_count):
			pixmap = QPixmap.fromImage(project.composite(index)).scaled(
				32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
			)
			item = QListWidgetItem(QIcon(pixmap), str(index + 1))
			item.setSizeHint(QSize(54, 42))
			self.frame_list.addItem(item)
		self.frame_list.setCurrentRow(project.current_frame)
		self.frame_list.scrollToItem(self.frame_list.currentItem())
		self.duration_spin.setValue(project.frame_durations_ms[project.current_frame])

	def _refresh_preview(self) -> None:
		if not hasattr(self, "preview"):
			return
		project = self.document.project
		image = project.composite()
		target = self.target_combo.currentData()
		if target == "scratch":
			canvas = QPixmap(480, 360)
			canvas.fill(QColor("#080b12"))
			painter = QPainter(canvas)
			scaled = QPixmap.fromImage(image).scaled(
				480, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
			)
			painter.drawPixmap((480 - scaled.width()) // 2, (360 - scaled.height()) // 2, scaled)
			painter.end()
			preview_pixmap = canvas
			self.preview_note.setText("480 × 360 stage · nearest-neighbour preview")
		elif target == "indexed":
			preview_source = image
			if image.width() > 320 or image.height() > 200:
				preview_source = image.scaled(
					320,
					200,
					Qt.AspectRatioMode.KeepAspectRatio,
					Qt.TransformationMode.FastTransformation,
				)
			indexed = quantize_xvm_image(preview_source)
			preview_pixmap = self._checker_preview(indexed)
			self.preview_note.setText("Indexed preview · inspect colour and transparency loss before XIMG export")
		else:
			preview_source = image
			if image.width() > 320 or image.height() > 200:
				preview_source = image.scaled(
					320,
					200,
					Qt.AspectRatioMode.KeepAspectRatio,
					Qt.TransformationMode.FastTransformation,
				)
			preview_pixmap = self._checker_preview(preview_source)
			self.preview_note.setText(f"Native {project.width} × {project.height} RGBA")
		self.preview.setPixmap(
			preview_pixmap.scaled(
				self.preview.size().boundedTo(QSize(300, 190)),
				Qt.AspectRatioMode.KeepAspectRatio,
				Qt.TransformationMode.FastTransformation,
			)
		)

	@staticmethod
	def _checker_preview(image: QImage) -> QPixmap:
		pixmap = QPixmap(image.size())
		painter = QPainter(pixmap)
		cell = max(2, min(8, max(2, min(image.width(), image.height()) // 8)))
		for y in range(0, image.height(), cell):
			for x in range(0, image.width(), cell):
				color = QColor("#232a37") if (x // cell + y // cell) % 2 else QColor("#171d28")
				painter.fillRect(x, y, cell, cell, color)
		painter.drawImage(0, 0, image)
		painter.end()
		return pixmap

	def resizeEvent(self, event) -> None:
		super().resizeEvent(event)
		compact_labels = {
			"pencil": "Pencil",
			"eraser": "Eraser",
			"fill": "Fill",
			"eyedropper": "Pick",
			"line": "Line",
			"rect": "Rect",
			"ellipse": "Ellipse",
			"select": "Select",
		}
		mnemonic_labels = {tool: shortcut for tool, _, shortcut in TOOLS}
		if self.width() < 900:
			labels = mnemonic_labels
		else:
			labels = compact_labels
		for tool, button in self.tool_buttons.items():
			button.setText(labels[tool])
		self._refresh_preview()
