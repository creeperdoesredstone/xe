from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

import xe_lang.sb3_exporter as sb3_exporter

from xe_lang.compiler_service import compile_source
from xe_lang.sb3_exporter import (
	SB3ExportError,
	analyze_compatibility,
	export_fallback,
	export_sb3,
)
from xe_lang.scratch_profile import ScratchVMProfile, bundled_template_path, load_bundled_profile


def _template(path: Path) -> str:
	project = {
		"targets": [{
			"isStage": True,
			"name": "Stage",
			"lists": {
				"program-id": ["MEM_PROGRAM", ["old"]],
				"data-id": ["MEM_DATA", []],
			},
		}],
		"monitors": [],
		"extensions": [],
		"meta": {"semver": "3.0.0"},
	}
	with zipfile.ZipFile(path, "w") as archive:
		archive.writestr("project.json", json.dumps(project))
		archive.writestr("asset.svg", b"<svg/>")
	return hashlib.sha256(path.read_bytes()).hexdigest()


def _profile(template_hash: str, syscalls: tuple[int, ...]) -> ScratchVMProfile:
	return ScratchVMProfile(
		"test-profile",
		"1",
		template_hash,
		frozenset(syscalls),
		200_000,
		65_536,
		frozenset(("core.io", "core.os")),
	)


def test_sb3_export_is_deterministic_and_injects_xbn_payload(tmp_path: Path) -> None:
	artifact = compile_source("out << 17")
	template = tmp_path / "template.sb3"
	profile = _profile(_template(template), artifact.required_syscalls)
	first = tmp_path / "first.sb3"
	second = tmp_path / "second.sb3"
	export_sb3(artifact, first, template, profile)
	export_sb3(artifact, second, template, profile)
	assert first.read_bytes() == second.read_bytes()
	with zipfile.ZipFile(first) as archive:
		project = json.loads(archive.read("project.json"))
		program = project["targets"][0]["lists"]["program-id"][1]
		assert program == [f"0x{word:09X}" for word in artifact.program[4:]]
		assert project["meta"]["xeExporter"]["artifactHash"] == artifact.artifact_hash


def test_export_blocks_memory_or_syscall_mismatch_and_writes_fallback(tmp_path: Path) -> None:
	artifact = compile_source("out << 17")
	profile = ScratchVMProfile("legacy", "1", "0" * 64, frozenset(), 65_536, 65_536, frozenset())
	report = analyze_compatibility(artifact, profile)
	assert not report.exact
	assert {issue.code for issue in report.issues} >= {"memory-model-mismatch", "unsupported-syscall"}
	with pytest.raises(SB3ExportError, match="not exact"):
		export_sb3(artifact, tmp_path / "bad.sb3", tmp_path / "missing.sb3", profile)
	xbn, compatibility = export_fallback(artifact, report, tmp_path / "program")
	assert xbn.exists()
	assert json.loads(compatibility.read_text(encoding="utf-8"))["exact"] is False


def test_export_rejects_template_hash_mismatch(tmp_path: Path) -> None:
	artifact = compile_source("out << 17")
	template = tmp_path / "template.sb3"
	_template(template)
	profile = _profile("0" * 64, artifact.required_syscalls)
	with pytest.raises(SB3ExportError, match="hash mismatch"):
		export_sb3(artifact, tmp_path / "bad.sb3", template, profile)


def test_bundled_legacy_profile_is_pinned_and_never_claims_200k_parity() -> None:
	profile = load_bundled_profile()
	assert profile.verify_template(bundled_template_path())
	report = analyze_compatibility(compile_source("out << 17"), profile)
	assert not report.exact
	assert any(issue.code == "memory-model-mismatch" for issue in report.issues)


def test_exact_gate_rejects_unavailable_or_dynamic_assets(tmp_path: Path) -> None:
	template = tmp_path / "template.sb3"
	digest = _template(template)
	static_asset = compile_source('var image: graphics::Image\nimage = graphics::load_image("icon.ximg")')
	profile = _profile(digest, static_asset.required_syscalls)
	report = analyze_compatibility(static_asset, profile)
	assert any(issue.code == "asset-rom-unavailable" for issue in report.issues)

	dynamic_asset = compile_source('var image: graphics::Image\nvar name: string\nname = "icon.ximg"\nimage = graphics::load_image(name)')
	profile_with_rom = ScratchVMProfile(
		"asset-profile",
		"1",
		digest,
		frozenset(dynamic_asset.required_syscalls),
		200_000,
		65_536,
		frozenset(("asset-rom",)),
	)
	report = analyze_compatibility(dynamic_asset, profile_with_rom)
	assert any(issue.code == "dynamic-asset-reference" for issue in report.issues)


def test_fallback_pair_restores_previous_generation_if_second_replace_fails(tmp_path: Path, monkeypatch) -> None:
	artifact = compile_source("out << 17")
	profile = ScratchVMProfile("legacy", "1", "0" * 64, frozenset(), 65_536, 65_536, frozenset())
	report = analyze_compatibility(artifact, profile)
	xbn = tmp_path / "program.xbn"
	compatibility = tmp_path / "program.compatibility.json"
	xbn.write_text("old-xbn", encoding="utf-8")
	compatibility.write_text("old-report", encoding="utf-8")
	real_replace = sb3_exporter.os.replace
	failed = False

	def fail_second_replacement(source, destination):
		nonlocal failed
		if Path(destination) == compatibility and str(source).endswith(".tmp") and not failed:
			failed = True
			raise OSError("simulated second replacement failure")
		return real_replace(source, destination)

	monkeypatch.setattr(sb3_exporter.os, "replace", fail_second_replacement)
	with pytest.raises(OSError, match="second replacement"):
		export_fallback(artifact, report, tmp_path / "program", overwrite=True)
	assert xbn.read_text(encoding="utf-8") == "old-xbn"
	assert compatibility.read_text(encoding="utf-8") == "old-report"
	assert not tuple(tmp_path.glob("*.tmp"))
	assert not tuple(tmp_path.glob("*.bak"))
