// Yerel gönderi ekranının uzak isteği kullanıcı seçimine bağlamasını ve sürümlü geri çekmeyi sınar.
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const code=fs.readFileSync(path.join(__dirname,"../radar/static/contribution-submissions.js"),"utf8");
const turn=()=>new Promise(resolve=>setImmediate(resolve));
const localId="a".repeat(32),remoteId="b".repeat(32);
const copy=value=>JSON.parse(JSON.stringify(value));
function harness({language="en",status="pending",empty=false,missing=false,failLoad=false,rows=null,confirmDialog=true}={}){
  class Node{
    constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attributes={};this._text="";this.disabled=false;}
    set textContent(value){this._text=String(value);this.children=[];}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join("");}
    set innerHTML(value){throw new Error("Unsafe HTML: "+value);}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;this._text="";}
    setAttribute(name,value){this.attributes[name]=value;}
    addEventListener(name,fn){(this.listeners[name] ||= []).push(fn);}
    async dispatch(name){if(this.disabled)return;await Promise.all((this.listeners[name]||[]).map(fn=>fn({target:this})));await turn();}
  }
  const root=new Node("main"),flatten=node=>[node,...node.children.flatMap(flatten)];root.id="submissions-root";
  const document={documentElement:{lang:"en"},createElement:tag=>new Node(tag),getElementById:id=>flatten(root).find(node=>node.id===id)};
  const row={local_id:localId,title:"<img src=x onerror=alert(1)> Public report",submission:missing?null:{id:remoteId,status,version:1,updated_at:"2026-11-05T10:00:00Z",moderation_note:"<script>Reviewer note</script>"}};
  const fixture={calls:[],confirm:false,confirmations:[],row,nextStatus:"published",fail:false,changeId:false,rows:rows||[row],badPage:false,failPage:false};
  const fetch=async(url,options={})=>{
    fixture.calls.push({url,options:copy(options),payload:options.body?JSON.parse(options.body):undefined});
    let value;
    if(url.endsWith("/session"))value={csrf_token:"CSRF_LOCAL",destination:"https://devtrellis.org"};
    else if(url.includes("/submissions?")){
      if(failLoad||fixture.failPage)return {ok:false,json:async()=>({detail:"unavailable"})};
      const query=new URL(url,"http://127.0.0.1").searchParams,offset=Number(query.get("offset")),limit=Number(query.get("limit")),records=empty?[]:fixture.rows;
      value={items:copy(records.slice(offset,offset+limit)),destination:"https://devtrellis.org",total:records.length,offset:fixture.badPage?offset+1:offset,limit};
    }
    else{
      assert.equal(options.method,"POST");assert.equal(options.headers["X-Radar-CSRF"],"CSRF_LOCAL");
      if(fixture.fail)return {ok:false,json:async()=>({detail:"conflict"})};
      value={local_id:localId,submission:{...copy(row.submission),id:fixture.changeId?"c".repeat(32):remoteId,status:url.endsWith("/withdraw")?"withdrawn":fixture.nextStatus,version:(row.submission?.version||0)+1}};
    }
    return {ok:true,json:async()=>value};
  };
  const DevTrellisConfirm={ask:async options=>{fixture.confirmations.push(options.message);fixture.confirmOptions.push(options);return fixture.confirm;}};fixture.confirmOptions=[];
  const window={location:{search:"?lang="+language},confirm:()=>{throw new Error("window.confirm must not be used");},...(confirmDialog?{DevTrellisConfirm}:{})};
  vm.runInNewContext(code,{document,window,fetch,URLSearchParams,Set,console});
  return {fixture,root,flatten,byId:document.getElementById,ready:async()=>{await turn();await turn();}};
}

test("sayfa açılışı yalnız yerel snapshot okur; güvenli metin ve dil seçimi dış istek üretmez",async()=>{
  const app=harness();await app.ready();
  assert.deepEqual(app.fixture.calls.map(call=>call.url),["/api/platform/community/session","/api/platform/community/submissions?offset=0&limit=25"]);
  assert.match(app.root.textContent,/Pending review/);assert.match(app.root.textContent,/<script>Reviewer note/);
  assert.ok(app.flatten(app.root).every(node=>!["IMG","SCRIPT","SVG"].includes(node.tagName)));assert.ok(!app.root.textContent.includes("CSRF_LOCAL"));
  app.byId("submissions-language").value="tr";await app.byId("submissions-language").dispatch("change");
  assert.match(app.root.textContent,/Gönderimlerim/);assert.match(app.root.textContent,/İnceleme bekliyor/);assert.equal(app.fixture.calls.length,2);
});

test("kullanıcı durum yenileyince yayın bağlantısı görünür; ret notu ve yayın kaldırma durumu izlenir",async()=>{
  const app=harness();await app.ready();await app.byId("refresh-"+localId).dispatch("click");
  const request=app.fixture.calls.at(-1);assert.equal(request.url,"/api/platform/community/submissions/"+localId+"/refresh");assert.deepEqual(request.payload,{});
  assert.equal(app.flatten(app.root).find(node=>node.tagName==="A").href,"https://devtrellis.org/?card="+remoteId);
  app.fixture.nextStatus="hidden";await app.byId("refresh-"+localId).dispatch("click");assert.match(app.root.textContent,/Removed from publication/);assert.equal(app.flatten(app.root).filter(node=>node.tagName==="A").length,0);
  app.fixture.nextStatus="rejected";await app.byId("refresh-"+localId).dispatch("click");assert.match(app.root.textContent,/Rejected/);assert.match(app.root.textContent,/Review note/);
});

test("geri çekme yalnız kullanıcı onayıyla geçerli sürümü gönderir ve tekrar düğmesini kaldırır",async()=>{
  const app=harness({language:"tr",status:"published"});await app.ready();
  await app.byId("withdraw-"+localId).dispatch("click");assert.equal(app.fixture.calls.length,2);assert.match(app.fixture.confirmations[0],/geri çekmek istiyor musun/);
  app.fixture.confirm=true;await app.byId("withdraw-"+localId).dispatch("click");
  const call=app.fixture.calls.at(-1);assert.equal(call.url,"/api/platform/community/submissions/"+localId+"/withdraw");assert.deepEqual(call.payload,{expected_version:1});
  assert.match(app.root.textContent,/Geri çekildi/);assert.equal(app.byId("withdraw-"+localId),undefined);assert.equal(app.flatten(app.root).filter(node=>node.tagName==="A").length,0);
});

test("başarısız veya başka gönderiye ait yanıt başarı diye görünmez; bilinmeyen teslim geri çekilemez",async()=>{
  const app=harness();await app.ready();app.fixture.confirm=true;app.fixture.fail=true;
  await app.byId("withdraw-"+localId).dispatch("click");assert.match(app.root.textContent,/could not be completed/);assert.match(app.root.textContent,/Pending review/);
  app.fixture.fail=false;app.fixture.changeId=true;await app.byId("refresh-"+localId).dispatch("click");assert.match(app.root.textContent,/response is invalid/);assert.match(app.root.textContent,/Pending review/);
  const missing=harness({missing:true});await missing.ready();assert.match(missing.root.textContent,/Delivery is unconfirmed/);assert.equal(missing.byId("withdraw-"+localId),undefined);assert.ok(missing.byId("refresh-"+localId));
  const empty=harness({empty:true});await empty.ready();assert.match(empty.root.textContent,/no saved submissions/);
});

test("belirsiz teslim yeniden yüklenmeden durum sorgusuyla bulunur; başarısız sorgu belirsiz kalır",async()=>{
  const app=harness({missing:true});await app.ready();
  app.fixture.fail=true;await app.byId("refresh-"+localId).dispatch("click");
  assert.match(app.root.textContent,/Delivery is unconfirmed/);assert.match(app.root.textContent,/could not be completed/);
  assert.equal(app.byId("withdraw-"+localId),undefined);
  app.fixture.fail=false;await app.byId("refresh-"+localId).dispatch("click");
  assert.match(app.root.textContent,/Published/);assert.doesNotMatch(app.root.textContent,/Delivery is unconfirmed/);
  assert.equal(app.flatten(app.root).find(node=>node.tagName==="A").href,"https://devtrellis.org/?card="+remoteId);
  assert.ok(app.fixture.calls.filter(call=>call.options.method==="POST").every(call=>call.url.endsWith("/refresh")&&JSON.stringify(call.payload)==="{}"));
});

test("ilk yerel yükleme başarısız olunca süren yükleme veya boş liste diye gösterilmez",async()=>{
  const app=harness({failLoad:true});await app.ready();
  assert.match(app.root.textContent,/could not be completed/);
  assert.doesNotMatch(app.root.textContent,/Reading local records|no saved submissions/);
  assert.equal(app.byId("submissions-reload").disabled,false);
});

test("yerel geçmiş yüz kayıt sonrasını iki dilde sayfalar; sayfalama uzak topluluk veya yazma isteği göndermez",async()=>{
  const rows=Array.from({length:105},(_,index)=>({local_id:index.toString(16).padStart(32,"0"),title:`Saved research ${index}`,submission:{id:remoteId,status:"published",version:1}}));
  for(const language of ["en","tr"]){
    const app=harness({language,rows});await app.ready();assert.equal(app.byId("submissions-previous").disabled,true);assert.match(app.byId("submissions-range").textContent,/1–25 \/ 105/);
    for(let page=1;page<5;page++)await app.byId("submissions-next").dispatch("click");
    assert.match(app.root.textContent,/Saved research 104/);assert.match(app.byId("submissions-range").textContent,/101–105 \/ 105/);assert.equal(app.byId("submissions-next").disabled,true);
    await app.byId("submissions-previous").dispatch("click");assert.match(app.byId("submissions-range").textContent,/76–100 \/ 105/);
    assert.ok(app.fixture.calls.every(call=>call.url.startsWith("/api/platform/community/")&&!call.options.method));
    assert.match(app.root.textContent,language==="en"?/no new contribution preview is needed/:/yeni bir katkı önizlemesi gerekmez/);
  }
});

test("geçmişte başarısız sayfa eski kayıtları korur; kayıt sayısı azalırsa son geçerli sayfaya döner",async()=>{
  const rows=Array.from({length:55},(_,index)=>({local_id:index.toString(16).padStart(32,"0"),title:`Saved research ${index}`,submission:null}));
  const app=harness({rows});await app.ready();app.fixture.failPage=true;await app.byId("submissions-next").dispatch("click");
  assert.match(app.root.textContent,/could not be completed/);assert.match(app.byId("submissions-range").textContent,/1–25 \/ 55/);assert.match(app.root.textContent,/Saved research 0/);
  app.fixture.failPage=false;app.fixture.badPage=true;await app.byId("submissions-next").dispatch("click");assert.match(app.root.textContent,/response is invalid/);assert.match(app.byId("submissions-range").textContent,/1–25 \/ 55/);
  app.fixture.badPage=false;await app.byId("submissions-next").dispatch("click");await app.byId("submissions-next").dispatch("click");assert.match(app.byId("submissions-range").textContent,/51–55 \/ 55/);
  app.fixture.rows=rows.slice(0,10);await app.byId("submissions-reload").dispatch("click");assert.equal(app.byId("submissions-range"),undefined);assert.match(app.root.textContent,/Saved research 0/);assert.equal(app.fixture.calls.at(-1).url,"/api/platform/community/submissions?offset=0&limit=25");
});

test("geri çekme onayı sayfa içi pencereden gelir; düğme etiketi ve odak dönüşü iki dilde verilir",async()=>{
  for(const language of ["en","tr"]){
    const app=harness({language,status:"published"});await app.ready();
    const trigger=app.byId("withdraw-"+localId);await trigger.dispatch("click");
    const options=app.fixture.confirmOptions.at(-1);
    assert.equal(options.confirmLabel,language==="en"?"Withdraw submission":"Gönderiyi geri çek");
    assert.equal(options.tone,"danger");assert.equal(options.returnFocus,trigger);
    assert.match(options.message,language==="en"?/Withdraw this submission\?/:/geri çekmek istiyor musun\?/);
    assert.equal(app.fixture.calls.filter(call=>call.options.method==="POST").length,0);
  }
});

test("onay bileşeni yüklenmezse geri çekme sessizce düşmez; görünür ileti çıkar ve istek gitmez",async()=>{
  for(const language of ["en","tr"]){
    const app=harness({language,status:"published",confirmDialog:false});await app.ready();
    await app.byId("withdraw-"+localId).dispatch("click");
    assert.equal(app.fixture.calls.filter(call=>call.options.method==="POST").length,0);
    const alert=app.flatten(app.root).find(node=>node.attributes.role==="alert");
    assert.equal(alert.textContent,language==="en"?"The confirmation dialog could not open; nothing was done. Reload the page and try again.":"Onay penceresi açılamadı; işlem yapılmadı. Sayfayı yenileyip yeniden dene.");
  }
});
