/* Offline QA form checks. Synthetic data only; no browser or production API. */
'use strict';
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const {webcrypto}=require('node:crypto');

const html=fs.readFileSync(path.join(__dirname,'annotation.html'),'utf8');
const sources=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.equal(sources.length,1);
new vm.Script(sources[0][1],{filename:'annotation.html.inline.js'});
const context=vm.createContext({crypto:webcrypto,TextEncoder,Uint8Array});
vm.runInContext(sources[0][1],context);
const run=source=>vm.runInContext(source,context);

class Element {
  constructor(tag,owner){this.tagName=tag;this.owner=owner;this.children=[];this.listeners={};this.dataset={};this.textContent='';this._value='';this.selectedIndex=0;this.files=[];this.disabled=false;this.checked=false;this.hidden=false;this.classList={add(){},toggle(){}};}
  get value(){return this.tagName==='select'?this.children[this.selectedIndex]?.value||'':this._value;}
  set value(v){if(this.tagName==='select')this.selectedIndex=this.children.findIndex(x=>x.value===v);else this._value=v;}
  get options(){return this.children;}
  append(...items){this.children.push(...items);if(this.tagName==='select' && this.selectedIndex<0)this.selectedIndex=0;}
  replaceChildren(...items){this.children=items;this.selectedIndex=0;}
  addEventListener(type,callback){this.listeners[type]=callback;}
  setAttribute(){}
  focus(){return this.listeners.focus?.({});}
  click(){if(this.tagName==='a')this.owner.downloads++;return this.listeners.click?.({});}
  remove(){}
  async trigger(type){return this.listeners[type]?.({});}
}
class Option extends Element{constructor(label,value){super('option');this.textContent=label;this.value=value;}}
function fakeDocument(){
  const doc={created:[],downloads:0};
  doc.createElement=tag=>{const item=new Element(tag,doc);doc.created.push(item);return item;};
  doc.createTextNode=text=>({textContent:text});
  doc.getElementById=id=>{let e=doc.created.find(x=>x.id===id);if(e)return e;const selects=['annotator','filter','conversation'];e=doc.createElement(selects.includes(id)?'select':id==='dataset'||id==='manifest'||id==='restore'||id==='checklistHash'?'input':'div');e.id=id;return e;};
  doc.body=doc.createElement('body');
  doc.getElementById('annotator').append(new Option('A','annotator-A'),new Option('B','annotator-B'));
  doc.getElementById('filter').append(new Option('pilot','pilot'),new Option('all','all'));
  return doc;
}
function allText(e){return (e.textContent||'')+e.children?.map(allText).join('');}
async function load(doc,id,value){const input=doc.getElementById(id);const raw=JSON.stringify(value);input.files=[{size:raw.length,text:async()=>raw}];await input.trigger('change');}

(async()=>{
  const sample=[{timestamp:'00:02',speaker:'operator',text:'Синтетикалық мысал / русский / English 😀'}];
  context.sample=sample;
  assert.equal(run('canonicalJSON({z:1,a:{z:"қ",a:[2,1]}})'),'{"a":{"a":[2,1],"z":"қ"},"z":1}');
  // Independently computed with Python hashlib and json.dumps(sort_keys=True,
  // ensure_ascii=False, separators=(',', ':')).
  assert.equal(await run('transcriptHash(sample)'), '9985fa52e5efb4620d4727ec805ff4149ea7586106b5f14351c61a233a077e16');
  const original=await run('transcriptHash(sample)');
  assert.equal(await run('transcriptHash([{text:sample[0].text,speaker:"operator",timestamp:"00:02"}])'),original);
  for(const key of ['timestamp','speaker','text']){
    context.changed=sample.map(x=>({...x,[key]:x[key]+' changed'}));
    assert.notEqual(await run('transcriptHash(changed)'),original);
  }
  assert.notEqual(await run('transcriptHash([...sample,{...sample[0],text:"second"}])'),await run('transcriptHash([{...sample[0],text:"second"},...sample])'));
  assert.notEqual(await run('transcriptHash([{...sample[0],extra:"original field"}])'),original);
  assert.equal(run('computeScore({1:"yes",2:"na",3:"na",4:15,5:"yes",6:7,7:"yes",8:"no"})'),85.38);
  assert.equal(run('computeScore({1:"yes",2:"na",3:"na",4:15,5:"yes",6:7,7:"yes",8:"yes"})'),85.38);
  assert.equal(run('computeScore({1:"na",2:"na",3:"na",4:15,5:"yes",6:7,7:"yes",8:"no"})'),null);
  assert.equal(run('computeScore({1:"yes",2:"yes",3:"yes",4:7,5:"yes",6:7,7:"yes",8:"no"})'),null);
  assert.equal(run('computeScore({1:"yes",2:"yes",3:"yes",4:20,5:"yes",6:true,7:"yes",8:"no"})'),null);
  assert.equal(run('computeScore({1:"yes",2:"yes",3:"yes",4:20,5:"yes",6:10,7:"yes",8:null})'),null);
  assert.equal(run('messageId("case-1",0)'),'case-1:m0001');
  assert.equal(run('answerValue("")'),null);
  assert.equal(run('answerValue("0")'),0);
  run('var rec={conversation_id:"synth",transcript:[{}]};var row={transcript_hash:"hash"};var entry={source:"human_individual",annotator_id:"annotator-A",checklist_hash:"rubric",transcript_hash:"hash",status:"complete",criteria:CRITERIA.map(c=>({criterion_id:c.id,answer:c.options[0],evidence:[{message_id:"synth:m0001"}],comment:"reason",disputed:false,uncertainty_reason:""}))}');
  assert.equal(run('validateAnnotation(entry,rec,row,"annotator-A","rubric",true).length'),0);
  assert.ok(run('validateAnnotation({...entry,source:"model_proposal"},rec,row,"annotator-A","rubric",true).length')>0);
  assert.ok(run('validateAnnotation(entry,rec,row,"annotator-B","rubric",true).length')>0);
  run('entry.criteria[0].evidence=[{message_id:"synth:m9999"}]');
  assert.ok(run('validateAnnotation(entry,rec,row,"annotator-A","rubric",true).length')>0);

  const doc=fakeDocument(),blobs=[];
  let badDigest=false,digestCalls=0;
  const browser=vm.createContext({document:doc,Option,crypto:{subtle:{digest:async(...args)=>{digestCalls++;return badDigest?new Uint8Array(32).buffer:webcrypto.subtle.digest(...args);}}},TextEncoder,Uint8Array,Blob,URL:{createObjectURL:blob=>{blobs.push(blob);return'blob:qa-test';},revokeObjectURL(){}},setTimeout:callback=>callback(),window:{confirm:()=>true,addEventListener(){}}});
  vm.runInContext(sources[0][1],browser);
  const dataset={isSuccess:true,data:{records:[{conversation_id:'synth',language:'KZ',channel:'agent chat',transcript:sample,audio_url:'synthetic-secret-not-for-export'}]}};
  const manifest={checklist_hash:'rubric',records:[{conversation_id:'synth',split:'tuning',pilot:true,scenario_group_id:'synthetic',transcript_hash:original}]};
  await load(doc,'dataset',dataset);
  await load(doc,'manifest',manifest);
  assert.equal(doc.getElementById('conversation').value,'synth');
  assert.ok(allText(doc.getElementById('transcript')).includes(sample[0].text));
  await load(doc,'manifest',{...manifest,records:[{...manifest.records[0],transcript_hash:'0'.repeat(64)}]});
  assert.equal(doc.getElementById('conversation').value,'');
  assert.ok(!allText(doc.getElementById('transcript')).includes(sample[0].text));
  assert.ok(doc.getElementById('status').textContent.includes('НЕ совпал'));
  await load(doc,'manifest',manifest);
  doc.getElementById('answer-1').value='yes';await doc.getElementById('answer-1').trigger('input');
  const beforeExport=digestCalls;
  await doc.getElementById('export').trigger('click');
  assert.ok(digestCalls>beforeExport,'Export must recalculate the transcript hash');
  assert.equal(doc.downloads,1);
  const exported=JSON.parse(await blobs[0].text());
  assert.equal(exported.source,'human_individual');
  assert.equal(exported.annotations[0].status,'draft');
  assert.equal(exported.annotations[0].answers['1'],'yes');
  assert.ok(!JSON.stringify(exported).includes(sample[0].text));
  assert.ok(!JSON.stringify(exported).includes('audio_url'));
  assert.ok(!JSON.stringify(exported).includes('synthetic-secret-not-for-export'));
  badDigest=true;
  await doc.getElementById('export').trigger('click');
  assert.equal(doc.downloads,1,'Changed hash must block a second download');
  assert.ok(doc.getElementById('status').textContent.includes('Экспорт заблокирован'));
  assert.ok(!allText(doc.getElementById('transcript')).includes(sample[0].text));
  assert.ok(!html.includes('innerHTML'));
  assert.ok(!/fetch\(|XMLHttpRequest|https?:\/\//.test(html));
  assert.ok(html.includes("connect-src 'none'"));
  console.log('PASS: offline annotation syntax, canonical SHA-256, scoring, provenance, evidence, stale-manifest and export tests.');
})().catch(error=>{console.error(error);process.exitCode=1;});
