import sys
import re
import time
import threading
import traceback
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
	QApplication,
	QMainWindow,
	QWidget,
	QVBoxLayout,
	QHBoxLayout,
	QPlainTextEdit,
	QTextEdit,
	QPushButton,
	QFileDialog,
	QMessageBox,
	QSplitter,
	QLabel,
	QComboBox,
	QLineEdit,
)
from PyQt6.QtGui import (
	QSyntaxHighlighter,
	QTextDocument,
	QTextCharFormat,
	QTextFormat,
	QColor,
	QKeySequence,
	QImage,
	QPixmap,
	QPainter,
	QKeyEvent,
	QMouseEvent,
	QFont,
	QTextOption,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QTimer, QRect, QSize

from xe_lang.lexer import lex
from xe_lang.helper import TT
from runtime import run, RuntimeContext
from ide_themes import THEMES
from xe_lang.devices import (
	DEFAULT_PALETTE,
	SCREEN_HEIGHT,
	SCREEN_WIDTH,
	FrameSnapshot,
	OSDevice,
)


PALETTE = list(DEFAULT_PALETTE)


def ansi_to_html(text: str) -> str:
	ansi_colors = {
		"30": "#000000",
		"31": "#ff3333",
		"32": "#33cc33",
		"33": "#ffcc00",
		"34": "#3366ff",
		"35": "#cc33ff",
		"36": "#33ffff",
		"37": "#ffffff",
		"90": "#666666",
		"91": "#ff6666",
		"92": "#66ff66",
		"93": "#ffff66",
		"94": "#6699ff",
		"95": "#df80ff",
		"96": "#80ffff",
		"97": "#f3f3f3",
	}
	html_text = (
		text.replace("&", "&amp;")
		.replace("<", "&lt;")
		.replace(">", "&gt;")
		.replace("\n", "<br>")
	)
	ansi_pattern = re.compile(r"\x1b\[([0-9;]*)m")
	pos = 0
	result = ""
	open_tags = 0

	for match in ansi_pattern.finditer(html_text):
		result += html_text[pos : match.start()]
		pos = match.end()
		codes = match.group(1).split(";")
		for code in codes:
			if code in ("", "0"):
				while open_tags > 0:
					result += '</span style="white-space: pre-wrap;">'
					open_tags -= 1
			elif code in ansi_colors:
				result += (
					f'<span style="color:{ansi_colors[code]}; white-space: pre-wrap;">'
				)
				open_tags += 1
	result += html_text[pos:]
	while open_tags > 0:
		result += "</span>"
		open_tags -= 1
	return result


class LineNumberArea(QWidget):
	def __init__(self, editor):
		super().__init__(editor)
		self.code_editor = editor

	def sizeHint(self):
		return QSize(self.code_editor.line_number_area_width(), 0)

	def paintEvent(self, event):
		self.code_editor.line_number_area_paint_event(event)


class CodeEditor(QPlainTextEdit):
	def __init__(self, parent=None):
		super().__init__(parent)

		self.line_number_area = LineNumberArea(self)

		self.blockCountChanged.connect(self.update_line_number_area_width)
		self.updateRequest.connect(self.update_line_number_area)
		self.cursorPositionChanged.connect(self.highlight_current_line)

		self.update_line_number_area_width(0)
		self.highlight_current_line()

	def line_number_area_width(self):
		digits = len(str(max(1, self.blockCount())))
		return 12 + self.fontMetrics().horizontalAdvance("9") * digits

	def update_line_number_area_width(self, _):
		self.setViewportMargins(
			self.line_number_area_width(),
			0,
			0,
			0,
		)

	def update_line_number_area(self, rect, dy):
		if dy:
			self.line_number_area.scroll(0, dy)
		else:
			self.line_number_area.update(
				0,
				rect.y(),
				self.line_number_area.width(),
				rect.height(),
			)

		if rect.contains(self.viewport().rect()):
			self.update_line_number_area_width(0)

	def resizeEvent(self, event):
		super().resizeEvent(event)

		cr = self.contentsRect()
		self.line_number_area.setGeometry(
			QRect(
				cr.left(),
				cr.top(),
				self.line_number_area_width(),
				cr.height(),
			)
		)

	def highlight_current_line(self):
		selections = []

		if not self.isReadOnly():
			selection = QTextEdit.ExtraSelection()

			selection.format.setProperty(
				QTextFormat.Property.FullWidthSelection,
				True,
			)

			selection.format.setBackground(QColor(255, 255, 255, 15))

			selection.cursor = self.textCursor()
			selection.cursor.clearSelection()

			selections.append(selection)

		self.setExtraSelections(selections)

	def line_number_area_paint_event(self, event):
		painter = QPainter(self.line_number_area)

		painter.fillRect(
			event.rect(), getattr(self, "line_number_bg", QColor("#1b1b1b"))
		)

		block = self.firstVisibleBlock()
		block_number = block.blockNumber()

		top = round(
			self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
		)

		bottom = top + round(self.blockBoundingRect(block).height())

		while block.isValid() and top <= event.rect().bottom():
			if block.isVisible() and bottom >= event.rect().top():
				number = str(block_number + 1)

				painter.setPen(getattr(self, "line_number_fg", QColor("#808080")))

				painter.drawText(
					0,
					top,
					self.line_number_area.width() - 6,
					self.fontMetrics().height(),
					Qt.AlignmentFlag.AlignRight,
					number,
				)

			block = block.next()
			top = bottom
			bottom = top + round(self.blockBoundingRect(block).height())
			block_number += 1


class XPP26SyntaxHighlighter(QSyntaxHighlighter):
	def __init__(self, document: QTextDocument, theme: dict):
		super().__init__(document)
		self.theme = theme
		self.setup_formats()

	def setup_formats(self):
		self.keyword_format = QTextCharFormat()
		self.keyword_format.setForeground(QColor(self.theme["keyword"]))
		self.keyword_format.setFontWeight(700)

		self.type_format = QTextCharFormat()
		self.type_format.setForeground(QColor(self.theme["type"]))
		self.type_format.setFontWeight(700)

		self.struct_type_format = QTextCharFormat()
		self.struct_type_format.setForeground(
			QColor(self.theme.get("struct_type", self.theme["type"]))
		)
		self.struct_type_format.setFontWeight(700)
		self.struct_type_format.setFontItalic(True)

		self.number_format = QTextCharFormat()
		self.number_format.setForeground(QColor(self.theme["number"]))

		self.string_format = QTextCharFormat()
		self.string_format.setForeground(QColor(self.theme["string"]))

		self.operator_format = QTextCharFormat()
		self.operator_format.setForeground(QColor(self.theme["operator"]))

		self.comment_format = QTextCharFormat()
		self.comment_format.setForeground(QColor(self.theme["comment"]))
		self.comment_format.setFontItalic(True)

		self.bool_format = QTextCharFormat()
		self.bool_format.setForeground(QColor(self.theme["bool"]))
		self.bool_format.setFontWeight(700)

		self.ident_format = QTextCharFormat()
		self.ident_format.setForeground(QColor(self.theme["ident"]))

		self.function_format = QTextCharFormat()
		self.function_format.setForeground(
			QColor(self.theme.get("call", self.theme["ident"]))
		)
		self.function_format.setFontWeight(700)

		self.library_format = QTextCharFormat()
		self.library_format.setForeground(
			QColor(self.theme.get("call", self.theme["ident"]))
		)

	def update_theme(self, theme: dict):
		self.theme = theme
		self.setup_formats()
		self.rehighlight()

	def highlightBlock(self, text: str):
		tokens, error = lex("<editor>", text)
		if not tokens:
			self.setFormat(0, len(text), self.ident_format)
			return

		char_to_token = {}
		for idx, token in enumerate(tokens):
			if token._type == TT.EOF:
				continue
			start_col = token.start_pos.col
			end_col = token.end_pos.col + 1
			if start_col <= len(text):
				for i in range(max(0, start_col), min(len(text), end_col)):
					char_to_token[i] = (token._type, idx)

		def prev_non_trivial_token(idx: int):
			j = idx - 1
			if j < 0 or j >= len(tokens):
				return None
			return tokens[j]

		def is_struct_or_class_name(idx: int) -> bool:
			prev = prev_non_trivial_token(idx)
			if prev is None:
				return False

			if prev._type == TT.KEYWORD and prev.value in (
				"struct", "class", "new"
			):
				return True
			
			if prev._type == TT.COL:
				return True

			return False
		
		LIBRARY_NAMES = {"math", "window", "graphics", "os"}

		def is_library_name(idx: int) -> bool:
			if idx < 0 or idx >= len(tokens):
				return False
			return tokens[idx].value in LIBRARY_NAMES

		current_pos = 0
		while current_pos < len(text):
			token_info = char_to_token.get(current_pos, (TT.IDENT, -1))
			token_type, token_idx = token_info

			end_pos = current_pos + 1
			while (
				end_pos < len(text)
				and char_to_token.get(end_pos, (TT.IDENT, -1)) == token_info
			):
				end_pos += 1

			length = end_pos - current_pos

			if token_type == TT.KEYWORD:
				self.setFormat(current_pos, length, self.keyword_format)
			elif token_type == TT.TYPE:
				self.setFormat(current_pos, length, self.type_format)
			elif token_type in (TT.INT, TT.FLOAT):
				self.setFormat(current_pos, length, self.number_format)
			elif token_type in (TT.CHAR, TT.STRING):
				self.setFormat(current_pos, length, self.string_format)
			elif token_type in (
				TT.ADD,
				TT.SUB,
				TT.MUL,
				TT.DIV,
				TT.MOD,
				TT.POW,
				TT.EQ,
				TT.NE,
				TT.LT,
				TT.LE,
				TT.GT,
				TT.GE,
				TT.AND,
				TT.OR,
				TT.NOT,
				TT.XOR,
				TT.ANDL,
				TT.ORL,
				TT.NOTL,
				TT.XORL,
				TT.ASGN,
				TT.ADD_ASGN,
				TT.SUB_ASGN,
				TT.MUL_ASGN,
				TT.DIV_ASGN,
				TT.MOD_ASGN,
				TT.POW_ASGN,
				TT.AND_ASGN,
				TT.OR_ASGN,
				TT.XOR_ASGN,
				TT.ANDL_ASGN,
				TT.ORL_ASGN,
				TT.XORL_ASGN,
				TT.ISTREAM,
				TT.OSTREAM,
				TT.ARROW,
				TT.SCOPE
			):
				self.setFormat(current_pos, length, self.operator_format)
			elif token_type == TT.BOOL:
				self.setFormat(current_pos, length, self.bool_format)
			elif token_type == TT.IDENT:
				if token_idx != -1 and is_library_name(token_idx): # standard lib
					self.setFormat(current_pos, length, self.library_format)
				elif token_idx != -1 and is_struct_or_class_name(token_idx):
					self.setFormat(current_pos, length, self.struct_type_format)
				else:
					is_func_call = False
					next_idx = token_idx + 1
					while next_idx < len(tokens):
						next_tok = tokens[next_idx]
						if next_tok._type == TT.EOF:
							break

						if next_tok._type == TT.LPR:
							is_func_call = True
							break
						break

					if is_func_call:
						self.setFormat(current_pos, length, self.function_format)
					else:
						self.setFormat(current_pos, length, self.ident_format)
			else:
				self.setFormat(current_pos, length, self.ident_format)
			current_pos = end_pos

		for match in re.finditer(r"(#.*)", text):
			start, end = match.span()
			self.setFormat(start, end - start, self.comment_format)


class VMGraphicsWidget(QWidget):
	def __init__(self, parent=None):
		super().__init__(parent)
		self.width_px = SCREEN_WIDTH
		self.height_px = SCREEN_HEIGHT
		self.scale = 1
		self.setFixedSize(self.width_px * self.scale, self.height_px * self.scale)

		self.image = QImage(self.width_px, self.height_px, QImage.Format.Format_RGB32)
		self.image.fill(QColor("black"))
		self.active_vm = None

		self.setMouseTracking(True)
		self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

	@pyqtSlot(object)
	def set_active_vm(self, vm):
		self.active_vm = vm

	@pyqtSlot(object)
	def update_frame(self, frame):
		if isinstance(frame, FrameSnapshot):
			if (frame.width, frame.height) != (self.width_px, self.height_px):
				self.width_px = frame.width
				self.height_px = frame.height
				self.setFixedSize(
					self.width_px * self.scale,
					self.height_px * self.scale,
				)
			palette = frame.palette or tuple(PALETTE)
			# The framebuffer is already an immutable byte-per-pixel image. Keep
			# that indexed representation instead of performing 43,200 Python
			# QColor writes for every frame on the UI thread.
			indexed = QImage(
				frame.indices,
				frame.width,
				frame.height,
				frame.width,
				QImage.Format.Format_Indexed8,
			).copy()
			indexed.setColorTable([QColor(color).rgba() for color in palette])
			self.image = indexed
		else:
			palette = PALETTE
			if self.active_vm and hasattr(self.active_vm, "devices"):
				palette = self.active_vm.devices.os.palette
			colors = [QColor(color) for color in palette]
			for y in range(min(self.height_px, len(frame))):
				for x in range(min(self.width_px, len(frame[y]))):
					self.image.setPixelColor(x, y, colors[frame[y][x] % len(colors)])
		self.update()

	def paintEvent(self, event):
		painter = QPainter(self)
		scaled_pixmap = QPixmap.fromImage(self.image).scaled(
			self.size(),
			Qt.AspectRatioMode.IgnoreAspectRatio,
			Qt.TransformationMode.FastTransformation,
		)
		painter.drawPixmap(0, 0, scaled_pixmap)

	def _pointer_position(self, event: QMouseEvent) -> tuple[int, int]:
		return (
			max(0, min(self.width_px - 1, int(event.position().x() // self.scale))),
			max(0, min(self.height_px - 1, int(event.position().y() // self.scale))),
		)

	def _update_pointer(self, event: QMouseEvent) -> None:
		if self.active_vm:
			x, y = self._pointer_position(event)
			self.active_vm.devices.input.move_pointer(x, y)

	def _modifier_mask(self, event: QKeyEvent) -> int:
		modifiers = event.modifiers()
		mask = 0
		if modifiers & Qt.KeyboardModifier.ShiftModifier:
			mask |= 1
		if modifiers & Qt.KeyboardModifier.ControlModifier:
			mask |= 2
		if modifiers & Qt.KeyboardModifier.AltModifier:
			mask |= 4
		return mask

	def _key_code(self, event: QKeyEvent) -> int:
		key = event.key()
		special = {
			Qt.Key.Key_Backspace: 8,
			Qt.Key.Key_Tab: 9,
			Qt.Key.Key_Return: 13,
			Qt.Key.Key_Enter: 13,
			Qt.Key.Key_Escape: 27,
			Qt.Key.Key_Space: 32,
			Qt.Key.Key_Left: 37,
			Qt.Key.Key_Up: 38,
			Qt.Key.Key_Right: 39,
			Qt.Key.Key_Down: 40,
			Qt.Key.Key_Delete: 46,
		}.get(key)
		if special is not None:
			return special
		text = event.text()
		if len(text) == 1 and ord(text) <= 0xFF:
			return ord(text)
		return int(key)

	def mouseMoveEvent(self, event: QMouseEvent):
		self._update_pointer(event)

	def mousePressEvent(self, event: QMouseEvent):
		if self.active_vm:
			self.setFocus(Qt.FocusReason.MouseFocusReason)
			self._update_pointer(event)
			button = {
				Qt.MouseButton.LeftButton: 1,
				Qt.MouseButton.RightButton: 2,
				Qt.MouseButton.MiddleButton: 4,
			}.get(event.button())
			if button:
				self.active_vm.devices.input.set_button(button, True)

	def mouseReleaseEvent(self, event: QMouseEvent):
		if self.active_vm:
			self._update_pointer(event)
			button = {
				Qt.MouseButton.LeftButton: 1,
				Qt.MouseButton.RightButton: 2,
				Qt.MouseButton.MiddleButton: 4,
			}.get(event.button())
			if button:
				self.active_vm.devices.input.set_button(button, False)

	def keyPressEvent(self, event: QKeyEvent):
		if self.active_vm and not event.isAutoRepeat():
			self.active_vm.devices.input.set_key(
				self._key_code(event), True, self._modifier_mask(event)
			)

	def keyReleaseEvent(self, event: QKeyEvent):
		if self.active_vm and not event.isAutoRepeat():
			self.active_vm.devices.input.set_key(
				self._key_code(event), False, self._modifier_mask(event)
			)

	def focusOutEvent(self, event):
		if self.active_vm:
			self.active_vm.devices.input.release_all()
		super().focusOutEvent(event)


class VMWorkerThread(QThread):
	execution_finished = pyqtSignal(object, object, str)
	frame_ready = pyqtSignal(object)
	vm_ready = pyqtSignal(object)
	output_ready = pyqtSignal(str)
	input_requested = pyqtSignal(object, object)

	def __init__(self, filename: str, code: str, context: RuntimeContext):
		super().__init__()
		self.filename = filename
		self.code = code
		self.context = context

	def run(self):
		try:
			last_frame_emit = 0.0

			def output_handler(text: str):
				self.output_ready.emit(text)

			def frame_handler(frame: FrameSnapshot):
				nonlocal last_frame_emit
				now = time.monotonic()
				if now - last_frame_emit >= 1.0 / 30.0:
					last_frame_emit = now
					self.frame_ready.emit(frame)

			def input_handler() -> str:
				ready = threading.Event()
				holder: dict[str, str] = {}
				self.input_requested.emit(ready, holder)
				while not ready.wait(0.05):
					if self.context.cancel_event.is_set() or self.isInterruptionRequested():
						return ""
				return holder.get("value", "")

			self.context.output_handler = output_handler
			self.context.input_handler = input_handler
			self.context.frame_handler = frame_handler
			self.context.vm_ready_handler = self.vm_ready.emit
			result, error, asm = run(self.filename, self.code, self.context)

			self.execution_finished.emit(result, error, asm or "")
		except Exception as e:
			error_string = traceback.format_exc()
			self.execution_finished.emit(
				None, f"Runtime Thread Exception\n{error_string}", ""
			)


class X26IDE(QMainWindow):
	def __init__(self):
		super().__init__()

		font = QFont()
		font.setFamilies(
			[
				"Fira Code",
				"Cascadia Code",
				"JetBrains Mono",
				"Consolas",
			]
		)
		font.setPointSizeF(11)

		self.setFont(font)

		self.setWindowTitle("Xenon IDE")
		self.setGeometry(100, 100, 950, 650)

		self.current_file: Optional[Path] = None
		self.os_device = OSDevice()
		self.runtime_context = RuntimeContext(os_device=self.os_device)
		self.current_theme = "Default Dark"
		self.worker: Optional[VMWorkerThread] = None

		self.refresh_timer = QTimer()
		self.refresh_timer.timeout.connect(self.poll_vm_buffer)

		central_widget = QWidget()
		self.setCentralWidget(central_widget)
		main_layout = QVBoxLayout(central_widget)

		toolbar_layout = QHBoxLayout()
		for text, slot in [
			("New", self.new_file),
			("Open", self.open_file),
			("Save", self.save_file),
			("Save As", self.save_as_file),
		]:
			btn = QPushButton(text)
			btn.clicked.connect(slot)
			toolbar_layout.addWidget(btn)

		toolbar_layout.addStretch()
		toolbar_layout.addWidget(QLabel("Theme:"))

		self.theme_dropdown = QComboBox()
		self.theme_dropdown.addItems(list(THEMES.keys()))
		self.theme_dropdown.setCurrentText(self.current_theme)
		self.theme_dropdown.currentTextChanged.connect(self.change_theme)
		toolbar_layout.addWidget(self.theme_dropdown)

		self.run_button = QPushButton("Run")
		self.run_button.clicked.connect(self.run_code)
		toolbar_layout.addWidget(self.run_button)
		main_layout.addLayout(toolbar_layout)

		main_splitter = QSplitter(Qt.Orientation.Horizontal)

		editor_container = QWidget()
		editor_layout = QVBoxLayout(editor_container)
		editor_layout.setContentsMargins(0, 0, 0, 0)
		editor_layout.addWidget(QLabel("Editor:"))
		self.editor = CodeEditor()
		self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
		self.editor.setTabStopDistance(
			self.editor.fontMetrics().horizontalAdvance(" ") * 4
		)
		self.editor.setCursorWidth(8)

		editor_layout.addWidget(self.editor)
		self.highlighter = XPP26SyntaxHighlighter(
			self.editor.document(), THEMES[self.current_theme]
		)
		main_splitter.addWidget(editor_container)

		right_panel_splitter = QSplitter(Qt.Orientation.Vertical)

		output_container = QWidget()
		output_layout = QVBoxLayout(output_container)
		output_layout.setContentsMargins(0, 0, 0, 0)
		output_layout.addWidget(QLabel("Terminal:"))
		self.output = QTextEdit()
		self.output.setReadOnly(True)
		output_layout.addWidget(self.output)
		self.input_line = QLineEdit()
		self.input_line.setPlaceholderText("Program input")
		self.input_line.returnPressed.connect(self.submit_program_input)
		self.input_line.hide()
		output_layout.addWidget(self.input_line)
		self._pending_input: tuple[threading.Event, dict[str, str]] | None = None
		right_panel_splitter.addWidget(output_container)

		graphics_container = QWidget()

		graphics_layout = QVBoxLayout(graphics_container)
		graphics_layout.setContentsMargins(8, 8, 8, 8)
		graphics_layout.setSpacing(8)

		graphics_label = QLabel("Graphics View:")
		graphics_layout.addWidget(graphics_label)

		graphics_layout.addStretch(1)

		self.graphics_view = VMGraphicsWidget()
		graphics_layout.addWidget(
			self.graphics_view,
			alignment=Qt.AlignmentFlag.AlignCenter,
		)

		graphics_layout.addStretch(1)

		right_panel_splitter.addWidget(graphics_container)

		right_panel_splitter.setStretchFactor(0, 1)
		right_panel_splitter.setStretchFactor(1, 1)
		main_splitter.addWidget(right_panel_splitter)

		main_splitter.setStretchFactor(0, 2)
		main_splitter.setStretchFactor(1, 1)
		main_layout.addWidget(main_splitter)

		self.setup_menu_bar()
		self.apply_theme()

	def setup_menu_bar(self):
		menubar = self.menuBar()
		file_menu = menubar.addMenu("File")
		for name, shortcut, slot in [
			("New", QKeySequence.StandardKey.New, self.new_file),
			("Open", QKeySequence.StandardKey.Open, self.open_file),
			("Save", QKeySequence.StandardKey.Save, self.save_file),
			("Save As", QKeySequence.StandardKey.SaveAs, self.save_as_file),
			("Exit", QKeySequence.StandardKey.Quit, self.close),
		]:
			act = file_menu.addAction(name)
			act.setShortcut(shortcut)
			act.triggered.connect(slot)

		run_menu = menubar.addMenu("Run")
		run_action = run_menu.addAction("Run")
		run_action.setShortcut("Ctrl+Return")
		run_action.triggered.connect(self.run_code)

	def apply_theme(self):
		theme = THEMES[self.current_theme]
		self.editor.line_number_bg = QColor(theme["toolbar_bg"])
		self.editor.line_number_fg = QColor(theme["comment"])
		stylesheet = f"""
			QMainWindow {{ background-color: {theme['background']}; color: {theme['foreground']}; }}
			QWidget {{ background-color: {theme['background']}; color: {theme['foreground']}; }}
			QMenuBar {{ background-color: {theme['toolbar_bg']}; color: {theme['foreground']}; border-bottom: 1px solid #555; }}
			QMenuBar::item:selected {{ background-color: {theme['button']}; }}
			QPushButton {{ background-color: {theme['button']}; color: white; border: none; border-radius: 3px; padding: 5px 10px; font-weight: bold; }}
			QPushButton:hover {{ background-color: {theme['button_hover']}; }}
			QPlainTextEdit {{ background-color: {theme['background']}; color: {theme['foreground']}; border: 1px solid #555; }}
			QTextEdit {{ background-color: {theme['background']}; color: {theme['foreground']}; border: 1px solid #555; }}
			QLabel {{ color: {theme['foreground']}; }}
			QSplitter::handle {{ background-color: #555; }}
		"""
		self.setStyleSheet(stylesheet)
		self.output.setStyleSheet(
			f"QTextEdit {{ background-color: {theme['output_bg']}; color: {theme['foreground']}; }}"
		)
		self.input_line.setStyleSheet(
			f"QLineEdit {{ background-color: {theme['output_bg']}; color: {theme['foreground']}; border: 1px solid #555; padding: 4px; }}"
		)
		self.highlighter.update_theme(theme)

	def change_theme(self, name):
		if name in THEMES:
			self.current_theme = name
			self.apply_theme()

	def new_file(self):
		self.editor.clear()
		self.output.clear()
		self.current_file = None
		self.update_title()

	def open_file(self):
		path, _ = QFileDialog.getOpenFileName(
			self, "Open", "", "Xe Files (*.xe);;All Files (*)"
		)
		if path:
			self.load_file(Path(path))

	def load_file(self, path: Path) -> None:
		self.current_file = path.resolve()
		self.editor.setPlainText(self.current_file.read_text(encoding="utf-8"))
		self.editor.document().setModified(False)
		self.update_title()

	def save_file(self):
		if not self.current_file:
			self.save_as_file()
		else:
			self.current_file.write_text(self.editor.toPlainText())
			self.editor.document().setModified(False)
			self.update_title()

	def save_as_file(self):
		path, _ = QFileDialog.getSaveFileName(
			self, "Save", "", "Xe Files (*.xe);;All Files (*)"
		)
		if path:
			self.current_file = Path(path)
			self.save_file()

	def update_title(self):
		self.setWindowTitle(
			f"Xenon IDE - {self.current_file.name if self.current_file else 'Untitled'}"
		)

	@pyqtSlot(str)
	def append_output(self, text: str):
		cursor = self.output.textCursor()
		cursor.movePosition(cursor.MoveOperation.End)
		html_text = (
			text.replace("&", "&amp;")
			.replace("<", "&lt;")
			.replace(">", "&gt;")
			.replace("\n", "<br>")
		)
		cursor.insertHtml(f'<span style="white-space: pre-wrap;">{html_text}</span>')
		self.output.setTextCursor(cursor)
		self.output.verticalScrollBar().setValue(
			self.output.verticalScrollBar().maximum()
		)

	@pyqtSlot(object, object)
	def request_program_input(self, ready: threading.Event, holder: dict[str, str]) -> None:
		self._pending_input = (ready, holder)
		self.input_line.clear()
		self.input_line.show()
		self.input_line.setFocus()

	def submit_program_input(self) -> None:
		if self._pending_input is None:
			return
		ready, holder = self._pending_input
		value = self.input_line.text()
		holder["value"] = value
		self.append_output(value + "\n")
		self._pending_input = None
		self.input_line.clear()
		self.input_line.hide()
		ready.set()

	def cancel_program_input(self) -> None:
		if self._pending_input is not None:
			ready, holder = self._pending_input
			holder["value"] = ""
			self._pending_input = None
			ready.set()
		self.input_line.clear()
		self.input_line.hide()

	def run_code(self):
		code = self.editor.toPlainText()
		if not code.strip():
			return

		if self.worker and self.worker.isRunning():
			self.cancel_program_input()
			self.runtime_context.cancel()
			self.worker.requestInterruption()
			self.worker.wait(2000)
			if self.worker.isRunning():
				self.append_output("Previous program did not stop cooperatively.\n")
				return

		self.output.setHtml("")
		self.runtime_context = RuntimeContext(os_device=self.os_device)
		filename = str(self.current_file) if self.current_file else "<editor>"

		self.worker = VMWorkerThread(filename, code, self.runtime_context)
		self.worker.frame_ready.connect(self.graphics_view.update_frame)
		self.worker.vm_ready.connect(self.graphics_view.set_active_vm)
		self.worker.output_ready.connect(self.append_output)
		self.worker.input_requested.connect(self.request_program_input)
		self.worker.execution_finished.connect(self.handle_execution_finished)

		self.graphics_view.active_vm = None
		self.worker.start()
		self.refresh_timer.start(33)

	def poll_vm_buffer(self):
		if hasattr(self.runtime_context, "vm") and self.runtime_context.vm:
			if self.graphics_view.active_vm is not self.runtime_context.vm:
				self.graphics_view.set_active_vm(self.runtime_context.vm)

	@pyqtSlot(object, object, str)
	def handle_execution_finished(self, result, error, assembly):
		self.refresh_timer.stop()
		self.cancel_program_input()
		if error:
			self.output.append(ansi_to_html(f"{error}"))
		else:
			self.append_output(f"Execution finished successfully.\n\nStack: {result[:16]}")

		if hasattr(self.runtime_context, "vm") and self.runtime_context.vm:
			frame = self.runtime_context.vm._last_snapshot
			self.graphics_view.update_frame(
				frame if frame is not None else self.runtime_context.vm.front_buffer
			)

	def closeEvent(self, event):
		if self.worker and self.worker.isRunning():
			self.cancel_program_input()
			self.runtime_context.cancel()
			self.worker.wait(2000)
		event.accept()


def main():
	app = QApplication(sys.argv)

	font = QFont()
	font.setFamilies(
		[
			"Fira Code",
			"Cascadia Code",
			"JetBrains Mono",
			"Consolas",
		]
	)
	font.setPointSizeF(11)

	app.setFont(font)

	ide = X26IDE()
	arguments = sys.argv[1:]
	auto_run = "--run" in arguments
	file_arguments = [argument for argument in arguments if argument != "--run"]
	if file_arguments:
		path = Path(file_arguments[0])
		if path.is_file():
			ide.load_file(path)
	ide.show()
	if auto_run and ide.current_file is not None:
		QTimer.singleShot(0, ide.run_code)

	sys.exit(app.exec())


if __name__ == "__main__":
	main()
