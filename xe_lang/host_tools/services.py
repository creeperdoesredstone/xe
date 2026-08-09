"""Small service contracts used by host IDE tools.

The widgets intentionally depend on these protocols instead of importing compiler
or packager internals.  That makes the UI testable and lets the canonical compiler
service be replaced without coupling it to PyQt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable


ConversionScope = Literal["active", "workspace"]


@dataclass(frozen=True, slots=True)
class ConversionIssue:
	code: str
	message: str
	severity: Literal["error", "warning", "info"] = "error"
	path: str = ""
	line: int | None = None
	column: int | None = None

	def display_location(self) -> str:
		parts: list[str] = []
		if self.path:
			parts.append(self.path)
		if self.line is not None:
			position = str(self.line)
			if self.column is not None:
				position += f":{self.column}"
			parts.append(position)
		return ":".join(parts)


@dataclass(frozen=True, slots=True)
class ConversionRequest:
	scope: ConversionScope
	source_text: str
	source_path: Path | None = None
	workspace_root: Path | None = None
	profile: str = "bundled-profile"


@dataclass(frozen=True, slots=True)
class ConversionReport:
	exact: bool
	blocked: bool
	summary: str
	issues: tuple[ConversionIssue, ...] = ()
	output_path: Path | None = None
	fallback_path: Path | None = None
	artifact_hash: str = ""
	details: dict[str, str] = field(default_factory=dict)

	@classmethod
	def unavailable(cls, message: str) -> "ConversionReport":
		return cls(
			exact=False,
			blocked=True,
			summary=message,
			issues=(
				ConversionIssue(
					code="export-service-unavailable",
					message=message,
				),
			),
		)


@runtime_checkable
class XeSb3ExportService(Protocol):
	"""Canonical, side-effect-free analysis plus explicit export boundary."""

	def analyze(self, request: ConversionRequest) -> ConversionReport:
		"""Check whether *request* is exactly portable without writing files."""

	def export(
		self,
		request: ConversionRequest,
		output_path: Path,
		*,
		allow_fallback: bool = False,
	) -> ConversionReport:
		"""Export an atomic SB3 or a staged, rollback-safe fallback pair."""


class UnavailableConverterService:
	def __init__(self, reason: str | None = None):
		self.reason = reason or (
			"The Xe-to-SB3 export service is not available in this build. "
			"Source editing and VM execution are still available."
		)

	def analyze(self, request: ConversionRequest) -> ConversionReport:
		return ConversionReport.unavailable(self.reason)

	def export(
		self,
		request: ConversionRequest,
		output_path: Path,
		*,
		allow_fallback: bool = False,
	) -> ConversionReport:
		return ConversionReport.unavailable(self.reason)


class CanonicalExporterAdapter:
	"""Adapt the compiler/exporter modules to the host's narrow UI contract."""

	def profile_label(self) -> str:
		from xe_lang.scratch_profile import load_bundled_profile
		profile = load_bundled_profile()
		return (
			f"{profile.name} · {profile.version} · "
			f"{profile.address_limit:,} addresses"
		)

	def _compile(self, request: ConversionRequest):
		from xe_lang.compiler_service import compile_source, compile_workspace

		if request.scope == "workspace" and request.workspace_root is not None:
			root = request.workspace_root.resolve()
			sources: dict[str, str] = {}
			for path in sorted(root.rglob("*.xe"), key=lambda item: item.as_posix().casefold()):
				resolved = path.resolve()
				if not resolved.is_relative_to(root):
					continue
				try:
					relative = path.relative_to(root).as_posix()
				except ValueError:
					continue
				if request.source_path is not None and resolved == request.source_path.resolve():
					sources[relative] = request.source_text
				else:
					sources[relative] = path.read_text(encoding="utf-8")
			entry = "workspace.xe"
			if not sources:
				sources[entry] = request.source_text
			elif entry not in sources and request.source_path is not None:
				try:
					entry = request.source_path.resolve().relative_to(root).as_posix()
				except ValueError:
					entry = request.source_path.name
			return compile_workspace(sources, entry_path=entry)
		filename = request.source_path.name if request.source_path else "workspace.xe"
		return compile_source(request.source_text, filename)

	@staticmethod
	def _report(artifact, compatibility, *, output_path=None, fallback_path=None) -> ConversionReport:
		issues: list[ConversionIssue] = [
			ConversionIssue(
				code=diagnostic.code or "compile-error",
				message=diagnostic.message,
				severity="error" if diagnostic.severity == "error" else "warning",
				path=diagnostic.path,
				line=diagnostic.line,
				column=diagnostic.column,
			)
			for diagnostic in artifact.diagnostics
		]
		issues.extend(
			ConversionIssue(
				code=issue.code,
				message=issue.message,
				severity="error" if issue.severity == "error" else "warning",
			)
			for issue in compatibility.issues
			if issue.code != "compile-failed" or not artifact.diagnostics
		)
		exact = bool(compatibility.exact and artifact.success)
		fallback_written = fallback_path is not None
		if output_path is not None:
			summary = "Exact Scratch project exported with the pinned VM profile."
		elif fallback_written:
			summary = "Exact export is unavailable; an explicitly labeled compatibility bundle was written."
		elif exact:
			summary = "This program is exactly compatible with the pinned Scratch VM profile."
		else:
			summary = "Exact Scratch export is blocked. Review the findings below."
		return ConversionReport(
			exact=exact,
			blocked=not exact and not fallback_written,
			summary=summary,
			issues=tuple(issues),
			output_path=output_path,
			fallback_path=fallback_path,
			artifact_hash=artifact.artifact_hash,
			details={
				"Profile": f"{compatibility.profile_name} {compatibility.profile_version}",
				"XVM contract address limit": f"{artifact.memory.address_limit:,}",
				"Static words": f"{artifact.memory.static_words:,}",
				"Required syscalls": ", ".join(str(value) for value in artifact.required_syscalls) or "None",
			},
		)

	def analyze(self, request: ConversionRequest) -> ConversionReport:
		from xe_lang.sb3_exporter import analyze_compatibility
		from xe_lang.scratch_profile import load_bundled_profile

		artifact = self._compile(request)
		compatibility = analyze_compatibility(artifact, load_bundled_profile())
		return self._report(artifact, compatibility)

	def export(
		self,
		request: ConversionRequest,
		output_path: Path,
		*,
		allow_fallback: bool = False,
	) -> ConversionReport:
		from xe_lang.sb3_exporter import (
			analyze_compatibility,
			export_fallback,
			export_sb3,
		)
		from xe_lang.scratch_profile import (
			bundled_template_path,
			load_bundled_profile,
		)

		artifact = self._compile(request)
		profile = load_bundled_profile()
		compatibility = analyze_compatibility(artifact, profile)
		if compatibility.exact:
			export_sb3(
				artifact,
				output_path,
				bundled_template_path(),
				profile,
				overwrite=True,
			)
			return self._report(artifact, compatibility, output_path=output_path)
		if allow_fallback and artifact.success:
			xbn_path, report_path = export_fallback(
				artifact,
				compatibility,
				output_path,
				overwrite=True,
			)
			result = self._report(artifact, compatibility, fallback_path=xbn_path)
			details = dict(result.details)
			details["Compatibility report"] = str(report_path)
			return ConversionReport(
				exact=result.exact,
				blocked=result.blocked,
				summary=result.summary,
				issues=result.issues,
				output_path=result.output_path,
				fallback_path=result.fallback_path,
				artifact_hash=result.artifact_hash,
				details=details,
			)
		return self._report(artifact, compatibility)

def load_default_converter_service() -> XeSb3ExportService:
	"""Load the optional canonical exporter without making it an IDE dependency."""

	try:
		import xe_lang.compiler_service
		import xe_lang.sb3_exporter
		import xe_lang.scratch_profile
		return CanonicalExporterAdapter()
	except (ImportError, AttributeError, RuntimeError) as exc:
		return UnavailableConverterService(str(exc) or None)
