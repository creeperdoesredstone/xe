from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

import xe_lang.sb3_exporter as sb3_exporter

from xe_lang.compiler_service import compile_source
from xe_lang.memory import MAX_ADDRESS_COUNT
from xe_lang.sb3_exporter import (
	SB3ExportError,
	analyze_compatibility,
	export_fallback,
	export_sb3,
)
from xe_lang.scratch_profile import (
	ScratchVMProfile,
	bundled_template_path,
	legacy_template_path,
	load_bundled_profile,
	load_legacy_profile,
)


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
		MAX_ADDRESS_COUNT,
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


def test_legacy_profile_is_pinned_and_never_claims_banked_memory_parity() -> None:
	profile = load_legacy_profile()
	assert profile.verify_template(legacy_template_path())
	report = analyze_compatibility(compile_source("out << 17"), profile)
	assert not report.exact
	assert any(issue.code == "memory-model-mismatch" for issue in report.issues)


def test_bundled_full_abi_profile_exports_current_core_program(tmp_path: Path) -> None:
	profile = load_bundled_profile()
	template = bundled_template_path()
	assert profile.verify_template(template)
	assert profile.address_limit == MAX_ADDRESS_COUNT == 2_000_000
	assert profile.memory_bank_words == 200_000
	assert profile.memory_bank_lists == tuple(f"MEM_DATA_{index}" for index in range(10))
	assert profile.distribution == "local-load"
	artifact = compile_source('out << "scratch"', "workspace.xe")
	report = analyze_compatibility(artifact, profile)
	assert report.exact, report.issues
	output = tmp_path / "current.sb3"
	export_sb3(artifact, output, template, profile)
	with zipfile.ZipFile(output) as archive:
		project = json.loads(archive.read("project.json"))
		stage = next(target for target in project["targets"] if target["name"] == "Stage")
		programs = [value for value in stage["lists"].values() if value[0] == "MEM_PROGRAM"]
		assert len(programs) == 1
		assert programs[0][1] == [f"0x{word:09X}" for word in artifact.program[4:]]


def test_full_profile_explorer_fallback_allowances_are_hash_bound() -> None:
	profile = load_bundled_profile()
	explorer = compile_source(
		Path("apps/file_explorer.xe").read_text(encoding="utf-8"),
		"apps/file_explorer.xe",
	)
	assert explorer.success
	assert {248, 276}.isdisjoint(profile.supported_syscalls)
	assert {248, 276} <= profile.supported_for_artifact(explorer.artifact_hash)
	assert profile.supported_for_artifact("0" * 64) == profile.supported_syscalls
	assert analyze_compatibility(explorer, profile).exact
	generic = compile_source(
		"proc main() { var pressed: bool\npressed = graphics::right_mouse_pressed() }",
		"generic-right-click.xe",
	)
	assert generic.success
	report = analyze_compatibility(generic, profile)
	assert not report.exact
	assert [issue.syscall for issue in report.issues if issue.code == "unsupported-syscall"] == [248]


def test_full_profile_accepts_the_bundled_draggable_window_example() -> None:
	profile = load_bundled_profile()
	source_path = Path("examples/small_draggable_window.xe")
	artifact = compile_source(source_path.read_text(encoding="utf-8"), str(source_path))
	assert artifact.success
	assert set(artifact.required_syscalls) == {21, 102, 103, 104, 109, 110}
	assert analyze_compatibility(artifact, profile).exact


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
		MAX_ADDRESS_COUNT,
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
