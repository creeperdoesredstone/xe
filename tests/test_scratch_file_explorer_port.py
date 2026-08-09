from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scratch_vm.build_file_explorer_port import (
	BANK_COUNT,
	BANK_WORDS,
	DEFAULT_OUTPUT,
	FIXED_ZIP_TIME,
	LOGICAL_WORDS,
	SOURCE_PATH,
	STANDBY_BANKS,
	WORKING_BANKS,
	build_bytes,
	logical_location,
	normalize_allocation_size,
	normalize_word,
	source_hash,
	validate_project,
)
from xe_lang.compiler_service import compile_source
from xe_lang.sb3_exporter import analyze_compatibility
from xe_lang.scratch_profile import load_bundled_profile


def _project() -> dict[str, object]:
	with zipfile.ZipFile(DEFAULT_OUTPUT) as archive:
		value = json.loads(archive.read("project.json"))
	assert isinstance(value, dict)
	return value


def _stage(project: dict[str, object]) -> dict[str, object]:
	return next(target for target in project["targets"] if target["name"] == "Stage")


def _named_values(container: dict[str, list[object]]) -> dict[str, list[object]]:
	return {value[0]: value[1] for value in container.values()}


def test_native_file_explorer_artifact_is_a_deterministic_valid_sb3() -> None:
	first = build_bytes()
	second = build_bytes()
	assert first == second == DEFAULT_OUTPUT.read_bytes()
	assert hashlib.sha256(first).hexdigest() == "28418c85522d91182f3cb511d0c5317718c85cd44b2892d04ccb62c2d8216c20"
	with zipfile.ZipFile(DEFAULT_OUTPUT) as archive:
		assert archive.testzip() is None
		assert archive.namelist() == sorted(archive.namelist())
		assert all(info.date_time == FIXED_ZIP_TIME for info in archive.infolist())
		assert all("/" not in info.filename and "\\" not in info.filename for info in archive.infolist())
		project = json.loads(archive.read("project.json"))
	validate_project(project)


def test_memory_schema_has_ten_independent_scratch_banks() -> None:
	project = _project()
	stage = _stage(project)
	lists = _named_values(stage["lists"])
	bank_names = [f"MEM_DATA_{index}" for index in range(BANK_COUNT)]
	assert [name for name in bank_names if name in lists] == bank_names
	assert all(len(lists[name]) == BANK_WORDS for name in bank_names)
	assert sum(len(lists[name]) for name in bank_names) == LOGICAL_WORDS
	assert all(set(lists[name]) == {0} for name in bank_names)
	assert WORKING_BANKS == (0, 1, 2, 3, 4)
	assert STANDBY_BANKS == (5, 6, 7, 8, 9)
	assert logical_location(0) == (0, 1)
	assert logical_location(199_999) == (0, 200_000)
	assert logical_location(200_000) == (1, 1)
	assert logical_location(999_999) == (4, 200_000)
	assert logical_location(1_000_000) == (5, 1)
	assert logical_location(LOGICAL_WORDS - 1) == (9, 200_000)
	for invalid in (-1, LOGICAL_WORDS, True, 1.5):
		with pytest.raises(ValueError):
			logical_location(invalid)  # type: ignore[arg-type]

	blocks = stage["blocks"].values()
	procedure_codes = {
		block.get("mutation", {}).get("proccode")
		for block in blocks
		if block.get("opcode") == "procedures_prototype"
	}
	assert procedure_codes >= {"memory map %n", "memory read %n", "memory write %n %n", "memory allocate %n"}
	list_writes = [
		block["fields"]["LIST"][0]
		for block in stage["blocks"].values()
		if block.get("opcode") == "data_replaceitemoflist"
	]
	assert sorted(name for name in list_writes if name.startswith("MEM_DATA_")) == bank_names
	list_appends = [
		block["fields"]["LIST"][0]
		for block in stage["blocks"].values()
		if block.get("opcode") == "data_addtolist"
	]
	assert not any(name.startswith("MEM_DATA_") for name in list_appends)


def test_memory_words_are_uint32_and_allocations_are_integral() -> None:
	assert normalize_word(0) == 0
	assert normalize_word(-1) == 0xFFFFFFFF
	assert normalize_word(-(1 << 32) - 7) == 0xFFFFFFF9
	assert normalize_word((1 << 32) + 9) == 9
	assert normalize_word(4.9) == 4
	for invalid in (True, "1", float("nan"), float("inf")):
		with pytest.raises(ValueError):
			normalize_word(invalid)  # type: ignore[arg-type]

	assert normalize_allocation_size(1) == 1
	assert normalize_allocation_size(200_000.0) == 200_000
	for invalid in (0, -1, 1.5, True, float("nan"), float("inf")):
		with pytest.raises(ValueError):
			normalize_allocation_size(invalid)

	source = SOURCE_PATH.read_text(encoding="utf-8")
	assert "memValue = floor(value) % 4294967296" in source
	assert "words > 0 and words == floor(words)" in source
	assert "repeatuntil MEM_DATA_" not in source


def test_native_port_contains_list_backed_vfs_and_interactive_atom_ux() -> None:
	project = _project()
	stage = _stage(project)
	lists = _named_values(stage["lists"])
	lengths = {len(lists[name]) for name in ("VFS_IDS", "VFS_NAMES", "VFS_TYPES", "VFS_PARENTS", "VFS_PATHS")}
	assert lengths == {7}
	assert {"file", "folder"} == set(lists["VFS_TYPES"])
	assert project["extensions"] == []
	targets = {target["name"]: target for target in project["targets"]}
	assert set(targets) >= {"Stage", "Window", "Nucleus", "Item", "Controller", "Back", "New item", "Trash"}
	item_opcodes = {block["opcode"] for block in targets["Item"]["blocks"].values()}
	assert item_opcodes >= {
		"control_create_clone_of",
		"control_start_as_clone",
		"event_whenthisspriteclicked",
		"motion_gotoxy",
		"sensing_touchingobject",
	}
	all_opcodes = {block["opcode"] for target in targets.values() for block in target["blocks"].values()}
	assert "sensing_askandwait" in all_opcodes
	assert "data_deleteoflist" in all_opcodes
	assert "event_broadcastandwait" in all_opcodes


def test_metadata_truthfully_marks_direct_port_and_source_snapshot() -> None:
	project = _project()
	meta = project["meta"]["xeNativePort"]
	assert meta["kind"] == "native-scratch-reference"
	assert meta["exactXeBytecode"] is False
	assert meta["memoryBanks"] == 10
	assert meta["memoryBankWords"] == 200_000
	assert SOURCE_PATH.is_file()
	assert meta["sourceSha256"] == source_hash()


def test_xe_file_explorer_remains_blocked_by_the_legacy_exact_exporter() -> None:
	source_path = Path("apps/file_explorer.xe")
	artifact = compile_source(source_path.read_text(encoding="utf-8"), source_path.as_posix())
	assert artifact.success
	report = analyze_compatibility(artifact, load_bundled_profile())
	assert report.exact is False
	unsupported = {issue.syscall for issue in report.issues if issue.code == "unsupported-syscall"}
	assert unsupported == set(artifact.required_syscalls) - set(load_bundled_profile().supported_syscalls)
	assert unsupported
