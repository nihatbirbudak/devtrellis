// Katkı paketini metin olarak gösterir; paylaşmayı iki açık kullanıcı onayına bağlar.
(() => {
  "use strict";
  const words = {
    title:["Katkıyı göndermeden önce incele","Review before contributing"],language:["Dil","Language"],
    waiting:["Önizleme bekleniyor…","Waiting for a preview…"],
    intro:["Bu ekran yalnız seçilen kopyayı gösterir. Gönderme kararı sana aittir; asistanın hazırladığı taslak onayın yerine geçmez.","This screen shows only the selected copy. You decide whether to send it; an assistant's draft is not your approval."],
    privacyNote:["Otomatik temizlik her kişisel veya özel ayrıntıyı bulamayabilir. Başlığı, metni, bağlantıları ve tüm paketi kontrol et. Özel bilgi varsa paylaşmadan yerelde tut ve yeni bir kamuya açık kopya hazırla.","Automatic redaction may miss personal or private details. Check the title, text, links and full packet. If private information remains, keep it local and prepare a new public copy."],
    nvidiaNotice:["DevTrellis, NVIDIA API deneme erişimiyle üretilen çıktıları toplulukta yayımlamaz. Deneme çıktısını yerelde tut; kişisel araştırmanı yerelde sürdürebilirsin. NVIDIA sağlayıcı adı, erişimin deneme türünde olduğunu tek başına göstermez. Farklı bir NVIDIA erişimin varsa paylaşım koşullarını ayrıca kontrol et.","DevTrellis does not publish output generated through NVIDIA API trial access to the community. Keep trial output local; you can continue personal research locally. The NVIDIA provider name alone does not establish the access type. If you have a different form of NVIDIA access, check its sharing terms separately."],
    privacy:["Paylaşılacak kopyayı kontrol ettim: yalnız kamuya açık araştırma içeriyor; özel veya kişisel bilgi içermiyor.","I reviewed the copy: it contains only public research and no private or personal information."],
    consent:["Bu içeriği paylaşmaya hakkım var; gösterilen metin izniyle gönderilmesini onaylıyorum.","I have the right to share this content and approve sending it under the content license shown."],
    permissionNote:["İzin yalnız bu metin içindir; bağlı kaynakların veya kodun izinlerini değiştirmez. Gönderim insan incelemesine gider.","Permission covers this text only; it does not change linked source or code licenses. Submissions go for human review."],
    send:["İncelemeye gönder","Send for review"],keep:["Yerelde tut","Keep local"],
    destination:["Gönderim adresi","Destination"],author:["Görünen yazar","Displayed author"],license:["Metin izni","Content license"],
    model:["Kaydedilen model","Recorded model"],generated:["Üretim tarihi","Generation date"],updated:["Kayıt güncellemesi","Record updated"],
    missingModel:["Model kaydedilmemiş","Model not recorded"],missingDate:["Üretim tarihi kaydedilmemiş","Generation date not recorded"],
    unknown:["Bilinmiyor","Unknown"],sources:["Kaynaklar ve bağlantılar","Sources and links"],full:["Gönderilecek paketin tamamı","Complete packet to be sent"],
    modelNote:["Model bilgisi doğruluk kanıtı değildir. Tarihi, kaynakları ve raporun sınırlarını değerlendir.","Model details do not prove accuracy. Assess the dates, sources and report limitations."],
    lineage:["Araştırmanın gelişimi","Research development"],parents:["Temel alınan araştırmalar","Research this builds on"],changes:["Bu çalışmada eklenenler","What this study adds"],version:["sürüm","version"],
    lineageNote:["Bağlantılar araştırmada kullanılan yayımlanmış sürümü açar. Bu ilişki ve eklenen bulgular da paylaşılacak kopyaya dahildir.","Links open the published version used by this research. These relationships and added findings are included in the shared copy."],
    fallback:["Yerel inceleme sayfasını aç","Open the local review page"],
    noToken:["Bu istemcide güvenli onay hazır değil. Paylaşmak için yerel inceleme sayfasını aç.","Secure approval is unavailable in this client. Open the local review page to share."],
    invalid:["Önizleme alınamadı veya süresi doldu. Yeni bir önizleme hazırla.","The preview is unavailable or expired. Prepare a new preview."],
    pending:["İnceleme bekliyor","Pending review"],published:["Yayımlandı","Published"],rejected:["Reddedildi","Rejected"],withdrawn:["Geri çekildi","Withdrawn"],hidden:["Yayından kaldırıldı","Removed from publication"],
    kept:["Önizleme iptal edildi. Bu kopya gönderilmedi; yerel araştırman korunur.","The preview was discarded. This copy was not sent; your local research is preserved."],
    result:["Gönderim durumu","Submission status"],
    submissions:["Gönderimlerim · durum ve geri çekme","My submissions · status and withdrawal"],
    failed:["İşlem doğrulanamadı. Önizleme korunuyor; tekrar denemeden önce sonucu kontrol et.","The action could not be confirmed. Your preview is preserved; check the result before retrying."],
    host:["İstemci bağlantısı doğrulanamadı. Araç yanıtındaki yerel inceleme bağlantısını kullan.","The client connection could not be verified. Use the local review link in the tool response."],
    loading:["İnceleme hazırlanıyor…","Preparing the review…"]
  };
  const root=document.getElementById("contribution-root");
  let language=globalThis.UfukUI?.language || (navigator.language?.toLowerCase().startsWith("tr")?"tr":"en"), chosenLanguage=false;
  const text=key=>words[key]?.[language==="tr"?0:1] || key;
  const make=(tag,classes="",value)=>{const element=document.createElement(tag);element.className=classes;if(value!==undefined)element.textContent=String(value);return element;};
  const safeURL=value=>{try{const url=new URL(value);return ["http:","https:"].includes(url.protocol)&&!url.username&&!url.password?url:null;}catch{return null;}};
  const validId=value=>typeof value==="string"&&/^[A-Za-z0-9_-]{16,100}$/.test(value);
  function reviewURL(value,id){const url=safeURL(value);return url&&url.protocol==="http:"&&["127.0.0.1","localhost","[::1]"].includes(url.hostname)&&url.pathname==="/static/contribution-review.html"&&url.searchParams.get("preview")===id&&[...url.searchParams.keys()].every(key=>key==="preview")&&!url.hash?url.href:null;}
  let current=null,busy=false,finished=false,outcome=null,decide=null,openLink=null,approvalToken=null;
  function error(message){const target=document.getElementById("review-error");if(target){target.textContent=message;target.hidden=false;}else{const feedback=make("p","error",message);feedback.id="review-error";feedback.setAttribute("role","alert");root.append(feedback);}}
  function date(value){const parsed=value?new Date(value):null;return !parsed||Number.isNaN(parsed.getTime())?text("unknown"):new Intl.DateTimeFormat(language==="tr"?"tr-TR":"en-US",{dateStyle:"medium",timeStyle:"short"}).format(parsed);}
  function controls(){
    const share=document.getElementById("review-send"),keep=document.getElementById("review-keep"),privacy=document.getElementById("review-public"),consent=document.getElementById("review-license");
    if(!share)return;
    const available=Boolean(approvalToken&&current);
    share.disabled=busy||finished||!available||!privacy.checked||!consent.checked;
    keep.disabled=busy||finished||!available;privacy.disabled=consent.disabled=busy||finished||!available;
    document.getElementById("review-language").disabled=busy;
  }
  function external(label,url){const address=safeURL(url);if(!address)return make("span","",label);const link=make("a","",label);link.href=address.href;link.target="_blank";link.rel="noopener noreferrer";if(openLink)link.addEventListener("click",event=>{event.preventDefault();Promise.resolve(openLink(address.href)).catch(()=>error(text("failed")));});return link;}
  function submissionsLink(){
    const value=reviewURL(current?.review_url,current?.preview_id)||(document.body.dataset.surface==="local"?window.location.href:null);
    const url=safeURL(value);
    if(!url||url.protocol!=="http:"||!["127.0.0.1","localhost","[::1]"].includes(url.hostname))return null;
    url.pathname="/static/contribution-submissions.html";url.search="?lang="+language;url.hash="";
    if(/^[a-f0-9]{32}$/.test(outcome?.localId||""))url.hash=outcome.localId;
    const link=external(text("submissions"),url.href);link.id="review-submissions";return link;
  }
  function render(){
    root.replaceChildren();document.documentElement.lang=language;
    const header=make("header"),brand=make("strong","","DevTrellis"),langLabel=make("label","language",text("language")),select=make("select");select.id="review-language";
    for(const value of ["en","tr"]){const option=make("option","",value.toUpperCase());option.value=value;select.append(option);}select.value=language;
    select.addEventListener("change",()=>{language=select.value==="tr"?"tr":"en";chosenLanguage=true;render();});langLabel.append(select);header.append(brand,langLabel);
    root.append(header,make("h1","",text("title")),make("p","notice",text("intro")),make("p","muted",text("privacyNote")));
    const history=submissionsLink();if(history)root.append(history);
    if(!current){root.append(make("p","",text("waiting")));return;}
    const packet=current.packet,p=packet.provenance||{};
    root.append(make("p","notice",text("destination")+": "+current.destination));
    if(p.provider==="nvidia"){
      const notice=make("p","notice",text("nvidiaNotice"));notice.id="review-nvidia-notice";root.append(notice);
    }
    root.append(make("h2","",packet.title),make("p","",packet.summary),make("div","report",packet.body||""));
    const facts=make("dl","facts");
    for(const [label,value] of [["author",packet.author],["license",packet.license],["model",p.model&&p.model!=="unknown"?p.model:text("missingModel")],["generated",p.generated_at?date(p.generated_at):text("missingDate")],["updated",date(packet.updated_at)]]){
      facts.append(make("dt","",text(label)),make("dd","",value||text("unknown")));
    }
    root.append(facts,make("p","muted",text("modelNote")));
    if(packet.lineage){
      const lineage=make("section");lineage.append(make("h3","",text("lineage")),make("h4","",text("parents")));
      const parents=make("ul","sources"),destination=safeURL(current.destination);
      for(const parent of packet.lineage.parents||[]){
        if(!/^[a-f0-9]{32}$/i.test(parent.research_id||"")||!Number.isSafeInteger(parent.version)||parent.version<1)continue;
        const row=make("li"),address=new URL("/",destination);address.searchParams.set("card",parent.research_id.toLowerCase());address.searchParams.set("version",String(parent.version));
        row.append(external(`${parent.research_id} · ${text("version")} ${parent.version}`,address.href));parents.append(row);
      }
      lineage.append(parents,make("h4","",text("changes")),make("p","report",packet.lineage.change_summary||""),make("p","muted",text("lineageNote")));root.append(lineage);
    }
    const sources=make("ul","sources"),seen=new Set();
    for(const source of [...(packet.sources||[]),{title:packet.url,url:packet.url},{title:p.source_url,url:p.source_url}]){
      const url=safeURL(source.url);if(!url||seen.has(url.href))continue;seen.add(url.href);const row=make("li");row.append(external(source.title||url.href,url.href));sources.append(row);
    }
    root.append(make("h3","",text("sources")),sources);
    const full=make("details");full.append(make("summary","",text("full")),make("pre","",JSON.stringify(packet,null,2)));root.append(full);
    const checks=make("section","checks");
    for(const [id,label] of [["review-public","privacy"],["review-license","consent"]]){
      const input=make("input");input.type="checkbox";input.id=id;input.checked=false;input.addEventListener("change",controls);
      const wrap=make("label","check");wrap.append(input,make("span","",text(label)));checks.append(wrap);
    }
    checks.append(make("p","muted",text("permissionNote")));
    const licenseURL=packet.license==="CC0-1.0"?"https://creativecommons.org/publicdomain/zero/1.0/":"https://creativecommons.org/licenses/by/4.0/";
    checks.append(external(packet.license,licenseURL));root.append(checks);
    const actions=make("div","actions");
    for(const [id,label,decision] of [["review-send","send","share"],["review-keep","keep","keep"]]){
      const control=make("button",decision==="share"?"primary":"",text(label));control.id=id;control.type="button";control.addEventListener("click",()=>submit(decision));actions.append(control);
    }
    root.append(actions);
    const feedback=make("p","error");feedback.id="review-error";feedback.hidden=true;feedback.setAttribute("role","alert");root.append(feedback);
    const status=make("p","notice");status.id="review-status";status.hidden=true;status.setAttribute("role","status");root.append(status);
    if(outcome){status.textContent=outcome.decision==="keep"?text("kept"):text("result")+": "+text(outcome.status);status.hidden=false;}
    if(!approvalToken&&!finished){
      root.append(make("p","notice",text("noToken")));
      const url=reviewURL(current.review_url,current.preview_id);
      if(url){const fallback=make("button","",text("fallback"));fallback.id="review-fallback";fallback.type="button";fallback.addEventListener("click",()=>{if(openLink)Promise.resolve(openLink(url)).catch(()=>error(text("failed")));else window.open(url,"_blank","noopener,noreferrer");});root.append(fallback);}
    }
    controls();
  }
  async function submit(decision){
    if(busy||finished||!current||!approvalToken)return;
    if(decision==="share"&&(!document.getElementById("review-public").checked||!document.getElementById("review-license").checked))return;
    busy=true;controls();document.getElementById("review-error").hidden=true;
    try{
      const result=await decide({preview_id:current.preview_id,approval_token:approvalToken,decision,reviewed_public:decision==="share",consent:decision==="share"});
      const value=result?.structuredContent||result;
      if(result?.isError||!value||(decision==="keep"?value.status!=="kept_local":!value.submission?.id))throw new Error(text("failed"));
      finished=true;approvalToken=null;outcome={decision,localId:value.local_id,status:["pending","published","rejected","withdrawn","hidden"].includes(value.submission?.status)?value.submission.status:"unknown"};
      render();
    }catch(failure){error(globalThis.UfukUI?.t(failure.message)||text("failed"));}
    finally{busy=false;controls();}
  }
  function show(data,token,handlers={}){
    if(busy)return;
    if(!data||!validId(data.preview_id)||!data.packet||data.packet.schema!=="ufuk-share"||!["CC-BY-4.0","CC0-1.0"].includes(data.packet.license)||!safeURL(data.destination)){disable("invalid");return;}
    current=data;approvalToken=typeof token==="string"&&token.length>=16&&token.length<=256?token:null;decide=handlers.decide;openLink=handlers.openLink||null;finished=false;outcome=null;
    if(!chosenLanguage&&["en","tr"].includes(data.packet.provenance?.language))language=data.packet.provenance.language;
    render();
  }
  function disable(reason){approvalToken=null;finished=true;if(current)controls();error(text(reason));}
  window.DevTrellisContributionReview={show,disable,error,reviewURL,validId};
  render();
  if(document.body.dataset.surface!=="local")return;
  (async()=>{
    try{
      const params=new URLSearchParams(window.location.search),values=params.getAll("preview"),id=values[0];
      if(values.length!==1||!validId(id))throw new Error(text("invalid"));
      const sessionResponse=await fetch("/api/platform/community/session",{credentials:"same-origin",headers:{Accept:"application/json"}});
      if(!sessionResponse.ok)throw new Error(text("invalid"));
      const session=await sessionResponse.json();if(typeof session.csrf_token!=="string"||!session.csrf_token)throw new Error(text("invalid"));
      async function api(path,payload){const options={credentials:"same-origin",headers:{Accept:"application/json","X-Radar-CSRF":session.csrf_token}};if(payload){options.method="POST";options.headers["Content-Type"]="application/json";options.body=JSON.stringify(payload);}const response=await fetch("/api/platform/community/"+path,options);const value=await response.json();if(!response.ok)throw new Error(typeof value.detail==="string"?value.detail:text("failed"));return value;}
      const data=await api("review/"+encodeURIComponent(id));if(data.preview_id!==id)throw new Error(text("invalid"));
      show(data,data.approval_token,{decide:args=>api(args.decision==="keep"?"discard":"send",args.decision==="keep"?{preview_id:args.preview_id,approval_token:args.approval_token}:{preview_id:args.preview_id,approval_token:args.approval_token,reviewed_public:true,consent:true})});
    }catch(failure){error(globalThis.UfukUI?.t(failure.message)||text("invalid"));}
  })();
})();
