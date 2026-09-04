import json,zipfile,sys
p=json.loads(zipfile.ZipFile(sys.argv[1]).read('project.json'))
used=set()
def walk(x):
 if isinstance(x,list):
  if len(x)>=3 and x[0] in (11,12,13) and isinstance(x[2],str): used.add(x[2])
  for y in x: walk(y)
 elif isinstance(x,dict):
  for k,y in x.items():
   if k in ('fields',):
    for z in y.values():
     if isinstance(z,list) and len(z)>1 and isinstance(z[1],str): used.add(z[1])
   walk(y)
for t in p['targets']:
 for b in t.get('blocks',{}).values(): walk({'inputs':b.get('inputs',{}),'fields':b.get('fields',{})})
for m in p.get('monitors',[]):
 if isinstance(m.get('id'),str): used.add(m['id'])
 walk(m.get('params',{}))
for t in p['targets']:
 for kind in ('variables','lists','broadcasts'):
  vals=t.get(kind,{})
  dead={k:v for k,v in vals.items() if k not in used}
  if dead:
   size=sum(len(json.dumps({k:v},separators=(',',':'))) for k,v in dead.items())
   nonempty=sum(bool(v[1] if isinstance(v,list) and len(v)>1 else None) for v in dead.values())
   print(t['name'],kind,len(dead),'nonempty',nonempty,'rough',size)
   for k,v in list(dead.items())[:10]: print(' ',k,v[0],len(v[1]) if kind=='lists' else v[1])
