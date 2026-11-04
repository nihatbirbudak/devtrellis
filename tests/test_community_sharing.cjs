// Hesapsız paylaşımda açık onayı, yerel kalmayı, hazır araştırmanın modelsiz kullanımını ve güvenli metin gösterimini sınar.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const {createTranslator, provenanceFacts, EN} = require("../radar/static/ui-text.js");
const read = name => fs.readFileSync(path.join(__dirname, "../radar/static", name), "utf8");
const copy = value => JSON.parse(JSON.stringify(value));
const turn = () => new Promise(resolve => setImmediate(resolve));
const packet = () => ({schema:"ufuk-share", schema_version:2, kind:"research", title:'<img src=x onerror="alert(1)">', summary:"Saved summary", body:"<script>original research</script>", sources:[{title:"Original source",url:"https://example.org/source"}], author:"Anonymous contributor", license:"CC-BY-4.0", created_at:"2026-01-01T00:00:00Z", updated_at:"2026-01-02T00:00:00Z", provenance:{model:"test/model",provider:"nvidia",generated_at:"2026-01-01T12:00:00Z",question:"Is this correct?",claim_text:"Original claim <svg onload=alert(1)>",topic:"software",source_url:"https://example.org/source",evidence_status:"verified",language:"en",modified:false}});

function harness({language="en", metadata=true}={}) {
  let document;
  class Node {
    constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attributes={};this.value="";this._text="";this.hidden=false;this.disabled=false;this.checked=false;this.isConnected=true;}
    set textContent(value){this._text=String(value);this.children=[];}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join("");}
    set innerHTML(value){throw new Error("Unsafe HTML: "+value);}
    append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
    replaceChildren(...items){this.children=[];this._text="";this.append(...items);}
    replaceWith(item){const index=this.parent.children.indexOf(this);item.parent=this.parent;this.parent.children[index]=item;this.isConnected=false;}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);this.isConnected=false;}
    setAttribute(name,value){this.attributes[name]=String(value);}
    focus(){document.activeElement=this;}
    addEventListener(name,handler){(this.listeners[name] ||= []).push(handler);}
    showModal(){this.open=true;}
    close(){this.open=false;for(const fn of this.listeners.close||[])fn({target:this});}
    async dispatch(name,details={}){if(this.disabled)return;const event={target:this,currentTarget:this,preventDefault(){},...details};await Promise.all((this.listeners[name]||[]).map(fn=>fn(event)));await turn();await turn();}
  }
  document={body:new Node("body"),activeElement:null};
  const element=(tag,classes="",text)=>{const node=new Node(tag);node.className=classes;if(text!==undefined)node.textContent=text;return node;};
  const localStorage=new Map();localStorage.getItem=localStorage.get.bind(localStorage);localStorage.setItem=localStorage.set.bind(localStorage);
  const fixture={requests:[],packet:packet(),preview:0,failSend:false,status:"pending",toasts:[],seeds:[]};
  if(!metadata){delete fixture.packet.provenance;fixture.packet.schema_version=1;}
  const post=async(url,payload)=>{
    fixture.requests.push({url,payload:copy(payload)});
    if(url.endsWith("/preview")){
      const draft=copy(fixture.packet);for(const key of ["author","summary","body"])if(Object.hasOwn(payload,key))draft[key]=payload[key];
      for(const key of ["question","claim_text"])if(draft.provenance&&Object.hasOwn(payload,key))draft.provenance[key]=payload[key];
      return {preview_id:`preview-${++fixture.preview}`,packet:draft,destination:"https://devtrellis.org",notice:""};
    }
    if(url.endsWith("/send")){if(fixture.failSend)throw new Error("Topluluk bağlantısı kurulamadı; yerel araştırmanız korunuyor. Yeniden deneyebilirsiniz.");return {submission:{id:"submission-one",status:fixture.status,version:1},local_id:"receipt-one",duplicate:false};}
    if(url.endsWith("/search"))return {items:[{id:"card-one",packet:copy(fixture.packet)}],total:1};
    if(url.endsWith("/import"))return {entry:{id:"local-one"},duplicate:false};
    throw new Error("Unexpected POST "+url);
  };
  const request=async url=>{fixture.requests.push({url});return {packet:copy(fixture.packet)};};
  const UI={language,t:createTranslator(language),provenanceFacts};
  const window={UfukUI:UI,addEventListener(){}};
  const scope={window,document,UfukUI:UI,localStorage,URL,console,Map};
  vm.runInNewContext(read("community-share.js"),scope);
  vm.runInNewContext(read("community-library.js"),scope);
  const sourceLink=(url,title)=>{const node=element("a","",title);if(/^https?:/.test(url))node.href=url;return node;};
  const formatDate=value=>value||UI.t("Bilinmiyor");
  const sharing=window.DevTrellisShareFactory({element,post,formatDate});
  const library=window.DevTrellisLibraryFactory({element,request,post,sourceLink,formatDate,toast:text=>fixture.toasts.push(text)});
  const flatten=root=>[root,...root.children.flatMap(flatten)];
  const find=(label,root=document.body)=>flatten(root).find(node=>node.tagName==="BUTTON"&&node.textContent===label);
  const byId=id=>flatten(document.body).find(node=>node.id===id);
  const consent=()=>flatten(document.body).find(node=>node.tagName==="INPUT"&&node.type==="checkbox");
  return {sharing,library,fixture,document,element,flatten,find,byId,consent,localStorage};
}

test("tamamlanmış rapor önerisi ve yerelde tut seçimi hiçbir istek göndermez", async()=>{
  const app=harness();const ref={task_id:"private-task"};const box=app.sharing.prompt(ref);app.document.body.append(box);
  assert.equal(app.fixture.requests.length,0);
  await app.find("Keep local").dispatch("click");
  assert.equal(app.fixture.requests.length,0);
  assert.match(box.textContent,/keeping this research locally/);
  assert.equal(app.localStorage.get("devtrellis.share-choice.task.private-task"),"kept");
  const refreshed=app.sharing.prompt(ref);assert.match(refreshed.textContent,/keeping this research locally/);
});

test("tam önizleme metindir; izin boş başlar, değişiklik onayı kaldırır ve yalnız yeni snapshot gönderilir", async()=>{
  const app=harness();const original=copy(app.fixture.packet);
  await app.sharing.open({task_id:"private-task"});
  assert.deepEqual(app.fixture.requests,[{url:"/api/platform/community/preview",payload:{task_id:"private-task"}}]);
  assert.equal(app.consent().checked,false);assert.equal(app.find("Send the copy I approved").disabled,true);
  assert.ok(app.document.body.textContent.includes(original.body));
  assert.ok(app.document.body.textContent.includes(JSON.stringify(original,null,2)));
  assert.ok(app.flatten(app.document.body).every(node=>!["IMG","SCRIPT","SVG"].includes(node.tagName)));
  app.consent().checked=true;await app.consent().dispatch("change");
  app.byId("direct-share-question").value="Redacted question";await app.byId("direct-share-question").dispatch("input");
  app.byId("direct-share-claim_text").value="Redacted claim";await app.byId("direct-share-claim_text").dispatch("input");
  assert.equal(app.consent().checked,false);assert.equal(app.find("Send the copy I approved").disabled,true);
  await app.find("Update preview").dispatch("click");
  assert.equal(app.fixture.requests[1].payload.question,"Redacted question");assert.equal(app.fixture.requests[1].payload.claim_text,"Redacted claim");
  assert.equal(app.consent().checked,false);
  app.consent().checked=true;await app.consent().dispatch("change");
  app.fixture.status="withdrawn";await app.find("Send the copy I approved").dispatch("click");
  assert.deepEqual(app.fixture.requests.at(-1),{url:"/api/platform/community/send",payload:{preview_id:"preview-2",consent:true}});
  assert.match(app.document.body.textContent,/Submission status: Withdrawn/);
  assert.doesNotMatch(app.document.body.textContent,/Contribution submitted for review/);
  assert.equal(app.consent().parent.hidden,true);
  assert.deepEqual(app.fixture.packet,original);
});

test("belirsiz gönderimde tekrar aynı onaylı önizlemeyi kullanır; özel rapor değişmez", async()=>{
  const app=harness();await app.sharing.open({entry_id:"entry-one"});
  app.consent().checked=true;await app.consent().dispatch("change");app.fixture.failSend=true;
  await app.find("Send the copy I approved").dispatch("click");
  assert.match(app.document.body.textContent,/your local research is preserved/);
  app.fixture.failSend=false;await app.find("Send the copy I approved").dispatch("click");
  const sends=app.fixture.requests.filter(row=>row.url.endsWith("/send"));
  assert.equal(sends.length,2);assert.deepEqual(sends[0].payload,sends[1].payload);
  assert.equal(sends[0].payload.preview_id,"preview-1");
});

test("v1 paylaşım model ve üretim tarihinin eksik olduğunu belirtir ve köken editörünü açmaz", async()=>{
  const app=harness({metadata:false});await app.sharing.open({entry_id:"v1"});
  assert.equal(app.byId("direct-share-question").hidden,true);assert.equal(app.byId("direct-share-claim_text").hidden,true);
  assert.match(app.document.body.textContent,/Model not recorded/);assert.match(app.document.body.textContent,/Generation date not recorded/);
  assert.ok(app.document.body.textContent.includes("2026-01-02"));
  await app.find("Update preview").dispatch("click");
  assert.equal(Object.hasOwn(app.fixture.requests.at(-1).payload,"question"),false);
});

test("hazır arama ancak açık tıklamada sorgu gönderir; kullanma yalnız içe aktarır", async()=>{
  const app=harness();const box=app.library.searchBox({initialQuery:"Question",onResearch:seed=>app.fixture.seeds.push(seed)});app.document.body.append(box);
  const input=app.flatten(box).find(node=>node.type==="search");input.value="Original claim";await input.dispatch("input");
  assert.equal(app.fixture.requests.length,0);
  await app.find("Find existing research").dispatch("click");
  assert.deepEqual(app.fixture.requests,[{url:"/api/platform/community/search",payload:{query:"Original claim",limit:10}}]);
  await app.find("Enough for me · Use without a model").dispatch("click");
  assert.deepEqual(app.fixture.requests.at(-1),{url:"/api/platform/community/cards/card-one/import",payload:{}});
  assert.equal(app.fixture.seeds.length,0);assert.equal(app.fixture.requests.some(row=>row.url.includes("research-requests")),false);
  assert.ok(app.flatten(box).some(node=>node.href==="/platform?entry=local-one"));
});

test("kamu bağlantısı yalnız niyettir: getir onayı olmadan uzak okuma veya model çağrısı yok", async()=>{
  const app=harness({metadata:false});await app.library.intent("card-one",seed=>app.fixture.seeds.push(seed));
  assert.equal(app.fixture.requests.length,0);
  await app.find("Fetch community record").dispatch("click");
  assert.deepEqual(app.fixture.requests,[{url:"/api/platform/community/cards/card-one"}]);
  assert.match(app.document.body.textContent,/Model not recorded/);assert.match(app.document.body.textContent,/Generation date not recorded/);
  assert.ok(app.document.body.textContent.includes("2026-01-02"));
  await app.find("Research from this starting point").dispatch("click");
  assert.equal(app.fixture.requests.length,1);assert.equal(app.fixture.seeds.length,1);
});

test("yeni araştırma özgün iddiayı korur; uzun metni sınırlar ve ortak içeriği doğrulanmış saymaz",()=>{
  const app=harness();const original=packet();original.body="a".repeat(9000);
  const seed=app.library.researchSeed(original);
  assert.equal(seed.question,original.provenance.question);assert.equal(seed.claim_text,original.provenance.claim_text);
  assert.equal(seed.source_url,"https://example.org/source");assert.equal(seed.topic,"software");
  assert.ok(seed.context_text.length<=6000);assert.match(seed.context_text,/do not treat it as source evidence or verified fact/);
  assert.match(seed.context_text,/first 5,000 characters/);assert.equal(original.body.length,9000);
});

test("hesapsız paylaşım ve hazır arama metinlerinin EN karşılıkları eksiksizdir",()=>{
  for(const name of ["community-share.js","community-library.js"]){
    for(const match of read(name).matchAll(/\bt\("([^"\\]*(?:\\.[^"\\]*)*)"/g))assert.ok(EN[match[1]],`${name}: ${match[1]}`);
  }
  const backend=fs.readFileSync(path.join(__dirname,"../radar/topluluk_baglantisi.py"),"utf8");
  const notice=[...backend.match(/NOTICE = \(([\s\S]+?)\)/)[1].matchAll(/"([^"\n]*)"/g)].map(match=>match[1]).join("");
  assert.ok(EN[notice],"Full backend preview notice must be translated");
  for(const match of backend.matchAll(/raise ValueError\("([^"\n]+)"\)/g)) if (/[ğüşıöçĞÜŞİÖÇ]/.test(match[1])) assert.ok(EN[match[1]],`Secure store error: ${match[1]}`);
  for(const match of backend.matchAll(/ToplulukBaglantiHatasi\("([^"\n]+)"\)/g))assert.ok(EN[match[1]],`Backend error: ${match[1]}`);
  for(const label of ["Paylaşılacak araştırma sorusu","Paylaşılacak iddia","Görünen ad (isteğe bağlı)","Paylaşmayı değerlendir","Önizle ve paylaş"]){assert.notEqual(createTranslator("en")(label),label);assert.equal(createTranslator("tr")(label),label);}
});
