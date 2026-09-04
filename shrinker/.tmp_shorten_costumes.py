import json,re,sys,zipfile
from pathlib import Path

src=Path(sys.argv[1]); dst=Path(sys.argv[2])
with zipfile.ZipFile(src) as z:
 infos=z.infolist(); payloads={i.filename:z.read(i.filename) for i in infos}; project=json.loads(payloads['project.json'])
renamed=0
for target in project['targets']:
 for costume in target.get('costumes',[]):
  match=re.fullmatch(r'xe_p([0-5])_c([0-9]{2})_([0-9]{2})',costume.get('name',''))
  if match:
   costume['name']=f'p{match.group(1)}c{match.group(2)}m{match.group(3)}'; renamed+=1
if renamed!=3072: raise RuntimeError(f'expected 3072 palette costumes, found {renamed}')
encoded=json.dumps(project,ensure_ascii=True,separators=(',',':')).encode()
payloads['project.json']=encoded
with zipfile.ZipFile(dst,'w') as out:
 for i in infos: out.writestr(i,payloads[i.filename])
print('renamed',renamed,'json',len(encoded))
