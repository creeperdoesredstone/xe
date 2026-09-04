import hashlib,json,sys,zipfile
from collections import Counter,deque

original,candidate=sys.argv[1:3]
with zipfile.ZipFile(original) as zo, zipfile.ZipFile(candidate) as zc:
 po=json.loads(zo.read('project.json')); pc=json.loads(zc.read('project.json'))
 assert set(zo.namelist())==set(zc.namelist())
 for name in zo.namelist():
  if name!='project.json': assert hashlib.sha256(zo.read(name)).digest()==hashlib.sha256(zc.read(name)).digest(),name
 raw=zc.read('project.json'); assert len(raw)<5*1024*1024,len(raw)

orig_stage=next(t for t in po['targets'] if t.get('isStage'))
cand_stage=next(t for t in pc['targets'] if t.get('isStage'))
dictionary=next(v[1] for v in cand_stage['lists'].values() if v[0]=='XO: program word dictionary')
for suffix in ('file explorer','settings','calculator'):
 old=next(v[1] for v in orig_stage['lists'].values() if v[0]=='XenonOS: program '+suffix)
 packed=next(v[1] for v in cand_stage['lists'].values() if v[0]=='XO: program '+suffix)
 assert [dictionary[index-1] for index in packed]==old,suffix

max_list=max((len(v[1]),t['name'],v[0]) for t in pc['targets'] for v in t.get('lists',{}).values())
assert max_list[0]<200000,max_list
definitions=set()
calls=[]
lazy=0
for target in pc['targets']:
 blocks=target.get('blocks',{})
 for block_id,block in blocks.items():
  for linked in (block.get('next'),block.get('parent')):
   assert linked is None or linked in blocks,(target['name'],block_id,linked)
  mutation=block.get('mutation')
  if mutation:
   assert mutation.get('tagName')=='mutation' and isinstance(mutation.get('children'),list),(target['name'],block_id)
  if block.get('opcode')=='procedures_definition':
   prototype=block.get('inputs',{}).get('custom_block',[0,None])[1]
   definitions.add((target['name'],blocks[prototype]['mutation']['proccode']))
  if block.get('opcode')=='procedures_call': calls.append((target['name'],block_id,block['mutation']['proccode']))
  if str(block_id).startswith('lazy_mem_'): lazy+=1
  if block.get('opcode')=='control_repeat' and block.get('inputs',{}).get('TIMES')==[1,[6,'200000']]:
   raise AssertionError(('eager preallocation remains',target['name'],block_id))
for target,block_id,code in calls:
 assert (target,code) in definitions,(target,block_id,code)
assert lazy>=30,lazy
print(json.dumps({'project_json':len(raw),'max_list':max_list,'lazy_blocks':lazy,'targets':len(pc['targets']),'calls':len(calls)}))
