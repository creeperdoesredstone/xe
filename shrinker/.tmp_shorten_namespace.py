import json,zipfile,sys
from pathlib import Path

src=Path(sys.argv[1]); dst=Path(sys.argv[2])
with zipfile.ZipFile(src) as z:
 infos=z.infolist(); payloads={i.filename:z.read(i.filename) for i in infos}; project=json.loads(payloads['project.json'])
changed=0
def rewrite(value):
 global changed
 if isinstance(value,str):
  new=value.replace('XenonOS: ','XO: ')
  changed += new != value
  return new
 if isinstance(value,list): return [rewrite(item) for item in value]
 if isinstance(value,dict): return {key:rewrite(item) for key,item in value.items()}
 return value
project=rewrite(project)
encoded=json.dumps(project,ensure_ascii=True,separators=(',',':')).encode()
payloads['project.json']=encoded
with zipfile.ZipFile(dst,'w') as out:
 for i in infos: out.writestr(i,payloads[i.filename])
print('renamed strings',changed,'json',len(encoded))
