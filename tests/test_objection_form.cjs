// İtirazın insan seçimini, özel fiş ömrünü, moderasyon yarışını ve gerçek sayfalama akışını sınar.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const turn = () => new Promise(resolve => setImmediate(resolve));
const copy = value => JSON.parse(JSON.stringify(value));
const identifier = "a".repeat(32);
const record = (id="b".repeat(32), research=identifier) => ({id,research_id:research,version:4,revision:1,status:"pending",private_purged:false,claim:"Private claim",reason:"Private reason",sources:[]});

function environment(filename, options={}) {
  const calls=[],fields={claim:"Private claim",reason:"Private reason",source_title:"",source_url:""};
  const state={confirmed:true,confirmationCalls:[],confirmationOptions:[],holdConfirm:false,pendingConfirm:null,dropCreate:false,failRead:false,failWithdraw:false,record:record(),held:new Map(),rows:[record()],decisions:[]};
  class Element {
    constructor(tag="div") { this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.listeners={};this._text="";this.hidden=false;this.disabled=false;this.value="";this.dataset={}; }
    set textContent(value) {this._text=String(value);this.children=[];}
    get textContent(){return this._text+this.children.map(x=>x.textContent).join("");}
    append(...items){for(const item of items){item.parentNode=this;this.children.push(item);}}
    before(item){const at=this.parentNode.children.indexOf(this);item.parentNode=this.parentNode;this.parentNode.children.splice(at,0,item);}
    replaceChildren(...items){this._text="";this.children=[];this.append(...items);}
    addEventListener(name,fn){this.listeners[name]=fn;}
    focus(){this.focused=true;}
    reportValidity(){return true;}
    reset(){fields.claim=fields.reason=fields.source_title=fields.source_url="";}
    querySelector(tag){return flatten(this).find(x=>x.tagName===tag.toUpperCase());}
    dispatch(name){if(this.disabled)return Promise.resolve();const event={currentTarget:this,preventDefault(){}};const result=this.listeners[name]?.(event);event.currentTarget=null;return Promise.resolve(result);}
  }
  const flatten=node=>[node,...node.children.flatMap(flatten)];
  const root=new Element("main");root.dataset={researchId:identifier,version:"4"};
  const ids=filename==="objection-form.js"?["objection-form","objection-preview","objection-status","objection-preview-text","objection-send","objection-edit"]:["objection-list","objection-detail","objection-status"];
  for(const id of ids){const node=new Element(id==="objection-form"?"form":id.endsWith("send")||id.endsWith("edit")?"button":"div");node.id=id;root.append(node);}
  const byId=id=>flatten(root).find(x=>x.id===id);
  if(byId("objection-form"))byId("objection-form").elements={claim:new Element("textarea")};
  const document={documentElement:{lang:options.language||"en"},createElement:tag=>new Element(tag),querySelector:()=>root,getElementById:byId};
  const response=(body,ok=true)=>({ok,json:async()=>copy(body)});
  async function fetch(url,opt={}){
    calls.push({url,options:copy(opt),body:opt.body?JSON.parse(opt.body):undefined});
    if(url.endsWith("/session"))return response({user:{role:"moderator"},csrf_token:"TEST_CSRF"});
    if(url.includes("/review?")){
      const params=new URL(url,"https://community.example").searchParams;
      const rows=state.rows.filter(x=>!params.get("status")||x.status===params.get("status"));
      const offset=Number(params.get("offset")),limit=Number(params.get("limit"));
      return response({items:rows.slice(offset,offset+limit),total:rows.length,offset,limit});
    }
    if(url.endsWith("/decision")){state.decisions.push(calls.at(-1));return response({});}
    if(url.includes("/review/")){
      const id=url.split("/").pop();
      if(options.deferDetails)return new Promise(resolve=>state.held.set(id,()=>resolve(response(state.rows.find(x=>x.id===id)))));
      return response(state.rows.find(x=>x.id===id));
    }
    if(url.endsWith("/read"))return response(state.record,!state.failRead);
    if(url.endsWith("/withdraw")){
      if(state.failWithdraw)return response({},false);
      state.record={...state.record,status:"withdrawn",revision:state.record.revision+1,private_purged:true,claim:"",reason:"",sources:[]};
      return response(state.record);
    }
    if(url==="/api/community/objections"){
      if(state.dropCreate){state.dropCreate=false;throw new Error("response lost after server commit");}
      return response({objection:state.record,duplicate:false});
    }
    throw new Error("Unexpected synthetic request");
  }
  class FormData{get(name){return fields[name]||"";}}
  const source=fs.readFileSync(path.join(__dirname,"../radar/static",filename),"utf8");
  vm.runInNewContext(source,{document,fetch,FormData,URL,Uint8Array,encodeURIComponent,
    btoa:value=>Buffer.from(value,"binary").toString("base64"),crypto:{getRandomValues(bytes){bytes.fill(7);return bytes;}},
    confirm:()=>{throw new Error("window.confirm must not be used");},
    ...(options.confirmDialog===false?{}:{DevTrellisConfirm:{ask:options=>{state.confirmationCalls.push(options.message);state.confirmationOptions.push(options);
      return state.holdConfirm?new Promise(resolve=>{state.pendingConfirm=resolve;}):Promise.resolve(state.confirmed);}}})});
  return {byId,root,flatten,calls,state,fields,ready:async()=>{await turn();await turn();},button:(node,label)=>flatten(node).find(x=>x.tagName==="BUTTON"&&x.textContent===label)};
}

test("önizleme ağ kullanmaz; fiş yalnız başlıkta kalır ve açık gönderim asenkron başarıya ulaşır",async()=>{
  const app=environment("objection-form.js");
  assert.match(app.byId("objection-receipt-note").textContent,/Reloading or closing/);
  await app.byId("objection-form").dispatch("submit");
  assert.equal(app.calls.length,0);assert.equal(app.byId("objection-form").hidden,true);
  assert.match(app.byId("objection-preview-text").textContent,/Private claim/);
  await app.byId("objection-send").dispatch("click");
  assert.deepEqual(app.calls.map(x=>x.url),["/api/community/session","/api/community/objections"]);
  const sent=app.calls[1],receipt=sent.options.headers["X-DevTrellis-Objection-Receipt"];
  assert.match(receipt,/^[A-Za-z0-9_-]{43}$/);assert.equal(sent.options.headers["X-Ufuk-CSRF"],"TEST_CSRF");
  assert.equal(JSON.stringify(sent.body).includes(receipt),false);assert.equal(app.root.textContent.includes(receipt),false);
  assert.equal(app.calls.some(x=>x.url.includes(receipt)),false);
  assert.equal(app.byId("objection-owner-controls").hidden,false);assert.equal(app.byId("objection-send").hidden,true);
  assert.match(app.byId("objection-status").textContent,/Objection status: Pending/);
});

test("durum ve geri çekme aynı özel fişle çalışır; iptal yazmaz, açık onay tam revizyonu gönderir",async()=>{
  const app=environment("objection-form.js");
  await app.byId("objection-form").dispatch("submit");await app.byId("objection-send").dispatch("click");
  const receipt=app.calls[1].options.headers["X-DevTrellis-Objection-Receipt"];
  app.state.record.revision=3;app.state.record.status="accepted";
  await app.byId("objection-read").dispatch("click");assert.match(app.byId("objection-status").textContent,/Accepted/);
  app.state.confirmed=false;const before=app.calls.length;await app.byId("objection-withdraw").dispatch("click");assert.equal(app.calls.length,before);
  app.state.confirmed=true;await app.byId("objection-withdraw").dispatch("click");
  const withdrawal=app.calls.find(x=>x.url.endsWith("/withdraw"));assert.deepEqual(withdrawal.body,{expected_revision:3});
  assert.equal(withdrawal.options.headers["X-DevTrellis-Objection-Receipt"],receipt);
  assert.match(app.state.confirmationCalls.at(-1),/published correction trace remains/);
  assert.match(app.byId("objection-status").textContent,/Withdrawn/);assert.equal(app.byId("objection-withdraw").hidden,true);
  assert.equal(app.byId("objection-preview-text").textContent,"");assert.equal(app.fields.claim,"");
});

test("kaybolan gönderim yanıtı durum sorgusuyla bulunur; tekrar yaratma veya otomatik geri çekme yok",async()=>{
  const app=environment("objection-form.js");app.state.dropCreate=true;
  await app.byId("objection-form").dispatch("submit");await app.byId("objection-send").dispatch("click");
  assert.match(app.byId("objection-status").textContent,/Delivery was not confirmed/);
  app.state.record.status="accepted";await app.byId("objection-read").dispatch("click");
  assert.match(app.byId("objection-status").textContent,/Accepted/);
  assert.equal(app.calls.filter(x=>x.url==="/api/community/objections").length,1);
  assert.equal(app.calls.filter(x=>x.url.endsWith("/withdraw")).length,0);
});

test("geri çekme çakışması sessiz yeniden deneme yapmaz; güncel revizyon tekrar insan seçimi ister",async()=>{
  const app=environment("objection-form.js",{language:"tr"});
  await app.byId("objection-form").dispatch("submit");await app.byId("objection-send").dispatch("click");
  app.state.failWithdraw=true;await app.byId("objection-withdraw").dispatch("click");
  assert.match(app.byId("objection-status").textContent,/Geri çekme doğrulanamadı/);
  assert.equal(app.calls.filter(x=>x.url.endsWith("/withdraw")).length,1);
  app.state.record.revision=8;await app.byId("objection-read").dispatch("click");
  assert.equal(app.calls.filter(x=>x.url.endsWith("/withdraw")).length,1);
  assert.equal(app.state.confirmationCalls.length,1);
});

test("eşzamanlı ayrıntı yanıtları yalnız son seçimi gösterir ve kararı o sabit kayda bağlar",async()=>{
  const app=environment("objection-review.js",{deferDetails:true});
  const A=record("a".repeat(32),"c".repeat(32)),B=record("b".repeat(32),"d".repeat(32));
  app.state.rows=[A,B];await app.ready();
  const first=app.byId("objection-list").children[0].dispatch("click"),second=app.byId("objection-list").children[1].dispatch("click");
  app.state.held.get(B.id)();await second;app.state.held.get(A.id)();await first;
  const detail=app.byId("objection-detail");assert.equal(app.flatten(detail).filter(x=>x.tagName==="FORM").length,1);
  assert.ok(detail.textContent.includes(B.research_id));assert.equal(detail.textContent.includes(A.research_id),false);
  await app.button(detail,"Reject").dispatch("click");
  assert.equal(app.state.decisions.length,1);assert.ok(app.state.decisions[0].url.includes(B.id));
  assert.equal(app.state.decisions[0].body.research_id,B.research_id);assert.equal(app.state.decisions[0].body.expected_revision,1);
});

test("önceki ayrıntı düğmesi başka kayıt açılırken etkisizleşir ve iptal kararı göndermez",async()=>{
  const app=environment("objection-review.js",{deferDetails:true});
  const A=record("a".repeat(32),"c".repeat(32)),B=record("b".repeat(32),"d".repeat(32));
  app.state.rows=[A,B];await app.ready();
  const first=app.byId("objection-list").children[0].dispatch("click");app.state.held.get(A.id)();await first;
  const stale=app.button(app.byId("objection-detail"),"Reject");
  const second=app.byId("objection-list").children[1].dispatch("click");await stale.dispatch("click");
  assert.equal(app.state.decisions.length,0);assert.equal(app.state.confirmationCalls.length,0);
  app.state.held.get(B.id)();await second;app.state.confirmed=false;
  await app.button(app.byId("objection-detail"),"Reject").dispatch("click");assert.equal(app.state.decisions.length,0);
});

test("moderatör bekleyenlerden başlar, 25 kayıtla sayfalar ve durum değişince ilk sayfaya döner",async()=>{
  const app=environment("objection-review.js");
  app.state.rows=Array.from({length:60},(_,i)=>record(i.toString(16).padStart(32,"0")));
  app.state.rows.push({...record("f".repeat(32)),status:"accepted"});await app.ready();
  assert.equal(app.byId("objection-list").children.length,25);assert.match(app.byId("objection-page").textContent,/1 \/ 3/);
  await app.byId("objection-next").dispatch("click");await app.ready();
  assert.ok(app.calls.some(x=>x.url.includes("offset=25&status=pending")));
  const filter=app.byId("objection-filter");filter.value="accepted";await filter.dispatch("change");await app.ready();
  assert.equal(app.byId("objection-list").children.length,1);assert.match(app.byId("objection-page").textContent,/1 \/ 1/);
  assert.ok(app.calls.at(-1).url.includes("offset=0&status=accepted"));assert.equal(app.byId("objection-next").disabled,true);
});

test("itiraz geri çekme onayı sayfa içi pencereden gelir; yüklenmezse görünür ileti çıkar ve istek gitmez",async()=>{
  const app=environment("objection-form.js");
  await app.byId("objection-form").dispatch("submit");await app.byId("objection-send").dispatch("click");
  app.state.confirmed=false;await app.byId("objection-withdraw").dispatch("click");
  const options=app.state.confirmationOptions.at(-1);
  assert.equal(options.confirmLabel,"Withdraw private objection");assert.equal(options.tone,"danger");
  assert.equal(options.returnFocus,app.byId("objection-withdraw"));
  assert.equal(app.calls.filter(x=>x.url.endsWith("/withdraw")).length,0);
  for(const language of ["en","tr"]){
    const missing=environment("objection-form.js",{confirmDialog:false,language});
    await missing.byId("objection-form").dispatch("submit");await missing.byId("objection-send").dispatch("click");
    await missing.byId("objection-withdraw").dispatch("click");
    assert.equal(missing.calls.filter(x=>x.url.endsWith("/withdraw")).length,0);
    assert.equal(missing.byId("objection-status").textContent,language==="en"?"The confirmation dialog could not open; nothing was done. Reload the page and try again.":"Onay penceresi açılamadı; işlem yapılmadı. Sayfayı yenileyip yeniden deneyin.");
  }
});

test("itiraz geri çekme onayı beklenirken durum değişirse eski onay geri çekme göndermez",async()=>{
  const app=environment("objection-form.js");
  await app.byId("objection-form").dispatch("submit");await app.byId("objection-send").dispatch("click");
  app.state.holdConfirm=true;const pending=app.byId("objection-withdraw").dispatch("click");
  app.state.record={...app.state.record,revision:5,status:"in_review"};app.state.holdConfirm=false;
  await app.byId("objection-read").dispatch("click");assert.match(app.byId("objection-status").textContent,/In review/);
  app.state.pendingConfirm(true);await pending;
  assert.equal(app.calls.filter(x=>x.url.endsWith("/withdraw")).length,0);
});

test("moderasyon onayı beklenirken başka kayıt açılırsa eski onay karar göndermez; onay metni korunur",async()=>{
  const app=environment("objection-review.js");
  const A=record("a".repeat(32),"c".repeat(32)),B=record("b".repeat(32),"d".repeat(32));
  app.state.rows=[A,B];await app.ready();
  await app.byId("objection-list").children[0].dispatch("click");
  app.state.holdConfirm=true;const pending=app.button(app.byId("objection-detail"),"Reject").dispatch("click");
  const options=app.state.confirmationOptions.at(-1);
  assert.equal(options.message,`Save this moderation decision?\n${A.research_id} · v4 · Reject`);
  assert.equal(options.confirmLabel,"Reject");assert.equal(options.tone,"danger");
  await app.byId("objection-list").children[1].dispatch("click");
  app.state.pendingConfirm(true);await pending;
  assert.equal(app.state.decisions.length,0);
  assert.ok(app.byId("objection-detail").textContent.includes(B.research_id));
});

test("moderasyon onay bileşeni yüklenmezse karar kaydedilmez ve görünür ileti çıkar",async()=>{
  for(const language of ["en","tr"]){
    const app=environment("objection-review.js",{confirmDialog:false,language});await app.ready();
    await app.byId("objection-list").children[0].dispatch("click");
    await app.button(app.byId("objection-detail"),language==="en"?"Reject":"Reddet").dispatch("click");
    assert.equal(app.state.decisions.length,0);
    assert.equal(app.byId("objection-status").textContent,language==="en"?"The confirmation dialog could not open; no decision was saved. Reload the page and try again.":"Onay penceresi açılamadı; karar kaydedilmedi. Sayfayı yenileyip yeniden deneyin.");
  }
});
