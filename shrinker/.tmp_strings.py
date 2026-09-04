import collections
import json
import sys
import zipfile


with zipfile.ZipFile(sys.argv[1]) as archive:
    project = json.loads(archive.read("project.json"))
keys = collections.Counter()
values = collections.Counter()


def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            keys[key] += 1
            walk(item)
    elif isinstance(value, list):
        for item in value:
            walk(item)
    elif isinstance(value, str):
        values[value] += 1


walk(project)
print("KEYS")
for value, count in keys.most_common():
    cost = (len(value) + 3) * count
    if cost < 1000:
        continue
    print(cost, count, repr(value))
print("VALUES")
for value, count in sorted(values.items(), key=lambda item: (len(item[0]) + 2) * item[1], reverse=True)[:160]:
    print((len(value) + 2) * count, count, repr(value))
