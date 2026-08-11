from __future__ import annotations

from io import BytesIO
import hashlib
import json
import zipfile

import pytest

from scratch_vm.build_full_abi_vm import (
	BANK_COUNT,
	BANK_WORDS,
	EXPLORER_OUTPUT,
	LOGICAL_WORDS,
	OPTIONAL_FALLBACK_SYSCALLS,
	PROFILE_OUTPUT,
	PROVEN_EXACT_PROFILE_BASE,
	SOURCE_SHA256,
	SOURCE_TEMPLATE,
	VM_OUTPUT,
	_profile_bytes,
	_dispatch_values,
	build_bytes,
	contracts,
	logical_location,
)
from xe_lang.syscall_abi import SyscallID


def _archive(payload: bytes) -> tuple[dict[str, object], set[str]]:
	with zipfile.ZipFile(BytesIO(payload)) as archive:
		return json.loads(archive.read("project.json")), set(archive.namelist())


def _target(project: dict[str, object], name: str) -> dict[str, object]:
	return next(target for target in project["targets"] if target["name"] == name)


def _input_ids(value: object) -> tuple[str, ...]:
	if not isinstance(value, list):
		return ()
	return tuple(item for item in value[1:3] if isinstance(item, str))


def _validate_graph(target: dict[str, object]) -> None:
	blocks = target.get("blocks", {})
	for block_id, block in blocks.items():
		assert isinstance(block, dict), block_id
		for key in ("next", "parent"):
			reference = block.get(key)
			assert reference is None or reference in blocks, (block_id, key, reference)
		for value in block.get("inputs", {}).values():
			for reference in _input_ids(value):
				assert reference in blocks, (block_id, reference)


def _procedure_body(target: dict[str, object], proccode: str) -> set[str]:
	blocks = target["blocks"]
	prototypes = {
		block_id for block_id, block in blocks.items()
		if block.get("opcode") == "procedures_prototype"
		and block.get("mutation", {}).get("proccode") == proccode
	}
	definition = next(
		block for block in blocks.values()
		if block.get("opcode") == "procedures_definition"
		and block.get("inputs", {}).get("custom_block", [None, None])[1] in prototypes
	)
	seen: set[str] = set()
	stack = [definition.get("next")]
	while stack:
		block_id = stack.pop()
		if not isinstance(block_id, str) or block_id in seen:
			continue
		seen.add(block_id)
		block = blocks[block_id]
		stack.append(block.get("next"))
		for value in block.get("inputs", {}).values():
			stack.extend(_input_ids(value))
	return seen


def _reachable_blocks(target: dict[str, object], starts: tuple[str, ...]) -> set[str]:
	blocks = target["blocks"]
	seen: set[str] = set()
	stack = list(starts)
	while stack:
		block_id = stack.pop()
		if block_id in seen:
			continue
		seen.add(block_id)
		block = blocks[block_id]
		next_id = block.get("next")
		if isinstance(next_id, str):
			stack.append(next_id)
		for value in block.get("inputs", {}).values():
			stack.extend(_input_ids(value))
	return seen


def _syscall_body(target: dict[str, object], syscall: int) -> set[str]:
	blocks = target["blocks"]
	candidates: list[set[str]] = []
	for block in blocks.values():
		if block.get("opcode") != "control_if":
			continue
		condition_ids = _input_ids(block.get("inputs", {}).get("CONDITION"))
		if len(condition_ids) != 1:
			continue
		condition = blocks[condition_ids[0]]
		if condition.get("opcode") != "operator_equals":
			continue
		operands = condition.get("inputs", {})
		matches = False
		for reporter_name, literal_name in (("OPERAND1", "OPERAND2"), ("OPERAND2", "OPERAND1")):
			reporter_ids = _input_ids(operands.get(reporter_name))
			literal = operands.get(literal_name)
			if len(reporter_ids) != 1 or not isinstance(literal, list) or len(literal) < 2:
				continue
			reporter = blocks[reporter_ids[0]]
			if (
				reporter.get("opcode") == "argument_reporter_string_number"
				and reporter.get("fields", {}).get("VALUE", [None])[0] == "id"
				and literal[1] == [4, str(syscall)]
			):
				matches = True
		if not matches:
			continue
		starts = _input_ids(block.get("inputs", {}).get("SUBSTACK"))
		if starts:
			candidates.append(_reachable_blocks(target, starts))
	assert candidates, syscall
	return set().union(*candidates)


def _field_values(
	target: dict[str, object], body: set[str], field: str,
) -> set[str]:
	values: set[str] = set()
	for block_id in body:
		value = target["blocks"][block_id].get("fields", {}).get(field, [None])[0]
		if isinstance(value, str):
			values.add(value)
	return values


@pytest.fixture(scope="module")
def full_project() -> dict[str, object]:
	project, _ = _archive(build_bytes(with_explorer=True))
	return project


def test_source_template_is_the_untouched_attachment() -> None:
	assert hashlib.sha256(SOURCE_TEMPLATE.read_bytes()).hexdigest() == SOURCE_SHA256


@pytest.mark.parametrize("with_explorer", [False, True])
def test_full_abi_project_is_deterministic_and_structurally_complete(with_explorer: bool) -> None:
	payload = build_bytes(with_explorer=with_explorer)
	assert payload == build_bytes(with_explorer=with_explorer)
	project, members = _archive(payload)
	names = [target["name"] for target in project["targets"]]
	assert names == ["Stage", "Xenon-131 VM"]

	vm = _target(project, "Xenon-131 VM")
	assert _dispatch_values(vm) == {int(value) for value in SyscallID}
	assert len(vm["costumes"]) == 258
	assert [costume["name"] for costume in vm["costumes"][:3]] == ["0_00", "0_01", "0_02"]
	assert vm["currentCostume"] == 112
	assert vm["visible"] is False
	assert vm["x"] == -120
	assert vm["size"] == 36000

	stage = _target(project, "Stage")
	lists = {value[0]: value[1] for value in stage["lists"].values()}
	assert {name for name in lists if name.startswith("MEM_DATA_")} == {
		f"MEM_DATA_{index}" for index in range(BANK_COUNT)
	}
	assert all(len(lists[f"MEM_DATA_{index}"]) == BANK_WORDS for index in range(BANK_COUNT))
	assert sum(len(lists[f"MEM_DATA_{index}"]) for index in range(BANK_COUNT)) == LOGICAL_WORDS
	assert all(monitor.get("params", {}).get("LIST") != "MEM_DATA" for monitor in project.get("monitors", []))
	visible_monitors = [monitor for monitor in project.get("monitors", []) if monitor.get("visible")]
	assert [monitor.get("params", {}).get("VARIABLE") for monitor in visible_monitors] == [
		"ABI_SCROLL_AXIS_LATCH"
	]

	metadata = project["meta"]["xeFullAbi"]
	assert metadata["runtimeTargets"] == names
	assert metadata["mergedTargets"] == ["Xenon Graphics Engine", "Graphics Engine"]
	assert (metadata["fileExplorer"] != "none") is with_explorer
	assert metadata["syscallIds"] == sorted({int(value) for value in SyscallID})
	assert metadata["memoryBanks"] == BANK_COUNT
	assert metadata["memoryBankWords"] == BANK_WORDS
	assert metadata["contracts"]["220"]["backend"] == "unsupported-host-compiler"
	assert metadata["contracts"]["292"]["backend"] == "exact-fail-closed"

	for target in project["targets"]:
		_validate_graph(target)
		proccodes = {
			block.get("mutation", {}).get("proccode")
			for block in target.get("blocks", {}).values()
			if block.get("opcode") == "procedures_prototype"
		}
		if "xemem read index %n to slot %n" not in proccodes:
			continue
		read_body = _procedure_body(target, "xemem read index %n to slot %n")
		write_body = _procedure_body(target, "xemem write index %n value %s")
		reset_body = _procedure_body(target, "xemem reset dirty")
		bank_ids = {
			entry_id for entry_id, value in stage["lists"].items()
			if value[0].startswith("MEM_DATA_")
		}
		for block_id, block in target["blocks"].items():
			field = block.get("fields", {}).get("LIST", [None, None])
			if len(field) > 1 and field[1] in bank_ids and block.get("opcode") in {
				"data_itemoflist", "data_replaceitemoflist"
			}:
				assert block_id in read_body | write_body | reset_body

	for target in project["targets"]:
		for costume in target.get("costumes", []):
			assert costume["md5ext"] in members

	if with_explorer:
		program = next(value for value in stage["lists"].values() if value[0] == "MEM_PROGRAM")
		assert len(program[1]) == metadata["fileExplorer"]["programWords"]
		assert metadata["fileExplorer"]["source"] == "apps/file_explorer.xe"


def test_scroll_and_modifier_contracts_are_frame_latched(full_project: dict[str, object]) -> None:
	vm = _target(full_project, "Xenon-131 VM")
	blocks = vm["blocks"]
	latch = _procedure_body(vm, "xeabi latch scroll frame")
	assert {blocks[block_id]["opcode"] for block_id in latch} >= {
		"data_changevariableby", "data_deleteoflist", "data_setvariableto",
	}
	assert _field_values(vm, latch, "LIST") >= {"ABI_SCROLL_DELTAS", "ABI_SCROLL_AXES"}
	assert _field_values(vm, latch, "VARIABLE") >= {
		"ABI_SCROLL_FRAME_DELTA", "ABI_SCROLL_FRAME_AXIS", "ABI_SCROLL_LAST_AXIS",
	}

	begin_body = _syscall_body(vm, 102)
	assert any(
		blocks[block_id].get("mutation", {}).get("proccode") == "xeabi latch scroll frame"
		for block_id in begin_body
	)
	scroll_body = _syscall_body(vm, 124)
	assert "ABI_SCROLL_FRAME_DELTA" in _field_values(vm, scroll_body, "VARIABLE")
	assert not any(
		blocks[block_id]["opcode"] == "data_deleteoflist"
		and blocks[block_id].get("fields", {}).get("LIST", [None])[0]
		in {"ABI_SCROLL_DELTAS", "ABI_SCROLL_AXES"}
		for block_id in scroll_body
	)

	modifier_body = _syscall_body(vm, 246)
	assert _field_values(vm, modifier_body, "KEY_OPTION") >= {"shift", "control", "space"}
	assert "ABI_SCROLL_FRAME_AXIS" in _field_values(vm, modifier_body, "VARIABLE")
	modifier_changes = {
		block["inputs"]["VALUE"][1][1]
		for block_id in modifier_body
		if (block := blocks[block_id]).get("opcode") == "data_changevariableby"
		and isinstance(block.get("inputs", {}).get("VALUE", [None, None])[1], list)
	}
	assert modifier_changes >= {"1", "2"}


def test_slider_114_has_pointer_capture_and_drag_return(full_project: dict[str, object]) -> None:
	vm = _target(full_project, "Xenon-131 VM")
	body = _syscall_body(vm, 114)
	blocks = vm["blocks"]
	opcodes = {blocks[block_id]["opcode"] for block_id in body}
	assert {"sensing_mousedown", "sensing_mousex", "operator_divide", "operator_mathop"} <= opcodes
	assert _field_values(vm, body, "VARIABLE") >= {
		"ABI_MOUSE_PRESSED", "ABI_SLIDER_ACTIVE", "ABI_SLIDER_HANDLE",
		"ABI_SLIDER_X", "ABI_SLIDER_Y", "ABI_SLIDER_WIDTH", "ABI_RETURN",
	}
	assert any(
		blocks[block_id].get("opcode") == "operator_mathop"
		and blocks[block_id].get("fields", {}).get("OPERATOR", [None])[0] == "round"
		for block_id in body
	)


def test_required_vfs_mutators_resolve_live_parent_and_basename(
	full_project: dict[str, object],
) -> None:
	vm = _target(full_project, "Xenon-131 VM")
	blocks = vm["blocks"]
	find_body = _procedure_body(vm, "xeabi find live vfs path")
	assert _field_values(vm, find_body, "LIST") >= {
		"ABI_VFS_NAMES", "ABI_VFS_PARENTS", "ABI_VFS_ALIVE",
	}
	assert not any(
		blocks[block_id].get("opcode") == "data_itemoflist"
		and blocks[block_id].get("fields", {}).get("LIST", [None])[0] == "ABI_VFS_PATHS"
		for block_id in find_body
	)
	split_body = _procedure_body(vm, "xeabi basename current vfs path")
	assert _field_values(vm, split_body, "VARIABLE") >= {
		"ABI_VFS_PARENT_PATH", "ABI_VFS_BASENAME",
	}
	for syscall in (214, 215):
		body = _syscall_body(vm, syscall)
		variables = _field_values(vm, body, "VARIABLE")
		assert {
			"ABI_VFS_DEST_PATH", "ABI_VFS_PARENT_PATH", "ABI_VFS_BASENAME",
			"ABI_VFS_DEST_PARENT_ROW", "ABI_VFS_TARGET_ROW",
		} <= variables
		assert "ABI_VFS_CONTEXT_ROW" not in variables
		assert _field_values(vm, body, "LIST") >= {
			"ABI_VFS_PATHS", "ABI_VFS_NAMES", "ABI_VFS_PARENTS",
			"ABI_VFS_ALIVE", "ABI_VFS_MTIME",
		}

	cache_body = _procedure_body(vm, "xeabi cache vfs children")
	assert any(
		blocks[block_id].get("mutation", {}).get("proccode") == "xeabi insert sorted vfs child %n"
		for block_id in cache_body
	)
	assert "ABI_VFS_CONTEXT_ROW" in _field_values(vm, cache_body, "VARIABLE")


def test_required_vfs_handles_rename_delete_and_revision_are_guarded(
	full_project: dict[str, object],
) -> None:
	vm = _target(full_project, "Xenon-131 VM")
	blocks = vm["blocks"]
	open_body = _syscall_body(vm, 160)
	assert _field_values(vm, open_body, "LIST") >= {
		"ABI_VFS_TYPES", "ABI_HANDLE_IDS", "ABI_HANDLE_ROWS",
		"ABI_HANDLE_MODES", "ABI_HANDLE_CURSORS", "ABI_HANDLE_OPEN",
	}
	read_body = _syscall_body(vm, 162)
	assert _field_values(vm, read_body, "LIST") >= {
		"ABI_HANDLE_IDS", "ABI_HANDLE_ROWS", "ABI_HANDLE_MODES",
		"ABI_HANDLE_CURSORS", "ABI_HANDLE_OPEN", "ABI_VFS_ALIVE", "ABI_VFS_CONTENTS",
	}
	close_body = _syscall_body(vm, 164)
	assert _field_values(vm, close_body, "LIST") >= {"ABI_HANDLE_ROWS", "ABI_HANDLE_OPEN"}
	assert any(
		blocks[block_id].get("opcode") == "data_replaceitemoflist"
		and blocks[block_id].get("fields", {}).get("LIST", [None])[0] == "ABI_HANDLE_OPEN"
		for block_id in close_body
	)

	for syscall in (216, 217):
		body = _syscall_body(vm, syscall)
		assert any(
			blocks[block_id].get("mutation", {}).get("proccode") == "xeabi vfs subtree is closed %n"
			for block_id in body
		)
		assert "ABI_VFS_REVISION" in _field_values(vm, body, "VARIABLE")
		assert "ABI_VFS_CLOCK" in _field_values(vm, body, "VARIABLE")
	rename_body = _syscall_body(vm, 216)
	assert _field_values(vm, rename_body, "LIST") >= {
		"ABI_VFS_NAMES", "ABI_VFS_PARENTS", "ABI_VFS_KEYS", "ABI_VFS_MTIME",
	}
	assert _field_values(vm, rename_body, "VARIABLE") >= {
		"ABI_VFS_DEST_PARENT_ROW", "ABI_VFS_BASENAME", "ABI_VFS_CLOCK",
	}
	revision_body = _syscall_body(vm, 265)
	assert "ABI_VFS_REVISION" in _field_values(vm, revision_body, "VARIABLE")


def test_optional_accelerator_fallback_is_explicit_metadata(
	full_project: dict[str, object],
) -> None:
	metadata = full_project["meta"]["xeFullAbi"]
	assert metadata["optionalFallbackSyscalls"] == sorted(OPTIONAL_FALLBACK_SYSCALLS)
	assert metadata["contracts"]["248"] == {
		"args": contracts()[248].args,
		"result": contracts()[248].result,
		"backend": "unsupported-secondary-pointer-fallback",
		"availability": "optional-fallback",
		"unavailableResult": 0,
	}
	assert metadata["contracts"]["276"] == {
		"args": contracts()[276].args,
		"result": contracts()[276].result,
		"backend": "unsupported-accelerator-fallback",
		"availability": "optional-fallback",
		"unavailableResult": 0xFFFFFFFF,
	}
	vm = _target(full_project, "Xenon-131 VM")
	body = _syscall_body(vm, 276)
	assert any(
		block.get("opcode") == "data_setvariableto"
		and block.get("fields", {}).get("VARIABLE", [None])[0] == "ABI_RETURN"
		and block.get("inputs", {}).get("VALUE") == [1, [4, str(0xFFFFFFFF)]]
		for block_id in body
		if (block := vm["blocks"][block_id])
	)


def test_current_explorer_required_syscalls_have_non_generic_base_routes(
	full_project: dict[str, object],
) -> None:
	metadata = full_project["meta"]["xeFullAbi"]
	required = set(metadata["fileExplorer"]["requiredSyscalls"])
	assert required - OPTIONAL_FALLBACK_SYSCALLS <= PROVEN_EXACT_PROFILE_BASE
	assert required <= PROVEN_EXACT_PROFILE_BASE | OPTIONAL_FALLBACK_SYSCALLS

	vm = _target(full_project, "Xenon-131 VM")
	blocks = vm["blocks"]
	generic_variables = {
		"ABI_DIAGNOSTICS_ENABLED", "ABI_LAST_ID", "ABI_RETURN", "ABI_RETURN_TEXT",
	}
	generic_lists = {"ABI_ARGS", "ABI_CALL_LOG", "ABI_GFX_COMMANDS"}
	for syscall in sorted(PROVEN_EXACT_PROFILE_BASE):
		body = _syscall_body(vm, syscall)
		procedures = {
			blocks[block_id].get("mutation", {}).get("proccode")
			for block_id in body
			if blocks[block_id].get("opcode") == "procedures_call"
		}
		live_procedures = procedures - {None, "xeabi virtual %n %n %n %s %n"}
		live_variables = _field_values(vm, body, "VARIABLE") - generic_variables
		live_lists = _field_values(vm, body, "LIST") - generic_lists
		sensing = any(
			str(blocks[block_id].get("opcode", "")).startswith("sensing_")
			for block_id in body
		)
		explicit_constant_return = any(
			blocks[block_id].get("opcode") == "data_setvariableto"
			and blocks[block_id].get("fields", {}).get("VARIABLE", [None])[0]
			in {"ABI_RETURN", "ABI_RETURN_TEXT"}
			and isinstance(blocks[block_id].get("inputs", {}).get("VALUE", [None, None])[1], list)
			for block_id in body
		)
		assert live_procedures or live_variables or live_lists or sensing or explicit_constant_return, syscall


def test_small_window_sample_primitives_are_live(full_project: dict[str, object]) -> None:
	vm = _target(full_project, "Xenon-131 VM")
	blocks = vm["blocks"]
	assert any(
		blocks[block_id].get("mutation", {}).get("proccode")
		== "XGE::Draw Window | XY %s %s width %s height %s title address %s State %s"
		for block_id in _syscall_body(vm, 102)
	)
	for syscall, proccode in ((103, "XGE::gfx_render"), (104, "XGE::gfx_clear_screen")):
		assert any(
			blocks[block_id].get("mutation", {}).get("proccode") == proccode
			for block_id in _syscall_body(vm, syscall)
		)
	assert any(
		blocks[block_id].get("mutation", {}).get("proccode") == "XGE::gfx_rect %s %s %s %s %s"
		for block_id in _syscall_body(vm, 109)
	)
	assert any(
		blocks[block_id].get("mutation", {}).get("proccode") == "xeabi draw text value %n %n %s %n %n"
		for block_id in _syscall_body(vm, 110)
	)


def test_generated_profile_is_conservative_and_fallbacks_are_hash_bound(
	full_project: dict[str, object],
) -> None:
	payload = json.loads(_profile_bytes(build_bytes(), contracts()))
	core = {int(value) for value in SyscallID if int(value) < 100}
	assert set(payload["supported_syscalls"]) == core | PROVEN_EXACT_PROFILE_BASE
	assert OPTIONAL_FALLBACK_SYSCALLS.isdisjoint(payload["supported_syscalls"])
	explorer_hash = full_project["meta"]["xeFullAbi"]["fileExplorer"]["artifactHash"]
	assert payload["artifact_syscall_overrides"] == {
		explorer_hash: sorted(OPTIONAL_FALLBACK_SYSCALLS),
	}


def test_checked_artifacts_match_clean_rebuilds() -> None:
	vm = build_bytes()
	assert VM_OUTPUT.read_bytes() == vm
	assert EXPLORER_OUTPUT.read_bytes() == build_bytes(with_explorer=True)
	assert PROFILE_OUTPUT.read_bytes() == _profile_bytes(vm, contracts())


@pytest.mark.parametrize(
	("address", "expected"),
	[
		(0, (0, 1)),
		(199_999, (0, 200_000)),
		(200_000, (1, 1)),
		(399_999, (1, 200_000)),
		(1_000_000, (5, 1)),
		(1_999_999, (9, 200_000)),
	],
)
def test_logical_memory_boundaries(address: int, expected: tuple[int, int]) -> None:
	assert logical_location(address) == expected


@pytest.mark.parametrize("address", [-1, 2_000_000, 1.5, True])
def test_logical_memory_rejects_invalid_addresses(address: object) -> None:
	with pytest.raises(ValueError):
		logical_location(address)  # type: ignore[arg-type]


def test_banked_memory_model_preserves_numbers_and_text_without_aliasing() -> None:
	banks: list[dict[int, object]] = [dict() for _ in range(BANK_COUNT)]
	values = {0: "H", 199_999: 0, 200_000: "001", 399_999: 0xFFFFFFFF, 1_000_000: "", 1_999_999: 17}
	for address, value in values.items():
		bank, slot = logical_location(address)
		banks[bank][slot] = value
	for address, expected in values.items():
		bank, slot = logical_location(address)
		assert banks[bank][slot] == expected
	assert len({logical_location(address) for address in values}) == len(values)
