from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable
from xe_lang.archive_safety import ArchiveSafetyError, load_safe_zip_members, normalize_archive_member

from xe_lang.compiler_service import (
	MAX_ADDRESS_COUNT,
	capability_for_syscall,
	compile_source,
	syscall_name,
)
from xe_lang.scratch_profile import ScratchVMProfile
from xe_lang.sb3_exporter import analyze_compatibility
from xe_lang.syscall_abi import SyscallID


ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "scratch_vm" / "profile.json"
TEMPLATE_PATH = ROOT / "scratch_vm" / "xenon131-vm.sb3"
MANIFEST_PATH = ROOT / "scratch_vm" / "capability-manifest.json"


def _safe_member_name(name: str) -> bool:
	try:
		normalize_archive_member(name)
		return True
	except ArchiveSafetyError:
		return False


def _input_block_id(value: object) -> str | None:
	if not isinstance(value, list) or len(value) < 2:
		return None
	return value[1] if isinstance(value[1], str) else None


def _input_integer(value: object) -> int | None:
	if not isinstance(value, list) or len(value) < 2:
		return None
	primitive = value[1]
	if not isinstance(primitive, list) or len(primitive) < 2:
		return None
	try:
		return int(str(primitive[1]), 10)
	except ValueError:
		return None


def _walk_block_graph(blocks: dict[str, object], roots: Iterable[str | None]) -> set[str]:
	seen: set[str] = set()
	stack = [value for value in roots if isinstance(value, str)]
	while stack:
		block_id = stack.pop()
		if block_id in seen or block_id not in blocks:
			continue
		seen.add(block_id)
		block = blocks[block_id]
		if not isinstance(block, dict):
			continue
		next_id = block.get("next")
		if isinstance(next_id, str):
			stack.append(next_id)
		for value in block.get("inputs", {}).values():
			if not isinstance(value, list):
				continue
			for candidate in value[1:]:
				if isinstance(candidate, str):
					stack.append(candidate)
	return seen


def _dispatcher_syscalls(project: dict[str, object]) -> tuple[int, ...]:
	matches: list[tuple[dict[str, object], dict[str, object], str]] = []
	for target in project.get("targets", []):
		if not isinstance(target, dict):
			continue
		blocks = target.get("blocks", {})
		if not isinstance(blocks, dict):
			continue
		for block_id, block in blocks.items():
			if not isinstance(block, dict) or block.get("opcode") != "procedures_prototype":
				continue
			if block.get("mutation", {}).get("proccode") == "sys_dispatch %n":
				matches.append((target, blocks, block_id))
	if len(matches) != 1:
		raise ValueError(f"Template must contain exactly one sys_dispatch %n prototype; found {len(matches)}")
	_, blocks, prototype_id = matches[0]
	definitions = [
		block
		for block in blocks.values()
		if isinstance(block, dict)
		and block.get("opcode") == "procedures_definition"
		and _input_block_id(block.get("inputs", {}).get("custom_block")) == prototype_id
	]
	if len(definitions) != 1:
		raise ValueError(f"Template must contain exactly one sys_dispatch definition; found {len(definitions)}")
	block_ids = _walk_block_graph(blocks, (definitions[0].get("next"),))
	values: set[int] = set()
	for block_id in block_ids:
		block = blocks[block_id]
		if not isinstance(block, dict) or block.get("opcode") != "operator_equals":
			continue
		inputs = block.get("inputs", {})
		left_id = _input_block_id(inputs.get("OPERAND1"))
		right_id = _input_block_id(inputs.get("OPERAND2"))
		left = blocks.get(left_id) if left_id is not None else None
		right = blocks.get(right_id) if right_id is not None else None
		left_is_id = (
			isinstance(left, dict)
			and left.get("opcode") == "argument_reporter_string_number"
			and left.get("fields", {}).get("VALUE", [None])[0] == "id"
		)
		right_is_id = (
			isinstance(right, dict)
			and right.get("opcode") == "argument_reporter_string_number"
			and right.get("fields", {}).get("VALUE", [None])[0] == "id"
		)
		if left_is_id:
			value = _input_integer(inputs.get("OPERAND2"))
		elif right_is_id:
			value = _input_integer(inputs.get("OPERAND1"))
		else:
			continue
		if value is not None:
			values.add(value)
	return tuple(sorted(values))


def _memory_initializers(project: dict[str, object], list_name: str) -> tuple[int, ...]:
	values: list[int] = []
	for target in project.get("targets", []):
		if not isinstance(target, dict):
			continue
		blocks = target.get("blocks", {})
		if not isinstance(blocks, dict):
			continue
		for block in blocks.values():
			if not isinstance(block, dict) or block.get("opcode") != "control_repeat":
				continue
			child_id = _input_block_id(block.get("inputs", {}).get("SUBSTACK"))
			child = blocks.get(child_id) if child_id is not None else None
			if not isinstance(child, dict) or child.get("opcode") != "data_addtolist":
				continue
			field = child.get("fields", {}).get("LIST", [None])
			if field[0] != list_name:
				continue
			value = _input_integer(block.get("inputs", {}).get("TIMES"))
			if value is not None:
				values.append(value)
	return tuple(sorted(values))


def _list_count(project: dict[str, object], target_name: str, list_name: str) -> int:
	count = 0
	for target in project.get("targets", []):
		if not isinstance(target, dict) or target.get("name") != target_name:
			continue
		for value in target.get("lists", {}).values():
			if isinstance(value, list) and len(value) == 2 and value[0] == list_name and isinstance(value[1], list):
				count += 1
	return count


def audit_template(template_path: Path, profile: ScratchVMProfile) -> dict[str, object]:
	payload = template_path.read_bytes()
	members = load_safe_zip_members(payload)
	names = [name for name, _ in members]
	projects = [data for name, data in members if name == "project.json"]
	if len(projects) != 1:
		raise ValueError(f"Template must contain exactly one project.json; found {len(projects)}")
	project = json.loads(projects[0])
	if not isinstance(project, dict):
		raise ValueError("Template project.json must contain an object")
	dispatcher = _dispatcher_syscalls(project)
	return {
		"archive_member_count": len(names),
		"archive_members_unique": len(names) == len(set(names)),
		"archive_paths_safe": all(_safe_member_name(name) for name in names),
		"mem_data_initializer_words": list(_memory_initializers(project, profile.mem_data_list)),
		"mem_data_list_count": _list_count(project, profile.mem_program_target, profile.mem_data_list),
		"mem_program_list_count": _list_count(project, profile.mem_program_target, profile.mem_program_list),
		"sha256": hashlib.sha256(payload).hexdigest(),
		"sys_dispatch": list(dispatcher),
		"sys_dispatch_matches_profile": set(dispatcher) == set(profile.supported_syscalls),
	}


def _syscall_record(value: int) -> dict[str, object]:
	return {
		"capability": capability_for_syscall(value),
		"id": value,
		"name": syscall_name(value),
	}


def _app_record(path: Path, profile: ScratchVMProfile) -> dict[str, object]:
	artifact = compile_source(path.read_text(encoding="utf-8"), path.name)
	report = analyze_compatibility(artifact, profile)
	issues: list[dict[str, object]] = []
	for issue in report.issues:
		record: dict[str, object] = {
			"code": issue.code,
			"message": issue.message,
			"severity": issue.severity,
		}
		if issue.syscall is not None:
			record["syscall"] = _syscall_record(issue.syscall)
		issues.append(record)
	return {
		"artifact_hash": artifact.artifact_hash,
		"assets": list(artifact.assets),
		"compile_success": artifact.success,
		"diagnostics": [
			{
				"code": item.code,
				"column": item.column,
				"line": item.line,
				"message": item.message,
				"path": item.path,
				"severity": item.severity,
			}
			for item in artifact.diagnostics
		],
		"dynamic_assets": list(artifact.dynamic_assets),
		"exact": report.exact,
		"issues": issues,
		"required_capabilities": list(artifact.required_capabilities),
		"required_syscalls": [_syscall_record(value) for value in artifact.required_syscalls],
		"source_hash": artifact.source_hash,
		"status": "exact" if report.exact else "blocked",
	}


def generate_manifest(root: Path = ROOT) -> dict[str, object]:
	profile_path = root / "scratch_vm" / "profile.json"
	template_path = root / "scratch_vm" / "xenon131-vm.sb3"
	profile = ScratchVMProfile.load(profile_path)
	template = audit_template(template_path, profile)
	known = sorted({int(value) for value in SyscallID})
	missing = sorted(set(known) - set(profile.supported_syscalls))
	missing_by_capability: dict[str, list[dict[str, object]]] = {}
	for value in missing:
		record = _syscall_record(value)
		missing_by_capability.setdefault(str(record["capability"]), []).append(record)
	apps = {
		path.name: _app_record(path, profile)
		for path in sorted((root / "apps").glob("*.xe"), key=lambda value: (value.name.casefold(), value.name))
	}
	return {
		"abi": {
			"known_syscall_count": len(known),
			"missing_by_capability": missing_by_capability,
			"missing_syscalls": [_syscall_record(value) for value in missing],
			"profile_supported_syscalls": [_syscall_record(value) for value in sorted(profile.supported_syscalls)],
		},
		"apps": apps,
		"compatibility": {
			"blocked_apps": sorted(name for name, record in apps.items() if not record["exact"]),
			"exact_apps": sorted(name for name, record in apps.items() if record["exact"]),
		},
		"memory": {
			"exact": profile.address_limit == MAX_ADDRESS_COUNT,
			"scratch_address_limit": profile.address_limit,
			"scratch_static_limit": profile.static_limit,
			"xvm_address_limit": MAX_ADDRESS_COUNT,
		},
		"profile": {
			"capabilities": sorted(profile.capabilities),
			"name": profile.name,
			"version": profile.version,
		},
		"schema_version": 1,
		"template": template,
	}


def manifest_text(root: Path = ROOT) -> str:
	return json.dumps(generate_manifest(root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
	parser = argparse.ArgumentParser(description="Audit Xe apps against the pinned vanilla-Scratch VM profile")
	group = parser.add_mutually_exclusive_group()
	group.add_argument("--write", action="store_true", help="replace scratch_vm/capability-manifest.json")
	group.add_argument("--check", action="store_true", help="fail if the checked manifest is stale")
	arguments = parser.parse_args()
	payload = manifest_text()
	if arguments.write:
		MANIFEST_PATH.write_text(payload, encoding="utf-8", newline="\n")
		print(f"Wrote {MANIFEST_PATH.relative_to(ROOT)}")
		return 0
	if arguments.check:
		if not MANIFEST_PATH.is_file() or MANIFEST_PATH.read_text(encoding="utf-8") != payload:
			print("Scratch compatibility manifest is stale; run: python -m scratch_vm.audit --write")
			return 1
		print("Scratch compatibility manifest is current")
		return 0
	print(payload, end="")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
