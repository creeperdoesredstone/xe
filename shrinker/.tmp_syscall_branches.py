import json,zipfile,sys
from collections import deque
p=json.loads(zipfile.ZipFile(sys.argv[1]).read('project.json'))
b=next(t['blocks'] for t in p['targets'] if t.get('name')=='VM')
defs={}
for k,v in b.items():
 if v.get('opcode')=='procedures_definition':
  x=v.get('inputs',{}).get('custom_block',[0,None])[1]; c=b.get(x,{}).get('mutation',{}).get('proccode')
  if c: defs[c]=k
def refs(v):
 if not isinstance(v,list): return []
 return [x for x in v[1:] if isinstance(x,str) and x in b]
def reachable(root):
 q=deque([root]); out=set()
 while q:
  x=q.popleft()
  if not x or x in out or x not in b: continue
  out.add(x); q.append(b[x].get('next'))
  for v in b[x].get('inputs',{}).values(): q.extend(refs(v))
 return out
def arglit(cid):
 c=b[cid]; condrefs=refs(c.get('inputs',{}).get('CONDITION'))
 if len(condrefs)!=1:return None
 e=b[condrefs[0]]
 if e.get('opcode')!='operator_equals':return None
 lit=None; args=[]
 for v in e.get('inputs',{}).values():
  for r in refs(v):
   q=b[r]
   if q.get('opcode')=='argument_reporter_string_number': args.append(q.get('fields',{}).get('VALUE',[None])[0])
  for x in v[1:] if isinstance(v,list) else []:
   if isinstance(x,list) and len(x)>1:
    try: lit=int(float(x[1]))
    except: pass
 return (tuple(args),lit) if args and lit is not None else None
for code,d in defs.items():
 if 'syscall' not in code.lower(): continue
 rows=[]
 for x in reachable(d):
  if b[x].get('opcode') in ('control_if','control_if_else'):
   z=arglit(x)
   if z:rows.append((z[1],x,z[0]))
 print(code,len(reachable(d)),len(rows),sorted(rows))
