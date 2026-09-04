import copy
import json
import sys
import zipfile


with zipfile.ZipFile(sys.argv[1]) as archive:
    base = json.loads(archive.read("project.json"))


def size(project):
    return len(json.dumps(project, separators=(",", ":")).encode())


def report(name, transform):
    project = copy.deepcopy(base)
    transform(project)
    print(name, size(project), size(base) - size(project))


def no_baked(project):
    vm = next(target for target in project["targets"] if target["name"] == "VM")
    vm["costumes"] = [costume for costume in vm["costumes"] if not costume["name"].startswith("xe_p")]


def no_programs(project):
    stage = project["targets"][0]
    for value in stage["lists"].values():
        if value[0].startswith("XenonOS: program "):
            value[1] = []


def short_procedures(project):
    names = {}
    for target in project["targets"]:
        for block in target.get("blocks", {}).values():
            mutation = block.get("mutation")
            if mutation and mutation.get("proccode"):
                names.setdefault(mutation["proccode"], "P" + str(len(names)))
                mutation["proccode"] = names[mutation["proccode"]]


def short_symbols(project):
    names = {}
    for target in project["targets"]:
        for collection in ("variables", "lists", "broadcasts"):
            for value in target.get(collection, {}).values():
                if isinstance(value, list):
                    names.setdefault(value[0], "S" + str(len(names)))
                    value[0] = names[value[0]]
        for block in target.get("blocks", {}).values():
            for field in block.get("fields", {}).values():
                if field and field[0] in names:
                    field[0] = names[field[0]]


report("base", lambda project: None)
report("no_baked", no_baked)
report("no_programs", no_programs)
report("short_procedures", short_procedures)
report("short_symbols", short_symbols)
report("combined", lambda p: [f(p) for f in (no_baked, no_programs, short_procedures, short_symbols)])
