import sys
import re
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


PALETTE = [
    "#000000",
    "#0000AA",
    "#00AA00",
    "#00AAAA",
    "#AA0000",
    "#AA00AA",
    "#AA5500",
    "#AAAAAA",
    "#555555",
    "#5555FF",
    "#55FF55",
    "#55FFFF",
    "#FF5555",
    "#FF55FF",
    "#FFFF55",
    "#FFFFFF",
]


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
        for token in tokens:
            if token._type == TT.EOF:
                continue
            start_col = token.start_pos.col
            end_col = token.end_pos.col + 1
            if start_col <= len(text):
                for i in range(max(0, start_col), min(len(text), end_col)):
                    char_to_token[i] = token._type

        current_pos = 0
        while current_pos < len(text):
            token_type = char_to_token.get(current_pos, TT.IDENT)
            end_pos = current_pos + 1
            while (
                end_pos < len(text)
                and char_to_token.get(end_pos, TT.IDENT) == token_type
            ):
                end_pos += 1

            if token_type == TT.KEYWORD:
                self.setFormat(current_pos, end_pos - current_pos, self.keyword_format)
            elif token_type == TT.TYPE:
                self.setFormat(current_pos, end_pos - current_pos, self.type_format)
            elif token_type in (TT.INT, TT.FLOAT):
                self.setFormat(current_pos, end_pos - current_pos, self.number_format)
            elif token_type in (TT.CHAR, TT.STRING):
                self.setFormat(current_pos, end_pos - current_pos, self.string_format)
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
            ):
                self.setFormat(current_pos, end_pos - current_pos, self.operator_format)
            elif token_type == TT.BOOL:
                self.setFormat(current_pos, end_pos - current_pos, self.bool_format)
            else:
                self.setFormat(current_pos, end_pos - current_pos, self.ident_format)
            current_pos = end_pos

        for match in re.finditer(r"(#.*)", text):
            start, end = match.span()
            self.setFormat(start, end - start, self.comment_format)


class VMGraphicsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.width_px = 240
        self.height_px = 180
        self.scale = 2
        self.setFixedSize(self.width_px * self.scale, self.height_px * self.scale)

        self.image = QImage(self.width_px, self.height_px, QImage.Format.Format_RGB32)
        self.image.fill(QColor("black"))
        self.active_vm = None

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def update_frame(self, front_buffer: list):
        for y in range(self.height_px):
            for x in range(self.width_px):
                color_hex = PALETTE[front_buffer[y][x] % 16]
                self.image.setPixelColor(x, y, QColor(color_hex))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        scaled_pixmap = QPixmap.fromImage(self.image).scaled(
            self.size(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        painter.drawPixmap(0, 0, scaled_pixmap)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.active_vm:
            self.active_vm.mouse_x = max(
                0, min(self.width_px - 1, int(event.position().x() // self.scale))
            )
            self.active_vm.mouse_y = max(
                0, min(self.height_px - 1, int(event.position().y() // self.scale))
            )

    def mousePressEvent(self, event: QMouseEvent):
        if self.active_vm:
            if event.button() == Qt.MouseButton.LeftButton:
                self.active_vm.mouse_btn = 1
            elif event.button() == Qt.MouseButton.RightButton:
                self.active_vm.mouse_btn = 2

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.active_vm:
            self.active_vm.mouse_btn = 0

    def keyPressEvent(self, event: QKeyEvent):
        if self.active_vm and not event.isAutoRepeat():
            code = event.nativeScanCode()
            self.active_vm.keys_down.add(code)
            self.active_vm.key_queue.append((1, code, 0))

    def keyReleaseEvent(self, event: QKeyEvent):
        if self.active_vm and not event.isAutoRepeat():
            code = event.nativeScanCode()
            self.active_vm.keys_down.discard(code)
            self.active_vm.key_queue.append((2, code, 0))


class VMWorkerThread(QThread):
    execution_finished = pyqtSignal(object, object, str)
    frame_ready = pyqtSignal(list)
    output_ready = pyqtSignal(str)

    def __init__(self, filename: str, code: str, context: RuntimeContext):
        super().__init__()
        self.filename = filename
        self.code = code
        self.context = context

    def run(self):
        try:
            def output_handler(text: str):
                self.output_ready.emit(text)

            self.context.output_handler = output_handler

            result, error, asm = run(self.filename, self.code, self.context)

            if hasattr(self.context, "vm") and self.context.vm:

                def signal_refresh_bridge():
                    self.frame_ready.emit(self.context.vm.front_buffer)

                self.context.vm.render_front_buffer = signal_refresh_bridge

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

        self.setWindowTitle("X++26 IDE")
        self.setGeometry(100, 100, 950, 650)

        self.current_file: Optional[Path] = None
        self.runtime_context = RuntimeContext()
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
            self.current_file = Path(path)
            self.editor.setPlainText(self.current_file.read_text())
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

    def run_code(self):
        code = self.editor.toPlainText()
        if not code.strip():
            return

        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()

        self.output.setHtml("")
        self.runtime_context = RuntimeContext()
        filename = str(self.current_file) if self.current_file else "<editor>"

        self.worker = VMWorkerThread(filename, code, self.runtime_context)
        self.worker.frame_ready.connect(self.graphics_view.update_frame)
        self.worker.output_ready.connect(self.append_output)
        self.worker.execution_finished.connect(self.handle_execution_finished)

        self.graphics_view.active_vm = None
        self.worker.start()
        self.refresh_timer.start(33)

    def poll_vm_buffer(self):
        if hasattr(self.runtime_context, "vm") and self.runtime_context.vm:
            if not self.graphics_view.active_vm:
                self.graphics_view.active_vm = self.runtime_context.vm
                
                self.runtime_context.vm.render_front_buffer = (
                    lambda: self.graphics_view.update_frame(
                        self.runtime_context.vm.front_buffer
                    )
                )

            if hasattr(self.runtime_context.vm, "front_buffer"):
                self.graphics_view.update_frame(self.runtime_context.vm.front_buffer)

    @pyqtSlot(object, object, str)
    def handle_execution_finished(self, result, error, assembly):
        self.refresh_timer.stop()
        if error:
            self.append_output(ansi_to_html(f"{error}"))
        else:
            self.append_output(f"Execution finished successfully.\n\nStack: {result[:32]}")

        if hasattr(self.runtime_context, "vm") and self.runtime_context.vm:
            self.graphics_view.update_frame(self.runtime_context.vm.front_buffer)


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
    ide.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
