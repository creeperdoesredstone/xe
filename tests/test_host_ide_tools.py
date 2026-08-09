import os
from pathlib import Path
import re
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog

from ide import X26IDE
from xe_lang.compiler_service import compile_source
from xe_lang.host_tools.converter import ConverterPane
from xe_lang.host_tools.file_picker import XeSourcePicker
from xe_lang.host_tools.help_content import HELP_TOPICS
from xe_lang.host_tools.help_view import _basic_markdown_to_html
from xe_lang.host_tools.services import (
	ConversionIssue,
	ConversionReport,
	ConversionRequest,
	UnavailableConverterService,
	load_default_converter_service,
)
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
	assert ide.system_clipboard_toggle.isChecked()
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
