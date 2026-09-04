import argparse
from collections import deque
import json
import zipfile
from pathlib import Path


ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def compact_id(prefix, number):
    encoded = ""
    while True:
        encoded = ALPHABET[number % len(ALPHABET)] + encoded
        number //= len(ALPHABET)
        if number == 0:
            return prefix + encoded


def rewrite_input(value, block_ids, symbol_ids):
    if not isinstance(value, list):
        return value
    rewritten = list(value)
    for index in range(1, len(rewritten)):
        item = rewritten[index]
        if isinstance(item, str):
            rewritten[index] = block_ids.get(item, item)
        elif isinstance(item, list):
            literal = list(item)
            if literal and literal[0] in (11, 12, 13) and len(literal) > 2:
                literal[2] = symbol_ids.get(literal[2], literal[2])
            rewritten[index] = literal
    return rewritten


def compact_project(project):
    counters = {"B": 0, "V": 0, "L": 0, "R": 0, "C": 0, "A": 0}
    symbol_ids = {}
    for target in project["targets"]:
        for collection, prefix in (("variables", "V"), ("lists", "L"), ("broadcasts", "R")):
            for old_id in target.get(collection, {}):
                symbol_ids.setdefault(old_id, compact_id(prefix, counters[prefix]))
                counters[prefix] += 1

    argument_ids = {}
    for target in project["targets"]:
        for block in target.get("blocks", {}).values():
            mutation = block.get("mutation", {})
            encoded = mutation.get("argumentids")
            if not encoded:
                continue
            for old_id in json.loads(encoded):
                if old_id not in argument_ids:
                    argument_ids[old_id] = compact_id("A", counters["A"])
                    counters["A"] += 1

    for target in project["targets"]:
        block_ids = {
            old_id: compact_id("B", counters["B"] + index)
            for index, old_id in enumerate(target.get("blocks", {}))
        }
        counters["B"] += len(block_ids)
        comment_ids = {
            old_id: compact_id("C", counters["C"] + index)
            for index, old_id in enumerate(target.get("comments", {}))
        }
        counters["C"] += len(comment_ids)

        for collection in ("variables", "lists", "broadcasts"):
            target[collection] = {
                symbol_ids.get(old_id, old_id): value
                for old_id, value in target.get(collection, {}).items()
            }

        rewritten_blocks = {}
        for old_id, block in target.get("blocks", {}).items():
            block["next"] = block_ids.get(block.get("next"), block.get("next"))
            block["parent"] = block_ids.get(block.get("parent"), block.get("parent"))
            if block.get("comment") is not None:
                block["comment"] = comment_ids.get(block["comment"], block["comment"])
            block["inputs"] = {
                argument_ids.get(name, name): rewrite_input(value, block_ids, symbol_ids)
                for name, value in block.get("inputs", {}).items()
            }
            for field in block.get("fields", {}).values():
                if isinstance(field, list) and len(field) > 1:
                    field[1] = symbol_ids.get(field[1], field[1])
            mutation = block.get("mutation")
            if mutation and mutation.get("argumentids"):
                mutation["argumentids"] = json.dumps(
                    [argument_ids.get(item, item) for item in json.loads(mutation["argumentids"])],
                    separators=(",", ":"),
                )
            rewritten_blocks[block_ids[old_id]] = block
        target["blocks"] = rewritten_blocks

        rewritten_comments = {}
        for old_id, comment in target.get("comments", {}).items():
            if comment.get("blockId") is not None:
                comment["blockId"] = block_ids.get(comment["blockId"], comment["blockId"])
            rewritten_comments[comment_ids[old_id]] = comment
        target["comments"] = rewritten_comments

    for monitor in project.get("monitors", []):
        monitor["id"] = symbol_ids.get(monitor.get("id"), monitor.get("id"))
        if isinstance(monitor.get("params"), dict):
            for key, value in list(monitor["params"].items()):
                monitor["params"][key] = symbol_ids.get(value, value)
    return counters


def input_block_ids(value, blocks):
    if not isinstance(value, list):
        return []
    return [item for item in value[1:] if isinstance(item, str) and item in blocks]


def prune_unreachable_blocks(project):
    removed = 0
    for target in project["targets"]:
        blocks = target.get("blocks", {})
        procedures = {}
        for block_id, block in blocks.items():
            if block.get("opcode") != "procedures_definition":
                continue
            prototype_id = block.get("inputs", {}).get("custom_block", [None, None])[1]
            code = blocks.get(prototype_id, {}).get("mutation", {}).get("proccode")
            if code:
                procedures[code] = block_id
        pending = deque(
            block_id
            for block_id, block in blocks.items()
            if block.get("topLevel") and block.get("opcode") != "procedures_definition"
        )
        reached = set()
        while pending:
            block_id = pending.popleft()
            if block_id in reached or block_id not in blocks:
                continue
            reached.add(block_id)
            block = blocks[block_id]
            if block.get("next"):
                pending.append(block["next"])
            for value in block.get("inputs", {}).values():
                pending.extend(input_block_ids(value, blocks))
            if block.get("opcode") == "procedures_call":
                definition = procedures.get(block.get("mutation", {}).get("proccode"))
                if definition:
                    pending.append(definition)
        removed += len(blocks) - len(reached)
        target["blocks"] = {key: value for key, value in blocks.items() if key in reached}
        target["comments"] = {
            key: value
            for key, value in target.get("comments", {}).items()
            if value.get("blockId") is None or value.get("blockId") in reached
        }
    return removed


def write_archive(source, destination, project_bytes):
    with zipfile.ZipFile(source, "r") as archive_in:
        infos = archive_in.infolist()
        with zipfile.ZipFile(destination, "w") as archive_out:
            for info in infos:
                payload = project_bytes if info.filename == "project.json" else archive_in.read(info.filename)
                archive_out.writestr(info, payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    with zipfile.ZipFile(args.source, "r") as archive:
        original = archive.read("project.json")
    project = json.loads(original)
    removed = prune_unreachable_blocks(project)
    counters = compact_project(project)
    encoded = json.dumps(project, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    write_archive(args.source, args.destination, encoded)
    print(f"project.json {len(original)} -> {len(encoded)} bytes")
    print(f"unreachable blocks removed={removed}")
    print("ids " + " ".join(f"{key}={value}" for key, value in counters.items()))


if __name__ == "__main__":
    main()
