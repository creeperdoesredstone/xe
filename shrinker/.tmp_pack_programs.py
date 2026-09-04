import json,zipfile,sys
from collections import Counter
from pathlib import Path

NAMES=('XenonOS: program file explorer','XenonOS: program settings','XenonOS: program calculator')

src=Path(sys.argv[1]); dst=Path(sys.argv[2])
with zipfile.ZipFile(src) as z:
 infos=z.infolist(); payloads={i.filename:z.read(i.filename) for i in infos}; project=json.loads(payloads['project.json'])
stage=next(t for t in project['targets'] if t.get('isStage'))
programs={v[0]:(k,v) for k,v in stage.get('lists',{}).items() if v[0] in NAMES}
if set(programs)!=set(NAMES): raise RuntimeError('missing embedded program list')
frequency=Counter(word for name in NAMES for word in programs[name][1][1])
words=[word for word,_ in sorted(frequency.items(),key=lambda pair:(-pair[1],pair[0]))]
word_index={word:index+1 for index,word in enumerate(words)}
for name in NAMES:
 value=programs[name][1]
 value[1]=[word_index[word] for word in value[1]]
dictionary_id='XenonOS_program_word_dictionary'
while dictionary_id in stage['lists']: dictionary_id+='_'
stage['lists'][dictionary_id]=['XenonOS: program word dictionary',words]
source_ids={programs[name][0] for name in NAMES}
wrapped=0
for target in project['targets']:
 blocks=target.get('blocks',{})
 for block_id in list(blocks):
  block=blocks[block_id]
  field=block.get('fields',{}).get('LIST')
  if block.get('opcode')!='data_itemoflist' or not isinstance(field,list) or len(field)<2 or field[1] not in source_ids: continue
  inner_id='XenonOS_program_word_index_'+str(wrapped)
  while inner_id in blocks: inner_id+='_'
  inner=dict(block); inner['parent']=block_id; inner['next']=None; inner['topLevel']=False
  blocks[inner_id]=inner
  for value in inner.get('inputs',{}).values():
   if isinstance(value,list):
    for item in value[1:]:
     if isinstance(item,str) and item in blocks and blocks[item].get('parent')==block_id:
      blocks[item]['parent']=inner_id
  blocks[block_id]={
   'opcode':'data_itemoflist','next':block.get('next'),'parent':block.get('parent'),
   'inputs':{'INDEX':[3,inner_id,[7,'1']]},
   'fields':{'LIST':['XenonOS: program word dictionary',dictionary_id]},
   'shadow':block.get('shadow',False),'topLevel':block.get('topLevel',False)
  }
  wrapped+=1
if wrapped!=3: raise RuntimeError(f'expected 3 reads, found {wrapped}')
encoded=json.dumps(project,ensure_ascii=True,separators=(',',':')).encode()
payloads['project.json']=encoded
with zipfile.ZipFile(dst,'w') as out:
 for i in infos: out.writestr(i,payloads[i.filename])
print('dictionary',len(words),'wrapped',wrapped,'json',len(encoded))
