"""Check read-prefetch hint semantics through `decode` and `encode`.

No hint execution or SMT model. Literal bytes also pin the displacement
field occupied by an external PC32 relocation in the RIP-relative form.
"""
from pathlib import Path
import copy,json,sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import x86, encode
names=['prefetchnta','prefetcht0','prefetcht1','prefetcht2'];assert list(x86.PREFETCH_HINTS)==names
# Literal opcode/ModRM/SIB spellings independent of the encoder table.
forms=[('',0x00,''),('',0x40,'7f'),('',0x80,'00020000'),('',0x84,'8800020000'),('41',0x40,'07'),('43',0x44,'8807'),('',0x05,'00000000')]
positives=semantic_changes=0
for hint,name in enumerate(names):
 for prefix,modrm,tail in forms:
  raw=bytes.fromhex(prefix+'0f18'+f'{modrm|(hint<<3):02x}'+tail);ins=x86.decode(raw)
  assert len(ins)==1 and ins[0]['mnemonic']==name
  assert ins[0]['ops'][0]['kind']=='Memory'
  encoded,fields=encode.encode(ins[0]);assert encoded==raw,(raw.hex(),encoded.hex())
  if modrm==5:
   assert fields['disp']==(3,4)
   # Actual assembler-object relocation roundtrips are checked separately.
  positives+=1
  for other,newname in enumerate(names):
   changed=copy.deepcopy(ins[0]);changed['mnemonic']=newname
   data,_=encode.encode(changed);decoded=x86.decode(data)[0]
   assert decoded['mnemonic']==newname and decoded['ops'][0]['mem']==ins[0]['ops'][0]['mem']
   # Retained original encoding provenance must not override changed semantics.
   at=len(bytes.fromhex(prefix))+2;assert (data[at]>>3)&7==other
   semantic_changes+=1
negative=0
for hint in range(4,8):
 for modrm in [0x00,0x40,0x80,0x05]:
  suffix={0x00:'',0x40:'01',0x80:'00020000',0x05:'00000000'}[modrm]
  try:x86.decode(bytes.fromhex('0f18'+f'{modrm|(hint<<3):02x}'+suffix))
  except x86.DecodeError as e:assert 'unsupported prefetch hint'in str(e)
  else:raise AssertionError('Unsupported extension accepted')
  negative+=1
for hint in range(4):
 try:x86.decode(bytes.fromhex('0f18'+f'{0xc0|(hint<<3):02x}'))
 except x86.DecodeError as e:assert 'memory-only'in str(e)
 else:raise AssertionError('Register operand accepted')
 negative+=1
 sample=x86.decode(bytes.fromhex('0f18'+f'{hint<<3:02x}'))[0]
 sample['ops']=[{'kind':'Register','reg':'rax','size':64}]
 try:encode.encode(sample)
 except encode.NoMatch:pass
 else:raise AssertionError('Encoder accepted register operand')
 negative+=1
for prefix in ['f0','f2','f3']:
 try:x86.decode(bytes.fromhex(prefix+'0f1808'))
 except x86.DecodeError as e:assert 'unsupported prefetch prefix'in str(e)
 else:raise AssertionError('Unsupported prefix accepted')
 negative+=1
for generic in ['prefetch','prefetchit0']:
 sample=x86.decode(bytes.fromhex('0f1808'))[0];sample['mnemonic']=generic
 try:encode.encode(sample)
 except encode.NoMatch:pass
 else:raise AssertionError('Unsupported mnemonic accepted')
 negative+=1
print(json.dumps({'status':'PASS_PURE_CODEC_HINT_SELECTORS_AND_ADDRESS_FORMS','literal_positive_forms':positives,'semantic_mnemonic_changes':semantic_changes,'negative_refusals':negative,'limits':'No CPU execution, architectural hint effect proof, speculative-prefetch bounds proof or SMT model. Selectors4..7 are unsupported by this decoder, not claimed universally reserved.'}))
