import json
import sys
import zipfile
from collections import deque


path = sys.argv[1]
with zipfile.ZipFile(path) as archive:
    project = json.loads(archive.read("project.json"))


def refs(value, blocks):
    found = []
    if isinstance(value, list):
        for item in value[1:]:
            if isinstance(item, str) and item in blocks:
                found.append(item)
    return found


for target in project["targets"]:
    blocks = target.get("blocks", {})
    procedures = {}
    for block_id, block in blocks.items():
        if block.get("opcode") != "procedures_definition":
            continue
        custom = block.get("inputs", {}).get("custom_block", [None, None])[1]
        prototype = blocks.get(custom, {})
        code = prototype.get("mutation", {}).get("proccode")
        if code:
            procedures[code] = block_id

    roots = [
        block_id
        for block_id, block in blocks.items()
        if block.get("topLevel") and block.get("opcode") != "procedures_definition"
    ]
    queue = deque(roots)
    reached = set()
    while queue:
        block_id = queue.popleft()
        if block_id in reached or block_id not in blocks:
            continue
        reached.add(block_id)
        block = blocks[block_id]
        if block.get("next"):
            queue.append(block["next"])
        for value in block.get("inputs", {}).values():
            queue.extend(refs(value, blocks))
        if block.get("opcode") == "procedures_call":
            definition = procedures.get(block.get("mutation", {}).get("proccode"))
            if definition:
                queue.append(definition)

    unreachable = set(blocks) - reached
    encoded = sum(len(json.dumps({key: blocks[key]}, separators=(",", ":"))) - 2 for key in unreachable)
    dead_defs = []
    for code, block_id in procedures.items():
        if block_id in unreachable:
            dead_defs.append(code)
    print(
        target["name"],
        f"blocks={len(blocks)} reached={len(reached)} unreachable={len(unreachable)}",
        f"approx_bytes={encoded} dead_defs={len(dead_defs)}",
    )
    for code in dead_defs[:30]:
        print("  ", code)
