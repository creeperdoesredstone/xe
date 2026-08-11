import sys
import re
import time
import math
import os
import tempfile
import threading
import traceback
from array import array
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
	QToolTip,
	QSplitter,
	QLabel,
	QComboBox,
	QLineEdit,
	QCheckBox,
	QInputDialog,
	QSizePolicy,
	QTabWidget,
	QMessageBox,
	QScrollArea,
	QFrame,
)
from PyQt6.QtGui import (
	QSyntaxHighlighter,
	QTextDocument,
	QTextCharFormat,
	QTextFormat,
	QTextCursor,
	QColor,
	QKeySequence,
	QShortcut,
	QImage,
	QPixmap,
	QPainter,
	QKeyEvent,
	QMouseEvent,
	QWheelEvent,
	QFont,
	QFontDatabase,
)
from PyQt6.QtCore import (
	QEvent,
	QIODevice,
	QObject,
	Qt,
	QThread,
	pyqtSignal,
	pyqtSlot,
	QTimer,
	QRect,
	QSize,
	QPoint,
)

try:
	from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
except (ImportError, OSError):
	QAudioFormat = None
	QAudioSink = None
	QMediaDevices = None

from xe_lang.lexer import lex
from xe_lang.parser import parse
from xe_lang.helper import TT, Token, Position
from xe_lang.nodes import (
	Node,
	VariableDeclaration,
	ArrayDeclaration,
	Parameter,
	FunctionDefinition,
	ProcedureDefinition,
	StructDefinition,
	ClassDefinition,
	EnumDeclaration,
)
from runtime import run, RuntimeContext
from ide_themes import THEMES
from xe_lang.devices.assets import AudioState, AudioVoice
from xe_lang.devices import (
	DEFAULT_PALETTE,
	SCREEN_HEIGHT,
	SCREEN_WIDTH,
	FrameSnapshot,
	OSDevice,
	default_settings_path,
)
from xe_lang.devices.keymap import normalize_key_code
from xe_lang.host_tools import ConverterPane, HelpPane, ImageStudioPane
from xe_lang.host_tools.converter import read_xe_source
from xe_lang.host_tools.services import ConversionRequest
from xe_lang.host_tools.ui_specs import (
	CODE_TOOLBAR_ACTIONS,
	WORKBENCH_TABS,
	workbench_tab_index,
)
from xe_lang.stdlib.specs import STANDARD_LIBRARY_SPECS


PREFERRED_MONOSPACE_FAMILIES = (
	"Fira Code",
	"Cascadia Code",
	"JetBrains Mono",
	"Consolas",
)

STANDARD_LIBRARY_NAMES = frozenset(
	{"math", *(library.name for library in STANDARD_LIBRARY_SPECS)}
)


def _atomic_write_text(path: Path, text: str) -> None:
	"""Replace *path* atomically with deterministic UTF-8 source text."""
	path = path.resolve()
	path.parent.mkdir(parents=True, exist_ok=True)
	fd, temporary_name = tempfile.mkstemp(
		prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
	)
	try:
		with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
			handle.write(text)
			handle.flush()
			os.fsync(handle.fileno())
		os.replace(temporary_name, path)
	finally:
		if os.path.exists(temporary_name):
			os.unlink(temporary_name)


def _comment_start(text: str) -> int | None:
	"""Return the first Xe comment marker outside string/character literals."""
	quote = ""
	escaped = False
	for index, character in enumerate(text):
		if quote:
			if escaped:
				escaped = False
			elif character == "\\":
				escaped = True
			elif character == quote:
				quote = ""
		elif character in {"'", '"'}:
			quote = character
		elif character == "#":
			return index
	return None


def _host_monospace_font(
	point_size: float = 11.0,
	*,
	available_families: tuple[str, ...] | None = None,
	fixed_font: QFont | None = None,
) -> QFont:
	"""Return a readable fixed-width host font with installed-family fallbacks."""
	if available_families is None:
		available_families = tuple(QFontDatabase.families())
	installed = {family.casefold(): family for family in available_families}
	preferred = next(
		(
			installed[name.casefold()]
			for name in PREFERRED_MONOSPACE_FAMILIES
			if name.casefold() in installed
		),
		None,
	)
	system_fixed = QFont(fixed_font) if fixed_font is not None else QFontDatabase.systemFont(
		QFontDatabase.SystemFont.FixedFont
	)
	families: list[str] = []
	seen: set[str] = set()
	for family in (preferred, system_fixed.family(), "monospace"):
		key = family.casefold() if family else ""
		if key and key not in seen:
			families.append(family)
			seen.add(key)
	font = QFont(system_fixed)
	font.setFamilies(families or ["monospace"])
	font.setStyleHint(QFont.StyleHint.Monospace)
	font.setFixedPitch(True)
	font.setPointSizeF(max(8.0, float(point_size)))
	return font


def _find_name_token_pos(tokens: list[Token], keyword_pos: Position, name: str) -> Position:
	for i, tok in enumerate(tokens):
		if tok.start_pos.idx == keyword_pos.idx:
			for j in range(i + 1, min(i + 4, len(tokens))):
				if tokens[j]._type == TT.IDENT and tokens[j].value == name:
					return tokens[j].start_pos
			break
	return keyword_pos


def _collect_definitions(node, tokens: list[Token], definitions: dict) -> None:
	if node is None:
		return

	if isinstance(node, (list, tuple)):
		for item in node:
			_collect_definitions(item, tokens, definitions)
		return

	if not isinstance(node, Node):
		return

	if isinstance(node, (VariableDeclaration, ArrayDeclaration)):
		pos = _find_name_token_pos(tokens, node.start_pos, node.name)
		definitions.setdefault(node.name, []).append(("variable", pos))
	elif isinstance(node, Parameter):
		definitions.setdefault(node.name, []).append(("variable", node.start_pos))
	elif isinstance(node, (FunctionDefinition, ProcedureDefinition)):
		pos = _find_name_token_pos(tokens, node.start_pos, node.name)
		definitions.setdefault(node.name, []).append(("subroutine", pos))
	elif isinstance(node, StructDefinition):
		var = node.var
		name = getattr(var, "value", None)
		pos = getattr(var, "start_pos", node.start_pos)
		if name:
			definitions.setdefault(name, []).append(("struct", pos))
	elif isinstance(node, ClassDefinition):
		pos = _find_name_token_pos(tokens, node.start_pos, node.name)
		definitions.setdefault(node.name, []).append(("class", pos))
	elif isinstance(node, EnumDeclaration):
		pos = _find_name_token_pos(tokens, node.start_pos, node.enum_name)
		definitions.setdefault(node.enum_name, []).append(("enum", pos))

	for value in vars(node).values():
		if isinstance(value, (Node, list, tuple)):
			_collect_definitions(value, tokens, definitions)


def _extract_node_signature(node, doc: QTextDocument) -> Optional[str]:
	if node is None:
		return None

	if isinstance(node, Parameter):
		type_str = f": {node.type_name}" if getattr(node, "type_name", None) else ""
		return f"{node.name}{type_str}"

	pos = getattr(node, "start_pos", None)
	if pos is not None and hasattr(pos, "ln"):
		block = doc.findBlockByNumber(pos.ln)
		if block.isValid():
			line = block.text().strip()
			if "{" in line:
				line = line.split("{")[0].strip()
			return line
	return None


def _collect_definition_details(
	node, tokens: list[Token], definitions: dict, doc: QTextDocument
) -> None:
	if node is None:
		return

	if isinstance(node, (list, tuple)):
		for item in node:
			_collect_definition_details(item, tokens, definitions, doc)
		return

	if not isinstance(node, Node):
		return

	sig = _extract_node_signature(node, doc)

	if isinstance(node, (VariableDeclaration, ArrayDeclaration)):
		pos = _find_name_token_pos(tokens, node.start_pos, node.name)
		definitions.setdefault(node.name, []).append(("variable", pos, sig))
	elif isinstance(node, Parameter):
		definitions.setdefault(node.name, []).append(("variable", node.start_pos, sig))
	elif isinstance(node, (FunctionDefinition, ProcedureDefinition)):
		pos = _find_name_token_pos(tokens, node.start_pos, node.name)
		definitions.setdefault(node.name, []).append(("subroutine", pos, sig))
	elif isinstance(node, StructDefinition):
		var = node.var
		name = getattr(var, "value", None)
		pos = getattr(var, "start_pos", node.start_pos)
		if name:
			definitions.setdefault(name, []).append(("struct", pos, sig))
	elif isinstance(node, ClassDefinition):
		pos = _find_name_token_pos(tokens, node.start_pos, node.name)
		definitions.setdefault(node.name, []).append(("class", pos, sig))
	elif isinstance(node, EnumDeclaration):
		pos = _find_name_token_pos(tokens, node.start_pos, node.enum_name)
		definitions.setdefault(node.enum_name, []).append(("enum", pos, sig))

	for value in vars(node).values():
		if isinstance(value, (Node, list, tuple)):
			_collect_definition_details(value, tokens, definitions, doc)


PALETTE = list(DEFAULT_PALETTE)


ANSI_COLORS = {
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
ANSI_PATTERN = re.compile(r"\x1b\[([0-9;]*)m")
ANSI_INCOMPLETE_PATTERN = re.compile(r"\x1b(?:\[[0-9;]*)?$")


class AnsiHtmlStream:
	"""Render independently delivered ANSI chunks without losing SGR state."""

	def __init__(self) -> None:
		self.pending = ""
		self.color: str | None = None

	def reset(self) -> None:
		self.pending = ""
		self.color = None

	@staticmethod
	def _escape(text: str) -> str:
		return (
			text.replace("&", "&amp;")
			.replace("<", "&lt;")
			.replace(">", "&gt;")
			.replace("\n", "<br>")
		)

	def _render_text(self, text: str) -> str:
		escaped = self._escape(text)
		if not escaped or self.color is None:
			return escaped
		return f'<span style="color:{self.color}; white-space: pre-wrap;">{escaped}</span>'

	def feed(self, text: str) -> str:
		data = self.pending + text
		self.pending = ""
		incomplete = ANSI_INCOMPLETE_PATTERN.search(data)
		if incomplete is not None:
			self.pending = incomplete.group(0)
			data = data[: incomplete.start()]

		result: list[str] = []
		position = 0
		for match in ANSI_PATTERN.finditer(data):
			result.append(self._render_text(data[position : match.start()]))
			for code in match.group(1).split(";"):
				if code in {"", "0"}:
					self.color = None
				elif code in ANSI_COLORS:
					self.color = ANSI_COLORS[code]
			position = match.end()
		result.append(self._render_text(data[position:]))
		return "".join(result)


def ansi_to_html(text: str) -> str:
	stream = AnsiHtmlStream()
	result = stream.feed(text)
	if stream.pending:
		result += stream._render_text(stream.pending)
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
	INDENT = "\t"

	def __init__(self, parent=None):
		super().__init__(parent)

		self.line_number_area = LineNumberArea(self)
		self.go_to_definition_callback = None
		self._hover_underline_selection: Optional[QTextEdit.ExtraSelection] = None
		self._last_mouse_pos: Optional[QPoint] = None
		self._definition_cache_revision = -1
		self._definition_cache: dict[str, list[tuple[str, Position, str | None]]] = {}
		self._tooltip_timer = QTimer(self)
		self._tooltip_timer.setSingleShot(True)
		self._tooltip_timer.setInterval(280)
		self._tooltip_timer.timeout.connect(self._show_pending_definition_tooltip)
		self.setMouseTracking(True)

		self.blockCountChanged.connect(self.update_line_number_area_width)
		self.updateRequest.connect(self.update_line_number_area)
		self.cursorPositionChanged.connect(self.highlight_current_line)

		self.update_line_number_area_width(0)
		self.highlight_current_line()

	def _format_signature(self, sig: str) -> str:
		match = re.match(
			r"^((?:proc|fn)\s+\w+)\s*\((.*?)\)(.*)$",
			sig,
			re.DOTALL,
		)
		if not match:
			return sig

		head, params_raw, tail = match.groups()
		params = [p.strip() for p in re.split(r",|\n", params_raw) if p.strip()]

		if len(params) > 3:
			# Use non-breaking spaces (&nbsp;) so HTML preserves leading parameter indent
			formatted_params = "<br>".join(f"&nbsp;&nbsp;&nbsp;&nbsp;{p}" for p in params)
			return f"{head}(<br>{formatted_params}<br>){tail}"

		return sig

	def _highlight_signature_html(self, sig: str) -> str:
		theme_name = getattr(self.window(), "current_theme", "Default Dark")
		theme = THEMES.get(theme_name, THEMES["Default Dark"])

		clean_sig_text = sig.replace("&nbsp;", " ").replace("<br>", "\n")

		doc = QTextDocument()
		doc.setDefaultFont(self.font())
		doc.setPlainText(clean_sig_text)

		highlighter = XPP26SyntaxHighlighter(doc, theme)
		highlighter.rehighlight()

		raw_html = doc.toHtml()

		body_match = re.search(r"<body[^>]*>(.*?)</body>", raw_html, re.DOTALL)
		content = body_match.group(1) if body_match else raw_html

		paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', content, re.DOTALL)
		if paragraphs:
			formatted_lines = []
			for line in paragraphs:
				leading_spaces = len(line) - len(line.lstrip(" "))
				if leading_spaces > 0:
					line = ("&nbsp;" * leading_spaces) + line.lstrip(" ")
				formatted_lines.append(line)
			content = "<br>".join(formatted_lines)

		return content

	def _find_definition_signature(self, name: str, hover_pos_idx: int) -> str | None:
		document = self.document()
		revision = document.revision()
		if revision != self._definition_cache_revision:
			text = self.toPlainText()
			tokens, _ = lex("<editor>", text)
			definitions: dict[str, list[tuple[str, Position, str | None]]] = {}
			if tokens:
				program = parse(tokens).value
				if program is not None:
					_collect_definition_details(program, tokens, definitions, document)
			self._definition_cache = definitions
			self._definition_cache_revision = revision
		matches = self._definition_cache.get(name, ())
		if not matches:
			return None
		before = [match for match in matches if match[1].idx <= hover_pos_idx]
		match = max(before, key=lambda item: item[1].idx) if before else min(
			matches, key=lambda item: item[1].idx
		)
		return match[2]

	def _show_pending_definition_tooltip(self) -> None:
		if self._last_mouse_pos is not None:
			self._show_definition_tooltip(self._last_mouse_pos)

	def _show_definition_tooltip(self, pos: QPoint) -> None:
		word_cursor = self._identifier_word_at(pos)
		if not word_cursor:
			QToolTip.hideText()
			return

		name = word_cursor.selectedText()
		hover_pos_idx = word_cursor.position()
		sig = self._find_definition_signature(name, hover_pos_idx)
		if sig:
			formatted_sig = self._format_signature(sig)
			html_sig = self._highlight_signature_html(formatted_sig)
			global_pos = self.viewport().mapToGlobal(pos)
			QToolTip.showText(global_pos, html_sig, self.viewport())
		else:
			QToolTip.hideText()

	def _identifier_word_at(self, pos) -> Optional[QTextCursor]:
		cursor = self.cursorForPosition(pos)
		text = cursor.block().text()
		idx = cursor.positionInBlock()

		def is_word_char(ch: str) -> bool:
			return ch.isalnum() or ch == "_"

		on_word = (idx < len(text) and is_word_char(text[idx])) or (
			idx > 0 and is_word_char(text[idx - 1])
		)
		if not on_word:
			return None

		word_cursor = QTextCursor(cursor)
		word_cursor.select(QTextCursor.SelectionType.WordUnderCursor)
		word = word_cursor.selectedText()
		if not word or not (word[0].isalpha() or word[0] == "_"):
			return None
		return word_cursor

	def _update_hover_underline(self, pos) -> None:
		selection = None
		if pos is not None and (
			QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier
		):
			word_cursor = self._identifier_word_at(pos)
			if word_cursor is not None:
				selection = QTextEdit.ExtraSelection()
				fmt = QTextCharFormat()
				fmt.setFontUnderline(True)
				fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
				selection.format = fmt
				selection.cursor = word_cursor

		self._hover_underline_selection = selection
		self.highlight_current_line()

	def _update_pointer_cursor(self, ctrl_held: bool) -> None:
		if ctrl_held and QApplication.mouseButtons() == Qt.MouseButton.NoButton:
			self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
		else:
			self.viewport().setCursor(Qt.CursorShape.IBeamCursor)

	def mousePressEvent(self, event: QMouseEvent):
		if (
			event.button() == Qt.MouseButton.LeftButton
			and event.modifiers() & Qt.KeyboardModifier.ControlModifier
			and self.go_to_definition_callback
		):
			if self._identifier_word_at(event.pos()) is not None:
				cursor = self.cursorForPosition(event.pos())
				self.go_to_definition_callback(cursor)
				return
		super().mousePressEvent(event)

	def mouseMoveEvent(self, event: QMouseEvent):
		pos = event.pos()
		self._last_mouse_pos = pos
		ctrl_held = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)

		self._update_pointer_cursor(ctrl_held)
		self._update_hover_underline(pos if ctrl_held else None)

		self._tooltip_timer.start()
		super().mouseMoveEvent(event)

	def leaveEvent(self, event):
		self._last_mouse_pos = None
		self._tooltip_timer.stop()
		self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
		self._update_hover_underline(None)
		QToolTip.hideText()
		super().leaveEvent(event)

	def keyPressEvent(self, event: QKeyEvent):
		if event.key() == Qt.Key.Key_Control:
			self._update_pointer_cursor(True)
			self._update_hover_underline(self._last_mouse_pos)
			super().keyPressEvent(event)
			return
		self._handle_other_key_press(event)

	def keyReleaseEvent(self, event: QKeyEvent):
		if event.key() == Qt.Key.Key_Control:
			self._update_pointer_cursor(False)
			self._update_hover_underline(None)
			super().keyReleaseEvent(event)
			return
		super().keyReleaseEvent(event)

	@staticmethod
	def _leading_whitespace(text: str) -> str:
		return text[: len(text) - len(text.lstrip(" \t"))]

	def _handle_return(self):
		cursor = self.textCursor()
		cursor.removeSelectedText()

		block_text = cursor.block().text()
		pos_in_block = cursor.positionInBlock()
		before = block_text[:pos_in_block]
		after = block_text[pos_in_block:]

		indent = self._leading_whitespace(block_text)
		opens_block = before.rstrip().endswith("{")
		closes_next = after.lstrip().startswith("}")

		if opens_block and closes_next:
			inner_indent = indent + self.INDENT
			cursor.insertText("\n" + inner_indent + "\n" + indent)
			cursor.movePosition(QTextCursor.MoveOperation.PreviousBlock)
			cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
			self.setTextCursor(cursor)
		elif opens_block:
			cursor.insertText("\n" + indent + self.INDENT)
		else:
			cursor.insertText("\n" + indent)

	def _maybe_dedent_before_brace(self) -> bool:
		cursor = self.textCursor()
		if cursor.hasSelection():
			return False

		block_text = cursor.block().text()
		pos_in_block = cursor.positionInBlock()
		before = block_text[:pos_in_block]

		if before == "" or before.strip() != "":
			return False

		if before.endswith(self.INDENT):
			new_indent = before[: -len(self.INDENT)]
		elif before.endswith(" "):
			strip_count = min(4, len(before) - len(before.rstrip(" ")))
			if not strip_count:
				return False
			new_indent = before[:-strip_count]
		else:
			return False

		cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
		cursor.movePosition(
			QTextCursor.MoveOperation.Right,
			QTextCursor.MoveMode.KeepAnchor,
			len(before),
		)
		cursor.insertText(new_indent)
		return True

	def _handle_tab(self, indent: bool):
		cursor = self.textCursor()

		if not cursor.hasSelection():
			if indent:
				cursor.insertText(self.INDENT)
			else:
				block_text = cursor.block().text()
				before = block_text[: cursor.positionInBlock()]
				if before.endswith(self.INDENT):
					cursor.movePosition(
						QTextCursor.MoveOperation.Left,
						QTextCursor.MoveMode.KeepAnchor,
						len(self.INDENT),
					)
					cursor.removeSelectedText()
				elif before.endswith(" "):
					strip_count = min(4, len(before) - len(before.rstrip(" ")))
					if strip_count:
						cursor.movePosition(
							QTextCursor.MoveOperation.Left,
							QTextCursor.MoveMode.KeepAnchor,
							strip_count,
						)
						cursor.removeSelectedText()
			return

		doc = self.document()
		start = cursor.selectionStart()
		end = cursor.selectionEnd()
		start_block_num = doc.findBlock(start).blockNumber()
		end_block_obj = doc.findBlock(end)
		end_block_num = end_block_obj.blockNumber()
		if end == end_block_obj.position() and end_block_num > start_block_num:
			end_block_num -= 1

		cursor.beginEditBlock()
		for bn in range(start_block_num, end_block_num + 1):
			block = doc.findBlockByNumber(bn)
			bc = QTextCursor(block)
			if indent:
				bc.insertText(self.INDENT)
			else:
				text = block.text()
				if text.startswith(self.INDENT):
					bc.movePosition(
						QTextCursor.MoveOperation.Right,
						QTextCursor.MoveMode.KeepAnchor,
						1,
					)
					bc.removeSelectedText()
				elif text.startswith(" "):
					n = min(4, len(text) - len(text.lstrip(" ")))
					if n:
						bc.movePosition(
							QTextCursor.MoveOperation.Right,
							QTextCursor.MoveMode.KeepAnchor,
							n,
						)
						bc.removeSelectedText()
		cursor.endEditBlock()

		new_start_block = doc.findBlockByNumber(start_block_num)
		new_end_block = doc.findBlockByNumber(end_block_num)
		sel_cursor = QTextCursor(doc)
		sel_cursor.setPosition(new_start_block.position())
		sel_cursor.setPosition(
			new_end_block.position() + len(new_end_block.text()),
			QTextCursor.MoveMode.KeepAnchor,
		)
		self.setTextCursor(sel_cursor)

	def _handle_other_key_press(self, event: QKeyEvent):
		key = event.key()

		if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
			self._handle_return()
			return

		if key == Qt.Key.Key_Tab:
			self._handle_tab(indent=True)
			return

		if key == Qt.Key.Key_Backtab:
			self._handle_tab(indent=False)
			return

		if event.text() == "}":
			self._maybe_dedent_before_brace()
			super().keyPressEvent(event)
			return

		super().keyPressEvent(event)

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

		if self._hover_underline_selection is not None:
			selections.append(self._hover_underline_selection)

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

		def is_library_name(idx: int) -> bool:
			if idx < 0 or idx >= len(tokens):
				return False
			return tokens[idx].value in STANDARD_LIBRARY_NAMES

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

		comment_start = _comment_start(text)
		if comment_start is not None:
			self.setFormat(comment_start, len(text) - comment_start, self.comment_format)


class VMGraphicsWidget(QWidget):
	def __init__(self, parent=None):
		super().__init__(parent)
		self.width_px = SCREEN_WIDTH
		self.height_px = SCREEN_HEIGHT
		self.scale = 0.5
		self.render_x = 0
		self.render_y = 0
		self.render_width = self.width_px // 2
		self.render_height = self.height_px // 2
		self.setMinimumSize(1, 1)
		self.setSizePolicy(
			QSizePolicy.Policy.Expanding,
			QSizePolicy.Policy.Expanding,
		)

		self.image = QImage(self.width_px, self.height_px, QImage.Format.Format_RGB32)
		self.image.fill(QColor("black"))
		self.active_vm = None
		self._forwarded_keys: dict[int, int] = {}
		self._wheel_angle_remainder = 0
		self._wheel_pixel_remainder = 0
		self._wheel_horizontal_angle_remainder = 0
		self._wheel_horizontal_pixel_remainder = 0

		self.setMouseTracking(True)
		self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

	def sizeHint(self):
		return QSize(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)

	def _fit_stage(self) -> None:
		available_width = max(1, self.width())
		available_height = max(1, self.height())
		stage_width = max(1, self.width_px)
		stage_height = max(1, self.height_px)
		self.scale = max(
			1e-6,
			min(available_width / stage_width, available_height / stage_height),
		)
		self.render_width = max(1, round(stage_width * self.scale))
		self.render_height = max(1, round(stage_height * self.scale))
		self.render_x = (available_width - self.render_width) // 2
		self.render_y = (available_height - self.render_height) // 2

	@pyqtSlot(object)
	def set_active_vm(self, vm):
		if self.active_vm is not None and self.active_vm is not vm:
			self.active_vm.devices.input.release_all()
		self._forwarded_keys.clear()
		self._wheel_angle_remainder = 0
		self._wheel_pixel_remainder = 0
		self._wheel_horizontal_angle_remainder = 0
		self._wheel_horizontal_pixel_remainder = 0
		self.active_vm = vm

	@pyqtSlot(object)
	def update_frame(self, frame):
		if isinstance(frame, FrameSnapshot):
			if (frame.width, frame.height) != (self.width_px, self.height_px):
				self.width_px = frame.width
				self.height_px = frame.height
				self._fit_stage()
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
			if not hasattr(self, "image") or self.image.format() == QImage.Format.Format_Indexed8:
				self.image = QImage(self.width_px, self.height_px, QImage.Format.Format_ARGB32)
			for y in range(min(self.height_px, len(frame))):
				for x in range(min(self.width_px, len(frame[y]))):
					self.image.setPixelColor(x, y, colors[frame[y][x] % len(colors)])
		self.update()

	def paintEvent(self, event):
		self._fit_stage()
		painter = QPainter(self)
		painter.fillRect(self.rect(), QColor("black"))
		scaled_pixmap = QPixmap.fromImage(self.image).scaled(
			self.render_width,
			self.render_height,
			Qt.AspectRatioMode.IgnoreAspectRatio,
			Qt.TransformationMode.FastTransformation,
		)
		painter.drawPixmap(self.render_x, self.render_y, scaled_pixmap)

	def _pointer_position(self, event: QMouseEvent) -> tuple[int, int]:
		scale_x = self.render_width / max(1, self.width_px)
		scale_y = self.render_height / max(1, self.height_px)
		return (
			max(
				0,
				min(
					self.width_px - 1,
					int((event.position().x() - self.render_x) / max(scale_x, 1e-6)),
				),
			),
			max(
				0,
				min(
					self.height_px - 1,
					int((event.position().y() - self.render_y) / max(scale_y, 1e-6)),
				),
			),
		)

	def _pointer_inside_stage(self, event: QMouseEvent | QWheelEvent) -> bool:
		position = event.position()
		return (
			self.render_x <= position.x() < self.render_x + self.render_width
			and self.render_y <= position.y() < self.render_y + self.render_height
		)

	def _update_pointer(self, event: QMouseEvent | QWheelEvent) -> bool:
		if self.active_vm and self._pointer_inside_stage(event):
			x, y = self._pointer_position(event)
			self.active_vm.devices.input.move_pointer(x, y)
			return True
		return False

	def _modifier_mask(self, event: QKeyEvent | QWheelEvent) -> int:
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
		key_name = {
			Qt.Key.Key_Backspace: "backspace",
			Qt.Key.Key_Tab: "tab",
			Qt.Key.Key_Backtab: "backtab",
			Qt.Key.Key_Return: "return",
			Qt.Key.Key_Enter: "enter",
			Qt.Key.Key_Escape: "escape",
			Qt.Key.Key_Space: "space",
			Qt.Key.Key_Left: "left",
			Qt.Key.Key_Up: "up",
			Qt.Key.Key_Right: "right",
			Qt.Key.Key_Down: "down",
			Qt.Key.Key_Delete: "delete",
		}.get(key, "")
		if not key_name and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
			key_name = chr(ord("a") + int(key - Qt.Key.Key_A))
		return normalize_key_code(
			key_name,
			event.text(),
			control=bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier),
			fallback=int(key),
		)

	@staticmethod
	def _physical_key(event: QKeyEvent) -> int:
		key = int(event.key())
		if key == int(Qt.Key.Key_Backtab):
			return int(Qt.Key.Key_Tab)
		return key

	def event(self, event):
		# QWidget normally consumes Tab/Backtab for host focus traversal before
		# keyPressEvent sees them. While the VM stage owns focus, both events are
		# virtual-machine input and must never move focus around the host IDE.
		if self.active_vm and event.type() in (
			QEvent.Type.KeyPress,
			QEvent.Type.KeyRelease,
		) and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
			if event.type() == QEvent.Type.KeyPress:
				self.keyPressEvent(event)
			else:
				self.keyReleaseEvent(event)
			event.accept()
			return True
		return super().event(event)

	def mouseMoveEvent(self, event: QMouseEvent):
		self._update_pointer(event)

	def mousePressEvent(self, event: QMouseEvent):
		if self.active_vm and self._pointer_inside_stage(event):
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
			if self._pointer_inside_stage(event):
				self._update_pointer(event)
			button = {
				Qt.MouseButton.LeftButton: 1,
				Qt.MouseButton.RightButton: 2,
				Qt.MouseButton.MiddleButton: 4,
			}.get(event.button())
			if button:
				self.active_vm.devices.input.set_button(button, False)

	@staticmethod
	def _consume_wheel_units(total: int, unit: int) -> tuple[int, int]:
		steps = total // unit if total >= 0 else -((-total) // unit)
		return steps, total - steps * unit

	def wheelEvent(self, event: QWheelEvent):
		if not self.active_vm:
			event.ignore()
			return

		if not self._update_pointer(event):
			event.ignore()
			return
		modifiers = self._modifier_mask(event)
		horizontal = bool(modifiers & 1)
		angle_vector = event.angleDelta()
		angle_delta = (
			angle_vector.x()
			if horizontal and angle_vector.x()
			else angle_vector.y()
		)
		if angle_delta:
			if horizontal:
				steps, self._wheel_horizontal_angle_remainder = self._consume_wheel_units(
					self._wheel_horizontal_angle_remainder + angle_delta,
					120,
				)
			else:
				steps, self._wheel_angle_remainder = self._consume_wheel_units(
					self._wheel_angle_remainder + angle_delta,
					120,
				)
		else:
			pixel_vector = event.pixelDelta()
			pixel_delta = (
				pixel_vector.x()
				if horizontal and pixel_vector.x()
				else pixel_vector.y()
			)
			if horizontal:
				steps, self._wheel_horizontal_pixel_remainder = self._consume_wheel_units(
					self._wheel_horizontal_pixel_remainder + pixel_delta,
					40,
				)
			else:
				steps, self._wheel_pixel_remainder = self._consume_wheel_units(
					self._wheel_pixel_remainder + pixel_delta,
					40,
				)
		if steps:
			self.active_vm.devices.input.add_scroll_delta(steps, modifiers)
		event.accept()

	def keyPressEvent(self, event: QKeyEvent):
		if self.active_vm and not event.isAutoRepeat():
			key = self._key_code(event)
			physical_key = self._physical_key(event)
			previous = self._forwarded_keys.get(physical_key)
			if previous is not None and previous != key:
				self.active_vm.devices.input.set_key(
					previous, False, self._modifier_mask(event)
				)
			self._forwarded_keys[physical_key] = key
			self.active_vm.devices.input.set_key(
				key, True, self._modifier_mask(event)
			)

	def keyReleaseEvent(self, event: QKeyEvent):
		if self.active_vm and not event.isAutoRepeat():
			key = self._forwarded_keys.pop(self._physical_key(event), None)
			if key is None:
				key = self._key_code(event)
			self.active_vm.devices.input.set_key(
				key, False, self._modifier_mask(event)
			)

	def focusOutEvent(self, event):
		if self.active_vm:
			self.active_vm.devices.input.release_all()
			self._forwarded_keys.clear()
			self._wheel_angle_remainder = 0
			self._wheel_pixel_remainder = 0
			self._wheel_horizontal_angle_remainder = 0
			self._wheel_horizontal_pixel_remainder = 0
		super().focusOutEvent(event)


class _ToneStream(QIODevice):
	"""Thread-safe pull stream for the host's optional note synthesizer."""

	def __init__(self, sample_rate: int = 48_000, parent: QObject | None = None):
		super().__init__(parent)
		self._sample_rate = max(8_000, int(sample_rate))
		self._voices: tuple[AudioVoice, ...] = ()
		self._volume = 0.0
		self._phases: dict[tuple[int, int], float] = {}
		self._lock = threading.RLock()
		self.open(QIODevice.OpenModeFlag.ReadOnly)

	def set_sample_rate(self, sample_rate: int) -> None:
		with self._lock:
			self._sample_rate = max(8_000, int(sample_rate))
			self._phases.clear()

	def set_state(self, state: AudioState) -> None:
		voices = tuple(state.voices)
		keys = {(voice.pitch, voice.instrument) for voice in voices}
		with self._lock:
			self._voices = voices
			self._volume = max(0.0, min(1.0, float(state.volume) / 100.0))
			self._phases = {
				key: phase for key, phase in self._phases.items() if key in keys
			}

	def silence(self) -> None:
		self.set_state(AudioState((), 0, False))

	def isSequential(self) -> bool:
		return True

	def bytesAvailable(self) -> int:
		return 16_384 + super().bytesAvailable()

	def readData(self, maxlen: int) -> bytes:
		byte_count = max(0, int(maxlen))
		frame_count = byte_count // 4
		if frame_count == 0:
			return b""

		with self._lock:
			voices = self._voices
			volume = self._volume
			sample_rate = self._sample_rate
			phases = dict(self._phases)

		if not voices or volume <= 0.0:
			return bytes(frame_count * 4)

		gain = 0.24 * volume / max(1.0, math.sqrt(len(voices)))
		steps: list[tuple[tuple[int, int], float, float]] = []
		for voice in voices:
			key = (int(voice.pitch), int(voice.instrument))
			frequency = 440.0 * 2.0 ** ((max(0, min(127, voice.pitch)) - 69) / 12.0)
			step = math.tau * frequency / sample_rate
			amplitude = gain * max(0, min(127, voice.velocity)) / 127.0
			steps.append((key, step, amplitude))

		output = array("h")
		for _ in range(frame_count):
			sample = 0.0
			for key, step, amplitude in steps:
				phase = phases.get(key, 0.0)
				sample += math.sin(phase) * amplitude
				phases[key] = (phase + step) % math.tau
			value = max(-32_768, min(32_767, round(sample * 32_767.0)))
			output.append(value)
			output.append(value)

		with self._lock:
			active = {(voice.pitch, voice.instrument) for voice in self._voices}
			for key, phase in phases.items():
				if key in active:
					self._phases[key] = phase
		return output.tobytes()

	def writeData(self, data: bytes) -> int:
		return 0


class _ToneEngine(QObject):
	"""Lazily connects the portable note stream to a native Qt audio output."""

	def __init__(self, parent: QObject | None = None):
		super().__init__(parent)
		self.stream = _ToneStream(parent=self)
		self.sink = None
		self._unavailable = QAudioSink is None

	def _ensure_output(self) -> None:
		if self.sink is not None or self._unavailable:
			return
		try:
			device = QMediaDevices.defaultAudioOutput()
			if device.isNull():
				self._unavailable = True
				return
			preferred_rate = device.preferredFormat().sampleRate()
			format_ = None
			for sample_rate in dict.fromkeys((48_000, 44_100, preferred_rate)):
				if sample_rate <= 0:
					continue
				candidate = QAudioFormat()
				candidate.setSampleRate(sample_rate)
				candidate.setChannelCount(2)
				candidate.setSampleFormat(QAudioFormat.SampleFormat.Int16)
				if device.isFormatSupported(candidate):
					format_ = candidate
					break
			if format_ is None:
				self._unavailable = True
				return
			self.stream.set_sample_rate(format_.sampleRate())
			self.sink = QAudioSink(device, format_, self)
			self.sink.setBufferSize(max(4_096, format_.sampleRate() * 4 // 10))
			self.sink.start(self.stream)
		except (OSError, RuntimeError):
			self.sink = None
			self._unavailable = True

	@pyqtSlot(object)
	def update_state(self, state: AudioState) -> None:
		self.stream.set_state(state)
		if state.voices and state.volume > 0:
			self._ensure_output()

	def silence(self) -> None:
		self.stream.silence()

	def shutdown(self) -> None:
		self.silence()
		if self.sink is not None:
			self.sink.stop()
			self.sink = None
		self.stream.close()


class VMWorkerThread(QThread):
	execution_finished = pyqtSignal(object, object, str)
	frame_ready = pyqtSignal(object)
	vm_ready = pyqtSignal(object)
	output_ready = pyqtSignal(str)
	input_requested = pyqtSignal(object, object)
	clipboard_read_requested = pyqtSignal(object, object)
	clipboard_write_requested = pyqtSignal(str, object, object)
	audio_state = pyqtSignal(object)

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

			def clipboard_read_handler() -> str:
				ready = threading.Event()
				holder: dict[str, str] = {}
				self.clipboard_read_requested.emit(ready, holder)
				while not ready.wait(0.05):
					if self.context.cancel_event.is_set() or self.isInterruptionRequested():
						return ""
				return holder.get("value", "")

			def clipboard_write_handler(text: str) -> bool:
				ready = threading.Event()
				holder: dict[str, bool] = {}
				self.clipboard_write_requested.emit(text, ready, holder)
				while not ready.wait(0.05):
					if self.context.cancel_event.is_set() or self.isInterruptionRequested():
						return False
				return bool(holder.get("written", False))

			self.context.output_handler = output_handler
			self.context.input_handler = input_handler
			self.context.frame_handler = frame_handler
			self.context.vm_ready_handler = self.vm_ready.emit
			self.context.audio_handler = self.audio_state.emit
			self.context.clipboard_read_handler = clipboard_read_handler
			self.context.clipboard_write_handler = clipboard_write_handler
			result, error, asm = run(self.filename, self.code, self.context)

			self.execution_finished.emit(result, error, asm or "")
		except Exception:
			error_string = traceback.format_exc()
			self.execution_finished.emit(
				None, f"Runtime Thread Exception\n{error_string}", ""
			)


class FindReplaceBar(QScrollArea):
	def __init__(self, editor: QPlainTextEdit, parent=None):
		super().__init__(parent)
		self.editor = editor
		self.setObjectName("CompactToolScroll")
		self.setFrameShape(QFrame.Shape.NoFrame)
		self.setWidgetResizable(True)
		self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
		self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
		self.setMinimumHeight(42)
		self.setMaximumHeight(62)
		content = QWidget()
		layout = QHBoxLayout(content)
		layout.setContentsMargins(4, 4, 4, 4)

		self.find_input = QLineEdit()
		self.find_input.setPlaceholderText("Find")
		self.replace_input = QLineEdit()
		self.replace_input.setPlaceholderText("Replace with")
		self.case_checkbox = QCheckBox("Match case")

		self.find_prev_btn = QPushButton("Prev")
		self.find_next_btn = QPushButton("Next")
		self.replace_btn = QPushButton("Replace")
		self.replace_all_btn = QPushButton("Replace All")
		self.close_btn = QPushButton("✕")
		self.close_btn.setFixedWidth(28)

		layout.addWidget(QLabel("Find:"))
		layout.addWidget(self.find_input)
		layout.addWidget(self.find_prev_btn)
		layout.addWidget(self.find_next_btn)
		layout.addWidget(self.case_checkbox)
		layout.addWidget(self.replace_input)
		layout.addWidget(self.replace_btn)
		layout.addWidget(self.replace_all_btn)
		layout.addStretch()
		layout.addWidget(self.close_btn)
		self.setWidget(content)

		self.find_next_btn.clicked.connect(lambda: self.find(backward=False))
		self.find_prev_btn.clicked.connect(lambda: self.find(backward=True))
		self.replace_btn.clicked.connect(self.replace_one)
		self.replace_all_btn.clicked.connect(self.replace_all)
		self.close_btn.clicked.connect(self.hide_and_focus_editor)
		self.find_input.returnPressed.connect(lambda: self.find(backward=False))
		self.replace_input.returnPressed.connect(self.replace_one)

		close_shortcut = QShortcut(QKeySequence("Escape"), self)
		close_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
		close_shortcut.activated.connect(self.hide_and_focus_editor)

	def set_replace_mode(self, enabled: bool):
		self.replace_input.setVisible(enabled)
		self.replace_btn.setVisible(enabled)
		self.replace_all_btn.setVisible(enabled)

	def hide_and_focus_editor(self):
		self.hide()
		self.editor.setFocus()

	def _flags(self, backward: bool = False) -> QTextDocument.FindFlag:
		flags = QTextDocument.FindFlag(0)
		if backward:
			flags |= QTextDocument.FindFlag.FindBackward
		if self.case_checkbox.isChecked():
			flags |= QTextDocument.FindFlag.FindCaseSensitively
		return flags

	def find(self, backward: bool = False) -> bool:
		text = self.find_input.text()
		if not text:
			return False

		found = self.editor.find(text, self._flags(backward))
		if not found:
			# wrap around to the other end of the document and try again
			cursor = self.editor.textCursor()
			cursor.movePosition(
				QTextCursor.MoveOperation.End
				if backward
				else QTextCursor.MoveOperation.Start
			)
			self.editor.setTextCursor(cursor)
			found = self.editor.find(text, self._flags(backward))
		return found

	def replace_one(self):
		text = self.find_input.text()
		if not text:
			return

		cursor = self.editor.textCursor()
		selected = cursor.selectedText()
		matches = (
			selected == text
			if self.case_checkbox.isChecked()
			else selected.lower() == text.lower()
		)
		if cursor.hasSelection() and matches:
			cursor.insertText(self.replace_input.text())

		self.find(backward=False)

	def replace_all(self) -> int:
		text = self.find_input.text()
		if not text:
			return 0

		replacement = self.replace_input.text()
		cursor = self.editor.textCursor()
		cursor.movePosition(QTextCursor.MoveOperation.Start)
		self.editor.setTextCursor(cursor)

		count = 0
		group_cursor = self.editor.textCursor()
		group_cursor.beginEditBlock()
		while self.editor.find(text, self._flags(backward=False)):
			self.editor.textCursor().insertText(replacement)
			count += 1
		group_cursor.endEditBlock()
		return count


class X26IDE(QMainWindow):
	def __init__(self):
		super().__init__()

		self.setFont(_host_monospace_font())

		self.setWindowTitle("Xenon IDE")
		self.setGeometry(100, 100, 1200, 760)

		self.current_file: Optional[Path] = None
		self.os_device = OSDevice(settings_path=default_settings_path())
		self.audio_engine = _ToneEngine(self)
		self.runtime_context = RuntimeContext(os_device=self.os_device)
		self.current_theme = "Default Dark"
		self.worker: Optional[VMWorkerThread] = None
		self.graphics_maximized = False
		self._graphics_splitter_sizes: tuple[list[int], list[int]] | None = None

		self.refresh_timer = QTimer()
		self.refresh_timer.timeout.connect(self.poll_vm_buffer)

		central_widget = QWidget()
		self.setCentralWidget(central_widget)
		root_layout = QVBoxLayout(central_widget)
		root_layout.setContentsMargins(0, 0, 0, 0)
		root_layout.setSpacing(0)

		self.workspace_tabs = QTabWidget()
		self.workspace_tabs.setObjectName("WorkspaceTabs")
		self.workspace_tabs.setDocumentMode(True)
		self.workspace_tabs.setMovable(False)
		self.workspace_tabs.setTabsClosable(False)
		root_layout.addWidget(self.workspace_tabs)

		self.code_tab = QWidget()
		self.code_tab.setObjectName("CodeWorkspace")
		main_layout = QVBoxLayout(self.code_tab)
		main_layout.setContentsMargins(10, 8, 10, 10)
		main_layout.setSpacing(8)

		toolbar_widget = QWidget()
		toolbar_layout = QHBoxLayout(toolbar_widget)
		toolbar_layout.setContentsMargins(0, 0, 0, 0)
		code_toolbar_callbacks = {
			"new": self.new_file,
			"open": self.open_file,
			"save": self.save_file,
			"save-as": self.save_as_file,
		}
		for spec in CODE_TOOLBAR_ACTIONS:
			btn = QPushButton(spec.label)
			btn.clicked.connect(code_toolbar_callbacks[spec.key])
			toolbar_layout.addWidget(btn)

		toolbar_layout.addStretch()
		self.system_clipboard_toggle = QCheckBox("System clipboard")
		self.system_clipboard_toggle.setChecked(False)
		self.system_clipboard_toggle.setToolTip(
			"Allow Xe programs to read and write the computer clipboard. "
			"This host-only bridge is not exported to Scratch."
		)
		self.system_clipboard_toggle.setAccessibleName("Enable system clipboard bridge")
		toolbar_layout.addWidget(self.system_clipboard_toggle)
		toolbar_layout.addWidget(QLabel("Theme:"))

		self.theme_dropdown = QComboBox()
		self.theme_dropdown.addItems(list(THEMES.keys()))
		self.theme_dropdown.setSizeAdjustPolicy(
			QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
		)
		self.theme_dropdown.setMinimumContentsLength(12)
		self.theme_dropdown.setMaximumWidth(180)
		self.theme_dropdown.setCurrentText(self.current_theme)
		self.theme_dropdown.currentTextChanged.connect(self.change_theme)
		toolbar_layout.addWidget(self.theme_dropdown)

		self.run_button = QPushButton("Run")
		self.run_button.clicked.connect(self.run_code)
		toolbar_layout.addWidget(self.run_button)
		self.code_toolbar_scroll = QScrollArea()
		self.code_toolbar_scroll.setObjectName("CompactToolScroll")
		self.code_toolbar_scroll.setFrameShape(QFrame.Shape.NoFrame)
		self.code_toolbar_scroll.setWidgetResizable(True)
		self.code_toolbar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
		self.code_toolbar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
		self.code_toolbar_scroll.setMinimumHeight(42)
		self.code_toolbar_scroll.setMaximumHeight(62)
		self.code_toolbar_scroll.setWidget(toolbar_widget)
		main_layout.addWidget(self.code_toolbar_scroll)

		main_splitter = QSplitter(Qt.Orientation.Horizontal)
		self.main_splitter = main_splitter

		editor_container = QWidget()
		self.editor_container = editor_container
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
		self.editor.go_to_definition_callback = self.go_to_definition

		self.find_replace_bar = FindReplaceBar(self.editor)
		self.find_replace_bar.hide()
		editor_layout.addWidget(self.find_replace_bar)

		self.highlighter = XPP26SyntaxHighlighter(
			self.editor.document(), THEMES[self.current_theme]
		)
		main_splitter.addWidget(editor_container)

		right_panel_splitter = QSplitter(Qt.Orientation.Vertical)
		self.right_panel_splitter = right_panel_splitter

		output_container = QWidget()
		self.output_container = output_container
		output_layout = QVBoxLayout(output_container)
		output_layout.setContentsMargins(0, 0, 0, 0)
		output_layout.addWidget(QLabel("Terminal:"))
		self.output = QTextEdit()
		self.output.setReadOnly(True)
		self.output.document().setMaximumBlockCount(5_000)
		self._ansi_stream = AnsiHtmlStream()
		output_layout.addWidget(self.output)
		self.input_line = QLineEdit()
		self.input_line.setPlaceholderText("Program input")
		self.input_line.returnPressed.connect(self.submit_program_input)
		self.input_line.hide()
		output_layout.addWidget(self.input_line)
		self._pending_input: tuple[threading.Event, dict[str, str]] | None = None
		right_panel_splitter.addWidget(output_container)

		graphics_container = QWidget()
		self.graphics_container = graphics_container

		graphics_layout = QVBoxLayout(graphics_container)
		graphics_layout.setContentsMargins(4, 4, 4, 4)
		graphics_layout.setSpacing(4)

		graphics_header = QHBoxLayout()
		graphics_header.addWidget(QLabel("Graphics View:"))
		graphics_header.addStretch(1)
		self.graphics_size_button = QPushButton("Maximize")
		self.graphics_size_button.clicked.connect(self.toggle_graphics_view)
		graphics_header.addWidget(self.graphics_size_button)
		graphics_layout.addLayout(graphics_header)

		self.graphics_view = VMGraphicsWidget()
		graphics_layout.addWidget(
			self.graphics_view,
			stretch=1,
		)

		right_panel_splitter.addWidget(graphics_container)

		right_panel_splitter.setSizes([180, 460])
		right_panel_splitter.setStretchFactor(0, 1)
		right_panel_splitter.setStretchFactor(1, 3)
		main_splitter.addWidget(right_panel_splitter)

		main_splitter.setStretchFactor(0, 2)
		main_splitter.setStretchFactor(1, 1)
		main_splitter.setSizes([650, 520])
		main_layout.addWidget(main_splitter)

		self.workspace_tabs.addTab(self.code_tab, WORKBENCH_TABS[0].label)
		self.converter_view = ConverterPane(request_provider=self._converter_request)
		self.workspace_tabs.addTab(self.converter_view, WORKBENCH_TABS[1].label)
		self.image_studio_view = ImageStudioPane()
		self.workspace_tabs.addTab(self.image_studio_view, WORKBENCH_TABS[2].label)
		self.help_view = HelpPane()
		self.workspace_tabs.addTab(self.help_view, WORKBENCH_TABS[3].label)
		self.editor.document().modificationChanged.connect(self.update_title)
		self.editor.document().contentsChanged.connect(
			self.converter_view.invalidate
		)

		self.setup_menu_bar()
		self.apply_theme()

	def resizeEvent(self, event) -> None:
		super().resizeEvent(event)
		if hasattr(self, "system_clipboard_toggle"):
			self.system_clipboard_toggle.setText(
				"Clipboard" if self.width() < 760 else "System clipboard"
			)

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

		edit_menu = menubar.addMenu("Edit")
		for name, shortcut, slot in [
			("Find", "Ctrl+F", lambda: self.show_find_bar(replace=False)),
			("Replace", "Ctrl+H", lambda: self.show_find_bar(replace=True)),
			("Rename Symbol", "F2", self.rename_symbol),
			("Toggle Comment", "Ctrl+/", self.toggle_comment),
		]:
			act = edit_menu.addAction(name)
			act.setShortcut(shortcut)
			act.triggered.connect(slot)

		view_menu = menubar.addMenu("View")
		self.graphics_view_action = view_menu.addAction("Maximize Graphics View")
		self.graphics_view_action.setShortcut("Ctrl+Shift+G")
		self.graphics_view_action.triggered.connect(self.toggle_graphics_view)
		view_menu.addSeparator()
		for index, spec in enumerate(WORKBENCH_TABS):
			action = view_menu.addAction(spec.label.replace("→", "to"))
			action.setShortcut(spec.shortcut)
			action.triggered.connect(
				lambda checked=False, tab_index=index: self.workspace_tabs.setCurrentIndex(tab_index)
			)

		help_menu = menubar.addMenu("Help")
		help_action = help_menu.addAction("Xe and IDE Help")
		help_action.setShortcut(QKeySequence.StandardKey.HelpContents)
		help_action.triggered.connect(self.open_help)

	def toggle_graphics_view(self):
		self.workspace_tabs.setCurrentWidget(self.code_tab)
		self.graphics_maximized = not self.graphics_maximized
		if self.graphics_maximized:
			self._graphics_splitter_sizes = (
				self.main_splitter.sizes(),
				self.right_panel_splitter.sizes(),
			)
			self.editor_container.hide()
			self.output_container.hide()
			self.graphics_size_button.setText("Restore")
			self.graphics_view_action.setText("Restore Graphics View")
		else:
			self.editor_container.show()
			self.output_container.show()
			if self._graphics_splitter_sizes:
				self.main_splitter.setSizes(self._graphics_splitter_sizes[0])
				self.right_panel_splitter.setSizes(self._graphics_splitter_sizes[1])
			self.graphics_size_button.setText("Maximize")
			self.graphics_view_action.setText("Maximize Graphics View")
		self.graphics_view.setFocus(Qt.FocusReason.OtherFocusReason)

	def apply_theme(self):
		theme = THEMES[self.current_theme]
		self.editor.line_number_bg = QColor(theme["toolbar_bg"])
		self.editor.line_number_fg = QColor(theme["comment"])
		is_light = QColor(theme["background"]).lightness() > 145
		warning_color = "#8a5800" if is_light else "#e8b967"
		disabled_surface = "#c5ccda" if is_light else "#334057"
		stylesheet = f"""
			QMainWindow {{ background-color: {theme['background']}; color: {theme['foreground']}; }}
			QWidget {{ background-color: {theme['background']}; color: {theme['foreground']}; }}
			QMenuBar {{ background-color: {theme['toolbar_bg']}; color: {theme['foreground']}; border-bottom: 1px solid #555; }}
			QMenuBar::item:selected {{ background-color: {theme['button']}; }}
			QMenu {{ background-color: {theme['toolbar_bg']}; color: {theme['foreground']}; border: 1px solid #39445a; padding: 4px; }}
			QMenu::item {{ padding: 6px 24px 6px 10px; border-radius: 3px; }}
			QMenu::item:selected {{ background-color: {theme['button']}; }}
			QPushButton {{ background-color: {theme['button']}; color: {theme['foreground']}; border: 1px solid transparent; border-radius: 4px; padding: 6px 11px; font-weight: 600; }}
			QPushButton:hover {{ background-color: {theme['button']}; border-color: {theme['keyword']}; }}
			QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus, QListWidget:focus {{ border: 1px solid {theme['keyword']}; }}
			QPushButton#PrimaryButton {{ background-color: {theme['keyword']}; color: {theme['background']}; }}
			QPushButton#PrimaryButton:hover {{ background-color: {theme['button_hover']}; }}
			QPushButton#PrimaryButton:disabled {{ background-color: {disabled_surface}; color: {theme['comment']}; border-color: transparent; }}
			QPushButton#SecondaryButton {{ border: 1px solid #47556d; }}
			QToolButton {{ background-color: transparent; color: {theme['foreground']}; border: 1px solid transparent; border-radius: 4px; padding: 5px 7px; }}
			QToolButton:hover {{ background-color: {theme['button']}; }}
			QToolButton:checked {{ background-color: {theme['button']}; border-color: {theme['keyword']}; }}
			QPushButton:disabled, QToolButton:disabled {{ color: {theme['comment']}; border-color: transparent; }}
			QPlainTextEdit {{ background-color: {theme['background']}; color: {theme['foreground']}; border: 1px solid #555; }}
			QTextEdit, QTextBrowser {{ background-color: {theme['background']}; color: {theme['foreground']}; border: 1px solid #39445a; border-radius: 4px; }}
			QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus {{ border-color: {theme['keyword']}; }}
			QLineEdit, QComboBox, QSpinBox {{ background-color: {theme['output_bg']}; color: {theme['foreground']}; border: 1px solid #39445a; border-radius: 4px; padding: 5px 7px; min-height: 20px; }}
			QComboBox::drop-down {{ border: none; width: 24px; }}
			QListWidget {{ background-color: {theme['output_bg']}; color: {theme['foreground']}; border: 1px solid #39445a; border-radius: 4px; outline: none; }}
			QListWidget::item {{ padding: 6px; border-radius: 3px; }}
			QListWidget::item:selected {{ background-color: {theme['button']}; color: {theme['foreground']}; }}
			QListWidget::item:hover {{ background-color: {theme['toolbar_bg']}; }}
			QLabel {{ color: {theme['foreground']}; background-color: transparent; }}
			QCheckBox {{ background-color: transparent; spacing: 6px; }}
			QCheckBox:focus {{ color: {theme['keyword']}; }}
			QLabel#ToolTitle {{ font-size: 18px; font-weight: 650; }}
			QLabel#SectionTitle {{ font-size: 13px; font-weight: 650; }}
			QLabel#MutedText {{ color: {theme['comment']}; }}
			QLabel#WarningText {{ color: {warning_color}; }}
			QLabel#StatusChip {{ border-radius: 10px; padding: 4px 10px; background-color: {theme['button']}; }}
			QLabel#StatusChip[status="success"] {{ background-color: #153f32; color: #70e1b6; }}
			QLabel#StatusChip[status="warning"] {{ background-color: #4a3514; color: #ffd37a; }}
			QLabel#StatusChip[status="error"] {{ background-color: #4a2028; color: #ff9ca8; }}
			QFrame#ToolCard, QFrame#ToolBarCard {{ background-color: {theme['toolbar_bg']}; border: 1px solid #313b4d; border-radius: 6px; }}
			QLabel#ImagePreview {{ background-color: {theme['output_bg']}; border: 1px solid #313b4d; border-radius: 5px; }}
			QTabWidget#WorkspaceTabs::pane {{ border: none; }}
			QTabBar::tab {{ background-color: {theme['toolbar_bg']}; color: {theme['comment']}; border: none; border-bottom: 2px solid transparent; min-width: 96px; padding: 9px 14px; }}
			QTabBar::tab:hover {{ color: {theme['foreground']}; background-color: {theme['button']}; }}
			QTabBar::tab:selected {{ color: {theme['foreground']}; border-bottom-color: {theme['keyword']}; }}
			QScrollBar:vertical {{ background: {theme['output_bg']}; width: 10px; margin: 0; }}
			QScrollBar::handle:vertical {{ background: #526078; min-height: 24px; border-radius: 4px; }}
			QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
			QScrollBar:horizontal {{ background: {theme['output_bg']}; height: 10px; margin: 0; }}
			QScrollBar::handle:horizontal {{ background: #526078; min-width: 24px; border-radius: 4px; }}
			QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
			QScrollArea#CompactToolScroll {{ background: transparent; border: none; }}
			QSlider::groove:horizontal {{ background-color: {theme['output_bg']}; border: 1px solid #39445a; height: 4px; border-radius: 2px; }}
			QSlider::sub-page:horizontal {{ background-color: {theme['keyword']}; border-radius: 2px; }}
			QSlider::handle:horizontal {{ background-color: {theme['foreground']}; border: 1px solid {theme['keyword']}; width: 12px; margin: -5px 0; border-radius: 6px; }}
			QSplitter::handle {{ background-color: #555; }}
			QToolTip {{
                background-color: {theme['toolbar_bg']};
                color: {theme['foreground']};
                border: none;
                padding: 0px;
            }}
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

	def open_help(self):
		self.workspace_tabs.setCurrentWidget(self.help_view)
		self.help_view.focus_search()

	def select_workspace_tool(self, name: str) -> bool:
		"""Select a host workbench tab by a stable command-line friendly name."""
		index = workbench_tab_index(name)
		if index is None or index >= self.workspace_tabs.count():
			return False
		self.workspace_tabs.setCurrentIndex(index)
		return True

	def show_find_bar(self, replace: bool):
		self.find_replace_bar.set_replace_mode(replace)
		self.find_replace_bar.show()
		self.find_replace_bar.find_input.setFocus()
		self.find_replace_bar.find_input.selectAll()

	def toggle_comment(self):
		cursor = self.editor.textCursor()
		doc = self.editor.document()

		if cursor.hasSelection():
			start_block_num = doc.findBlock(cursor.selectionStart()).blockNumber()
			end_pos = cursor.selectionEnd()
			end_block_obj = doc.findBlock(end_pos)
			end_block_num = end_block_obj.blockNumber()
			if end_pos == end_block_obj.position() and end_block_num > start_block_num:
				end_block_num -= 1
		else:
			start_block_num = end_block_num = doc.findBlock(
				cursor.position()
			).blockNumber()

		blocks = [
			doc.findBlockByNumber(bn) for bn in range(start_block_num, end_block_num + 1)
		]
		non_empty = [b for b in blocks if b.text().strip() != ""]
		already_commented = bool(non_empty) and all(
			b.text().lstrip().startswith("#") for b in non_empty
		)

		edit_cursor = self.editor.textCursor()
		edit_cursor.beginEditBlock()
		for block in blocks:
			text = block.text()
			if already_commented:
				stripped = text.lstrip()
				ws_len = len(text) - len(stripped)
				if stripped.startswith("# "):
					remove_len = 2
				elif stripped.startswith("#"):
					remove_len = 1
				else:
					continue
				bc = QTextCursor(block)
				bc.setPosition(block.position() + ws_len)
				bc.movePosition(
					QTextCursor.MoveOperation.Right,
					QTextCursor.MoveMode.KeepAnchor,
					remove_len,
				)
				bc.removeSelectedText()
			else:
				if text.strip() == "":
					continue
				ws_len = len(text) - len(text.lstrip(" \t"))
				bc = QTextCursor(block)
				bc.setPosition(block.position() + ws_len)
				bc.insertText("# ")
		edit_cursor.endEditBlock()

	def rename_symbol(self):
		cursor = self.editor.textCursor()
		cursor.select(QTextCursor.SelectionType.WordUnderCursor)
		old_name = cursor.selectedText()

		if not old_name or not (old_name[0].isalpha() or old_name[0] == "_"):
			return

		new_name, ok = QInputDialog.getText(
			self, "Rename Symbol", f"Rename '{old_name}' to:", text=old_name
		)
		if not ok or not new_name or new_name == old_name:
			return
		if not re.fullmatch(r"[A-Za-z_]\w*", new_name):
			QMessageBox.warning(self, "Invalid name", "Enter a valid Xe identifier.")
			return
		validation_tokens, validation_error = lex("<rename>", new_name)
		if validation_error or not validation_tokens or validation_tokens[0]._type != TT.IDENT:
			QMessageBox.warning(self, "Invalid name", "Xe keywords cannot be used as symbol names.")
			return

		source = self.editor.toPlainText()
		tokens, _ = lex("<editor>", source)
		ranges = [
			(token.start_pos.idx, token.end_pos.idx + 1)
			for token in tokens
			if token._type == TT.IDENT and token.value == old_name
		]
		if not ranges:
			return
		edit_cursor = QTextCursor(self.editor.document())
		edit_cursor.beginEditBlock()
		for start, end in reversed(ranges):
			edit_cursor.setPosition(start)
			edit_cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
			edit_cursor.insertText(new_name)
		edit_cursor.endEditBlock()

	def go_to_definition(self, cursor: QTextCursor):
		word_cursor = QTextCursor(cursor)
		word_cursor.select(QTextCursor.SelectionType.WordUnderCursor)
		name = word_cursor.selectedText()

		if not name or not (name[0].isalpha() or name[0] == "_"):
			return

		code = self.editor.toPlainText()
		tokens, lex_error = lex("<editor>", code)
		if not tokens:
			return

		ast_result = parse(tokens)
		program = ast_result.value
		if program is None:
			return

		definitions: dict = {}
		_collect_definitions(program, tokens, definitions)

		matches = definitions.get(name)
		if not matches:
			return

		click_idx = cursor.position()
		before = [(kind, pos) for kind, pos in matches if pos.idx <= click_idx]
		if before:
			_, target_pos = max(before, key=lambda kp: kp[1].idx)
		else:
			_, target_pos = min(matches, key=lambda kp: kp[1].idx)

		self._move_cursor_to(target_pos)

	def _move_cursor_to(self, pos: Position):
		doc = self.editor.document()
		block = doc.findBlockByNumber(pos.ln)
		if not block.isValid():
			return

		target_cursor = QTextCursor(block)
		target_cursor.setPosition(block.position() + min(pos.col, len(block.text())))
		self.editor.setTextCursor(target_cursor)
		self.editor.centerCursor()
		self.editor.setFocus()

	def new_file(self):
		if not self._confirm_editor_changes("create a new file"):
			return False
		self.workspace_tabs.setCurrentWidget(self.code_tab)
		self.editor.clear()
		self.output.clear()
		self._ansi_stream.reset()
		self.current_file = None
		self.editor.document().setModified(False)
		self.update_title()
		return True

	def open_file(self):
		path, _ = QFileDialog.getOpenFileName(
			self, "Open", "", "Xe Files (*.xe);;All Files (*)"
		)
		if path:
			return self.load_file(Path(path))
		return False

	def load_file(self, path: Path) -> bool:
		if not self._confirm_editor_changes("open another file"):
			return False
		candidate = path.resolve()
		try:
			text = candidate.read_text(encoding="utf-8")
		except (OSError, UnicodeError) as exc:
			QMessageBox.warning(self, "Open failed", f"Could not open {candidate}:\n\n{exc}")
			return False
		self.workspace_tabs.setCurrentWidget(self.code_tab)
		self.current_file = candidate
		self.editor.setPlainText(text)
		self.editor.document().setModified(False)
		self.update_title()
		return True

	def save_file(self) -> bool:
		if not self.current_file:
			return self.save_as_file()
		try:
			_atomic_write_text(self.current_file, self.editor.toPlainText())
		except OSError as exc:
			QMessageBox.warning(self, "Save failed", f"Could not save {self.current_file}:\n\n{exc}")
			return False
		self.editor.document().setModified(False)
		self.update_title()
		return True

	def save_as_file(self) -> bool:
		path, _ = QFileDialog.getSaveFileName(
			self, "Save", "", "Xe Files (*.xe);;All Files (*)"
		)
		if not path:
			return False
		previous = self.current_file
		self.current_file = Path(path).resolve()
		if self.save_file():
			return True
		self.current_file = previous
		self.update_title()
		return False

	def _confirm_editor_changes(self, action: str) -> bool:
		if not self.editor.document().isModified():
			return True
		answer = QMessageBox.warning(
			self,
			"Unsaved Xe source",
			f"Save changes before you {action}?",
			QMessageBox.StandardButton.Save
			| QMessageBox.StandardButton.Discard
			| QMessageBox.StandardButton.Cancel,
			QMessageBox.StandardButton.Save,
		)
		if answer == QMessageBox.StandardButton.Save:
			return self.save_file()
		return answer == QMessageBox.StandardButton.Discard

	def update_title(self, *args):
		modified = self.editor.document().isModified()
		marker = " *" if modified else ""
		name = self.current_file.name if self.current_file else "Untitled"
		self.setWindowTitle(
			f"Xenon IDE - {name}{marker}"
		)
		if hasattr(self, "workspace_tabs"):
			self.workspace_tabs.setTabText(
				self.workspace_tabs.indexOf(self.code_tab),
				"Code *" if modified else "Code",
			)

	def _converter_request(self, scope: str) -> ConversionRequest:
		active_path = self.current_file.resolve() if self.current_file else None
		workspace_root = active_path.parent if active_path else Path.cwd().resolve()
		if active_path is not None:
			for candidate in active_path.parents:
				if (candidate / "workspace.xe").is_file():
					workspace_root = candidate
					break
		if scope == "workspace":
			entry_path = workspace_root / "workspace.xe"
			if active_path == entry_path.resolve():
				source = self.editor.toPlainText()
			elif entry_path.is_file():
				source = read_xe_source(entry_path)
			else:
				source = self.editor.toPlainText()
				entry_path = active_path
			return ConversionRequest(
				scope="workspace",
				source_text=source,
				source_path=entry_path,
				workspace_root=workspace_root,
			)
		return ConversionRequest(
			scope="active",
			source_text=self.editor.toPlainText(),
			source_path=active_path,
			workspace_root=workspace_root,
		)

	@pyqtSlot(str)
	def append_output(self, text: str):
		cursor = self.output.textCursor()
		cursor.movePosition(cursor.MoveOperation.End)
		cursor.insertHtml(
			f'<span style="white-space: pre-wrap;">{self._ansi_stream.feed(text)}</span>'
		)
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

	@pyqtSlot(object, object)
	def read_system_clipboard(self, ready: threading.Event, holder: dict[str, str]) -> None:
		holder["value"] = (
			QApplication.clipboard().text()
			if self.system_clipboard_toggle.isChecked()
			else ""
		)
		ready.set()

	@pyqtSlot(str, object, object)
	def write_system_clipboard(
		self,
		text: str,
		ready: threading.Event,
		holder: dict[str, bool],
	) -> None:
		enabled = self.system_clipboard_toggle.isChecked()
		if enabled:
			QApplication.clipboard().setText(text)
		holder["written"] = enabled
		ready.set()

	def run_code(self):
		self.workspace_tabs.setCurrentWidget(self.code_tab)
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
		self._ansi_stream.reset()
		self.audio_engine.silence()
		self.runtime_context = RuntimeContext(os_device=self.os_device)
		filename = str(self.current_file) if self.current_file else "<editor>"

		self.worker = VMWorkerThread(filename, code, self.runtime_context)
		self.worker.frame_ready.connect(self.graphics_view.update_frame)
		self.worker.vm_ready.connect(self.graphics_view.set_active_vm)
		self.worker.output_ready.connect(self.append_output)
		self.worker.input_requested.connect(self.request_program_input)
		self.worker.clipboard_read_requested.connect(self.read_system_clipboard)
		self.worker.clipboard_write_requested.connect(self.write_system_clipboard)
		self.worker.audio_state.connect(self.audio_engine.update_state)
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
		self.audio_engine.silence()
		if error:
			self.append_output(f"{error}\n")
		else:
			if self.output.toPlainText() and not self.output.toPlainText().endswith("\n"):
				self.append_output("\n")
			stack = result[:16] if result is not None else []
			self.append_output(f"Execution finished successfully.\n\nStack: {stack}")

		if hasattr(self.runtime_context, "vm") and self.runtime_context.vm:
			frame = self.runtime_context.vm._last_snapshot
			self.graphics_view.update_frame(
				frame if frame is not None else self.runtime_context.vm.front_buffer
			)

	def closeEvent(self, event):
		if not self._confirm_editor_changes("close the IDE"):
			event.ignore()
			return
		if self.image_studio_view.document.modified and not self.image_studio_view._confirm_discard_changes():
			event.ignore()
			return
		if not self.image_studio_view.shutdown():
			QMessageBox.warning(
				self,
				"Image operation still running",
				"The current import or export is still finishing. Try closing again shortly.",
			)
			event.ignore()
			return
		if not self.converter_view.shutdown():
			QMessageBox.warning(
				self,
				"Export still running",
				"The current compatibility or export job is still finishing. Try closing again shortly.",
			)
			event.ignore()
			return
		if self.worker and self.worker.isRunning():
			self.cancel_program_input()
			self.runtime_context.cancel()
			self.worker.wait(2000)
			if self.worker.isRunning():
				QMessageBox.warning(
					self,
					"Program still running",
					"The current program did not stop cooperatively. The IDE will remain open.",
				)
				event.ignore()
				return
		self.audio_engine.shutdown()
		event.accept()

	def keyPressEvent(self, event: QKeyEvent):
		if event.key() == Qt.Key.Key_F2:
			self.rename_symbol()
		return super().keyPressEvent(event)


def main():
	app = QApplication(sys.argv)
	app.setFont(_host_monospace_font())

	ide = X26IDE()
	arguments = sys.argv[1:]
	auto_run = False
	requested_tab: str | None = None
	file_arguments: list[str] = []
	index = 0
	while index < len(arguments):
		argument = arguments[index]
		if argument == "--run":
			auto_run = True
		elif argument in ("--tab", "--tool") and index + 1 < len(arguments):
			index += 1
			requested_tab = arguments[index]
		elif argument.startswith("--tab=") or argument.startswith("--tool="):
			requested_tab = argument.split("=", 1)[1]
		else:
			file_arguments.append(argument)
		index += 1
	if file_arguments:
		path = Path(file_arguments[0])
		if path.is_file():
			ide.load_file(path)
	if requested_tab is not None:
		ide.select_workspace_tool(requested_tab)
	ide.show()
	if auto_run and ide.current_file is not None:
		QTimer.singleShot(0, ide.run_code)

	sys.exit(app.exec())


if __name__ == "__main__":
	main()
