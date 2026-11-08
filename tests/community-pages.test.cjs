// Kamu sayfasında yerel önizleme, açık gönderim, güvenli tekrar ve iki dilli taslağı sınar.
const assert = require("node:assert/strict");
const {test} = require("node:test");
const {PAGES, COPY, initialLanguage, requestPayload, createUuid, validReceipt, createApp} = require("../radar/static/community-pages.js");
const turn = () => new Promise(resolve => setImmediate(resolve));
const received = {id: "a".repeat(32), status: "new", created_at: "2026-11-05T15:30:00.123456+00:00", duplicate: false};
const response = (status, value) => ({status, ok: status >= 200 && status < 300, json: async () => value});

function harness({page="support", language=null, storageBroken=false, secure=true}={}) {
  const state = {calls: [], saved: [], focused: null, keys: 0, session: response(200, {csrf_token: "CSRF_FOR_TEST"}), post: response(201, {...received})};
  class Node {
    constructor(tag) {this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attributes={};this._text="";this.disabled=false;}
    set textContent(value) {this._text=String(value);this.children=[];}
    get textContent() {return this._text + this.children.map(child=>child.textContent).join("");}
    set innerHTML(value) {throw new Error("Untrusted HTML insertion: " + value);}
    append(...children) {this.children.push(...children);}
    replaceChildren(...children) {this.children=children;this._text="";}
    setAttribute(name,value) {this.attributes[name]=String(value);}
    getAttribute(name) {return this.attributes[name];}
    addEventListener(name,fn) {(this.listeners[name] ||= []).push(fn);}
    focus() {state.focused=this.id;}
    async dispatch(name) {
      if(this.disabled)return;
      await Promise.all((this.listeners[name]||[]).map(fn=>fn({target:this,preventDefault(){}})));
      await turn();
    }
  }
  const root=new Node("div");root.id="community-page-root";
  const flatten=node=>[node,...node.children.flatMap(flatten)];
  const document={documentElement:{lang:""},title:"",createElement:tag=>new Node(tag),getElementById:id=>flatten(root).find(node=>node.id===id)};
  const storage={getItem(key){assert.ok(["devtrellis.ui.language","ufuk.ui.language"].includes(key));if(storageBroken)throw new Error("blocked");return language;},setItem(key,value){assert.equal(key,"devtrellis.ui.language");state.saved.push({key,value});if(storageBroken)throw new Error("blocked");}};
  const crypto=secure?{randomUUID(){state.keys++;return "00000000-0000-4000-8000-"+String(state.keys).padStart(12,"0");}}:{};
  const fetcher=async(url,options={})=>{
    state.calls.push({url,options,payload:options.body?JSON.parse(options.body):undefined});
    const result=url==="/api/community/session"?state.session:state.post;
    if(typeof result==="function")return result();
    return result;
  };
  const app=createApp({document,window:{location:{pathname:"/"+page},navigator:{language:"tr"}},fetcher,crypto,storage});
  const byId=document.getElementById;
  async function input(id,value,event="input") {const field=byId(id);assert.ok(field,id);if(typeof value==="boolean")field.checked=value;else field.value=value;await field.dispatch(event);}
  async function fill(overrides={}) {
    const values={subject:"A useful topic",message:"How can I help?",name:"",email:"",...overrides};
    for(const [name,value] of Object.entries(values))await input("request-"+name,value,["category","consent"].includes(name)?"change":"input");
    if(!Object.hasOwn(overrides,"consent"))await input("request-consent",true,"change");
  }
  return {app,state,root,flatten,document,byId,input,fill,preview:()=>byId("community-request-form").dispatch("submit"),send:()=>byId("request-send").dispatch("click"),posts:()=>state.calls.filter(call=>call.url==="/api/community/requests"&&call.options.method==="POST")};
}

test("four routes have translated titles, one heading and one current navigation link without fetching",()=>{
  for(const language of ["en","tr"])for(const page of PAGES){
    const h=harness({page,language});const nodes=h.flatten(h.root);
    assert.equal(h.document.documentElement.lang,language);
    assert.equal(h.document.title,COPY[language][page]+" — DevTrellis");
    assert.deepEqual(nodes.filter(n=>n.tagName==="H1").map(n=>n.textContent),[COPY[language][page+"Title"]]);
    const active=nodes.filter(n=>n.getAttribute("aria-current")==="page");
    assert.equal(active.length,1);assert.equal(active[0].href,"/"+page);
    for(const route of PAGES)assert.ok(nodes.some(n=>n.tagName==="A"&&n.href==="/"+route));
    assert.equal(h.state.calls.length,0);
  }
  assert.equal(harness().document.documentElement.lang,"en");
  assert.equal(harness({storageBroken:true}).document.documentElement.lang,"en");
  assert.equal(initialLanguage({getItem:()=>"fr"}),"en");
});

test("language changes retain every draft field and consent; only the language is persisted",async()=>{
  const h=harness({page:"contribute"});
  await h.fill({category:"translation",subject:" Draft subject ",message:"Line one\nLine two",name:"Ada",email:"ada@example.org"});
  await h.input("pages-language","tr","change");
  for(const [key,value] of Object.entries({category:"translation",subject:" Draft subject ",message:"Line one\nLine two",name:"Ada",email:"ada@example.org"}))assert.equal(h.byId("request-"+key).value,value);
  assert.equal(h.byId("request-consent").checked,true);
  assert.equal(h.document.documentElement.lang,"tr");assert.equal(h.state.focused,"pages-language");
  assert.deepEqual(h.state.saved,[{key:"devtrellis.ui.language",value:"tr"}]);assert.equal(h.state.calls.length,0);
});

test("preview is local and displays exact normalized fields as inert text",async()=>{
  const h=harness();
  const dangerous='<img src=x onerror="alert(1)">\n<script>private draft</script>';
  await h.fill({subject:"  What changed?  ",message:"  "+dangerous+"  ",name:"<b>Ada</b>",email:"ada@example.org"});await h.preview();
  assert.equal(h.state.calls.length,0);assert.equal(h.state.keys,1);assert.equal(h.state.focused,"request-preview-title");
  const values=h.flatten(h.root).filter(n=>n.tagName==="DD").map(n=>n.textContent);
  assert.ok(values.includes("What changed?"));assert.ok(values.includes(dangerous));assert.ok(values.includes("<b>Ada</b>"));
  assert.equal(h.flatten(h.root).filter(n=>n.tagName==="IMG").length,1);assert.ok(!h.flatten(h.root).some(n=>["SCRIPT","B"].includes(n.tagName)));
  assert.equal(h.byId("request-send").disabled,false);assert.equal(h.byId("request-subject"),undefined);
});

test("only explicit send opens the CSRF session and posts exactly the reviewed payload",async()=>{
  const h=harness({page:"contribute",language:"tr"});
  await h.fill({category:"code",subject:"  Docs help ",message:" Improve the setup guide. ",name:"Ada",email:"ada@example.org"});await h.preview();
  await h.input("pages-language","en","change");assert.equal(h.state.calls.length,0);await h.send();
  assert.deepEqual(h.state.calls.map(call=>call.url),["/api/community/session","/api/community/requests"]);
  assert.equal(h.state.calls[0].options.method,"POST");
  const call=h.posts()[0];assert.equal(call.options.credentials,"same-origin");assert.equal(call.options.headers["X-Ufuk-CSRF"],"CSRF_FOR_TEST");
  assert.equal(call.options.headers["Content-Type"],"application/json");
  assert.deepEqual(call.payload,{kind:"contribute",category:"code",subject:"Docs help",message:"Improve the setup guide.",name:"Ada",email:"ada@example.org",language:"tr",consent:true,website:"",idempotency_key:"00000000-0000-4000-8000-000000000001"});
  assert.equal(h.byId("request-received").textContent,"Message received");assert.equal(h.state.focused,"request-received");
  assert.equal(h.byId("request-send"),undefined);assert.ok(!h.root.textContent.includes("ada@example.org"));assert.ok(!h.root.textContent.includes("CSRF_FOR_TEST"));
});

test("a lost POST response keeps delivery unconfirmed and reuses the exact message and key",async()=>{
  const h=harness();await h.fill({email:"ada@example.org"});await h.preview();
  h.state.post=()=>{throw new Error("network disconnected");};await h.send();
  assert.equal(h.byId("request-received"),undefined);assert.equal(h.byId("request-edit").disabled,true);assert.equal(h.state.focused,"request-error");
  assert.equal(h.byId("request-error").tabIndex,-1);assert.match(h.root.textContent,/Keep this message unchanged/);
  const body=h.posts()[0].options.body;
  await h.input("pages-language","tr","change");
  h.state.post=response(200,{...received,status:"in_review",duplicate:true});await h.send();
  assert.equal(h.posts().length,2);assert.equal(h.posts()[1].options.body,body);assert.equal(h.state.keys,1);
  assert.equal(h.byId("request-received").textContent,"Mesaj alındı");assert.match(h.root.textContent,/daha önce alındığını/);
});

test("double clicks and language changes during send cannot create concurrent requests",async()=>{
  const h=harness();await h.fill();await h.preview();let resolve;
  h.state.post=()=>new Promise(done=>{resolve=done;});
  const original=h.byId("request-send");const first=original.dispatch("click");await turn();
  assert.equal(h.posts().length,1);assert.equal(h.byId("request-send").disabled,true);assert.equal(h.byId("request-edit").disabled,true);
  assert.equal(h.byId("pages-language").disabled,true);
  await original.dispatch("click");await h.byId("request-send").dispatch("click");
  h.app.changeLanguage("tr");assert.equal(h.document.documentElement.lang,"en");assert.equal(h.posts().length,1);
  resolve(response(201,{...received}));await first;assert.ok(h.byId("request-received"));
  await original.dispatch("click");assert.equal(h.posts().length,1);
});

test("malformed successful responses never become receipts and preserve the retry identity",async()=>{
  const cases=[response(202,{...received}),response(201,{...received,id:"wrong"}),response(201,{...received,duplicate:"false"}),response(201,{...received,status:"published"}),response(201,{...received,created_at:"2026"}),{ok:true,status:201,json:async()=>{throw new Error("broken JSON");}}];
  for(const bad of cases){
    const h=harness();await h.fill();await h.preview();h.state.post=bad;await h.send();
    assert.equal(h.byId("request-received"),undefined);assert.ok(h.byId("request-error"));assert.equal(h.byId("request-edit").disabled,true);
    const body=h.posts()[0].options.body;h.state.post=response(200,{...received,status:"closed",duplicate:true});await h.send();
    assert.equal(h.posts()[1].options.body,body);assert.ok(h.byId("request-received"));
  }
});

test("server faults are ambiguous delivery; known rejection and throttling permit a safe retry",async()=>{
  for(const status of [500,502,504,408]){
    const h=harness();await h.fill();await h.preview();h.state.post=response(status,{});await h.send();
    assert.equal(h.byId("request-received"),undefined);assert.equal(h.byId("request-edit").disabled,true);
    assert.match(h.root.textContent,/Keep this message unchanged/);
  }
  for(const status of [400,403,422,429]){
    const h=harness();await h.fill();await h.preview();h.state.post=response(status,{});await h.send();
    assert.equal(h.byId("request-received"),undefined);assert.equal(h.byId("request-edit").disabled,false);
    assert.ok(!h.root.textContent.includes("Nothing has been sent yet"));const body=h.posts()[0].options.body;
    h.state.post=response(201,{...received});await h.send();assert.equal(h.posts()[1].options.body,body);assert.ok(h.byId("request-received"));
  }
});

test("an unavailable or invalid session does not POST and retries with the existing key",async()=>{
  for(const session of [response(503,{}),response(200,{}),response(200,{csrf_token:12}),()=>{throw new Error("offline");}]){
    const h=harness();await h.fill();await h.preview();h.state.session=session;await h.send();
    assert.equal(h.posts().length,0);assert.equal(h.byId("request-received"),undefined);assert.match(h.root.textContent,/This attempt did not send/);
    h.state.session=response(200,{csrf_token:"fresh"});await h.send();assert.equal(h.posts().length,1);assert.equal(h.state.keys,1);assert.ok(h.byId("request-received"));
  }
});

test("editing before delivery reuses an unchanged draft key and replaces it only for changed content",async()=>{
  const h=harness();await h.fill();await h.preview();assert.equal(h.state.keys,1);
  await h.byId("request-edit").dispatch("click");assert.equal(h.byId("request-subject").value,"A useful topic");await h.preview();assert.equal(h.state.keys,1);
  await h.byId("request-edit").dispatch("click");await h.input("request-message","A new message");await h.preview();assert.equal(h.state.keys,2);
  await h.send();assert.equal(h.posts()[0].payload.idempotency_key,"00000000-0000-4000-8000-000000000002");assert.equal(h.posts()[0].payload.message,"A new message");
});

test("required consent, invalid fields, honeypot and unavailable secure randomness block delivery",async()=>{
  for(const overrides of [{consent:false},{subject:" "},{message:""},{email:"not-an-email"},{subject:"x".repeat(161)},{message:"x".repeat(4001)},{name:"x".repeat(81)},{message:"bad\u0000value"},{website:"https://spam.example"}]){
    const h=harness();await h.fill(overrides);await h.preview();assert.ok(h.byId("request-error"));assert.equal(h.byId("request-send"),undefined);assert.equal(h.state.calls.length,0);
  }
  const h=harness({secure:false});await h.fill();await h.preview();assert.match(h.root.textContent,/secure request identifier/);assert.equal(h.state.calls.length,0);
});

test("payload validation follows route categories, optional contact and exact field boundaries",()=>{
  const base={category:"other",subject:"s".repeat(160),message:"m".repeat(4000),name:"",email:"",consent:true,website:""};
  const value=requestPayload("support",base,"en");assert.equal(value.name,"");assert.equal(value.email,"");assert.equal(value.subject.length,160);assert.equal(value.message.length,4000);
  assert.throws(()=>requestPayload("contribute",{...base,category:"privacy"},"en"),/invalid/);
  assert.throws(()=>requestPayload("support",{...base,category:"research"},"en"),/invalid/);
  assert.throws(()=>requestPayload("support",{...base,consent:"true"},"en"),/consentNeeded/);
});

test("secure fallback creates a version-four UUID and accepted receipts include changed operator status",()=>{
  const uuid=createUuid({getRandomValues:bytes=>{bytes.fill(255);return bytes;}});
  assert.match(uuid,/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  assert.throws(()=>createUuid({randomUUID:()=>"weak"}),/secureUnavailable/);
  for(const status of ["new","in_review","closed"])assert.equal(validReceipt({...received,status,duplicate:true}),true);
  assert.equal(validReceipt({...received,created_at:"not a date"}),false);
});

test("contact and draft text never enter navigation URLs or storage, even after receipt",async()=>{
  const h=harness();await h.fill({name:"Unique Private Name",email:"private@example.org",subject:"Unique Subject",message:"Unique secret draft"});await h.preview();
  await h.input("pages-language","tr","change");await h.send();await h.input("pages-language","en","change");
  assert.deepEqual(h.state.saved,[{key:"devtrellis.ui.language",value:"tr"},{key:"devtrellis.ui.language",value:"en"}]);
  for(const node of h.flatten(h.root).filter(n=>n.tagName==="A"))assert.doesNotMatch(node.href,/Private|private|Unique|secret/);
  assert.ok(!h.root.textContent.includes("Unique secret draft"));assert.ok(!h.root.textContent.includes("private@example.org"));
});

test("policy copy separates private requests from research and scopes retention without reply promises",()=>{
  for(const language of ["en","tr"]){
    const h=harness({page:"contribute",language});assert.match(h.root.textContent,language==="en"?/90-day/:/90 günlük/);
    assert.match(h.root.textContent,language==="en"?/Backup copies follow a separate rotation/:/Yedek kopyaların rotasyonu ayrıdır/);
    assert.match(h.root.textContent,language==="en"?/does not grant a license/:/lisans izni vermez/);
    const nodes=h.flatten(h.root);assert.equal(h.byId("request-name").required,false);assert.equal(h.byId("request-email").required,false);
    assert.ok(nodes.some(n=>n.tagName==="A"&&n.href==="https://github.com/nihatbirbudak/devtrellis/issues"));
  }
});

test("new language preference wins and legacy preference is still readable",()=>{
  assert.equal(initialLanguage({getItem:key=>({"devtrellis.ui.language":"tr","ufuk.ui.language":"en"}[key])}),"tr");
  assert.equal(initialLanguage({getItem:key=>key==="ufuk.ui.language"?"tr":null}),"tr");
});
test("community guide links use existing repository documentation instead of the missing local guide",()=>{
  for(const language of ["tr","en"]){const h=harness({language});const links=h.flatten(h.root).filter(x=>x.tagName==="A");
    assert.equal(links.some(x=>/^\/guide(?:\?|$)/.test(String(x.href))),false);
    assert.ok(links.some(x=>x.href===(language==="tr"?"https://github.com/nihatbirbudak/devtrellis/blob/main/README.tr.md":"https://github.com/nihatbirbudak/devtrellis#install")));
  }
});
