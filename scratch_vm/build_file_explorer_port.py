from __future__ import annotations

import argparse
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = ROOT / "scratch_vm" / "file_explorer_native.sasm"
PROJECT_DIR = ROOT / "scratch_vm" / "file_explorer_native_project"
PROJECT_PATH = PROJECT_DIR / "project.json"
DEFAULT_OUTPUT = ROOT / "examples" / "scratch" / "xenon_file_explorer_native.sb3"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
BANK_WORDS = 200_000
BANK_COUNT = 10
LOGICAL_WORDS = BANK_WORDS * BANK_COUNT
WORKING_BANKS = tuple(range(5))
STANDBY_BANKS = tuple(range(5, 10))


class NativeScratchPortError(RuntimeError):
	pass


def source_hash() -> str:
	text = SOURCE_PATH.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
	return hashlib.sha256(text.encode("utf-8")).hexdigest()


def logical_location(address: int) -> tuple[int, int]:
	"""Return the zero-based bank and Scratch's one-based list slot."""
	if type(address) is not int or not 0 <= address < LOGICAL_WORDS:
		raise ValueError(f"logical address must be in 0..{LOGICAL_WORDS - 1}")
	return address // BANK_WORDS, address % BANK_WORDS + 1


def normalize_word(value: int | float) -> int:
	if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
		raise ValueError("memory word must be numeric")
	return int(value // 1) % (1 << 32)


def normalize_allocation_size(value: int | float) -> int:
	if (
		isinstance(value, bool)
		or not isinstance(value, (int, float))
		or not math.isfinite(value)
		or value <= 0
		or value != int(value)
	):
		raise ValueError("allocation size must be a positive whole number")
	return int(value)


def _materialize_memory_banks(project: dict[str, object]) -> None:
	targets = project.get("targets")
	if not isinstance(targets, list):
		raise NativeScratchPortError("project has no target list")
	stage = next(
		(target for target in targets if isinstance(target, dict) and target.get("name") == "Stage"),
		None,
	)
	if stage is None or not isinstance(stage.get("lists"), dict):
		raise NativeScratchPortError("project has no Stage list map")
	lists = _named_entries(stage["lists"], "list")
	for index in range(BANK_COUNT):
		name = f"MEM_DATA_{index}"
		if name not in lists:
			raise NativeScratchPortError(f"missing physical memory bank: {name}")
		lists[name][1][1] = [0] * BANK_WORDS


def _named_entries(container: dict[str, object], prefix: str) -> dict[str, tuple[str, list[object]]]:
	result: dict[str, tuple[str, list[object]]] = {}
	for entry_id, value in container.items():
		if not isinstance(value, list) or len(value) < 2 or not isinstance(value[0], str):
			raise NativeScratchPortError(f"invalid {prefix} entry: {entry_id}")
		if value[0] in result:
			raise NativeScratchPortError(f"duplicate {prefix} name: {value[0]}")
		result[value[0]] = (entry_id, value)
	return result


def _input_block_ids(value: object) -> tuple[str, ...]:
	if not isinstance(value, list) or len(value) < 2:
		return ()
	ids: list[str] = []
	for candidate in value[1:3]:
		if isinstance(candidate, str):
			ids.append(candidate)
	return tuple(ids)


def _validate_blocks(target: dict[str, object]) -> None:
	blocks = target.get("blocks")
	if not isinstance(blocks, dict):
		raise NativeScratchPortError(f"target {target.get('name')} has no block map")
	allowed_prefixes = (
		"argument_", "control_", "data_", "event_", "looks_", "motion_",
		"operator_", "procedures_", "sensing_", "sound_",
	)
	for block_id, value in blocks.items():
		if not isinstance(value, dict):
			raise NativeScratchPortError(f"block {block_id} is not an object")
		opcode = value.get("opcode")
		if not isinstance(opcode, str) or not opcode.startswith(allowed_prefixes):
			raise NativeScratchPortError(f"non-vanilla opcode in {target.get('name')}: {opcode!r}")
		for field in ("next", "parent"):
			reference = value.get(field)
			if reference is not None and reference not in blocks:
				raise NativeScratchPortError(f"block {block_id} has missing {field} {reference}")
		inputs = value.get("inputs", {})
		if not isinstance(inputs, dict):
			raise NativeScratchPortError(f"block {block_id} inputs are not an object")
		for input_value in inputs.values():
			for reference in _input_block_ids(input_value):
				if reference not in blocks:
					raise NativeScratchPortError(f"block {block_id} has missing input {reference}")


def validate_project(project: dict[str, object]) -> None:
	targets = project.get("targets")
	if not isinstance(targets, list) or not targets:
		raise NativeScratchPortError("project has no targets")
	target_names = [target.get("name") for target in targets if isinstance(target, dict)]
	if len(target_names) != len(targets) or len(set(target_names)) != len(target_names):
		raise NativeScratchPortError("target names must be unique strings")
	if target_names.count("Stage") != 1:
		raise NativeScratchPortError("project must contain exactly one Stage")
	for required in ("Window", "Nucleus", "Item", "Controller", "Back", "New item", "Trash"):
		if required not in target_names:
			raise NativeScratchPortError(f"missing native File Explorer target: {required}")
	if project.get("extensions", []) != []:
		raise NativeScratchPortError("native port must use vanilla Scratch blocks only")

	for target in targets:
		assert isinstance(target, dict)
		_validate_blocks(target)

	stage = next(target for target in targets if target.get("name") == "Stage")
	lists = _named_entries(stage.get("lists", {}), "list")
	bank_names = {f"MEM_DATA_{index}" for index in range(BANK_COUNT)}
	actual_banks = {name for name in lists if name.startswith("MEM_DATA_")}
	if actual_banks != bank_names:
		raise NativeScratchPortError(f"memory banks differ: expected {sorted(bank_names)}, got {sorted(actual_banks)}")
	for name in sorted(bank_names):
		contents = lists[name][1][1]
		if not isinstance(contents, list) or len(contents) != BANK_WORDS:
			raise NativeScratchPortError(f"{name} must contain exactly {BANK_WORDS} physical words")
		if any(value != 0 for value in contents):
			raise NativeScratchPortError(f"{name} must initialize every physical word to zero")

	variables = _named_entries(stage.get("variables", {}), "variable")
	expected_variables = {
		"MEM_BANK_SIZE": BANK_WORDS,
		"MEM_LOGICAL_LIMIT": LOGICAL_WORDS,
		"MEM_WORKING_BANKS": len(WORKING_BANKS),
		"MEM_RESERVE_BANKS": len(STANDBY_BANKS),
	}
	for name, expected in expected_variables.items():
		if name not in variables or int(variables[name][1][1]) != expected:
			raise NativeScratchPortError(f"{name} must be {expected}")

	blocks = stage["blocks"]
	procedures = {
		block.get("mutation", {}).get("proccode")
		for block in blocks.values()
		if block.get("opcode") == "procedures_prototype"
	}
	for procedure in ("memory map %n", "memory read %n", "memory write %n %n", "memory allocate %n"):
		if procedure not in procedures:
			raise NativeScratchPortError(f"missing memory routing block: {procedure}")

	vfs_names = ("VFS_IDS", "VFS_NAMES", "VFS_TYPES", "VFS_PARENTS", "VFS_PATHS")
	if any(name not in lists for name in vfs_names):
		raise NativeScratchPortError("native port VFS lists are incomplete")
	vfs_columns = [lists[name][1][1] for name in vfs_names]
	if len({len(column) for column in vfs_columns}) != 1:
		raise NativeScratchPortError("native port VFS columns have different lengths")
	ids, _, types, parents, paths = vfs_columns
	ids = [int(value) for value in ids]
	parents = [int(value) for value in parents]
	if len(set(ids)) != len(ids) or any(value not in {"file", "folder"} for value in types):
		raise NativeScratchPortError("native port VFS identifiers or types are invalid")
	if any(parent != 0 and parent not in ids for parent in parents):
		raise NativeScratchPortError("native port VFS contains an unknown parent")
	if any(not isinstance(path, str) or not path.startswith("/") for path in paths):
		raise NativeScratchPortError("native port VFS paths must be rooted")

	meta = project.get("meta")
	if not isinstance(meta, dict) or not isinstance(meta.get("xeNativePort"), dict):
		raise NativeScratchPortError("native port metadata is missing")
	port_meta = meta["xeNativePort"]
	if port_meta.get("kind") != "native-scratch-reference" or port_meta.get("exactXeBytecode") is not False:
		raise NativeScratchPortError("native port metadata must not claim Xe bytecode parity")
	if port_meta.get("workingBanks") != list(WORKING_BANKS) or port_meta.get("standbyBanks") != list(STANDBY_BANKS):
		raise NativeScratchPortError("native port memory tiers are not frozen")
	if port_meta.get("sourceSha256") != source_hash():
		raise NativeScratchPortError("ScratchASM source and generated project snapshot differ")


def _project_and_assets() -> tuple[dict[str, object], dict[str, bytes]]:
	try:
		project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
	except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
		raise NativeScratchPortError(f"cannot load native port project: {error}") from error
	if not isinstance(project, dict):
		raise NativeScratchPortError("native port project root is not an object")
	_materialize_memory_banks(project)
	validate_project(project)
	assets: dict[str, bytes] = {}
	referenced: set[str] = set()
	for target in project["targets"]:
		for costume in target.get("costumes", []):
			if not isinstance(costume, dict) or not isinstance(costume.get("md5ext"), str):
				raise NativeScratchPortError(f"invalid costume in {target.get('name')}")
			referenced.add(costume["md5ext"])
	for name in sorted(referenced):
		path = PROJECT_DIR / name
		try:
			payload = path.read_bytes()
		except OSError as error:
			raise NativeScratchPortError(f"missing costume asset {name}: {error}") from error
		if Path(name).name != name or not name.endswith(".svg"):
			raise NativeScratchPortError(f"unsafe or unsupported asset name: {name}")
		if hashlib.md5(payload).hexdigest() != name[:-4]:  # Scratch asset IDs use MD5.
			raise NativeScratchPortError(f"costume asset hash mismatch: {name}")
		assets[name] = payload
	return project, assets


def build_bytes() -> bytes:
	project, assets = _project_and_assets()
	project_bytes = json.dumps(
		project,
		ensure_ascii=False,
		sort_keys=True,
		separators=(",", ":"),
	).encode("utf-8")
	members = {"project.json": project_bytes, **assets}
	stream = BytesIO()
	with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
		for name in sorted(members):
			info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
			info.compress_type = zipfile.ZIP_DEFLATED
			info.create_system = 0
			info.external_attr = 0
			archive.writestr(info, members[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
	return stream.getvalue()


def write_artifact(output: Path, *, overwrite: bool) -> None:
	payload = build_bytes()
	output.parent.mkdir(parents=True, exist_ok=True)
	if output.exists() and not overwrite:
		raise NativeScratchPortError(f"output already exists: {output}")
	fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
	os.close(fd)
	temporary = Path(temporary_name)
	try:
		temporary.write_bytes(payload)
		if output.exists() and not overwrite:
			raise NativeScratchPortError(f"output already exists: {output}")
		os.replace(temporary, output)
	finally:
		temporary.unlink(missing_ok=True)


def main() -> int:
	parser = argparse.ArgumentParser(description="Build the deterministic native-Scratch File Explorer reference port")
	parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
	parser.add_argument("--check", action="store_true", help="verify the checked artifact matches a clean rebuild")
	parser.add_argument("--overwrite", action="store_true")
	args = parser.parse_args()
	try:
		payload = build_bytes()
		if args.check:
			if not args.output.is_file() or args.output.read_bytes() != payload:
				raise NativeScratchPortError(f"native Scratch artifact is stale: {args.output}")
			print(f"Native Scratch File Explorer is current: {args.output}")
			return 0
		write_artifact(args.output, overwrite=args.overwrite)
		print(f"Wrote {args.output} ({len(payload)} bytes, sha256 {hashlib.sha256(payload).hexdigest()})")
		return 0
	except NativeScratchPortError as error:
		parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
	raise SystemExit(main())
