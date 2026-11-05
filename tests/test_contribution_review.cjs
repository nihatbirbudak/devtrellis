// Yerel ve MCP katkı ekranlarında gerçek karar adımını, gizli onay anahtarını ve pencere sınırını doğrular.
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const {createTranslator}=require("../radar/static/ui-text.js");
const source=name=>fs.readFileSync(path.join(__dirname,"../radar/static",name),"utf8");
const turn=()=>new Promise(resolve=>setImmediate(resolve));
const id="P".repeat(32),token="PRIVATE_APPROVAL_TOKEN_FOR_TEST_ONLY";
const copy=value=>JSON.parse(JSON.stringify(value));
function preview(language="en") {return {preview_id:id,destination:"https://devtrellis.org",review_url:`http://127.0.0.1:8781/static/contribution-review.html?preview=${id}`,packet:{schema:"ufuk-share",schema_version:2,kind:"research",title:"<img src=x onerror=alert(1)>",summary:"Public summary",body:"<script>full original report</script>\nFULL_END",author:"Anonymous contributor",license:"CC-BY-4.0",updated_at:"2026-11-05T07:00:00Z",sources:[{title:"<svg>Source</svg>",url:"https://example.org/source"},{title:"unsafe",url:"javascript:alert(1)"}],provenance:{model:"test/model",generated_at:null,language}}};}
function harness({surface="local",search=`?preview=${id}`,tokenAvailable=true,language="en",status="pending"}={}){
  let document;
  class Node {
    constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.dataset={};this.attributes={};this._text="";this.value="";this.disabled=false;this.hidden=false;this.checked=false;}
    set textContent(value){this._text=String(value);this.children=[];}
    get textContent(){return this._text+this.children.map(node=>node.textContent).join("");}
    set innerHTML(value){throw new Error("Unsafe HTML "+value);}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this._text="";this.children=nodes;}
    addEventListener(name,handler){(this.listeners[name] ||= []).push(handler);}
    setAttribute(name,value){this.attributes[name]=String(value);}
    async dispatch(name){if(this.disabled)return;await Promise.all((this.listeners[name]||[]).map(fn=>fn({target:this,currentTarget:this,preventDefault(){}})));await turn();}
  }
  const flatten=node=>[node,...node.children.flatMap(flatten)],body=new Node("body"),root=new Node("main");root.id="contribution-root";body.dataset.surface=surface;body.append(root);
  document={body,documentElement:{lang:"en"},createElement:tag=>new Node(tag),getElementById:key=>flatten(body).find(node=>node.id===key)};
  const fixture={requests:[],messages:[],opened:[],data:preview(language),fail:false,status};
  const fetch=async(url,options={})=>{fixture.requests.push({url,options:copy(options),payload:options.body?JSON.parse(options.body):null});let value;
    if(url.endsWith("/session"))value={csrf_token:"LOCAL_CSRF"};
    else if(url.includes("/review/")){assert.equal(options.headers["X-Radar-CSRF"],"LOCAL_CSRF");value={...copy(fixture.data),...(tokenAvailable?{approval_token:token}:{})};}
    else if(url.endsWith("/send"))value=fixture.fail?{detail:"Temporary failure"}:{submission:{id:"submission-one",status:fixture.status}};
    else if(url.endsWith("/discard"))value={status:"kept_local"};else throw new Error("Unexpected network: "+url);
    return {ok:!fixture.fail,status:fixture.fail?502:200,json:async()=>value};
  };
  const listeners={},parent={postMessage(message,origin){fixture.messages.push({message:copy(message),origin});}};
  const window={parent,location:{search},open:(...args)=>fixture.opened.push(args),addEventListener:(name,fn)=>(listeners[name] ||= []).push(fn)};
  const context={document,window,navigator:{language},URL,URLSearchParams,Date,Intl,Set,Map,fetch,console,setTimeout:(fn,ms)=>{const timer=setTimeout(fn,ms);timer.unref();return timer;},clearTimeout};
  if(surface==="local")context.UfukUI={language,t:createTranslator(language)};
  const code=surface==="mcp"?source("mcp-contribution.html").match(/<script>([\s\S]*?)<\/script>/)[1]:source("contribution-review.js");
  vm.runInNewContext(code,context);
  const host=(message,{origin="https://host.example",source=parent}={})=>{for(const fn of listeners.message||[])fn({data:message,origin,source});};
  const initialize=(options={})=>host({jsonrpc:"2.0",id:"devtrellis-initialize",result:{protocolVersion:"2026-01-26"}},options);
  const deliver=(data=fixture.data,meta=tokenAvailable?{approval_token:token}:{},options={})=>host({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:{structuredContent:copy(data),_meta:meta}},options);
  return {fixture,document,window,root,flatten,host,initialize,deliver,byId:document.getElementById,ready:async()=>{await turn();await turn();}};
}
async function checkBoth(app){for(const name of ["review-public","review-license"]){app.byId(name).checked=true;await app.byId(name).dispatch("change");}}

test("yerel inceleme sadece CSRF korumalı önizlemeyi okur; iki kutu olmadan göndermez",async()=>{
  const app=harness();await app.ready();
  assert.deepEqual(app.fixture.requests.map(row=>row.url),["/api/platform/community/session",`/api/platform/community/review/${id}`]);
  assert.equal(app.byId("review-send").disabled,true);assert.equal(app.byId("review-public").checked,false);assert.equal(app.byId("review-license").checked,false);
  assert.ok(app.root.textContent.includes("FULL_END"));assert.ok(app.root.textContent.includes("test/model"));assert.ok(app.root.textContent.includes("Generation date not recorded"));assert.ok(app.root.textContent.includes("https://devtrellis.org"));
  assert.ok(!app.root.textContent.includes(token));assert.ok(app.flatten(app.root).every(node=>!["SCRIPT","IMG","SVG"].includes(node.tagName)&&!String(node.href||"").startsWith("javascript:")));
  app.byId("review-public").checked=true;await app.byId("review-public").dispatch("change");assert.equal(app.byId("review-send").disabled,true);
  await checkBoth(app);await app.byId("review-send").dispatch("click");
  const call=app.fixture.requests.at(-1);assert.equal(call.url,"/api/platform/community/send");assert.equal(call.options.headers["X-Radar-CSRF"],"LOCAL_CSRF");assert.deepEqual(call.payload,{preview_id:id,approval_token:token,reviewed_public:true,consent:true});
  assert.match(app.byId("review-status").textContent,/Pending review/);assert.equal(app.byId("review-send").disabled,true);
});

test("yerelde tut iki paylaşım iznini istemeden önizlemeyi iptal eder; geçersiz URL hiçbir istek yapmaz",async()=>{
  const app=harness();await app.ready();await app.byId("review-keep").dispatch("click");
  assert.deepEqual(app.fixture.requests.at(-1).payload,{preview_id:id,approval_token:token});assert.equal(app.fixture.requests.at(-1).url,"/api/platform/community/discard");
  assert.match(app.byId("review-status").textContent,/copy was not sent/);assert.equal(app.fixture.requests.some(row=>row.url.endsWith("/send")),false);
  for(const search of ["?preview=../private",`?preview=${id}&preview=${id}`,"?preview="]){const invalid=harness({search});await invalid.ready();assert.equal(invalid.fixture.requests.length,0);}
});

test("iki dil değişimi onay kutularını sıfırlar; başarısız gönderim aynı snapshot ile yeniden denenir",async()=>{
  const app=harness({language:"tr"});await app.ready();assert.match(app.root.textContent,/Katkıyı göndermeden önce incele/);await checkBoth(app);
  app.byId("review-language").value="en";await app.byId("review-language").dispatch("change");assert.match(app.root.textContent,/Review before contributing/);assert.equal(app.byId("review-send").disabled,true);
  await checkBoth(app);app.fixture.fail=true;await app.byId("review-send").dispatch("click");app.fixture.fail=false;await app.byId("review-send").dispatch("click");
  const sends=app.fixture.requests.filter(row=>row.url.endsWith("/send"));assert.equal(sends.length,2);assert.deepEqual(sends[0].payload,sends[1].payload);
  app.byId("review-language").value="tr";await app.byId("review-language").dispatch("change");assert.match(app.byId("review-status").textContent,/İnceleme bekliyor/);
});

test("MCP el sıkışması sonrası hedef origin sabitlenir; başka pencere veya origin paketi kabul edilmez",async()=>{
  const app=harness({surface:"mcp"});assert.equal(app.fixture.requests.length,0);
  assert.equal(app.fixture.messages[0].origin,"*");assert.equal(app.fixture.messages[0].message.method,"ui/initialize");assert.equal(app.fixture.messages[0].message.params.protocolVersion,"2026-01-26");
  app.deliver();assert.equal(app.byId("review-send"),undefined);
  app.initialize({source:{}});app.deliver();assert.equal(app.byId("review-send"),undefined);
  app.initialize();assert.equal(app.fixture.messages[1].message.method,"ui/notifications/initialized");assert.equal(app.fixture.messages[1].origin,"https://host.example");
  app.deliver(undefined,undefined,{origin:"https://attacker.example"});assert.equal(app.byId("review-send"),undefined);
  app.deliver();assert.equal(app.byId("review-send").disabled,true);assert.ok(!app.root.textContent.includes(token));assert.equal(app.fixture.messages.length,2);
  const wrong=harness({surface:"mcp"});wrong.initialize({origin:"null"});wrong.deliver();assert.equal(wrong.fixture.messages.length,1);assert.match(wrong.root.textContent,/could not be verified/);
});

test("MCP yalnız açık iki onaydan sonra app-only karar aracını çağırır; token modele mesaj olarak taşınmaz",async()=>{
  const app=harness({surface:"mcp"});app.initialize();app.deliver();await checkBoth(app);
  const pending=app.byId("review-send").dispatch("click");await turn();const request=app.fixture.messages.at(-1);
  assert.equal(request.origin,"https://host.example");assert.equal(request.message.method,"tools/call");assert.deepEqual(request.message.params,{name:"contribution_decision",arguments:{preview_id:id,approval_token:token,decision:"share",reviewed_public:true,consent:true}});
  assert.equal(app.byId("review-send").disabled,true);assert.equal(app.byId("review-keep").disabled,true);
  app.host({jsonrpc:"2.0",id:request.message.id,result:{structuredContent:{submission:{id:"S",status:"withdrawn"}}}});await pending;
  assert.match(app.byId("review-status").textContent,/Withdrawn/);assert.equal(app.fixture.requests.length,0);
  assert.ok(app.fixture.messages.every(row=>!["ui/message","ui/update-model-context","notifications/message"].includes(row.message.method)));
});

test("MCP yerelde tut kararı onaysız paylaşmaz; token yoksa yalnız güvenli fallback bağlantısı sunulur",async()=>{
  const app=harness({surface:"mcp"});app.initialize();app.deliver();const pending=app.byId("review-keep").dispatch("click");await turn();const request=app.fixture.messages.at(-1);
  assert.equal(request.message.params.arguments.decision,"keep");assert.equal(request.message.params.arguments.consent,false);assert.equal(request.message.params.arguments.reviewed_public,false);
  app.host({jsonrpc:"2.0",id:request.message.id,result:{structuredContent:{status:"kept_local"}}});await pending;assert.match(app.byId("review-status").textContent,/copy was not sent/);
  const fallback=harness({surface:"mcp",tokenAvailable:false});fallback.initialize();fallback.deliver();assert.equal(fallback.byId("review-send").disabled,true);
  const opening=fallback.byId("review-fallback").dispatch("click");await turn();const link=fallback.fixture.messages.at(-1);assert.equal(link.message.method,"ui/open-link");assert.equal(link.message.params.url,fallback.fixture.data.review_url);
  fallback.host({jsonrpc:"2.0",id:link.message.id,result:{}});await opening;
  const unsafe=harness({surface:"mcp",tokenAvailable:false});unsafe.initialize();unsafe.fixture.data.review_url="https://attacker.example/?token=steal";unsafe.deliver();assert.equal(unsafe.byId("review-fallback"),undefined);assert.equal(unsafe.byId("review-send").disabled,true);
});

test("MCP kaynak self-contained kalır; inceleme paketi protokol ve UI dışından betik yüklemez",()=>{
  const html=source("mcp-contribution.html");assert.doesNotMatch(html,/<script[^>]+src=|<link[^>]+href=|<iframe/i);assert.match(html,/connect-src 'none'/);
  assert.ok(html.includes(source("contribution-review.js").trim()),"MCP bundle and local review core must stay identical");
});
