// Topluluk arayüzünde güvenli metin gösterimini, açık önizlemeyi, oturum dönüşünü ve çakışmalı gönderim tekrarını sınar.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const {safeURL, parsePacket, validatePacket, filterCards, publicCardId} = require("../radar/static/community.js");
const {BRAND, EN, createTranslator, provenanceFacts, loadTopicLabels} = require("../radar/static/ui-text.js");
const source = fs.readFileSync(path.join(__dirname, "../radar/static/community.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../radar/static/community.html"), "utf8");
const copy = value => JSON.parse(JSON.stringify(value));
const turn = () => new Promise(resolve => setImmediate(resolve));
const packet = () => ({schema: "ufuk-share", schema_version: 1, kind: "research", title: "İklim araştırması", summary: "Kaynaklı özet", body: "Araştırma ayrıntıları", url: "", tags: ["İklim"], sources: [{title: "Makale", url: "https://example.org/article"}], author: "Test Author", license: "CC-BY-4.0", created_at: "2026-01-01T00:00:00+00:00", updated_at: "2026-01-02T00:00:00+00:00"});
const topicDefinitions = [
  {id: "software", label_tr: "Yazılım geliştirme", label_en: "Software development"},
  {id: "jarvis", label_tr: "Ev otomasyonu", label_en: "Home automation"},
  {id: "data", label_tr: "Veri ve veritabanları", label_en: "Data and databases"}
];

function harness({authenticated = false, role = "contributor", malicious = false, provenance = null, language = "en", feedItems = [], topicStatus = 200, cardId = "card-one", search = "", publicStatus = 200, kind = "research", fullBody = null} = {}) {
  let document;
  class Node {
    constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = new Map(); this.attributes = {}; this.dataset = {}; this.value = ""; this.hidden = false; this.disabled = false; this.open = false; this._text = ""; this.className = ""; }
    set textContent(text) { this.children = []; this._text = String(text); }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
    set innerHTML(value) { throw new Error(`Unsafe HTML assignment: ${value}`); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = [...children]; this._text = ""; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener(name, listener) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(listener); }
    focus() { document.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; for (const handler of this.listeners.get("close") || []) handler({target:this}); }
    reportValidity() { return true; }
    async dispatch(name, details = {}) {
      if (this.disabled) return;
      const event = {target: this, currentTarget: this, preventDefault() {}, ...details};
      await Promise.all((this.listeners.get(name) || []).map(handler => handler(event)));
      await turn(); await turn();
    }
  }
  const nodes = [];
  document = {activeElement: null, body: new Node("body"), createElement: tag => new Node(tag), getElementById: id => nodes.find(node => node.id === id), querySelectorAll: selector => {
    if (selector === "[data-tab]") return nodes.filter(node => node.dataset.tab);
    if (selector === "[data-close]") return nodes.filter(node => node.dataset.close);
    if (selector === "dialog") return nodes.filter(node => node.tagName === "DIALOG");
    return [];
  }};
  for (const match of html.matchAll(/<(\w+)\b([^>]*)>/g)) {
    const attributes = Object.fromEntries([...match[2].matchAll(/([\w-]+)="([^"]*)"/g)].map(attribute => [attribute[1], attribute[2]]));
    if (!attributes.id && !attributes["data-tab"] && !attributes["data-close"]) continue;
    const item = new Node(match[1]);
    item.id = attributes.id;
    item.dataset = {tab: attributes["data-tab"], close: attributes["data-close"]};
    item.hidden = /\bhidden\b/.test(match[2]);
    item.disabled = /\bdisabled\b/.test(match[2]);
    item.attributes = attributes;
    nodes.push(item);
    document.body.append(item);
  }
  const card = {id: cardId, owner: {username: "tester", display_name: "Test Contributor"}, version: 7, status: "pending", packet: {...packet(), kind}, published_version: 4, moderation_note: "Please clarify the source."};
  if (fullBody !== null) card.packet.body = fullBody;
  if (provenance) { card.packet.schema_version = 2; card.packet.provenance = provenance; }
  if (malicious) { card.packet.title = '<img src=x onerror="alert(1)">'; card.packet.body = "<script>alert(2)</script>"; card.packet.sources[0] = {title: "<svg onload=alert(3)>", url: "javascript:alert(4)"}; }
  const fixture = {authenticated, role, conflict: false, requests: [], card, uuidCount: 0, history: []};
  const session = () => ({authenticated: fixture.authenticated, user: fixture.authenticated ? {id: "user-one", username: "tester", display_name: "Test Contributor", role: fixture.role} : null, csrf_token: fixture.authenticated ? "TEST_AUTH_CSRF" : "TEST_ANON_CSRF"});
  const fetch = async (url, options) => {
    const body = options.body ? JSON.parse(options.body) : null;
    fixture.requests.push({url, options: copy(options), body});
    let response;
    let status = 200;
    if (url === "/api/topics") { response = topicDefinitions; status = topicStatus; }
    else if (url === "/api/community/session") response = session();
    else if (url === "/api/community/login") { fixture.authenticated = true; response = session(); }
    else if (url === "/api/community/logout") { fixture.authenticated = false; response = {ok: true}; }
    else if (url.startsWith("/api/community/feed?")) response = {items: feedItems, total: feedItems.length, offset: 0, limit: 24};
    else if (url === "/api/community/mine" || url === "/api/community/review") response = {items: [card]};
    else if (url.startsWith("/api/community/cards?")) response = {items: [card], total: 1};
    else if (url === `/api/community/cards/${card.id}`) {status=publicStatus;response=status===200 ? {...card, status:"published", moderation_version:11} : {detail:"Not found"};}
    else if (url.startsWith("/api/community/submissions/")) {
      if (fixture.conflict) { status = 409; response = {detail: "version conflict"}; }
      else { response = {card: {...card, version: 8, packet: body.packet || card.packet}}; }
    } else throw new Error(`Unexpected API call: ${url}`);
    return {ok: status === 200, status, json: async () => copy(response)};
  };
  const window = {addEventListener() {}, confirm: () => true, location:new URL(`https://community.example/${search}`), history:{replaceState(_state,_title,address){fixture.history.push(address);window.location=new URL(address,window.location);}}};
  vm.runInNewContext(source, {document, window, fetch, URL, URLSearchParams, TextEncoder, Intl, Date, Set, console, crypto: {randomUUID: () => `TEST-REQUEST-${++fixture.uuidCount}`}, UfukUI: {language, brand: BRAND, t: createTranslator(language), provenanceFacts, loadTopicLabels: () => loadTopicLabels(language, fetch), initialize() {}}});
  const flatten = root => [root, ...root.children.flatMap(flatten)];
  return {fixture, document, window, byId: document.getElementById, ready: async () => { await turn(); await turn(); }, findButton: label => flatten(document.getElementById("community-list")).find(item => item.tagName === "BUTTON" && item.textContent === label), flatten};
}

test("ortak haberlerin konu dili API kaydından gelir; arşiv işareti yerine yayın ve görülme tarihleri kalır", async () => {
  const feedItems = topicDefinitions.map(topic => ({id: topic.id, topic: topic.id, topic_label: topic.label_tr,
    title: "Original source title", excerpt: "Original source description", url: "https://example.org/news", source_name: "Example",
    is_archive: true, published_at: "2026-11-04T10:00:00Z", last_seen_at: "2026-11-04T11:00:00Z"}));
  for (const language of ["en", "tr"]) {
    const app = harness({language, feedItems}); await app.ready();
    const list = app.byId("community-list");
    for (const topic of topicDefinitions) assert.ok(list.textContent.includes(topic[language === "en" ? "label_en" : "label_tr"]));
    assert.match(list.textContent, language === "en" ? /Published:/ : /Yayın:/);
    assert.match(list.textContent, language === "en" ? /Last seen:/ : /Son görüldü:/);
    assert.doesNotMatch(list.textContent, /Archived record|Arşiv kaydı/);
    assert.ok(app.flatten(list).every(node => !node.className.split(" ").includes("archive")));
    assert.ok(list.textContent.includes("Original source title"));
    assert.equal(app.fixture.requests.filter(request => request.url === "/api/topics").length, 1);
    assert.equal(app.fixture.requests.filter(request => request.options.method === "POST").length, 0);
    assert.ok(feedItems.every(item => item.is_archive));
  }
});

test("konu listesi alınamazsa ortak haberler önceki API etiketiyle okunabilir", async () => {
  const app = harness({topicStatus: 503, feedItems: [{topic: "software", topic_label: "Yazılım geliştirme",
    title: "Still available", url: "https://example.org/news", is_archive: true}]});
  await app.ready();
  assert.ok(app.byId("community-list").textContent.includes("Still available"));
  assert.ok(app.byId("community-list").textContent.includes("Yazılım geliştirme"));
  assert.equal(app.byId("community-error").hidden, true);
});

test("paylaşım dosyası biçimi, boyutu ve lisans koşulları doğrulanır", () => {
  assert.deepEqual(parsePacket(JSON.stringify(packet())), packet());
  assert.throws(() => parsePacket('{"schema":"other"}'), /desteklenen/);
  assert.throws(() => parsePacket(JSON.stringify({...packet(), body: "ğ".repeat(17000)})), /32 KB/);
  assert.throws(() => validatePacket({...packet(), sources: []}), /en az bir kaynak/);
  assert.throws(() => validatePacket({...packet(), license: null}), /izni/);
  assert.throws(() => validatePacket({...packet(), sources: [{title: "x", url: "javascript:alert(1)"}]}), /Geçersiz bağlantı/);
  assert.equal(safeURL("https://user:password@example.org"), null);
});

test("arama Türkçe Unicode metni işler ve kullanıcı içeriğini değiştirmez", () => {
  const records = [{packet: packet()}];
  assert.equal(filterCards(records, "İKLİM").length, 1);
  assert.equal(filterCards(records, "I\u0307KLI\u0307M makale").length, 1);
  assert.equal(filterCards(records, "olmayan").length, 0);
  assert.equal(records[0].packet.title, "İklim araştırması");
});

test("v2 köken kaydı güvenli metin olarak görünür; düzeltme özgün modeli koruyup kanıt durumunu düşürür", async () => {
  const provenance = {model: "nvidia/original-model", provider: "nvidia", generated_at: "2026-01-01T12:00:00Z", question: "<script>original question</script>", claim_text: "UFUK <img src=x onerror=alert(1)> original claim", topic: "ai", source_url: "https://example.org/source", evidence_status: "verified", language: "en", modified: false};
  assert.equal(parsePacket(JSON.stringify({...packet(), schema_version: 2, provenance})).provenance.model, provenance.model);
  const app = harness({authenticated: true, provenance}); await app.ready();
  await app.byId("community-tab-mine").dispatch("click"); await app.findButton("Read").dispatch("click");
  assert.match(app.byId("card-content").textContent, /nvidia\/original-model/);
  assert.match(app.byId("card-content").textContent, /claims by the author/);
  assert.ok(app.byId("card-content").textContent.includes(provenance.question));
  assert.ok(app.byId("card-content").textContent.includes(provenance.claim_text));
  assert.match(app.byId("card-content").textContent, /Claim under review/);
  assert.ok(app.flatten(app.byId("card-content")).every(item => !["SCRIPT", "IMG"].includes(item.tagName)));
  app.byId("card-dialog").close(); await app.findButton("Revise").dispatch("click");
  app.byId("compose-body").value = "Changed report text"; await app.byId("compose-form").dispatch("submit");
  const preview = JSON.parse(app.byId("compose-json").textContent);
  assert.equal(preview.schema_version, 2); assert.equal(preview.provenance.model, provenance.model);
  assert.equal(preview.provenance.generated_at, provenance.generated_at); assert.equal(preview.provenance.modified, true); assert.equal(preview.provenance.evidence_status, "unverified");
  assert.equal(preview.provenance.claim_text, provenance.claim_text);
  assert.match(app.byId("compose-preview-content").textContent, /evidence has not been checked again/);
  assert.equal(app.fixture.requests.filter(item => item.options.method === "POST").length, 0);
});

test("ortak çeviri marka ve yer tutucuları çevirir, interpolasyondaki kullanıcı metnini çevirmeye kalkmaz", () => {
  const english = createTranslator("en");
  assert.equal(english("UFUK — Topluluk"), `${BRAND} — Community`);
  assert.equal(english("Güncellendi: {date}", {date: "2026"}), "Updated: 2026");
  assert.equal(english("İnceleme notu: {note}", {note: "UFUK <img>"}), "Review note: UFUK <img>");
  assert.equal(createTranslator("tr")("Giriş yap"), "Giriş yap");
  assert.equal(english("Giriş yap"), "Sign in");
  assert.ok(Object.keys(EN).length > 200);
});

test("anonim okuma XSS metnini HTML'e çevirmeden gösterir ve güvensiz kaynak bağlantısı açmaz", async () => {
  const app = harness({malicious: true});
  await app.ready();
  await app.byId("community-tab-research").dispatch("click");
  assert.match(app.byId("community-list").textContent, /<img src=x onerror/);
  await app.findButton("Read research").dispatch("click");
  assert.equal(app.byId("card-dialog").open, true);
  assert.match(app.byId("card-content").textContent, /<script>alert\(2\)<\/script>/);
  assert.ok(app.flatten(app.byId("card-content")).every(item => item.tagName !== "SCRIPT" && item.tagName !== "IMG" && !String(item.href || "").startsWith("javascript:")));
  assert.equal(app.fixture.requests.filter(item => item.options.method === "POST").length, 0);
});

test("araştırmaya başla hesap istemez ve yerel uygulamaya kendiliğinden bağlanmaz", async () => {
  for (const language of ["en", "tr"]) {
    const app = harness({language}); await app.ready();
    const before = app.fixture.requests.length;
    await app.byId("community-start").dispatch("click");
    assert.equal(app.fixture.requests.length, before);
    assert.equal(app.byId("login-dialog").open, false);
    const links = app.flatten(app.document.body).filter(node => node.tagName === "A");
    assert.ok(links.some(node => node.href === "http://127.0.0.1:8781/"));
    assert.match(app.document.body.textContent, language === "en" ? /No account is required/ : /Hesap gerekmez/);
  }
});

test("kamu araştırmasının yerel geçişi yalnız niyet bağlantısı oluşturur; yeni çağrı yapmaz", async () => {
  const app = harness(); await app.ready();
  app.fixture.card.owner = {username:null, display_name:"Hesapsız katkı", verified:false};
  app.fixture.card.submission_type = "anonymous";
  await app.byId("community-tab-research").dispatch("click");
  assert.match(app.byId("community-list").textContent,/Author as stated: Test Author/);
  assert.match(app.byId("community-list").textContent,/Author identity unverified/);
  const before=app.fixture.requests.length;
  await app.findButton("Research with my model").dispatch("click");
  assert.equal(app.fixture.requests.length,before);
  assert.ok(app.flatten(app.document.body).some(node=>node.href==="http://127.0.0.1:8781/?community_card=card-one"));
  assert.match(app.document.body.textContent,/no model starts automatically/);
});

test("giriş eski CSRF ile yapılır; sonraki açık gönderim yeni CSRF ve aynı tekrar anahtarını kullanır", async () => {
  const app = harness();
  await app.ready();
  app.byId("login-username").value = "tester";
  app.byId("login-password").value = "TEST_ONLY_PASSWORD";
  await app.byId("login-form").dispatch("submit");
  const login = app.fixture.requests.find(item => item.url.endsWith("/login"));
  assert.equal(login.options.headers["X-Ufuk-CSRF"], "TEST_ANON_CSRF");
  assert.equal(app.byId("login-password").value, "");
  await app.byId("community-tab-mine").dispatch("click");
  assert.match(app.byId("community-list").textContent, /Review note: Please clarify the source\./);
  await app.findButton("Revise").dispatch("click");
  app.byId("compose-body").value = "My preserved revision";
  await app.byId("compose-form").dispatch("submit");
  assert.equal(app.byId("compose-preview").hidden, false);
  assert.equal(app.fixture.requests.filter(item => item.url.includes("/submissions/")).length, 0);
  app.fixture.conflict = true;
  await app.byId("compose-submit").dispatch("click");
  assert.equal(app.byId("compose-dialog").open, true);
  assert.equal(app.byId("compose-body").value, "My preserved revision");
  assert.match(app.byId("compose-error").textContent, /draft is preserved/);
  app.fixture.conflict = false;
  await app.byId("compose-submit").dispatch("click");
  const writes = app.fixture.requests.filter(item => item.url.includes("/submissions/"));
  assert.equal(writes.length, 2);
  assert.equal(writes[0].options.headers["X-Ufuk-CSRF"], "TEST_AUTH_CSRF");
  assert.equal(writes[0].body.expected_version, 7);
  assert.equal(writes[0].body.idempotency_key, writes[1].body.idempotency_key);
  assert.equal(app.byId("compose-dialog").open, false);
});

test("yayından kaldırma yayımlı revizyonu değil moderasyon sürümünü kullanır ve gerekçe ister", async () => {
  const app = harness({authenticated: true, role: "moderator"});
  await app.ready();
  await app.byId("community-tab-research").dispatch("click");
  await app.findButton("Remove from publication").dispatch("click");
  await app.byId("review-hide").dispatch("click");
  assert.equal(app.fixture.requests.filter(item => item.url.endsWith("/hide")).length, 0);
  app.byId("review-note").value = "Source has been withdrawn.";
  await app.byId("review-hide").dispatch("click");
  const write = app.fixture.requests.find(item => item.url.endsWith("/hide"));
  assert.equal(write.body.expected_version, 11);
  assert.equal(write.body.note, "Source has been withdrawn.");
});

test("iki dilde başlık ve birincil okuma tam metni açar; okumak model, giriş veya yerel uygulama istemez", async () => {
  const id="25335c49583f4d4096b2a5f1026bfce0";
  const fullBody="Original report\n"+"Evidence detail. ".repeat(300)+"\nREADER_COMPLETE_END";
  for(const language of ["en","tr"]){
    const app=harness({cardId:id,language,fullBody,provenance:{model:"synthetic/test-model",generated_at:"2026-01-02T12:00:00Z",question:"Original question",evidence_status:"unverified"}});await app.ready();
    await app.byId("community-tab-research").dispatch("click");
    const heading=app.flatten(app.byId("community-list")).find(item=>item.tagName==="A"&&item.className==="card-title-link");
    assert.equal(heading.href,`/?card=${id}`);
    const read=app.findButton(language==="en"?"Read research":"Araştırmayı oku");assert.match(read.className,/read-primary/);
    const before=app.fixture.requests.length;await heading.dispatch("click");
    assert.deepEqual(app.fixture.requests.slice(before).map(row=>row.url),[`/api/community/cards/${id}`]);
    assert.equal(app.byId("card-dialog").open,true);assert.equal(app.byId("login-dialog").open,false);
    const body=app.flatten(app.byId("card-content")).find(item=>item.className==="detail-body");assert.equal(body.textContent,fullBody);
    assert.match(app.byId("card-content").textContent,/synthetic\/test-model/);
    assert.match(app.byId("card-content").textContent,language==="en"?/Full research text/:/Tam araştırma metni/);
    assert.match(app.byId("card-content").textContent,language==="en"?/requires no account, local app or AI model/:/hesap, yerel uygulama veya yapay zekâ modeli gerekmez/);
    assert.equal(app.window.location.search,`?card=${id}`);
    assert.ok(app.fixture.requests.every(row=>!row.options.method||row.options.method==="GET"));
    assert.ok(app.fixture.requests.every(row=>!row.url.includes("/login")&&!row.url.includes("127.0.0.1")&&!row.url.includes("research-requests")));
    app.byId("card-dialog").close();assert.equal(app.window.location.search,"");
    await read.dispatch("click");assert.equal(app.byId("card-dialog").open,true);
  }
});

test("proje kartında ayrıntı okuma birincildir; GitHub ve özgün kaynaklar güvenli doğrudan bağlantıdır", async()=>{
  const app=harness({kind:"project"});await app.ready();
  app.fixture.card.packet.url="https://github.com/example/project";
  app.fixture.card.packet.provenance={source_url:"https://example.org/original",model:"unknown",generated_at:null};
  app.fixture.card.packet.sources=[{title:"Original",url:"https://example.org/original"},{title:"Unsafe",url:"javascript:alert(1)"},{title:"Credential",url:"https://name:secret@example.org"},{title:"Article",url:"https://example.org/article"}];
  await app.byId("community-tab-project").dispatch("click");
  const links=app.flatten(app.byId("community-list")).filter(item=>item.tagName==="A");
  assert.ok(links.some(link=>link.href==="https://github.com/example/project"&&link.textContent.startsWith("GitHub")));
  assert.ok(links.some(link=>link.href==="https://example.org/original"));
  for(const link of links){assert.equal(link.target,"_blank");assert.equal(link.rel,"noopener noreferrer");assert.ok(safeURL(link.href));}
  const before=app.fixture.requests.length;await app.findButton("Read details").dispatch("click");
  assert.deepEqual(app.fixture.requests.slice(before).map(row=>row.url),["/api/community/cards/card-one"]);
  assert.match(app.byId("card-content").textContent,/Project details/);
  const detailLinks=app.flatten(app.byId("card-content")).filter(item=>item.tagName==="A");
  assert.equal(detailLinks.filter(link=>link.href==="https://example.org/original").length,1);
  assert.ok(detailLinks.every(link=>!String(link.href).startsWith("javascript:")&&!String(link.href).includes("secret")));
});

test("kalıcı bağlantı sadece 32 haneli tek kayıt kimliğini kabul eder",()=>{
  const id="25335c49583f4d4096b2a5f1026bfce0";
  assert.equal(publicCardId(`?card=${id}`),id);assert.equal(publicCardId(`?research=${id.toUpperCase()}`),id);
  for(const input of ["", "?card=", "?card=../private", "?card=%3Cscript%3E", "?card=https://other.test", `?card=${id}&card=${id}`, `?card=${id}&research=${id}`,`?card=${id}0`]) assert.equal(publicCardId(input),null,input);
});

test("kalıcı bağlantı yenilendiğinde yalnız yayımlı kayıt endpointi açılır ve eksik meta bilinmiyor kalır",async()=>{
  const id="25335c49583f4d4096b2a5f1026bfce0";
  for(const key of ["card","research"]){
    const app=harness({cardId:id,search:`?${key}=${id}`,fullBody:"PERMALINK_FULL_BODY"});await app.ready();
    assert.equal(app.byId("card-dialog").open,true);assert.match(app.byId("card-content").textContent,/PERMALINK_FULL_BODY/);
    assert.match(app.byId("card-content").textContent,/Model not recorded/);assert.match(app.byId("card-content").textContent,/Generation date not recorded/);
    assert.equal(app.fixture.requests.filter(row=>row.url===`/api/community/cards/${id}`).length,1);
    assert.equal(app.fixture.requests.some(row=>row.url.includes("/mine")||row.url.includes("/review")||row.options.method==="POST"),false);
  }
});

test("yayınlanmamış permalink özel kayda düşmez; geçersiz kimlik uzak kayıt isteği oluşturmaz",async()=>{
  const id="52b6ec51d4aa414d8cc6e05f2b58eb1c";
  const missing=harness({authenticated:true,role:"moderator",cardId:id,search:`?card=${id}`,publicStatus:404,fullBody:"PRIVATE_PENDING_TEST_MARKER"});await missing.ready();
  assert.equal(missing.byId("card-dialog").open,false);assert.match(missing.byId("community-error").textContent,/not found or is no longer published/);
  assert.doesNotMatch(missing.document.body.textContent,/PRIVATE_PENDING_TEST_MARKER/);
  assert.equal(missing.fixture.requests.some(row=>row.url.includes("/mine")||row.url.includes("/review")),false);
  const invalid=harness({search:"?card=../private"});await invalid.ready();
  assert.equal(invalid.fixture.requests.some(row=>row.url.startsWith("/api/community/cards/")),false);
});

test("özel katkı görünümü kalıcı bağlantı üretmez ve mevcut taslağı yalnız kendi alanında okur",async()=>{
  const id="52b6ec51d4aa414d8cc6e05f2b58eb1c";
  const app=harness({authenticated:true,cardId:id});await app.ready();await app.byId("community-tab-mine").dispatch("click");
  assert.equal(app.flatten(app.byId("community-list")).some(item=>String(item.href).includes("?card=")),false);
  const before=app.fixture.requests.length;await app.findButton("Read").dispatch("click");
  assert.equal(app.fixture.requests.length,before);assert.equal(app.fixture.history.length,0);
  assert.equal(app.flatten(app.byId("card-content")).some(item=>String(item.href).includes("?card=")),false);
});
