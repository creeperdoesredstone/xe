from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
from typing import Any, Callable
import zipfile

from xe_lang.syscall_abi import SyscallID


ROOT = Path(__file__).resolve().parent.parent
SOURCE_TEMPLATE = ROOT / "scratch_vm" / "templates" / "Xenon-131-VM-1ec2a237.sb3"
NATIVE_EXPLORER = ROOT / "examples" / "scratch" / "xenon_file_explorer_native.sb3"
FILE_EXPLORER_SOURCE = ROOT / "apps" / "file_explorer.xe"
VM_OUTPUT = ROOT / "examples" / "scratch" / "Xenon-131-VM-Full-ABI.sb3"
EXPLORER_OUTPUT = ROOT / "examples" / "scratch" / "Xenon-131-VM-Full-ABI-File-Explorer.sb3"
PROFILE_OUTPUT = ROOT / "scratch_vm" / "full-abi-profile.json"
SOURCE_SHA256 = "1ec2a2371090f52931dea6a5f4bcf8d737fca0e4ae9cfdcb2a5a4e983ec8d65b"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
BANK_WORDS = 200_000
BANK_COUNT = 10
LOGICAL_WORDS = BANK_WORDS * BANK_COUNT
TRUE = 0xFFFFFFFF
OPTIONAL_FALLBACK_SYSCALLS = frozenset({248, 276})
PROVEN_EXACT_PROFILE_BASE = frozenset({
	10, 12, 20, 21,
	102, 103, 104, 105, 106, 107, 108, 109, 110, 114,
	117, 118, 119, 121, 122, 123, 124, 127, 128, 129,
	142, 143, 145, 152, 160, 162, 164, 170, 171, 184, 209,
	210, 211, 212, 213, 214, 215, 216, 217, 246, 253, 254,
	265, 292, 293,
})


class FullAbiBuildError(RuntimeError):
	pass


def logical_location(address: int) -> tuple[int, int]:
	if type(address) is not int or not 0 <= address < LOGICAL_WORDS:
		raise ValueError(f"logical address must be in 0..{LOGICAL_WORDS - 1}")
	return address // BANK_WORDS, address % BANK_WORDS + 1


@dataclass(frozen=True)
class Contract:
	args: int
	result: str
	backend: str
	default: int | str = 0
	result_index: int = 0
	default_reporter: str = ""


def _json_string(values: list[str]) -> str:
	return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _load_archive(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
	try:
		payload = path.read_bytes()
	except OSError as error:
		raise FullAbiBuildError(f"cannot read {path}: {error}") from error
	if path == SOURCE_TEMPLATE and hashlib.sha256(payload).hexdigest() != SOURCE_SHA256:
		raise FullAbiBuildError("pinned Xenon-131 source template hash differs")
	members: dict[str, bytes] = {}
	try:
		with zipfile.ZipFile(BytesIO(payload)) as archive:
			for info in archive.infolist():
				name = info.filename.replace("\\", "/")
				if name.startswith("/") or ".." in Path(name).parts or name in members:
					raise FullAbiBuildError(f"unsafe or duplicate archive member: {name}")
				members[name] = archive.read(info)
	except (OSError, zipfile.BadZipFile) as error:
		raise FullAbiBuildError(f"cannot open {path}: {error}") from error
	try:
		project = json.loads(members.pop("project.json"))
	except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
		raise FullAbiBuildError(f"invalid project.json in {path}: {error}") from error
	if not isinstance(project, dict):
		raise FullAbiBuildError("project root must be an object")
	return project, members


def _target(project: dict[str, Any], name: str) -> dict[str, Any]:
	matches = [value for value in project.get("targets", []) if value.get("name") == name]
	if len(matches) != 1:
		raise FullAbiBuildError(f"expected one target named {name!r}; found {len(matches)}")
	return matches[0]


def _named_entries(container: dict[str, Any]) -> dict[str, tuple[str, list[Any]]]:
	return {
		value[0]: (entry_id, value)
		for entry_id, value in container.items()
		if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
	}


def _rewrite_block(
	block: dict[str, Any],
	block_ids: dict[str, str],
	entity_ids: dict[str, str],
	entity_names: dict[str, str],
	procedure_names: dict[str, str] | None = None,
) -> dict[str, Any]:
	value = deepcopy(block)

	def rewrite_inline(node: Any) -> None:
		if not isinstance(node, list):
			return
		if len(node) >= 3 and node[0] in {11, 12, 13} and isinstance(node[2], str):
			old_id = node[2]
			node[2] = entity_ids.get(old_id, old_id)
			if old_id in entity_names:
				node[1] = entity_names[old_id]
		for child in node:
			rewrite_inline(child)

	for key in ("next", "parent"):
		if isinstance(value.get(key), str):
			value[key] = block_ids.get(value[key], value[key])
	for input_value in value.get("inputs", {}).values():
		if not isinstance(input_value, list):
			continue
		rewrite_inline(input_value)
		for index in range(1, min(3, len(input_value))):
			if isinstance(input_value[index], str):
				input_value[index] = block_ids.get(input_value[index], input_value[index])
	for field in value.get("fields", {}).values():
		if not isinstance(field, list) or len(field) < 2 or not isinstance(field[1], str):
			continue
		old_id = field[1]
		field[1] = entity_ids.get(old_id, old_id)
		if old_id in entity_names:
			field[0] = entity_names[old_id]
	mutation = value.get("mutation")
	if procedure_names and isinstance(mutation, dict):
		proccode = mutation.get("proccode")
		if isinstance(proccode, str) and proccode in procedure_names:
			mutation["proccode"] = procedure_names[proccode]
	return value


def _reachable_blocks(blocks: dict[str, Any]) -> set[str]:
	roots = sorted(
		block_id for block_id, block in blocks.items()
		if block.get("topLevel") is True
	)
	reachable: set[str] = set()
	pending = list(reversed(roots))
	while pending:
		block_id = pending.pop()
		if block_id in reachable or block_id not in blocks:
			continue
		reachable.add(block_id)
		block = blocks[block_id]
		children: list[str] = []
		if isinstance(block.get("next"), str):
			children.append(block["next"])
		for input_value in block.get("inputs", {}).values():
			if not isinstance(input_value, list):
				continue
			children.extend(
				child for child in input_value[1:3]
				if isinstance(child, str)
			)
		pending.extend(reversed(children))
	return reachable


def _script_descendants(blocks: dict[str, Any], root: str | None) -> set[str]:
	if not isinstance(root, str):
		return set()
	result: set[str] = set()
	pending = [root]
	while pending:
		block_id = pending.pop()
		if block_id in result or block_id not in blocks:
			continue
		result.add(block_id)
		block = blocks[block_id]
		if isinstance(block.get("next"), str):
			pending.append(block["next"])
		for value in block.get("inputs", {}).values():
			if not isinstance(value, list):
				continue
			pending.extend(child for child in value[1:3] if isinstance(child, str))
	return result


def _merge_graphics_engines(project: dict[str, Any]) -> None:
	vm = _target(project, "Xenon-131 VM")
	xge = _target(project, "Xenon Graphics Engine")
	ge = _target(project, "Graphics Engine")
	original_vm_costumes = deepcopy(vm.get("costumes", []))
	original_vm_sounds = deepcopy(vm.get("sounds", []))
	init_proccodes: list[str] = []
	broadcast_proccodes: dict[str, str] = {}
	monitor_names: dict[str, dict[str, str]] = {}
	monitor_entity_ids: dict[str, dict[str, str]] = {}
	monitor_entity_display: dict[str, dict[str, str]] = {}

	for engine, prefix, namespace, costume_offset in (
		(xge, "xge_", "XGE::", 0),
		(ge, "ge_", "GE::", len(xge.get("costumes", []))),
	):
		engine_blocks = engine.get("blocks", {})
		reachable = _reachable_blocks(engine_blocks)
		event_hats = {
			block_id for block_id, block in engine_blocks.items()
			if block.get("topLevel") is True and str(block.get("opcode", "")).startswith("event_")
		}
		flag_hats = [
			block_id for block_id in sorted(event_hats)
			if engine_blocks[block_id].get("opcode") == "event_whenflagclicked"
		]
		if len(flag_hats) != 1:
			raise FullAbiBuildError(f"{engine['name']} must have one green-flag initializer")
		flag_hat = flag_hats[0]
		init_chain: list[str] = []
		worker_blocks: set[str] = set()
		current = engine_blocks[flag_hat].get("next")
		while isinstance(current, str):
			block = engine_blocks[current]
			if block.get("opcode") == "control_forever":
				worker_blocks.update(_script_descendants(engine_blocks, current))
				break
			init_chain.append(current)
			current = block.get("next")

		excluded = set(event_hats) | worker_blocks
		copy_ids = sorted(reachable - excluded)
		block_ids = {old_id: prefix + old_id for old_id in copy_ids}
		variable_ids = {old_id: prefix + old_id for old_id in engine.get("variables", {})}
		list_ids = {old_id: prefix + old_id for old_id in engine.get("lists", {})}
		comment_ids = {old_id: prefix + old_id for old_id in engine.get("comments", {})}
		entity_ids = {**variable_ids, **list_ids}
		entity_names: dict[str, str] = {}
		for old_id, entry in engine.get("variables", {}).items():
			entity_names[old_id] = namespace + entry[0]
			vm.setdefault("variables", {})[variable_ids[old_id]] = [entity_names[old_id], deepcopy(entry[1])]
		for old_id, entry in engine.get("lists", {}).items():
			entity_names[old_id] = namespace + entry[0]
			vm.setdefault("lists", {})[list_ids[old_id]] = [entity_names[old_id], deepcopy(entry[1])]

		proccodes = {
			block.get("mutation", {}).get("proccode")
			for block_id, block in engine_blocks.items()
			if block_id in reachable and block.get("opcode") == "procedures_prototype"
		}
		procedure_names = {
			value: namespace + value for value in proccodes if isinstance(value, str)
		}
		for old_id in copy_ids:
			block = engine_blocks[old_id]
			new_id = block_ids[old_id]
			value = _rewrite_block(block, block_ids, entity_ids, entity_names, procedure_names)
			if engine is ge and value.get("opcode") == "looks_costume":
				field = value.get("fields", {}).get("COSTUME")
				if isinstance(field, list) and field:
					field[0] = namespace + str(field[0])
			if engine is ge and value.get("opcode") == "looks_costumenumbername":
				field = value.get("fields", {}).get("NUMBER_NAME")
				if isinstance(field, list) and field and field[0] == "number":
					raw_id = f"{new_id}_raw"
					raw = deepcopy(value)
					raw["parent"] = new_id
					raw["next"] = None
					raw["topLevel"] = False
					vm.setdefault("blocks", {})[raw_id] = raw
					value = {
						"opcode": "operator_subtract", "next": None, "parent": value.get("parent"),
						"inputs": {"NUM1": [3, raw_id, [4, "0"]], "NUM2": [1, [4, str(costume_offset)]]},
						"fields": {}, "shadow": False, "topLevel": False,
					}
			vm.setdefault("blocks", {})[new_id] = value

		# GE static costume menus are namespaced. Numeric selectors use the
		# fixed segment offset while the temp-costume name restore stays a name.
		if engine is ge:
			for old_id in copy_ids:
				if engine_blocks[old_id].get("opcode") != "looks_switchcostumeto":
					continue
				new_id = block_ids[old_id]
				old_input = engine_blocks[old_id].get("inputs", {}).get("COSTUME", [])
				old_root = old_input[1] if len(old_input) > 1 else None
				if isinstance(old_root, str) and engine_blocks.get(old_root, {}).get("opcode") == "looks_costume":
					continue
				if not (isinstance(old_root, str) and engine_blocks.get(old_root, {}).get("opcode") == "operator_add"):
					continue
				input_value = vm["blocks"][new_id]["inputs"]["COSTUME"]
				offset_id = f"{new_id}_offset"
				vm["blocks"][offset_id] = {
					"opcode": "operator_add", "next": None, "parent": new_id,
					"inputs": {"NUM1": deepcopy(input_value), "NUM2": [1, [4, str(costume_offset)]]},
					"fields": {}, "shadow": False, "topLevel": False,
				}
				for child in input_value[1:3]:
					if isinstance(child, str) and child in vm["blocks"]:
						vm["blocks"][child]["parent"] = offset_id
				vm["blocks"][new_id]["inputs"]["COSTUME"] = [3, offset_id, [4, ""]]

		for old_id, comment in engine.get("comments", {}).items():
			value = deepcopy(comment)
			if isinstance(value.get("blockId"), str):
				if value["blockId"] not in block_ids:
					continue
				value["blockId"] = block_ids[value["blockId"]]
			vm.setdefault("comments", {})[comment_ids[old_id]] = value

		writer = _BlockWriter(vm)
		init_proccode = namespace + "initialize"
		definition, _ = writer.procedure_definition(init_proccode, [], [])
		if init_chain:
			first = block_ids[init_chain[0]]
			last = block_ids[init_chain[-1]]
			writer.blocks[definition]["next"] = first
			writer.blocks[first]["parent"] = definition
			if engine is ge:
				restore_size = writer.new(
					"looks_setsizeto", parent=last,
					inputs={"SIZE": [1, [4, str(xge.get("size", 36000))]]},
				)
				writer.blocks[last]["next"] = restore_size
			else:
				writer.blocks[last]["next"] = None
		init_proccodes.append(init_proccode)

		for hat_id in sorted(event_hats):
			hat = engine_blocks[hat_id]
			if hat.get("opcode") != "event_whenbroadcastreceived":
				continue
			field = hat.get("fields", {}).get("BROADCAST_OPTION", [])
			name = str(field[0]) if field else "event"
			body = hat.get("next")
			if not isinstance(body, str) or body not in block_ids:
				continue
			proccode = namespace + "event " + name
			definition, _ = writer.procedure_definition(proccode, [], [])
			first = block_ids[body]
			writer.blocks[definition]["next"] = first
			writer.blocks[first]["parent"] = definition
			broadcast_proccodes[name] = proccode

		monitor_names[engine["name"]] = {
			entry[0]: entity_names[entry_id]
			for entry_id, entry in {**engine.get("variables", {}), **engine.get("lists", {})}.items()
			if entry_id in entity_names
		}
		monitor_entity_ids[engine["name"]] = dict(entity_ids)
		monitor_entity_display[engine["name"]] = dict(entity_names)

	# Preserve each engine's costume ordinal segment. GE names are namespaced so
	# its dynamic costume-name save/restore is unambiguous after consolidation.
	xge_costumes = deepcopy(xge.get("costumes", []))
	ge_costumes = deepcopy(ge.get("costumes", []))
	for costume in ge_costumes:
		costume["name"] = "GE::" + costume["name"]
	vm["costumes"] = xge_costumes + ge_costumes + original_vm_costumes
	vm["sounds"] = deepcopy(xge.get("sounds", [])) + deepcopy(ge.get("sounds", [])) + original_vm_sounds
	for key in (
		"currentCostume", "visible", "x", "y", "size", "direction", "draggable",
		"rotationStyle", "volume",
	):
		if key in xge:
			vm[key] = deepcopy(xge[key])

	# One canonical green-flag worker calls both finite engine initializers before
	# the original VM boot. No transplanted engine event hat or forever worker remains.
	green_flags = [
		block_id for block_id, block in vm.get("blocks", {}).items()
		if block.get("topLevel") is True and block.get("opcode") == "event_whenflagclicked"
	]
	if len(green_flags) != 1:
		raise FullAbiBuildError("merged VM must retain exactly one green-flag script")
	green_flag = green_flags[0]
	original_first = vm["blocks"][green_flag].get("next")
	writer = _BlockWriter(vm)
	init_calls = [writer.procedure_call(value, []) for value in reversed(init_proccodes)]
	first_call = writer.chain(init_calls, green_flag)
	vm["blocks"][green_flag]["next"] = first_call or original_first
	if init_calls:
		vm["blocks"][init_calls[-1]]["next"] = original_first
		if isinstance(original_first, str):
			vm["blocks"][original_first]["parent"] = init_calls[-1]

	# Replace any retained Draw Desktop broadcast boundary with its synchronous
	# local procedure. Other broadcasts remain part of the VM's own contract.
	for block in vm.get("blocks", {}).values():
		if block.get("opcode") not in {"event_broadcast", "event_broadcastandwait"}:
			continue
		input_value = block.get("inputs", {}).get("BROADCAST_INPUT")
		name = None
		if isinstance(input_value, list):
			for value in input_value[1:3]:
				if isinstance(value, list) and len(value) >= 2 and value[0] == 11:
					name = value[1]
		if name not in broadcast_proccodes:
			continue
		block["opcode"] = "procedures_call"
		block["inputs"] = {}
		block["fields"] = {}
		block["mutation"] = {
			"tagName": "mutation", "children": [], "proccode": broadcast_proccodes[name],
			"argumentids": "[]", "warp": "true",
		}

	project["targets"] = [
		target for target in project["targets"] if target is not xge and target is not ge
	]
	for index, target in enumerate(project["targets"]):
		target["layerOrder"] = index
	for monitor in project.get("monitors", []):
		sprite_name = monitor.get("spriteName")
		if sprite_name in monitor_names or sprite_name in {"Graphics Engine (temporary)", "VM"}:
			monitor["spriteName"] = vm["name"]
			engine_name = str(sprite_name) if sprite_name in monitor_names else "Xenon Graphics Engine"
			mapping = monitor_names.get(engine_name, {})
			id_mapping = monitor_entity_ids.get(engine_name, {})
			display_mapping = monitor_entity_display.get(engine_name, {})
			old_id = monitor.get("id")
			if isinstance(old_id, str) and old_id in id_mapping:
				monitor["id"] = id_mapping[old_id]
				for key in ("VARIABLE", "LIST"):
					if key in monitor.get("params", {}):
						monitor["params"][key] = display_mapping[old_id]
			for key in ("VARIABLE", "LIST"):
				name = monitor.get("params", {}).get(key)
				if isinstance(name, str) and name in mapping:
					monitor["params"][key] = mapping[name]
		elif sprite_name is not None and sprite_name != vm["name"]:
			monitor["spriteName"] = None
			monitor["visible"] = False


def _materialize_banks(project: dict[str, Any]) -> None:
	stage = _target(project, "Stage")
	lists = _named_entries(stage.setdefault("lists", {}))
	if "MEM_DATA" not in lists and "MEM_DATA_0" not in lists:
		raise FullAbiBuildError("source Stage has no MEM_DATA list")
	old_name = "MEM_DATA" if "MEM_DATA" in lists else "MEM_DATA_0"
	base_id, base = lists[old_name]
	base[0] = "MEM_DATA_0"
	base[1] = [0] * BANK_WORDS
	for target in project["targets"]:
		for block in target.get("blocks", {}).values():
			for field in block.get("fields", {}).values():
				if isinstance(field, list) and len(field) > 1 and field[1] == base_id:
					field[0] = "MEM_DATA_0"
	for monitor in project.get("monitors", []):
		monitor["visible"] = False
		if monitor.get("opcode") == "data_listcontents" and monitor.get("params", {}).get("LIST") == "MEM_DATA":
			monitor["params"]["LIST"] = "MEM_DATA_0"
	for index in range(1, BANK_COUNT):
		entry_id = f"xe_mem_data_{index}"
		stage["lists"][entry_id] = [f"MEM_DATA_{index}", [0] * BANK_WORDS]
	for index in range(BANK_COUNT):
		stage["lists"][f"xe_mem_dirty_{index}"] = [f"MEM_DIRTY_{index}", []]
		stage["lists"][f"xe_mem_dirty_flags_{index}"] = [f"MEM_DIRTY_FLAGS_{index}", [0] * BANK_WORDS]
	from xe_lang.design_tokens import (
		BACKGROUND_COLOR_INDICES, BACKGROUND_NAMES, PALETTES,
		WINDOW_COLOR_SEMANTICS, WINDOW_MEASURE_PRIMITIVES,
	)
	token_names = [f"window.color.{name}" for name in WINDOW_COLOR_SEMANTICS]
	token_values: list[int | str] = list(WINDOW_COLOR_SEMANTICS.values())
	token_names.extend(f"window.measure.{name}" for name in WINDOW_MEASURE_PRIMITIVES)
	token_values.extend(WINDOW_MEASURE_PRIMITIVES.values())
	for palette_index, palette in enumerate(PALETTES):
		for color_index, color in enumerate(palette):
			token_names.append(f"palette.{palette_index}.color.{color_index}")
			token_values.append(color)
	for background_index, (name, color_index) in enumerate(zip(BACKGROUND_NAMES, BACKGROUND_COLOR_INDICES)):
		token_names.extend((f"background.{background_index}.name", f"background.{background_index}.color"))
		token_values.extend((name, color_index))
	design_lists = {
		"XE_DESIGN_TOKEN_NAMES": token_names,
		"XE_DESIGN_TOKEN_VALUES": token_values,
		"XE_DESIGN_STATE_NAMES": [
			"background", "palette", "theme_mode", "corner_style", "icon_size",
			"clock_format", "master_volume", "music_volume", "sfx_volume",
			"settings_enabled",
		],
		"XE_DESIGN_STATE_VALUES": [0, 0, 0, 0, 1, 1, 100, 100, 100, TRUE],
		"ABI_SCROLL_DELTAS": [],
		"ABI_SCROLL_AXES": [],
		"ABI_KEY_CODES": [],
	}
	stage_lists = _named_entries(stage["lists"])
	for name, initial in design_lists.items():
		if name not in stage_lists:
			stage["lists"][f"xe_{name.lower()}"] = [name, initial]

	variables = _named_entries(stage.setdefault("variables", {}))
	for name, initial in (
		("MEM_BANK_SIZE", BANK_WORDS),
		("MEM_BANK_COUNT", BANK_COUNT),
		("MEM_LOGICAL_LIMIT", LOGICAL_WORDS),
		("MEM_WORKING_BANKS", 5),
		("MEM_RESERVE_BANKS", 5),
		("MEM_RESERVE_ACTIVE", 0),
		("ABI_SCROLL_LAST_AXIS", 0),
		("ABI_SCROLL_AXIS_LATCH", 0),
	):
		if name in variables:
			variables[name][1][1] = initial
		else:
			stage["variables"]["xe_" + name.lower()] = [name, initial]
	axis_entry = _named_entries(stage["variables"])["ABI_SCROLL_AXIS_LATCH"]
	axis_monitor = next(
		(monitor for monitor in project.setdefault("monitors", []) if monitor.get("id") == axis_entry[0]),
		None,
	)
	if axis_monitor is None:
		axis_monitor = {
			"id": axis_entry[0], "mode": "slider", "opcode": "data_variable",
			"params": {"VARIABLE": "ABI_SCROLL_AXIS_LATCH"}, "spriteName": None,
			"value": 0, "width": 0, "height": 0, "x": 350, "y": 4,
			"visible": True, "sliderMin": 0, "sliderMax": 1, "isDiscrete": True,
		}
		project["monitors"].append(axis_monitor)
	else:
		axis_monitor["visible"] = True

	# The legacy initializer may rebuild bank zero; it must retain the full bank size.
	for target in project["targets"]:
		blocks = target.get("blocks", {})
		for block in blocks.values():
			if block.get("opcode") != "control_repeat":
				continue
			substack = block.get("inputs", {}).get("SUBSTACK", [])
			child = blocks.get(substack[1]) if len(substack) > 1 and isinstance(substack[1], str) else None
			if child and child.get("opcode") == "data_addtolist":
				field = child.get("fields", {}).get("LIST", [])
				if len(field) > 1 and field[1] == base_id:
					block["inputs"]["TIMES"] = [1, [4, str(BANK_WORDS)]]


def _lower_banked_memory_cached(project: dict[str, Any]) -> dict[str, dict[str, int]]:
	"""Type-preserving bank lowering using target-local read caches."""
	stage = _target(project, "Stage")
	bank_entries = _named_entries(stage.get("lists", {}))
	banks = [bank_entries[f"MEM_DATA_{index}"] for index in range(BANK_COUNT)]
	dirties = [bank_entries[f"MEM_DIRTY_{index}"] for index in range(BANK_COUNT)]
	dirty_flags = [bank_entries[f"MEM_DIRTY_FLAGS_{index}"] for index in range(BANK_COUNT)]
	base_id = banks[0][0]
	stage_variables = _named_entries(stage.get("variables", {}))
	logical = stage_variables["MEM_LOGICAL_LIMIT"]
	reserve_active = stage_variables["MEM_RESERVE_ACTIVE"]
	lowering_stats: dict[str, dict[str, int]] = {}

	reporter_opcodes = {
		"data_variable", "data_itemoflist", "data_lengthoflist", "data_listcontainsitem",
		"data_itemnumoflist", "argument_reporter_string_number", "argument_reporter_boolean",
		"sensing_answer", "sensing_mousex", "sensing_mousey", "sensing_mousedown",
		"sensing_keypressed", "sensing_timer", "sensing_current", "sensing_dayssince2000",
		"sensing_username", "sensing_loudness", "sensing_touchingobject", "sensing_touchingcolor",
		"sensing_coloristouchingcolor", "sensing_distanceto", "sensing_of",
		"motion_xposition", "motion_yposition", "motion_direction", "looks_size",
		"looks_costumenumbername", "looks_backdropnumbername", "sound_volume",
	}

	def is_reporter(block: dict[str, Any]) -> bool:
		opcode = str(block.get("opcode", ""))
		return opcode.startswith("operator_") or opcode in reporter_opcodes

	for target in project["targets"]:
		blocks = target.get("blocks", {})
		read_ids = {
			block_id for block_id, block in blocks.items()
			if block.get("opcode") == "data_itemoflist"
			and block.get("fields", {}).get("LIST", [None, None])[1] == base_id
		}
		read_slots = {
			block_id: slot
			for slot, block_id in enumerate(sorted(read_ids), start=1)
		}
		write_ids = [
			block_id for block_id, block in blocks.items()
			if block.get("opcode") == "data_replaceitemoflist"
			and block.get("fields", {}).get("LIST", [None, None])[1] == base_id
		]
		length_ids = [
			block_id for block_id, block in blocks.items()
			if block.get("opcode") == "data_lengthoflist"
			and block.get("fields", {}).get("LIST", [None, None])[1] == base_id
		]
		delete_ids = [
			block_id for block_id, block in blocks.items()
			if block.get("opcode") == "data_deletealloflist"
			and block.get("fields", {}).get("LIST", [None, None])[1] == base_id
		]
		add_ids = [
			block_id for block_id, block in blocks.items()
			if block.get("opcode") == "data_addtolist"
			and block.get("fields", {}).get("LIST", [None, None])[1] == base_id
		]
		if not (read_ids or write_ids or length_ids or delete_ids or add_ids):
			continue

		writer = _BlockWriter(target)
		cache_id = f"xemem_cache_{target.get('name', 'target').replace(' ', '_')}"
		target.setdefault("lists", {})[cache_id] = ["MEM_READ_CACHE", [""] * len(read_ids)]
		read_proc = "xemem read index %n to slot %n"
		read_args = ["xemem_read_index", "xemem_read_slot"]
		read_def, _ = writer.procedure_definition(read_proc, read_args, ["index", "slot"])
		read_body: list[str] = []
		clear = writer.new("data_replaceitemoflist", fields={"LIST": ["MEM_READ_CACHE", cache_id]})
		clear_slot = writer.arg("slot", clear)
		writer.blocks[clear]["inputs"] = {"INDEX": [3, clear_slot, [4, "1"]], "ITEM": [1, [10, ""]]}
		read_body.append(clear)
		for bank, (bank_id, entry) in enumerate(banks):
			branch = writer.new("control_if")
			low_arg = writer.arg("index", branch)
			low_lt = writer.operator(
				"operator_lt", branch,
				{"OPERAND1": [3, low_arg, [4, "0"]], "OPERAND2": [1, [4, str(bank * BANK_WORDS + 1)]]},
			)
			writer.blocks[low_arg]["parent"] = low_lt
			low_ok = writer.operator("operator_not", branch, {"OPERAND": [2, low_lt]})
			writer.blocks[low_lt]["parent"] = low_ok
			high_arg = writer.arg("index", branch)
			high_ok = writer.operator(
				"operator_lt", branch,
				{"OPERAND1": [3, high_arg, [4, "0"]], "OPERAND2": [1, [4, str((bank + 1) * BANK_WORDS + 1)]]},
			)
			writer.blocks[high_arg]["parent"] = high_ok
			condition = writer.operator("operator_and", branch, {"OPERAND1": [2, low_ok], "OPERAND2": [2, high_ok]})
			writer.blocks[low_ok]["parent"] = condition
			writer.blocks[high_ok]["parent"] = condition
			store = writer.new("data_replaceitemoflist", parent=branch, fields={"LIST": ["MEM_READ_CACHE", cache_id]})
			store_slot = writer.arg("slot", store)
			read = writer.new("data_itemoflist", parent=store, fields={"LIST": [entry[0], bank_id]})
			index_arg = writer.arg("index", read)
			if bank:
				index_value = writer.operator(
					"operator_subtract", read,
					{"NUM1": [3, index_arg, [4, "0"]], "NUM2": [1, [4, str(bank * BANK_WORDS)]]},
				)
				writer.blocks[index_arg]["parent"] = index_value
			else:
				index_value = index_arg
			writer.blocks[read]["inputs"] = {"INDEX": [3, index_value, [4, "1"]]}
			writer.blocks[store]["inputs"] = {"INDEX": [3, store_slot, [4, "1"]], "ITEM": [3, read, [10, ""]]}
			substack = store
			if bank >= 5:
				activate = writer.new(
					"data_setvariableto", parent=branch,
					fields={"VARIABLE": ["MEM_RESERVE_ACTIVE", reserve_active[0]]},
					inputs={"VALUE": [1, [4, "1"]]},
				)
				writer.blocks[activate]["next"] = store
				writer.blocks[store]["parent"] = activate
				substack = activate
			writer.blocks[branch]["inputs"] = {"CONDITION": [2, condition], "SUBSTACK": [2, substack]}
			read_body.append(branch)
		writer.blocks[read_def]["next"] = writer.chain(read_body, read_def)

		write_proc = "xemem write index %n value %s"
		write_args = ["xemem_write_index", "xemem_write_value"]
		write_def, _ = writer.procedure_definition(write_proc, write_args, ["index", "value"])
		write_body: list[str] = []
		for bank, (bank_id, entry) in enumerate(banks):
			branch = writer.new("control_if")
			low_arg = writer.arg("index", branch)
			low_lt = writer.operator(
				"operator_lt", branch,
				{"OPERAND1": [3, low_arg, [4, "0"]], "OPERAND2": [1, [4, str(bank * BANK_WORDS + 1)]]},
			)
			writer.blocks[low_arg]["parent"] = low_lt
			low_ok = writer.operator("operator_not", branch, {"OPERAND": [2, low_lt]})
			writer.blocks[low_lt]["parent"] = low_ok
			high_arg = writer.arg("index", branch)
			high_ok = writer.operator(
				"operator_lt", branch,
				{"OPERAND1": [3, high_arg, [4, "0"]], "OPERAND2": [1, [4, str((bank + 1) * BANK_WORDS + 1)]]},
			)
			writer.blocks[high_arg]["parent"] = high_ok
			condition = writer.operator("operator_and", branch, {"OPERAND1": [2, low_ok], "OPERAND2": [2, high_ok]})
			writer.blocks[low_ok]["parent"] = condition
			writer.blocks[high_ok]["parent"] = condition
			store = writer.new("data_replaceitemoflist", parent=branch, fields={"LIST": [entry[0], bank_id]})
			index_arg = writer.arg("index", store)
			if bank:
				index_value = writer.operator(
					"operator_subtract", store,
					{"NUM1": [3, index_arg, [4, "0"]], "NUM2": [1, [4, str(bank * BANK_WORDS)]]},
				)
				writer.blocks[index_arg]["parent"] = index_value
			else:
				index_value = index_arg
			value_arg = writer.arg("value", store)
			writer.blocks[store]["inputs"] = {"INDEX": [3, index_value, [4, "1"]], "ITEM": [3, value_arg, [10, ""]]}
			substack = store
			if bank >= 5:
				activate = writer.new(
					"data_setvariableto", parent=branch,
					fields={"VARIABLE": ["MEM_RESERVE_ACTIVE", reserve_active[0]]},
					inputs={"VALUE": [1, [4, "1"]]},
				)
				writer.blocks[activate]["next"] = store
				writer.blocks[store]["parent"] = activate
				substack = activate
			track = writer.new("control_if", parent=branch)
			track_index_arg = writer.arg("index", track)
			if bank:
				track_index = writer.operator(
					"operator_subtract", track,
					{"NUM1": [3, track_index_arg, [4, "0"]], "NUM2": [1, [4, str(bank * BANK_WORDS)]]},
				)
				writer.blocks[track_index_arg]["parent"] = track_index
			else:
				track_index = track_index_arg
			dirty_flag = writer.new(
				"data_itemoflist", parent=track,
				inputs={"INDEX": [3, track_index, [4, "1"]]},
				fields={"LIST": [dirty_flags[bank][1][0], dirty_flags[bank][0]]},
			)
			writer.blocks[track_index]["parent"] = dirty_flag
			new_dirty = writer.operator(
				"operator_equals", track,
				{"OPERAND1": [3, dirty_flag, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
			)
			writer.blocks[dirty_flag]["parent"] = new_dirty
			add_dirty = writer.new(
				"data_addtolist", parent=track,
				fields={"LIST": [dirties[bank][1][0], dirties[bank][0]]},
			)
			add_index_arg = writer.arg("index", add_dirty)
			if bank:
				add_index = writer.operator(
					"operator_subtract", add_dirty,
					{"NUM1": [3, add_index_arg, [4, "0"]], "NUM2": [1, [4, str(bank * BANK_WORDS)]]},
				)
				writer.blocks[add_index_arg]["parent"] = add_index
			else:
				add_index = add_index_arg
			writer.blocks[add_dirty]["inputs"] = {"ITEM": [3, add_index, [4, "0"]]}
			mark_dirty = writer.new(
				"data_replaceitemoflist", parent=track,
				fields={"LIST": [dirty_flags[bank][1][0], dirty_flags[bank][0]]},
			)
			mark_index_arg = writer.arg("index", mark_dirty)
			if bank:
				mark_index = writer.operator(
					"operator_subtract", mark_dirty,
					{"NUM1": [3, mark_index_arg, [4, "0"]], "NUM2": [1, [4, str(bank * BANK_WORDS)]]},
				)
				writer.blocks[mark_index_arg]["parent"] = mark_index
			else:
				mark_index = mark_index_arg
			writer.blocks[mark_dirty]["inputs"] = {"INDEX": [3, mark_index, [4, "1"]], "ITEM": [1, [4, "1"]]}
			track_first = writer.chain([add_dirty, mark_dirty], track)
			writer.blocks[track]["inputs"] = {"CONDITION": [2, new_dirty], "SUBSTACK": [2, track_first]}
			writer.blocks[track]["next"] = substack
			writer.blocks[substack]["parent"] = track
			substack = track
			writer.blocks[branch]["inputs"] = {"CONDITION": [2, condition], "SUBSTACK": [2, substack]}
			write_body.append(branch)
		writer.blocks[write_def]["next"] = writer.chain(write_body, write_def)
		reset_proc = "xemem reset dirty"
		if delete_ids:
			reset_def, _ = writer.procedure_definition(reset_proc, [], [])
			reset_body: list[str] = []
			for bank, (bank_id, entry) in enumerate(banks):
				dirty_id, dirty_entry = dirties[bank]
				repeat_dirty = writer.new("control_repeat_until")
				dirty_length = writer.new(
					"data_lengthoflist", parent=repeat_dirty,
					fields={"LIST": [dirty_entry[0], dirty_id]},
				)
				dirty_empty = writer.operator(
					"operator_equals", repeat_dirty,
					{"OPERAND1": [3, dirty_length, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
				)
				writer.blocks[dirty_length]["parent"] = dirty_empty
				clear_word = writer.new(
					"data_replaceitemoflist", parent=repeat_dirty,
					fields={"LIST": [entry[0], bank_id]},
				)
				dirty_slot = writer.new(
					"data_itemoflist", parent=clear_word,
					inputs={"INDEX": [1, [4, "1"]]},
					fields={"LIST": [dirty_entry[0], dirty_id]},
				)
				writer.blocks[clear_word]["inputs"] = {
					"INDEX": [3, dirty_slot, [4, "1"]], "ITEM": [1, [4, "0"]],
				}
				clear_flag = writer.new(
					"data_replaceitemoflist",
					fields={"LIST": [dirty_flags[bank][1][0], dirty_flags[bank][0]]},
				)
				flag_slot = writer.new(
					"data_itemoflist", parent=clear_flag,
					inputs={"INDEX": [1, [4, "1"]]},
					fields={"LIST": [dirty_entry[0], dirty_id]},
				)
				writer.blocks[clear_flag]["inputs"] = {
					"INDEX": [3, flag_slot, [4, "1"]], "ITEM": [1, [4, "0"]],
				}
				delete_dirty = writer.new(
					"data_deleteoflist", inputs={"INDEX": [1, [4, "1"]]},
					fields={"LIST": [dirty_entry[0], dirty_id]},
				)
				reset_first = writer.chain([clear_word, clear_flag, delete_dirty], repeat_dirty)
				writer.blocks[repeat_dirty]["inputs"] = {
					"CONDITION": [2, dirty_empty], "SUBSTACK": [2, reset_first],
				}
				reset_body.append(repeat_dirty)
			reset_reserve = writer.new(
				"data_setvariableto",
				fields={"VARIABLE": ["MEM_RESERVE_ACTIVE", reserve_active[0]]},
				inputs={"VALUE": [1, [4, "0"]]},
			)
			reset_body.append(reset_reserve)
			writer.blocks[reset_def]["next"] = writer.chain(reset_body, reset_def)

		def anchor_for(block_id: str) -> str:
			current = block_id
			while True:
				parent = blocks[current].get("parent")
				if not isinstance(parent, str) or parent not in blocks:
					raise FullAbiBuildError(f"cannot find evaluation anchor for {target.get('name')}:{block_id}")
				if not is_reporter(blocks[parent]):
					return parent
				current = parent

		groups: dict[str, list[str]] = {}
		for read_id in sorted(read_ids):
			groups.setdefault(anchor_for(read_id), [])

		def collect(block_id: str, wanted: set[str], output: list[str], seen: set[str]) -> None:
			if block_id in seen or block_id not in blocks:
				return
			seen.add(block_id)
			block = blocks[block_id]
			inputs = block.get("inputs", {})
			keys = list(inputs)
			if block.get("opcode") == "procedures_call":
				try:
					argument_ids = json.loads(block.get("mutation", {}).get("argumentids", "[]"))
				except (TypeError, json.JSONDecodeError):
					argument_ids = []
				keys = [key for key in argument_ids if key in inputs]
				keys.extend(key for key in inputs if key not in keys)
			for key in keys:
				input_value = inputs[key]
				if key.startswith("SUBSTACK") or not isinstance(input_value, list):
					continue
				for child in input_value[1:3]:
					if isinstance(child, str):
						collect(child, wanted, output, seen)
			if block_id in wanted:
				output.append(block_id)

		for anchor_id in sorted(groups):
			ordered: list[str] = []
			collect(anchor_id, read_ids, ordered, set())
			groups[anchor_id] = ordered
		if sum(map(len, groups.values())) != len(read_ids):
			raise FullAbiBuildError(f"read anchor traversal missed memory reporters in {target.get('name')}")

		def update_input_parents(input_value: Any, parent: str) -> None:
			if isinstance(input_value, list):
				for child in input_value[1:3]:
					if isinstance(child, str) and child in blocks:
						blocks[child]["parent"] = parent

		def clone_tree(block_id: str, parent: str) -> str:
			new_id = writer.new("operator_add", parent=parent)
			value = deepcopy(blocks[block_id])
			value["parent"] = parent
			value["next"] = None
			value["topLevel"] = False
			blocks[new_id] = value
			for input_value in value.get("inputs", {}).values():
				if isinstance(input_value, list):
					for index in range(1, min(3, len(input_value))):
						if isinstance(input_value[index], str):
							input_value[index] = clone_tree(input_value[index], new_id)
			return new_id

		def clone_call(call_id: str) -> str:
			new_id = writer.new("procedures_call")
			value = deepcopy(blocks[call_id])
			value["parent"] = None
			value["next"] = None
			blocks[new_id] = value
			for input_value in value.get("inputs", {}).values():
				if isinstance(input_value, list):
					for index in range(1, min(3, len(input_value))):
						if isinstance(input_value[index], str):
							input_value[index] = clone_tree(input_value[index], new_id)
			return new_id

		for anchor_id in sorted(groups):
			ordered = groups[anchor_id]
			calls: list[str] = []
			for read_id in ordered:
				slot = read_slots[read_id]
				read_block = blocks[read_id]
				index_input = deepcopy(read_block.get("inputs", {}).get("INDEX", [1, [4, "0"]]))
				call = writer.procedure_call(read_proc, read_args)
				writer.blocks[call]["inputs"] = {
					read_args[0]: index_input,
					read_args[1]: [1, [4, str(slot)]],
				}
				update_input_parents(index_input, call)
				parent = read_block.get("parent")
				read_block.clear()
				read_block.update({
					"opcode": "data_itemoflist", "next": None, "parent": parent,
					"inputs": {"INDEX": [1, [4, str(slot)]]},
					"fields": {"LIST": ["MEM_READ_CACHE", cache_id]},
					"shadow": False, "topLevel": False,
				})
				calls.append(call)
			old_parent = blocks[anchor_id].get("parent")
			if not isinstance(old_parent, str) or old_parent not in blocks:
				raise FullAbiBuildError(f"cannot splice memory reads before {anchor_id}")
			first = writer.chain(calls, old_parent)
			parent_block = blocks[old_parent]
			if parent_block.get("next") == anchor_id:
				parent_block["next"] = first
			else:
				replaced = False
				for input_value in parent_block.get("inputs", {}).values():
					if isinstance(input_value, list):
						for index in range(1, min(3, len(input_value))):
							if input_value[index] == anchor_id:
								input_value[index] = first
								replaced = True
								break
					if replaced:
						break
				if not replaced:
					raise FullAbiBuildError(f"cannot locate parent edge for {anchor_id}")
			blocks[calls[-1]]["next"] = anchor_id
			blocks[anchor_id]["parent"] = calls[-1]
			if blocks[anchor_id].get("opcode") == "control_repeat_until":
				refresh = [clone_call(call_id) for call_id in calls]
				substack = blocks[anchor_id].get("inputs", {}).get("SUBSTACK")
				if isinstance(substack, list) and len(substack) > 1 and isinstance(substack[1], str):
					tail = substack[1]
					while isinstance(blocks[tail].get("next"), str):
						tail = blocks[tail]["next"]
					writer.chain(refresh, tail)
					blocks[tail]["next"] = refresh[0]
				else:
					writer.chain(refresh, anchor_id)
					blocks[anchor_id].setdefault("inputs", {})["SUBSTACK"] = [2, refresh[0]]

		for block_id in write_ids:
			block = blocks[block_id]
			index_input = block.get("inputs", {}).get("INDEX", [1, [4, "0"]])
			item_input = block.get("inputs", {}).get("ITEM", [1, [4, "0"]])
			block["opcode"] = "procedures_call"
			block["inputs"] = {write_args[0]: index_input, write_args[1]: item_input}
			block["fields"] = {}
			block["mutation"] = {
				"tagName": "mutation", "children": [], "proccode": write_proc,
				"argumentids": _json_string(write_args), "warp": "true",
			}
			update_input_parents(index_input, block_id)
			update_input_parents(item_input, block_id)
		for block_id in length_ids:
			parent = blocks[block_id].get("parent")
			blocks[block_id] = {
				"opcode": "data_variable", "next": None, "parent": parent, "inputs": {},
				"fields": {"VARIABLE": ["MEM_LOGICAL_LIMIT", logical[0]]},
				"shadow": False, "topLevel": False,
			}

		for block_id in delete_ids:
			block = blocks[block_id]
			block["opcode"] = "procedures_call"
			block["inputs"] = {}
			block["fields"] = {}
			block["mutation"] = {
				"tagName": "mutation", "children": [], "proccode": reset_proc,
				"argumentids": "[]", "warp": "true",
			}
		for block_id in add_ids:
			block = blocks[block_id]
			parent = block.get("parent")
			if isinstance(parent, str) and blocks.get(parent, {}).get("opcode") == "control_repeat":
				blocks[parent].setdefault("inputs", {})["TIMES"] = [1, [4, "0"]]
			block["opcode"] = "control_wait"
			block["inputs"] = {"DURATION": [1, [4, "0"]]}
			block["fields"] = {}
			block.pop("mutation", None)

		target_name = str(target.get("name", "target"))
		cache = target.get("lists", {}).get(cache_id, [None, []])[1]
		if len(cache) != len(read_ids):
			raise FullAbiBuildError(f"memory cache size mismatch in {target_name}")
		if any(
			blocks[block_id].get("opcode") != "data_itemoflist"
			or blocks[block_id].get("fields", {}).get("LIST", [None, None])[1] != cache_id
			for block_id in read_ids
		):
			raise FullAbiBuildError(f"memory read reporter was not cache-lowered in {target_name}")
		if any(
			blocks[block_id].get("opcode") != "procedures_call"
			or blocks[block_id].get("mutation", {}).get("proccode") != write_proc
			for block_id in write_ids
		):
			raise FullAbiBuildError(f"memory write was not helper-lowered in {target_name}")
		refresh_reads = sum(
			len(ordered) for anchor_id, ordered in groups.items()
			if blocks[anchor_id].get("opcode") == "control_repeat_until"
		)
		read_call_count = sum(
			1 for block in blocks.values()
			if block.get("opcode") == "procedures_call"
			and block.get("mutation", {}).get("proccode") == read_proc
		)
		if read_call_count != len(read_ids) + refresh_reads:
			raise FullAbiBuildError(f"memory refresh call mismatch in {target_name}")
		lowering_stats[target_name] = {
			"reads": len(read_ids),
			"readAnchors": len(groups),
			"refreshReads": refresh_reads,
			"writes": len(write_ids),
			"cacheSlots": len(cache),
		}
	return lowering_stats


def _neutralize_preallocated_stack_reset(project: dict[str, Any]) -> None:
	stage = _target(project, "Stage")
	stack = _named_entries(stage.get("lists", {})).get("STACK_DATA")
	if stack is None:
		return
	stack_id, stack_entry = stack
	if len(stack_entry[1]) != BANK_WORDS:
		raise FullAbiBuildError(f"STACK_DATA must contain {BANK_WORDS} preallocated cells")
	for target in project["targets"]:
		blocks = target.get("blocks", {})
		for block in blocks.values():
			if block.get("fields", {}).get("LIST", [None, None])[1] != stack_id:
				continue
			if block.get("opcode") not in {"data_deletealloflist", "data_addtolist"}:
				continue
			if block.get("opcode") == "data_addtolist":
				parent = block.get("parent")
				if isinstance(parent, str) and blocks.get(parent, {}).get("opcode") == "control_repeat":
					blocks[parent].setdefault("inputs", {})["TIMES"] = [1, [4, "0"]]
			block["opcode"] = "control_wait"
			block["inputs"] = {"DURATION": [1, [4, "0"]]}
			block["fields"] = {}


def _handler_contracts() -> dict[int, Contract]:
	path = ROOT / "xe_lang" / "devices" / "syscalls.py"
	module = ast.parse(path.read_text(encoding="utf-8"))
	device = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "DeviceRuntime")
	functions = {node.name: node for node in device.body if isinstance(node, ast.FunctionDef)}
	constructor = functions["__init__"]
	handler_dict = next(
		node.value
		for node in ast.walk(constructor)
		if isinstance(node, ast.Assign)
		and isinstance(node.value, ast.Dict)
		and any(isinstance(target, ast.Attribute) and target.attr == "_handlers" for target in node.targets)
	)
	handlers = {
		SyscallID[key.attr]: value.attr
		for key, value in zip(handler_dict.keys, handler_dict.values)
		if isinstance(key, ast.Attribute) and isinstance(value, ast.Attribute)
	}

	def infer_args(name: str, seen: set[str] | None = None) -> int | None:
		seen = set() if seen is None else seen
		if name in seen or name not in functions:
			return None
		seen.add(name)
		function = functions[name]
		for call in ast.walk(function):
			if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
				continue
			if call.func.attr in {"_args", "_window_args", "_translate_draw"} and len(call.args) >= 3:
				count = call.args[2]
				if isinstance(count, ast.Constant) and isinstance(count.value, int):
					return count.value
		for call in ast.walk(function):
			if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
				value = infer_args(call.func.attr, seen)
				if value is not None:
					return value
		return 0

	def infer_result(name: str, seen: set[str] | None = None) -> str:
		seen = set() if seen is None else seen
		if name in seen or name not in functions:
			return "void"
		seen.add(name)
		function = functions[name]
		calls = [call for call in ast.walk(function) if isinstance(call, ast.Call)]
		if any(isinstance(call.func, ast.Attribute) and call.func.attr == "_push_string" for call in calls):
			return "string"
		if any(isinstance(call.func, ast.Attribute) and call.func.attr == "_push_bool" for call in calls):
			return "bool"
		if any(
			isinstance(call.func, ast.Attribute)
			and call.func.attr == "push"
			and isinstance(call.func.value, ast.Name)
			and call.func.value.id == "vm"
			for call in calls
		):
			return "number"
		for call in calls:
			if isinstance(call.func, ast.Attribute):
				value = infer_result(call.func.attr, seen)
				if value != "void":
					return value
		return "void"

	contracts: dict[int, Contract] = {}
	for syscall, handler in handlers.items():
		if int(syscall) < 100:
			continue
		args = infer_args(handler)
		if args is None:
			raise FullAbiBuildError(f"cannot infer argument count for {syscall.name}")
		result = infer_result(handler)
		backend = "project-local-graphics" if "GRAPHICS" in syscall.name else "project-local-state"
		if "COMPILER" in syscall.name:
			backend = "unsupported-host-compiler"
		elif "CURRENCY" in syscall.name:
			backend = "project-local-currency-rom"
		elif "AUDIO" in syscall.name:
			backend = "unsupported-dynamic-audio"
		elif "IMAGE" in syscall.name:
			backend = "unsupported-dynamic-image"
		elif syscall in {SyscallID.APP_OS_CLIPBOARD_READ, SyscallID.APP_OS_CLIPBOARD_WRITE}:
			backend = "exact-fail-closed"
		elif syscall.name.startswith("APP_OS_") and int(syscall) >= 160:
			backend = "project-local-vfs"
		contracts[int(syscall)] = Contract(args, result, backend)
	return contracts


def contracts() -> dict[int, Contract]:
	values = _handler_contracts()
	values.update({
		26: Contract(0, "number", "exact-prng", default_reporter="randf"),
		27: Contract(1, "void", "exact-prng"),
		28: Contract(0, "number", "exact-clock", default_reporter="hour"),
		29: Contract(0, "number", "exact-clock", default_reporter="minute"),
		54: Contract(0, "void", "merged-graphics"),
		56: Contract(6, "number", "merged-graphics", result_index=4),
		57: Contract(1, "number", "merged-graphics", default=6),
		58: Contract(1, "number", "merged-graphics", default=4),
	})
	defaults: dict[int, int | str] = {
		113: 0, 114: 0, 122: 480, 123: 360, 126: 0, 127: 0, 128: 0,
		130: 100, 132: 0, 134: 0, 138: TRUE, 139: 3, 140: 6,
		150: 0, 151: 0, 152: 0, 180: 100, 182: 100, 184: 0,
		186: 0, 188: 0, 190: 1, 192: 1, 194: TRUE, 196: TRUE,
		200: 4, 202: TRUE, 203: 0, 204: 1065353216, 205: 0, 209: 6, 213: 0, 214: 0, 215: 0,
		216: 0, 217: 0, 220: 0, 222: 0, 223: 0, 225: 0, 227: 0,
		234: 0, 236: TRUE, 240: 0, 242: TRUE, 255: 0, 261: 0, 262: 0, 263: 0, 264: 0,
		265: 1, 271: 0, 272: 0, 273: 0, 274: 0, 276: TRUE,
		281: 0, 282: 0, 283: 0, 284: 0, 285: 0, 286: 0, 287: 0,
		289: TRUE, 290: 0, 291: 0, 292: "", 293: 0,
	}
	strings = {
		201: "USD", 207: "", 211: "", 221: "Scratch compiler backend requires a precompiled workspace ROM",
		224: "", 228: "", 233: "", 235: "", 241: "", 245: "",
		266: "/", 291: "Scratch workspace execution is unavailable without a precompiled ROM", 292: "",
	}
	for syscall, default in defaults.items():
		if syscall in values:
			value = values[syscall]
			values[syscall] = Contract(value.args, value.result, value.backend, default, value.result_index, value.default_reporter)
	for syscall, default in strings.items():
		if syscall in values:
			value = values[syscall]
			values[syscall] = Contract(value.args, "string", value.backend, default, value.result_index, value.default_reporter)
	for syscall, reporter in {115: "mouse_x", 116: "mouse_y", 117: "mouse_down", 127: "mouse_x", 128: "mouse_y", 141: "ticks", 250: "year", 251: "month", 252: "date"}.items():
		if syscall in values:
			value = values[syscall]
			values[syscall] = Contract(value.args, value.result, value.backend, value.default, value.result_index, reporter)
	for syscall, index in {56: 4, 114: 5}.items():
		if syscall in values:
			value = values[syscall]
			values[syscall] = Contract(value.args, value.result, value.backend, value.default, index, value.default_reporter)
	if 276 in values:
		value = values[276]
		# UINT32_MAX is the ABI's signed -1 "unsupported accelerator" result.
		# File Explorer deliberately falls back to portable primitive drawing.
		values[276] = Contract(value.args, value.result, "unsupported-accelerator-fallback", 0xFFFFFFFF)
	if 248 in values:
		value = values[248]
		# Vanilla Scratch has no secondary-pointer press edge. File Explorer's
		# stationary primary-button hold is the portable context-action fallback.
		values[248] = Contract(value.args, value.result, "unsupported-secondary-pointer-fallback", 0)
	return values


class _BlockWriter:
	def __init__(self, target: dict[str, Any]) -> None:
		self.target = target
		self.blocks: dict[str, dict[str, Any]] = target.setdefault("blocks", {})
		self.counter = 0

	def new(self, opcode: str, *, parent: str | None = None, **extra: Any) -> str:
		while True:
			block_id = f"xeabi_{self.counter:06d}"
			self.counter += 1
			if block_id not in self.blocks:
				break
		block = {
			"opcode": opcode,
			"next": None,
			"parent": parent,
			"inputs": {},
			"fields": {},
			"shadow": False,
			"topLevel": False,
		}
		block.update(extra)
		self.blocks[block_id] = block
		return block_id

	def chain(self, block_ids: list[str], parent: str) -> str | None:
		if not block_ids:
			return None
		for index, block_id in enumerate(block_ids):
			block = self.blocks[block_id]
			block["parent"] = parent if index == 0 else block_ids[index - 1]
			block["next"] = block_ids[index + 1] if index + 1 < len(block_ids) else None
		return block_ids[0]

	def arg(self, name: str, parent: str) -> str:
		return self.new(
			"argument_reporter_string_number", parent=parent,
			fields={"VALUE": [name, None]}, shadow=False,
		)

	def variable(self, name: str, variable_id: str, parent: str) -> str:
		return self.new("data_variable", parent=parent, fields={"VARIABLE": [name, variable_id]})

	def list_item(self, name: str, list_id: str, index: int, parent: str) -> str:
		return self.new(
			"data_itemoflist", parent=parent,
			inputs={"INDEX": [1, [4, str(index)]]}, fields={"LIST": [name, list_id]},
		)

	def operator(self, opcode: str, parent: str, inputs: dict[str, Any], fields: dict[str, Any] | None = None) -> str:
		return self.new(opcode, parent=parent, inputs=inputs, fields=fields or {})

	def current(self, menu: str, parent: str) -> str:
		return self.new("sensing_current", parent=parent, fields={"CURRENTMENU": [menu, None]})

	def procedure_call(self, proccode: str, argument_ids: list[str], parent: str | None = None) -> str:
		return self.new(
			"procedures_call", parent=parent,
			mutation={
				"tagName": "mutation", "children": [], "proccode": proccode,
				"argumentids": _json_string(argument_ids), "warp": "true",
			},
		)

	def procedure_definition(self, proccode: str, argument_ids: list[str], argument_names: list[str]) -> tuple[str, str]:
		definition = self.new("procedures_definition", topLevel=True, x=2600, y=120)
		prototype = self.new(
			"procedures_prototype", parent=definition, shadow=True,
			mutation={
				"tagName": "mutation", "children": [], "proccode": proccode,
				"argumentids": _json_string(argument_ids),
				"argumentnames": _json_string(argument_names),
				"argumentdefaults": _json_string(["0" for _ in argument_ids]),
				"warp": "true",
			},
		)
		self.blocks[definition]["inputs"] = {"custom_block": [1, prototype]}
		for argument_id, name in zip(argument_ids, argument_names):
			shadow = self.arg(name, prototype)
			self.blocks[prototype]["inputs"][argument_id] = [1, shadow]
		return definition, prototype


def _install_scroll_input(
	writer: _BlockWriter,
	variables: dict[str, tuple[str, list[Any]]],
	lists: dict[str, tuple[str, list[Any]]],
) -> None:
	proccode = "xeabi enqueue scroll %n %n"
	argument_ids = ["xeabi_scroll_delta", "xeabi_scroll_axis"]
	definition, _ = writer.procedure_definition(proccode, argument_ids, ["delta", "axis"])
	add_delta = writer.new(
		"data_addtolist", fields={"LIST": ["ABI_SCROLL_DELTAS", lists["ABI_SCROLL_DELTAS"][0]]}
	)
	delta = writer.arg("delta", add_delta)
	writer.blocks[add_delta]["inputs"] = {"ITEM": [3, delta, [4, "0"]]}
	add_axis = writer.new(
		"data_addtolist", fields={"LIST": ["ABI_SCROLL_AXES", lists["ABI_SCROLL_AXES"][0]]}
	)
	axis = writer.arg("axis", add_axis)
	writer.blocks[add_axis]["inputs"] = {"ITEM": [3, axis, [4, "0"]]}
	cap = writer.new("control_if")
	length = writer.new(
		"data_lengthoflist", parent=cap,
		fields={"LIST": ["ABI_SCROLL_DELTAS", lists["ABI_SCROLL_DELTAS"][0]]},
	)
	overflow = writer.operator(
		"operator_gt", cap,
		{"OPERAND1": [3, length, [4, "0"]], "OPERAND2": [1, [4, "64"]]},
	)
	writer.blocks[length]["parent"] = overflow
	delete_delta = writer.new(
		"data_deleteoflist", parent=cap, inputs={"INDEX": [1, [4, "1"]]},
		fields={"LIST": ["ABI_SCROLL_DELTAS", lists["ABI_SCROLL_DELTAS"][0]]},
	)
	delete_axis = writer.new(
		"data_deleteoflist", inputs={"INDEX": [1, [4, "1"]]},
		fields={"LIST": ["ABI_SCROLL_AXES", lists["ABI_SCROLL_AXES"][0]]},
	)
	first_delete = writer.chain([delete_delta, delete_axis], cap)
	writer.blocks[cap]["inputs"] = {"CONDITION": [2, overflow], "SUBSTACK": [2, first_delete]}
	writer.blocks[definition]["next"] = writer.chain([add_delta, add_axis, cap], definition)

	latch_proc = "xeabi latch scroll frame"
	latch_definition, _ = writer.procedure_definition(latch_proc, [], [])
	reset_delta = writer.new(
		"data_setvariableto",
		fields={"VARIABLE": ["ABI_SCROLL_FRAME_DELTA", variables["ABI_SCROLL_FRAME_DELTA"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	reset_axis = writer.new(
		"data_setvariableto",
		fields={"VARIABLE": ["ABI_SCROLL_FRAME_AXIS", variables["ABI_SCROLL_FRAME_AXIS"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	reset_last_axis = writer.new(
		"data_setvariableto",
		fields={"VARIABLE": ["ABI_SCROLL_LAST_AXIS", variables["ABI_SCROLL_LAST_AXIS"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	latch_repeat = writer.new("control_repeat")
	latch_length = writer.new(
		"data_lengthoflist", parent=latch_repeat,
		fields={"LIST": ["ABI_SCROLL_DELTAS", lists["ABI_SCROLL_DELTAS"][0]]},
	)
	writer.blocks[latch_repeat]["inputs"] = {"TIMES": [3, latch_length, [4, "0"]]}
	accumulate_delta = writer.new(
		"data_changevariableby", parent=latch_repeat,
		fields={"VARIABLE": ["ABI_SCROLL_FRAME_DELTA", variables["ABI_SCROLL_FRAME_DELTA"][0]]},
	)
	queued_delta = writer.list_item(
		"ABI_SCROLL_DELTAS", lists["ABI_SCROLL_DELTAS"][0], 1, accumulate_delta
	)
	writer.blocks[accumulate_delta]["inputs"] = {"VALUE": [3, queued_delta, [4, "0"]]}
	copy_axis = writer.new(
		"data_setvariableto",
		fields={"VARIABLE": ["ABI_SCROLL_FRAME_AXIS", variables["ABI_SCROLL_FRAME_AXIS"][0]]},
	)
	queued_axis = writer.list_item(
		"ABI_SCROLL_AXES", lists["ABI_SCROLL_AXES"][0], 1, copy_axis
	)
	writer.blocks[copy_axis]["inputs"] = {"VALUE": [3, queued_axis, [4, "0"]]}
	copy_last_axis = writer.new(
		"data_setvariableto",
		fields={"VARIABLE": ["ABI_SCROLL_LAST_AXIS", variables["ABI_SCROLL_LAST_AXIS"][0]]},
	)
	frame_axis = writer.variable(
		"ABI_SCROLL_FRAME_AXIS", variables["ABI_SCROLL_FRAME_AXIS"][0], copy_last_axis
	)
	writer.blocks[copy_last_axis]["inputs"] = {"VALUE": [3, frame_axis, [4, "0"]]}
	latch_delete_delta = writer.new(
		"data_deleteoflist", inputs={"INDEX": [1, [4, "1"]]},
		fields={"LIST": ["ABI_SCROLL_DELTAS", lists["ABI_SCROLL_DELTAS"][0]]},
	)
	latch_delete_axis = writer.new(
		"data_deleteoflist", inputs={"INDEX": [1, [4, "1"]]},
		fields={"LIST": ["ABI_SCROLL_AXES", lists["ABI_SCROLL_AXES"][0]]},
	)
	latch_body = writer.chain(
		[accumulate_delta, copy_axis, copy_last_axis, latch_delete_delta, latch_delete_axis],
		latch_repeat,
	)
	writer.blocks[latch_repeat]["inputs"]["SUBSTACK"] = [2, latch_body]
	writer.blocks[latch_definition]["next"] = writer.chain(
		[reset_delta, reset_axis, reset_last_axis, latch_repeat], latch_definition
	)
	key_proc = "xeabi enqueue key %n"
	key_args = ["xeabi_key_code"]
	key_definition, _ = writer.procedure_definition(key_proc, key_args, ["code"])
	add_key = writer.new("data_addtolist", fields={"LIST": ["ABI_KEY_CODES", lists["ABI_KEY_CODES"][0]]})
	key_argument = writer.arg("code", add_key)
	writer.blocks[add_key]["inputs"] = {"ITEM": [3, key_argument, [4, "0"]]}
	cap_keys = writer.new("control_if")
	key_length = writer.new("data_lengthoflist", parent=cap_keys, fields={"LIST": ["ABI_KEY_CODES", lists["ABI_KEY_CODES"][0]]})
	key_overflow = writer.operator("operator_gt", cap_keys, {"OPERAND1": [3, key_length, [4, "0"]], "OPERAND2": [1, [4, "64"]]})
	writer.blocks[key_length]["parent"] = key_overflow
	delete_key = writer.new("data_deleteoflist", parent=cap_keys, inputs={"INDEX": [1, [4, "1"]]}, fields={"LIST": ["ABI_KEY_CODES", lists["ABI_KEY_CODES"][0]]})
	writer.blocks[cap_keys]["inputs"] = {"CONDITION": [2, key_overflow], "SUBSTACK": [2, delete_key]}
	writer.blocks[key_definition]["next"] = writer.chain([add_key, cap_keys], key_definition)

	for index, (key, delta_value, fixed_axis, key_code) in enumerate((
		("up arrow", 1, None, 4), ("down arrow", -1, None, 6),
		("left arrow", 1, 1, 3), ("right arrow", -1, 1, 5),
	)):
		hat = writer.new(
			"event_whenkeypressed", topLevel=True, x=2860, y=120 + index * 110,
			fields={"KEY_OPTION": [key, None]},
		)
		discriminate = writer.new("control_if_else", parent=hat)
		physical_key = writer.new("sensing_keypressed", parent=discriminate)
		key_menu = writer.new(
			"sensing_keyoptions", parent=physical_key, shadow=True,
			fields={"KEY_OPTION": [key, None]},
		)
		writer.blocks[physical_key]["inputs"] = {"KEY_OPTION": [1, key_menu]}
		key_call = writer.procedure_call(key_proc, key_args, discriminate)
		writer.blocks[key_call]["inputs"] = {key_args[0]: [1, [4, str(key_code)]]}
		call = writer.procedure_call(proccode, argument_ids, discriminate)
		writer.blocks[call]["inputs"][argument_ids[0]] = [1, [4, str(delta_value)]]
		if fixed_axis is None:
			axis_reporter = writer.variable(
				"ABI_SCROLL_AXIS_LATCH", variables["ABI_SCROLL_AXIS_LATCH"][0], call
			)
			writer.blocks[call]["inputs"][argument_ids[1]] = [3, axis_reporter, [4, "0"]]
		else:
			writer.blocks[call]["inputs"][argument_ids[1]] = [1, [4, str(fixed_axis)]]
		writer.blocks[discriminate]["inputs"] = {
			"CONDITION": [2, physical_key],
			"SUBSTACK": [2, key_call],
			"SUBSTACK2": [2, call],
		}
		writer.blocks[hat]["next"] = discriminate

	extra_keys = {
		"space": 32, "enter": 13, "backspace": 8, "delete": 127, "escape": 27,
		"+": 43, "-": 45, "=": 61,
		**{chr(value): value for value in range(48, 58)},
		**{chr(value): value for value in range(97, 123)},
	}
	for index, (key, key_code) in enumerate(sorted(extra_keys.items())):
		hat = writer.new(
			"event_whenkeypressed", topLevel=True, x=3140, y=120 + index * 90,
			fields={"KEY_OPTION": [key, None]},
		)
		call = writer.procedure_call(key_proc, key_args, hat)
		writer.blocks[call]["inputs"] = {key_args[0]: [1, [4, str(key_code)]]}
		writer.blocks[hat]["next"] = call


def _procedure_signature(target: dict[str, Any], proccode: str) -> list[str]:
	for block in target.get("blocks", {}).values():
		mutation = block.get("mutation", {})
		if block.get("opcode") == "procedures_prototype" and mutation.get("proccode") == proccode:
			try:
				return list(json.loads(mutation.get("argumentids", "[]")))
			except json.JSONDecodeError as error:
				raise FullAbiBuildError(f"invalid argument IDs for {proccode}") from error
	return []


def _dispatch_values(target: dict[str, Any]) -> set[int]:
	blocks = target.get("blocks", {})
	prototype_ids = {
		block_id for block_id, block in blocks.items()
		if block.get("opcode") == "procedures_prototype"
		and block.get("mutation", {}).get("proccode") in {"sys_dispatch %s", "sys_dispatch %n"}
	}
	definitions = [
		block for block in blocks.values()
		if block.get("opcode") == "procedures_definition"
		and block.get("inputs", {}).get("custom_block", [None, None])[1] in prototype_ids
	]
	if len(definitions) != 1:
		raise FullAbiBuildError(f"expected one sys_dispatch definition; found {len(definitions)}")
	seen: set[str] = set()
	stack = [definitions[0].get("next")]
	values: set[int] = set()
	while stack:
		block_id = stack.pop()
		if not isinstance(block_id, str) or block_id in seen or block_id not in blocks:
			continue
		seen.add(block_id)
		block = blocks[block_id]
		stack.append(block.get("next"))
		for value in block.get("inputs", {}).values():
			if isinstance(value, list):
				stack.extend(item for item in value[1:3] if isinstance(item, str))
		if block.get("opcode") != "operator_equals":
			continue
		for operand, other in (("OPERAND1", "OPERAND2"), ("OPERAND2", "OPERAND1")):
			value = block.get("inputs", {}).get(operand, [])
			reporter = blocks.get(value[1]) if len(value) > 1 and isinstance(value[1], str) else None
			if reporter and reporter.get("opcode") == "argument_reporter_string_number" and reporter.get("fields", {}).get("VALUE", [None])[0] == "id":
				literal = block.get("inputs", {}).get(other, [])
				if len(literal) > 1 and isinstance(literal[1], list) and len(literal[1]) > 1:
					try:
						values.add(int(str(literal[1][1])))
					except ValueError:
						pass
	return values


def _append_full_dispatch(project: dict[str, Any], abi: dict[int, Contract]) -> None:
	from xe_lang.devices.graphics import FONT_3X5, FONT_5X7
	from xe_lang.devices.currency_snapshot import (
		CURRENCY_CODES, DAILY_RATES, MONTHLY_RATES, WEEKLY_RATES,
	)

	vm = _target(project, "Xenon-131 VM")
	writer = _BlockWriter(vm)
	variables = _named_entries(vm.setdefault("variables", {}))
	lists = _named_entries(vm.setdefault("lists", {}))
	for name, initial in (
		("ABI_LAST_ID", 0), ("ABI_VOLUME", 100), ("ABI_BACKGROUND", 0),
		("ABI_PALETTE", 0), ("ABI_MUSIC_VOLUME", 100), ("ABI_SFX_VOLUME", 100),
		("ABI_THEME", 0), ("ABI_TRANSPARENCY", 0), ("ABI_CORNER", 0),
		("ABI_ICON_SIZE", 1), ("ABI_CLOCK_FORMAT", 1), ("ABI_SETTINGS_ENABLED", TRUE),
		("ABI_RETURN", 0), ("ABI_RETURN_TEXT", ""), ("ABI_TEMP_TEXT", ""), ("ABI_INDEX", 0),
		("ABI_ROW", 0), ("ABI_COUNT", 0), ("ABI_VFS_CONTEXT_ROW", 1),
		("ABI_SCROLL_FRAME_DELTA", 0), ("ABI_SCROLL_FRAME_AXIS", 0),
		("ABI_SLIDER_ACTIVE", 0), ("ABI_SLIDER_HANDLE", 0),
		("ABI_SLIDER_X", 0), ("ABI_SLIDER_Y", 0), ("ABI_SLIDER_WIDTH", 0),
		("ABI_CURRENCY_RATE", 0), ("ABI_CURRENCY_STATUS", 0),
		("ABI_CURRENCY_BASE", 0), ("ABI_CURRENCY_QUOTE", 0),
		("ABI_CURRENCY_RANGE", 0), ("ABI_CURRENCY_ROW_KIND", 0),
		("ABI_CURRENCY_FIRST_ROW", 0), ("ABI_CURRENCY_POINT_COUNT", 0),
		("ABI_VFS_REVISION", 1),
		("ABI_VFS_NEXT_ID", 9), ("ABI_VFS_CLOCK", 1), ("ABI_FH_NEXT_ID", 1),
		("ABI_VFS_PARENT_PATH", "."), ("ABI_VFS_BASENAME", ""),
		("ABI_VFS_SOURCE_PATH", ""), ("ABI_VFS_DEST_PATH", ""),
		("ABI_VFS_SOURCE_ROW", 0), ("ABI_VFS_DEST_PARENT_ROW", 0),
		("ABI_VFS_TARGET_ROW", 0), ("ABI_VFS_MUTATION_OK", 0),
		("ABI_HANDLE_INDEX", 0),
		("ABI_LAST_ERROR", ""),
		("ABI_PREVIEW_ACTIVE", 0), ("ABI_PREVIEW_BACKGROUND", 0),
		("ABI_PREVIEW_PALETTE", 0), ("ABI_PREVIEW_THEME", 0),
		("ABI_PREVIEW_CORNER", 0),
		("ABI_MOUSE_PREVIOUS", 0), ("ABI_MOUSE_PRESSED", 0),
		("ABI_MOUSE_RELEASED", 0),
		("ABI_DRAW_ORIGIN_X", 0), ("ABI_DRAW_ORIGIN_Y", 0),
		("ABI_DRAW_WIDTH", 480), ("ABI_DRAW_HEIGHT", 360), ("ABI_DRAW_SCALE", 1),
		("ABI_DIAGNOSTICS_ENABLED", 0),
		("ABI_COMPILER_ERROR", ""), ("ABI_COMPILER_LINE", 1),
		("ABI_COMPILER_COLUMN", 1), ("ABI_COMPILER_ASSEMBLY", ""),
		("ABI_COMPILER_BYTECODE_SIZE", 0),
	):
		entry_id = f"xeabi_var_{name.lower()}"
		vm["variables"][entry_id] = [name, initial]
	small_fallback = FONT_3X5["\x7f"]
	normal_fallback = FONT_5X7["\x7f"]
	small_advances = [(FONT_3X5.get(chr(code), small_fallback)[0] + 1) for code in range(128)]
	normal_advances = [(FONT_5X7.get(chr(code), normal_fallback)[0] + 1) for code in range(128)]
	def fp32_bits(value: float) -> int:
		return struct.unpack(">I", struct.pack(">f", float(value)))[0]

	def currency_cross_table(rows: tuple[tuple[str, tuple[float, ...]], ...]) -> list[int]:
		values: list[int] = []
		for _, rates in rows:
			for base_index, base_rate in enumerate(rates):
				for quote_index, quote_rate in enumerate(rates):
					values.append(
						fp32_bits(1.0 if base_index == quote_index else quote_rate / base_rate)
					)
		return values

	list_initial = {
		"ABI_ARGS": [], "ABI_CALL_LOG": [], "ABI_GFX_COMMANDS": [],
		"ABI_VFS_PATHS": [
			".", "Documents", "Pictures", "notes.xe", "readme.txt",
			"Documents/demo.xe", "Documents/guide.txt", "Pictures/icon.ximg",
		],
		"ABI_VFS_IDS": list(range(1, 9)),
		"ABI_VFS_KEYS": [
			".", "documents", "pictures", "notes.xe", "readme.txt",
			"documents/demo.xe", "documents/guide.txt", "pictures/icon.ximg",
		],
		"ABI_VFS_NAMES": [
			"", "Documents", "Pictures", "notes.xe", "readme.txt",
			"demo.xe", "guide.txt", "icon.ximg",
		],
		"ABI_VFS_TYPES": ["folder", "folder", "folder", "file", "file", "file", "file", "file"],
		"ABI_VFS_ALIVE": [1] * 8,
		"ABI_VFS_MTIME": [0] * 8,
		"ABI_VFS_CONTENTS": [
			"", "", "", "out << 131", "Xenon project-local Scratch VFS",
			"out << \"demo\"", "Open files by double-clicking them.", "XIMG placeholder",
		],
		"ABI_VFS_PARENTS": [0, 1, 1, 1, 1, 2, 2, 3],
		"ABI_CHILD_ROWS": [],
		"ABI_AUDIO_STATE": [], "ABI_IMAGE_ROM": [], "ABI_COMPILER_ROM": [],
		"ABI_WINDOW_IDS": [], "ABI_WINDOW_STATES": [],
		"ABI_HANDLE_ROWS": [], "ABI_HANDLE_MODES": [],
		"ABI_HANDLE_IDS": [], "ABI_HANDLE_CURSORS": [], "ABI_HANDLE_OPEN": [],
		"ABI_CURRENCY_CODES": list(CURRENCY_CODES),
		"ABI_CURRENCY_DAILY_DATES": [date for date, _ in DAILY_RATES],
		"ABI_CURRENCY_WEEKLY_DATES": [date for date, _ in WEEKLY_RATES],
		"ABI_CURRENCY_MONTHLY_DATES": [date for date, _ in MONTHLY_RATES],
		"ABI_CURRENCY_DAILY_CROSS": currency_cross_table(DAILY_RATES),
		"ABI_CURRENCY_WEEKLY_CROSS": currency_cross_table(WEEKLY_RATES),
		"ABI_CURRENCY_MONTHLY_CROSS": currency_cross_table(MONTHLY_RATES),
		"ABI_CURRENCY_RANGE_KIND": [0, 0, 0, 0, 1, 2],
		"ABI_CURRENCY_RANGE_FIRST": [44, 40, 38, 15, 0, 0],
		"ABI_CURRENCY_RANGE_COUNT": [2, 6, 8, 31, 31, 61],
		"ABI_HEX_DIGITS": list("0123456789ABCDEF"),
		"ABI_FONT_SMALL_ADVANCE": small_advances,
		"ABI_FONT_NORMAL_ADVANCE": normal_advances,
		"ABI_FONT_LARGE_ADVANCE": [value * 2 for value in normal_advances],
		"ABI_BOOL_SYSCALLS": [syscall for syscall, contract in sorted(abi.items()) if contract.result == "bool"],
	}
	for name, initial in list_initial.items():
		vm["lists"][f"xeabi_list_{name.lower()}"] = [name, initial]
	variables = _named_entries(vm["variables"])
	lists = _named_entries(vm["lists"])
	for name, entry in _named_entries(_target(project, "Stage").get("variables", {})).items():
		variables.setdefault(name, entry)
	for name, entry in _named_entries(_target(project, "Stage").get("lists", {})).items():
		lists.setdefault(name, entry)
	_install_scroll_input(writer, variables, lists)

	proc = "xeabi virtual %n %n %n %s %n"
	arg_ids = ["xeabi_id", "xeabi_argc", "xeabi_mode", "xeabi_default", "xeabi_index"]
	arg_names = ["id", "argc", "mode", "default", "result index"]
	definition, _ = writer.procedure_definition(proc, arg_ids, arg_names)
	body: list[str] = []

	set_last = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_LAST_ID", variables["ABI_LAST_ID"][0]]})
	set_last_arg = writer.arg("id", set_last)
	writer.blocks[set_last]["inputs"] = {"VALUE": [3, set_last_arg, [4, "0"]]}
	body.append(set_last)
	body.append(writer.new("data_deletealloflist", fields={"LIST": ["ABI_ARGS", lists["ABI_ARGS"][0]]}))

	repeat = writer.new("control_repeat")
	repeat_arg = writer.arg("argc", repeat)
	writer.blocks[repeat]["inputs"] = {"TIMES": [3, repeat_arg, [4, "0"]]}
	pop_call = writer.procedure_call("sys_pop", [], repeat)
	insert = writer.new(
		"data_insertatlist", parent=pop_call,
		inputs={"INDEX": [1, [4, "1"]]},
		fields={"LIST": ["ABI_ARGS", lists["ABI_ARGS"][0]]},
	)
	result_var = variables.get("sys_result")
	if result_var is None:
		raise FullAbiBuildError("VM target is missing sys_result")
	result_reporter = writer.variable("sys_result", result_var[0], insert)
	writer.blocks[insert]["inputs"]["ITEM"] = [3, result_reporter, [4, "0"]]
	writer.chain([pop_call, insert], repeat)
	writer.blocks[repeat]["inputs"]["SUBSTACK"] = [2, pop_call]
	body.append(repeat)
	for return_name in ("ABI_RETURN", "ABI_RETURN_TEXT"):
		set_return = writer.new("data_setvariableto", fields={"VARIABLE": [return_name, variables[return_name][0]]})
		return_arg = writer.arg("default", set_return)
		writer.blocks[set_return]["inputs"] = {"VALUE": [3, return_arg, [10, ""]]}
		body.append(set_return)

	log = writer.new("data_addtolist", fields={"LIST": ["ABI_CALL_LOG", lists["ABI_CALL_LOG"][0]]})
	log_arg = writer.arg("id", log)
	writer.blocks[log]["inputs"] = {"ITEM": [3, log_arg, [4, "0"]]}
	cap = writer.new("control_if")
	length = writer.new("data_lengthoflist", parent=cap, fields={"LIST": ["ABI_CALL_LOG", lists["ABI_CALL_LOG"][0]]})
	greater = writer.operator("operator_gt", cap, {"OPERAND1": [3, length, [4, "0"]], "OPERAND2": [1, [4, "1024"]]})
	writer.blocks[length]["parent"] = greater
	delete = writer.new("data_deleteoflist", parent=cap, inputs={"INDEX": [1, [4, "1"]]}, fields={"LIST": ["ABI_CALL_LOG", lists["ABI_CALL_LOG"][0]]})
	writer.blocks[cap]["inputs"] = {"CONDITION": [2, greater], "SUBSTACK": [2, delete]}
	diagnostics = writer.new("control_if")
	diagnostics_enabled = writer.variable(
		"ABI_DIAGNOSTICS_ENABLED", variables["ABI_DIAGNOSTICS_ENABLED"][0], diagnostics
	)
	diagnostics_condition = writer.operator(
		"operator_equals", diagnostics,
		{"OPERAND1": [3, diagnostics_enabled, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[diagnostics_enabled]["parent"] = diagnostics_condition
	diagnostics_first = writer.chain([log, cap], diagnostics)
	writer.blocks[diagnostics]["inputs"] = {
		"CONDITION": [2, diagnostics_condition], "SUBSTACK": [2, diagnostics_first],
	}
	body.append(diagnostics)

	state_setters = {
		27: ("PRNG_SEED", 0), 131: ("ABI_VOLUME", 7),
		133: ("ABI_BACKGROUND", 1), 135: ("ABI_PALETTE", 2),
		181: ("ABI_MUSIC_VOLUME", 8), 183: ("ABI_SFX_VOLUME", 9),
		185: ("ABI_THEME", 3), 187: ("ABI_TRANSPARENCY", 0),
		189: ("ABI_CORNER", 4), 191: ("ABI_ICON_SIZE", 5),
		193: ("ABI_CLOCK_FORMAT", 6), 195: ("ABI_SETTINGS_ENABLED", 10),
	}
	for syscall, (variable_name, design_index) in state_setters.items():
		if variable_name not in variables:
			continue
		branch = writer.new("control_if")
		id_arg = writer.arg("id", branch)
		equals = writer.operator("operator_equals", branch, {"OPERAND1": [3, id_arg, [4, "0"]], "OPERAND2": [1, [4, str(syscall)]]})
		writer.blocks[id_arg]["parent"] = equals
		setter = writer.new("data_setvariableto", parent=branch, fields={"VARIABLE": [variable_name, variables[variable_name][0]]})
		item = writer.list_item("ABI_ARGS", lists["ABI_ARGS"][0], 1, setter)
		writer.blocks[setter]["inputs"] = {"VALUE": [3, item, [4, "0"]]}
		statements = [setter]
		if design_index:
			mirror = writer.new(
				"data_replaceitemoflist",
				fields={"LIST": ["XE_DESIGN_STATE_VALUES", lists["XE_DESIGN_STATE_VALUES"][0]]},
				inputs={"INDEX": [1, [4, str(design_index)]]},
			)
			mirror_item = writer.list_item("ABI_ARGS", lists["ABI_ARGS"][0], 1, mirror)
			writer.blocks[mirror]["inputs"]["ITEM"] = [3, mirror_item, [4, "0"]]
			statements.append(mirror)
		first = writer.chain(statements, branch)
		writer.blocks[branch]["inputs"] = {"CONDITION": [2, equals], "SUBSTACK": [2, first]}
		body.append(branch)

	family_branches: dict[int, list[str]] = {}

	def syscall_branch(syscall: int, statements: list[str]) -> str:
		branch = writer.new("control_if")
		id_reporter = writer.arg("id", branch)
		equals = writer.operator(
			"operator_equals", branch,
			{"OPERAND1": [3, id_reporter, [4, "0"]], "OPERAND2": [1, [4, str(syscall)]]},
		)
		writer.blocks[id_reporter]["parent"] = equals
		first = writer.chain(statements, branch)
		writer.blocks[branch]["inputs"] = {"CONDITION": [2, equals]}
		if first is not None:
			writer.blocks[branch]["inputs"]["SUBSTACK"] = [2, first]
		family_branches.setdefault(syscall // 20, []).append(branch)
		return branch

	def argument_item(index: int, parent: str) -> str:
		return writer.list_item("ABI_ARGS", lists["ABI_ARGS"][0], index, parent)

	def read_descriptor(index: int) -> str:
		proccode = "sys_read_descriptor %n"
		argument_ids = _procedure_signature(vm, proccode)
		call = writer.procedure_call(proccode, argument_ids)
		item = argument_item(index, call)
		writer.blocks[call]["inputs"] = {argument_ids[0]: [3, item, [4, "0"]]}
		return call

	def set_return(reporter_factory: Callable[[str], str]) -> str:
		statement = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]})
		reporter = reporter_factory(statement)
		writer.blocks[statement]["inputs"] = {"VALUE": [3, reporter, [4, "0"]]}
		return statement

	def set_return_text(reporter_factory: Callable[[str], str]) -> str:
		statement = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_RETURN_TEXT", variables["ABI_RETURN_TEXT"][0]]})
		reporter = reporter_factory(statement)
		writer.blocks[statement]["inputs"] = {"VALUE": [3, reporter, [10, ""]]}
		return statement

	circle_proc = "xeabi draw circle %n %n %n %n"
	circle_args = ["xeabi_circle_x", "xeabi_circle_y", "xeabi_circle_radius", "xeabi_circle_color"]
	circle_definition, _ = writer.procedure_definition(circle_proc, circle_args, ["x", "y", "radius", "color"])
	set_circle_row = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]})
	circle_radius = writer.arg("radius", set_circle_row)
	negative_radius = writer.operator("operator_subtract", set_circle_row, {"NUM1": [1, [4, "0"]], "NUM2": [3, circle_radius, [4, "0"]]})
	writer.blocks[circle_radius]["parent"] = negative_radius
	writer.blocks[set_circle_row]["inputs"] = {"VALUE": [3, negative_radius, [4, "0"]]}
	circle_repeat = writer.new("control_repeat")
	repeat_radius = writer.arg("radius", circle_repeat)
	double_radius = writer.operator("operator_multiply", circle_repeat, {"NUM1": [3, repeat_radius, [4, "0"]], "NUM2": [1, [4, "2"]]})
	writer.blocks[repeat_radius]["parent"] = double_radius
	circle_times = writer.operator("operator_add", circle_repeat, {"NUM1": [3, double_radius, [4, "0"]], "NUM2": [1, [4, "1"]]})
	writer.blocks[double_radius]["parent"] = circle_times
	writer.blocks[circle_repeat]["inputs"] = {"TIMES": [3, circle_times, [4, "0"]]}
	set_half_width = writer.new("data_setvariableto", parent=circle_repeat, fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]})
	radius_a = writer.arg("radius", set_half_width)
	radius_b = writer.arg("radius", set_half_width)
	radius_squared = writer.operator("operator_multiply", set_half_width, {"NUM1": [3, radius_a, [4, "0"]], "NUM2": [3, radius_b, [4, "0"]]})
	writer.blocks[radius_a]["parent"] = radius_squared
	writer.blocks[radius_b]["parent"] = radius_squared
	row_a = writer.variable("ABI_ROW", variables["ABI_ROW"][0], set_half_width)
	row_b = writer.variable("ABI_ROW", variables["ABI_ROW"][0], set_half_width)
	row_squared = writer.operator("operator_multiply", set_half_width, {"NUM1": [3, row_a, [4, "0"]], "NUM2": [3, row_b, [4, "0"]]})
	writer.blocks[row_a]["parent"] = row_squared
	writer.blocks[row_b]["parent"] = row_squared
	remaining = writer.operator("operator_subtract", set_half_width, {"NUM1": [3, radius_squared, [4, "0"]], "NUM2": [3, row_squared, [4, "0"]]})
	writer.blocks[radius_squared]["parent"] = remaining
	writer.blocks[row_squared]["parent"] = remaining
	square_root = writer.operator("operator_mathop", set_half_width, {"NUM": [3, remaining, [4, "0"]]}, {"OPERATOR": ["sqrt", None]})
	writer.blocks[remaining]["parent"] = square_root
	floor_width = writer.operator("operator_mathop", set_half_width, {"NUM": [3, square_root, [4, "0"]]}, {"OPERATOR": ["floor", None]})
	writer.blocks[square_root]["parent"] = floor_width
	writer.blocks[set_half_width]["inputs"] = {"VALUE": [3, floor_width, [4, "0"]]}
	circle_rect_args = _procedure_signature(vm, "XGE::gfx_rect %s %s %s %s %s")
	draw_circle_edges: list[str] = []
	for operation in ("operator_subtract", "operator_add"):
		draw_circle_edge = writer.procedure_call("XGE::gfx_rect %s %s %s %s %s", circle_rect_args)
		circle_x = writer.arg("x", draw_circle_edge)
		circle_half_width = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], draw_circle_edge)
		edge_x = writer.operator(
			operation, draw_circle_edge,
			{"NUM1": [3, circle_x, [4, "0"]], "NUM2": [3, circle_half_width, [4, "0"]]},
		)
		writer.blocks[circle_x]["parent"] = edge_x
		writer.blocks[circle_half_width]["parent"] = edge_x
		circle_y = writer.arg("y", draw_circle_edge)
		circle_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], draw_circle_edge)
		edge_y = writer.operator(
			"operator_add", draw_circle_edge,
			{"NUM1": [3, circle_y, [4, "0"]], "NUM2": [3, circle_row, [4, "0"]]},
		)
		writer.blocks[circle_y]["parent"] = edge_y
		writer.blocks[circle_row]["parent"] = edge_y
		circle_color = writer.arg("color", draw_circle_edge)
		writer.blocks[draw_circle_edge]["inputs"] = {
			circle_rect_args[0]: [3, edge_x, [4, "0"]],
			circle_rect_args[1]: [3, edge_y, [4, "0"]],
			circle_rect_args[2]: [1, [4, "1"]], circle_rect_args[3]: [1, [4, "1"]],
			circle_rect_args[4]: [3, circle_color, [4, "0"]],
		}
		draw_circle_edges.append(draw_circle_edge)
	advance_circle_row = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
	circle_body = writer.chain([set_half_width, *draw_circle_edges, advance_circle_row], circle_repeat)
	writer.blocks[circle_repeat]["inputs"]["SUBSTACK"] = [2, circle_body]
	writer.blocks[circle_definition]["next"] = writer.chain([set_circle_row, circle_repeat], circle_definition)

	text_proc = "xeabi draw text value %n %n %s %n %n"
	text_args = ["xeabi_text_x", "xeabi_text_y", "xeabi_text_value", "xeabi_text_color", "xeabi_text_advance"]
	text_definition, _ = writer.procedure_definition(text_proc, text_args, ["x", "y", "text", "color", "advance"])
	set_text = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0]]})
	text_value = writer.arg("text", set_text)
	writer.blocks[set_text]["inputs"] = {"VALUE": [3, text_value, [10, ""]]}
	set_text_row = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
	set_text_cursor = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]})
	text_cursor_start = writer.arg("x", set_text_cursor)
	writer.blocks[set_text_cursor]["inputs"] = {"VALUE": [3, text_cursor_start, [4, "0"]]}
	text_repeat = writer.new("control_repeat")
	text_length_value = writer.variable("ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], text_repeat)
	text_length = writer.new("operator_length", parent=text_repeat, inputs={"STRING": [3, text_length_value, [10, ""]]})
	writer.blocks[text_length_value]["parent"] = text_length
	writer.blocks[text_repeat]["inputs"] = {"TIMES": [3, text_length, [4, "0"]]}
	character_args = _procedure_signature(vm, "XGE::gfx_character %n %n %n %n")
	draw_character = writer.procedure_call("XGE::gfx_character %n %n %n %n", character_args, text_repeat)
	character_x = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], draw_character)
	writer.blocks[draw_character]["inputs"][character_args[0]] = [3, character_x, [4, "0"]]
	text_y = writer.arg("y", draw_character)
	writer.blocks[draw_character]["inputs"][character_args[1]] = [3, text_y, [4, "0"]]
	letter_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], draw_character)
	letter_text = writer.variable("ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], draw_character)
	letter = writer.new("operator_letter_of", parent=draw_character, inputs={"LETTER": [3, letter_row, [4, "1"]], "STRING": [3, letter_text, [10, ""]]})
	writer.blocks[letter_row]["parent"] = letter
	writer.blocks[letter_text]["parent"] = letter
	charset = lists["charset"]
	character_index = writer.new("data_itemnumoflist", parent=draw_character, inputs={"ITEM": [3, letter, [10, ""]]}, fields={"LIST": ["charset", charset[0]]})
	writer.blocks[letter]["parent"] = character_index
	character_code = writer.operator("operator_subtract", draw_character, {"NUM1": [3, character_index, [4, "0"]], "NUM2": [1, [4, "1"]]})
	writer.blocks[character_index]["parent"] = character_code
	writer.blocks[draw_character]["inputs"][character_args[2]] = [3, character_code, [4, "0"]]
	text_color = writer.arg("color", draw_character)
	writer.blocks[draw_character]["inputs"][character_args[3]] = [3, text_color, [4, "0"]]
	advance_cursor = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]})
	advance_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], advance_cursor)
	advance_text = writer.variable("ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], advance_cursor)
	advance_letter = writer.new(
		"operator_letter_of", parent=advance_cursor,
		inputs={"LETTER": [3, advance_row, [4, "1"]], "STRING": [3, advance_text, [10, ""]]},
	)
	writer.blocks[advance_row]["parent"] = advance_letter
	writer.blocks[advance_text]["parent"] = advance_letter
	advance_character = writer.new(
		"data_itemnumoflist", parent=advance_cursor,
		inputs={"ITEM": [3, advance_letter, [10, ""]]}, fields={"LIST": ["charset", charset[0]]},
	)
	writer.blocks[advance_letter]["parent"] = advance_character
	advance_size = writer.arg("advance", advance_cursor)
	advance_small = writer.operator(
		"operator_lt", advance_cursor,
		{"OPERAND1": [3, advance_size, [4, "0"]], "OPERAND2": [1, [4, "5"]]},
	)
	writer.blocks[advance_size]["parent"] = advance_small
	advance_if = writer.new("control_if_else")
	small_width = writer.new(
		"data_itemoflist", parent=advance_cursor,
		inputs={"INDEX": [3, advance_character, [4, "1"]]},
		fields={"LIST": ["ABI_FONT_SMALL_ADVANCE", lists["ABI_FONT_SMALL_ADVANCE"][0]]},
	)
	writer.blocks[advance_character]["parent"] = small_width
	set_small_advance = writer.new(
		"data_setvariableto", parent=advance_if,
		fields={"VARIABLE": ["ABI_INDEX", variables["ABI_INDEX"][0]]},
		inputs={"VALUE": [3, small_width, [4, "4"]]},
	)
	writer.blocks[small_width]["parent"] = set_small_advance
	# Recompute the character index in the normal branch because a Scratch
	# reporter cannot be shared by two input trees.
	normal_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], advance_if)
	normal_text = writer.variable("ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], advance_if)
	normal_letter = writer.new(
		"operator_letter_of", parent=advance_if,
		inputs={"LETTER": [3, normal_row, [4, "1"]], "STRING": [3, normal_text, [10, ""]]},
	)
	writer.blocks[normal_row]["parent"] = normal_letter
	writer.blocks[normal_text]["parent"] = normal_letter
	normal_character = writer.new(
		"data_itemnumoflist", parent=advance_if,
		inputs={"ITEM": [3, normal_letter, [10, ""]]}, fields={"LIST": ["charset", charset[0]]},
	)
	writer.blocks[normal_letter]["parent"] = normal_character
	normal_width = writer.new(
		"data_itemoflist", parent=advance_if,
		inputs={"INDEX": [3, normal_character, [4, "1"]]},
		fields={"LIST": ["ABI_FONT_NORMAL_ADVANCE", lists["ABI_FONT_NORMAL_ADVANCE"][0]]},
	)
	writer.blocks[normal_character]["parent"] = normal_width
	set_normal_advance = writer.new(
		"data_setvariableto", parent=advance_if,
		fields={"VARIABLE": ["ABI_INDEX", variables["ABI_INDEX"][0]]},
		inputs={"VALUE": [3, normal_width, [4, "6"]]},
	)
	writer.blocks[normal_width]["parent"] = set_normal_advance
	writer.blocks[advance_small]["parent"] = advance_if
	writer.blocks[advance_if]["inputs"] = {
		"CONDITION": [2, advance_small], "SUBSTACK": [2, set_small_advance], "SUBSTACK2": [2, set_normal_advance],
	}
	advance_amount = writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], advance_cursor)
	writer.blocks[advance_cursor]["inputs"] = {"VALUE": [3, advance_amount, [4, "0"]]}
	advance_text_row = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
	text_body = writer.chain([draw_character, advance_if, advance_cursor, advance_text_row], text_repeat)
	writer.blocks[text_repeat]["inputs"]["SUBSTACK"] = [2, text_body]
	writer.blocks[text_definition]["next"] = writer.chain([set_text, set_text_row, set_text_cursor, text_repeat], text_definition)

	icon_proc = "xeabi draw icon %n %n %n %n %s %n"
	icon_args = [
		"xeabi_icon_x", "xeabi_icon_y", "xeabi_icon_width", "xeabi_icon_height",
		"xeabi_icon_pixels", "xeabi_icon_scale",
	]
	icon_definition, _ = writer.procedure_definition(
		icon_proc, icon_args, ["x", "y", "width", "height", "pixels", "scale"]
	)
	icon_set_text = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0]]})
	icon_text = writer.arg("pixels", icon_set_text)
	writer.blocks[icon_set_text]["inputs"] = {"VALUE": [3, icon_text, [10, ""]]}
	icon_set_row = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	icon_rows = writer.new("control_repeat")
	icon_height = writer.arg("height", icon_rows)
	writer.blocks[icon_rows]["inputs"] = {"TIMES": [3, icon_height, [4, "0"]]}
	icon_set_column = writer.new(
		"data_setvariableto", parent=icon_rows,
		fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	icon_columns = writer.new("control_repeat")
	icon_width = writer.arg("width", icon_columns)
	writer.blocks[icon_columns]["inputs"] = {"TIMES": [3, icon_width, [4, "0"]]}
	icon_set_character = writer.new(
		"data_setvariableto", parent=icon_columns,
		fields={"VARIABLE": ["ABI_RETURN_TEXT", variables["ABI_RETURN_TEXT"][0]]},
	)
	icon_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], icon_set_character)
	icon_width_for_index = writer.arg("width", icon_set_character)
	icon_row_offset = writer.operator(
		"operator_multiply", icon_set_character,
		{"NUM1": [3, icon_row, [4, "0"]], "NUM2": [3, icon_width_for_index, [4, "0"]]},
	)
	writer.blocks[icon_row]["parent"] = icon_row_offset
	writer.blocks[icon_width_for_index]["parent"] = icon_row_offset
	icon_column = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], icon_set_character)
	icon_linear = writer.operator(
		"operator_add", icon_set_character,
		{"NUM1": [3, icon_row_offset, [4, "0"]], "NUM2": [3, icon_column, [4, "0"]]},
	)
	writer.blocks[icon_row_offset]["parent"] = icon_linear
	writer.blocks[icon_column]["parent"] = icon_linear
	icon_one_based = writer.operator(
		"operator_add", icon_set_character,
		{"NUM1": [3, icon_linear, [4, "0"]], "NUM2": [1, [4, "1"]]},
	)
	writer.blocks[icon_linear]["parent"] = icon_one_based
	icon_pattern = writer.variable("ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], icon_set_character)
	icon_letter = writer.new(
		"operator_letter_of", parent=icon_set_character,
		inputs={"LETTER": [3, icon_one_based, [4, "1"]], "STRING": [3, icon_pattern, [10, ""]]},
	)
	writer.blocks[icon_one_based]["parent"] = icon_letter
	writer.blocks[icon_pattern]["parent"] = icon_letter
	writer.blocks[icon_set_character]["inputs"] = {"VALUE": [3, icon_letter, [10, ""]]}
	icon_set_color = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
	)
	icon_character = writer.variable("ABI_RETURN_TEXT", variables["ABI_RETURN_TEXT"][0], icon_set_color)
	icon_color_index = writer.new(
		"data_itemnumoflist", parent=icon_set_color,
		inputs={"ITEM": [3, icon_character, [10, ""]]},
		fields={"LIST": ["ABI_HEX_DIGITS", lists["ABI_HEX_DIGITS"][0]]},
	)
	writer.blocks[icon_character]["parent"] = icon_color_index
	writer.blocks[icon_set_color]["inputs"] = {"VALUE": [3, icon_color_index, [4, "0"]]}
	icon_draw_if = writer.new("control_if")
	icon_color = writer.variable("ABI_RETURN", variables["ABI_RETURN"][0], icon_draw_if)
	icon_visible = writer.operator(
		"operator_gt", icon_draw_if,
		{"OPERAND1": [3, icon_color, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
	)
	writer.blocks[icon_color]["parent"] = icon_visible
	icon_rect_args = _procedure_signature(vm, "XGE::gfx_rect %s %s %s %s %s")
	icon_rect = writer.procedure_call("XGE::gfx_rect %s %s %s %s %s", icon_rect_args, icon_draw_if)
	for position, (base_name, offset_name) in enumerate((("x", "ABI_COUNT"), ("y", "ABI_ROW"))):
		base = writer.arg(base_name, icon_rect)
		offset = writer.variable(offset_name, variables[offset_name][0], icon_rect)
		scale = writer.arg("scale", icon_rect)
		scaled_offset = writer.operator(
			"operator_multiply", icon_rect,
			{"NUM1": [3, offset, [4, "0"]], "NUM2": [3, scale, [4, "1"]]},
		)
		writer.blocks[offset]["parent"] = scaled_offset
		writer.blocks[scale]["parent"] = scaled_offset
		coordinate = writer.operator(
			"operator_add", icon_rect,
			{"NUM1": [3, base, [4, "0"]], "NUM2": [3, scaled_offset, [4, "0"]]},
		)
		writer.blocks[base]["parent"] = coordinate
		writer.blocks[scaled_offset]["parent"] = coordinate
		writer.blocks[icon_rect]["inputs"][icon_rect_args[position]] = [3, coordinate, [4, "0"]]
	for position in (2, 3):
		scale = writer.arg("scale", icon_rect)
		writer.blocks[icon_rect]["inputs"][icon_rect_args[position]] = [3, scale, [4, "1"]]
	icon_color_value = writer.variable("ABI_RETURN", variables["ABI_RETURN"][0], icon_rect)
	icon_palette_color = writer.operator(
		"operator_subtract", icon_rect,
		{"NUM1": [3, icon_color_value, [4, "0"]], "NUM2": [1, [4, "1"]]},
	)
	writer.blocks[icon_color_value]["parent"] = icon_palette_color
	writer.blocks[icon_rect]["inputs"][icon_rect_args[4]] = [3, icon_palette_color, [4, "0"]]
	writer.blocks[icon_draw_if]["inputs"] = {"CONDITION": [2, icon_visible], "SUBSTACK": [2, icon_rect]}
	icon_advance_column = writer.new(
		"data_changevariableby", fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	icon_column_body = writer.chain(
		[icon_set_character, icon_set_color, icon_draw_if, icon_advance_column], icon_columns
	)
	writer.blocks[icon_columns]["inputs"]["SUBSTACK"] = [2, icon_column_body]
	icon_advance_row = writer.new(
		"data_changevariableby", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	icon_row_body = writer.chain([icon_set_column, icon_columns, icon_advance_row], icon_rows)
	writer.blocks[icon_rows]["inputs"]["SUBSTACK"] = [2, icon_row_body]
	writer.blocks[icon_definition]["next"] = writer.chain(
		[icon_set_text, icon_set_row, icon_rows], icon_definition
	)

	# Window-begin drains and coalesces the event queue once. Every read during
	# that rendered frame observes the same signed delta and event axis.
	return_scroll = set_return(
		lambda parent: writer.variable(
			"ABI_SCROLL_FRAME_DELTA", variables["ABI_SCROLL_FRAME_DELTA"][0], parent
		)
	)
	syscall_branch(124, [return_scroll])
	consume_key = writer.new("control_if")
	key_length = writer.new("data_lengthoflist", parent=consume_key, fields={"LIST": ["ABI_KEY_CODES", lists["ABI_KEY_CODES"][0]]})
	has_key = writer.operator("operator_gt", consume_key, {"OPERAND1": [3, key_length, [4, "0"]], "OPERAND2": [1, [4, "0"]]})
	writer.blocks[key_length]["parent"] = has_key
	return_key = set_return(lambda parent: writer.list_item("ABI_KEY_CODES", lists["ABI_KEY_CODES"][0], 1, parent))
	delete_key = writer.new("data_deleteoflist", inputs={"INDEX": [1, [4, "1"]]}, fields={"LIST": ["ABI_KEY_CODES", lists["ABI_KEY_CODES"][0]]})
	key_body = writer.chain([return_key, delete_key], consume_key)
	writer.blocks[consume_key]["inputs"] = {"CONDITION": [2, has_key], "SUBSTACK": [2, key_body]}
	syscall_branch(121, [consume_key])
	def key_pressed(key: str, parent: str) -> str:
		pressed = writer.new("sensing_keypressed", parent=parent)
		menu = writer.new(
			"sensing_keyoptions", parent=pressed, shadow=True,
			fields={"KEY_OPTION": [key, None]},
		)
		writer.blocks[pressed]["inputs"] = {"KEY_OPTION": [1, menu]}
		return pressed

	modifier_reset = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	shift_branch = writer.new("control_if")
	axis_value = writer.variable(
		"ABI_SCROLL_FRAME_AXIS", variables["ABI_SCROLL_FRAME_AXIS"][0], shift_branch
	)
	horizontal_axis = writer.operator(
		"operator_equals", shift_branch,
		{"OPERAND1": [3, axis_value, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[axis_value]["parent"] = horizontal_axis
	physical_shift = key_pressed("shift", shift_branch)
	shift_active = writer.operator(
		"operator_or", shift_branch,
		{"OPERAND1": [2, horizontal_axis], "OPERAND2": [2, physical_shift]},
	)
	writer.blocks[horizontal_axis]["parent"] = shift_active
	writer.blocks[physical_shift]["parent"] = shift_active
	add_shift = writer.new(
		"data_changevariableby", parent=shift_branch,
		fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	writer.blocks[shift_branch]["inputs"] = {"CONDITION": [2, shift_active], "SUBSTACK": [2, add_shift]}
	control_branch = writer.new("control_if")
	physical_control = key_pressed("control", control_branch)
	portable_control = key_pressed("space", control_branch)
	control_active = writer.operator(
		"operator_or", control_branch,
		{"OPERAND1": [2, physical_control], "OPERAND2": [2, portable_control]},
	)
	writer.blocks[physical_control]["parent"] = control_active
	writer.blocks[portable_control]["parent"] = control_active
	add_control = writer.new(
		"data_changevariableby", parent=control_branch,
		fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
		inputs={"VALUE": [1, [4, "2"]]},
	)
	writer.blocks[control_branch]["inputs"] = {"CONDITION": [2, control_active], "SUBSTACK": [2, add_control]}
	syscall_branch(246, [modifier_reset, shift_branch, control_branch])
	for syscall, variable_name in ((184, "ABI_THEME"), (265, "ABI_VFS_REVISION")):
		explicit_state = set_return(
			lambda parent, variable_name=variable_name: writer.variable(
				variable_name, variables[variable_name][0], parent
			)
		)
		syscall_branch(syscall, [explicit_state])
	clipboard_read = writer.new(
		"data_setvariableto",
		fields={"VARIABLE": ["ABI_RETURN_TEXT", variables["ABI_RETURN_TEXT"][0]]},
		inputs={"VALUE": [1, [10, ""]]},
	)
	syscall_branch(292, [clipboard_read])
	clipboard_write = writer.new(
		"data_setvariableto",
		fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	syscall_branch(293, [clipboard_write])

	preview_branch = writer.new("control_if")
	preview_conditions: list[str] = []
	for argument_index, upper in ((1, 3), (2, 6), (3, 2), (4, 3)):
		low_argument = argument_item(argument_index, preview_branch)
		below_zero = writer.operator(
			"operator_lt", preview_branch,
			{"OPERAND1": [3, low_argument, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
		)
		writer.blocks[low_argument]["parent"] = below_zero
		nonnegative = writer.operator("operator_not", preview_branch, {"OPERAND": [2, below_zero]})
		writer.blocks[below_zero]["parent"] = nonnegative
		high_argument = argument_item(argument_index, preview_branch)
		below_upper = writer.operator(
			"operator_lt", preview_branch,
			{"OPERAND1": [3, high_argument, [4, "0"]], "OPERAND2": [1, [4, str(upper)]]},
		)
		writer.blocks[high_argument]["parent"] = below_upper
		valid_argument = writer.operator(
			"operator_and", preview_branch,
			{"OPERAND1": [2, nonnegative], "OPERAND2": [2, below_upper]},
		)
		writer.blocks[nonnegative]["parent"] = valid_argument
		writer.blocks[below_upper]["parent"] = valid_argument
		preview_conditions.append(valid_argument)
	preview_valid = preview_conditions[0]
	for next_condition in preview_conditions[1:]:
		combined = writer.operator(
			"operator_and", preview_branch,
			{"OPERAND1": [2, preview_valid], "OPERAND2": [2, next_condition]},
		)
		writer.blocks[preview_valid]["parent"] = combined
		writer.blocks[next_condition]["parent"] = combined
		preview_valid = combined
	preview_steps: list[str] = []
	for argument_index, variable_name, design_index in (
		(1, "ABI_PREVIEW_BACKGROUND", 1), (2, "ABI_PREVIEW_PALETTE", 2),
		(3, "ABI_PREVIEW_THEME", 3), (4, "ABI_PREVIEW_CORNER", 4),
	):
		setter = writer.new("data_setvariableto", fields={"VARIABLE": [variable_name, variables[variable_name][0]]})
		argument = argument_item(argument_index, setter)
		value: str = argument
		if argument_index == 4:
			value = writer.operator(
				"operator_gt", setter,
				{"OPERAND1": [3, argument, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
			)
			writer.blocks[argument]["parent"] = value
		writer.blocks[setter]["inputs"] = {"VALUE": [3, value, [4, "0"]]}
		mirror = writer.new(
			"data_replaceitemoflist",
			fields={"LIST": ["XE_DESIGN_STATE_VALUES", lists["XE_DESIGN_STATE_VALUES"][0]]},
			inputs={"INDEX": [1, [4, str(design_index)]]},
		)
		mirror_value = writer.variable(variable_name, variables[variable_name][0], mirror)
		writer.blocks[mirror]["inputs"]["ITEM"] = [3, mirror_value, [4, "0"]]
		preview_steps.extend([setter, mirror])
	preview_steps.extend([
		writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_PREVIEW_ACTIVE", variables["ABI_PREVIEW_ACTIVE"][0]]}, inputs={"VALUE": [1, [4, "1"]]}),
		writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]}, inputs={"VALUE": [1, [4, str(TRUE)]]}),
	])
	preview_first = writer.chain(preview_steps, preview_branch)
	writer.blocks[preview_branch]["inputs"] = {"CONDITION": [2, preview_valid], "SUBSTACK": [2, preview_first]}
	syscall_branch(294, [preview_branch])

	clear_preview_steps: list[str] = []
	for preview_name, committed_name, design_index in (
		("ABI_PREVIEW_BACKGROUND", "ABI_BACKGROUND", 1),
		("ABI_PREVIEW_PALETTE", "ABI_PALETTE", 2),
		("ABI_PREVIEW_THEME", "ABI_THEME", 3),
		("ABI_PREVIEW_CORNER", "ABI_CORNER", 4),
	):
		copy_value = writer.new("data_setvariableto", fields={"VARIABLE": [preview_name, variables[preview_name][0]]})
		committed = writer.variable(committed_name, variables[committed_name][0], copy_value)
		writer.blocks[copy_value]["inputs"] = {"VALUE": [3, committed, [4, "0"]]}
		mirror = writer.new(
			"data_replaceitemoflist",
			fields={"LIST": ["XE_DESIGN_STATE_VALUES", lists["XE_DESIGN_STATE_VALUES"][0]]},
			inputs={"INDEX": [1, [4, str(design_index)]]},
		)
		mirror_value = writer.variable(committed_name, variables[committed_name][0], mirror)
		writer.blocks[mirror]["inputs"]["ITEM"] = [3, mirror_value, [4, "0"]]
		clear_preview_steps.extend([copy_value, mirror])
	clear_preview_steps.append(
		writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_PREVIEW_ACTIVE", variables["ABI_PREVIEW_ACTIVE"][0]]}, inputs={"VALUE": [1, [4, "0"]]})
	)
	syscall_branch(295, clear_preview_steps)

	def translated_argument(index: int, axis: str, parent: str) -> str:
		argument = argument_item(index, parent)
		origin_name = "ABI_DRAW_ORIGIN_X" if axis == "x" else "ABI_DRAW_ORIGIN_Y"
		origin = writer.variable(origin_name, variables[origin_name][0], parent)
		translated = writer.operator(
			"operator_add", parent,
			{"NUM1": [3, argument, [4, "0"]], "NUM2": [3, origin, [4, "0"]]},
		)
		writer.blocks[argument]["parent"] = translated
		writer.blocks[origin]["parent"] = translated
		return translated

	def translated_argument_with_extent(index: int, extent_index: int, axis: str, parent: str) -> str:
		base = translated_argument(index, axis, parent)
		extent = argument_item(extent_index, parent)
		last = writer.operator(
			"operator_subtract", parent,
			{"NUM1": [3, extent, [4, "0"]], "NUM2": [1, [4, "1"]]},
		)
		writer.blocks[extent]["parent"] = last
		value = writer.operator(
			"operator_add", parent,
			{"NUM1": [3, base, [4, "0"]], "NUM2": [3, last, [4, "0"]]},
		)
		writer.blocks[base]["parent"] = value
		writer.blocks[last]["parent"] = value
		return value

	def xge_call(proccode: str, arguments: list[tuple[str, int | str]]) -> str:
		procedure_args = _procedure_signature(vm, proccode)
		if len(procedure_args) != len(arguments):
			raise FullAbiBuildError(f"renderer signature mismatch for {proccode}")
		call = writer.procedure_call(proccode, procedure_args)
		for argument_id, (kind, value) in zip(procedure_args, arguments):
			if kind == "argument":
				reporter = argument_item(int(value), call)
				writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
			elif kind in {"x", "y"}:
				reporter = translated_argument(int(value), kind, call)
				writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
			elif kind == "variable":
				reporter = writer.variable(str(value), variables[str(value)][0], call)
				writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
			else:
				writer.blocks[call]["inputs"][argument_id] = [1, [4, str(value)]]
		return call

	def design_token(index: int, parent: str) -> str:
		return writer.list_item(
			"XE_DESIGN_TOKEN_VALUES", lists["XE_DESIGN_TOKEN_VALUES"][0], index, parent
		)

	def pointer_coordinate(axis: str, parent: str) -> str:
		if axis == "x":
			mouse = writer.new("sensing_mousex", parent=parent)
			value = writer.operator(
				"operator_add", parent,
				{"NUM1": [3, mouse, [4, "0"]], "NUM2": [1, [4, "240"]]},
			)
			writer.blocks[mouse]["parent"] = value
			return value
		mouse = writer.new("sensing_mousey", parent=parent)
		value = writer.operator(
			"operator_subtract", parent,
			{"NUM1": [1, [4, "180"]], "NUM2": [3, mouse, [4, "0"]]},
		)
		writer.blocks[mouse]["parent"] = value
		return value

	def hit_test(x_index: int, y_index: int, width_index: int, height_index: int, parent: str) -> str:
		comparisons: list[str] = []
		for axis, position_index, extent_index in (
			("x", x_index, width_index), ("y", y_index, height_index),
		):
			pointer_low = pointer_coordinate(axis, parent)
			low_edge = translated_argument(position_index, axis, parent)
			below_low = writer.operator(
				"operator_lt", parent,
				{"OPERAND1": [3, pointer_low, [4, "0"]], "OPERAND2": [3, low_edge, [4, "0"]]},
			)
			writer.blocks[pointer_low]["parent"] = below_low
			writer.blocks[low_edge]["parent"] = below_low
			at_or_above = writer.operator("operator_not", parent, {"OPERAND": [2, below_low]})
			writer.blocks[below_low]["parent"] = at_or_above
			pointer_high = pointer_coordinate(axis, parent)
			high_edge = translated_argument(position_index, axis, parent)
			extent = argument_item(extent_index, parent)
			high = writer.operator(
				"operator_add", parent,
				{"NUM1": [3, high_edge, [4, "0"]], "NUM2": [3, extent, [4, "0"]]},
			)
			writer.blocks[high_edge]["parent"] = high
			writer.blocks[extent]["parent"] = high
			below_high = writer.operator(
				"operator_lt", parent,
				{"OPERAND1": [3, pointer_high, [4, "0"]], "OPERAND2": [3, high, [4, "0"]]},
			)
			writer.blocks[pointer_high]["parent"] = below_high
			writer.blocks[high]["parent"] = below_high
			axis_inside = writer.operator(
				"operator_and", parent,
				{"OPERAND1": [2, at_or_above], "OPERAND2": [2, below_high]},
			)
			writer.blocks[at_or_above]["parent"] = axis_inside
			writer.blocks[below_high]["parent"] = axis_inside
			comparisons.append(axis_inside)
		inside = writer.operator(
			"operator_and", parent,
			{"OPERAND1": [2, comparisons[0]], "OPERAND2": [2, comparisons[1]]},
		)
		writer.blocks[comparisons[0]]["parent"] = inside
		writer.blocks[comparisons[1]]["parent"] = inside
		return inside

	def draw_text_call(*, text_variable: str, advance: int, small: bool = False) -> str:
		call = writer.procedure_call(text_proc, text_args)
		for argument_id, (kind, value) in zip(text_args, (
			("x", 2), ("y", 3), ("variable", text_variable),
			("argument", 5), ("constant", advance),
		)):
			if kind in {"x", "y"}:
				reporter = translated_argument(int(value), kind, call)
				writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
			elif kind == "variable":
				reporter = writer.variable(str(value), variables[str(value)][0], call)
				writer.blocks[call]["inputs"][argument_id] = [3, reporter, [10, ""]]
			elif kind == "argument":
				reporter = argument_item(int(value), call)
				writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
			else:
				writer.blocks[call]["inputs"][argument_id] = [1, [4, str(value)]]
		return call

	# Every portable primitive used by the canonical Explorer is routed to the
	# merged Xenon renderer. The command list remains a bounded diagnostics trace.
	graphics_ids = sorted(value for value in abi if 100 <= value <= 129 or 142 <= value <= 146 or value in {208, 209, 246, 247, 248, 249, 253, 254, 270, 271, 272, 273, 274, 275, 276})
	for syscall in graphics_ids:
		trace = writer.new("control_if")
		trace_enabled = writer.variable("ABI_DIAGNOSTICS_ENABLED", variables["ABI_DIAGNOSTICS_ENABLED"][0], trace)
		trace_condition = writer.operator(
			"operator_equals", trace,
			{"OPERAND1": [3, trace_enabled, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
		)
		writer.blocks[trace_enabled]["parent"] = trace_condition
		commands = writer.new("data_addtolist", parent=trace, fields={"LIST": ["ABI_GFX_COMMANDS", lists["ABI_GFX_COMMANDS"][0]]})
		commands_arg = writer.arg("id", commands)
		writer.blocks[commands]["inputs"] = {"ITEM": [3, commands_arg, [4, "0"]]}
		trace_cap = writer.new("control_if")
		trace_length = writer.new("data_lengthoflist", parent=trace_cap, fields={"LIST": ["ABI_GFX_COMMANDS", lists["ABI_GFX_COMMANDS"][0]]})
		trace_overflow = writer.operator(
			"operator_gt", trace_cap,
			{"OPERAND1": [3, trace_length, [4, "0"]], "OPERAND2": [1, [4, "64"]]},
		)
		writer.blocks[trace_length]["parent"] = trace_overflow
		trace_delete = writer.new(
			"data_deleteoflist", parent=trace_cap, inputs={"INDEX": [1, [4, "1"]]},
			fields={"LIST": ["ABI_GFX_COMMANDS", lists["ABI_GFX_COMMANDS"][0]]},
		)
		writer.blocks[trace_cap]["inputs"] = {"CONDITION": [2, trace_overflow], "SUBSTACK": [2, trace_delete]}
		trace_first = writer.chain([commands, trace_cap], trace)
		writer.blocks[trace]["inputs"] = {"CONDITION": [2, trace_condition], "SUBSTACK": [2, trace_first]}
		statements = [trace]
		if syscall in {33, 103}:
			statements.append(writer.procedure_call("XGE::gfx_render", []))
		elif syscall in {32, 104}:
			statements.append(writer.procedure_call("XGE::gfx_clear_screen", []))
		elif syscall == 105:
			statements.append(xge_call("XGE::gfx_rect %s %s %s %s %s", [
				("x", 2), ("y", 3), ("constant", 1), ("constant", 1), ("argument", 4),
			]))
		elif syscall == 106:
			call = writer.procedure_call(circle_proc, circle_args)
			for argument_id, (kind, value) in zip(circle_args, (
				("x", 2), ("y", 3), ("argument", 4), ("argument", 5),
			)):
				reporter = translated_argument(int(value), kind, call) if kind in {"x", "y"} else argument_item(int(value), call)
				writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
			statements.append(call)
		elif syscall == 107:
			statements.append(xge_call("XGE::gfx_line %n %n %n %n %n", [
				("x", 2), ("y", 3), ("x", 4), ("y", 5), ("argument", 6),
			]))
		elif syscall == 108:
			rect_args = _procedure_signature(vm, "XGE::gfx_rect %s %s %s %s %s")
			for horizontal, far_edge in ((True, False), (True, True), (False, False), (False, True)):
				call = writer.procedure_call("XGE::gfx_rect %s %s %s %s %s", rect_args)
				if horizontal:
					x = translated_argument(2, "x", call)
					y = translated_argument_with_extent(3, 5, "y", call) if far_edge else translated_argument(3, "y", call)
					width = argument_item(4, call)
					height = None
				else:
					x = translated_argument_with_extent(2, 4, "x", call) if far_edge else translated_argument(2, "x", call)
					y = translated_argument(3, "y", call)
					width = None
					height = argument_item(5, call)
				writer.blocks[call]["inputs"][rect_args[0]] = [3, x, [4, "0"]]
				writer.blocks[call]["inputs"][rect_args[1]] = [3, y, [4, "0"]]
				writer.blocks[call]["inputs"][rect_args[2]] = [1, [4, "1"]] if width is None else [3, width, [4, "1"]]
				writer.blocks[call]["inputs"][rect_args[3]] = [1, [4, "1"]] if height is None else [3, height, [4, "1"]]
				color = argument_item(6, call)
				writer.blocks[call]["inputs"][rect_args[4]] = [3, color, [4, "0"]]
				statements.append(call)
		elif syscall == 109:
			statements.append(xge_call("XGE::gfx_rect %s %s %s %s %s", [
				("x", 2), ("y", 3), ("argument", 4), ("argument", 5), ("argument", 6),
			]))
		elif syscall in {110, 129}:
			statements.append(read_descriptor(4))
			call = writer.procedure_call(text_proc, text_args)
			for argument_id, (kind, value) in zip(text_args, (
				("x", 2), ("y", 3), ("variable", "sys_text"),
				("argument", 5), ("constant", 4 if syscall == 129 else 6),
			)):
				if kind in {"x", "y"}:
					reporter = translated_argument(int(value), kind, call)
					writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
				elif kind == "variable":
					reporter = writer.variable(str(value), variables[str(value)][0], call)
					writer.blocks[call]["inputs"][argument_id] = [3, reporter, [10, ""]]
				elif kind == "argument":
					reporter = argument_item(int(value), call)
					writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
				else:
					writer.blocks[call]["inputs"][argument_id] = [1, [4, str(value)]]
			statements.append(call)
		elif syscall in {111, 112, 143, 144}:
			call = writer.procedure_call(text_proc, text_args)
			for argument_id, (kind, value) in zip(text_args, (
				("x", 2), ("y", 3), ("argument", 4),
				("argument", 5), ("constant", 4 if syscall in {143, 144} else 6),
			)):
				if kind in {"x", "y"}:
					reporter = translated_argument(int(value), kind, call)
					writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
				elif kind == "argument":
					reporter = argument_item(int(value), call)
					writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
				else:
					writer.blocks[call]["inputs"][argument_id] = [1, [4, str(value)]]
			statements.append(call)
		elif syscall == 114:
			# Capture a specific slider on the press edge and continue updating it
			# while the pointer is held, even after it leaves the track bounds.
			statements.append(set_return(lambda parent: argument_item(5, parent)))
			release_capture = writer.new("control_if")
			mouse_released = writer.operator(
				"operator_not", release_capture,
				{"OPERAND": [2, writer.new("sensing_mousedown", parent=release_capture)]},
			)
			release_mouse = writer.blocks[mouse_released]["inputs"]["OPERAND"][1]
			writer.blocks[release_mouse]["parent"] = mouse_released
			clear_capture = writer.new(
				"data_setvariableto", parent=release_capture,
				fields={"VARIABLE": ["ABI_SLIDER_ACTIVE", variables["ABI_SLIDER_ACTIVE"][0]]},
				inputs={"VALUE": [1, [4, "0"]]},
			)
			writer.blocks[release_capture]["inputs"] = {
				"CONDITION": [2, mouse_released], "SUBSTACK": [2, clear_capture],
			}
			statements.append(release_capture)

			capture = writer.new("control_if")
			pressed_value = writer.variable(
				"ABI_MOUSE_PRESSED", variables["ABI_MOUSE_PRESSED"][0], capture
			)
			press_edge = writer.operator(
				"operator_equals", capture,
				{"OPERAND1": [3, pressed_value, [4, "0"]], "OPERAND2": [1, [4, str(TRUE)]]},
			)
			writer.blocks[pressed_value]["parent"] = press_edge
			hit_conditions: list[str] = [press_edge]
			for axis, position_index, extent_literal in (("x", 2, None), ("y", 3, 9)):
				pointer_low = pointer_coordinate(axis, capture)
				low_edge = translated_argument(position_index, axis, capture)
				below_low = writer.operator(
					"operator_lt", capture,
					{"OPERAND1": [3, pointer_low, [4, "0"]], "OPERAND2": [3, low_edge, [4, "0"]]},
				)
				writer.blocks[pointer_low]["parent"] = below_low
				writer.blocks[low_edge]["parent"] = below_low
				at_or_above = writer.operator("operator_not", capture, {"OPERAND": [2, below_low]})
				writer.blocks[below_low]["parent"] = at_or_above
				pointer_high = pointer_coordinate(axis, capture)
				high_edge = translated_argument(position_index, axis, capture)
				if extent_literal is None:
					extent_reporter = argument_item(4, capture)
					extent_input: list[Any] = [3, extent_reporter, [4, "0"]]
				else:
					extent_reporter = ""
					extent_input = [1, [4, str(extent_literal)]]
				high = writer.operator(
					"operator_add", capture,
					{"NUM1": [3, high_edge, [4, "0"]], "NUM2": extent_input},
				)
				writer.blocks[high_edge]["parent"] = high
				if extent_reporter:
					writer.blocks[extent_reporter]["parent"] = high
				below_high = writer.operator(
					"operator_lt", capture,
					{"OPERAND1": [3, pointer_high, [4, "0"]], "OPERAND2": [3, high, [4, "0"]]},
				)
				writer.blocks[pointer_high]["parent"] = below_high
				writer.blocks[high]["parent"] = below_high
				hit_conditions.extend((at_or_above, below_high))
			capture_condition = hit_conditions[0]
			for condition in hit_conditions[1:]:
				combined = writer.operator(
					"operator_and", capture,
					{"OPERAND1": [2, capture_condition], "OPERAND2": [2, condition]},
				)
				writer.blocks[capture_condition]["parent"] = combined
				writer.blocks[condition]["parent"] = combined
				capture_condition = combined
			capture_steps = [writer.new(
				"data_setvariableto",
				fields={"VARIABLE": ["ABI_SLIDER_ACTIVE", variables["ABI_SLIDER_ACTIVE"][0]]},
				inputs={"VALUE": [1, [4, "1"]]},
			)]
			for variable_name, argument_index in (
				("ABI_SLIDER_HANDLE", 1), ("ABI_SLIDER_X", 2),
				("ABI_SLIDER_Y", 3), ("ABI_SLIDER_WIDTH", 4),
			):
				store_capture = writer.new(
					"data_setvariableto", fields={"VARIABLE": [variable_name, variables[variable_name][0]]},
				)
				store_argument = argument_item(argument_index, store_capture)
				writer.blocks[store_capture]["inputs"] = {"VALUE": [3, store_argument, [4, "0"]]}
				capture_steps.append(store_capture)
			capture_first = writer.chain(capture_steps, capture)
			writer.blocks[capture]["inputs"] = {
				"CONDITION": [2, capture_condition], "SUBSTACK": [2, capture_first],
			}
			statements.append(capture)

			drag = writer.new("control_if")
			drag_conditions: list[str] = [writer.new("sensing_mousedown", parent=drag)]
			active_value = writer.variable("ABI_SLIDER_ACTIVE", variables["ABI_SLIDER_ACTIVE"][0], drag)
			active = writer.operator(
				"operator_equals", drag,
				{"OPERAND1": [3, active_value, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
			)
			writer.blocks[active_value]["parent"] = active
			drag_conditions.append(active)
			for variable_name, argument_index in (
				("ABI_SLIDER_HANDLE", 1), ("ABI_SLIDER_X", 2),
				("ABI_SLIDER_Y", 3), ("ABI_SLIDER_WIDTH", 4),
			):
				captured_value = writer.variable(variable_name, variables[variable_name][0], drag)
				current_value = argument_item(argument_index, drag)
				matches = writer.operator(
					"operator_equals", drag,
					{"OPERAND1": [3, captured_value, [4, "0"]], "OPERAND2": [3, current_value, [4, "0"]]},
				)
				writer.blocks[captured_value]["parent"] = matches
				writer.blocks[current_value]["parent"] = matches
				drag_conditions.append(matches)
			drag_condition = drag_conditions[0]
			for condition in drag_conditions[1:]:
				combined = writer.operator(
					"operator_and", drag,
					{"OPERAND1": [2, drag_condition], "OPERAND2": [2, condition]},
				)
				writer.blocks[drag_condition]["parent"] = combined
				writer.blocks[condition]["parent"] = combined
				drag_condition = combined
			set_drag_value = writer.new(
				"data_setvariableto", parent=drag,
				fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
			)
			stage_pointer = pointer_coordinate("x", set_drag_value)
			origin = writer.variable("ABI_DRAW_ORIGIN_X", variables["ABI_DRAW_ORIGIN_X"][0], set_drag_value)
			stage_offset = writer.operator(
				"operator_subtract", set_drag_value,
				{"NUM1": [3, stage_pointer, [4, "0"]], "NUM2": [3, origin, [4, "0"]]},
			)
			writer.blocks[stage_pointer]["parent"] = stage_offset
			writer.blocks[origin]["parent"] = stage_offset
			scale = writer.variable("ABI_DRAW_SCALE", variables["ABI_DRAW_SCALE"][0], set_drag_value)
			local_pointer = writer.operator(
				"operator_divide", set_drag_value,
				{"NUM1": [3, stage_offset, [4, "0"]], "NUM2": [3, scale, [4, "1"]]},
			)
			writer.blocks[stage_offset]["parent"] = local_pointer
			writer.blocks[scale]["parent"] = local_pointer
			slider_x = argument_item(2, set_drag_value)
			pointer_offset = writer.operator(
				"operator_subtract", set_drag_value,
				{"NUM1": [3, local_pointer, [4, "0"]], "NUM2": [3, slider_x, [4, "0"]]},
			)
			writer.blocks[local_pointer]["parent"] = pointer_offset
			writer.blocks[slider_x]["parent"] = pointer_offset
			maximum = argument_item(7, set_drag_value)
			minimum = argument_item(6, set_drag_value)
			value_range = writer.operator(
				"operator_subtract", set_drag_value,
				{"NUM1": [3, maximum, [4, "0"]], "NUM2": [3, minimum, [4, "0"]]},
			)
			writer.blocks[maximum]["parent"] = value_range
			writer.blocks[minimum]["parent"] = value_range
			numerator = writer.operator(
				"operator_multiply", set_drag_value,
				{"NUM1": [3, pointer_offset, [4, "0"]], "NUM2": [3, value_range, [4, "0"]]},
			)
			writer.blocks[pointer_offset]["parent"] = numerator
			writer.blocks[value_range]["parent"] = numerator
			width = argument_item(4, set_drag_value)
			denominator = writer.operator(
				"operator_subtract", set_drag_value,
				{"NUM1": [3, width, [4, "1"]], "NUM2": [1, [4, "1"]]},
			)
			writer.blocks[width]["parent"] = denominator
			ratio_value = writer.operator(
				"operator_divide", set_drag_value,
				{"NUM1": [3, numerator, [4, "0"]], "NUM2": [3, denominator, [4, "1"]]},
			)
			writer.blocks[numerator]["parent"] = ratio_value
			writer.blocks[denominator]["parent"] = ratio_value
			rounded = writer.operator(
				"operator_mathop", set_drag_value, {"NUM": [3, ratio_value, [4, "0"]]},
				{"OPERATOR": ["round", None]},
			)
			writer.blocks[ratio_value]["parent"] = rounded
			minimum_again = argument_item(6, set_drag_value)
			drag_value = writer.operator(
				"operator_add", set_drag_value,
				{"NUM1": [3, minimum_again, [4, "0"]], "NUM2": [3, rounded, [4, "0"]]},
			)
			writer.blocks[minimum_again]["parent"] = drag_value
			writer.blocks[rounded]["parent"] = drag_value
			writer.blocks[set_drag_value]["inputs"] = {"VALUE": [3, drag_value, [4, "0"]]}
			clamp_low = writer.new("control_if")
			return_low = writer.variable("ABI_RETURN", variables["ABI_RETURN"][0], clamp_low)
			minimum_low = argument_item(6, clamp_low)
			below_minimum = writer.operator(
				"operator_lt", clamp_low,
				{"OPERAND1": [3, return_low, [4, "0"]], "OPERAND2": [3, minimum_low, [4, "0"]]},
			)
			writer.blocks[return_low]["parent"] = below_minimum
			writer.blocks[minimum_low]["parent"] = below_minimum
			set_minimum = writer.new(
				"data_setvariableto", parent=clamp_low,
				fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
			)
			minimum_value = argument_item(6, set_minimum)
			writer.blocks[set_minimum]["inputs"] = {"VALUE": [3, minimum_value, [4, "0"]]}
			writer.blocks[clamp_low]["inputs"] = {
				"CONDITION": [2, below_minimum], "SUBSTACK": [2, set_minimum],
			}
			clamp_high = writer.new("control_if")
			maximum_high = argument_item(7, clamp_high)
			return_high = writer.variable("ABI_RETURN", variables["ABI_RETURN"][0], clamp_high)
			above_maximum = writer.operator(
				"operator_lt", clamp_high,
				{"OPERAND1": [3, maximum_high, [4, "0"]], "OPERAND2": [3, return_high, [4, "0"]]},
			)
			writer.blocks[maximum_high]["parent"] = above_maximum
			writer.blocks[return_high]["parent"] = above_maximum
			set_maximum = writer.new(
				"data_setvariableto", parent=clamp_high,
				fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
			)
			maximum_value = argument_item(7, set_maximum)
			writer.blocks[set_maximum]["inputs"] = {"VALUE": [3, maximum_value, [4, "0"]]}
			writer.blocks[clamp_high]["inputs"] = {
				"CONDITION": [2, above_maximum], "SUBSTACK": [2, set_maximum],
			}
			drag_first = writer.chain([set_drag_value, clamp_low, clamp_high], drag)
			writer.blocks[drag]["inputs"] = {
				"CONDITION": [2, drag_condition], "SUBSTACK": [2, drag_first],
			}
			statements.append(drag)

			track_args = _procedure_signature(vm, "XGE::gfx_rect %s %s %s %s %s")
			track = writer.procedure_call("XGE::gfx_rect %s %s %s %s %s", track_args)
			track_x = translated_argument(2, "x", track)
			track_y = translated_argument(3, "y", track)
			track_width = argument_item(4, track)
			track_color = design_token(9, track)
			for argument_id, reporter, shadow in zip(
				track_args,
				(track_x, track_y, track_width, None, track_color),
				([4, "0"], [4, "0"], [4, "0"], [4, "3"], [4, "0"]),
			):
				writer.blocks[track]["inputs"][argument_id] = [1, shadow] if reporter is None else [3, reporter, shadow]
			statements.append(track)
			thumb_args = _procedure_signature(vm, "XGE::gfx_rect %s %s %s %s %s")
			thumb = writer.procedure_call("XGE::gfx_rect %s %s %s %s %s", thumb_args)
			thumb_base_x = translated_argument(2, "x", thumb)
			value = writer.variable("ABI_RETURN", variables["ABI_RETURN"][0], thumb)
			minimum = argument_item(6, thumb)
			value_offset = writer.operator(
				"operator_subtract", thumb,
				{"NUM1": [3, value, [4, "0"]], "NUM2": [3, minimum, [4, "0"]]},
			)
			writer.blocks[value]["parent"] = value_offset
			writer.blocks[minimum]["parent"] = value_offset
			width = argument_item(4, thumb)
			scaled_value = writer.operator(
				"operator_multiply", thumb,
				{"NUM1": [3, value_offset, [4, "0"]], "NUM2": [3, width, [4, "0"]]},
			)
			writer.blocks[value_offset]["parent"] = scaled_value
			writer.blocks[width]["parent"] = scaled_value
			maximum = argument_item(7, thumb)
			minimum_again = argument_item(6, thumb)
			range_value = writer.operator(
				"operator_subtract", thumb,
				{"NUM1": [3, maximum, [4, "0"]], "NUM2": [3, minimum_again, [4, "0"]]},
			)
			writer.blocks[maximum]["parent"] = range_value
			writer.blocks[minimum_again]["parent"] = range_value
			thumb_offset = writer.operator(
				"operator_divide", thumb,
				{"NUM1": [3, scaled_value, [4, "0"]], "NUM2": [3, range_value, [4, "1"]]},
			)
			writer.blocks[scaled_value]["parent"] = thumb_offset
			writer.blocks[range_value]["parent"] = thumb_offset
			thumb_x = writer.operator(
				"operator_add", thumb,
				{"NUM1": [3, thumb_base_x, [4, "0"]], "NUM2": [3, thumb_offset, [4, "0"]]},
			)
			writer.blocks[thumb_base_x]["parent"] = thumb_x
			writer.blocks[thumb_offset]["parent"] = thumb_x
			thumb_y = translated_argument(3, "y", thumb)
			thumb_color = design_token(10, thumb)
			for argument_id, reporter, shadow in zip(
				thumb_args,
				(thumb_x, thumb_y, None, None, thumb_color),
				([4, "0"], [4, "0"], [4, "3"], [4, "7"], [4, "0"]),
			):
				writer.blocks[thumb]["inputs"][argument_id] = [1, shadow] if reporter is None else [3, reporter, shadow]
			statements.append(thumb)
		elif syscall == 145:
			set_button_color = writer.new(
				"data_setvariableto", fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
			)
			button_color = argument_item(7, set_button_color)
			writer.blocks[set_button_color]["inputs"] = {"VALUE": [3, button_color, [4, "0"]]}
			statements.append(set_button_color)
			hover_branch = writer.new("control_if")
			hover = hit_test(2, 3, 4, 5, hover_branch)
			set_hover_color = writer.new(
				"data_setvariableto", parent=hover_branch,
				fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
			)
			hover_color = design_token(7, set_hover_color)
			writer.blocks[set_hover_color]["inputs"] = {"VALUE": [3, hover_color, [4, "0"]]}
			pressed_branch = writer.new("control_if")
			mouse_down = writer.new("sensing_mousedown", parent=pressed_branch)
			set_pressed_color = writer.new(
				"data_setvariableto", parent=pressed_branch,
				fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
			)
			pressed_color = design_token(8, set_pressed_color)
			writer.blocks[set_pressed_color]["inputs"] = {"VALUE": [3, pressed_color, [4, "0"]]}
			writer.blocks[pressed_branch]["inputs"] = {"CONDITION": [2, mouse_down], "SUBSTACK": [2, set_pressed_color]}
			hover_first = writer.chain([set_hover_color, pressed_branch], hover_branch)
			writer.blocks[hover_branch]["inputs"] = {"CONDITION": [2, hover], "SUBSTACK": [2, hover_first]}
			statements.append(hover_branch)
			statements.append(xge_call("XGE::gfx_rect %s %s %s %s %s", [
				("x", 2), ("y", 3), ("argument", 4), ("argument", 5), ("variable", "ABI_COUNT"),
			]))
			statements.append(read_descriptor(6))
			button_text = writer.procedure_call(text_proc, text_args)
			for argument_id, (kind, value) in zip(text_args, (
				("x", 2), ("y", 3), ("variable", "sys_text"), ("token", 4), ("constant", 4),
			)):
				if kind in {"x", "y"}:
					reporter = translated_argument(int(value), kind, button_text)
					writer.blocks[button_text]["inputs"][argument_id] = [3, reporter, [4, "0"]]
				elif kind == "variable":
					reporter = writer.variable(str(value), variables[str(value)][0], button_text)
					writer.blocks[button_text]["inputs"][argument_id] = [3, reporter, [10, ""]]
				elif kind == "token":
					reporter = design_token(int(value), button_text)
					writer.blocks[button_text]["inputs"][argument_id] = [3, reporter, [4, "0"]]
				else:
					writer.blocks[button_text]["inputs"][argument_id] = [1, [4, str(value)]]
			statements.append(button_text)
			button_return = set_return(lambda parent: writer.operator(
				"operator_and", parent,
				{
					"OPERAND1": [2, hit_test(2, 3, 4, 5, parent)],
					"OPERAND2": [3, writer.variable("ABI_MOUSE_PRESSED", variables["ABI_MOUSE_PRESSED"][0], parent), [4, "0"]],
				},
			))
			return_and = writer.blocks[button_return]["inputs"]["VALUE"][1]
			return_hit = writer.blocks[return_and]["inputs"]["OPERAND1"][1]
			return_pressed = writer.blocks[return_and]["inputs"]["OPERAND2"][1]
			writer.blocks[return_hit]["parent"] = return_and
			writer.blocks[return_pressed]["parent"] = return_and
			statements.append(button_return)
		elif syscall in {125, 142, 254}:
			statements.append(xge_call("XGE::gfx_character %n %n %n %n", [
				("x", 2), ("y", 3), ("argument", 4), ("argument", 5),
			]))
		elif syscall in {146, 253}:
			statements.append(read_descriptor(6))
			call = writer.procedure_call(icon_proc, icon_args)
			for argument_id, (kind, value) in zip(icon_args, (
				("x", 2), ("y", 3), ("argument", 4), ("argument", 5),
				("variable", "sys_text"), ("argument", 7) if syscall == 146 else ("constant", 1),
			)):
				if kind in {"x", "y"}:
					reporter = translated_argument(int(value), kind, call)
					writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
				elif kind == "argument":
					reporter = argument_item(int(value), call)
					writer.blocks[call]["inputs"][argument_id] = [3, reporter, [4, "0"]]
				elif kind == "variable":
					reporter = writer.variable(str(value), variables[str(value)][0], call)
					writer.blocks[call]["inputs"][argument_id] = [3, reporter, [10, ""]]
				else:
					writer.blocks[call]["inputs"][argument_id] = [1, [4, str(value)]]
			statements.append(call)
		elif syscall == 209:
			def advance_statement(list_name: str) -> str:
				statement = writer.new(
					"data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
				)
				code = argument_item(1, statement)
				index = writer.operator(
					"operator_add", statement,
					{"NUM1": [3, code, [4, "0"]], "NUM2": [1, [4, "1"]]},
				)
				writer.blocks[code]["parent"] = index
				advance = writer.new(
					"data_itemoflist", parent=statement,
					inputs={"INDEX": [3, index, [4, "1"]]},
					fields={"LIST": [list_name, lists[list_name][0]]},
				)
				writer.blocks[index]["parent"] = advance
				writer.blocks[statement]["inputs"] = {"VALUE": [3, advance, [4, "0"]]}
				return statement

			font_branch = writer.new("control_if_else")
			font_size = argument_item(2, font_branch)
			font_large = writer.operator(
				"operator_gt", font_branch,
				{"OPERAND1": [3, font_size, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
			)
			writer.blocks[font_size]["parent"] = font_large
			font_small = writer.operator("operator_not", font_branch, {"OPERAND": [2, font_large]})
			writer.blocks[font_large]["parent"] = font_small
			small_advance = advance_statement("ABI_FONT_SMALL_ADVANCE")
			normal_branch = writer.new("control_if_else", parent=font_branch)
			normal_size = argument_item(2, normal_branch)
			is_large = writer.operator(
				"operator_gt", normal_branch,
				{"OPERAND1": [3, normal_size, [4, "0"]], "OPERAND2": [1, [4, "2"]]},
			)
			writer.blocks[normal_size]["parent"] = is_large
			normal_advance = advance_statement("ABI_FONT_NORMAL_ADVANCE")
			large_advance = advance_statement("ABI_FONT_LARGE_ADVANCE")
			writer.blocks[normal_branch]["inputs"] = {
				"CONDITION": [2, is_large], "SUBSTACK": [2, large_advance], "SUBSTACK2": [2, normal_advance],
			}
			writer.blocks[font_branch]["inputs"] = {
				"CONDITION": [2, font_small], "SUBSTACK": [2, small_advance], "SUBSTACK2": [2, normal_branch],
			}
			statements.append(font_branch)
		elif syscall == 276:
			statements.append(writer.new(
				"data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
				inputs={"VALUE": [1, [4, str(0xFFFFFFFF)]]},
			))
		syscall_branch(syscall, statements)

	cache = lists.get("MEM_READ_CACHE")
	if cache is None:
		raise FullAbiBuildError("bank-lowered VM target is missing MEM_READ_CACHE")
	window_cache_base = len(cache[1][1]) + 1
	cache[1][1].extend([""] * 8)
	window_slots = {
		"x": window_cache_base, "y": window_cache_base + 1,
		"width": window_cache_base + 2, "height": window_cache_base + 3,
		"title": window_cache_base + 4, "state": window_cache_base + 5,
		"handle": window_cache_base + 6, "scale": window_cache_base + 7,
	}
	memory_read_proc = "xemem read index %n to slot %n"
	memory_read_args = _procedure_signature(vm, memory_read_proc)

	def read_window_field(offset: int, slot: int) -> str:
		call = writer.procedure_call(memory_read_proc, memory_read_args)
		pointer = argument_item(1, call)
		index = writer.operator(
			"operator_add", call,
			{"NUM1": [3, pointer, [4, "0"]], "NUM2": [1, [4, str(offset + 1)]]},
		)
		writer.blocks[pointer]["parent"] = index
		writer.blocks[call]["inputs"] = {
			memory_read_args[0]: [3, index, [4, "0"]],
			memory_read_args[1]: [1, [4, str(slot)]],
		}
		return call

	def cached_window_value(name: str, parent: str) -> str:
		return writer.list_item("MEM_READ_CACHE", cache[0], window_slots[name], parent)

	window_reads = [read_window_field(offset, window_slots[name]) for offset, name in enumerate(
		("x", "y", "width", "height", "title", "state", "handle", "scale")
	)]
	set_origin_x = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_DRAW_ORIGIN_X", variables["ABI_DRAW_ORIGIN_X"][0]]},
	)
	window_x = cached_window_value("x", set_origin_x)
	border_x = design_token(12, set_origin_x)
	origin_x = writer.operator(
		"operator_add", set_origin_x,
		{"NUM1": [3, window_x, [4, "0"]], "NUM2": [3, border_x, [4, "0"]]},
	)
	writer.blocks[window_x]["parent"] = origin_x
	writer.blocks[border_x]["parent"] = origin_x
	writer.blocks[set_origin_x]["inputs"] = {"VALUE": [3, origin_x, [4, "0"]]}
	set_origin_y = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_DRAW_ORIGIN_Y", variables["ABI_DRAW_ORIGIN_Y"][0]]},
	)
	window_y = cached_window_value("y", set_origin_y)
	title_height = design_token(11, set_origin_y)
	origin_y = writer.operator(
		"operator_add", set_origin_y,
		{"NUM1": [3, window_y, [4, "0"]], "NUM2": [3, title_height, [4, "0"]]},
	)
	writer.blocks[window_y]["parent"] = origin_y
	writer.blocks[title_height]["parent"] = origin_y
	writer.blocks[set_origin_y]["inputs"] = {"VALUE": [3, origin_y, [4, "0"]]}
	set_content_width = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_DRAW_WIDTH", variables["ABI_DRAW_WIDTH"][0]]},
	)
	window_width = cached_window_value("width", set_content_width)
	border_width_a = design_token(12, set_content_width)
	border_width_b = design_token(12, set_content_width)
	double_border = writer.operator(
		"operator_add", set_content_width,
		{"NUM1": [3, border_width_a, [4, "0"]], "NUM2": [3, border_width_b, [4, "0"]]},
	)
	writer.blocks[border_width_a]["parent"] = double_border
	writer.blocks[border_width_b]["parent"] = double_border
	content_width = writer.operator(
		"operator_subtract", set_content_width,
		{"NUM1": [3, window_width, [4, "0"]], "NUM2": [3, double_border, [4, "0"]]},
	)
	writer.blocks[window_width]["parent"] = content_width
	writer.blocks[double_border]["parent"] = content_width
	writer.blocks[set_content_width]["inputs"] = {"VALUE": [3, content_width, [4, "0"]]}
	set_content_height = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_DRAW_HEIGHT", variables["ABI_DRAW_HEIGHT"][0]]},
	)
	window_height = cached_window_value("height", set_content_height)
	title_height_again = design_token(11, set_content_height)
	border_height = design_token(12, set_content_height)
	chrome_height = writer.operator(
		"operator_add", set_content_height,
		{"NUM1": [3, title_height_again, [4, "0"]], "NUM2": [3, border_height, [4, "0"]]},
	)
	writer.blocks[title_height_again]["parent"] = chrome_height
	writer.blocks[border_height]["parent"] = chrome_height
	content_height = writer.operator(
		"operator_subtract", set_content_height,
		{"NUM1": [3, window_height, [4, "0"]], "NUM2": [3, chrome_height, [4, "0"]]},
	)
	writer.blocks[window_height]["parent"] = content_height
	writer.blocks[chrome_height]["parent"] = content_height
	writer.blocks[set_content_height]["inputs"] = {"VALUE": [3, content_height, [4, "0"]]}
	set_draw_scale = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_DRAW_SCALE", variables["ABI_DRAW_SCALE"][0]]},
	)
	draw_scale = cached_window_value("scale", set_draw_scale)
	writer.blocks[set_draw_scale]["inputs"] = {"VALUE": [3, draw_scale, [4, "1"]]}
	window_draw_proc = "XGE::Draw Window | XY %s %s width %s height %s title address %s State %s"
	window_draw_args = _procedure_signature(vm, window_draw_proc)
	draw_window = writer.procedure_call(window_draw_proc, window_draw_args)
	for argument_id, field_name in zip(
		window_draw_args, ("x", "y", "width", "height", "title", "state"),
	):
		field_value = cached_window_value(field_name, draw_window)
		writer.blocks[draw_window]["inputs"][argument_id] = [3, field_value, [4, "0"]]
	window_setup = [
		*window_reads, draw_window, set_origin_x, set_origin_y,
		set_content_width, set_content_height, set_draw_scale,
	]

	window_ids = lists["ABI_WINDOW_IDS"]
	window_states = lists["ABI_WINDOW_STATES"]
	reset_pressed = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_MOUSE_PRESSED", variables["ABI_MOUSE_PRESSED"][0]]}, inputs={"VALUE": [1, [4, "0"]]})
	reset_released = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_MOUSE_RELEASED", variables["ABI_MOUSE_RELEASED"][0]]}, inputs={"VALUE": [1, [4, "0"]]})
	sample_mouse = writer.new("control_if_else")
	mouse_down = writer.new("sensing_mousedown", parent=sample_mouse)
	pressed_if = writer.new("control_if", parent=sample_mouse)
	pressed_previous = writer.variable("ABI_MOUSE_PREVIOUS", variables["ABI_MOUSE_PREVIOUS"][0], pressed_if)
	pressed_condition = writer.operator("operator_equals", pressed_if, {"OPERAND1": [3, pressed_previous, [4, "0"]], "OPERAND2": [1, [4, "0"]]})
	writer.blocks[pressed_previous]["parent"] = pressed_condition
	mark_pressed = writer.new("data_setvariableto", parent=pressed_if, fields={"VARIABLE": ["ABI_MOUSE_PRESSED", variables["ABI_MOUSE_PRESSED"][0]]}, inputs={"VALUE": [1, [4, str(TRUE)]]})
	writer.blocks[pressed_if]["inputs"] = {"CONDITION": [2, pressed_condition], "SUBSTACK": [2, mark_pressed]}
	mark_down = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_MOUSE_PREVIOUS", variables["ABI_MOUSE_PREVIOUS"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
	down_first = writer.chain([pressed_if, mark_down], sample_mouse)
	released_if = writer.new("control_if", parent=sample_mouse)
	released_previous = writer.variable("ABI_MOUSE_PREVIOUS", variables["ABI_MOUSE_PREVIOUS"][0], released_if)
	released_condition = writer.operator("operator_equals", released_if, {"OPERAND1": [3, released_previous, [4, "0"]], "OPERAND2": [1, [4, "1"]]})
	writer.blocks[released_previous]["parent"] = released_condition
	mark_released = writer.new("data_setvariableto", parent=released_if, fields={"VARIABLE": ["ABI_MOUSE_RELEASED", variables["ABI_MOUSE_RELEASED"][0]]}, inputs={"VALUE": [1, [4, str(TRUE)]]})
	writer.blocks[released_if]["inputs"] = {"CONDITION": [2, released_condition], "SUBSTACK": [2, mark_released]}
	mark_up = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_MOUSE_PREVIOUS", variables["ABI_MOUSE_PREVIOUS"][0]]}, inputs={"VALUE": [1, [4, "0"]]})
	up_first = writer.chain([released_if, mark_up], sample_mouse)
	writer.blocks[sample_mouse]["inputs"] = {"CONDITION": [2, mouse_down], "SUBSTACK": [2, down_first], "SUBSTACK2": [2, up_first]}
	begin_window = writer.new("control_if")
	begin_pointer = argument_item(1, begin_window)
	window_contains = writer.new(
		"data_listcontainsitem", parent=begin_window,
		inputs={"ITEM": [3, begin_pointer, [4, "0"]]}, fields={"LIST": ["ABI_WINDOW_IDS", window_ids[0]]},
	)
	writer.blocks[begin_pointer]["parent"] = window_contains
	window_missing = writer.operator("operator_not", begin_window, {"OPERAND": [2, window_contains]})
	writer.blocks[window_contains]["parent"] = window_missing
	add_window = writer.new("data_addtolist", parent=begin_window, fields={"LIST": ["ABI_WINDOW_IDS", window_ids[0]]})
	add_pointer = argument_item(1, add_window)
	writer.blocks[add_window]["inputs"] = {"ITEM": [3, add_pointer, [4, "0"]]}
	add_state = writer.new("data_addtolist", fields={"LIST": ["ABI_WINDOW_STATES", window_states[0]]}, inputs={"ITEM": [1, [4, "0"]]})
	begin_first = writer.chain([add_window, add_state], begin_window)
	writer.blocks[begin_window]["inputs"] = {"CONDITION": [2, window_missing], "SUBSTACK": [2, begin_first]}
	latch_scroll_frame = writer.procedure_call("xeabi latch scroll frame", [])
	syscall_branch(102, [
		latch_scroll_frame, *window_setup,
		reset_pressed, reset_released, sample_mouse, begin_window,
	])
	for syscall, variable_name in ((118, "ABI_MOUSE_PRESSED"), (119, "ABI_MOUSE_RELEASED")):
		mouse_edge = set_return(lambda parent, variable_name=variable_name: writer.variable(variable_name, variables[variable_name][0], parent))
		syscall_branch(syscall, [mouse_edge])
	mouse_down_return = set_return(lambda parent: writer.new("sensing_mousedown", parent=parent))
	syscall_branch(117, [mouse_down_return])
	def local_pointer_return(axis: str) -> str:
		statement = writer.new(
			"data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
		)
		stage_coordinate = pointer_coordinate(axis, statement)
		origin_name = "ABI_DRAW_ORIGIN_X" if axis == "x" else "ABI_DRAW_ORIGIN_Y"
		origin = writer.variable(origin_name, variables[origin_name][0], statement)
		offset = writer.operator(
			"operator_subtract", statement,
			{"NUM1": [3, stage_coordinate, [4, "0"]], "NUM2": [3, origin, [4, "0"]]},
		)
		writer.blocks[stage_coordinate]["parent"] = offset
		writer.blocks[origin]["parent"] = offset
		scale = writer.variable("ABI_DRAW_SCALE", variables["ABI_DRAW_SCALE"][0], statement)
		coordinate = writer.operator(
			"operator_divide", statement,
			{"NUM1": [3, offset, [4, "0"]], "NUM2": [3, scale, [4, "1"]]},
		)
		writer.blocks[offset]["parent"] = coordinate
		writer.blocks[scale]["parent"] = coordinate
		floor_value = writer.operator(
			"operator_mathop", statement, {"NUM": [3, coordinate, [4, "0"]]}, {"OPERATOR": ["floor", None]},
		)
		writer.blocks[coordinate]["parent"] = floor_value
		writer.blocks[statement]["inputs"] = {"VALUE": [3, floor_value, [4, "0"]]}
		return statement

	pointer_x = local_pointer_return("x")
	syscall_branch(127, [pointer_x])
	pointer_y = local_pointer_return("y")
	syscall_branch(128, [pointer_y])
	for syscall, variable_name in ((122, "ABI_DRAW_WIDTH"), (123, "ABI_DRAW_HEIGHT")):
		content_size = set_return(
			lambda parent, variable_name=variable_name: writer.variable(variable_name, variables[variable_name][0], parent)
		)
		syscall_branch(syscall, [content_size])

	def window_index_statement() -> str:
		statement = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_INDEX", variables["ABI_INDEX"][0]]})
		pointer = argument_item(1, statement)
		lookup = writer.new(
			"data_itemnumoflist", parent=statement,
			inputs={"ITEM": [3, pointer, [4, "0"]]}, fields={"LIST": ["ABI_WINDOW_IDS", window_ids[0]]},
		)
		writer.blocks[pointer]["parent"] = lookup
		writer.blocks[statement]["inputs"] = {"VALUE": [3, lookup, [4, "0"]]}
		return statement

	close_lookup = window_index_statement()
	close_branch = writer.new("control_if")
	close_index = writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], close_branch)
	close_valid = writer.operator("operator_gt", close_branch, {"OPERAND1": [3, close_index, [4, "0"]], "OPERAND2": [1, [4, "0"]]})
	writer.blocks[close_index]["parent"] = close_valid
	close_write = writer.new("data_replaceitemoflist", parent=close_branch, fields={"LIST": ["ABI_WINDOW_STATES", window_states[0]]})
	close_row = writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], close_write)
	writer.blocks[close_write]["inputs"] = {"INDEX": [3, close_row, [4, "1"]], "ITEM": [1, [4, "3"]]}
	writer.blocks[close_branch]["inputs"] = {"CONDITION": [2, close_valid], "SUBSTACK": [2, close_write]}
	memory_write_proc = "xemem write index %n value %s"
	memory_write_args = _procedure_signature(vm, memory_write_proc)
	close_memory = writer.procedure_call(memory_write_proc, memory_write_args)
	close_pointer = argument_item(1, close_memory)
	close_state_index = writer.operator(
		"operator_add", close_memory,
		{"NUM1": [3, close_pointer, [4, "0"]], "NUM2": [1, [4, "6"]]},
	)
	writer.blocks[close_pointer]["parent"] = close_state_index
	writer.blocks[close_memory]["inputs"] = {
		memory_write_args[0]: [3, close_state_index, [4, "0"]],
		memory_write_args[1]: [1, [4, "3"]],
	}
	syscall_branch(150, [close_lookup, close_branch, close_memory])
	for syscall, state_value in ((151, 2), (152, 1)):
		lookup = window_index_statement()
		result_statement = set_return(lambda parent, state_value=state_value: writer.operator(
			"operator_equals", parent,
			{
				"OPERAND1": [3, writer.new(
					"data_itemoflist", parent=parent,
					inputs={"INDEX": [3, writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], parent), [4, "1"]]},
					fields={"LIST": ["ABI_WINDOW_STATES", window_states[0]]},
				), [4, "0"]],
				"OPERAND2": [1, [4, str(state_value)]],
			},
		))
		equals_state = writer.blocks[result_statement]["inputs"]["VALUE"][1]
		state_item = writer.blocks[equals_state]["inputs"]["OPERAND1"][1]
		writer.blocks[state_item]["parent"] = equals_state
		state_index = writer.blocks[state_item]["inputs"]["INDEX"][1]
		writer.blocks[state_index]["parent"] = state_item
		syscall_branch(syscall, [lookup, result_statement])

	# Project-local VFS. Paths are descriptor strings; the three columns remain
	# aligned and root is immutable at row 1.
	sys_text = variables.get("sys_text")
	if sys_text is None:
		raise FullAbiBuildError("VM target is missing sys_text")
	paths = lists["ABI_VFS_PATHS"]
	types = lists["ABI_VFS_TYPES"]
	contents = lists["ABI_VFS_CONTENTS"]
	alive = lists["ABI_VFS_ALIVE"]
	names = lists["ABI_VFS_NAMES"]
	parents = lists["ABI_VFS_PARENTS"]

	find_path_proc = "xeabi find live vfs path"
	find_path_definition, _ = writer.procedure_definition(find_path_proc, [], [])
	find_reset = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_INDEX", variables["ABI_INDEX"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	find_row_reset = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	find_repeat = writer.new("control_repeat")
	find_length = writer.new("data_lengthoflist", parent=find_repeat, fields={"LIST": ["ABI_VFS_PATHS", paths[0]]})
	writer.blocks[find_repeat]["inputs"] = {"TIMES": [3, find_length, [4, "0"]]}
	find_prepare_path = writer.new(
		"data_setvariableto", parent=find_repeat,
		fields={"VARIABLE": ["ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0]]},
	)
	find_name_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], find_prepare_path)
	find_name = writer.new(
		"data_itemoflist", parent=find_prepare_path,
		inputs={"INDEX": [3, find_name_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_NAMES", names[0]]},
	)
	writer.blocks[find_name_row]["parent"] = find_name
	writer.blocks[find_prepare_path]["inputs"] = {"VALUE": [3, find_name, [10, ""]]}
	find_root = writer.new("control_if")
	find_root_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], find_root)
	find_is_root = writer.operator(
		"operator_equals", find_root,
		{"OPERAND1": [3, find_root_row, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[find_root_row]["parent"] = find_is_root
	find_set_root = writer.new(
		"data_setvariableto", parent=find_root,
		fields={"VARIABLE": ["ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0]]},
		inputs={"VALUE": [1, [10, "."]]},
	)
	writer.blocks[find_root]["inputs"] = {"CONDITION": [2, find_is_root], "SUBSTACK": [2, find_set_root]}
	find_prepare_parent = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
	)
	find_parent_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], find_prepare_parent)
	find_parent = writer.new(
		"data_itemoflist", parent=find_prepare_parent,
		inputs={"INDEX": [3, find_parent_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_PARENTS", parents[0]]},
	)
	writer.blocks[find_parent_row]["parent"] = find_parent
	writer.blocks[find_prepare_parent]["inputs"] = {"VALUE": [3, find_parent, [4, "0"]]}
	find_build = writer.new("control_repeat")
	find_depth_limit = writer.new(
		"data_lengthoflist", parent=find_build, fields={"LIST": ["ABI_VFS_PARENTS", parents[0]]},
	)
	writer.blocks[find_build]["inputs"] = {"TIMES": [3, find_depth_limit, [4, "0"]]}
	find_prepend = writer.new("control_if", parent=find_build)
	find_ancestor = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], find_prepend)
	find_has_ancestor = writer.operator(
		"operator_gt", find_prepend,
		{"OPERAND1": [3, find_ancestor, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[find_ancestor]["parent"] = find_has_ancestor
	find_set_path = writer.new(
		"data_setvariableto", parent=find_prepend,
		fields={"VARIABLE": ["ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0]]},
	)
	find_ancestor_for_name = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], find_set_path)
	find_ancestor_name = writer.new(
		"data_itemoflist", parent=find_set_path,
		inputs={"INDEX": [3, find_ancestor_for_name, [4, "1"]]},
		fields={"LIST": ["ABI_VFS_NAMES", names[0]]},
	)
	writer.blocks[find_ancestor_for_name]["parent"] = find_ancestor_name
	find_separator = writer.operator(
		"operator_join", find_set_path,
		{"STRING1": [3, find_ancestor_name, [10, ""]], "STRING2": [1, [10, "/"]]},
	)
	writer.blocks[find_ancestor_name]["parent"] = find_separator
	find_tail = writer.variable("ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], find_set_path)
	find_full_path = writer.operator(
		"operator_join", find_set_path,
		{"STRING1": [3, find_separator, [10, ""]], "STRING2": [3, find_tail, [10, ""]]},
	)
	writer.blocks[find_separator]["parent"] = find_full_path
	writer.blocks[find_tail]["parent"] = find_full_path
	writer.blocks[find_set_path]["inputs"] = {"VALUE": [3, find_full_path, [10, ""]]}
	find_advance_parent = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
	)
	find_current_parent = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], find_advance_parent)
	find_next_parent = writer.new(
		"data_itemoflist", parent=find_advance_parent,
		inputs={"INDEX": [3, find_current_parent, [4, "1"]]}, fields={"LIST": ["ABI_VFS_PARENTS", parents[0]]},
	)
	writer.blocks[find_current_parent]["parent"] = find_next_parent
	writer.blocks[find_advance_parent]["inputs"] = {"VALUE": [3, find_next_parent, [4, "0"]]}
	find_prepend_first = writer.chain([find_set_path, find_advance_parent], find_prepend)
	writer.blocks[find_prepend]["inputs"] = {
		"CONDITION": [2, find_has_ancestor], "SUBSTACK": [2, find_prepend_first],
	}
	writer.blocks[find_build]["inputs"]["SUBSTACK"] = [2, find_prepend]
	find_match = writer.new("control_if", parent=find_repeat)
	find_alive_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], find_match)
	find_alive = writer.new(
		"data_itemoflist", parent=find_match,
		inputs={"INDEX": [3, find_alive_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_ALIVE", alive[0]]},
	)
	writer.blocks[find_alive_row]["parent"] = find_alive
	find_is_alive = writer.operator(
		"operator_equals", find_match,
		{"OPERAND1": [3, find_alive, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[find_alive]["parent"] = find_is_alive
	find_path_value = writer.variable("ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], find_match)
	find_text = writer.variable("sys_text", sys_text[0], find_match)
	find_equal = writer.operator(
		"operator_equals", find_match,
		{"OPERAND1": [3, find_path_value, [10, ""]], "OPERAND2": [3, find_text, [10, ""]]},
	)
	writer.blocks[find_path_value]["parent"] = find_equal
	writer.blocks[find_text]["parent"] = find_equal
	find_condition = writer.operator(
		"operator_and", find_match,
		{"OPERAND1": [2, find_is_alive], "OPERAND2": [2, find_equal]},
	)
	writer.blocks[find_is_alive]["parent"] = find_condition
	writer.blocks[find_equal]["parent"] = find_condition
	find_store = writer.new(
		"data_setvariableto", parent=find_match,
		fields={"VARIABLE": ["ABI_INDEX", variables["ABI_INDEX"][0]]},
	)
	find_store_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], find_store)
	writer.blocks[find_store]["inputs"] = {"VALUE": [3, find_store_row, [4, "0"]]}
	writer.blocks[find_match]["inputs"] = {"CONDITION": [2, find_condition], "SUBSTACK": [2, find_store]}
	find_advance = writer.new(
		"data_changevariableby", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	find_body = writer.chain(
		[find_prepare_path, find_root, find_prepare_parent, find_build, find_match, find_advance],
		find_repeat,
	)
	writer.blocks[find_repeat]["inputs"]["SUBSTACK"] = [2, find_body]
	writer.blocks[find_path_definition]["next"] = writer.chain(
		[find_reset, find_row_reset, find_repeat], find_path_definition
	)
	basename_proc = "xeabi basename current vfs path"
	basename_definition, _ = writer.procedure_definition(basename_proc, [], [])
	basename_clear = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0]]},
		inputs={"VALUE": [1, [10, ""]]},
	)
	basename_parent_reset = writer.new(
		"data_setvariableto",
		fields={"VARIABLE": ["ABI_VFS_PARENT_PATH", variables["ABI_VFS_PARENT_PATH"][0]]},
		inputs={"VALUE": [1, [10, "."]]},
	)
	basename_row_reset = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	basename_repeat = writer.new("control_repeat")
	basename_source = writer.variable("sys_text", sys_text[0], basename_repeat)
	basename_length = writer.new("operator_length", parent=basename_repeat, inputs={"STRING": [3, basename_source, [10, ""]]})
	writer.blocks[basename_source]["parent"] = basename_length
	writer.blocks[basename_repeat]["inputs"] = {"TIMES": [3, basename_length, [4, "0"]]}
	basename_branch = writer.new("control_if_else", parent=basename_repeat)
	basename_letter_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], basename_branch)
	basename_text = writer.variable("sys_text", sys_text[0], basename_branch)
	basename_letter = writer.new(
		"operator_letter_of", parent=basename_branch,
		inputs={"LETTER": [3, basename_letter_row, [4, "1"]], "STRING": [3, basename_text, [10, ""]]},
	)
	writer.blocks[basename_letter_row]["parent"] = basename_letter
	writer.blocks[basename_text]["parent"] = basename_letter
	is_separator = writer.operator(
		"operator_equals", basename_branch,
		{"OPERAND1": [3, basename_letter, [10, ""]], "OPERAND2": [1, [10, "/"]]},
	)
	writer.blocks[basename_letter]["parent"] = is_separator
	basename_parent_branch = writer.new("control_if_else", parent=basename_branch)
	basename_parent_value = writer.variable(
		"ABI_VFS_PARENT_PATH", variables["ABI_VFS_PARENT_PATH"][0], basename_parent_branch
	)
	basename_parent_is_root = writer.operator(
		"operator_equals", basename_parent_branch,
		{"OPERAND1": [3, basename_parent_value, [10, ""]], "OPERAND2": [1, [10, "."]]},
	)
	writer.blocks[basename_parent_value]["parent"] = basename_parent_is_root
	basename_first_parent = writer.new(
		"data_setvariableto", parent=basename_parent_branch,
		fields={"VARIABLE": ["ABI_VFS_PARENT_PATH", variables["ABI_VFS_PARENT_PATH"][0]]},
	)
	basename_first_component = writer.variable(
		"ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], basename_first_parent
	)
	writer.blocks[basename_first_parent]["inputs"] = {
		"VALUE": [3, basename_first_component, [10, ""]],
	}
	basename_nested_parent = writer.new(
		"data_setvariableto", parent=basename_parent_branch,
		fields={"VARIABLE": ["ABI_VFS_PARENT_PATH", variables["ABI_VFS_PARENT_PATH"][0]]},
	)
	basename_parent_left = writer.variable(
		"ABI_VFS_PARENT_PATH", variables["ABI_VFS_PARENT_PATH"][0], basename_nested_parent
	)
	basename_parent_slash = writer.operator(
		"operator_join", basename_nested_parent,
		{"STRING1": [3, basename_parent_left, [10, ""]], "STRING2": [1, [10, "/"]]},
	)
	writer.blocks[basename_parent_left]["parent"] = basename_parent_slash
	basename_component = writer.variable(
		"ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], basename_nested_parent
	)
	basename_parent_join = writer.operator(
		"operator_join", basename_nested_parent,
		{"STRING1": [3, basename_parent_slash, [10, ""]], "STRING2": [3, basename_component, [10, ""]]},
	)
	writer.blocks[basename_parent_slash]["parent"] = basename_parent_join
	writer.blocks[basename_component]["parent"] = basename_parent_join
	writer.blocks[basename_nested_parent]["inputs"] = {"VALUE": [3, basename_parent_join, [10, ""]]}
	writer.blocks[basename_parent_branch]["inputs"] = {
		"CONDITION": [2, basename_parent_is_root],
		"SUBSTACK": [2, basename_first_parent], "SUBSTACK2": [2, basename_nested_parent],
	}
	basename_reset = writer.new(
		"data_setvariableto", parent=basename_branch,
		fields={"VARIABLE": ["ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0]]},
		inputs={"VALUE": [1, [10, ""]]},
	)
	basename_append = writer.new(
		"data_setvariableto", parent=basename_branch,
		fields={"VARIABLE": ["ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0]]},
	)
	basename_left = writer.variable("ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], basename_append)
	append_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], basename_append)
	append_source = writer.variable("sys_text", sys_text[0], basename_append)
	append_letter = writer.new(
		"operator_letter_of", parent=basename_append,
		inputs={"LETTER": [3, append_row, [4, "1"]], "STRING": [3, append_source, [10, ""]]},
	)
	writer.blocks[append_row]["parent"] = append_letter
	writer.blocks[append_source]["parent"] = append_letter
	basename_join = writer.operator(
		"operator_join", basename_append,
		{"STRING1": [3, basename_left, [10, ""]], "STRING2": [3, append_letter, [10, ""]]},
	)
	writer.blocks[basename_left]["parent"] = basename_join
	writer.blocks[append_letter]["parent"] = basename_join
	writer.blocks[basename_append]["inputs"] = {"VALUE": [3, basename_join, [10, ""]]}
	separator_first = writer.chain([basename_parent_branch, basename_reset], basename_branch)
	writer.blocks[basename_branch]["inputs"] = {
		"CONDITION": [2, is_separator], "SUBSTACK": [2, separator_first], "SUBSTACK2": [2, basename_append],
	}
	basename_advance = writer.new(
		"data_changevariableby", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	basename_body = writer.chain([basename_branch, basename_advance], basename_repeat)
	writer.blocks[basename_repeat]["inputs"]["SUBSTACK"] = [2, basename_body]
	basename_store = writer.new(
		"data_setvariableto",
		fields={"VARIABLE": ["ABI_VFS_BASENAME", variables["ABI_VFS_BASENAME"][0]]},
	)
	basename_value = writer.variable("ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], basename_store)
	writer.blocks[basename_store]["inputs"] = {"VALUE": [3, basename_value, [10, ""]]}
	writer.blocks[basename_definition]["next"] = writer.chain(
		[basename_clear, basename_parent_reset, basename_row_reset, basename_repeat, basename_store],
		basename_definition,
	)

	def set_variable_from_sys_text(variable_name: str) -> str:
		statement = writer.new(
			"data_setvariableto", fields={"VARIABLE": [variable_name, variables[variable_name][0]]},
		)
		value = writer.variable("sys_text", sys_text[0], statement)
		writer.blocks[statement]["inputs"] = {"VALUE": [3, value, [10, ""]]}
		return statement

	def set_sys_text_from_variable(variable_name: str) -> str:
		statement = writer.new(
			"data_setvariableto", fields={"VARIABLE": ["sys_text", sys_text[0]]},
		)
		value = writer.variable(variable_name, variables[variable_name][0], statement)
		writer.blocks[statement]["inputs"] = {"VALUE": [3, value, [10, ""]]}
		return statement

	def set_variable_from_index(variable_name: str) -> str:
		statement = writer.new(
			"data_setvariableto", fields={"VARIABLE": [variable_name, variables[variable_name][0]]},
		)
		value = writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], statement)
		writer.blocks[statement]["inputs"] = {"VALUE": [3, value, [4, "0"]]}
		return statement

	def resolve_current_destination() -> list[str]:
		"""Preserve sys_text and resolve destination row, basename, and parent row."""
		return [
			set_variable_from_sys_text("ABI_VFS_DEST_PATH"),
			writer.procedure_call(basename_proc, []),
			writer.procedure_call(find_path_proc, []),
			set_variable_from_index("ABI_VFS_TARGET_ROW"),
			set_sys_text_from_variable("ABI_VFS_PARENT_PATH"),
			writer.procedure_call(find_path_proc, []),
			set_variable_from_index("ABI_VFS_DEST_PARENT_ROW"),
			set_sys_text_from_variable("ABI_VFS_DEST_PATH"),
		]

	for syscall in (213, 261):
		read = read_descriptor(1)
		find_path = writer.procedure_call(find_path_proc, [])
		if syscall == 213:
			result_statement = set_return(lambda parent: writer.operator(
				"operator_gt", parent,
				{"OPERAND1": [3, writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], parent), [4, "0"]], "OPERAND2": [1, [4, "0"]]},
			))
			result_condition = writer.blocks[result_statement]["inputs"]["VALUE"][1]
			result_index = writer.blocks[result_condition]["inputs"]["OPERAND1"][1]
			writer.blocks[result_index]["parent"] = result_condition
			syscall_branch(syscall, [read, find_path, result_statement])
		else:
			result_statement = set_return(lambda parent: writer.operator(
				"operator_equals", parent,
				{
					"OPERAND1": [3, writer.new(
						"data_itemoflist", parent=parent,
						inputs={"INDEX": [3, writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], parent), [4, "0"]]},
						fields={"LIST": ["ABI_VFS_TYPES", types[0]]},
					), [10, ""]],
					"OPERAND2": [1, [10, "folder"]],
				},
			))
			equals_block = writer.blocks[result_statement]["inputs"]["VALUE"][1]
			item_block = writer.blocks[equals_block]["inputs"]["OPERAND1"][1]
			writer.blocks[item_block]["parent"] = equals_block
			index_block = writer.blocks[item_block]["inputs"]["INDEX"][1]
			writer.blocks[index_block]["parent"] = item_block
			syscall_branch(syscall, [read, find_path, result_statement])

	for syscall, entry_type in ((214, "file"), (215, "folder")):
		read = read_descriptor(1)
		branch = writer.new("control_if")
		target_row = writer.variable("ABI_VFS_TARGET_ROW", variables["ABI_VFS_TARGET_ROW"][0], branch)
		destination_missing = writer.operator(
			"operator_equals", branch,
			{"OPERAND1": [3, target_row, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
		)
		writer.blocks[target_row]["parent"] = destination_missing
		parent_row = writer.variable(
			"ABI_VFS_DEST_PARENT_ROW", variables["ABI_VFS_DEST_PARENT_ROW"][0], branch
		)
		parent_exists = writer.operator(
			"operator_gt", branch,
			{"OPERAND1": [3, parent_row, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
		)
		writer.blocks[parent_row]["parent"] = parent_exists
		parent_type_row = writer.variable(
			"ABI_VFS_DEST_PARENT_ROW", variables["ABI_VFS_DEST_PARENT_ROW"][0], branch
		)
		parent_type = writer.new(
			"data_itemoflist", parent=branch,
			inputs={"INDEX": [3, parent_type_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_TYPES", types[0]]},
		)
		writer.blocks[parent_type_row]["parent"] = parent_type
		parent_is_folder = writer.operator(
			"operator_equals", branch,
			{"OPERAND1": [3, parent_type, [10, ""]], "OPERAND2": [1, [10, "folder"]]},
		)
		writer.blocks[parent_type]["parent"] = parent_is_folder
		basename_value = writer.variable("ABI_VFS_BASENAME", variables["ABI_VFS_BASENAME"][0], branch)
		basename_length = writer.new(
			"operator_length", parent=branch, inputs={"STRING": [3, basename_value, [10, ""]]},
		)
		writer.blocks[basename_value]["parent"] = basename_length
		basename_present = writer.operator(
			"operator_gt", branch,
			{"OPERAND1": [3, basename_length, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
		)
		writer.blocks[basename_length]["parent"] = basename_present
		basename_dot_value = writer.variable("ABI_VFS_BASENAME", variables["ABI_VFS_BASENAME"][0], branch)
		basename_is_dot = writer.operator(
			"operator_equals", branch,
			{"OPERAND1": [3, basename_dot_value, [10, ""]], "OPERAND2": [1, [10, "."]]},
		)
		writer.blocks[basename_dot_value]["parent"] = basename_is_dot
		basename_not_dot = writer.operator("operator_not", branch, {"OPERAND": [2, basename_is_dot]})
		writer.blocks[basename_is_dot]["parent"] = basename_not_dot
		basename_dot_dot_value = writer.variable(
			"ABI_VFS_BASENAME", variables["ABI_VFS_BASENAME"][0], branch
		)
		basename_is_dot_dot = writer.operator(
			"operator_equals", branch,
			{"OPERAND1": [3, basename_dot_dot_value, [10, ""]], "OPERAND2": [1, [10, ".."]]},
		)
		writer.blocks[basename_dot_dot_value]["parent"] = basename_is_dot_dot
		basename_not_dot_dot = writer.operator(
			"operator_not", branch, {"OPERAND": [2, basename_is_dot_dot]},
		)
		writer.blocks[basename_is_dot_dot]["parent"] = basename_not_dot_dot
		create_conditions = [
			destination_missing, parent_exists, parent_is_folder, basename_present,
			basename_not_dot, basename_not_dot_dot,
		]
		create_condition = create_conditions[0]
		for condition in create_conditions[1:]:
			combined = writer.operator(
				"operator_and", branch,
				{"OPERAND1": [2, create_condition], "OPERAND2": [2, condition]},
			)
			writer.blocks[create_condition]["parent"] = combined
			writer.blocks[condition]["parent"] = combined
			create_condition = combined
		add_path = writer.new("data_addtolist", parent=branch, fields={"LIST": ["ABI_VFS_PATHS", paths[0]]})
		path_value = writer.variable("ABI_VFS_DEST_PATH", variables["ABI_VFS_DEST_PATH"][0], add_path)
		writer.blocks[add_path]["inputs"] = {"ITEM": [3, path_value, [10, ""]]}
		add_name = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_NAMES", names[0]]})
		name_value = writer.variable("ABI_VFS_BASENAME", variables["ABI_VFS_BASENAME"][0], add_name)
		writer.blocks[add_name]["inputs"] = {"ITEM": [3, name_value, [10, ""]]}
		add_type = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_TYPES", types[0]]}, inputs={"ITEM": [1, [10, entry_type]]})
		add_content = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_CONTENTS", contents[0]]}, inputs={"ITEM": [1, [10, ""]]})
		add_parent = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_PARENTS", parents[0]]})
		parent_value = writer.variable(
			"ABI_VFS_DEST_PARENT_ROW", variables["ABI_VFS_DEST_PARENT_ROW"][0], add_parent
		)
		writer.blocks[add_parent]["inputs"] = {"ITEM": [3, parent_value, [4, "1"]]}
		add_id = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_IDS", lists["ABI_VFS_IDS"][0]]})
		next_id = writer.variable("ABI_VFS_NEXT_ID", variables["ABI_VFS_NEXT_ID"][0], add_id)
		writer.blocks[add_id]["inputs"] = {"ITEM": [3, next_id, [4, "0"]]}
		add_key = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_KEYS", lists["ABI_VFS_KEYS"][0]]})
		key_value = writer.variable("ABI_VFS_DEST_PATH", variables["ABI_VFS_DEST_PATH"][0], add_key)
		writer.blocks[add_key]["inputs"] = {"ITEM": [3, key_value, [10, ""]]}
		add_alive = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_ALIVE", alive[0]]}, inputs={"ITEM": [1, [4, "1"]]})
		add_mtime = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_MTIME", lists["ABI_VFS_MTIME"][0]]})
		clock_value = writer.variable("ABI_VFS_CLOCK", variables["ABI_VFS_CLOCK"][0], add_mtime)
		writer.blocks[add_mtime]["inputs"] = {"ITEM": [3, clock_value, [4, "0"]]}
		advance_id = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_VFS_NEXT_ID", variables["ABI_VFS_NEXT_ID"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
		advance_clock = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_VFS_CLOCK", variables["ABI_VFS_CLOCK"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
		bump_revision = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_VFS_REVISION", variables["ABI_VFS_REVISION"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
		mark_true = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]}, inputs={"VALUE": [1, [4, str(TRUE)]]})
		first = writer.chain([
			add_path, add_name, add_type, add_content, add_parent,
			add_id, add_key, add_alive, add_mtime, advance_id, advance_clock,
			bump_revision, mark_true,
		], branch)
		writer.blocks[branch]["inputs"] = {"CONDITION": [2, create_condition], "SUBSTACK": [2, first]}
		syscall_branch(syscall, [read, *resolve_current_destination(), branch])

	child_rows = lists["ABI_CHILD_ROWS"]
	insert_child_proc = "xeabi insert sorted vfs child %n"
	insert_child_args = ["xeabi_child_row"]
	insert_child_definition, _ = writer.procedure_definition(
		insert_child_proc, insert_child_args, ["row"]
	)
	insert_position_reset = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	insert_scan = writer.new("control_repeat")
	insert_length = writer.new(
		"data_lengthoflist", parent=insert_scan, fields={"LIST": ["ABI_CHILD_ROWS", child_rows[0]]},
	)
	writer.blocks[insert_scan]["inputs"] = {"TIMES": [3, insert_length, [4, "0"]]}
	insert_advance_if = writer.new("control_if", parent=insert_scan)
	current_position = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], insert_advance_if)
	current_child = writer.new(
		"data_itemoflist", parent=insert_advance_if,
		inputs={"INDEX": [3, current_position, [4, "1"]]}, fields={"LIST": ["ABI_CHILD_ROWS", child_rows[0]]},
	)
	writer.blocks[current_position]["parent"] = current_child
	current_type = writer.new(
		"data_itemoflist", parent=insert_advance_if,
		inputs={"INDEX": [3, current_child, [4, "1"]]}, fields={"LIST": ["ABI_VFS_TYPES", types[0]]},
	)
	writer.blocks[current_child]["parent"] = current_type
	current_folder = writer.operator(
		"operator_equals", insert_advance_if,
		{"OPERAND1": [3, current_type, [10, ""]], "OPERAND2": [1, [10, "folder"]]},
	)
	writer.blocks[current_type]["parent"] = current_folder
	new_type_row = writer.arg("row", insert_advance_if)
	new_type = writer.new(
		"data_itemoflist", parent=insert_advance_if,
		inputs={"INDEX": [3, new_type_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_TYPES", types[0]]},
	)
	writer.blocks[new_type_row]["parent"] = new_type
	new_folder = writer.operator(
		"operator_equals", insert_advance_if,
		{"OPERAND1": [3, new_type, [10, ""]], "OPERAND2": [1, [10, "folder"]]},
	)
	writer.blocks[new_type]["parent"] = new_folder
	new_not_folder = writer.operator("operator_not", insert_advance_if, {"OPERAND": [2, new_folder]})
	writer.blocks[new_folder]["parent"] = new_not_folder
	folder_before_file = writer.operator(
		"operator_and", insert_advance_if,
		{"OPERAND1": [2, current_folder], "OPERAND2": [2, new_not_folder]},
	)
	writer.blocks[current_folder]["parent"] = folder_before_file
	writer.blocks[new_not_folder]["parent"] = folder_before_file
	current_type_again_position = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], insert_advance_if)
	current_type_again_child = writer.new(
		"data_itemoflist", parent=insert_advance_if,
		inputs={"INDEX": [3, current_type_again_position, [4, "1"]]},
		fields={"LIST": ["ABI_CHILD_ROWS", child_rows[0]]},
	)
	writer.blocks[current_type_again_position]["parent"] = current_type_again_child
	current_type_again = writer.new(
		"data_itemoflist", parent=insert_advance_if,
		inputs={"INDEX": [3, current_type_again_child, [4, "1"]]},
		fields={"LIST": ["ABI_VFS_TYPES", types[0]]},
	)
	writer.blocks[current_type_again_child]["parent"] = current_type_again
	new_type_again_row = writer.arg("row", insert_advance_if)
	new_type_again = writer.new(
		"data_itemoflist", parent=insert_advance_if,
		inputs={"INDEX": [3, new_type_again_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_TYPES", types[0]]},
	)
	writer.blocks[new_type_again_row]["parent"] = new_type_again
	same_type = writer.operator(
		"operator_equals", insert_advance_if,
		{"OPERAND1": [3, current_type_again, [10, ""]], "OPERAND2": [3, new_type_again, [10, ""]]},
	)
	writer.blocks[current_type_again]["parent"] = same_type
	writer.blocks[new_type_again]["parent"] = same_type
	new_name_row = writer.arg("row", insert_advance_if)
	new_name = writer.new(
		"data_itemoflist", parent=insert_advance_if,
		inputs={"INDEX": [3, new_name_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_NAMES", names[0]]},
	)
	writer.blocks[new_name_row]["parent"] = new_name
	current_name_position = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], insert_advance_if)
	current_name_child = writer.new(
		"data_itemoflist", parent=insert_advance_if,
		inputs={"INDEX": [3, current_name_position, [4, "1"]]}, fields={"LIST": ["ABI_CHILD_ROWS", child_rows[0]]},
	)
	writer.blocks[current_name_position]["parent"] = current_name_child
	current_name = writer.new(
		"data_itemoflist", parent=insert_advance_if,
		inputs={"INDEX": [3, current_name_child, [4, "1"]]}, fields={"LIST": ["ABI_VFS_NAMES", names[0]]},
	)
	writer.blocks[current_name_child]["parent"] = current_name
	new_before_current = writer.operator(
		"operator_lt", insert_advance_if,
		{"OPERAND1": [3, new_name, [10, ""]], "OPERAND2": [3, current_name, [10, ""]]},
	)
	writer.blocks[new_name]["parent"] = new_before_current
	writer.blocks[current_name]["parent"] = new_before_current
	current_name_before_or_equal = writer.operator(
		"operator_not", insert_advance_if, {"OPERAND": [2, new_before_current]},
	)
	writer.blocks[new_before_current]["parent"] = current_name_before_or_equal
	same_type_before = writer.operator(
		"operator_and", insert_advance_if,
		{"OPERAND1": [2, same_type], "OPERAND2": [2, current_name_before_or_equal]},
	)
	writer.blocks[same_type]["parent"] = same_type_before
	writer.blocks[current_name_before_or_equal]["parent"] = same_type_before
	current_before_new = writer.operator(
		"operator_or", insert_advance_if,
		{"OPERAND1": [2, folder_before_file], "OPERAND2": [2, same_type_before]},
	)
	writer.blocks[folder_before_file]["parent"] = current_before_new
	writer.blocks[same_type_before]["parent"] = current_before_new
	advance_insert_position = writer.new(
		"data_changevariableby", parent=insert_advance_if,
		fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	writer.blocks[insert_advance_if]["inputs"] = {
		"CONDITION": [2, current_before_new], "SUBSTACK": [2, advance_insert_position],
	}
	writer.blocks[insert_scan]["inputs"]["SUBSTACK"] = [2, insert_advance_if]
	insert_child = writer.new(
		"data_insertatlist", fields={"LIST": ["ABI_CHILD_ROWS", child_rows[0]]},
	)
	insert_at = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], insert_child)
	insert_row = writer.arg("row", insert_child)
	writer.blocks[insert_child]["inputs"] = {
		"INDEX": [3, insert_at, [4, "1"]], "ITEM": [3, insert_row, [4, "0"]],
	}
	writer.blocks[insert_child_definition]["next"] = writer.chain(
		[insert_position_reset, insert_scan, insert_child], insert_child_definition
	)
	cache_children_proc = "xeabi cache vfs children"
	cache_definition, _ = writer.procedure_definition(cache_children_proc, [], [])
	clear_children = writer.new("data_deletealloflist", fields={"LIST": ["ABI_CHILD_ROWS", child_rows[0]]})
	cache_find_path = writer.procedure_call(find_path_proc, [])
	set_context = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_VFS_CONTEXT_ROW", variables["ABI_VFS_CONTEXT_ROW"][0]]})
	context_lookup = writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], set_context)
	writer.blocks[set_context]["inputs"] = {"VALUE": [3, context_lookup, [4, "0"]]}
	set_row = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
	cache_repeat = writer.new("control_repeat")
	path_length = writer.new("data_lengthoflist", parent=cache_repeat, fields={"LIST": ["ABI_VFS_PATHS", paths[0]]})
	writer.blocks[cache_repeat]["inputs"] = {"TIMES": [3, path_length, [4, "0"]]}
	match_child = writer.new("control_if", parent=cache_repeat)
	row_for_parent = writer.variable("ABI_ROW", variables["ABI_ROW"][0], match_child)
	parent_item = writer.new("data_itemoflist", parent=match_child, inputs={"INDEX": [3, row_for_parent, [4, "1"]]}, fields={"LIST": ["ABI_VFS_PARENTS", parents[0]]})
	writer.blocks[row_for_parent]["parent"] = parent_item
	context_row = writer.variable("ABI_VFS_CONTEXT_ROW", variables["ABI_VFS_CONTEXT_ROW"][0], match_child)
	child_matches = writer.operator("operator_equals", match_child, {"OPERAND1": [3, parent_item, [4, "0"]], "OPERAND2": [3, context_row, [4, "0"]]})
	writer.blocks[parent_item]["parent"] = child_matches
	writer.blocks[context_row]["parent"] = child_matches
	alive_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], match_child)
	alive_item = writer.new(
		"data_itemoflist", parent=match_child,
		inputs={"INDEX": [3, alive_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_ALIVE", alive[0]]},
	)
	writer.blocks[alive_row]["parent"] = alive_item
	child_alive = writer.operator(
		"operator_equals", match_child,
		{"OPERAND1": [3, alive_item, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[alive_item]["parent"] = child_alive
	child_visible = writer.operator(
		"operator_and", match_child,
		{"OPERAND1": [2, child_matches], "OPERAND2": [2, child_alive]},
	)
	writer.blocks[child_matches]["parent"] = child_visible
	writer.blocks[child_alive]["parent"] = child_visible
	visible_context = writer.variable(
		"ABI_VFS_CONTEXT_ROW", variables["ABI_VFS_CONTEXT_ROW"][0], match_child
	)
	context_exists = writer.operator(
		"operator_gt", match_child,
		{"OPERAND1": [3, visible_context, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
	)
	writer.blocks[visible_context]["parent"] = context_exists
	child_in_live_directory = writer.operator(
		"operator_and", match_child,
		{"OPERAND1": [2, child_visible], "OPERAND2": [2, context_exists]},
	)
	writer.blocks[child_visible]["parent"] = child_in_live_directory
	writer.blocks[context_exists]["parent"] = child_in_live_directory
	add_child = writer.procedure_call(insert_child_proc, insert_child_args, match_child)
	child_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], add_child)
	writer.blocks[add_child]["inputs"] = {insert_child_args[0]: [3, child_row, [4, "0"]]}
	writer.blocks[match_child]["inputs"] = {
		"CONDITION": [2, child_in_live_directory], "SUBSTACK": [2, add_child],
	}
	advance_row = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
	cache_body = writer.chain([match_child, advance_row], cache_repeat)
	writer.blocks[cache_repeat]["inputs"]["SUBSTACK"] = [2, cache_body]
	writer.blocks[cache_definition]["next"] = writer.chain(
		[clear_children, cache_find_path, set_context, set_row, cache_repeat], cache_definition
	)

	read_count_path = read_descriptor(1)
	cache_count = writer.procedure_call(cache_children_proc, [])
	count_statement = set_return(lambda parent: writer.new("data_lengthoflist", parent=parent, fields={"LIST": ["ABI_CHILD_ROWS", child_rows[0]]}))
	syscall_branch(210, [read_count_path, cache_count, count_statement])

	def child_row_statement() -> str:
		statement = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_INDEX", variables["ABI_INDEX"][0]]})
		argument = argument_item(2, statement)
		one_based = writer.operator("operator_add", statement, {"NUM1": [3, argument, [4, "0"]], "NUM2": [1, [4, "1"]]})
		writer.blocks[argument]["parent"] = one_based
		row_item = writer.new("data_itemoflist", parent=statement, inputs={"INDEX": [3, one_based, [4, "1"]]}, fields={"LIST": ["ABI_CHILD_ROWS", child_rows[0]]})
		writer.blocks[one_based]["parent"] = row_item
		writer.blocks[statement]["inputs"] = {"VALUE": [3, row_item, [4, "0"]]}
		return statement

	read_name_path = read_descriptor(1)
	cache_names = writer.procedure_call(cache_children_proc, [])
	set_name_row = child_row_statement()
	entry_name = set_return_text(lambda parent: writer.new("data_itemoflist", parent=parent, inputs={"INDEX": [3, writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], parent), [4, "1"]]}, fields={"LIST": ["ABI_VFS_NAMES", names[0]]}))
	name_item = writer.blocks[entry_name]["inputs"]["VALUE"][1]
	name_index = writer.blocks[name_item]["inputs"]["INDEX"][1]
	writer.blocks[name_index]["parent"] = name_item
	syscall_branch(211, [read_name_path, cache_names, set_name_row, entry_name])

	read_type_path = read_descriptor(1)
	cache_types = writer.procedure_call(cache_children_proc, [])
	set_type_row = child_row_statement()
	entry_type = set_return(lambda parent: writer.operator("operator_equals", parent, {"OPERAND1": [3, writer.new("data_itemoflist", parent=parent, inputs={"INDEX": [3, writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], parent), [4, "1"]]}, fields={"LIST": ["ABI_VFS_TYPES", types[0]]}), [10, ""]], "OPERAND2": [1, [10, "folder"]]}))
	type_equals = writer.blocks[entry_type]["inputs"]["VALUE"][1]
	type_item = writer.blocks[type_equals]["inputs"]["OPERAND1"][1]
	writer.blocks[type_item]["parent"] = type_equals
	type_index = writer.blocks[type_item]["inputs"]["INDEX"][1]
	writer.blocks[type_index]["parent"] = type_item
	syscall_branch(212, [read_type_path, cache_types, set_type_row, entry_type])

	# Normalization is deterministic and fail-closed: return the project-local
	# path text unchanged; the VM never exposes a host path.
	read = read_descriptor(1)
	normalized = set_return_text(lambda parent: writer.variable("sys_text", sys_text[0], parent))
	syscall_branch(266, [read, normalized])

	# Mutable string descriptors retain the core VM's allocation/capacity rules.
	read_target = read_descriptor(1)
	save_target = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0]]})
	target_text = writer.variable("sys_text", sys_text[0], save_target)
	writer.blocks[save_target]["inputs"] = {"VALUE": [3, target_text, [10, ""]]}
	read_suffix = read_descriptor(2)
	write_proc = "sys_write_descriptor %n %s"
	write_args = _procedure_signature(vm, write_proc)
	write_string = writer.procedure_call(write_proc, write_args)
	target_descriptor = argument_item(1, write_string)
	left_text = writer.variable("ABI_TEMP_TEXT", variables["ABI_TEMP_TEXT"][0], write_string)
	right_text = writer.variable("sys_text", sys_text[0], write_string)
	joined_text = writer.operator("operator_join", write_string, {"STRING1": [3, left_text, [10, ""]], "STRING2": [3, right_text, [10, ""]]})
	writer.blocks[left_text]["parent"] = joined_text
	writer.blocks[right_text]["parent"] = joined_text
	writer.blocks[write_string]["inputs"] = {write_args[0]: [3, target_descriptor, [4, "0"]], write_args[1]: [3, joined_text, [10, ""]]}
	syscall_branch(170, [read_target, save_target, read_suffix, write_string])

	read_target_char = read_descriptor(1)
	write_char = writer.procedure_call(write_proc, write_args)
	char_target = argument_item(1, write_char)
	char_left = writer.variable("sys_text", sys_text[0], write_char)
	charset = lists.get("charset")
	if charset is None:
		raise FullAbiBuildError("VM target is missing charset")
	char_value = argument_item(2, write_char)
	char_index = writer.operator("operator_add", write_char, {"NUM1": [3, char_value, [4, "0"]], "NUM2": [1, [4, "1"]]})
	writer.blocks[char_value]["parent"] = char_index
	char_reporter = writer.new(
		"data_itemoflist", parent=write_char,
		inputs={"INDEX": [3, char_index, [4, "1"]]}, fields={"LIST": ["charset", charset[0]]},
	)
	writer.blocks[char_index]["parent"] = char_reporter
	char_join = writer.operator("operator_join", write_char, {"STRING1": [3, char_left, [10, ""]], "STRING2": [3, char_reporter, [10, ""]]})
	writer.blocks[char_left]["parent"] = char_join
	writer.blocks[char_reporter]["parent"] = char_join
	writer.blocks[write_char]["inputs"] = {write_args[0]: [3, char_target, [4, "0"]], write_args[1]: [3, char_join, [10, ""]]}
	syscall_branch(171, [read_target_char, write_char])

	def index_current_path() -> str:
		return writer.procedure_call(find_path_proc, [])

	handle_rows = lists["ABI_HANDLE_ROWS"]
	handle_modes = lists["ABI_HANDLE_MODES"]
	handle_ids = lists["ABI_HANDLE_IDS"]
	handle_cursors = lists["ABI_HANDLE_CURSORS"]
	handle_open = lists["ABI_HANDLE_OPEN"]
	read_open_path = read_descriptor(1)
	lookup_open_path = index_current_path()
	open_read_branch = writer.new("control_if")
	open_row = writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], open_read_branch)
	open_exists = writer.operator(
		"operator_gt", open_read_branch,
		{"OPERAND1": [3, open_row, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
	)
	writer.blocks[open_row]["parent"] = open_exists
	open_type_row = writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], open_read_branch)
	open_type = writer.new(
		"data_itemoflist", parent=open_read_branch,
		inputs={"INDEX": [3, open_type_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_TYPES", types[0]]},
	)
	writer.blocks[open_type_row]["parent"] = open_type
	open_is_file = writer.operator(
		"operator_equals", open_read_branch,
		{"OPERAND1": [3, open_type, [10, ""]], "OPERAND2": [1, [10, "file"]]},
	)
	writer.blocks[open_type]["parent"] = open_is_file
	open_valid = writer.operator(
		"operator_and", open_read_branch,
		{"OPERAND1": [2, open_exists], "OPERAND2": [2, open_is_file]},
	)
	writer.blocks[open_exists]["parent"] = open_valid
	writer.blocks[open_is_file]["parent"] = open_valid
	add_handle = writer.new("data_addtolist", fields={"LIST": ["ABI_HANDLE_ROWS", handle_rows[0]]})
	handle_row = writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], add_handle)
	writer.blocks[add_handle]["inputs"] = {"ITEM": [3, handle_row, [4, "0"]]}
	add_mode = writer.new(
		"data_addtolist", fields={"LIST": ["ABI_HANDLE_MODES", handle_modes[0]]},
		inputs={"ITEM": [1, [10, "r"]]},
	)
	add_handle_id = writer.new("data_addtolist", fields={"LIST": ["ABI_HANDLE_IDS", handle_ids[0]]})
	next_handle_id = writer.variable("ABI_FH_NEXT_ID", variables["ABI_FH_NEXT_ID"][0], add_handle_id)
	writer.blocks[add_handle_id]["inputs"] = {"ITEM": [3, next_handle_id, [4, "0"]]}
	add_cursor = writer.new(
		"data_addtolist", fields={"LIST": ["ABI_HANDLE_CURSORS", handle_cursors[0]]},
		inputs={"ITEM": [1, [4, "0"]]},
	)
	add_open = writer.new(
		"data_addtolist", fields={"LIST": ["ABI_HANDLE_OPEN", handle_open[0]]},
		inputs={"ITEM": [1, [4, "1"]]},
	)
	set_handle = set_return(
		lambda parent: writer.variable("ABI_FH_NEXT_ID", variables["ABI_FH_NEXT_ID"][0], parent)
	)
	advance_handle_id = writer.new(
		"data_changevariableby", fields={"VARIABLE": ["ABI_FH_NEXT_ID", variables["ABI_FH_NEXT_ID"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	open_first = writer.chain(
		[add_handle, add_mode, add_handle_id, add_cursor, add_open, set_handle, advance_handle_id],
		open_read_branch,
	)
	writer.blocks[open_read_branch]["inputs"] = {
		"CONDITION": [2, open_valid], "SUBSTACK": [2, open_first],
	}
	syscall_branch(160, [read_open_path, lookup_open_path, open_read_branch])
	for unavailable_open in (161, 260):
		syscall_branch(unavailable_open, [writer.new(
			"data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
			inputs={"VALUE": [1, [4, "0"]]},
		)])

	close_path = read_descriptor(1)
	close_lookup = index_current_path()
	close_row_reset = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	close_repeat = writer.new("control_repeat")
	close_length = writer.new("data_lengthoflist", parent=close_repeat, fields={"LIST": ["ABI_HANDLE_IDS", handle_ids[0]]})
	writer.blocks[close_repeat]["inputs"] = {"TIMES": [3, close_length, [4, "0"]]}
	close_match = writer.new("control_if", parent=close_repeat)
	close_handle_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], close_match)
	close_record = writer.new(
		"data_itemoflist", parent=close_match,
		inputs={"INDEX": [3, close_handle_row, [4, "1"]]}, fields={"LIST": ["ABI_HANDLE_ROWS", handle_rows[0]]},
	)
	writer.blocks[close_handle_row]["parent"] = close_record
	close_target = writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], close_match)
	close_record_match = writer.operator(
		"operator_equals", close_match,
		{"OPERAND1": [3, close_record, [4, "0"]], "OPERAND2": [3, close_target, [4, "0"]]},
	)
	writer.blocks[close_record]["parent"] = close_record_match
	writer.blocks[close_target]["parent"] = close_record_match
	close_open_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], close_match)
	close_open_value = writer.new(
		"data_itemoflist", parent=close_match,
		inputs={"INDEX": [3, close_open_row, [4, "1"]]}, fields={"LIST": ["ABI_HANDLE_OPEN", handle_open[0]]},
	)
	writer.blocks[close_open_row]["parent"] = close_open_value
	close_is_open = writer.operator(
		"operator_equals", close_match,
		{"OPERAND1": [3, close_open_value, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[close_open_value]["parent"] = close_is_open
	close_condition = writer.operator(
		"operator_and", close_match,
		{"OPERAND1": [2, close_record_match], "OPERAND2": [2, close_is_open]},
	)
	writer.blocks[close_record_match]["parent"] = close_condition
	writer.blocks[close_is_open]["parent"] = close_condition
	mark_handle_closed = writer.new(
		"data_replaceitemoflist", parent=close_match,
		fields={"LIST": ["ABI_HANDLE_OPEN", handle_open[0]]},
	)
	close_mark_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], mark_handle_closed)
	writer.blocks[mark_handle_closed]["inputs"] = {"INDEX": [3, close_mark_row, [4, "1"]], "ITEM": [1, [4, "0"]]}
	writer.blocks[close_match]["inputs"] = {"CONDITION": [2, close_condition], "SUBSTACK": [2, mark_handle_closed]}
	close_advance = writer.new(
		"data_changevariableby", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	close_body = writer.chain([close_match, close_advance], close_repeat)
	writer.blocks[close_repeat]["inputs"]["SUBSTACK"] = [2, close_body]
	syscall_branch(164, [close_path, close_lookup, close_row_reset, close_repeat])

	find_handle_reset = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_HANDLE_INDEX", variables["ABI_HANDLE_INDEX"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	find_handle_row_reset = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	find_handle_repeat = writer.new("control_repeat")
	find_handle_length = writer.new(
		"data_lengthoflist", parent=find_handle_repeat, fields={"LIST": ["ABI_HANDLE_IDS", handle_ids[0]]},
	)
	writer.blocks[find_handle_repeat]["inputs"] = {"TIMES": [3, find_handle_length, [4, "0"]]}
	find_handle_match = writer.new("control_if", parent=find_handle_repeat)
	find_handle_id_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], find_handle_match)
	find_handle_id = writer.new(
		"data_itemoflist", parent=find_handle_match,
		inputs={"INDEX": [3, find_handle_id_row, [4, "1"]]}, fields={"LIST": ["ABI_HANDLE_IDS", handle_ids[0]]},
	)
	writer.blocks[find_handle_id_row]["parent"] = find_handle_id
	requested_handle = argument_item(1, find_handle_match)
	handle_id_matches = writer.operator(
		"operator_equals", find_handle_match,
		{"OPERAND1": [3, find_handle_id, [4, "0"]], "OPERAND2": [3, requested_handle, [4, "0"]]},
	)
	writer.blocks[find_handle_id]["parent"] = handle_id_matches
	writer.blocks[requested_handle]["parent"] = handle_id_matches
	find_open_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], find_handle_match)
	find_open = writer.new(
		"data_itemoflist", parent=find_handle_match,
		inputs={"INDEX": [3, find_open_row, [4, "1"]]}, fields={"LIST": ["ABI_HANDLE_OPEN", handle_open[0]]},
	)
	writer.blocks[find_open_row]["parent"] = find_open
	handle_is_open = writer.operator(
		"operator_equals", find_handle_match,
		{"OPERAND1": [3, find_open, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[find_open]["parent"] = handle_is_open
	find_mode_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], find_handle_match)
	find_mode = writer.new(
		"data_itemoflist", parent=find_handle_match,
		inputs={"INDEX": [3, find_mode_row, [4, "1"]]}, fields={"LIST": ["ABI_HANDLE_MODES", handle_modes[0]]},
	)
	writer.blocks[find_mode_row]["parent"] = find_mode
	handle_is_read = writer.operator(
		"operator_equals", find_handle_match,
		{"OPERAND1": [3, find_mode, [10, ""]], "OPERAND2": [1, [10, "r"]]},
	)
	writer.blocks[find_mode]["parent"] = handle_is_read
	find_handle_conditions = [handle_id_matches, handle_is_open, handle_is_read]
	find_handle_condition = find_handle_conditions[0]
	for condition in find_handle_conditions[1:]:
		combined = writer.operator(
			"operator_and", find_handle_match,
			{"OPERAND1": [2, find_handle_condition], "OPERAND2": [2, condition]},
		)
		writer.blocks[find_handle_condition]["parent"] = combined
		writer.blocks[condition]["parent"] = combined
		find_handle_condition = combined
	store_handle_index = writer.new(
		"data_setvariableto", parent=find_handle_match,
		fields={"VARIABLE": ["ABI_HANDLE_INDEX", variables["ABI_HANDLE_INDEX"][0]]},
	)
	matched_handle_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], store_handle_index)
	writer.blocks[store_handle_index]["inputs"] = {"VALUE": [3, matched_handle_row, [4, "0"]]}
	writer.blocks[find_handle_match]["inputs"] = {
		"CONDITION": [2, find_handle_condition], "SUBSTACK": [2, store_handle_index],
	}
	find_handle_advance = writer.new(
		"data_changevariableby", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	find_handle_body = writer.chain([find_handle_match, find_handle_advance], find_handle_repeat)
	writer.blocks[find_handle_repeat]["inputs"]["SUBSTACK"] = [2, find_handle_body]
	read_handle_branch = writer.new("control_if")
	read_handle_index = writer.variable(
		"ABI_HANDLE_INDEX", variables["ABI_HANDLE_INDEX"][0], read_handle_branch
	)
	handle_found = writer.operator(
		"operator_gt", read_handle_branch,
		{"OPERAND1": [3, read_handle_index, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
	)
	writer.blocks[read_handle_index]["parent"] = handle_found
	read_cursor_index = writer.variable(
		"ABI_HANDLE_INDEX", variables["ABI_HANDLE_INDEX"][0], read_handle_branch
	)
	read_cursor = writer.new(
		"data_itemoflist", parent=read_handle_branch,
		inputs={"INDEX": [3, read_cursor_index, [4, "1"]]},
		fields={"LIST": ["ABI_HANDLE_CURSORS", handle_cursors[0]]},
	)
	writer.blocks[read_cursor_index]["parent"] = read_cursor
	cursor_at_start = writer.operator(
		"operator_equals", read_handle_branch,
		{"OPERAND1": [3, read_cursor, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
	)
	writer.blocks[read_cursor]["parent"] = cursor_at_start
	read_record_index = writer.variable(
		"ABI_HANDLE_INDEX", variables["ABI_HANDLE_INDEX"][0], read_handle_branch
	)
	read_vfs_row = writer.new(
		"data_itemoflist", parent=read_handle_branch,
		inputs={"INDEX": [3, read_record_index, [4, "1"]]}, fields={"LIST": ["ABI_HANDLE_ROWS", handle_rows[0]]},
	)
	writer.blocks[read_record_index]["parent"] = read_vfs_row
	read_alive = writer.new(
		"data_itemoflist", parent=read_handle_branch,
		inputs={"INDEX": [3, read_vfs_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_ALIVE", alive[0]]},
	)
	writer.blocks[read_vfs_row]["parent"] = read_alive
	row_is_alive = writer.operator(
		"operator_equals", read_handle_branch,
		{"OPERAND1": [3, read_alive, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[read_alive]["parent"] = row_is_alive
	read_conditions = [handle_found, cursor_at_start, row_is_alive]
	read_condition = read_conditions[0]
	for condition in read_conditions[1:]:
		combined = writer.operator(
			"operator_and", read_handle_branch,
			{"OPERAND1": [2, read_condition], "OPERAND2": [2, condition]},
		)
		writer.blocks[read_condition]["parent"] = combined
		writer.blocks[condition]["parent"] = combined
		read_condition = combined
	read_handle = set_return_text(lambda parent: writer.new(
		"data_itemoflist", parent=parent,
		inputs={"INDEX": [3, writer.new(
			"data_itemoflist", parent=parent,
			inputs={"INDEX": [3, writer.variable(
				"ABI_HANDLE_INDEX", variables["ABI_HANDLE_INDEX"][0], parent
			), [4, "1"]]}, fields={"LIST": ["ABI_HANDLE_ROWS", handle_rows[0]]},
		), [4, "1"]]}, fields={"LIST": ["ABI_VFS_CONTENTS", contents[0]]},
	))
	read_content = writer.blocks[read_handle]["inputs"]["VALUE"][1]
	read_row = writer.blocks[read_content]["inputs"]["INDEX"][1]
	writer.blocks[read_row]["parent"] = read_content
	read_record = writer.blocks[read_row]["inputs"]["INDEX"][1]
	writer.blocks[read_record]["parent"] = read_row
	advance_cursor = writer.new(
		"data_replaceitemoflist", fields={"LIST": ["ABI_HANDLE_CURSORS", handle_cursors[0]]},
	)
	advance_cursor_index = writer.variable(
		"ABI_HANDLE_INDEX", variables["ABI_HANDLE_INDEX"][0], advance_cursor
	)
	read_text_value = writer.variable("ABI_RETURN_TEXT", variables["ABI_RETURN_TEXT"][0], advance_cursor)
	read_text_length = writer.new(
		"operator_length", parent=advance_cursor, inputs={"STRING": [3, read_text_value, [10, ""]]},
	)
	writer.blocks[read_text_value]["parent"] = read_text_length
	writer.blocks[advance_cursor]["inputs"] = {
		"INDEX": [3, advance_cursor_index, [4, "1"]], "ITEM": [3, read_text_length, [4, "0"]],
	}
	read_first = writer.chain([read_handle, advance_cursor], read_handle_branch)
	writer.blocks[read_handle_branch]["inputs"] = {
		"CONDITION": [2, read_condition], "SUBSTACK": [2, read_first],
	}
	syscall_branch(162, [
		find_handle_reset, find_handle_row_reset, find_handle_repeat, read_handle_branch,
	])
	syscall_branch(163, [writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)])

	open_subtree_proc = "xeabi vfs subtree is closed %n"
	open_subtree_args = ["xeabi_subtree_row"]
	open_subtree_definition, _ = writer.procedure_definition(
		open_subtree_proc, open_subtree_args, ["row"]
	)
	open_subtree_reset = writer.new(
		"data_setvariableto",
		fields={"VARIABLE": ["ABI_VFS_MUTATION_OK", variables["ABI_VFS_MUTATION_OK"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	open_subtree_row_reset = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	open_subtree_repeat = writer.new("control_repeat")
	open_subtree_length = writer.new(
		"data_lengthoflist", parent=open_subtree_repeat, fields={"LIST": ["ABI_HANDLE_OPEN", handle_open[0]]},
	)
	writer.blocks[open_subtree_repeat]["inputs"] = {"TIMES": [3, open_subtree_length, [4, "0"]]}
	open_record_branch = writer.new("control_if", parent=open_subtree_repeat)
	open_record_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], open_record_branch)
	open_record_value = writer.new(
		"data_itemoflist", parent=open_record_branch,
		inputs={"INDEX": [3, open_record_row, [4, "1"]]}, fields={"LIST": ["ABI_HANDLE_OPEN", handle_open[0]]},
	)
	writer.blocks[open_record_row]["parent"] = open_record_value
	open_record_is_open = writer.operator(
		"operator_equals", open_record_branch,
		{"OPERAND1": [3, open_record_value, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[open_record_value]["parent"] = open_record_is_open
	open_set_candidate = writer.new(
		"data_setvariableto", parent=open_record_branch,
		fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
	)
	open_handle_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], open_set_candidate)
	open_candidate = writer.new(
		"data_itemoflist", parent=open_set_candidate,
		inputs={"INDEX": [3, open_handle_row, [4, "1"]]}, fields={"LIST": ["ABI_HANDLE_ROWS", handle_rows[0]]},
	)
	writer.blocks[open_handle_row]["parent"] = open_candidate
	writer.blocks[open_set_candidate]["inputs"] = {"VALUE": [3, open_candidate, [4, "0"]]}
	open_walk = writer.new("control_repeat")
	open_walk_length = writer.new(
		"data_lengthoflist", parent=open_walk, fields={"LIST": ["ABI_VFS_PARENTS", parents[0]]},
	)
	writer.blocks[open_walk]["inputs"] = {"TIMES": [3, open_walk_length, [4, "0"]]}
	open_match = writer.new("control_if", parent=open_walk)
	open_candidate_row = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], open_match)
	open_target_row = writer.arg("row", open_match)
	open_matches_target = writer.operator(
		"operator_equals", open_match,
		{"OPERAND1": [3, open_candidate_row, [4, "0"]], "OPERAND2": [3, open_target_row, [4, "0"]]},
	)
	writer.blocks[open_candidate_row]["parent"] = open_matches_target
	writer.blocks[open_target_row]["parent"] = open_matches_target
	open_mark_blocked = writer.new(
		"data_setvariableto", parent=open_match,
		fields={"VARIABLE": ["ABI_VFS_MUTATION_OK", variables["ABI_VFS_MUTATION_OK"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	writer.blocks[open_match]["inputs"] = {
		"CONDITION": [2, open_matches_target], "SUBSTACK": [2, open_mark_blocked],
	}
	open_parent_branch = writer.new("control_if")
	open_current_candidate = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], open_parent_branch)
	open_can_ascend = writer.operator(
		"operator_gt", open_parent_branch,
		{"OPERAND1": [3, open_current_candidate, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[open_current_candidate]["parent"] = open_can_ascend
	open_set_parent = writer.new(
		"data_setvariableto", parent=open_parent_branch,
		fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
	)
	open_parent_index = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], open_set_parent)
	open_parent_value = writer.new(
		"data_itemoflist", parent=open_set_parent,
		inputs={"INDEX": [3, open_parent_index, [4, "1"]]}, fields={"LIST": ["ABI_VFS_PARENTS", parents[0]]},
	)
	writer.blocks[open_parent_index]["parent"] = open_parent_value
	writer.blocks[open_set_parent]["inputs"] = {"VALUE": [3, open_parent_value, [4, "0"]]}
	writer.blocks[open_parent_branch]["inputs"] = {
		"CONDITION": [2, open_can_ascend], "SUBSTACK": [2, open_set_parent],
	}
	open_walk_body = writer.chain([open_match, open_parent_branch], open_walk)
	writer.blocks[open_walk]["inputs"]["SUBSTACK"] = [2, open_walk_body]
	open_record_first = writer.chain([open_set_candidate, open_walk], open_record_branch)
	writer.blocks[open_record_branch]["inputs"] = {
		"CONDITION": [2, open_record_is_open], "SUBSTACK": [2, open_record_first],
	}
	open_record_advance = writer.new(
		"data_changevariableby", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	open_subtree_body = writer.chain(
		[open_record_branch, open_record_advance], open_subtree_repeat
	)
	writer.blocks[open_subtree_repeat]["inputs"]["SUBSTACK"] = [2, open_subtree_body]
	writer.blocks[open_subtree_definition]["next"] = writer.chain(
		[open_subtree_reset, open_subtree_row_reset, open_subtree_repeat], open_subtree_definition
	)

	read = read_descriptor(1)
	lookup = index_current_path()
	store_delete_row = set_variable_from_index("ABI_VFS_SOURCE_ROW")
	check_delete_handles = writer.procedure_call(open_subtree_proc, open_subtree_args)
	delete_handle_target = writer.variable(
		"ABI_VFS_SOURCE_ROW", variables["ABI_VFS_SOURCE_ROW"][0], check_delete_handles
	)
	writer.blocks[check_delete_handles]["inputs"] = {
		open_subtree_args[0]: [3, delete_handle_target, [4, "0"]],
	}
	delete_branch = writer.new("control_if")
	index_value = writer.variable("ABI_VFS_SOURCE_ROW", variables["ABI_VFS_SOURCE_ROW"][0], delete_branch)
	can_delete = writer.operator(
		"operator_gt", delete_branch,
		{"OPERAND1": [3, index_value, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[index_value]["parent"] = can_delete
	delete_guard_value = writer.variable(
		"ABI_VFS_MUTATION_OK", variables["ABI_VFS_MUTATION_OK"][0], delete_branch
	)
	delete_handles_closed = writer.operator(
		"operator_equals", delete_branch,
		{"OPERAND1": [3, delete_guard_value, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[delete_guard_value]["parent"] = delete_handles_closed
	delete_allowed = writer.operator(
		"operator_and", delete_branch,
		{"OPERAND1": [2, can_delete], "OPERAND2": [2, delete_handles_closed]},
	)
	writer.blocks[can_delete]["parent"] = delete_allowed
	writer.blocks[delete_handles_closed]["parent"] = delete_allowed
	mark_deleted = writer.new("data_replaceitemoflist", fields={"LIST": ["ABI_VFS_ALIVE", alive[0]]})
	delete_index = writer.variable(
		"ABI_VFS_SOURCE_ROW", variables["ABI_VFS_SOURCE_ROW"][0], mark_deleted
	)
	writer.blocks[mark_deleted]["inputs"] = {"INDEX": [3, delete_index, [4, "1"]], "ITEM": [1, [4, "0"]]}
	delete_row_reset = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "2"]]},
	)
	delete_repeat = writer.new("control_repeat")
	delete_length = writer.new("data_lengthoflist", parent=delete_repeat, fields={"LIST": ["ABI_VFS_PATHS", paths[0]]})
	delete_tail_count = writer.operator(
		"operator_subtract", delete_repeat,
		{"NUM1": [3, delete_length, [4, "0"]], "NUM2": [1, [4, "1"]]},
	)
	writer.blocks[delete_length]["parent"] = delete_tail_count
	writer.blocks[delete_repeat]["inputs"] = {"TIMES": [3, delete_tail_count, [4, "0"]]}
	delete_child = writer.new("control_if", parent=delete_repeat)
	delete_child_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], delete_child)
	delete_child_alive = writer.new(
		"data_itemoflist", parent=delete_child,
		inputs={"INDEX": [3, delete_child_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_ALIVE", alive[0]]},
	)
	writer.blocks[delete_child_row]["parent"] = delete_child_alive
	delete_parent_row_index = writer.variable("ABI_ROW", variables["ABI_ROW"][0], delete_child)
	delete_parent_row = writer.new(
		"data_itemoflist", parent=delete_child,
		inputs={"INDEX": [3, delete_parent_row_index, [4, "1"]]},
		fields={"LIST": ["ABI_VFS_PARENTS", lists["ABI_VFS_PARENTS"][0]]},
	)
	writer.blocks[delete_parent_row_index]["parent"] = delete_parent_row
	delete_parent_alive = writer.new(
		"data_itemoflist", parent=delete_child,
		inputs={"INDEX": [3, delete_parent_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_ALIVE", alive[0]]},
	)
	writer.blocks[delete_parent_row]["parent"] = delete_parent_alive
	child_live = writer.operator(
		"operator_equals", delete_child,
		{"OPERAND1": [3, delete_child_alive, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[delete_child_alive]["parent"] = child_live
	parent_dead = writer.operator(
		"operator_equals", delete_child,
		{"OPERAND1": [3, delete_parent_alive, [4, "1"]], "OPERAND2": [1, [4, "0"]]},
	)
	writer.blocks[delete_parent_alive]["parent"] = parent_dead
	delete_descendant = writer.operator(
		"operator_and", delete_child,
		{"OPERAND1": [2, child_live], "OPERAND2": [2, parent_dead]},
	)
	writer.blocks[child_live]["parent"] = delete_descendant
	writer.blocks[parent_dead]["parent"] = delete_descendant
	mark_descendant = writer.new("data_replaceitemoflist", parent=delete_child, fields={"LIST": ["ABI_VFS_ALIVE", alive[0]]})
	mark_descendant_row = writer.variable("ABI_ROW", variables["ABI_ROW"][0], mark_descendant)
	writer.blocks[mark_descendant]["inputs"] = {"INDEX": [3, mark_descendant_row, [4, "1"]], "ITEM": [1, [4, "0"]]}
	writer.blocks[delete_child]["inputs"] = {"CONDITION": [2, delete_descendant], "SUBSTACK": [2, mark_descendant]}
	delete_advance = writer.new(
		"data_changevariableby", fields={"VARIABLE": ["ABI_ROW", variables["ABI_ROW"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	delete_body = writer.chain([delete_child, delete_advance], delete_repeat)
	writer.blocks[delete_repeat]["inputs"]["SUBSTACK"] = [2, delete_body]
	delete_true = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]}, inputs={"VALUE": [1, [4, str(TRUE)]]})
	delete_revision = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_VFS_REVISION", variables["ABI_VFS_REVISION"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
	delete_clock = writer.new(
		"data_changevariableby", fields={"VARIABLE": ["ABI_VFS_CLOCK", variables["ABI_VFS_CLOCK"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	delete_first = writer.chain([
		mark_deleted, delete_row_reset, delete_repeat, delete_clock, delete_revision, delete_true,
	], delete_branch)
	writer.blocks[delete_branch]["inputs"] = {
		"CONDITION": [2, delete_allowed], "SUBSTACK": [2, delete_first],
	}
	syscall_branch(217, [read, lookup, store_delete_row, check_delete_handles, delete_branch])

	# Rename updates stable identity/parent/name columns. Path lookup derives its
	# canonical value from those columns, so descendants remain valid after a move.
	read_old = read_descriptor(1)
	lookup_old = index_current_path()
	store_old_row = set_variable_from_index("ABI_VFS_SOURCE_ROW")
	store_old_path = set_variable_from_sys_text("ABI_VFS_SOURCE_PATH")
	read_new = read_descriptor(2)
	resolve_rename_destination = resolve_current_destination()
	check_rename_handles = writer.procedure_call(open_subtree_proc, open_subtree_args)
	check_rename_row = writer.variable(
		"ABI_VFS_SOURCE_ROW", variables["ABI_VFS_SOURCE_ROW"][0], check_rename_handles
	)
	writer.blocks[check_rename_handles]["inputs"] = {
		open_subtree_args[0]: [3, check_rename_row, [4, "0"]],
	}
	rename_ancestry_set = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
	)
	rename_parent_candidate = writer.variable(
		"ABI_VFS_DEST_PARENT_ROW", variables["ABI_VFS_DEST_PARENT_ROW"][0], rename_ancestry_set
	)
	writer.blocks[rename_ancestry_set]["inputs"] = {"VALUE": [3, rename_parent_candidate, [4, "0"]]}
	rename_ancestry_repeat = writer.new("control_repeat")
	rename_ancestry_length = writer.new(
		"data_lengthoflist", parent=rename_ancestry_repeat,
		fields={"LIST": ["ABI_VFS_PARENTS", parents[0]]},
	)
	writer.blocks[rename_ancestry_repeat]["inputs"] = {"TIMES": [3, rename_ancestry_length, [4, "0"]]}
	rename_cycle_branch = writer.new("control_if", parent=rename_ancestry_repeat)
	rename_candidate_row = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], rename_cycle_branch)
	rename_source_row = writer.variable(
		"ABI_VFS_SOURCE_ROW", variables["ABI_VFS_SOURCE_ROW"][0], rename_cycle_branch
	)
	rename_cycle = writer.operator(
		"operator_equals", rename_cycle_branch,
		{"OPERAND1": [3, rename_candidate_row, [4, "0"]], "OPERAND2": [3, rename_source_row, [4, "0"]]},
	)
	writer.blocks[rename_candidate_row]["parent"] = rename_cycle
	writer.blocks[rename_source_row]["parent"] = rename_cycle
	rename_block_cycle = writer.new(
		"data_setvariableto", parent=rename_cycle_branch,
		fields={"VARIABLE": ["ABI_VFS_MUTATION_OK", variables["ABI_VFS_MUTATION_OK"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	writer.blocks[rename_cycle_branch]["inputs"] = {
		"CONDITION": [2, rename_cycle], "SUBSTACK": [2, rename_block_cycle],
	}
	rename_ascend = writer.new("control_if")
	rename_current_parent = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], rename_ascend)
	rename_can_ascend = writer.operator(
		"operator_gt", rename_ascend,
		{"OPERAND1": [3, rename_current_parent, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[rename_current_parent]["parent"] = rename_can_ascend
	rename_set_parent = writer.new(
		"data_setvariableto", parent=rename_ascend,
		fields={"VARIABLE": ["ABI_COUNT", variables["ABI_COUNT"][0]]},
	)
	rename_parent_index = writer.variable("ABI_COUNT", variables["ABI_COUNT"][0], rename_set_parent)
	rename_parent_value = writer.new(
		"data_itemoflist", parent=rename_set_parent,
		inputs={"INDEX": [3, rename_parent_index, [4, "1"]]}, fields={"LIST": ["ABI_VFS_PARENTS", parents[0]]},
	)
	writer.blocks[rename_parent_index]["parent"] = rename_parent_value
	writer.blocks[rename_set_parent]["inputs"] = {"VALUE": [3, rename_parent_value, [4, "0"]]}
	writer.blocks[rename_ascend]["inputs"] = {
		"CONDITION": [2, rename_can_ascend], "SUBSTACK": [2, rename_set_parent],
	}
	rename_ancestry_body = writer.chain([rename_cycle_branch, rename_ascend], rename_ancestry_repeat)
	writer.blocks[rename_ancestry_repeat]["inputs"]["SUBSTACK"] = [2, rename_ancestry_body]
	rename_branch = writer.new("control_if")
	rename_index = writer.variable("ABI_VFS_SOURCE_ROW", variables["ABI_VFS_SOURCE_ROW"][0], rename_branch)
	rename_source_valid = writer.operator(
		"operator_gt", rename_branch,
		{"OPERAND1": [3, rename_index, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[rename_index]["parent"] = rename_source_valid
	rename_target = writer.variable("ABI_VFS_TARGET_ROW", variables["ABI_VFS_TARGET_ROW"][0], rename_branch)
	rename_destination_missing = writer.operator(
		"operator_equals", rename_branch,
		{"OPERAND1": [3, rename_target, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
	)
	writer.blocks[rename_target]["parent"] = rename_destination_missing
	rename_parent_row = writer.variable(
		"ABI_VFS_DEST_PARENT_ROW", variables["ABI_VFS_DEST_PARENT_ROW"][0], rename_branch
	)
	rename_parent_exists = writer.operator(
		"operator_gt", rename_branch,
		{"OPERAND1": [3, rename_parent_row, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
	)
	writer.blocks[rename_parent_row]["parent"] = rename_parent_exists
	rename_parent_type_row = writer.variable(
		"ABI_VFS_DEST_PARENT_ROW", variables["ABI_VFS_DEST_PARENT_ROW"][0], rename_branch
	)
	rename_parent_type = writer.new(
		"data_itemoflist", parent=rename_branch,
		inputs={"INDEX": [3, rename_parent_type_row, [4, "1"]]}, fields={"LIST": ["ABI_VFS_TYPES", types[0]]},
	)
	writer.blocks[rename_parent_type_row]["parent"] = rename_parent_type
	rename_parent_is_folder = writer.operator(
		"operator_equals", rename_branch,
		{"OPERAND1": [3, rename_parent_type, [10, ""]], "OPERAND2": [1, [10, "folder"]]},
	)
	writer.blocks[rename_parent_type]["parent"] = rename_parent_is_folder
	rename_name_value = writer.variable("ABI_VFS_BASENAME", variables["ABI_VFS_BASENAME"][0], rename_branch)
	rename_name_length = writer.new(
		"operator_length", parent=rename_branch, inputs={"STRING": [3, rename_name_value, [10, ""]]},
	)
	writer.blocks[rename_name_value]["parent"] = rename_name_length
	rename_name_present = writer.operator(
		"operator_gt", rename_branch,
		{"OPERAND1": [3, rename_name_length, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
	)
	writer.blocks[rename_name_length]["parent"] = rename_name_present
	rename_dot_value = writer.variable("ABI_VFS_BASENAME", variables["ABI_VFS_BASENAME"][0], rename_branch)
	rename_is_dot = writer.operator(
		"operator_equals", rename_branch,
		{"OPERAND1": [3, rename_dot_value, [10, ""]], "OPERAND2": [1, [10, "."]]},
	)
	writer.blocks[rename_dot_value]["parent"] = rename_is_dot
	rename_not_dot = writer.operator("operator_not", rename_branch, {"OPERAND": [2, rename_is_dot]})
	writer.blocks[rename_is_dot]["parent"] = rename_not_dot
	rename_dot_dot_value = writer.variable(
		"ABI_VFS_BASENAME", variables["ABI_VFS_BASENAME"][0], rename_branch
	)
	rename_is_dot_dot = writer.operator(
		"operator_equals", rename_branch,
		{"OPERAND1": [3, rename_dot_dot_value, [10, ""]], "OPERAND2": [1, [10, ".."]]},
	)
	writer.blocks[rename_dot_dot_value]["parent"] = rename_is_dot_dot
	rename_not_dot_dot = writer.operator(
		"operator_not", rename_branch, {"OPERAND": [2, rename_is_dot_dot]},
	)
	writer.blocks[rename_is_dot_dot]["parent"] = rename_not_dot_dot
	rename_mutation_value = writer.variable(
		"ABI_VFS_MUTATION_OK", variables["ABI_VFS_MUTATION_OK"][0], rename_branch
	)
	rename_mutation_allowed = writer.operator(
		"operator_equals", rename_branch,
		{"OPERAND1": [3, rename_mutation_value, [4, "0"]], "OPERAND2": [1, [4, "1"]]},
	)
	writer.blocks[rename_mutation_value]["parent"] = rename_mutation_allowed
	rename_conditions = [
		rename_source_valid, rename_destination_missing, rename_parent_exists,
		rename_parent_is_folder, rename_name_present, rename_not_dot,
		rename_not_dot_dot, rename_mutation_allowed,
	]
	rename_ok = rename_conditions[0]
	for condition in rename_conditions[1:]:
		combined = writer.operator(
			"operator_and", rename_branch,
			{"OPERAND1": [2, rename_ok], "OPERAND2": [2, condition]},
		)
		writer.blocks[rename_ok]["parent"] = combined
		writer.blocks[condition]["parent"] = combined
		rename_ok = combined
	rename_write = writer.new("data_replaceitemoflist", parent=rename_branch, fields={"LIST": ["ABI_VFS_PATHS", paths[0]]})
	rename_row = writer.variable("ABI_VFS_SOURCE_ROW", variables["ABI_VFS_SOURCE_ROW"][0], rename_write)
	rename_text = writer.variable("ABI_VFS_DEST_PATH", variables["ABI_VFS_DEST_PATH"][0], rename_write)
	writer.blocks[rename_write]["inputs"] = {"INDEX": [3, rename_row, [4, "1"]], "ITEM": [3, rename_text, [10, ""]]}
	rename_name = writer.new("data_replaceitemoflist", fields={"LIST": ["ABI_VFS_NAMES", names[0]]})
	rename_name_row = writer.variable("ABI_VFS_SOURCE_ROW", variables["ABI_VFS_SOURCE_ROW"][0], rename_name)
	rename_name_text = writer.variable("ABI_VFS_BASENAME", variables["ABI_VFS_BASENAME"][0], rename_name)
	writer.blocks[rename_name]["inputs"] = {"INDEX": [3, rename_name_row, [4, "1"]], "ITEM": [3, rename_name_text, [10, ""]]}
	rename_parent = writer.new("data_replaceitemoflist", fields={"LIST": ["ABI_VFS_PARENTS", parents[0]]})
	rename_parent_source = writer.variable("ABI_VFS_SOURCE_ROW", variables["ABI_VFS_SOURCE_ROW"][0], rename_parent)
	rename_parent_destination = writer.variable(
		"ABI_VFS_DEST_PARENT_ROW", variables["ABI_VFS_DEST_PARENT_ROW"][0], rename_parent
	)
	writer.blocks[rename_parent]["inputs"] = {
		"INDEX": [3, rename_parent_source, [4, "1"]],
		"ITEM": [3, rename_parent_destination, [4, "1"]],
	}
	rename_key = writer.new(
		"data_replaceitemoflist", fields={"LIST": ["ABI_VFS_KEYS", lists["ABI_VFS_KEYS"][0]]},
	)
	rename_key_row = writer.variable("ABI_VFS_SOURCE_ROW", variables["ABI_VFS_SOURCE_ROW"][0], rename_key)
	rename_key_text = writer.variable("ABI_VFS_DEST_PATH", variables["ABI_VFS_DEST_PATH"][0], rename_key)
	writer.blocks[rename_key]["inputs"] = {
		"INDEX": [3, rename_key_row, [4, "1"]], "ITEM": [3, rename_key_text, [10, ""]],
	}
	rename_mtime = writer.new(
		"data_replaceitemoflist", fields={"LIST": ["ABI_VFS_MTIME", lists["ABI_VFS_MTIME"][0]]},
	)
	rename_mtime_row = writer.variable("ABI_VFS_SOURCE_ROW", variables["ABI_VFS_SOURCE_ROW"][0], rename_mtime)
	rename_clock_value = writer.variable("ABI_VFS_CLOCK", variables["ABI_VFS_CLOCK"][0], rename_mtime)
	writer.blocks[rename_mtime]["inputs"] = {
		"INDEX": [3, rename_mtime_row, [4, "1"]], "ITEM": [3, rename_clock_value, [4, "0"]],
	}
	rename_clock = writer.new(
		"data_changevariableby", fields={"VARIABLE": ["ABI_VFS_CLOCK", variables["ABI_VFS_CLOCK"][0]]},
		inputs={"VALUE": [1, [4, "1"]]},
	)
	rename_revision = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_VFS_REVISION", variables["ABI_VFS_REVISION"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
	rename_true = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]}, inputs={"VALUE": [1, [4, str(TRUE)]]})
	rename_first = writer.chain([
		rename_write, rename_name, rename_parent, rename_key, rename_mtime,
		rename_clock, rename_revision, rename_true,
	], rename_branch)
	writer.blocks[rename_branch]["inputs"] = {"CONDITION": [2, rename_ok], "SUBSTACK": [2, rename_first]}
	syscall_branch(216, [
		read_old, lookup_old, store_old_row, store_old_path, read_new,
		*resolve_rename_destination, check_rename_handles,
		rename_ancestry_set, rename_ancestry_repeat, rename_branch,
	])

	copy_read_source = read_descriptor(1)
	copy_lookup_source = index_current_path()
	copy_read_destination = read_descriptor(2)
	copy_branch = writer.new("control_if")
	copy_index = writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], copy_branch)
	# Recursive copy is outside the current portable profile. Keep the dispatcher
	# branch explicit but unreachable so an accidental profile admission fails closed.
	copy_valid = writer.operator("operator_lt", copy_branch, {"OPERAND1": [3, copy_index, [4, "0"]], "OPERAND2": [1, [4, "0"]]})
	writer.blocks[copy_index]["parent"] = copy_valid
	copy_destination = writer.variable("sys_text", sys_text[0], copy_branch)
	copy_contains = writer.new(
		"data_listcontainsitem", parent=copy_branch,
		inputs={"ITEM": [3, copy_destination, [10, ""]]}, fields={"LIST": ["ABI_VFS_PATHS", paths[0]]},
	)
	writer.blocks[copy_destination]["parent"] = copy_contains
	copy_missing = writer.operator("operator_not", copy_branch, {"OPERAND": [2, copy_contains]})
	writer.blocks[copy_contains]["parent"] = copy_missing
	copy_condition = writer.operator("operator_and", copy_branch, {"OPERAND1": [2, copy_valid], "OPERAND2": [2, copy_missing]})
	writer.blocks[copy_valid]["parent"] = copy_condition
	writer.blocks[copy_missing]["parent"] = copy_condition
	copy_path = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_PATHS", paths[0]]})
	copy_path_text = writer.variable("sys_text", sys_text[0], copy_path)
	writer.blocks[copy_path]["inputs"] = {"ITEM": [3, copy_path_text, [10, ""]]}
	copy_name = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_NAMES", lists["ABI_VFS_NAMES"][0]]})
	copy_name_text = writer.variable("sys_text", sys_text[0], copy_name)
	writer.blocks[copy_name]["inputs"] = {"ITEM": [3, copy_name_text, [10, ""]]}
	copy_steps = [copy_path, copy_name]
	for list_name, entry in (("ABI_VFS_TYPES", types), ("ABI_VFS_CONTENTS", contents)):
		copy_column = writer.new("data_addtolist", fields={"LIST": [list_name, entry[0]]})
		copy_row = writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], copy_column)
		copy_item = writer.new(
			"data_itemoflist", parent=copy_column,
			inputs={"INDEX": [3, copy_row, [4, "1"]]}, fields={"LIST": [list_name, entry[0]]},
		)
		writer.blocks[copy_row]["parent"] = copy_item
		writer.blocks[copy_column]["inputs"] = {"ITEM": [3, copy_item, [10, ""]]}
		copy_steps.append(copy_column)
	copy_parent = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_PARENTS", lists["ABI_VFS_PARENTS"][0]]})
	copy_parent_value = writer.variable("ABI_VFS_CONTEXT_ROW", variables["ABI_VFS_CONTEXT_ROW"][0], copy_parent)
	writer.blocks[copy_parent]["inputs"] = {"ITEM": [3, copy_parent_value, [4, "1"]]}
	copy_steps.append(copy_parent)
	copy_id = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_IDS", lists["ABI_VFS_IDS"][0]]})
	copy_next_id = writer.variable("ABI_VFS_NEXT_ID", variables["ABI_VFS_NEXT_ID"][0], copy_id)
	writer.blocks[copy_id]["inputs"] = {"ITEM": [3, copy_next_id, [4, "0"]]}
	copy_key = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_KEYS", lists["ABI_VFS_KEYS"][0]]})
	copy_key_text = writer.variable("sys_text", sys_text[0], copy_key)
	writer.blocks[copy_key]["inputs"] = {"ITEM": [3, copy_key_text, [10, ""]]}
	copy_alive = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_ALIVE", alive[0]]}, inputs={"ITEM": [1, [4, "1"]]})
	copy_mtime = writer.new("data_addtolist", fields={"LIST": ["ABI_VFS_MTIME", lists["ABI_VFS_MTIME"][0]]})
	copy_clock = writer.variable("ABI_VFS_CLOCK", variables["ABI_VFS_CLOCK"][0], copy_mtime)
	writer.blocks[copy_mtime]["inputs"] = {"ITEM": [3, copy_clock, [4, "0"]]}
	copy_advance_id = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_VFS_NEXT_ID", variables["ABI_VFS_NEXT_ID"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
	copy_advance_clock = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_VFS_CLOCK", variables["ABI_VFS_CLOCK"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
	copy_steps.extend([copy_id, copy_key, copy_alive, copy_mtime, copy_advance_id, copy_advance_clock])
	copy_revision = writer.new("data_changevariableby", fields={"VARIABLE": ["ABI_VFS_REVISION", variables["ABI_VFS_REVISION"][0]]}, inputs={"VALUE": [1, [4, "1"]]})
	copy_steps.append(copy_revision)
	copy_true = writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]}, inputs={"VALUE": [1, [4, str(TRUE)]]})
	copy_steps.append(copy_true)
	copy_first = writer.chain(copy_steps, copy_branch)
	writer.blocks[copy_branch]["inputs"] = {"CONDITION": [2, copy_condition], "SUBSTACK": [2, copy_first]}
	syscall_branch(262, [copy_read_source, copy_lookup_source, copy_read_destination, copy_branch])

	read_size = read_descriptor(1)
	lookup_size = index_current_path()
	size_statement = set_return(lambda parent: writer.new(
		"operator_length", parent=parent,
		inputs={"STRING": [3, writer.new(
			"data_itemoflist", parent=parent,
			inputs={"INDEX": [3, writer.variable("ABI_INDEX", variables["ABI_INDEX"][0], parent), [4, "1"]]},
			fields={"LIST": ["ABI_VFS_CONTENTS", contents[0]]},
		), [10, ""]]},
	))
	size_op = writer.blocks[size_statement]["inputs"]["VALUE"][1]
	size_item = writer.blocks[size_op]["inputs"]["STRING"][1]
	writer.blocks[size_item]["parent"] = size_op
	size_index = writer.blocks[size_item]["inputs"]["INDEX"][1]
	writer.blocks[size_index]["parent"] = size_item
	syscall_branch(263, [read_size, lookup_size, size_statement])

	# Currency uses the canonical offline snapshot. Cross-rate FP32 values are
	# precomputed at build time so Scratch only performs deterministic list reads.
	def bounded_currency_argument(index: int, upper: int, parent: str) -> str:
		low_arg = argument_item(index, parent)
		below_zero = writer.operator(
			"operator_lt", parent,
			{"OPERAND1": [3, low_arg, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
		)
		writer.blocks[low_arg]["parent"] = below_zero
		nonnegative = writer.operator("operator_not", parent, {"OPERAND": [2, below_zero]})
		writer.blocks[below_zero]["parent"] = nonnegative
		high_arg = argument_item(index, parent)
		below_upper = writer.operator(
			"operator_lt", parent,
			{"OPERAND1": [3, high_arg, [4, "0"]], "OPERAND2": [1, [4, str(upper)]]},
		)
		writer.blocks[high_arg]["parent"] = below_upper
		condition = writer.operator(
			"operator_and", parent,
			{"OPERAND1": [2, nonnegative], "OPERAND2": [2, below_upper]},
		)
		writer.blocks[nonnegative]["parent"] = condition
		writer.blocks[below_upper]["parent"] = condition
		return condition

	def set_currency_argument(variable_name: str, index: int) -> str:
		statement = writer.new(
			"data_setvariableto", fields={"VARIABLE": [variable_name, variables[variable_name][0]]},
		)
		value = argument_item(index, statement)
		writer.blocks[statement]["inputs"] = {"VALUE": [3, value, [4, "0"]]}
		return statement

	def currency_range_index(parent: str) -> str:
		value = argument_item(3, parent)
		index = writer.operator(
			"operator_add", parent,
			{"NUM1": [3, value, [4, "0"]], "NUM2": [1, [4, "1"]]},
		)
		writer.blocks[value]["parent"] = index
		return index

	def set_currency_range_value(variable_name: str, list_name: str) -> str:
		statement = writer.new(
			"data_setvariableto", fields={"VARIABLE": [variable_name, variables[variable_name][0]]},
		)
		index = currency_range_index(statement)
		value = writer.new(
			"data_itemoflist", parent=statement,
			inputs={"INDEX": [3, index, [4, "1"]]}, fields={"LIST": [list_name, lists[list_name][0]]},
		)
		writer.blocks[index]["parent"] = value
		writer.blocks[statement]["inputs"] = {"VALUE": [3, value, [4, "0"]]}
		return statement

	def currency_flat_index(parent: str, *, point: bool) -> str:
		if point:
			first = writer.variable("ABI_CURRENCY_FIRST_ROW", variables["ABI_CURRENCY_FIRST_ROW"][0], parent)
			point_arg = argument_item(1, parent)
			row = writer.operator(
				"operator_add", parent,
				{"NUM1": [3, first, [4, "0"]], "NUM2": [3, point_arg, [4, "0"]]},
			)
			writer.blocks[first]["parent"] = row
			writer.blocks[point_arg]["parent"] = row
			row_input: list[Any] = [3, row, [4, "0"]]
		else:
			row_input = [1, [4, str(len(DAILY_RATES) - 1)]]
		row_times = writer.operator(
			"operator_multiply", parent,
			{"NUM1": row_input, "NUM2": [1, [4, str(len(CURRENCY_CODES) ** 2)]]},
		)
		if point:
			writer.blocks[row]["parent"] = row_times
		base = writer.variable("ABI_CURRENCY_BASE", variables["ABI_CURRENCY_BASE"][0], parent)
		base_times = writer.operator(
			"operator_multiply", parent,
			{"NUM1": [3, base, [4, "0"]], "NUM2": [1, [4, str(len(CURRENCY_CODES))]]},
		)
		writer.blocks[base]["parent"] = base_times
		row_and_base = writer.operator(
			"operator_add", parent,
			{"NUM1": [3, row_times, [4, "0"]], "NUM2": [3, base_times, [4, "0"]]},
		)
		writer.blocks[row_times]["parent"] = row_and_base
		writer.blocks[base_times]["parent"] = row_and_base
		quote = writer.variable("ABI_CURRENCY_QUOTE", variables["ABI_CURRENCY_QUOTE"][0], parent)
		with_quote = writer.operator(
			"operator_add", parent,
			{"NUM1": [3, row_and_base, [4, "0"]], "NUM2": [3, quote, [4, "0"]]},
		)
		writer.blocks[row_and_base]["parent"] = with_quote
		writer.blocks[quote]["parent"] = with_quote
		one_based = writer.operator(
			"operator_add", parent,
			{"NUM1": [3, with_quote, [4, "0"]], "NUM2": [1, [4, "1"]]},
		)
		writer.blocks[with_quote]["parent"] = one_based
		return one_based

	currency_count = set_return(lambda parent: writer.new(
		"data_lengthoflist", parent=parent, fields={"LIST": ["ABI_CURRENCY_CODES", lists["ABI_CURRENCY_CODES"][0]]},
	))
	syscall_branch(200, [currency_count])
	code_statement = set_return_text(lambda parent: writer.new(
		"data_itemoflist", parent=parent,
		inputs={"INDEX": [3, writer.operator(
			"operator_add", parent,
			{"NUM1": [3, argument_item(1, parent), [4, "0"]], "NUM2": [1, [4, "1"]]},
		), [4, "1"]]},
		fields={"LIST": ["ABI_CURRENCY_CODES", lists["ABI_CURRENCY_CODES"][0]]},
	))
	code_item = writer.blocks[code_statement]["inputs"]["VALUE"][1]
	code_index = writer.blocks[code_item]["inputs"]["INDEX"][1]
	writer.blocks[code_index]["parent"] = code_item
	code_arg = writer.blocks[code_index]["inputs"]["NUM1"][1]
	writer.blocks[code_arg]["parent"] = code_index
	syscall_branch(201, [code_statement])

	load_currency = writer.new("control_if_else")
	base_valid = bounded_currency_argument(1, len(CURRENCY_CODES), load_currency)
	quote_valid = bounded_currency_argument(2, len(CURRENCY_CODES), load_currency)
	range_valid = bounded_currency_argument(3, 6, load_currency)
	base_quote_valid = writer.operator(
		"operator_and", load_currency,
		{"OPERAND1": [2, base_valid], "OPERAND2": [2, quote_valid]},
	)
	writer.blocks[base_valid]["parent"] = base_quote_valid
	writer.blocks[quote_valid]["parent"] = base_quote_valid
	all_valid = writer.operator(
		"operator_and", load_currency,
		{"OPERAND1": [2, base_quote_valid], "OPERAND2": [2, range_valid]},
	)
	writer.blocks[base_quote_valid]["parent"] = all_valid
	writer.blocks[range_valid]["parent"] = all_valid
	load_steps = [
		set_currency_argument("ABI_CURRENCY_BASE", 1),
		set_currency_argument("ABI_CURRENCY_QUOTE", 2),
		set_currency_argument("ABI_CURRENCY_RANGE", 3),
		set_currency_range_value("ABI_CURRENCY_ROW_KIND", "ABI_CURRENCY_RANGE_KIND"),
		set_currency_range_value("ABI_CURRENCY_FIRST_ROW", "ABI_CURRENCY_RANGE_FIRST"),
		set_currency_range_value("ABI_CURRENCY_POINT_COUNT", "ABI_CURRENCY_RANGE_COUNT"),
	]
	load_rate = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_CURRENCY_RATE", variables["ABI_CURRENCY_RATE"][0]]},
	)
	current_index = currency_flat_index(load_rate, point=False)
	current_rate = writer.new(
		"data_itemoflist", parent=load_rate,
		inputs={"INDEX": [3, current_index, [4, "1"]]},
		fields={"LIST": ["ABI_CURRENCY_DAILY_CROSS", lists["ABI_CURRENCY_DAILY_CROSS"][0]]},
	)
	writer.blocks[current_index]["parent"] = current_rate
	writer.blocks[load_rate]["inputs"] = {"VALUE": [3, current_rate, [4, "0"]]}
	load_steps.extend([
		load_rate,
		writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_CURRENCY_STATUS", variables["ABI_CURRENCY_STATUS"][0]]}, inputs={"VALUE": [1, [4, "2"]]}),
		writer.new("data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]}, inputs={"VALUE": [1, [4, str(TRUE)]]}),
	])
	valid_first = writer.chain(load_steps, load_currency)
	invalid_status = writer.new(
		"data_setvariableto", parent=load_currency,
		fields={"VARIABLE": ["ABI_CURRENCY_STATUS", variables["ABI_CURRENCY_STATUS"][0]]},
		inputs={"VALUE": [1, [4, "3"]]},
	)
	invalid_false = writer.new(
		"data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	invalid_first = writer.chain([invalid_status, invalid_false], load_currency)
	writer.blocks[load_currency]["inputs"] = {
		"CONDITION": [2, all_valid], "SUBSTACK": [2, valid_first], "SUBSTACK2": [2, invalid_first],
	}
	syscall_branch(202, [load_currency])
	for syscall, variable_name in (
		(203, "ABI_CURRENCY_STATUS"), (204, "ABI_CURRENCY_RATE"),
		(205, "ABI_CURRENCY_POINT_COUNT"),
	):
		syscall_branch(syscall, [set_return(
			lambda parent, variable_name=variable_name: writer.variable(variable_name, variables[variable_name][0], parent)
		)])

	def currency_point_valid(parent: str) -> str:
		low_arg = argument_item(1, parent)
		below_zero = writer.operator(
			"operator_lt", parent,
			{"OPERAND1": [3, low_arg, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
		)
		writer.blocks[low_arg]["parent"] = below_zero
		nonnegative = writer.operator("operator_not", parent, {"OPERAND": [2, below_zero]})
		writer.blocks[below_zero]["parent"] = nonnegative
		high_arg = argument_item(1, parent)
		count = writer.variable("ABI_CURRENCY_POINT_COUNT", variables["ABI_CURRENCY_POINT_COUNT"][0], parent)
		below_count = writer.operator(
			"operator_lt", parent,
			{"OPERAND1": [3, high_arg, [4, "0"]], "OPERAND2": [3, count, [4, "0"]]},
		)
		writer.blocks[high_arg]["parent"] = below_count
		writer.blocks[count]["parent"] = below_count
		condition = writer.operator(
			"operator_and", parent,
			{"OPERAND1": [2, nonnegative], "OPERAND2": [2, below_count]},
		)
		writer.blocks[nonnegative]["parent"] = condition
		writer.blocks[below_count]["parent"] = condition
		return condition

	point_valid_branch = writer.new("control_if")
	point_valid = currency_point_valid(point_valid_branch)
	point_kind_branches: list[str] = []
	for kind, list_name in enumerate((
		"ABI_CURRENCY_DAILY_CROSS", "ABI_CURRENCY_WEEKLY_CROSS", "ABI_CURRENCY_MONTHLY_CROSS",
	)):
		kind_branch = writer.new("control_if")
		kind_value = writer.variable("ABI_CURRENCY_ROW_KIND", variables["ABI_CURRENCY_ROW_KIND"][0], kind_branch)
		kind_match = writer.operator(
			"operator_equals", kind_branch,
			{"OPERAND1": [3, kind_value, [4, "0"]], "OPERAND2": [1, [4, str(kind)]]},
		)
		writer.blocks[kind_value]["parent"] = kind_match
		set_point = writer.new(
			"data_setvariableto", parent=kind_branch,
			fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
		)
		flat_index = currency_flat_index(set_point, point=True)
		point_value = writer.new(
			"data_itemoflist", parent=set_point,
			inputs={"INDEX": [3, flat_index, [4, "1"]]}, fields={"LIST": [list_name, lists[list_name][0]]},
		)
		writer.blocks[flat_index]["parent"] = point_value
		writer.blocks[set_point]["inputs"] = {"VALUE": [3, point_value, [4, "0"]]}
		writer.blocks[kind_branch]["inputs"] = {"CONDITION": [2, kind_match], "SUBSTACK": [2, set_point]}
		point_kind_branches.append(kind_branch)
	point_first = writer.chain(point_kind_branches, point_valid_branch)
	writer.blocks[point_valid_branch]["inputs"] = {"CONDITION": [2, point_valid], "SUBSTACK": [2, point_first]}
	syscall_branch(206, [point_valid_branch])

	date_valid_branch = writer.new("control_if")
	date_valid = currency_point_valid(date_valid_branch)
	date_kind_branches: list[str] = []
	for kind, list_name in enumerate((
		"ABI_CURRENCY_DAILY_DATES", "ABI_CURRENCY_WEEKLY_DATES", "ABI_CURRENCY_MONTHLY_DATES",
	)):
		kind_branch = writer.new("control_if")
		kind_value = writer.variable("ABI_CURRENCY_ROW_KIND", variables["ABI_CURRENCY_ROW_KIND"][0], kind_branch)
		kind_match = writer.operator(
			"operator_equals", kind_branch,
			{"OPERAND1": [3, kind_value, [4, "0"]], "OPERAND2": [1, [4, str(kind)]]},
		)
		writer.blocks[kind_value]["parent"] = kind_match
		set_date = writer.new(
			"data_setvariableto", parent=kind_branch,
			fields={"VARIABLE": ["ABI_RETURN_TEXT", variables["ABI_RETURN_TEXT"][0]]},
		)
		first_row = writer.variable("ABI_CURRENCY_FIRST_ROW", variables["ABI_CURRENCY_FIRST_ROW"][0], set_date)
		point_arg = argument_item(1, set_date)
		row = writer.operator(
			"operator_add", set_date,
			{"NUM1": [3, first_row, [4, "0"]], "NUM2": [3, point_arg, [4, "0"]]},
		)
		writer.blocks[first_row]["parent"] = row
		writer.blocks[point_arg]["parent"] = row
		index = writer.operator(
			"operator_add", set_date,
			{"NUM1": [3, row, [4, "0"]], "NUM2": [1, [4, "1"]]},
		)
		writer.blocks[row]["parent"] = index
		date_value = writer.new(
			"data_itemoflist", parent=set_date,
			inputs={"INDEX": [3, index, [4, "1"]]}, fields={"LIST": [list_name, lists[list_name][0]]},
		)
		writer.blocks[index]["parent"] = date_value
		writer.blocks[set_date]["inputs"] = {"VALUE": [3, date_value, [10, ""]]}
		writer.blocks[kind_branch]["inputs"] = {"CONDITION": [2, kind_match], "SUBSTACK": [2, set_date]}
		date_kind_branches.append(kind_branch)
	date_first = writer.chain(date_kind_branches, date_valid_branch)
	writer.blocks[date_valid_branch]["inputs"] = {"CONDITION": [2, date_valid], "SUBSTACK": [2, date_first]}
	syscall_branch(207, [date_valid_branch])
	compiler_diagnostic = (
		"Xe runtime compilation is unavailable in this Scratch project; precompile and "
		"bundle the program with the Xe-to-Scratch exporter."
	)
	for syscall in (220, 255, 290, 291):
		read_source = read_descriptor(1)
		compiler_steps = [read_source]
		for variable_name, value in (
			("ABI_COMPILER_ERROR", compiler_diagnostic), ("ABI_COMPILER_LINE", 1),
			("ABI_COMPILER_COLUMN", 1), ("ABI_COMPILER_ASSEMBLY", ""),
			("ABI_COMPILER_BYTECODE_SIZE", 0),
		):
			primitive = 10 if isinstance(value, str) else 4
			compiler_steps.append(writer.new(
				"data_setvariableto", fields={"VARIABLE": [variable_name, variables[variable_name][0]]},
				inputs={"VALUE": [1, [primitive, str(value)]]},
			))
		if syscall in {255, 291}:
			compiler_steps.append(writer.new(
				"data_setvariableto", fields={"VARIABLE": ["ABI_RETURN_TEXT", variables["ABI_RETURN_TEXT"][0]]},
				inputs={"VALUE": [1, [10, compiler_diagnostic]]},
			))
		else:
			compiler_steps.append(writer.new(
				"data_setvariableto", fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
				inputs={"VALUE": [1, [4, "0"]]},
			))
		syscall_branch(syscall, compiler_steps)
	for syscall, variable_name, string_result in (
		(221, "ABI_COMPILER_ERROR", True), (222, "ABI_COMPILER_LINE", False),
		(223, "ABI_COMPILER_COLUMN", False), (224, "ABI_COMPILER_ASSEMBLY", True),
		(225, "ABI_COMPILER_BYTECODE_SIZE", False),
	):
		setter = writer.new(
			"data_setvariableto",
			fields={"VARIABLE": ["ABI_RETURN_TEXT" if string_result else "ABI_RETURN", variables["ABI_RETURN_TEXT" if string_result else "ABI_RETURN"][0]]},
		)
		value = writer.variable(variable_name, variables[variable_name][0], setter)
		writer.blocks[setter]["inputs"] = {"VALUE": [3, value, [10 if string_result else 4, ""]]}
		syscall_branch(syscall, [setter])

	# One coarse range gate keeps hot graphics/input calls from evaluating every
	# ABI branch. The inner order remains deterministic, including multiple
	# specialized state transitions for one syscall ID.
	for family, branches in sorted(family_branches.items()):
		family_gate = writer.new("control_if")
		family_id_low = writer.arg("id", family_gate)
		below_low = writer.operator(
			"operator_lt", family_gate,
			{"OPERAND1": [3, family_id_low, [4, "0"]], "OPERAND2": [1, [4, str(family * 20)]]},
		)
		writer.blocks[family_id_low]["parent"] = below_low
		at_or_above = writer.operator("operator_not", family_gate, {"OPERAND": [2, below_low]})
		writer.blocks[below_low]["parent"] = at_or_above
		family_id_high = writer.arg("id", family_gate)
		below_high = writer.operator(
			"operator_lt", family_gate,
			{"OPERAND1": [3, family_id_high, [4, "0"]], "OPERAND2": [1, [4, str((family + 1) * 20)]]},
		)
		writer.blocks[family_id_high]["parent"] = below_high
		in_family = writer.operator(
			"operator_and", family_gate,
			{"OPERAND1": [2, at_or_above], "OPERAND2": [2, below_high]},
		)
		writer.blocks[at_or_above]["parent"] = in_family
		writer.blocks[below_high]["parent"] = in_family
		family_first = writer.chain(branches, family_gate)
		writer.blocks[family_gate]["inputs"] = {
			"CONDITION": [2, in_family], "SUBSTACK": [2, family_first],
		}
		body.append(family_gate)
	normalize_bool = writer.new("control_if")
	bool_id = writer.arg("id", normalize_bool)
	is_bool_syscall = writer.new(
		"data_listcontainsitem", parent=normalize_bool,
		inputs={"ITEM": [3, bool_id, [4, "0"]]},
		fields={"LIST": ["ABI_BOOL_SYSCALLS", lists["ABI_BOOL_SYSCALLS"][0]]},
	)
	writer.blocks[bool_id]["parent"] = is_bool_syscall
	bool_value_branch = writer.new("control_if_else", parent=normalize_bool)
	bool_value = writer.variable("ABI_RETURN", variables["ABI_RETURN"][0], bool_value_branch)
	is_false = writer.operator(
		"operator_equals", bool_value_branch,
		{"OPERAND1": [3, bool_value, [4, "0"]], "OPERAND2": [1, [4, "0"]]},
	)
	writer.blocks[bool_value]["parent"] = is_false
	set_false = writer.new(
		"data_setvariableto", parent=bool_value_branch,
		fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
		inputs={"VALUE": [1, [4, "0"]]},
	)
	set_true = writer.new(
		"data_setvariableto", parent=bool_value_branch,
		fields={"VARIABLE": ["ABI_RETURN", variables["ABI_RETURN"][0]]},
		inputs={"VALUE": [1, [4, str(TRUE)]]},
	)
	writer.blocks[bool_value_branch]["inputs"] = {
		"CONDITION": [2, is_false], "SUBSTACK": [2, set_false], "SUBSTACK2": [2, set_true],
	}
	writer.blocks[normalize_bool]["inputs"] = {
		"CONDITION": [2, is_bool_syscall], "SUBSTACK": [2, bool_value_branch],
	}
	body.append(normalize_bool)

	# Mode 1: numeric/bool result.
	mode_num = writer.new("control_if")
	mode_arg = writer.arg("mode", mode_num)
	mode_eq = writer.operator("operator_equals", mode_num, {"OPERAND1": [3, mode_arg, [4, "0"]], "OPERAND2": [1, [4, "1"]]})
	writer.blocks[mode_arg]["parent"] = mode_eq
	push_args = _procedure_signature(vm, "sys_push %n")
	push = writer.procedure_call("sys_push %n", push_args, mode_num)
	return_value = writer.variable("ABI_RETURN", variables["ABI_RETURN"][0], push)
	writer.blocks[push]["inputs"] = {push_args[0]: [3, return_value, [4, "0"]]}
	writer.blocks[mode_num]["inputs"] = {"CONDITION": [2, mode_eq], "SUBSTACK": [2, push]}
	body.append(mode_num)

	# Mode 2: return a newly allocated descriptor for the default string.
	mode_string = writer.new("control_if")
	string_arg = writer.arg("mode", mode_string)
	string_eq = writer.operator("operator_equals", mode_string, {"OPERAND1": [3, string_arg, [4, "0"]], "OPERAND2": [1, [4, "2"]]})
	writer.blocks[string_arg]["parent"] = string_eq
	allocate_args = _procedure_signature(vm, "sys_allocate_string %s")
	allocate = writer.procedure_call("sys_allocate_string %s", allocate_args, mode_string)
	allocate_value = writer.variable("ABI_RETURN_TEXT", variables["ABI_RETURN_TEXT"][0], allocate)
	writer.blocks[allocate]["inputs"] = {allocate_args[0]: [3, allocate_value, [10, ""]]}
	push_descriptor = writer.procedure_call("sys_push %n", push_args, allocate)
	descriptor = writer.variable("sys_result", result_var[0], push_descriptor)
	writer.blocks[push_descriptor]["inputs"] = {push_args[0]: [3, descriptor, [4, "0"]]}
	writer.chain([allocate, push_descriptor], mode_string)
	writer.blocks[mode_string]["inputs"] = {"CONDITION": [2, string_eq], "SUBSTACK": [2, allocate]}
	body.append(mode_string)

	# Mode 3: return one of the already-popped arguments.
	mode_item = writer.new("control_if")
	item_mode_arg = writer.arg("mode", mode_item)
	item_eq = writer.operator("operator_equals", mode_item, {"OPERAND1": [3, item_mode_arg, [4, "0"]], "OPERAND2": [1, [4, "3"]]})
	writer.blocks[item_mode_arg]["parent"] = item_eq
	push_item = writer.procedure_call("sys_push %n", push_args, mode_item)
	index_arg = writer.arg("result index", push_item)
	item_reporter = writer.new("data_itemoflist", parent=push_item, fields={"LIST": ["ABI_ARGS", lists["ABI_ARGS"][0]]})
	writer.blocks[item_reporter]["inputs"] = {"INDEX": [3, index_arg, [4, "1"]]}
	writer.blocks[index_arg]["parent"] = item_reporter
	writer.blocks[push_item]["inputs"] = {push_args[0]: [3, item_reporter, [4, "0"]]}
	writer.blocks[mode_item]["inputs"] = {"CONDITION": [2, item_eq], "SUBSTACK": [2, push_item]}
	body.append(mode_item)

	# Mode 4: deterministic seeded random float converted to IEEE-754 bits.
	mode_rand = writer.new("control_if")
	rand_mode_arg = writer.arg("mode", mode_rand)
	rand_eq = writer.operator("operator_equals", mode_rand, {"OPERAND1": [3, rand_mode_arg, [4, "0"]], "OPERAND2": [1, [4, "4"]]})
	writer.blocks[rand_mode_arg]["parent"] = rand_eq
	prng = variables.get("PRNG_SEED")
	temp = variables.get("temp")
	if prng is None or temp is None:
		raise FullAbiBuildError("VM target is missing PRNG_SEED or temp")
	seed_set = writer.new("data_setvariableto", parent=mode_rand, fields={"VARIABLE": ["PRNG_SEED", prng[0]]})
	seed_value = writer.variable("PRNG_SEED", prng[0], seed_set)
	multiply = writer.operator("operator_multiply", seed_set, {"NUM1": [3, seed_value, [4, "0"]], "NUM2": [1, [4, "1664525"]]})
	writer.blocks[seed_value]["parent"] = multiply
	add = writer.operator("operator_add", seed_set, {"NUM1": [3, multiply, [4, "0"]], "NUM2": [1, [4, "1013904223"]]})
	writer.blocks[multiply]["parent"] = add
	mod = writer.operator("operator_mod", seed_set, {"NUM1": [3, add, [4, "0"]], "NUM2": [1, [4, "4294967296"]]})
	writer.blocks[add]["parent"] = mod
	writer.blocks[seed_set]["inputs"] = {"VALUE": [3, mod, [4, "0"]]}
	fp_proc = "HE_Number %s to FP32 Representation"
	fp_args = _procedure_signature(vm, fp_proc)
	fp_call = writer.procedure_call(fp_proc, fp_args, seed_set)
	seed_report = writer.variable("PRNG_SEED", prng[0], fp_call)
	divide = writer.operator("operator_divide", fp_call, {"NUM1": [3, seed_report, [4, "0"]], "NUM2": [1, [4, "4294967296"]]})
	writer.blocks[seed_report]["parent"] = divide
	writer.blocks[fp_call]["inputs"] = {fp_args[0]: [3, divide, [4, "0"]]}
	push_float = writer.procedure_call("sys_push %n", push_args, fp_call)
	temp_report = writer.variable("temp", temp[0], push_float)
	writer.blocks[push_float]["inputs"] = {push_args[0]: [3, temp_report, [4, "0"]]}
	writer.chain([seed_set, fp_call, push_float], mode_rand)
	writer.blocks[mode_rand]["inputs"] = {"CONDITION": [2, rand_eq], "SUBSTACK": [2, seed_set]}
	body.append(mode_rand)

	writer.blocks[definition]["next"] = writer.chain(body, definition)

	# Locate the sys_dispatch definition and append missing IDs to its sequential body.
	blocks = vm["blocks"]
	prototypes = {
		block_id for block_id, block in blocks.items()
		if block.get("opcode") == "procedures_prototype"
		and block.get("mutation", {}).get("proccode") in {"sys_dispatch %s", "sys_dispatch %n"}
	}
	dispatch_definition = next(
		block for block in blocks.values()
		if block.get("opcode") == "procedures_definition"
		and block.get("inputs", {}).get("custom_block", [None, None])[1] in prototypes
	)
	tail_id = dispatch_definition.get("next")
	while isinstance(tail_id, str) and isinstance(blocks[tail_id].get("next"), str):
		tail_id = blocks[tail_id]["next"]
	if not isinstance(tail_id, str):
		raise FullAbiBuildError("sys_dispatch has no body")
	existing = _dispatch_values(vm)
	wanted = {int(value) for value in SyscallID}
	missing = sorted(wanted - existing)
	branches: list[str] = []
	state_getters = {
		130: "ABI_VOLUME", 132: "ABI_BACKGROUND", 134: "ABI_PALETTE", 180: "ABI_MUSIC_VOLUME",
		182: "ABI_SFX_VOLUME", 184: "ABI_THEME", 186: "ABI_TRANSPARENCY", 188: "ABI_CORNER",
		190: "ABI_ICON_SIZE", 192: "ABI_CLOCK_FORMAT", 194: "ABI_SETTINGS_ENABLED",
		203: "ABI_CURRENCY_STATUS", 204: "ABI_CURRENCY_RATE",
		265: "ABI_VFS_REVISION",
	}
	for syscall in missing:
		contract = abi.get(syscall)
		if contract is None:
			raise FullAbiBuildError(f"no Scratch contract for syscall {syscall}")
		branch = writer.new("control_if")
		id_arg = writer.arg("id", branch)
		equals = writer.operator("operator_equals", branch, {"OPERAND1": [3, id_arg, [4, "0"]], "OPERAND2": [1, [4, str(syscall)]]})
		writer.blocks[id_arg]["parent"] = equals
		call = writer.procedure_call(proc, arg_ids, branch)
		mode = 0 if contract.result == "void" else 2 if contract.result == "string" else 3 if contract.result_index else 4 if contract.default_reporter == "randf" else 1
		writer.blocks[call]["inputs"] = {
			arg_ids[0]: [1, [4, str(syscall)]],
			arg_ids[1]: [1, [4, str(contract.args)]],
			arg_ids[2]: [1, [4, str(mode)]],
			arg_ids[4]: [1, [4, str(contract.result_index)]],
		}
		if syscall in state_getters:
			name = state_getters[syscall]
			reporter = writer.variable(name, variables[name][0], call)
			writer.blocks[call]["inputs"][arg_ids[3]] = [3, reporter, [4, "0"]]
		elif contract.default_reporter:
			kind = contract.default_reporter
			if kind in {"hour", "minute", "year", "month", "date"}:
				reporter = writer.current({"hour": "HOUR", "minute": "MINUTE", "year": "YEAR", "month": "MONTH", "date": "DATE"}[kind], call)
			elif kind in {"mouse_x", "mouse_y", "mouse_down"}:
				reporter = writer.new({"mouse_x": "sensing_mousex", "mouse_y": "sensing_mousey", "mouse_down": "sensing_mousedown"}[kind], parent=call)
			elif kind == "ticks":
				timer = writer.new("sensing_timer", parent=call)
				multiply_ticks = writer.operator("operator_multiply", call, {"NUM1": [3, timer, [4, "0"]], "NUM2": [1, [4, "1000"]]})
				writer.blocks[timer]["parent"] = multiply_ticks
				reporter = writer.operator("operator_mathop", call, {"NUM": [3, multiply_ticks, [4, "0"]]}, {"OPERATOR": ["floor", None]})
				writer.blocks[multiply_ticks]["parent"] = reporter
			else:
				reporter = None
			if reporter is not None:
				writer.blocks[call]["inputs"][arg_ids[3]] = [3, reporter, [4, "0"]]
		if arg_ids[3] not in writer.blocks[call]["inputs"]:
			primitive = 10 if isinstance(contract.default, str) else 4
			writer.blocks[call]["inputs"][arg_ids[3]] = [1, [primitive, str(contract.default)]]
		writer.blocks[branch]["inputs"] = {"CONDITION": [2, equals], "SUBSTACK": [2, call]}
		branches.append(branch)
	writer.chain(branches, tail_id)
	blocks[tail_id]["next"] = branches[0] if branches else None


def _merge_native_explorer(project: dict[str, Any], assets: dict[str, bytes]) -> None:
	explorer, explorer_assets = _load_archive(NATIVE_EXPLORER)
	stage = _target(project, "Stage")
	source_stage = _target(explorer, "Stage")
	stage["costumes"] = deepcopy(source_stage.get("costumes", []))
	stage["currentCostume"] = source_stage.get("currentCostume", 0)
	for monitor in project.get("monitors", []):
		monitor["visible"] = False
	prefix = "xfe_"
	broadcast_map: dict[str, str] = {}
	for old_id, name in source_stage.get("broadcasts", {}).items():
		new_id = prefix + old_id
		broadcast_map[old_id] = new_id
		stage.setdefault("broadcasts", {})[new_id] = name

	base_lists = _named_entries(stage.setdefault("lists", {}))
	base_variables = _named_entries(stage.setdefault("variables", {}))
	block_ids = {old: prefix + old for old in source_stage.get("blocks", {})}
	entity_ids: dict[str, str] = {}
	entity_names: dict[str, str] = {}
	for old_id, entry in source_stage.get("lists", {}).items():
		name = entry[0]
		if name in base_lists:
			entity_ids[old_id] = base_lists[name][0]
			entity_names[old_id] = name
		else:
			new_id = prefix + old_id
			entity_ids[old_id] = new_id
			entity_names[old_id] = name
			stage["lists"][new_id] = deepcopy(entry)
	for old_id, entry in source_stage.get("variables", {}).items():
		name = entry[0]
		if name in base_variables:
			entity_ids[old_id] = base_variables[name][0]
			entity_names[old_id] = name
		else:
			new_id = prefix + old_id
			entity_ids[old_id] = new_id
			entity_names[old_id] = name
			stage["variables"][new_id] = deepcopy(entry)
	for old_id, block in source_stage.get("blocks", {}).items():
		stage.setdefault("blocks", {})[block_ids[old_id]] = _rewrite_block(
			block, block_ids, {**entity_ids, **broadcast_map}, entity_names
		)
	for old_id, comment in source_stage.get("comments", {}).items():
		value = deepcopy(comment)
		if isinstance(value.get("blockId"), str):
			value["blockId"] = block_ids.get(value["blockId"], value["blockId"])
		stage.setdefault("comments", {})[prefix + old_id] = value

	for block in stage.get("blocks", {}).values():
		for field in block.get("fields", {}).values():
			if isinstance(field, list) and len(field) > 1 and field[1] in broadcast_map:
				field[1] = broadcast_map[field[1]]

	for source_target in explorer["targets"]:
		if source_target.get("isStage"):
			continue
		target = deepcopy(source_target)
		target["name"] = "File Explorer · " + str(target.get("name"))
		block_map = {old: prefix + target["name"].replace(" ", "_") + "_" + old for old in target.get("blocks", {})}
		variable_map = {old: prefix + target["name"].replace(" ", "_") + "_" + old for old in target.get("variables", {})}
		list_map = {old: prefix + target["name"].replace(" ", "_") + "_" + old for old in target.get("lists", {})}
		ids = {**entity_ids, **broadcast_map, **variable_map, **list_map}
		names = {old: value[0] for old, value in target.get("variables", {}).items()}
		names.update({old: value[0] for old, value in target.get("lists", {}).items()})
		target["blocks"] = {new_id: _rewrite_block(target["blocks"][old], block_map, ids, names) for old, new_id in block_map.items()}
		target["variables"] = {variable_map[old]: value for old, value in target.get("variables", {}).items()}
		target["lists"] = {list_map[old]: value for old, value in target.get("lists", {}).items()}
		target["comments"] = {}
		target["layerOrder"] = len(project["targets"])
		project["targets"].append(target)
	assets.update(explorer_assets)


def _compile_file_explorer() -> Any:
	from xe_lang.compiler_service import compile_source

	source = FILE_EXPLORER_SOURCE.read_text(encoding="utf-8")
	artifact = compile_source(source, "apps/file_explorer.xe")
	if not artifact.success:
		diagnostics = "; ".join(str(value) for value in artifact.diagnostics)
		raise FullAbiBuildError(f"canonical File Explorer failed to compile: {diagnostics}")
	unsupported = sorted(set(artifact.required_syscalls) - {int(value) for value in SyscallID})
	if unsupported:
		raise FullAbiBuildError(f"File Explorer requires undefined syscall IDs: {unsupported}")
	return artifact


def _inject_file_explorer_program(project: dict[str, Any]) -> dict[str, Any]:
	artifact = _compile_file_explorer()
	stage = _target(project, "Stage")
	programs = [
		entry for entry in stage.get("lists", {}).values()
		if isinstance(entry, list) and len(entry) == 2 and entry[0] == "MEM_PROGRAM"
	]
	if len(programs) != 1:
		raise FullAbiBuildError(f"expected one Stage MEM_PROGRAM list; found {len(programs)}")
	program_payload = tuple(artifact.program[4:])
	programs[0][1] = [f"0x{word:09X}" for word in program_payload]
	xbn_payload = ("\n".join(f"0x{word:09X}" for word in artifact.program) + "\n").encode("utf-8")
	mem_payload = ("\n".join(programs[0][1]) + "\n").encode("utf-8")
	vm = _target(project, "Xenon-131 VM")
	blocks = vm.get("blocks", {})
	launch_chains: list[list[str]] = []
	for block_id, block in blocks.items():
		if block.get("opcode") != "event_whenflagclicked":
			continue
		chain: list[str] = []
		current = block.get("next")
		seen: set[str] = set()
		while isinstance(current, str) and current in blocks and current not in seen:
			seen.add(current)
			value = blocks[current]
			if value.get("opcode") == "procedures_call":
				chain.append(str(value.get("mutation", {}).get("proccode", "")))
			current = value.get("next")
		launch_chains.append(chain)
	if not any("Initialize Memory" in chain and "Run" in chain for chain in launch_chains):
		raise FullAbiBuildError("VM green-flag script does not initialize memory and run MEM_PROGRAM")
	return {
		"kind": "compiled-xe-xbn1",
		"source": "apps/file_explorer.xe",
		"sourceHash": artifact.source_hash,
		"artifactHash": artifact.artifact_hash,
		"xbnSha256": hashlib.sha256(xbn_payload).hexdigest(),
		"memProgramSha256": hashlib.sha256(mem_payload).hexdigest(),
		"compilerProfile": artifact.compiler_profile,
		"header": list(artifact.program[:4]),
		"programWords": len(artifact.program) - 4,
		"textWords": artifact.memory.text_words,
		"dataWords": artifact.memory.data_words,
		"staticWords": artifact.memory.static_words,
		"requiredSyscalls": list(artifact.required_syscalls),
		"launch": {"event": "green flag", "target": "Xenon-131 VM", "sequence": ["Initialize Memory", "Run"]},
	}


def _project_bytes(project: dict[str, Any], assets: dict[str, bytes]) -> bytes:
	members = {
		"project.json": json.dumps(project, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
		**assets,
	}
	stream = BytesIO()
	with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
		for name in sorted(members):
			info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
			info.compress_type = zipfile.ZIP_DEFLATED
			info.create_system = 0
			info.external_attr = 0
			archive.writestr(info, members[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
	return stream.getvalue()


def build_bytes(*, with_explorer: bool = False) -> bytes:
	project, assets = _load_archive(SOURCE_TEMPLATE)
	_merge_graphics_engines(project)
	_materialize_banks(project)
	lowering_stats = _lower_banked_memory_cached(project)
	_neutralize_preallocated_stack_reset(project)
	abi = contracts()
	_append_full_dispatch(project, abi)
	file_explorer: dict[str, Any] | str = "none"
	if with_explorer:
		file_explorer = _inject_file_explorer_program(project)
	expected = {int(value) for value in SyscallID}
	actual = _dispatch_values(_target(project, "Xenon-131 VM"))
	if actual != expected:
		raise FullAbiBuildError(f"dispatcher differs from current ABI: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")
	project.setdefault("meta", {})["xeFullAbi"] = {
		"schemaVersion": 1,
		"sourceSha256": SOURCE_SHA256,
		"mergedTargets": ["Xenon Graphics Engine", "Graphics Engine"],
		"runtimeTargets": ["Stage", "Xenon-131 VM"],
		"memoryBankWords": BANK_WORDS,
		"memoryBanks": BANK_COUNT,
		"workingBanks": [0, 1, 2, 3, 4],
		"standbyBanks": [5, 6, 7, 8, 9],
		"reserveActiveVariable": "MEM_RESERVE_ACTIVE",
		"memoryLowering": lowering_stats,
		"syscallIds": sorted(expected),
		"optionalFallbackSyscalls": sorted(OPTIONAL_FALLBACK_SYSCALLS),
		"contracts": {
			str(syscall): {
				"args": contract.args,
				"result": contract.result,
				"backend": contract.backend,
				**({
					"availability": "optional-fallback",
					"unavailableResult": 0 if syscall == 248 else 0xFFFFFFFF,
				} if syscall in OPTIONAL_FALLBACK_SYSCALLS else {}),
			}
			for syscall, contract in sorted(abi.items())
		},
		"fileExplorer": file_explorer,
	}
	return _project_bytes(project, assets)


def _atomic_write(path: Path, payload: bytes) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
	os.close(fd)
	temporary = Path(temporary_name)
	try:
		temporary.write_bytes(payload)
		os.replace(temporary, path)
	finally:
		temporary.unlink(missing_ok=True)


def _profile_bytes(template: bytes, abi: dict[int, Contract]) -> bytes:
	from xe_lang.compiler_service import capability_for_syscall

	core = {int(value) for value in SyscallID if int(value) < 100}
	supported = core | set(PROVEN_EXACT_PROFILE_BASE)
	unknown = sorted(supported - {int(value) for value in SyscallID})
	if unknown:
		raise FullAbiBuildError(f"profile marks undefined syscall IDs exact: {unknown}")
	explorer = _compile_file_explorer()
	required = set(explorer.required_syscalls)
	missing = sorted(required - supported - OPTIONAL_FALLBACK_SYSCALLS)
	if missing:
		raise FullAbiBuildError(
			f"File Explorer requires unproven Scratch syscall implementations: {missing}"
		)
	fallbacks = required & OPTIONAL_FALLBACK_SYSCALLS
	capabilities = sorted({capability_for_syscall(syscall) for syscall in supported})
	payload = {
		"address_limit": LOGICAL_WORDS,
		"artifact_syscall_overrides": {
			explorer.artifact_hash: sorted(fallbacks),
		},
		"capabilities": [*capabilities, "banked-memory-2m"],
		"distribution": "local-load",
		"mem_data_list": "MEM_DATA_0",
		"mem_program_list": "MEM_PROGRAM",
		"mem_program_target": "Stage",
		"memory_bank_lists": [f"MEM_DATA_{index}" for index in range(BANK_COUNT)],
		"memory_bank_words": BANK_WORDS,
		"name": "Xenon-131 Full ABI Scratch VM",
		"semantics": {
			"accelerator": "unavailable except for a hash-bound tested fallback",
			"clipboard": "project-local fail-closed",
			"compiler": "precompiled programs only",
			"dynamic_media": "requires a future project ROM",
			"filesystem": "project-local session VFS",
			"right_click": "unavailable except for a hash-bound 500 ms left-hold fallback",
		},
		"static_limit": 65_536,
		"supported_syscalls": sorted(supported),
		"template_sha256": hashlib.sha256(template).hexdigest(),
		"version": "8.1.0-full-abi-banked",
	}
	return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_or_check(path: Path, payload: bytes, check: bool) -> None:
	if check:
		if not path.is_file() or path.read_bytes() != payload:
			raise FullAbiBuildError(f"artifact is stale: {path}")
		return
	_atomic_write(path, payload)


def main() -> int:
	parser = argparse.ArgumentParser(description="Build deterministic full-ABI Xenon-131 Scratch projects")
	parser.add_argument("--check", action="store_true")
	parser.add_argument("--copy-downloads", action="store_true")
	args = parser.parse_args()
	try:
		vm = build_bytes()
		explorer = build_bytes(with_explorer=True)
		profile = _profile_bytes(vm, contracts())
		_write_or_check(VM_OUTPUT, vm, args.check)
		_write_or_check(EXPLORER_OUTPUT, explorer, args.check)
		_write_or_check(PROFILE_OUTPUT, profile, args.check)
		if args.copy_downloads and not args.check:
			_write_or_check(Path.home() / "Downloads" / VM_OUTPUT.name, vm, False)
			_write_or_check(Path.home() / "Downloads" / EXPLORER_OUTPUT.name, explorer, False)
		verb = "Verified" if args.check else "Wrote"
		for path, payload in ((VM_OUTPUT, vm), (EXPLORER_OUTPUT, explorer)):
			print(f"{verb} {path} ({len(payload)} bytes, sha256 {hashlib.sha256(payload).hexdigest()})")
		print(f"{verb} {PROFILE_OUTPUT} ({len(profile)} bytes)")
		return 0
	except FullAbiBuildError as error:
		parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
	raise SystemExit(main())
