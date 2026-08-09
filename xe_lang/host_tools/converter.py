"""Xe-to-Scratch export surface for the desktop IDE."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
	QCheckBox,
	QComboBox,
	QFileDialog,
	QFrame,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QMessageBox,
	QPushButton,
	QTextBrowser,
	QVBoxLayout,
	QWidget,
)

from .services import (
	ConversionReport,
	ConversionRequest,
	XeSb3ExportService,
	load_default_converter_service,
)
from .file_picker import select_xe_source


RequestProvider = Callable[[str], ConversionRequest]
SourcePicker = Callable[[QWidget], Path | None]


class ConverterPane(QWidget):
	"""A conservative export UI: analyze first, write only on explicit export."""

	def __init__(
		self,
		service: XeSb3ExportService | None = None,
		request_provider: RequestProvider | None = None,
		source_picker: SourcePicker | None = None,
		parent: QWidget | None = None,
	):
		super().__init__(parent)
		self.service = service or load_default_converter_service()
		self.request_provider = request_provider or self._empty_request
		self.source_picker = source_picker or select_xe_source
		self.selected_source_path: Path | None = None
		self.last_report: ConversionReport | None = None
		self._busy = False
		self._build_ui()

	@staticmethod
	def _empty_request(scope: str) -> ConversionRequest:
		return ConversionRequest(scope="workspace" if scope == "workspace" else "active", source_text="")

	def _build_ui(self) -> None:
		root = QVBoxLayout(self)
		root.setContentsMargins(22, 18, 22, 20)
		root.setSpacing(14)

		header = QHBoxLayout()
		titles = QVBoxLayout()
		title = QLabel("Xe → Scratch Project")
		title.setObjectName("ToolTitle")
		title.setFont(QFont(self.font().family(), 16, QFont.Weight.DemiBold))
		subtitle = QLabel(
			"Verify the selected Xe program against the pinned Scratch VM profile before exporting."
		)
		subtitle.setObjectName("MutedText")
		subtitle.setWordWrap(True)
		titles.addWidget(title)
		titles.addWidget(subtitle)
		header.addLayout(titles, 1)
		self.status_chip = QLabel("Not analyzed")
		self.status_chip.setObjectName("StatusChip")
		self.status_chip.setProperty("status", "idle")
		self.status_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
		header.addWidget(self.status_chip, 0, Qt.AlignmentFlag.AlignTop)
		root.addLayout(header)

		settings = QFrame()
		settings.setObjectName("ToolCard")
		settings_layout = QVBoxLayout(settings)
		settings_layout.setContentsMargins(16, 14, 16, 14)
		settings_layout.setSpacing(10)

		source_row = QHBoxLayout()
		source_label = QLabel("Convert")
		source_label.setMinimumWidth(80)
		self.scope_combo = QComboBox()
		self.scope_combo.addItem("Active editor", "active")
		self.scope_combo.addItem("Whole workspace", "workspace")
		self.scope_combo.addItem("Choose .xe file", "file")
		self.scope_combo.setAccessibleName("Conversion scope")
		self.scope_combo.currentIndexChanged.connect(self._scope_changed)
		source_row.addWidget(source_label)
		source_row.addWidget(self.scope_combo, 1)
		self.choose_source_button = QPushButton("Choose file…")
		self.choose_source_button.setObjectName("SecondaryButton")
		self.choose_source_button.setToolTip("Open the Xenon virtual-drive file picker")
		self.choose_source_button.clicked.connect(self.choose_source_file)
		self.choose_source_button.hide()
		source_row.addWidget(self.choose_source_button)
		settings_layout.addLayout(source_row)
		self.source_path_label = QLabel("No .xe file selected")
		self.source_path_label.setObjectName("MutedText")
		self.source_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
		self.source_path_label.hide()
		settings_layout.addWidget(self.source_path_label)

		profile_row = QHBoxLayout()
		profile_label = QLabel("Profile")
		profile_label.setMinimumWidth(80)
		self.profile_field = QLineEdit(self._service_profile_label())
		self.profile_field.setReadOnly(True)
		self.profile_field.setToolTip(self.profile_field.text())
		self.profile_field.setAccessibleDescription(
			"The exact bundled Scratch VM profile used for compatibility checks and exports"
		)
		profile_row.addWidget(profile_label)
		profile_row.addWidget(self.profile_field, 1)
		settings_layout.addLayout(profile_row)

		self.fallback_checkbox = QCheckBox(
			"Allow non-SB3 fallback (.xbn + .compatibility.json)"
		)
		self.fallback_checkbox.setChecked(False)
		self.fallback_checkbox.setToolTip(
			"Fallbacks are labeled explicitly and never presented as exact Scratch conversions."
		)
		self.fallback_checkbox.toggled.connect(self._refresh_export_action)
		settings_layout.addWidget(self.fallback_checkbox)
		fallback_notice = QLabel(
			"Fallback bundles preserve compiled Xe and the compatibility findings. "
			"They are not Scratch projects and cannot be opened as an .sb3."
		)
		fallback_notice.setObjectName("WarningText")
		fallback_notice.setWordWrap(True)
		settings_layout.addWidget(fallback_notice)
		root.addWidget(settings)

		action_row = QHBoxLayout()
		self.analyze_button = QPushButton("Check compatibility")
		self.analyze_button.setObjectName("SecondaryButton")
		self.analyze_button.clicked.connect(self.analyze)
		self.export_button = QPushButton("Export…")
		self.export_button.setObjectName("PrimaryButton")
		self.export_button.setEnabled(False)
		self.export_button.clicked.connect(self.export)
		action_row.addWidget(self.analyze_button)
		action_row.addStretch(1)
		action_row.addWidget(self.export_button)
		root.addLayout(action_row)

		report_label = QLabel("Compatibility report")
		report_label.setObjectName("SectionTitle")
		root.addWidget(report_label)
		self.report_view = QTextBrowser()
		self.report_view.setObjectName("ReportView")
		self.report_view.setOpenExternalLinks(False)
		self.report_view.setPlaceholderText(
			"Run a compatibility check to see syscalls, assets, memory limits, and exact-export blockers."
		)
		root.addWidget(self.report_view, 1)

	def set_service(self, service: XeSb3ExportService) -> None:
		self.service = service
		self.profile_field.setText(self._service_profile_label())
		self.profile_field.setToolTip(self.profile_field.text())
		self.invalidate()

	def _service_profile_label(self) -> str:
		provider = getattr(self.service, "profile_label", None)
		if callable(provider):
			try:
				return str(provider())
			except Exception:
				pass
		return "Service-provided profile"

	def set_request_provider(self, provider: RequestProvider) -> None:
		self.request_provider = provider
		self.invalidate()

	def _scope(self) -> str:
		return str(self.scope_combo.currentData())

	def _scope_changed(self) -> None:
		is_file = self._scope() == "file"
		self.choose_source_button.setVisible(is_file)
		self.source_path_label.setVisible(is_file)
		self.invalidate()

	def choose_source_file(self) -> None:
		path = self.source_picker(self)
		if path is None:
			return
		candidate = Path(path).resolve()
		if not candidate.is_file() or candidate.suffix.casefold() != ".xe":
			self.selected_source_path = None
			self.source_path_label.setText("Select an existing .xe source file")
			self.invalidate()
			return
		self.selected_source_path = candidate
		self.source_path_label.setText(str(candidate))
		self.source_path_label.setToolTip(str(candidate))
		self.invalidate()

	def _request(self) -> ConversionRequest:
		if self._scope() == "file":
			path = self.selected_source_path
			if path is None:
				return ConversionRequest(scope="active", source_text="")
			try:
				source = path.read_text(encoding="utf-8")
			except (OSError, UnicodeError):
				source = ""
			return ConversionRequest(
				scope="active",
				source_text=source,
				source_path=path,
				workspace_root=path.parent,
				profile=self.profile_field.text(),
			)
		request = self.request_provider(self._scope())
		if request.profile != self.profile_field.text():
			request = ConversionRequest(
				scope=request.scope,
				source_text=request.source_text,
				source_path=request.source_path,
				workspace_root=request.workspace_root,
				profile=self.profile_field.text(),
			)
		return request

	def invalidate(self) -> None:
		self.last_report = None
		self._set_status("Not analyzed", "idle")
		if hasattr(self, "report_view"):
			self.report_view.clear()
		self._refresh_export_action()

	def _set_busy(self, busy: bool) -> None:
		self._busy = busy
		self.analyze_button.setEnabled(not busy)
		self._refresh_export_action()
		if busy:
			self._set_status("Working…", "idle")

	def _refresh_export_action(self) -> None:
		if not hasattr(self, "export_button"):
			return
		report = self.last_report
		exact = bool(report and report.exact and not report.blocked)
		fallback = bool(
			report
			and report.blocked
			and report.artifact_hash
			and self.fallback_checkbox.isChecked()
		)
		if exact:
			self.export_button.setText("Export SB3…")
		elif fallback:
			self.export_button.setText("Export fallback…")
		else:
			self.export_button.setText("Export…")
		self.export_button.setEnabled(not self._busy and (exact or fallback))

	def _set_status(self, text: str, status: str) -> None:
		self.status_chip.setText(text)
		self.status_chip.setProperty("status", status)
		self.status_chip.style().unpolish(self.status_chip)
		self.status_chip.style().polish(self.status_chip)

	def analyze(self) -> ConversionReport:
		self._set_busy(True)
		try:
			request = self._request()
			if not request.source_text.strip():
				report = ConversionReport.unavailable("There is no Xe source to analyze.")
			else:
				report = self.service.analyze(request)
		except Exception as exc:
			report = ConversionReport.unavailable(f"Compatibility check failed safely: {exc}")
		finally:
			self._set_busy(False)
		self._show_report(report)
		return report

	def export(self) -> ConversionReport | None:
		request = self._request()
		if not request.source_text.strip():
			report = ConversionReport.unavailable("There is no Xe source to export.")
			self._show_report(report)
			return report

		# Recheck the immutable source snapshot immediately before choosing a file
		# type. An editor change after the previous analysis must never turn an XBN
		# fallback picker into a misleading .sb3, or vice versa.
		self._set_busy(True)
		try:
			preflight = self.service.analyze(request)
		except Exception as exc:
			preflight = ConversionReport.unavailable(f"Compatibility check failed safely: {exc}")
		finally:
			self._set_busy(False)
		self._show_report(preflight)
		exact = preflight.exact and not preflight.blocked
		fallback = (
			preflight.blocked
			and bool(preflight.artifact_hash)
			and self.fallback_checkbox.isChecked()
		)
		if not exact and not fallback:
			return preflight

		stem = request.source_path.stem if request.source_path else "xenon-project"
		default_name = stem + (".sb3" if exact else ".xbn")
		dialog_title = "Export Scratch project" if exact else "Export Xe fallback bundle"
		dialog_filter = "Scratch Project (*.sb3);;All files (*)" if exact else "Xe fallback bundle (*.xbn);;All files (*)"
		output, _ = QFileDialog.getSaveFileName(
			self,
			dialog_title,
			default_name,
			dialog_filter,
		)
		if not output:
			return None

		path = Path(output)
		expected_suffix = ".sb3" if exact else ".xbn"
		if path.suffix.lower() != expected_suffix:
			path = path.with_suffix(expected_suffix)
		self._set_busy(True)
		try:
			report = self.service.export(
				request,
				path,
				allow_fallback=self.fallback_checkbox.isChecked(),
			)
		except Exception as exc:
			report = ConversionReport.unavailable(f"Export failed safely: {exc}")
		finally:
			self._set_busy(False)
		self._show_report(report)
		if report.output_path or report.fallback_path:
			written = report.output_path or report.fallback_path
			title = "Scratch export complete" if report.output_path else "Fallback bundle written"
			note = "" if report.output_path else "\n\nThis is not an SB3 Scratch project."
			QMessageBox.information(self, title, f"Written to:\n{written}{note}")
		return report

	def _show_report(self, report: ConversionReport) -> None:
		self.last_report = report
		if report.exact and not report.blocked:
			self._set_status("Exact", "success")
		elif report.blocked:
			self._set_status("Blocked", "error")
		else:
			self._set_status("Fallback", "warning")
		self._refresh_export_action()

		lines = [report.summary]
		if report.artifact_hash:
			lines.extend(("", f"Artifact: {report.artifact_hash}"))
		if report.output_path:
			lines.append(f"SB3: {report.output_path}")
		if report.fallback_path:
			lines.append(f"Fallback: {report.fallback_path}")
		if report.details:
			lines.append("")
			for key, value in sorted(report.details.items()):
				lines.append(f"{key}: {value}")
		if report.issues:
			lines.extend(("", "Findings"))
			for issue in report.issues:
				location = issue.display_location()
				where = f" ({location})" if location else ""
				lines.append(
					f"[{issue.severity.upper()}] {issue.code}{where}: {issue.message}"
				)
		self.report_view.setPlainText("\n".join(lines))
