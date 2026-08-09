from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

from xe_lang.archive_safety import ArchiveSafetyError, load_safe_zip_members, normalize_archive_member

from xe_lang.compiler_service import CompileArtifact, syscall_name
from xe_lang.memory import MAX_ADDRESS_COUNT
from xe_lang.scratch_profile import ScratchVMProfile


FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
XVM_ADDRESS_LIMIT = MAX_ADDRESS_COUNT


@dataclass(frozen=True)
class CompatibilityIssue:
	severity: str
	code: str
	message: str
	syscall: int | None = None


@dataclass(frozen=True)
class CompatibilityReport:
	exact: bool
	issues: tuple[CompatibilityIssue, ...]
	artifact_hash: str
	profile_name: str
	profile_version: str

	def to_dict(self) -> dict[str, object]:
		return {
			"artifact_hash": self.artifact_hash,
			"exact": self.exact,
			"issues": [
				{
					"code": item.code,
					"message": item.message,
					"severity": item.severity,
					**({"syscall": item.syscall, "syscall_name": syscall_name(item.syscall)} if item.syscall is not None else {}),
				}
				for item in self.issues
			],
			"profile": {"name": self.profile_name, "version": self.profile_version},
		}


class SB3ExportError(RuntimeError):
	pass


def analyze_compatibility(artifact: CompileArtifact, profile: ScratchVMProfile) -> CompatibilityReport:
	issues: list[CompatibilityIssue] = []
	if not artifact.success:
		issues.append(CompatibilityIssue("error", "compile-failed", "Xe source must compile before Scratch export"))
	if profile.address_limit != XVM_ADDRESS_LIMIT:
		issues.append(CompatibilityIssue(
			"error",
			"memory-model-mismatch",
			f"Scratch profile exposes {profile.address_limit} addresses, but the XVM contract requires {XVM_ADDRESS_LIMIT}",
		))
	if artifact.memory.static_words > profile.static_limit:
		issues.append(CompatibilityIssue(
			"error",
			"static-memory-overflow",
			f"Program requires {artifact.memory.static_words} static words; profile limit is {profile.static_limit}",
		))
	for dynamic_asset in artifact.dynamic_assets:
		issues.append(CompatibilityIssue(
			"error",
			"dynamic-asset-reference",
			f"Scratch export requires a literal portable asset path: {dynamic_asset}",
		))
	if artifact.assets and "asset-rom" not in profile.capabilities:
		issues.append(CompatibilityIssue(
			"error",
			"asset-rom-unavailable",
			f"Program references {len(artifact.assets)} asset(s), but this Scratch profile has no deterministic asset ROM",
		))
	for syscall in artifact.required_syscalls:
		if syscall not in profile.supported_syscalls:
			issues.append(CompatibilityIssue(
				"error",
				"unsupported-syscall",
				f"{syscall_name(syscall)} ({syscall}) is not implemented by this Scratch VM profile",
				syscall,
			))
	return CompatibilityReport(
		not any(issue.severity == "error" for issue in issues),
		tuple(issues),
		artifact.artifact_hash,
		profile.name,
		profile.version,
	)


def _safe_member_name(name: str) -> str:
	try:
		return normalize_archive_member(name)
	except ArchiveSafetyError as error:
		raise SB3ExportError(str(error)) from error


def _load_template(path: Path, profile: ScratchVMProfile, allow_unpinned: bool) -> tuple[dict[str, object], dict[str, bytes]]:
	if not path.is_file():
		raise SB3ExportError(f"Scratch VM template not found: {path}")
	try:
		payload = path.read_bytes()
		members = load_safe_zip_members(payload)
	except (OSError, ArchiveSafetyError) as error:
		raise SB3ExportError(f"Invalid Scratch VM template: {error}") from error
	digest = hashlib.sha256(payload).hexdigest()
	if not allow_unpinned and digest != profile.template_sha256:
		raise SB3ExportError(f"Scratch VM template hash mismatch: expected {profile.template_sha256}, got {digest}")
	assets: dict[str, bytes] = {}
	for name, data in members:
		if name == "project.json":
			try:
				project = json.loads(data)
			except (UnicodeDecodeError, json.JSONDecodeError) as error:
				raise SB3ExportError(f"Invalid project.json: {error}") from error
			if not isinstance(project, dict):
				raise SB3ExportError("Template project.json must contain an object")
		else:
			assets[name] = data
	if "project" not in locals():
		raise SB3ExportError("Scratch VM template has no project.json")
	return project, assets


def _find_unique_list(project: dict[str, object], target_name: str, list_name: str) -> list[object]:
	matches: list[list[object]] = []
	for target in project.get("targets", []):
		if not isinstance(target, dict) or target.get("name") != target_name:
			continue
		for value in target.get("lists", {}).values():
			if isinstance(value, list) and len(value) == 2 and value[0] == list_name and isinstance(value[1], list):
				matches.append(value)
	if len(matches) != 1:
		raise SB3ExportError(f"Template must contain exactly one {target_name}/{list_name} list; found {len(matches)}")
	return matches[0]


def _canonical_project_bytes(project: dict[str, object]) -> bytes:
	return json.dumps(project, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_zip(path: Path, project: dict[str, object], assets: dict[str, bytes]) -> None:
	members = dict(assets)
	members["project.json"] = _canonical_project_bytes(project)
	with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
		for name in sorted(members):
			info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
			info.compress_type = zipfile.ZIP_DEFLATED
			info.create_system = 0
			info.external_attr = 0
			archive.writestr(info, members[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def export_sb3(
	artifact: CompileArtifact,
	output_path: str | Path,
	template_path: str | Path,
	profile: ScratchVMProfile,
	*,
	allow_unpinned_template: bool = False,
	overwrite: bool = False,
) -> CompatibilityReport:
	report = analyze_compatibility(artifact, profile)
	if not report.exact:
		raise SB3ExportError("Program is not exact for the selected Scratch VM profile; export XBN + compatibility report instead")
	output = Path(output_path)
	if output.exists() and not overwrite:
		raise SB3ExportError(f"Output already exists: {output}")
	project, assets = _load_template(Path(template_path), profile, allow_unpinned_template)
	program_list = _find_unique_list(project, profile.mem_program_target, profile.mem_program_list)
	program_list[1] = [f"0x{word:09X}" for word in artifact.program[4:]]
	meta = project.setdefault("meta", {})
	if not isinstance(meta, dict):
		raise SB3ExportError("Template project meta field must be an object")
	meta["xeExporter"] = {
		"artifactHash": artifact.artifact_hash,
		"profile": profile.name,
		"profileVersion": profile.version,
		"sourceHash": artifact.source_hash,
	}
	output.parent.mkdir(parents=True, exist_ok=True)
	fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
	os.close(fd)
	temporary = Path(temporary_name)
	try:
		_write_zip(temporary, project, assets)
		if output.exists() and not overwrite:
			raise SB3ExportError(f"Output already exists: {output}")
		os.replace(temporary, output)
	finally:
		if temporary.exists():
			temporary.unlink()
	return report


def export_fallback(
	artifact: CompileArtifact,
	report: CompatibilityReport,
	output_stem: str | Path,
	*,
	overwrite: bool = False,
) -> tuple[Path, Path]:
	stem = Path(output_stem)
	if stem.suffix.lower() in {".xbn", ".json", ".sb3"}:
		stem = stem.with_suffix("")
	xbn_path = stem.with_suffix(".xbn")
	report_path = stem.with_suffix(".compatibility.json")
	for path in (xbn_path, report_path):
		if path.exists() and not overwrite:
			raise SB3ExportError(f"Output already exists: {path}")
	xbn_payload = "\n".join(f"0x{word:09X}" for word in artifact.program) + ("\n" if artifact.program else "")
	report_payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
	xbn_path.parent.mkdir(parents=True, exist_ok=True)
	targets = ((xbn_path, xbn_payload), (report_path, report_payload))
	temporaries: dict[Path, Path] = {}
	backups: dict[Path, Path] = {}
	replaced: list[Path] = []
	committed = False
	try:
		# Stage and close both payloads before either public path changes.
		for path, payload in targets:
			fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
			os.close(fd)
			temporary = Path(temporary_name)
			temporaries[path] = temporary
			temporary.write_text(payload, encoding="utf-8", newline="\n")
		if not overwrite:
			for path, _ in targets:
				if path.exists():
					raise SB3ExportError(f"Output already exists: {path}")
		else:
			# Move prior outputs aside so a failed second replacement can restore the
			# complete previous pair instead of leaving mixed generations.
			for path, _ in targets:
				if not path.exists():
					continue
				fd, backup_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".bak", dir=path.parent)
				os.close(fd)
				backup = Path(backup_name)
				backup.unlink()
				os.replace(path, backup)
				backups[path] = backup
		for path, _ in targets:
			os.replace(temporaries[path], path)
			replaced.append(path)
		committed = True
	except Exception as error:
		rollback_failures: list[str] = []
		for path in reversed(replaced):
			try:
				path.unlink(missing_ok=True)
			except OSError as rollback_error:
				rollback_failures.append(f"remove {path}: {rollback_error}")
		for path, backup in backups.items():
			if backup.exists():
				try:
					os.replace(backup, path)
				except OSError as rollback_error:
					# Preserve a backup that cannot be restored automatically; never
					# delete the only recoverable prior generation in finally.
					rollback_failures.append(f"restore {backup} to {path}: {rollback_error}")
		if rollback_failures:
			raise SB3ExportError(
				"Fallback export failed and automatic rollback was incomplete; "
				+ "; ".join(rollback_failures)
			) from error
		raise
	finally:
		for temporary in temporaries.values():
			if temporary.exists():
				temporary.unlink()
		if committed:
			for backup in backups.values():
				if backup.exists():
					backup.unlink()
	return xbn_path, report_path
