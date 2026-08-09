from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import zipfile

import pytest

from scratch_vm.audit import MANIFEST_PATH, ROOT, audit_template, generate_manifest, manifest_text
from xe_lang.compiler_service import MAX_ADDRESS_COUNT, capability_for_syscall, compile_source
from xe_lang.scratch_profile import bundled_template_path, load_bundled_profile
from xe_lang.sb3_exporter import (
	FIXED_ZIP_TIME,
	SB3ExportError,
	_load_template,
	_write_zip,
	analyze_compatibility,
	export_sb3,
)
from xe_lang.syscall_abi import SyscallID


def _enum_ids(prefix: str) -> set[int]:
	return {int(value) for value in SyscallID if value.name.startswith(prefix)}


def test_pinned_template_profile_is_truthful_and_legacy_bounded() -> None:
	profile = load_bundled_profile()
	audit = audit_template(bundled_template_path(), profile)

	assert profile.verify_template(bundled_template_path())
	assert audit["sha256"] == profile.template_sha256
	assert audit["archive_members_unique"] is True
	assert audit["archive_paths_safe"] is True
	assert audit["mem_program_list_count"] == 1
	assert audit["mem_data_list_count"] == 1
	assert audit["mem_data_initializer_words"] == [65_536]
	assert audit["sys_dispatch_matches_profile"] is True
	assert set(audit["sys_dispatch"]) == set(profile.supported_syscalls)
	assert len(profile.supported_syscalls) == 48
	assert profile.address_limit == profile.static_limit == 65_536
	assert MAX_ADDRESS_COUNT == 200_000
	assert profile.address_limit != MAX_ADDRESS_COUNT
	assert {capability_for_syscall(value) for value in profile.supported_syscalls} == set(profile.capabilities)


def test_current_abi_gaps_are_explicit_and_named() -> None:
	profile = load_bundled_profile()
	missing = {int(value) for value in SyscallID} - set(profile.supported_syscalls)
	app_calls = _enum_ids("APP_")
	image_calls = {int(value) for value in SyscallID if "GRAPHICS_" in value.name and 270 <= int(value) <= 276}
	audio_calls = _enum_ids("APP_AUDIO_")
	compiler_calls = _enum_ids("APP_COMPILER_")

	assert app_calls
	assert app_calls <= missing
	assert image_calls == set(range(270, 277))
	assert image_calls <= missing
	assert audio_calls == set(range(280, 290))
	assert audio_calls <= missing
	assert compiler_calls
	assert compiler_calls <= missing


def test_checked_manifest_covers_every_app_without_silent_degradation() -> None:
	checked = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
	generated = generate_manifest(ROOT)
	assert checked == generated
	assert MANIFEST_PATH.read_text(encoding="utf-8") == manifest_text(ROOT)

	app_names = sorted(path.name for path in (ROOT / "apps").glob("*.xe"))
	assert sorted(checked["apps"]) == app_names
	assert checked["compatibility"]["exact_apps"] == []
	assert checked["compatibility"]["blocked_apps"] == app_names
	for app_name, record in checked["apps"].items():
		assert record["compile_success"] is True, (app_name, record["diagnostics"])
		assert record["status"] == "blocked"
		assert record["exact"] is False
		assert record["issues"]
		codes = {item["code"] for item in record["issues"]}
		assert "memory-model-mismatch" in codes
		unsupported = {
			item["syscall"]["id"]: item["syscall"]["name"]
			for item in record["issues"]
			if item["code"] == "unsupported-syscall"
		}
		required = {item["id"] for item in record["required_syscalls"]}
		expected = required - set(load_bundled_profile().supported_syscalls)
		assert set(unsupported) == expected
		assert all(name and not name.startswith("SYS_") for name in unsupported.values())

	music_unsupported = {
		item["syscall"]["id"]
		for item in checked["apps"]["xenon_music.xe"]["issues"]
		if item["code"] == "unsupported-syscall"
	}
	ide_unsupported = {
		item["syscall"]["id"]
		for item in checked["apps"]["xenon_ide.xe"]["issues"]
		if item["code"] == "unsupported-syscall"
	}
	assert music_unsupported & _enum_ids("APP_AUDIO_")
	assert ide_unsupported & _enum_ids("APP_COMPILER_")
	assert {item["code"] for item in checked["apps"]["xenon_music.xe"]["issues"]} >= {
		"dynamic-asset-reference",
	}


def test_bundled_export_is_blocked_and_packaging_primitive_is_deterministic(tmp_path: Path) -> None:
	profile = load_bundled_profile()
	artifact = compile_source('out << "scratch"', "workspace.xe")
	blocked = analyze_compatibility(artifact, profile)
	assert blocked.exact is False
	assert {item.code for item in blocked.issues} == {"memory-model-mismatch"}
	blocked_output = tmp_path / "blocked.sb3"
	with pytest.raises(SB3ExportError, match="not exact"):
		export_sb3(artifact, blocked_output, bundled_template_path(), profile)
	assert not blocked_output.exists()

	project, assets = _load_template(bundled_template_path(), profile, False)
	first = tmp_path / "first.sb3"
	second = tmp_path / "second.sb3"
	_write_zip(first, project, assets)
	_write_zip(second, project, assets)
	assert first.read_bytes() == second.read_bytes()

	with zipfile.ZipFile(first, "r") as archive:
		infos = archive.infolist()
		names = [item.filename for item in infos]
		assert names == sorted(names)
		assert len(names) == len(set(names))
		assert all(item.date_time == FIXED_ZIP_TIME for item in infos)
		assert all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts for name in names)
		project_payload = archive.read("project.json")
		project = json.loads(project_payload)
		assert project_payload == json.dumps(
			project,
			ensure_ascii=False,
			sort_keys=True,
			separators=(",", ":"),
		).encode("utf-8")
