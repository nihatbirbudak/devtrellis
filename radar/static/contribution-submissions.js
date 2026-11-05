// Yerel gönderi kayıtlarını gösterir; durumu yalnız seçimle yeniler, geri çekmeyi kullanıcıya onaylatır.
(() => {
  "use strict";
  const words={
    title:["Gönderimlerim","My submissions"],language:["Dil","Language"],
    intro:["Bu bilgisayarda saklanan gönderiler sayfalar halinde gösterilir. Durum kayıtlı bir görüntüdür; güncel sonucu almak için Durumu yenile'yi seç. Bu sayfayı açmak veya sayfa değiştirmek topluluğa istek göndermez.","Submissions saved on this computer are shown in pages. Status is a saved snapshot; choose Refresh status for the current result. Opening this page or changing pages makes no community request."],
    lifetime:["Bu sayfa MCP oturumu açıkken çalışır. Sonraki oturumda asistandan gönderim geçmişini açmasını isteyebilirsin; yeni bir katkı önizlemesi gerekmez.","This page works while the MCP session is running. In a later session, ask your assistant to open submission history; no new contribution preview is needed."],
    previous:["Önceki sayfa","Previous page"],next:["Sonraki sayfa","Next page"],pages:["Gönderim sayfaları","Submission pages"],range:["Gösterilen kayıtlar","Showing records"],
    reload:["Yerel listeyi yenile","Reload local list"],refresh:["Durumu yenile","Refresh status"],
    withdraw:["Gönderiyi geri çek","Withdraw submission"],open:["Yayımlanan araştırmayı aç","Open published research"],
    confirm:["Bu gönderiyi geri çekmek istiyor musun? Yayındaki kopya erişime kapanır; başkalarının önceden indirdiği kopyalar geri alınamaz.","Withdraw this submission? Its published server copy will become unavailable; copies already downloaded by others cannot be recalled."],
    empty:["Bu bilgisayardan henüz gönderim kaydı yok.","There are no saved submissions on this computer yet."],
    missing:["Teslim doğrulanmadı. Durumu yenile ile topluluğa ulaşıp ulaşmadığını kontrol et; bu işlem araştırmayı yeniden göndermez. Kayıt bulunamazsa yeni bir önizleme hazırlayıp aynı araştırmayı yeniden gönderebilirsin.","Delivery is unconfirmed. Refresh status to check whether it reached the community; this does not send the research again. If no record is found, prepare a new preview and retry the same research."],
    note:["İnceleme notu","Review note"],updated:["Son durum değişikliği","Last status change"],
    pending:["İnceleme bekliyor","Pending review"],published:["Yayımlandı","Published"],rejected:["Reddedildi","Rejected"],withdrawn:["Geri çekildi","Withdrawn"],hidden:["Yayından kaldırıldı","Removed from publication"],
    loading:["Yerel kayıtlar okunuyor…","Reading local records…"],
    failed:["İşlem tamamlanamadı. MCP bağlantısını kontrol edip yeniden dene. Durum değişmişse önce Durumu yenile'yi seç.","The action could not be completed. Check the MCP connection and retry. If the status has changed, refresh it first."],
    invalid:["Yerel gönderim yanıtı geçersiz; işlem yapılmadı.","The local submission response is invalid; no action was taken."],
    destination:["Topluluk","Community"]
  };
  const root=document.getElementById("submissions-root");
  const requested=new URLSearchParams(window.location.search).get("lang");
  let language=["en","tr"].includes(requested)?requested:(globalThis.UfukUI?.language||"en");
  const PAGE_SIZE=25;
  let items=[],csrf=null,busy=false,ready=false,errorMessage="",destination="https://devtrellis.org",offset=0,total=0;
  const text=key=>words[key][language==="tr"?0:1];
  const validId=value=>typeof value==="string"&&/^[a-f0-9]{32}$/.test(value);
  const statuses=new Set(["pending","published","rejected","withdrawn","hidden"]);
  const make=(tag,value)=>{const node=document.createElement(tag);if(value!==undefined)node.textContent=String(value);return node;};
  const action=(label,handler)=>{const button=make("button",label);button.type="button";button.disabled=busy;button.addEventListener("click",handler);return button;};
  function record(value){
    if(!value||!validId(value.local_id)||typeof value.title!=="string")throw new Error(text("invalid"));
    const submission=value.submission;
    if(submission!==null&&(!submission||!validId(submission.id)||!statuses.has(submission.status)||!Number.isSafeInteger(submission.version)||submission.version<1))throw new Error(text("invalid"));
    return value;
  }
  function publicURL(id){return destination+"/?card="+id;}
  function render(){
    document.documentElement.lang=language;root.replaceChildren();
    const header=make("header"),select=make("select");select.id="submissions-language";select.disabled=busy;
    for(const value of ["en","tr"]){const option=make("option",value.toUpperCase());option.value=value;select.append(option);}select.value=language;
    select.addEventListener("change",()=>{language=select.value==="tr"?"tr":"en";render();});
    const label=make("label",text("language"));label.append(select);header.append(make("strong","DevTrellis"),label);
    root.append(header,make("h1",text("title")),make("p",text("intro")),make("p",text("lifetime")),make("p",text("destination")+": "+destination));
    const reload=action(text("reload"),()=>load(offset));reload.id="submissions-reload";root.append(reload);
    if(errorMessage){const error=make("p",errorMessage);error.className="error";error.setAttribute("role","alert");root.append(error);}
    if(!ready){if(busy)root.append(make("p",text("loading")));return;}
    if(total>PAGE_SIZE){
      const pages=make("nav");pages.className="actions";pages.setAttribute("aria-label",text("pages"));
      const previous=action(text("previous"),()=>load(Math.max(0,offset-PAGE_SIZE)));previous.id="submissions-previous";previous.disabled=busy||offset===0;
      const next=action(text("next"),()=>load(offset+PAGE_SIZE));next.id="submissions-next";next.disabled=busy||offset+PAGE_SIZE>=total;
      const range=make("p",`${text("range")}: ${offset+1}–${offset+items.length} / ${total}`);range.id="submissions-range";range.setAttribute("role","status");
      pages.append(previous,range,next);root.append(pages);
    }
    if(!items.length)root.append(make("p",text("empty")));
    for(const item of items){
      const article=make("section");article.id=item.local_id;article.className="checks";
      article.append(make("h2",item.title));
      const submission=item.submission;
      if(!submission){
        article.append(make("p",text("missing")));
        const refresh=action(text("refresh"),()=>mutate(item,"refresh"));refresh.id="refresh-"+item.local_id;article.append(refresh);
        root.append(article);continue;
      }
      const status=make("p",text(submission.status));status.className="notice";status.setAttribute("role","status");article.append(status);
      if(submission.updated_at)article.append(make("p",text("updated")+": "+submission.updated_at));
      if(submission.moderation_note)article.append(make("p",text("note")+": "+submission.moderation_note));
      const actions=make("div");actions.className="actions";
      const refresh=action(text("refresh"),()=>mutate(item,"refresh"));refresh.id="refresh-"+item.local_id;actions.append(refresh);
      if(submission.status==="published"){
        const link=make("a",text("open"));link.href=publicURL(submission.id);link.target="_blank";link.rel="noopener noreferrer";actions.append(link);
      }
      if(submission.status!=="withdrawn"){
        const withdraw=action(text("withdraw"),()=>mutate(item,"withdraw"));withdraw.id="withdraw-"+item.local_id;actions.append(withdraw);
      }
      article.append(actions);root.append(article);
    }
  }
  async function api(path,payload){
    const options={credentials:"same-origin",headers:{Accept:"application/json"}};
    if(payload!==undefined){options.method="POST";options.headers["X-Radar-CSRF"]=csrf;options.headers["Content-Type"]="application/json";options.body=JSON.stringify(payload);}
    const response=await fetch("/api/platform/community/"+path,options);
    const value=await response.json();if(!response.ok)throw new Error(text("failed"));return value;
  }
  async function load(targetOffset=offset){
    if(busy)return;busy=true;errorMessage="";render();
    try{
      const session=await api("session");
      if(typeof session.csrf_token!=="string"||!session.csrf_token||session.destination!=="https://devtrellis.org")throw new Error(text("invalid"));
      csrf=session.csrf_token;destination=session.destination;
      let result=await api(`submissions?offset=${targetOffset}&limit=${PAGE_SIZE}`);
      const validPage=value=>Array.isArray(value.items)&&value.items.length<=PAGE_SIZE&&value.destination===destination&&Number.isSafeInteger(value.total)&&value.total>=0&&value.limit===PAGE_SIZE&&value.offset===targetOffset&&value.items.length<=value.total;
      if(!validPage(result))throw new Error(text("invalid"));
      if(targetOffset>0&&targetOffset>=result.total){targetOffset=Math.max(0,Math.floor((result.total-1)/PAGE_SIZE)*PAGE_SIZE);result=await api(`submissions?offset=${targetOffset}&limit=${PAGE_SIZE}`);if(!validPage(result))throw new Error(text("invalid"));}
      items=result.items.map(record);offset=targetOffset;total=result.total;ready=true;
    }catch(error){errorMessage=error.message||text("failed");}
    finally{busy=false;render();}
  }
  async function mutate(item,operation){
    if(busy||!csrf||!items.includes(item)||(operation==="withdraw"&&!item.submission))return;
    if(operation==="withdraw"&&!window.confirm(text("confirm")))return;
    busy=true;errorMessage="";render();
    try{
      const value=await api("submissions/"+item.local_id+"/"+operation,operation==="withdraw"?{expected_version:item.submission.version}:{});
      if(value.local_id!==item.local_id||!value.submission||(item.submission&&value.submission.id!==item.submission.id))throw new Error(text("invalid"));
      record({...item,submission:value.submission});item.submission=value.submission;
    }catch(error){errorMessage=error.message||text("failed");}
    finally{busy=false;render();}
  }
  load();
})();
