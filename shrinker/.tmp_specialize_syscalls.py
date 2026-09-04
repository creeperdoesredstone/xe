import json,zipfile,sys
from collections import deque
from pathlib import Path

USED={1,5,6,9,10,12,20,21,22,28,29,70,102,103,105,106,107,108,109,110,111,113,114,117,118,119,121,122,123,124,125,126,127,128,129,130,132,134,139,140,142,143,145,150,152,160,162,164,170,171,180,182,184,187,188,190,192,194,200,201,202,203,204,205,206,207,209,210,211,212,213,214,215,216,217,246,248,253,254,261,262,265,276,292,293,295,296,302,304,306}
PROC_CODES={'VM::Dispatch Syscall %s for process %s','MP::After syscall %s','MP::Extended syscall %s','MP::VFS syscall %s','MP::Service syscall %s','MP::Compiler syscall %s'}
EXTRA_UNUSED_BRANCHES={'B2yM','B31d','B32H','B33y','B358','B39Z','B32k','B2zV'}

def refs(value,blocks):
 if not isinstance(value,list): return []
 return [x for x in value[1:] if isinstance(x,str) and x in blocks]

def reachable(root,blocks):
 q=deque([root]); out=set()
 while q:
  x=q.popleft()
  if not x or x in out or x not in blocks: continue
  out.add(x); q.append(blocks[x].get('next'))
  for v in blocks[x].get('inputs',{}).values(): q.extend(refs(v,blocks))
 return out

def syscall_literal(cid,blocks):
 c=blocks[cid]; rr=refs(c.get('inputs',{}).get('CONDITION'),blocks)
 if len(rr)!=1:return None
 e=blocks[rr[0]]
 if e.get('opcode')!='operator_equals': return None
 arg_names=[]; number=None
 for v in e.get('inputs',{}).values():
  for r in refs(v,blocks):
   a=blocks[r]
   if a.get('opcode')=='argument_reporter_string_number': arg_names.append(a.get('fields',{}).get('VALUE',[None])[0])
  for x in v[1:] if isinstance(v,list) else []:
   if isinstance(x,list) and len(x)>1:
    try: number=int(float(x[1]))
    except (TypeError,ValueError): pass
 if number is None or not any(x in ('id','syscall id') for x in arg_names): return None
 return number

def detach(block_id,blocks):
 block=blocks[block_id]; parent_id=block.get('parent'); nxt=block.get('next')
 if parent_id not in blocks: raise RuntimeError(('missing parent',block_id,parent_id))
 parent=blocks[parent_id]; attached=False
 if parent.get('next')==block_id:
  parent['next']=nxt; attached=True
 else:
  for name,value in list(parent.get('inputs',{}).items()):
   if not isinstance(value,list): continue
   new=list(value); changed=False
   for i in range(1,len(new)):
    if new[i]==block_id: new[i]=nxt; changed=True
   if changed:
    if nxt is None:
     del parent['inputs'][name]
    else: parent['inputs'][name]=new
    attached=True
 if not attached: raise RuntimeError(('not attached',block_id,parent_id))
 if nxt in blocks: blocks[nxt]['parent']=parent_id

src=Path(sys.argv[1]); dst=Path(sys.argv[2])
with zipfile.ZipFile(src) as z:
 infos=z.infolist(); payloads={i.filename:z.read(i.filename) for i in infos}; project=json.loads(payloads['project.json'])
removed=[]
for target in project['targets']:
 blocks=target.get('blocks',{}); definitions={}
 for bid,b in blocks.items():
  if b.get('opcode')=='procedures_definition':
   pid=b.get('inputs',{}).get('custom_block',[0,None])[1]
   code=blocks.get(pid,{}).get('mutation',{}).get('proccode')
   if code: definitions[code]=bid
 for code in PROC_CODES:
  did=definitions.get(code)
  if not did: continue
  candidates=[]
  for bid in reachable(did,blocks):
   if blocks[bid].get('opcode') in ('control_if','control_if_else'):
    number=syscall_literal(bid,blocks)
    if number is not None and number not in USED: candidates.append((bid,number))
  # deepest first is immaterial for disjoint chain nodes; detach in current linkage order
  for bid,number in candidates:
   detach(bid,blocks); removed.append((code,number,bid))
 if target.get('name')=='VM':
  for bid in EXTRA_UNUSED_BRANCHES:
   if bid in blocks:
    detach(bid,blocks); removed.append(('unused render command',-1,bid))
  # The three bundled apps contain no compiler or playback syscalls. Skip the
  # corresponding initialization lane while retaining service volume state.
  if all(x in blocks for x in ('B2c7','B5CD','B4YQ')):
   blocks['B2c7']['next']='B4YQ'; blocks['B4YQ']['parent']='B2c7'
   removed.append(('unused compiler/audio initialization',-1,'B5CD..B57R'))
  if 'B4ro' in blocks:
   detach('B4ro',blocks); removed.append(('unused typed fallback',-1,'B4ro'))
encoded=json.dumps(project,ensure_ascii=True,separators=(',',':')).encode()
payloads['project.json']=encoded
with zipfile.ZipFile(dst,'w') as out:
 for i in infos: out.writestr(i,payloads[i.filename])
print('removed branches',len(removed),'json',len(encoded))
for row in removed: print(*row)
