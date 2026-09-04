import zipfile,json,sys
from collections import deque
p=json.loads(zipfile.ZipFile(sys.argv[1]).read('project.json')); b=next(t['blocks'] for t in p['targets'] if t['name']=='VM')
calls=[k for k,v in b.items() if v.get('opcode')=='procedures_call' and v.get('mutation',{}).get('proccode')=='GE::Set Pixel Block | color: %s value: %s']
def after(x):
 q=b[x]
 if q.get('next'): return [q['next']]
 parent=q.get('parent')
 while parent and parent in b:
  q=b[parent]
  if q.get('next'): return [q['next']]
  parent=q.get('parent')
 return []
def edges(x):
 q=b[x]; out=[]
 for n,v in q.get('inputs',{}).items():
  if n.startswith('SUBSTACK') and isinstance(v,list) and len(v)>1 and isinstance(v[1],str): out.append(v[1])
 out += after(x)
 return out
for start in calls:
 if b[start].get('opcode')=='procedures_prototype': continue
 q=deque([(start,[])]); seen=set(); found=[]
 while q and len(found)<4:
  x,path=q.popleft()
  if x in seen or len(path)>35: continue
  seen.add(x); op=b[x]['opcode']; code=b[x].get('mutation',{}).get('proccode')
  p2=path+[(x,code or op)]
  if op=='pen_stamp': found.append(p2); continue
  for n in edges(x): q.append((n,p2))
 print('\n',start)
 for path in found:
  print(' -> '.join(f'{x}:{o}' for x,o in path))
