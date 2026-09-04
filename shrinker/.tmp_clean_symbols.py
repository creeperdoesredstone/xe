import json,zipfile,sys
from pathlib import Path

src=Path(sys.argv[1]); dst=Path(sys.argv[2])
with zipfile.ZipFile(src) as z:
 infos=z.infolist(); payloads={i.filename:z.read(i.filename) for i in infos}; project=json.loads(payloads['project.json'])
used=set()
def scan(value):
 if isinstance(value,list):
  if len(value)>=3 and value[0] in (11,12,13) and isinstance(value[2],str): used.add(value[2])
  for item in value: scan(item)
 elif isinstance(value,dict):
  for item in value.values(): scan(item)
for target in project['targets']:
 for block in target.get('blocks',{}).values():
  for field in block.get('fields',{}).values():
   if isinstance(field,list) and len(field)>1 and isinstance(field[1],str): used.add(field[1])
  scan(block.get('inputs',{}))
for monitor in project.get('monitors',[]):
 if isinstance(monitor.get('id'),str): used.add(monitor['id'])
 scan(monitor.get('params',{}))
removed={}
for target in project['targets']:
 for kind in ('variables','lists','broadcasts'):
  collection=target.get(kind,{})
  dead=[key for key in collection if key not in used]
  for key in dead: del collection[key]
  removed[kind]=removed.get(kind,0)+len(dead)
encoded=json.dumps(project,ensure_ascii=True,separators=(',',':')).encode()
payloads['project.json']=encoded
with zipfile.ZipFile(dst,'w') as out:
 for i in infos: out.writestr(i,payloads[i.filename])
print('removed',removed,'json',len(encoded))
