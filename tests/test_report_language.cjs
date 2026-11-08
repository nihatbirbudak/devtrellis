// Raporun açık dil seçimini, kayıtlı tercihi, eski anahtarı ve depolama engelini ağsız sınar.
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const {test}=require("node:test");
const source=fs.readFileSync(path.join(__dirname,"../radar/static/report-language.js"),"utf8");
function run({href="https://community.example/r/"+"a".repeat(32)+"/v/3#sources",language="tr",values={},blocked=false}={}){
  const redirects=[],writes=[],listeners={};
  const anchors=["tr","en"].map(value=>({dataset:{reportLanguage:value},addEventListener(event,handler){listeners[value]=handler;}}));
  const storage={getItem(key){if(blocked)throw new Error("blocked");return values[key]||null;},setItem(key,value){if(blocked)throw new Error("blocked");writes.push({key,value});}};
  const window={location:{href,replace(value){redirects.push(value);}},localStorage:storage};
  const document={documentElement:{lang:language},querySelectorAll(){return anchors;}};
  vm.runInNewContext(source,{window,document,URL});
  return {redirects,writes,listeners};
}
test("kayıtlı yeni dil query ve fragment korunarak yalnız aynı rapora yönlendirir",()=>{
  const app=run({values:{"devtrellis.ui.language":"en","ufuk.ui.language":"tr"}});
  assert.equal(app.redirects.length,1);const url=new URL(app.redirects[0]);
  assert.equal(url.origin,"https://community.example");assert.equal(url.searchParams.get("lang"),"en");
  assert.equal(url.hash,"#sources");assert.match(url.pathname,/\/v\/3$/);assert.deepEqual(app.writes,[]);
});
test("açık URL dili tercih önündedir; eşleşen veya bozuk tercih döngü oluşturmaz",()=>{
  assert.equal(run({href:"https://community.example/r/a/v/3?lang=tr",values:{"devtrellis.ui.language":"en"}}).redirects.length,0);
  assert.equal(run({language:"en",values:{"devtrellis.ui.language":"en"}}).redirects.length,0);
  assert.equal(run({values:{"devtrellis.ui.language":"javascript:alert(1)"}}).redirects.length,0);
  assert.equal(run({blocked:true}).redirects.length,0);
});
test("eski anahtar okunur, insan dil bağlantısına basınca yalnız yeni tercih yazılır",()=>{
  const app=run({values:{"ufuk.ui.language":"en"}});assert.equal(app.redirects.length,1);
  app.listeners.tr();assert.deepEqual(app.writes,[{key:"devtrellis.ui.language",value:"tr"}]);
});

function centralLanguage(values={}, {browserLanguage="en",blocked=false}={}) {
  const writes=[],events={},state={reloads:0};
  const select={value:"",addEventListener(name,handler){events[name]=handler;}};
  const storage={getItem(key){if(blocked)throw new Error("blocked");return values[key]||null;},
    setItem(key,value){if(blocked)throw new Error("blocked");values[key]=value;writes.push({key,value});}};
  const document={documentElement:{lang:""},title:"DevTrellis",body:{},
    createTreeWalker(){return {nextNode(){return false;}};},
    querySelectorAll(selector){return selector==="[data-language]"?[select]:[];}};
  const window={location:{reload(){state.reloads++;}}};
  const central=fs.readFileSync(path.join(__dirname,"../radar/static/ui-text.js"),"utf8");
  vm.runInNewContext(central,{document,window,localStorage:storage,navigator:{language:browserLanguage},NodeFilter:{SHOW_TEXT:4}});
  window.UfukUI.initialize();
  return {window,document,select,writes,state,values,choose(value){select.value=value;events.change();}};
}
test("gerçek merkezi seçici çelişen yeni TR eski EN değerinde raporla aynı dili seçer",()=>{
  const values={"devtrellis.ui.language":"tr","ufuk.ui.language":"en"};
  const home=centralLanguage(values);
  assert.equal(home.window.UfukUI.language,"tr");assert.equal(home.document.documentElement.lang,"tr");assert.equal(home.select.value,"tr");
  const report=run({language:"en",values});
  assert.equal(new URL(report.redirects[0]).searchParams.get("lang"),"tr");
  assert.deepEqual(home.writes,[]);
});
test("gerçek ana sayfa seçimi yeni anahtarı yazar ve eski değer kalırken rapor yeni seçimi izler",()=>{
  const values={"ufuk.ui.language":"en"};const home=centralLanguage(values,{browserLanguage:"tr"});
  assert.equal(home.document.documentElement.lang,"en");
  home.choose("tr");
  assert.deepEqual(home.writes,[{key:"devtrellis.ui.language",value:"tr"}]);assert.equal(home.state.reloads,1);
  assert.equal(values["ufuk.ui.language"],"en");assert.equal(values["devtrellis.ui.language"],"tr");
  assert.equal(centralLanguage(values).document.documentElement.lang,"tr");
  assert.equal(new URL(run({language:"en",values}).redirects[0]).searchParams.get("lang"),"tr");
});
test("merkezi seçici geçersiz yeni değerde eskiye, depolama kapalıysa tarayıcı diline döner",()=>{
  assert.equal(centralLanguage({"devtrellis.ui.language":"fr","ufuk.ui.language":"tr"}).document.documentElement.lang,"tr");
  assert.equal(centralLanguage({}, {blocked:true,browserLanguage:"tr-TR"}).document.documentElement.lang,"tr");
});

test("okuma rehberi yeni veya eski kayıtlı dili izler ve fragmenti korur",()=>{
  for(const values of [{"devtrellis.ui.language":"en"},{"ufuk.ui.language":"en"}]){
    const app=run({href:"https://community.example/report-guide#limits",values});
    assert.deepEqual(app.redirects,["https://community.example/report-guide?lang=en#limits"]);
  }
  const mixed=run({href:"https://community.example/report-guide",language:"en",
    values:{"devtrellis.ui.language":"tr","ufuk.ui.language":"en"}});
  assert.deepEqual(mixed.redirects,["https://community.example/report-guide?lang=tr"]);
});
test("rehberin açık dili önceliklidir; depolama hatası ve başka sayfa yönlendirme yapmaz",()=>{
  for(const href of ["https://community.example/report-guide?lang=tr","https://community.example/guide","https://community.example/report-guide-extra"]){
    assert.equal(run({href,values:{"devtrellis.ui.language":"en"}}).redirects.length,0);
  }
  assert.equal(run({href:"https://community.example/report-guide",blocked:true}).redirects.length,0);
});
