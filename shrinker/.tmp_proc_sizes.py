import json
import sys
import zipfile


with zipfile.ZipFile(sys.argv[1]) as archive:
    project = json.loads(archive.read("project.json"))


def descendants(blocks, root):
    found = set()
    pending = [root]
    while pending:
        block_id = pending.pop()
        if block_id in found or block_id not in blocks:
            continue
        found.add(block_id)
        block = blocks[block_id]
        if block.get("next"):
            pending.append(block["next"])
        for value in block.get("inputs", {}).values():
            if isinstance(value, list):
                pending.extend(item for item in value[1:] if isinstance(item, str) and item in blocks)
    return found


for target in project["targets"]:
    rows = []
    blocks = target.get("blocks", {})
    for block_id, block in blocks.items():
        if block.get("opcode") != "procedures_definition":
            continue
        prototype = blocks[block["inputs"]["custom_block"][1]]
        code = prototype["mutation"]["proccode"]
        members = descendants(blocks, block_id)
        rows.append((len(members), code, block_id))
    if rows:
        print("TARGET", target["name"])
        for row in sorted(rows, reverse=True)[:80]:
            print(*row, sep="\t")
