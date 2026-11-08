// Araştırma raporunda karar, taslak, gecikmiş yanıt ve güvenli kaynak gösterimini doğrular.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const translator = require("./research-i18n.cjs");
const source = fs.readFileSync(path.join(__dirname, "..", "radar", "static", "workflow.js"), "utf8");
const discoverySource = fs.readFileSync(path.join(__dirname, "..", "radar", "static", "discovery.js"), "utf8");
const app = fs.readFileSync(path.join(__dirname, "..", "radar", "static", "app.js"), "utf8");
const links = app.slice(app.indexOf("  function safeUrl("), app.indexOf("  function dateValue("));
const copy = value => JSON.parse(JSON.stringify(value));
const turn = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
function task(extras = {}) {
  return {id: "t1", item_id: "i1", version: 1, kind: "deep", status: "ready", source_revision: 1, profile_topic: "ai", notification_read: true, stale: false, decision: null,
    created_at: "2026-10-26T07:00:00Z", updated_at: "2026-10-26T08:00:00Z", coverage: {limited: false, attempted_count: 2, read_count: 2},
    result: {evidence_quality: {status: "verified", coverage_complete: true, claims: [], summary_tr: "İddialar kaynaklarla desteklendi."}, title_tr: "Türkçe araştırma raporu", summary_tr: "Kaynak metinlerinden hazırlanan açıklama.", benefit_tr: "Mevcut çalışma düzeniyle uyumlu bir deneme.", fit: "conditional", recommendation: "watch", limitations: ["Bağımsız doğrulama değildir."], requirements: ["İzole deneme ortamı."], integration_steps: ["Önce örnek veride dene."], verification_plan: ["Önceki çıktıyla karşılaştır."], evidence: [{item_id: "i1", quote: "Kaynakta bulunan açık bir cümle."}]},
    sources: [{item_id: "i1", title: "Asıl yayın", url: "https://example.org/source"}], ...extras};
}
// options.asyncClose: gerçek tarayıcı gibi close olayını sonraki görevde gönderir; options.library: topluluk arama kutusu sahtesi.
function harness(rows = [task()], discovery = false, language = null, topics = {ai: "Yapay zekâ", jarvis: "Ev otomasyonu"}, options = {}) {
  let document;
  class Node {
    constructor(tag, className = "", text = "") {
      this.tagName = tag.toUpperCase();
      this.className = className;
      this.children = [];
      this.parentElement = null;
      this.dataset = {};
      this.attributes = {};
      this.listeners = new Map();
      this.value = "";
      this.type = "";
      this.disabled = false;
      this.hidden = false;
      this.open = false;
      this._text = String(text ?? "");
    }
    get classList() { return {add: value => { this.className += ` ${value}`; }}; }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    get textContent() { return this._text + this.children.map(node => node.textContent).join(""); }
    set textContent(value) { this.replaceChildren(); this._text = String(value); }
    get isConnected() { return this === document.body || Boolean(this.parentElement?.isConnected); }
    append(...nodes) {
      for (const node of nodes) {
        if (node.parentElement) node.remove();
        node.parentElement = this;
        this.children.push(node);
      }
    }
    replaceChildren(...nodes) {
      this.children.forEach(node => { if (node.contains(document.activeElement)) document.activeElement = document.body; node.parentElement = null; });
      this.children = [];
      this._text = "";
      this.append(...nodes);
    }
    remove() {
      if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this);
      this.parentElement = null;
    }
    replaceWith(node) {
      if (!this.parentElement) return;
      const parent = this.parentElement;
      const index = parent.children.indexOf(this);
      this.remove();
      node.parentElement = parent;
      parent.children.splice(index, 0, node);
    }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    addEventListener(name, listener) {
      if (!this.listeners.has(name)) this.listeners.set(name, []);
      this.listeners.get(name).push(listener);
    }
    querySelectorAll(selector) {
      const selectors = selector.split(",").map(value => value.trim());
      const matches = node => selectors.some(value => value.startsWith(".") ? node.className.split(" ").includes(value.slice(1)) : value.startsWith("#") ? node.id === value.slice(1) : value === "[data-focus-key]" ? Boolean(node.dataset.focusKey) : value.startsWith('[data-focus-key="') ? node.dataset.focusKey === value.slice(17, -2) : node.tagName.toLowerCase() === value);
      const results = [];
      for (const node of this.children) {
        if (matches(node)) results.push(node);
        results.push(...node.querySelectorAll(selector));
      }
      return results;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    focus() { if (!this.disabled && this.isConnected) document.activeElement = this; }
    showModal() { this.open = true; }
    close() {
      if (options.asyncClose) {
        if (!this.open) return;
        this.open = false;
        setImmediate(() => { for (const listener of this.listeners.get("close") || []) listener(); });
        return;
      }
      this.open = false; for (const listener of this.listeners.get("close") || []) listener();
    }
    reportValidity() {
      return this.querySelectorAll("input,select,textarea").every(node => !node.required || Boolean(node.value));
    }
    async dispatch(name) {
      if (name === "click" && this.disabled) return;
      const event = {preventDefault() {}, target: this, currentTarget: this};
      await Promise.all((this.listeners.get(name) || []).map(listener => listener(event)));
      await turn();
    }
  }
  const documentListeners = new Map();
  const windowListeners = new Map();
  document = {body: new Node("body"), activeElement: null, visibilityState: "visible",
    getElementById(id) { return this.body.querySelector(`#${id}`); },
    querySelectorAll(selector) { return this.body.querySelectorAll(selector); },
    querySelector(selector) { return this.body.querySelector(selector); },
    addEventListener(name, listener) { documentListeners.set(name, listener); }};
  document.activeElement = document.body;
  const content = new Node("div"); content.id = "content"; document.body.append(content);
  const heading = new Node("h2"); heading.id = "view-title"; document.body.append(heading);
  const state = {view: "selection", search: "", topic: "all", pending: new Set(), data: {
    csrf_token: "test", items: [{id: "i1", title: "Original title", revision: 1, topic: "ai"}],
    workflow: {tasks: rows, settings: {enabled: true, daily_hour: 8, screen_limit: 12, auto_deep_limit: 2, auto_threshold: 90, version: 0, ai_daily_limit: 80, article_daily_limit: 80}, status: {automatic: true, enabled: true}, profiles: []}}};
  const requests = []; const posts = []; const messages = []; const timers = new Map(); let nextTimer = 0;
  const observations = {refreshes: 0, renders: 0, views: []};
  const handlers = {post: async () => { throw new Error("Beklenmedik POST"); }, request: async () => copy(state.data.workflow), refresh: async () => true};
  const element = (tag, className, text) => new Node(tag, className, text);
  const ui = translator(language);
  const sandbox = vm.createContext({t: ui.t, window: {UfukUI: language ? ui : undefined, DevTrellisLibraryFactory: options.library, addEventListener(name, listener) { windowListeners.set(name, listener); }}, document, element, icon: () => element("svg"), URL,
    setTimeout(callback, delay) { const value = ++nextTimer; timers.set(value, {callback, delay}); return value; }, clearTimeout(value) { timers.delete(value); }});
  vm.runInContext(links, sandbox);
  vm.runInContext(discovery ? discoverySource : source, sandbox);
  let api;
  api = (discovery ? sandbox.window.UFUKDiscoveryFactory : sandbox.window.UFUKWorkflowFactory)({element, state, topics, sourceLink: sandbox.sourceLink, formatDate: value => value || "Bilinmiyor",
    post: async (url, payload) => { posts.push({url, payload: copy(payload)}); return handlers.post(url, payload); },
    request: async url => { requests.push(url); return handlers.request(url); },
    refreshAfterMutation: async () => { observations.refreshes++; return handlers.refresh(); },
    toast: (message, error) => messages.push({message, error}), render() { observations.renders++; api?.sync(); },
    changeView(view) { state.view = view; observations.views.push(view); }, findItem: id => state.data.items.find(item => item.id === id)});
  function findButton(label, scope = document.body) { const node = scope.querySelectorAll("button").find(node => node.textContent === label); assert.ok(node, `Düğme bulunmalı: ${label}`); return node; }
  function control(label) { const caption = document.body.querySelectorAll("label").find(node => node.textContent === label); assert.ok(caption, label); return document.getElementById(caption.htmlFor); }
  return {api, state, content, document, handlers, requests, posts, timers, messages, observations, element, findButton, control, documentListeners, windowListeners, window: sandbox.window,
    dialog: () => document.body.querySelector("dialog"), submit: () => document.body.querySelector("form").dispatch("submit")};
}

test("İngilizce rapor arayüzü içeriği ve karar enum değerini korur", async () => {
  const row = task(); row.result.title_tr = "Araştırma UFUK <script>local</script>";
  const h = harness([row], false, "en");
  h.api.open(row.id);
  assert.match(h.dialog().textContent, /Brief summary/);
  assert.match(h.dialog().textContent, /How would you like to continue/);
  assert.ok(h.dialog().textContent.includes(row.result.title_tr));
  assert.ok(h.dialog().textContent.includes(row.result.summary_tr));
  assert.ok(h.dialog().textContent.includes(row.result.evidence[0].quote));
  assert.equal(h.dialog().querySelectorAll("script").length, 0);
  h.handlers.post = async (_url, payload) => ({...row, decision: payload.decision, version: 2});
  await h.findButton("Watch").dispatch("click");
  assert.equal(h.posts[0].payload.decision, "watch");
  assert.equal(h.posts[0].payload.expected_version, 1);
});

test("İngilizce keşif ortak sekiz konuyu kullanır ve kullanıcı sorusunu çevirmeden gönderir", async () => {
  const topics = Object.fromEntries(["ai", "web", "security", "data", "devtools", "robotics", "agriculture", "science"].map(id => [id, id.toUpperCase()]));
  const h = harness([], true, "en", topics); h.api.open();
  const question = h.control("What do you want to learn?");
  question.value = "Araştırma UFUK kullanıcının özgün sorusu"; await question.dispatch("input");
  const topic = h.control("Topic");
  assert.equal(topic.children.length, 8);
  assert.equal(topic.children.find(node => node.value === "security").textContent, "SECURITY");
  topic.value = "security"; await topic.dispatch("input");
  assert.match(question.placeholder, /Which methods could improve research/);
  h.handlers.post = async () => ({id: "new"}); await h.submit();
  assert.equal(h.posts[0].payload.question, question.value);
  assert.equal(h.posts[0].payload.topic, "security");
  assert.deepEqual(h.posts[0].payload.budget, {max_candidates: 10, max_deep_tasks: 1});
});

test("İngilizce günlük ayarlar seçili Codex sağlayıcısını açıklar", () => {
  const h = harness([], false, "en"); h.state.data.ai = {deep_provider: "codex"};
  const panel = h.api.settingsPanel();
  assert.match(panel.textContent, /Codex for a suitability review/);
  assert.doesNotMatch(panel.textContent, /Claude/);
  assert.match(panel.textContent, /Around 08:00 Türkiye time/);
});

test("sosyal URL ve transkript taslağı açık gönderimle gider; hata aynı isteği korur", async () => {
  const h = harness([], true, "en"); h.state.data.ai = {deep_provider: "nvidia", deep_model: "nvidia/recorded-model"}; h.api.open();
  assert.match(h.dialog().textContent, /Research provider: NVIDIA/);
  assert.match(h.dialog().textContent, /Video and audio are not watched/);
  for (const [label, value] of [["What do you want to learn?", "Is this claim supported?"], ["Social post or news URL (optional)", "https://example.org/post"], ["Claim to investigate (optional)", "<img src=x> Claim UFUK"], ["Context or transcript (optional)", "Line one\nLine two <script>keep as text</script>"]]) {
    const field = h.control(label); field.value = value; await field.dispatch("input");
  }
  assert.equal(h.posts.length, 0); assert.equal(h.requests.length, 0);
  h.handlers.post = async () => { throw new Error("Network failed"); }; await h.submit();
  assert.equal(h.posts[0].payload.source_url, "https://example.org/post");
  assert.equal(h.posts[0].payload.claim_text, "<img src=x> Claim UFUK");
  assert.equal(h.posts[0].payload.context_text, "Line one\nLine two <script>keep as text</script>");
  assert.equal(h.control("Context or transcript (optional)").value, h.posts[0].payload.context_text);
  h.handlers.post = async () => ({id: "new-request"}); await h.submit();
  assert.equal(h.posts[1].payload.idempotency_key, h.posts[0].payload.idempotency_key);
});

test("bağlantı erişilemiyorsa medya izlenmiş gibi görünmez; tehlikeli URL gönderilmez", async () => {
  const h = harness([], true, "en"); h.api.open();
  const question = h.control("What do you want to learn?"); question.value = "Evaluate this statement"; await question.dispatch("input");
  const url = h.control("Social post or news URL (optional)"); url.value = "https://user:secret@example.org/post"; await url.dispatch("input");
  await h.submit(); assert.equal(h.posts.length, 0); assert.match(h.dialog().textContent, /without a username or password/); h.dialog().close();
  h.state.data.research_requests = {requests: [{id: "r1", question: "<script>original question</script>", topic: "ai", status: "no_sources", source_url: "https://example.org/post", claim_text: "<img> user claim", link_access: {status: "unavailable", media_processed: false}}]};
  h.api.renderRequests(h.content);
  assert.match(h.content.textContent, /Linked content could not be accessed/);
  assert.match(h.content.textContent, /Video and audio were not processed/);
  assert.match(h.content.textContent, /inaccessible post is not presented as evidence/);
  assert.equal(h.content.querySelectorAll("script,img").length, 0);
});

test("yeni araştırma özgün raporu değiştirmez; paylaşım yalnız taslak POST'u oluşturur", async () => {
  const row = task({model: "nvidia/historical-model", source_url: "https://example.org/original"});
  const h = harness([row], false, "en"); h.state.data.ai = {deep_provider: "codex", deep_model: "codex/current"};
  let prefill; h.window.UFUKDiscovery = {open(_opener, value) {prefill = value;}};
  h.api.open(row.id); assert.match(h.dialog().textContent, /Recorded model: nvidia\/historical-model/); assert.doesNotMatch(h.dialog().textContent, /codex\/current/);
  assert.match(h.dialog().textContent, /does not prove accuracy/);
  await h.findButton("Research again").dispatch("click");
  assert.equal(prefill.source_url, row.source_url); assert.equal(h.posts.length, 0); assert.equal(h.state.data.workflow.tasks[0].result, row.result);
  let destination; h.window.location = {assign(value) {destination = value;}};
  h.api.open(row.id); h.handlers.post = async () => ({entry: {id: "entry-1"}, duplicate: false});
  await h.findButton("Create sharing draft").dispatch("click");
  assert.equal(h.posts[0].url, "/api/platform/research/t1/draft"); assert.deepEqual(h.posts[0].payload, {});
  assert.equal(destination, "/platform?entry=entry-1"); assert.equal(h.state.data.workflow.tasks[0].result, row.result);
});

test("manuel oturum iki dilde günlük zamanlama vaat etmez; etkin zamanlama ve süren iş doğru görünür", async () => {
  for (const language of ["tr", "en"]) {
    const h = harness([], false, language);
    const status = h.state.data.workflow.status;
    Object.assign(status, {automatic: false, enabled: false, message: "Günlük takip hazır.", next_run_at: "2026-10-27T08:00:00+03:00"});
    const manual = language === "en" ? /Automatic daily tracking is off/ : /Otomatik günlük takip bu oturumda kapalı/;
    const next = language === "en" ? /Next (daily check|check)/ : /Sonraki (günlük kontrol|kontrol)/;
    assert.match(h.api.statusStrip().textContent, manual);
    assert.match(h.api.settingsPanel().textContent, manual);
    assert.doesNotMatch(h.api.statusStrip().textContent, next);
    assert.doesNotMatch(h.api.settingsPanel().textContent, next);
    const panel = h.api.settingsPanel(); h.document.body.append(panel);
    await h.findButton(language === "en" ? "Configure daily routine" : "Günlük düzeni ayarla").dispatch("click");
    assert.match(h.dialog().textContent, language === "en" ? /automatic daily tracking stays off/ : /otomatik günlük takip bu oturumda kapalı kalır/);
    h.dialog().close();
    Object.assign(status, {automatic: true, enabled: false}); h.state.data.workflow.settings.enabled = false;
    assert.match(h.api.statusStrip().textContent, language === "en" ? /paused/i : /duraklatıldı/);
    assert.doesNotMatch(h.api.statusStrip().textContent, next);
    Object.assign(status, {automatic: true, enabled: true}); h.state.data.workflow.settings.enabled = true;
    assert.match(h.api.statusStrip().textContent, next);
    assert.match(h.api.settingsPanel().textContent, next);
    Object.assign(status, {automatic: false, enabled: false, running: true, message: "Asıl yayın ve ilgili kaynakların metinleri okunuyor."});
    assert.match(h.api.statusStrip().textContent, language === "en" ? /Reading the original publication/ : /Asıl yayın/);
    assert.doesNotMatch(h.api.statusStrip().textContent, next);
    assert.equal(h.posts.length, 0); assert.equal(h.requests.length, 0);
  }
});

test("konu talebi ağ hatasında taslağı ve aynı istek anahtarını korur", async () => {
  const h = harness([], true); h.api.open();
  const question = h.control("Ne öğrenmek istiyorsun?"); question.value = "Ev otomasyonu için kaynaklı araştırma nasıl gelişir?"; await question.dispatch("input");
  h.handlers.post = async () => { throw Error("Bağlantı koptu"); };
  await h.submit(); assert.match(h.dialog().textContent, /Bağlantı koptu/);
  const first = h.posts[0]; assert.equal(first.url, "/api/research-requests");
  assert.equal(first.payload.question, question.value);
  await h.findButton("Kapat").dispatch("click"); h.api.open();
  assert.equal(h.control("Ne öğrenmek istiyorsun?").value, question.value);
  h.handlers.post = async () => ({id: "saved"}); await h.submit();
  assert.equal(h.posts[1].payload.idempotency_key, first.payload.idempotency_key);
  assert.deepEqual(h.posts[1].payload.budget, {max_candidates: 10, max_deep_tasks: 1});
  assert.equal(h.dialog(), null); assert.deepEqual(h.observations.views, ["research"]);
  h.api.open(); assert.equal(h.control("Ne öğrenmek istiyorsun?").value, "");
});

test("talep gönderilirken ikinci kayıt açılmaz; boş kaynak sonucu hazır rapor gibi görünmez", async () => {
  const h = harness([], true); h.api.open();
  const question = h.control("Ne öğrenmek istiyorsun?"); question.value = "Yeni araştırma yöntemleri"; await question.dispatch("input");
  const pending = deferred(); h.handlers.post = () => pending.promise;
  const first = h.submit(); await turn(); await h.submit();
  assert.equal(h.posts.length, 1); assert.equal(h.findButton("Kapat").disabled, true);
  pending.resolve({id: "r1"}); await first;
  h.state.data.research_requests = {requests: [{id: "r1", question: question.value, topic: "jarvis", status: "no_sources", tasks: []}]};
  h.api.renderRequests(h.content);
  assert.match(h.content.textContent, /Uygun kaynak bulunamadı/);
  assert.equal(h.content.querySelectorAll("button").some(node => /Araştırmayı aç|İsteği iptal et/.test(node.textContent)), false);
});

test("talep iptalinde görünür sürüm gönderilir ve çakışma kullanıcıya gösterilir", async () => {
  const h = harness([], true);
  h.state.data.research_requests = {requests: [{id: "r1", question: "Kaynak sorgusu", topic: "jarvis", status: "discovering", version: 7, tasks: []}]};
  h.api.renderRequests(h.content); h.handlers.post = async () => { throw Error("Talep başka pencerede değişti."); };
  await h.findButton("İsteği iptal et").dispatch("click");
  assert.deepEqual(h.posts[0], {url: "/api/research-requests/r1/cancel", payload: {expected_version: 7}});
  assert.equal(h.messages[0].error, true); assert.match(h.content.textContent, /Kaynaklar taranıyor/);
});

test("Derin araştır yalnız yeni görev API'sini çağırır; kişisel not istemez", async () => {
  const h = harness([]);
  const queued = task({status: "queued", result: null});
  h.handlers.post = async () => queued;
  h.content.append(h.api.researchButton(h.state.data.items[0]));
  await h.findButton("Derin araştır").dispatch("click");
  assert.deepEqual(h.posts, [{url: "/api/workflow/items/i1/research", payload: {}}]);
  assert.equal(h.state.data.workflow.tasks[0].status, "queued");
  assert.equal(h.dialog().open, true);
  assert.equal(h.dialog().querySelectorAll("textarea").length, 0);
  assert.match(h.dialog().textContent, /Sırada/);
});

test("geliştirme kararı GET tamamlanana kadar kilitlidir; sonra devir ve kapatma kullanılabilir", async () => {
  const h = harness(); await h.api.open("t1"); await turn();
  const saved = task({decision: "develop"});
  h.handlers.post = async () => saved;
  const refresh = deferred(); h.handlers.refresh = () => refresh.promise;
  const action = h.findButton("Geliştirmeye al").dispatch("click"); await turn();
  assert.equal(h.findButton("Kapat").disabled, true);
  await h.findButton("İzlemeye al").dispatch("click");
  assert.equal(h.posts.length, 1);
  refresh.resolve(true); await action;
  assert.deepEqual(h.posts[0], {url: "/api/workflow/tasks/t1/decision", payload: {decision: "develop", expected_version: 1}});
  assert.equal(h.findButton("Kapat").disabled, false);
  const handoff = h.dialog().querySelectorAll("a").find(node => node.textContent === "Devir planını indir");
  assert.equal(handoff.href, "/api/workflow/tasks/t1/handoff");
  await h.findButton("Kapat").dispatch("click"); assert.equal(h.dialog(), null);
});

test("sınırlı rapor geliştirilemez; izlenebilir ve yeniden araştırılabilir", async () => {
  const h = harness([task({coverage: {limited: true, attempted_count: 2, read_count: 1}, scope_notes: ["Asıl sayfaya erişilemedi."]})]);
  await h.api.open("t1"); await turn();
  assert.match(h.dialog().textContent, /Asıl makale tam okunamadığı/);
  assert.match(h.dialog().textContent, /Asıl sayfaya erişilemedi/);
  assert.equal(h.findButton("Geliştirmeye al").disabled, true);
  await h.findButton("Geliştirmeye al").dispatch("click"); assert.equal(h.posts.length, 0);
  assert.equal(h.findButton("İzlemeye al").disabled, false);
  assert.ok(h.findButton("Araştırmayı yeniden dene"));
  assert.equal(h.dialog().querySelectorAll("a").some(node => node.href.endsWith("/handoff")), false);
});

test("kanıt denetlenmediyse, çelişiyorsa veya kapsam eksikse geliştirme kapalı kalır", async () => {
  for (const evidence_quality of [undefined, {status: "unverified"}, {status: "partial"}, {status: "contradicted"}, {status: "verified", coverage_complete: false}]) {
    const row = task({decision: "develop"}); row.result.evidence_quality = evidence_quality;
    const h = harness([row]); await h.api.open("t1"); await turn();
    assert.equal(h.findButton("Geliştirmeye al").disabled, true);
    await h.findButton("Geliştirmeye al").dispatch("click"); assert.equal(h.posts.length, 0);
    assert.equal(h.dialog().querySelectorAll("a").some(node => node.href.endsWith("/handoff")), false);
    assert.ok(h.findButton("Araştırmayı yeniden dene"));
    assert.match(h.dialog().textContent, /denetim|denetlen|desteklen|çeliş/i);
  }
});

test("iddia ve alıntı metin olarak görünür; kötü bağlantı çalıştırılmaz", async () => {
  const row = task();
  row.result.evidence_quality.claims = [{id: "c1", text: "<img src=x onerror=alert(1)>", status: "supported", reason_tr: "Kaynakta açıkça yazıyor.", evidence: [{url: "javascript:alert(1)", quote: "<script>run()</script>"}]}];
  const h = harness([row]); await h.api.open("t1"); await turn();
  assert.match(h.dialog().textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match(h.dialog().textContent, /<script>run\(\)<\/script>/);
  assert.equal(h.dialog().querySelectorAll("img,script").length, 0);
  assert.equal(h.dialog().querySelectorAll("a").some(node => node.href.startsWith("javascript:")), false);
});

test("eski kaynak raporunda karar verilmez; önce güncel araştırma gerekir", async () => {
  const h = harness([task({stale: true, decision: "develop"})]);
  await h.api.open("t1"); await turn();
  assert.match(h.dialog().textContent, /Kaynak bu rapordan sonra değişmiş/);
  assert.equal(h.dialog().querySelectorAll("button").some(node => node.textContent === "Geliştirmeye al"), false);
  assert.equal(h.dialog().querySelectorAll("a").some(node => node.href.endsWith("/handoff")), false);
  assert.ok(h.findButton("Araştırmayı yeniden dene"));
});

test("POST öncesindeki GET kararı geri almaz ve yeni durumu okumak beklenir", async () => {
  const h = harness(); const read = deferred();
  h.handlers.request = () => read.promise;
  await h.api.open("t1");
  h.handlers.post = async () => task({decision: "watch"});
  const writing = h.findButton("İzlemeye al").dispatch("click"); await turn();
  assert.equal(h.observations.refreshes, 0);
  read.resolve({tasks: [task()], settings: {}}); await writing;
  assert.equal(h.observations.refreshes, 1);
  assert.equal(h.state.data.workflow.tasks[0].decision, "watch");
  assert.equal(h.findButton("İzlemeye al").getAttribute("aria-pressed"), "true");
});

test("kapatıp yeniden açma eski uçuşta yanıtı bekler ve yeni raporu yeniden alır", async () => {
  const h = harness(); const read = deferred(); let calls = 0;
  h.handlers.request = () => ++calls === 1 ? read.promise : Promise.resolve({...h.state.data.workflow, tasks: [task({stale: true})]});
  await h.api.open("t1");
  await h.findButton("Kapat").dispatch("click");
  const reopening = h.api.open("t1"); await turn();
  assert.equal(calls, 1);
  read.resolve({...h.state.data.workflow, tasks: [task()]}); await reopening; await turn();
  assert.equal(calls, 2);
  assert.match(h.dialog().textContent, /Kaynak bu rapordan sonra değişmiş/);
});

test("gizli sekme ve pagehide ardından geç yanıt kontrol zamanlayıcısını başlatmaz", async () => {
  const h = harness([task({status: "analyzing", result: null})]);
  await h.api.open("t1"); await turn(); assert.equal(h.timers.size, 1);
  h.document.visibilityState = "hidden"; h.documentListeners.get("visibilitychange")();
  assert.equal(h.timers.size, 0);
  const read = deferred(); h.handlers.request = () => read.promise;
  h.document.visibilityState = "visible"; h.documentListeners.get("visibilitychange")(); await turn();
  h.windowListeners.get("pagehide")(); read.resolve(h.state.data.workflow); await turn();
  assert.equal(h.timers.size, 0);
});

test("bekleyen rapor dakikada bir okunur; ilerleme ve kapanış zamanlayıcıyı doğru değiştirir", async () => {
  const h = harness([task({status: "blocked", result: null})]);
  await h.api.open("t1"); await turn();
  assert.equal(h.requests.length, 1); assert.equal(h.timers.size, 1);
  assert.equal([...h.timers.values()][0].delay, 60000);
  h.document.visibilityState = "hidden"; h.documentListeners.get("visibilitychange")();
  assert.equal(h.timers.size, 0);
  h.document.visibilityState = "visible"; h.documentListeners.get("visibilitychange")(); await turn();
  assert.equal(h.requests.length, 2); assert.equal([...h.timers.values()][0].delay, 60000);
  h.handlers.request = async () => ({...h.state.data.workflow, tasks: [task({status: "analyzing", result: null})]});
  const [timerId, timer] = [...h.timers.entries()][0]; h.timers.delete(timerId); timer.callback(); await turn();
  assert.equal(h.requests.length, 3); assert.equal([...h.timers.values()][0].delay, 2500);
  assert.equal(h.posts.length, 0, "Durum okuma yeni model işi veya karar başlatmamalı.");
  await h.findButton("Kapat").dispatch("click"); assert.equal(h.timers.size, 0);
});

test("bekleyen rapor hazır olunca kendiliğinden durum sorgulamayı bırakır", async () => {
  const h = harness([task({status: "blocked", result: null})]);
  await h.api.open("t1"); await turn();
  h.handlers.request = async () => ({...h.state.data.workflow, tasks: [task()]});
  const [timerId, timer] = [...h.timers.entries()][0]; h.timers.delete(timerId); timer.callback(); await turn();
  assert.equal(h.timers.size, 0); assert.match(h.dialog().textContent, /Türkçe araştırma raporu/);
});

test("ayar 409 hatası taslağı ve sürümü korur; çıkış açık seçim ister", async () => {
  const h = harness(); h.content.append(h.api.settingsPanel());
  await h.findButton("Günlük düzeni ayarla").dispatch("click");
  h.control("Günlük ön inceleme sayısı").value = "18";
  h.handlers.post = async () => { throw Object.assign(new Error("Ayar sürümü değişti."), {status: 409}); };
  await h.submit();
  assert.equal(h.posts[0].payload.expected_version, 0);
  assert.equal(h.control("Günlük ön inceleme sayısı").value, "18");
  assert.equal(h.document.activeElement, h.dialog().querySelector(".inline-error"));
  assert.equal(h.findButton("Kapat").disabled, false);
  await h.dialog().dispatch("cancel"); assert.equal(h.dialog().open, true);
  assert.ok(h.findButton("Kaydetmeden çık"));
  h.handlers.request = async () => ({...h.state.data.workflow, settings: {...h.state.data.workflow.settings, version: 3, screen_limit: 9}});
  await h.findButton("Değişikliklerimi bırakıp güncel ayarları al").dispatch("click");
  assert.equal(h.control("Günlük ön inceleme sayısı").value, "9");
  h.handlers.post = async (url, payload) => ({...payload, version: 4});
  await h.submit(); assert.equal(h.posts[1].payload.expected_version, 3);
  assert.equal(h.dialog(), null);
});

test("rapor metni HTML çalıştırmaz; tehlikeli kaynak bağlantısı ve kesin doğruluk vaadi yoktur", async () => {
  const record = task(); record.result.title_tr = '<img src=x onerror="alert(1)">';
  record.result.summary_tr = "<script>çalışmaz</script>"; record.sources[0].url = "javascript:alert(1)";
  const h = harness([record]); await h.api.open("t1"); await turn();
  assert.match(h.dialog().textContent, /<script>/);
  assert.equal(h.dialog().querySelectorAll("script,img").length, 0);
  assert.ok(h.dialog().querySelectorAll("a").every(node => !node.href.startsWith("javascript:")));
  assert.match(h.dialog().textContent, /bağımsız doğrulama değildir/);
});

test("hazır raporu açmak yalnız görüldü işareti yazar ve model başlatmaz", async () => {
  const h = harness([task({notification_read: false})]);
  h.handlers.post = async () => task({notification_read: true});
  assert.equal(h.api.counts().notifications, 1);
  await h.api.open("t1"); await turn();
  assert.deepEqual(h.posts, [{url: "/api/workflow/tasks/t1/seen", payload: {}}]);
  assert.equal(h.api.counts().notifications, 0);
  assert.equal(h.dialog().open, true);
});

test("karar sürümü çakışınca otomatik yazma yapılmaz; güncel rapordan yeniden seçilir", async () => {
  const h = harness(); await h.api.open("t1"); await turn();
  h.handlers.post = async () => { throw Object.assign(new Error("Rapor değişti."), {status: 409}); };
  await h.findButton("Geliştirmeye al").dispatch("click");
  assert.equal(h.posts.length, 1);
  assert.equal(h.posts[0].payload.expected_version, 1);
  assert.match(h.dialog().querySelector(".inline-error").textContent, /Güncel durumu yeniden al/);
  assert.equal(h.state.data.workflow.tasks[0].decision, null);
  h.handlers.request = async () => ({...h.state.data.workflow, tasks: [task({version: 4, decision: "watch"})]});
  await h.findButton("Durumu yenile").dispatch("click");
  assert.equal(h.posts.length, 1, "Güncel durumu almak eski geliştirme kararını tekrar göndermemeli.");
  h.handlers.post = async () => task({version: 5, decision: "develop"});
  await h.findButton("Geliştirmeye al").dispatch("click");
  assert.equal(h.posts[1].payload.expected_version, 4);
});

test("yeniden deneme görülen görev sürümünü gönderir ve hata raporu korur", async () => {
  const h = harness([task({status: "failed", version: 7})]);
  await h.api.open("t1"); await turn();
  h.handlers.post = async () => { throw Object.assign(new Error("Günlük bütçe dolu."), {status: 429}); };
  await h.findButton("Araştırmayı yeniden dene").dispatch("click");
  assert.deepEqual(h.posts[0], {url: "/api/workflow/tasks/t1/retry", payload: {expected_version: 7}});
  assert.match(h.dialog().textContent, /Günlük bütçe dolu/);
  assert.match(h.dialog().textContent, /Kaynak metinlerinden hazırlanan açıklama/);
  assert.equal(h.findButton("Kapat").disabled, false);
});

test("ana seçki yalnız güncel Türkçe raporları alır; ham ve eski İngilizce kayıtlar elenir", () => {
  const start = app.indexOf("  function publicationAge("); const end = app.indexOf("  function displayTitle(", start);
  const items = ["raw", "screen", "old", "summary", "editor", "deep"].map((id, index) => ({id, revision: 2, title: "Original English title", topic: "ai", source_id: String(index), personal_score: 100 - index}));
  items[3].ai_summary = {title_tr: "Türkçe başlık", summary_tr: "Türkçe açıklama"}; items[4].summary_status = "editor_ozeti";
  const tasks = [task({item_id: "screen", kind: "screen", source_revision: 2}), task({item_id: "old", source_revision: 1}), task({item_id: "deep", source_revision: 2})];
  const sandbox = vm.createContext({state: {data: {workflow: {tasks}, personal: {settings: {daily_limit: 8}}}}, dateValue: value => value ? new Date(value) : null, feedback: () => "yok", snoozed: () => false});
  vm.runInContext(app.slice(start, end), sandbox);
  assert.deepEqual(Array.from(sandbox.selectedItems(items), item => item.id), ["screen", "summary", "editor", "deep"]);
});

test("güncel derin rapor aynı revizyondaki eski ön incelemeyi süren iş gibi göstermez", () => {
  const h = harness([task({id: "screen-old", kind: "screen", stale: true}), task({id: "deep-current"})]);
  assert.equal(h.api.counts().research, 0);
  h.api.renderView("research", h.content);
  assert.equal(h.content.querySelectorAll(".workflow-card").length, 0);
  assert.match(h.content.textContent, /Süren bir araştırma yok/);
  h.content.replaceChildren(); h.api.renderView("decisions", h.content);
  assert.equal(h.content.querySelectorAll(".workflow-card").length, 1);
});

test("aynı haberin iki soru raporu genel raporun başlığını veya araştırma düğmesini devralmaz", async () => {
  const rows = [task({id: "general", result: {...task().result, title_tr: "Genel rapor"}}),
    task({id: "question-a", request_id: "request-a", updated_at: "2026-10-26T09:00:00Z", result: {...task().result, title_tr: "Birinci sorunun cevabı"}}),
    task({id: "question-b", request_id: "request-b", updated_at: "2026-10-26T10:00:00Z", result: {...task().result, title_tr: "İkinci sorunun cevabı"}})];
  const h = harness(rows); const item = h.state.data.items[0];
  assert.equal(h.api.taskForItem(item).id, "general");
  assert.equal(h.api.summaryFor(item).title_tr, "Genel rapor");
  assert.equal(selectionHarness(rows).workflowTask(item).id, "general");
  await h.api.open("question-a"); await turn(); assert.match(h.dialog().textContent, /Birinci sorunun cevabı/);
  await h.findButton("Kapat").dispatch("click");
  await h.api.open("question-b"); await turn(); assert.match(h.dialog().textContent, /İkinci sorunun cevabı/);
  h.state.data.workflow.tasks = rows.slice(1);
  assert.equal(h.api.taskForItem(item), undefined); assert.equal(h.api.summaryFor(item), null);
  assert.equal(selectionHarness(rows.slice(1)).workflowTask(item), null);
});

test("başka sorunun veya genel haberin yeni raporu eski soru raporunu gizlemez", () => {
  const h = harness([task({id: "question-a-old", request_id: "request-a", stale: true}),
    task({id: "question-b-current", request_id: "request-b", source_revision: 2}),
    task({id: "general-current", source_revision: 2})]);
  assert.equal(h.api.counts().research, 1);
  h.api.renderView("research", h.content);
  assert.equal(h.content.querySelectorAll(".headline-link")[0].dataset.focusKey, "workflow-open-question-a-old");
  h.state.data.workflow.tasks.push(task({id: "question-a-current", request_id: "request-a", source_revision: 2}));
  assert.equal(h.api.counts().research, 0, "Yerine yalnız aynı sorunun güncel raporu geçebilir.");
});

test("yeni revizyonun güncel raporu eski aynı tür raporu gizler; eski ön inceleme derin raporun yerini almaz", () => {
  const h = harness([
    task({id: "deep-old", stale: true}), task({id: "deep-new", source_revision: 2}),
    task({id: "screen-old", item_id: "i2", kind: "screen", stale: true}), task({id: "screen-new", item_id: "i2", kind: "screen", source_revision: 2}),
    task({id: "deep-needs-refresh", item_id: "i3", stale: true}), task({id: "screen-insufficient", item_id: "i3", kind: "screen", source_revision: 2}),
    task({id: "newer-source", item_id: "i4", kind: "screen", source_revision: 3, stale: true}), task({id: "older-deep", item_id: "i4", source_revision: 2}),
  ]);
  assert.equal(h.api.counts().research, 2);
  h.api.renderView("research", h.content);
  assert.deepEqual(h.content.querySelectorAll(".headline-link").map(node => node.dataset.focusKey).sort(), ["workflow-open-deep-needs-refresh", "workflow-open-newer-source"]);
});

test("güncel rapor gerçek aktif, engellenmiş veya başarısız işi gizlemez", () => {
  const statuses = ["queued", "reading", "analyzing", "blocked", "failed"];
  const h = harness([...statuses.map(status => task({id: status, kind: "screen", status, stale: true})), task({id: "replacement", source_revision: 2})]);
  assert.equal(h.api.counts().research, statuses.length);
  h.api.renderView("research", h.content);
  assert.equal(h.content.querySelectorAll(".workflow-card").length, statuses.length);
  assert.deepEqual(h.content.querySelectorAll(".headline-link").map(node => node.dataset.focusKey).sort(), statuses.map(status => `workflow-open-${status}`).sort());
});

test("ilgi elemesi ve uygunluk kararı Türkçe başlık taşısa da ana seçkiye girmez", () => {
  const rows = [task({id: "interest", item_id: "interest", kind: "interest", engine: "nvidia"}), task({id: "review", item_id: "review", kind: "review", engine: "claude"}), task({id: "screen", item_id: "screen", kind: "screen", engine: "nvidia"})];
  const h = harness(rows);
  const items = rows.map((row, index) => ({id: row.item_id, revision: 1, topic: "ai", source_id: String(index)}));
  assert.equal(h.api.summaryFor(items[0]), null); assert.equal(h.api.summaryFor(items[1]), null);
  assert.equal(h.api.summaryFor(items[2]).title_tr, rows[2].result.title_tr);
  const sandbox = vm.createContext({state: {data: {workflow: {tasks: rows}, personal: {settings: {daily_limit: 8}}}}, dateValue: () => null, feedback: () => "yok", snoozed: () => false});
  vm.runInContext(app.slice(app.indexOf("  function publicationAge("), app.indexOf("  function displayTitle(")), sandbox);
  assert.deepEqual(Array.from(sandbox.selectedItems(items), item => item.id), ["screen"]);
});

test("tamamlanan izle ve pas geç uygunlukları aktif sayaçta değil geçmişte görünür", () => {
  const rows = ["watch", "dismiss"].map(value => task({id: value, kind: "review", engine: "claude", result: {...task().result, research_decision: value}}));
  const h = harness(rows);
  assert.equal(h.api.counts().research, 0); assert.equal(h.api.counts().decisions, 0);
  h.api.renderView("research", h.content);
  const history = h.content.querySelector(".workflow-history");
  assert.ok(history); assert.equal(history.open, false);
  assert.equal(history.querySelectorAll(".workflow-card").length, 2);
  assert.match(history.textContent, /Claude uygunluk değerlendirmesi/);
  assert.match(history.textContent, /Şimdilik izle/); assert.match(history.textContent, /Derin araştırmaya gerek görülmedi/);
});

test("sonraki güncel aşama eski ilgi elemesini ve ön araştırmayı aktif listeden kaldırır", () => {
  const h = harness([task({id: "interest-old", kind: "interest", stale: true}), task({id: "screen-old", kind: "screen", stale: true}), task({id: "review-current", kind: "review", result: {...task().result, research_decision: "watch"}})]);
  assert.equal(h.api.counts().research, 0);
  h.api.renderView("research", h.content);
  assert.equal(h.content.querySelectorAll(".workflow-card").length, 2, "Eski ilgi elemesi ile tamamlanan review yalnız geçmişte korunur.");
  assert.ok(h.content.querySelectorAll(".workflow-card").every(node => node.parentElement.parentElement.className === "workflow-history"));
});

test("ön araştırma raporundan doğrudan derin araştırma başlatılır ve yeni iş aynı pencerede açılır", async () => {
  const h = harness([task({id: "surface", kind: "screen", engine: "nvidia", model: "nvidia/model"})]);
  await h.api.open("surface"); await turn();
  assert.match(h.dialog().textContent, /NVIDIA ön araştırması/);
  h.handlers.post = async () => task({id: "deep", kind: "deep", engine: "claude", model: "claude-opus", status: "queued", result: null, source_title: "Original title"});
  await h.findButton("Derin araştır").dispatch("click");
  assert.deepEqual(h.posts, [{url: "/api/workflow/items/i1/research", payload: {}}]);
  assert.match(h.dialog().textContent, /Claude derin araştırması/); assert.match(h.dialog().textContent, /Asıl yayın: Original title/);
  assert.equal(h.document.body.querySelectorAll("dialog").length, 1);
});

test("web dayanakları güvenli bağlantı ve düz metin olur; açık bölüm yenilemede korunur", async () => {
  const record = task({engine: "claude", model: "claude-opus"});
  record.result.external_sources = [{title: '<img src=x onerror="alert(1)">', url: "javascript:alert(1)", summary_tr: "<script>kod değil</script>"}, {title: "Resmî belge", url: "https://example.org/docs", summary_tr: "Yayıncının açıklaması."}];
  const h = harness([record]); await h.api.open("t1"); await turn();
  const external = h.dialog().querySelectorAll("details").find(node => node.dataset.section === "external");
  assert.ok(external); external.open = true;
  assert.equal(external.querySelectorAll("script,img").length, 0);
  assert.match(external.textContent, /<script>kod değil/);
  assert.ok(external.querySelectorAll("a").every(node => !node.href.startsWith("javascript:")));
  assert.ok(external.querySelectorAll("a").some(node => node.href === "https://example.org/docs"));
  assert.match(external.textContent, /bağımsız doğrulandığı anlamına gelmez/);
  h.state.data.workflow.tasks[0].version = 2;
  await h.findButton("Durumu yenile").dispatch("click");
  assert.equal(h.dialog().querySelectorAll("details").find(node => node.dataset.section === "external").open, true);
});

function selectionHarness(rows) {
  const data = {workflow: {tasks: rows}, personal: {settings: {daily_limit: 8}}};
  const sandbox = vm.createContext({state: {data}, dateValue: value => value ? new Date(value) : null, feedback: () => "yok", snoozed: () => false});
  vm.runInContext(app.slice(app.indexOf("  function publicationAge("), app.indexOf("  function displayTitle(")), sandbox);
  return sandbox;
}

test("düşük uygunluk ve uygun değil raporları seçkiye girmez; havuz ve insan kararları korunur", () => {
  const results = [
    task({id: "low-screen", item_id: "low-screen", kind: "screen", result: {...task().result, fit: "low", relevance_score: 15}}),
    task({id: "no-screen", item_id: "no-screen", kind: "screen", result: {...task().result, fit: "high", recommendation: "uygun_degil"}}),
    task({id: "low-deep", item_id: "low-deep", result: {...task().result, fit: "low", relevance_score: 15}}),
    task({id: "no-deep", item_id: "no-deep", result: {...task().result, fit: "possible", recommendation: "uygun_degil"}}),
    task({id: "earlier-positive", item_id: "no-deep", kind: "screen", result: {...task().result, fit: "high", relevance_score: 98}}),
  ];
  const items = results.slice(0, 4).map(row => ({id: row.item_id, revision: 1, topic: "ai", source_id: row.item_id, summary_status: "editor_ozeti", ai_summary: {title_tr: "Eski olumlu başlık", summary_tr: "Eski olumlu açıklama"}}));
  const before = JSON.stringify({items, results});
  const select = selectionHarness(results);
  assert.deepEqual(Array.from(select.selectedItems(items)), [], "Olumlu eski özet veya screen, güncel olumsuz deep kararını seçkiye geri sokmamalı.");
  assert.equal(JSON.stringify({items, results}), before, "Seçki filtresi havuz kaydını veya raporu değiştirmemeli.");
  const h = harness(results); h.state.data.items = items;
  assert.equal(h.api.counts().decisions, 2);
  h.api.renderView("decisions", h.content);
  assert.deepEqual(h.content.querySelectorAll(".headline-link").map(node => node.dataset.focusKey).sort(), ["workflow-open-low-deep", "workflow-open-no-deep"]);
  assert.equal(select.workflowSummary(items[2]).fit, "low", "Tüm havuz ve okuyucu olumsuz raporu göstermeye devam etmeli.");
});

test("güncel model ilgi puanı önce gelir; eşit puanda kişisel tercih ve konu çeşitliliği korunur", () => {
  const values = [["ilgili", 95, -10, "ai"], ["sessiz", 95, -900, "ai"], ["yeni", 80, 10000, "ai"], ["robot", 60, 20000, "robotik"]];
  const rows = values.map(([id, score]) => task({id, item_id: id, result: {...task().result, fit: "possible", relevance_score: score}}));
  const items = values.map(([id, score, personal, topic]) => ({id, revision: 1, topic, source_id: id, personal_score: personal, published_at: id === "yeni" ? new Date().toISOString() : "2025-01-01T00:00:00Z"}));
  items.push({id: "legacy", revision: 1, topic: "tarim", source_id: "legacy", personal_score: 50000, summary_status: "editor_ozeti"});
  const select = selectionHarness(rows);
  assert.deepEqual(Array.from(select.selectedItems(items), item => item.id), ["ilgili", "sessiz", "yeni", "robot", "legacy"], "Konu çeşitliliği için seçilen kartlar da son görünümde model ilgi puanına göre sıralanmalı.");
});

test("eski olumsuz rapor güncel Türkçe özeti engellemez; interest ve review ana seçkiyi yönetmez", () => {
  const ids = ["stale", "old-revision", "interest", "review"];
  const rows = ids.map(id => task({id, item_id: id, kind: ["interest", "review"].includes(id) ? id : "deep", stale: id === "stale", source_revision: id === "old-revision" ? 1 : 2, result: {...task().result, fit: "low", recommendation: "uygun_degil"}}));
  const items = ids.map(id => ({id, revision: 2, topic: "ai", source_id: id, ai_summary: {title_tr: "Güncel Türkçe özet", summary_tr: "Güncel kaynak özeti"}}));
  const select = selectionHarness(rows);
  assert.deepEqual(Array.from(select.selectedItems(items), item => item.id), ids);
  items.forEach(item => { delete item.ai_summary; });
  assert.deepEqual(Array.from(select.selectedItems(items)), [], "İlgi elemesi veya uygunluk kararı tek başına seçki üretmemeli.");
});

test("aynı deponun ardışık sürümleri ana seçkiyi doldurmaz", () => {
  const items = [{id: "new", url: "https://github.com/example/project/releases/tag/v2", topic: "ai", source_id: "a", score: 90},
    {id: "old", url: "https://github.com/example/project/releases/tag/v1", topic: "ai", source_id: "b", score: 80},
    {id: "different", url: "https://github.com/example/another/releases/tag/v1", topic: "ai", source_id: "c", score: 70}]
    .map(row => ({revision: 1, summary_status: "editor_ozeti", ...row}));
  const select = selectionHarness([]);
  assert.deepEqual(Array.from(select.selectedItems(items), item => item.id), ["new", "different"]);
});

test("hazır araştırmadan yeni taslak, mevcut taslağı yalnız sayfa içi onayla bırakır; window.confirm kullanılmaz", async () => {
  for (const language of [null, "en"]) {
    const h = harness([], true, language); const asked = []; let answer = false;
    h.window.confirm = () => { throw new Error("window.confirm must not be used"); };
    h.window.DevTrellisConfirm = {ask: async options => { asked.push(options); return answer; }};
    const questionLabel = language === "en" ? "What do you want to learn?" : "Ne öğrenmek istiyorsun?";
    h.api.open();
    const question = h.control(questionLabel); question.value = "Kendi taslağım"; await question.dispatch("input");
    await h.findButton(language === "en" ? "Close" : "Kapat").dispatch("click"); assert.equal(h.dialog(), null);
    const opener = h.element("button"); h.document.body.append(opener);
    await h.api.open(opener, {question: "Hazır araştırmadan gelen soru", topic: "jarvis"});
    assert.equal(asked.length, 1);
    assert.equal(asked[0].message, language === "en"
      ? "Discard your existing research draft and open a new draft using this research?"
      : "Mevcut araştırma taslağını bırakıp bu araştırmanın bilgileriyle yeni bir taslak açmak istiyor musun?");
    assert.equal(asked[0].confirmLabel, language === "en" ? "Open new draft" : "Yeni taslak aç");
    assert.equal(asked[0].returnFocus, opener);
    assert.equal(h.dialog(), null);
    h.api.open(); assert.equal(h.control(questionLabel).value, "Kendi taslağım");
    await h.findButton(language === "en" ? "Close" : "Kapat").dispatch("click");
    answer = true;
    await h.api.open(opener, {question: "Hazır araştırmadan gelen soru", topic: "jarvis"});
    assert.equal(h.control(questionLabel).value, "Hazır araştırmadan gelen soru");
    assert.equal(h.posts.length, 0);
  }
});

test("bununla araştır: tarayıcı close olayını geciktirse de eski taslak gider, yeni taslak açılır", async () => {
  for (const withDraft of [true, false]) {
    let searchOptions = null;
    const library = () => ({searchBox(value) { searchOptions = value; const box = h.element("div"); box.focusSearch = () => {}; return box; }, busy: () => false});
    const h = harness([], true, null, undefined, {asyncClose: true, library});
    const asked = [];
    h.window.DevTrellisConfirm = {ask: async value => { asked.push(value); return true; }};
    const opener = h.element("button"); h.document.body.append(opener);
    await h.api.open(opener);
    const first = h.dialog(); assert.ok(first); assert.ok(searchOptions?.onResearch);
    if (withDraft) { const question = h.control("Ne öğrenmek istiyorsun?"); question.value = "Eski taslak"; await question.dispatch("input"); }
    await searchOptions.onResearch({question: "Hazır araştırmadan gelen soru", topic: "jarvis", need: "Topluluk raporu"});
    assert.equal(asked.length, withDraft ? 1 : 0);
    // close olayı henüz gelmedi: eski pencere kapandı ama DOM'dan çıkmadı.
    assert.equal(first.open, false);
    await turn(); await turn();
    const dialogs = h.document.body.querySelectorAll("dialog");
    assert.equal(dialogs.length, 1, "Yalnız yeni taslak penceresi açık kalmalı");
    assert.notEqual(dialogs[0], first); assert.equal(dialogs[0].open, true);
    assert.equal(h.control("Ne öğrenmek istiyorsun?").value, "Hazır araştırmadan gelen soru");
    assert.equal(h.control("İhtiyacımızı kısaca anlat (isteğe bağlı)").value, "Topluluk raporu");
    assert.equal(h.posts.length, 0);
    await h.findButton("Kapat").dispatch("click"); await turn();
    assert.equal(h.dialog(), null);
    assert.equal(h.document.activeElement, opener, "Odak ilk açan düğmeye döner");
  }
});

test("bununla araştır: onay reddedilirse taslak ve pencere korunur (gecikmeli close ile)", async () => {
  let searchOptions = null;
  const library = () => ({searchBox(value) { searchOptions = value; const box = h.element("div"); box.focusSearch = () => {}; return box; }, busy: () => false});
  const h = harness([], true, null, undefined, {asyncClose: true, library});
  h.window.DevTrellisConfirm = {ask: async () => false};
  await h.api.open();
  const first = h.dialog(); const question = h.control("Ne öğrenmek istiyorsun?"); question.value = "Eski taslak"; await question.dispatch("input");
  await searchOptions.onResearch({question: "Hazır soru", topic: "ai"});
  await turn(); await turn();
  assert.equal(h.dialog(), first); assert.equal(first.open, true);
  assert.equal(h.control("Ne öğrenmek istiyorsun?").value, "Eski taslak");
});

test("taslak onayı beklenirken ikinci pencere açılmaz; onay bileşeni yoksa görünür hata çıkar", async () => {
  const h = harness([], true); const pending = deferred();
  h.window.DevTrellisConfirm = {ask: () => pending.promise};
  h.api.open(); const question = h.control("Ne öğrenmek istiyorsun?"); question.value = "Kendi taslağım"; await question.dispatch("input");
  await h.findButton("Kapat").dispatch("click");
  const waiting = h.api.open(null, {question: "Hazır soru", topic: "ai"});
  assert.equal(h.api.busy(), true); h.api.open(); assert.equal(h.dialog(), null);
  pending.resolve(false); await waiting; assert.equal(h.api.busy(), false); assert.equal(h.dialog(), null);

  const missing = harness([], true);
  missing.api.open(); const draft = missing.control("Ne öğrenmek istiyorsun?"); draft.value = "Kendi taslağım"; await draft.dispatch("input");
  await missing.findButton("Kapat").dispatch("click");
  await missing.api.open(null, {question: "Hazır soru", topic: "ai"});
  assert.equal(missing.dialog(), null);
  assert.deepEqual(missing.messages.at(-1), {message: "Onay penceresi açılamadı; işlem yapılmadı. Sayfayı yenileyip yeniden dene.", error: true});
});
