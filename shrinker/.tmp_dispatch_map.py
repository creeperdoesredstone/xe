import json, zipfile, sys
from pathlib import Path

p=Path(sys.argv[1])
with zipfile.ZipFile(p) as z: project=json.loads(z.read('project.json'))

def lit(blocks, inp):
    if not isinstance(inp,list): return None
    for x in inp[1:]:
        if isinstance(x,list) and len(x)>1: return x[1]
        if isinstance(x,str) and x in blocks:
            b=blocks[x]
            if b.get('opcode')=='operator_equals':
                vals=[]
                for v in b.get('inputs',{}).values():
                    q=lit(blocks,v)
                    if q is not None: vals.append(q)
                return ('eq',vals)
            return ('block',b.get('opcode'),x)
    return None

for t in project['targets']:
  blocks=t.get('blocks',{})
  if t.get('name')!='VM': continue
  defs={}
  for bid,b in blocks.items():
    if b.get('opcode')=='procedures_definition':
      pid=b.get('inputs',{}).get('custom_block',[0,None])[1]
      code=blocks.get(pid,{}).get('mutation',{}).get('proccode')
      if code: defs[bid]=code
  for did,code in defs.items():
    if 'syscall' not in code.lower(): continue
    print('\nDEF',did,code)
    cur=blocks[did].get('next'); n=0
    while cur and n<200:
      b=blocks[cur]; op=b.get('opcode')
      extra=''
      if op in ('control_if','control_if_else'):
        extra=' cond='+repr(lit(blocks,b.get('inputs',{}).get('CONDITION')))
        sid=b.get('inputs',{}).get('SUBSTACK',[0,None])[1]
        if sid in blocks:
          sb=blocks[sid]; extra+=' sub='+sb.get('opcode','')
          if sb.get('opcode')=='procedures_call': extra+=' '+sb.get('mutation',{}).get('proccode','')
      elif op=='procedures_call': extra=' '+b.get('mutation',{}).get('proccode','')
      print(cur,op,extra)
      cur=b.get('next'); n+=1
