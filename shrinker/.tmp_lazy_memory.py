import argparse
import copy
import json
from pathlib import Path
import zipfile


VM_TARGET = "VM"
DATA_BANK_NAMES = {f"MEM::data_{index}" for index in range(5)}
PREALLOCATED_LISTS = DATA_BANK_NAMES | {"MEM::stack_data", "MEM::stack_call"}


def input_block_ids(value, blocks):
    if not isinstance(value, list):
        return []
    return [item for item in value[1:] if isinstance(item, str) and item in blocks]


def procedure_definition(blocks, proccode):
    for block_id, block in blocks.items():
        if block.get("opcode") != "procedures_definition":
            continue
        prototype_id = block.get("inputs", {}).get("custom_block", [None, None])[1]
        prototype = blocks.get(prototype_id, {})
        if prototype.get("mutation", {}).get("proccode") == proccode:
            return block_id
    raise ValueError(f"procedure was not found: {proccode}")


def procedure_members(blocks, definition_id):
    found = {definition_id}
    pending = [blocks[definition_id].get("next")]
    while pending:
        block_id = pending.pop()
        if block_id is None or block_id in found or block_id not in blocks:
            continue
        found.add(block_id)
        block = blocks[block_id]
        if block.get("next"):
            pending.append(block["next"])
        for value in block.get("inputs", {}).values():
            pending.extend(input_block_ids(value, blocks))
    return found


def unique_id(blocks, requested):
    if requested not in blocks:
        return requested
    suffix = 2
    while f"{requested}_{suffix}" in blocks:
        suffix += 1
    return f"{requested}_{suffix}"


def clone_input(blocks, value, parent, prefix, counter):
    cloned = copy.deepcopy(value)
    for index in range(1, len(cloned)):
        item = cloned[index]
        if not isinstance(item, str) or item not in blocks:
            continue
        counter[0] += 1
        new_id = f"{prefix}_reporter_{counter[0]}"
        original = blocks[item]
        duplicate = copy.deepcopy(original)
        duplicate["parent"] = parent
        duplicate["topLevel"] = False
        duplicate["next"] = None
        duplicate["inputs"] = {
            name: clone_input(blocks, child, new_id, prefix, counter)
            for name, child in original.get("inputs", {}).items()
        }
        blocks[new_id] = duplicate
        cloned[index] = new_id
    return cloned


def replace_parent_reference(blocks, old_id, new_id):
    old = blocks[old_id]
    parent_id = old.get("parent")
    if parent_id not in blocks:
        raise ValueError(f"{old_id} has no valid parent")
    parent = blocks[parent_id]
    replaced = False
    if parent.get("next") == old_id:
        parent["next"] = new_id
        replaced = True
    for value in parent.get("inputs", {}).values():
        if not isinstance(value, list):
            continue
        for index in range(1, len(value)):
            if value[index] == old_id:
                value[index] = new_id
                replaced = True
    if not replaced:
        raise ValueError(f"parent {parent_id} does not reference {old_id}")
    return parent_id


def add_growth_loop(blocks, write_id, label):
    write = blocks[write_id]
    if write.get("opcode") != "data_replaceitemoflist":
        raise ValueError(f"{write_id} is not a list replacement")
    list_field = copy.deepcopy(write["fields"]["LIST"])
    index_input = write["inputs"]["INDEX"]
    prefix = f"lazy_mem_{label}"
    repeat_id = unique_id(blocks, f"{prefix}_grow")
    subtract_id = unique_id(blocks, f"{prefix}_missing")
    length_id = unique_id(blocks, f"{prefix}_length")
    add_id = unique_id(blocks, f"{prefix}_append_zero")

    parent_id = replace_parent_reference(blocks, write_id, repeat_id)
    counter = [0]
    cloned_index = clone_input(blocks, index_input, subtract_id, prefix, counter)

    blocks[repeat_id] = {
        "fields": {},
        "inputs": {
            "SUBSTACK": [2, add_id],
            "TIMES": [3, subtract_id, [4, "0"]],
        },
        "next": write_id,
        "opcode": "control_repeat",
        "parent": parent_id,
        "shadow": False,
        "topLevel": False,
    }
    blocks[subtract_id] = {
        "fields": {},
        "inputs": {
            "NUM1": cloned_index,
            "NUM2": [3, length_id, [4, "0"]],
        },
        "next": None,
        "opcode": "operator_subtract",
        "parent": repeat_id,
        "shadow": False,
        "topLevel": False,
    }
    blocks[length_id] = {
        "fields": {"LIST": list_field},
        "inputs": {},
        "next": None,
        "opcode": "data_lengthoflist",
        "parent": subtract_id,
        "shadow": False,
        "topLevel": False,
    }
    blocks[add_id] = {
        "fields": {"LIST": list_field},
        "inputs": {"ITEM": [1, [10, "0"]]},
        "next": None,
        "opcode": "data_addtolist",
        "parent": repeat_id,
        "shadow": False,
        "topLevel": False,
    }
    write["parent"] = repeat_id


def find_eager_preallocation(blocks):
    init_id = procedure_definition(blocks, "VM::Init")
    init_members = procedure_members(blocks, init_id)
    matches = []
    for block_id in init_members:
        block = blocks[block_id]
        if block.get("opcode") != "control_repeat":
            continue
        if block.get("inputs", {}).get("TIMES") != [1, [6, "200000"]]:
            continue
        substack_id = block.get("inputs", {}).get("SUBSTACK", [None, None])[1]
        names = set()
        current = substack_id
        while current in blocks:
            child = blocks[current]
            if child.get("opcode") != "data_addtolist":
                break
            names.add(child.get("fields", {}).get("LIST", [None])[0])
            current = child.get("next")
        if names == PREALLOCATED_LISTS and current is None:
            matches.append(block_id)
    if len(matches) != 1:
        raise ValueError(f"expected one 200,000-item memory preallocation loop, found {matches!r}")
    return matches[0]


def remove_eager_preallocation(blocks):
    repeat_id = find_eager_preallocation(blocks)
    repeat = blocks[repeat_id]
    parent_id = repeat.get("parent")
    next_id = repeat.get("next")
    if parent_id not in blocks or blocks[parent_id].get("next") != repeat_id:
        raise ValueError("preallocation loop is not in the expected VM::Init chain")
    blocks[parent_id]["next"] = next_id
    if next_id in blocks:
        blocks[next_id]["parent"] = parent_id
    found = {repeat_id}
    pending = input_block_ids(repeat.get("inputs", {}).get("SUBSTACK"), blocks)
    while pending:
        block_id = pending.pop()
        if block_id in found:
            continue
        found.add(block_id)
        block = blocks[block_id]
        if block.get("next"):
            pending.append(block["next"])
        for value in block.get("inputs", {}).values():
            pending.extend(input_block_ids(value, blocks))
    body_lists = {
        blocks[block_id].get("fields", {}).get("LIST", [None])[0]
        for block_id in found - {repeat_id}
        if blocks[block_id].get("opcode") == "data_addtolist"
    }
    if body_lists != PREALLOCATED_LISTS:
        raise ValueError(f"unexpected preallocation lists: {sorted(body_lists)!r}")
    for block_id in found:
        del blocks[block_id]
    return len(found), parent_id, next_id


def add_bootstrap_words(blocks, parent_id, first_write):
    repeat_id = unique_id(blocks, "lazy_mem_bootstrap_data_0")
    add_id = unique_id(blocks, "lazy_mem_bootstrap_data_0_append")
    if blocks[parent_id].get("next") != first_write:
        raise ValueError("VM::Init bootstrap insertion point is not in the expected state")
    list_field = copy.deepcopy(blocks[first_write]["fields"]["LIST"])
    blocks[parent_id]["next"] = repeat_id
    blocks[repeat_id] = {
        "fields": {},
        "inputs": {
            "SUBSTACK": [2, add_id],
            "TIMES": [1, [6, "8"]],
        },
        "next": first_write,
        "opcode": "control_repeat",
        "parent": parent_id,
        "shadow": False,
        "topLevel": False,
    }
    blocks[add_id] = {
        "fields": {"LIST": list_field},
        "inputs": {"ITEM": [1, [10, "0"]]},
        "next": None,
        "opcode": "data_addtolist",
        "parent": repeat_id,
        "shadow": False,
        "topLevel": False,
    }
    blocks[first_write]["parent"] = repeat_id


def find_writes(blocks, proccode, list_names):
    definition_id = procedure_definition(blocks, proccode)
    members = procedure_members(blocks, definition_id)
    found = []
    for block_id in members:
        block = blocks[block_id]
        if block.get("opcode") != "data_replaceitemoflist":
            continue
        name = block.get("fields", {}).get("LIST", [None])[0]
        if name in list_names:
            found.append((block_id, name.removeprefix("MEM::")))
    return found


def validate_graph(project):
    for target in project["targets"]:
        blocks = target.get("blocks", {})
        for block_id, block in blocks.items():
            for linked in (block.get("next"), block.get("parent")):
                if linked is not None and linked not in blocks:
                    raise ValueError(f"{target['name']}:{block_id} links missing block {linked}")
            for value in block.get("inputs", {}).values():
                for linked in input_block_ids(value, blocks):
                    if linked not in blocks:
                        raise ValueError(f"{target['name']}:{block_id} input is missing {linked}")


def write_archive(source, destination, project_bytes):
    with zipfile.ZipFile(source, "r") as archive_in:
        with zipfile.ZipFile(destination, "w") as archive_out:
            for info in archive_in.infolist():
                payload = project_bytes if info.filename == "project.json" else archive_in.read(info.filename)
                archive_out.writestr(info, payload)


def transform(source, destination):
    with zipfile.ZipFile(source, "r") as archive:
        original = archive.read("project.json")
    project = json.loads(original)
    vm = next((target for target in project["targets"] if target["name"] == VM_TARGET), None)
    if vm is None:
        raise ValueError("VM target was not found")
    blocks = vm["blocks"]
    removed, init_parent, first_boot_write = remove_eager_preallocation(blocks)
    if blocks[first_boot_write].get("fields", {}).get("LIST", [None])[0] != "MEM::data_0":
        raise ValueError("first post-allocation VM::Init write is not MEM::data_0")
    add_bootstrap_words(blocks, init_parent, first_boot_write)
    writes = find_writes(
        blocks,
        "VM::Write %s to Index (1-indexed) %s",
        DATA_BANK_NAMES,
    )
    writes += find_writes(blocks, "VM::Push %s to Data Stack", {"MEM::stack_data"})
    writes += find_writes(blocks, "VM::Push Current IP to Call Stack", {"MEM::stack_call"})
    if len(writes) != 7 or {name for _, name in writes} != {
        "data_0", "data_1", "data_2", "data_3", "data_4", "stack_data", "stack_call"
    }:
        raise ValueError(f"unexpected lazy-growth write set: {writes!r}")
    for write_id, label in writes:
        add_growth_loop(blocks, write_id, label)
    validate_graph(project)
    encoded = json.dumps(project, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    write_archive(source, destination, encoded)
    return len(original), len(encoded), removed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    before, after, removed = transform(args.source, args.destination)
    print(f"project.json {before} -> {after} bytes")
    print(f"removed eager-allocation blocks={removed}")
    print("patched lazy-growth writes=7")


if __name__ == "__main__":
    main()
