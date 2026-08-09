import os
from pathlib import Path
import re
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QTextDocument
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

import ide as ide_module
from ide import (
	AnsiHtmlStream,
	VMGraphicsWidget,
	X26IDE,
	XPP26SyntaxHighlighter,
	_atomic_write_text,
	ansi_to_html,
)
from ide_themes import THEMES
from xe_lang.compiler_service import compile_source
from xe_lang.host_tools.converter import ConverterPane
from xe_lang.host_tools.file_picker import XeSourcePicker
from xe_lang.host_tools.help_content import HELP_TOPICS
from xe_lang.host_tools.help_view import _basic_markdown_to_html
from xe_lang.host_tools.render_helpers import ElidingPathLineEdit
from xe_lang.host_tools.services import (
	ConversionIssue,
	ConversionReport,
	ConversionRequest,
	UnavailableConverterService,
	load_default_converter_service,
)
from xe_lang.host_tools.ui_specs import CODE_TOOLBAR_ACTIONS, WORKBENCH_TABS, workbench_tab_index
from xe_lang.scratch_profile import load_bundled_profile


@pytest.fixture(scope="module")
def app():
	instance = QApplication.instance() or QApplication([])
	yield instance


class FakeExporter:
	def __init__(self, report: ConversionReport):
		self.report = report
		self.analyzed: list[ConversionRequest] = []
		self.exported: list[tuple[ConversionRequest, Path, bool]] = []

	def analyze(self, request: ConversionRequest) -> ConversionReport:
		self.analyzed.append(request)
		return self.report

	def export(self, request, output_path, *, allow_fallback=True):
		self.exported.append((request, output_path, allow_fallback))
		return self.report


def test_help_search_requires_every_term():
	graphics = next(topic for topic in HELP_TOPICS if topic.title == "Graphics and windows")
	assert graphics.matches("graphics window")
	assert graphics.matches("scratch 480")
	assert not graphics.matches("graphics currency")


def test_help_renderer_keeps_code_and_official_link():
	html = _basic_markdown_to_html("# Title\n\nUse `out`.\n\nhttps://example.invalid")
	assert "<h1>Title</h1>" in html
	assert "<code>out</code>" in html
	assert "example.invalid" in html
	assert "<br>" not in html
	assert _basic_markdown_to_html("First wrapped\nline.").count("<p>") == 1


def test_bundled_xe_help_examples_compile():
	for topic in HELP_TOPICS:
		for source in re.findall(r"```xe\n(.*?)```", topic.markdown, re.DOTALL):
			artifact = compile_source(source, topic.title.replace(" ", "_") + ".xe")
			assert artifact.success, (topic.title, artifact.diagnostics)


def test_conversion_issue_location():
	issue = ConversionIssue("bad-call", "Unsupported", path="main.xe", line=8, column=3)
	assert issue.display_location() == "main.xe:8:3"


def test_converter_analyze_renders_exact_report(app):
	report = ConversionReport(
		exact=True,
		blocked=False,
		summary="Compatible with the pinned profile.",
		artifact_hash="sha256:1234",
		details={"addresses": "1400", "syscalls": "4"},
	)
	service = FakeExporter(report)
	pane = ConverterPane(
		service=service,
		request_provider=lambda scope: ConversionRequest(
			scope=scope,
			source_text='out "ok"',
			source_path=Path("main.xe"),
		),
	)
	result = pane.analyze()
	assert result is report
	assert pane.status_chip.text() == "Exact"
	assert pane.status_chip.property("status") == "success"
	assert "sha256:1234" in pane.report_view.toPlainText()
	assert service.analyzed[0].scope == "active"
	assert not pane.fallback_checkbox.isChecked()
	assert pane.export_button.isEnabled()
	assert pane.export_button.text() == "Export SB3…"
	pane.close()


def test_converter_unavailable_is_blocked_without_writes(app):
	pane = ConverterPane(
		service=UnavailableConverterService("Exporter missing"),
		request_provider=lambda scope: ConversionRequest(scope="active", source_text="out 1"),
	)
	report = pane.analyze()
	assert report.blocked
	assert pane.status_chip.text() == "Blocked"
	assert "Exporter missing" in pane.report_view.toPlainText()
	assert not pane.export_button.isEnabled()
	pane.close()


def test_converter_requires_explicit_fallback_opt_in(app):
	report = ConversionReport(
		exact=False,
		blocked=True,
		summary="The selected profile cannot run this artifact exactly.",
		artifact_hash="sha256:blocked",
	)
	pane = ConverterPane(
		service=FakeExporter(report),
		request_provider=lambda scope: ConversionRequest(scope="active", source_text="out << 1"),
	)
	assert not pane.export_button.isEnabled()
	pane.analyze()
	assert not pane.export_button.isEnabled()
	pane.fallback_checkbox.setChecked(True)
	assert pane.export_button.isEnabled()
	assert pane.export_button.text() == "Export fallback…"
	pane.close()


def test_converter_can_analyze_a_chosen_xe_file(app, tmp_path):
	source = tmp_path / "chosen.xe"
	source.write_text("out << 73", encoding="utf-8")
	report = ConversionReport(
		exact=True,
		blocked=False,
		summary="Chosen source is compatible.",
		artifact_hash="sha256:chosen",
	)
	service = FakeExporter(report)
	pane = ConverterPane(
		service=service,
		request_provider=lambda scope: ConversionRequest(scope="active", source_text="wrong"),
		source_picker=lambda _parent: source,
	)
	pane.scope_combo.setCurrentIndex(pane.scope_combo.findData("file"))
	assert pane.choose_source_button.isVisibleTo(pane)
	pane.choose_source_file()
	assert pane.selected_source_path == source.resolve()
	result = pane.analyze()
	assert result is report
	assert service.analyzed[-1].source_text == "out << 73"
	assert service.analyzed[-1].source_path == source.resolve()
	assert service.analyzed[-1].scope == "active"
	pane.close()


def test_converter_bounds_selected_host_source_reads(app, tmp_path, monkeypatch):
	from xe_lang.host_tools import converter

	source = tmp_path / "too-large.xe"
	source.write_text("out << 123", encoding="utf-8")
	monkeypatch.setattr(converter, "MAX_CONVERTER_SOURCE_BYTES", 4)
	pane = ConverterPane(
		service=FakeExporter(ConversionReport(True, False, "unused")),
		source_picker=lambda _parent: source,
	)
	pane.scope_combo.setCurrentIndex(pane.scope_combo.findData("file"))
	pane.choose_source_file()
	report = pane.analyze()
	assert report.blocked
	assert "2 mib" in report.summary.lower()
	pane.close()


def test_virtual_drive_source_picker_filters_and_opens_xe(app, tmp_path):
	(tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")
	(tmp_path / "main.xe").write_text("out << 1", encoding="utf-8")
	(tmp_path / "nested").mkdir()
	(tmp_path / "nested" / "tool.xe").write_text("out << 2", encoding="utf-8")
	picker = XeSourcePicker(root=tmp_path)
	assert [picker.entries.item(index).text() for index in range(picker.entries.count())] == [
		"Folder  nested",
		"Xe file  main.xe",
	]
	folder = picker.entries.item(0)
	picker._activate_item(folder)
	assert picker.current_directory == "nested"
	assert picker.entries.count() == 1
	picker._activate_item(picker.entries.item(0))
	assert picker.selected_path == (tmp_path / "nested" / "tool.xe").resolve()
	picker.close()


def test_converter_fallback_picker_never_uses_an_sb3_suffix(app, monkeypatch, tmp_path):
	report = ConversionReport(
		exact=False,
		blocked=True,
		summary="Blocked by the selected profile.",
		artifact_hash="sha256:blocked",
	)
	service = FakeExporter(report)
	pane = ConverterPane(
		service=service,
		request_provider=lambda scope: ConversionRequest(
			scope="active",
			source_text="out << 1",
			source_path=Path("program.xe"),
		),
	)
	pane.analyze()
	pane.fallback_checkbox.setChecked(True)
	seen: dict[str, str] = {}

	def choose(_parent, title, default_name, file_filter):
		seen.update(title=title, default_name=default_name, file_filter=file_filter)
		return str(tmp_path / "portable-fallback"), ""

	monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
	pane.export()
	assert seen["title"] == "Export Xe fallback bundle"
	assert seen["default_name"].endswith(".xbn")
	assert "*.xbn" in seen["file_filter"]
	assert service.exported[-1][1] == tmp_path / "portable-fallback.xbn"
	assert service.exported[-1][2] is True
	pane.close()


def test_canonical_converter_analysis_is_side_effect_free(tmp_path):
	entry = tmp_path / "workspace.xe"
	entry.write_text("out << 42", encoding="utf-8")
	before = {path.name for path in tmp_path.iterdir()}
	service = load_default_converter_service()
	profile = load_bundled_profile()
	pane = ConverterPane(service=service)
	assert profile.name in pane.profile_field.text()
	assert f"{profile.address_limit:,}" in pane.profile_field.text()
	assert "scratch-200k" not in pane.profile_field.text().casefold()
	pane.close()
	report = service.analyze(
		ConversionRequest(
			scope="workspace",
			source_text="out << 42",
			source_path=entry,
			workspace_root=tmp_path,
		)
	)
	assert "Profile" in report.details
	assert not any(issue.code == "export-service-unavailable" for issue in report.issues)
	assert {path.name for path in tmp_path.iterdir()} == before


def test_host_ide_preserves_code_workspace_and_adds_tools(app, tmp_path):
	ide = X26IDE()
	ide.resize(1024, 640)
	ide.show()
	app.processEvents()
	assert ide.workspace_tabs.count() == 4
	assert [ide.workspace_tabs.tabText(index) for index in range(4)] == [
		"Code",
		"Xe → SB3",
		"Image Studio",
		"Help",
	]
	assert ide.editor is not None
	assert ide.output is not None
	assert ide.graphics_view is not None
	assert ide.run_button.text() == "Run"
	assert not ide.system_clipboard_toggle.isChecked()
	assert "not exported to Scratch" in ide.system_clipboard_toggle.toolTip()
	ide.editor.setPlainText("out 42")
	request = ide._converter_request("active")
	assert request.source_text == "out 42"
	assert request.scope == "active"
	ide.change_theme("Default Light")
	assert ide.current_theme == "Default Light"
	ide.open_help()
	app.processEvents()
	assert ide.workspace_tabs.currentWidget() is ide.help_view
	assert ide.help_view.search.hasFocus()
	assert ide.select_workspace_tool("image-studio")
	app.processEvents()
	assert ide.workspace_tabs.currentWidget() is ide.image_studio_view
	assert ide.workspace_tabs.tabBar().isVisible()
	assert ide.image_studio_view.isVisible()
	assert ide.image_studio_view.canvas.isVisible()
	assert ide.select_workspace_tool("converter")
	assert ide.workspace_tabs.currentWidget() is ide.converter_view
	assert not ide.select_workspace_tool("missing-tool")
	ide.toggle_graphics_view()
	assert ide.workspace_tabs.currentWidget() is ide.code_tab
	assert ide.graphics_maximized
	ide.toggle_graphics_view()
	assert not ide.graphics_maximized
	ide.close()


def test_host_workbench_metadata_preserves_labels_order_and_aliases():
	assert [spec.label for spec in WORKBENCH_TABS] == [
		"Code",
		"Xe → SB3",
		"Image Studio",
		"Help",
	]
	assert [spec.label for spec in CODE_TOOLBAR_ACTIONS] == ["New", "Open", "Save", "Save As"]
	assert workbench_tab_index("editor") == 0
	assert workbench_tab_index("Xe_to_SB3") == 1
	assert workbench_tab_index("image editor") == 2
	assert workbench_tab_index("docs") == 3
	assert workbench_tab_index("unknown") is None


def test_path_display_elides_without_losing_the_copyable_value(app):
	path = "C:/very/long/workspace/with/several/nested/folders/program.xe"
	display = ElidingPathLineEdit(path)
	display.resize(130, 28)
	display.show()
	app.processEvents()
	assert display.fullText() == path
	assert display.toolTip() == path
	assert display.text() != path
	assert "…" in display.text()
	display.close()


def test_every_theme_comment_colour_remains_readable():
	def luminance(value: str) -> float:
		colour = QColor(value)
		channels = (colour.redF(), colour.greenF(), colour.blueF())
		linear = tuple(
			channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
			for channel in channels
		)
		return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

	for theme in THEMES.values():
		foreground = luminance(theme["comment"])
		background = luminance(theme["background"])
		contrast = (max(foreground, background) + 0.05) / (min(foreground, background) + 0.05)
		assert contrast >= 4.5


def test_host_clipboard_toggle_disables_reads_and_writes(app):
	ide = X26IDE()
	app.clipboard().setText("before")
	ide.system_clipboard_toggle.setChecked(False)
	ready = threading.Event()
	holder: dict[str, str] = {}
	ide.read_system_clipboard(ready, holder)
	assert ready.is_set()
	assert holder == {"value": ""}
	write_ready = threading.Event()
	written: dict[str, bool] = {}
	ide.write_system_clipboard("blocked", write_ready, written)
	assert write_ready.is_set()
	assert written == {"written": False}
	assert app.clipboard().text() == "before"
	ide.system_clipboard_toggle.setChecked(True)
	ide.write_system_clipboard("allowed", threading.Event(), written)
	assert app.clipboard().text() == "allowed"
	app.clipboard().clear()
	ide.close()


def test_ansi_reset_and_stream_output_are_well_formed_and_bounded(app):
	html = ansi_to_html("\x1b[31mred\x1b[0mplain")
	assert "</span style=" not in html
	assert html.endswith("</span>plain")
	stream = AnsiHtmlStream()
	assert stream.feed("\x1b[") == ""
	assert "#ff3333" in stream.feed("31mred")
	assert "#ff3333" in stream.feed(" stays red")
	assert stream.feed("\x1b[0mplain") == "plain"
	ide = X26IDE()
	ide.append_output("\x1b[32mok\x1b[0m\n")
	assert ide.output.toPlainText() == "ok\n"
	ide.append_output("\n".join(str(index) for index in range(6_000)))
	assert ide.output.document().blockCount() <= 5_000
	ide.close()


def test_hash_inside_string_keeps_string_highlighting(app):
	document = QTextDocument()
	document.setPlainText('out << "a#b"')
	highlighter = XPP26SyntaxHighlighter(document, THEMES["Default Dark"])
	highlighter.rehighlight()
	formats = document.firstBlock().layout().formats()
	for position in range(7, 12):
		matching = [item for item in formats if item.start <= position < item.start + item.length]
		assert matching
		assert matching[-1].format.foreground().color().name() == THEMES["Default Dark"]["string"]


def test_host_editor_long_documents_scroll_both_axes(app):
	ide = X26IDE()
	ide.resize(720, 520)
	ide.show()
	ide.editor.setPlainText("\n".join(f"line {index} " + "x" * 220 for index in range(2_000)))
	app.processEvents()
	assert ide.editor.verticalScrollBar().maximum() > 0
	assert ide.editor.horizontalScrollBar().maximum() > 0
	ide.close()


def test_dirty_new_file_can_cancel_or_discard(app, monkeypatch):
	ide = X26IDE()
	ide.editor.insertPlainText("unsaved")
	monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel)
	assert not ide.new_file()
	assert ide.editor.toPlainText() == "unsaved"
	monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard)
	assert ide.new_file()
	assert ide.editor.toPlainText() == ""
	ide.close()


def test_atomic_utf8_save_preserves_existing_file_on_replace_failure(tmp_path, monkeypatch):
	path = tmp_path / "source.xe"
	path.write_text("old", encoding="utf-8")
	monkeypatch.setattr(ide_module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace failed")))
	with pytest.raises(OSError, match="replace failed"):
		_atomic_write_text(path, "new π")
	assert path.read_text(encoding="utf-8") == "old"
	assert not tuple(tmp_path.glob("*.tmp"))


def test_converter_button_analysis_runs_in_background(app):
	report = ConversionReport(True, False, "Exact", artifact_hash="hash")
	pane = ConverterPane(
		service=FakeExporter(report),
		request_provider=lambda scope: ConversionRequest(scope="active", source_text="out << 1"),
	)
	pane.analyze_async()
	assert pane._busy
	for _ in range(100):
		app.processEvents()
		if not pane._busy:
			break
		time.sleep(0.005)
	assert not pane._busy
	assert pane.last_report is report
	pane.close()


def test_converter_discards_an_async_report_after_source_invalidation(app):
	started = threading.Event()
	release = threading.Event()

	class SlowExporter(FakeExporter):
		def analyze(self, request):
			started.set()
			release.wait(2)
			return super().analyze(request)

	report = ConversionReport(True, False, "Exact", artifact_hash="hash")
	pane = ConverterPane(
		service=SlowExporter(report),
		request_provider=lambda scope: ConversionRequest(scope="active", source_text="out << 1"),
	)
	pane.analyze_async()
	assert started.wait(1)
	pane.invalidate()
	release.set()
	for _ in range(100):
		app.processEvents()
		if not pane._busy:
			break
		time.sleep(0.005)
	assert not pane._busy
	assert pane.last_report is None
	assert pane.status_chip.text() == "Not analyzed"
	pane.close()


def test_converter_async_snapshot_provider_failure_is_contained(app):
	def broken_provider(_scope):
		raise OSError("source disappeared")

	pane = ConverterPane(
		service=FakeExporter(ConversionReport.unavailable("unused")),
		request_provider=broken_provider,
	)
	pane.analyze_async()
	assert not pane._busy
	assert pane.last_report is not None
	assert pane.last_report.blocked
	assert "failed safely" in pane.last_report.summary.lower()
	pane.export_async()
	assert not pane._busy
	assert pane.last_report is not None
	assert pane.last_report.blocked
	pane.close()


def test_graphics_letterbox_does_not_forward_pointer_clicks(app):
	class Input:
		def __init__(self):
			self.buttons = []

		def move_pointer(self, x, y):
			self.position = (x, y)

		def set_button(self, button, down):
			self.buttons.append((button, down))

	class Devices:
		def __init__(self):
			self.input = Input()

	class VM:
		def __init__(self):
			self.devices = Devices()

	widget = VMGraphicsWidget()
	widget.resize(900, 360)
	widget._fit_stage()
	vm = VM()
	widget.active_vm = vm
	outside = QPointF(10, 180)
	event = QMouseEvent(
		QMouseEvent.Type.MouseButtonPress,
		outside,
		outside,
		Qt.MouseButton.LeftButton,
		Qt.MouseButton.LeftButton,
		Qt.KeyboardModifier.NoModifier,
	)
	widget.mousePressEvent(event)
	assert vm.devices.input.buttons == []
	widget.close()
