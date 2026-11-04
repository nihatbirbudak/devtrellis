// Kaynak denetiminde güvenli metni, gerçek ölçüm ayrımını ve çakışmalı işlemleri doğrular.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const translator = require("./research-i18n.cjs");

const source = fs.readFileSync(path.join(__dirname, "..", "radar", "static", "source-learning.js"), "utf8");
const turn = () => new Promise(resolve => setImmediate(resolve));

function harness(data, language = null) {
  let document;
  class Node {
    constructor(tag, className = "", text = "") {
      this.tagName = tag.toUpperCase(); this.className = className; this._text = String(text ?? "");
      this.children = []; this.parentElement = null; this.dataset = {}; this.attributes = {}; this.listeners = new Map();
      this.disabled = false; this.open = false;
    }
    get textContent() { return this._text + this.children.map(node => node.textContent).join(""); }
    set textContent(value) { this.replaceChildren(); this._text = String(value); }
    set innerHTML(_) { throw new Error("HTML metin olarak işlenmemeli"); }
    get isConnected() { return this === document.body || Boolean(this.parentElement?.isConnected); }
    append(...nodes) { for (const node of nodes) { if (node.parentElement) node.remove(); node.parentElement = this; this.children.push(node); } }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this); this.parentElement = null; }
    replaceChildren(...nodes) { this.children.forEach(node => { node.parentElement = null; }); this.children = []; this._text = ""; this.append(...nodes); }
    replaceWith(node) { const parent = this.parentElement; if (!parent) return; const index = parent.children.indexOf(this); this.remove(); node.parentElement = parent; parent.children.splice(index, 0, node); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    querySelectorAll(selector) {
      const selectors = selector.split(",").map(value => value.trim());
      const matches = node => selectors.some(value => value === "[data-focus-key]" ? Boolean(node.dataset.focusKey) : value.startsWith(".") ? node.className.split(" ").includes(value.slice(1)) : value.startsWith("#") ? node.id === value.slice(1) : node.tagName.toLowerCase() === value);
      const result = [];
      for (const node of this.children) { if (matches(node)) result.push(node); result.push(...node.querySelectorAll(selector)); }
      return result;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    addEventListener(name, action) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(action); }
    focus() { if (!this.disabled && this.isConnected) document.activeElement = this; }
    async dispatch(name) {
      if (name === "click" && this.disabled) return;
      await Promise.all((this.listeners.get(name) || []).map(action => action({target: this, currentTarget: this, preventDefault() {}})));
      await turn();
    }
  }
  document = {body: new Node("body"), activeElement: null}; document.activeElement = document.body;
  const element = (tag, className, text) => new Node(tag, className, text);
  const settings = {enabled: true, exploration_percent: 20, version: 2};
  const sourceRow = {source_id: "s/1", name: "Birincil yayın", topic: "jarvis", protected: false, lifecycle: "regular", version: 3, configured_interval_hours: 24, effective_interval_hours: 24, metrics: {collected: 10, examined: 0, human_positive: 0, human_negative: 0, observation_days: 1}, evidence_label: "Yeterli gözlem yok", recommendation: {action: "keep", reason: "Henüz incelenmeyen kayıtlar olumsuz sayılmaz."}};
  const candidate = {id: "c/1", name: "Yeni topluluk", topic: "jarvis", status: "verified", version: 1, url: "https://example.org/feed", reason: "Yeni araştırma alanı", discovered_from: "Bağımsız arama"};
  const change = {id: "d/1", source_id: "s/1", before_interval: 24, after_interval: 72, reason: "Yeterli geri bildirim sonrası", created_at: "2026-10-20", reverted: false, version: 4};
  const state = {topic: "all", search: "", pending: new Set(), data: data || {source_learning: {settings, sources: [sourceRow], changes: [change], daily: {collected: 10, examined: 0, model_calls: 2}, weekly: {collected: 10, examined: 0}}, source_scout: {running: false, candidates: [candidate], coverage: {sample_count: 0}}}};
  const posts = [], notices = [], views = [];
  const handlers = {post: async () => ({}), refresh: async () => true};
  const sandbox = vm.createContext({window: {UfukUI: language ? translator(language) : undefined}, document, URL, Intl}); vm.runInContext(source, sandbox);
  const api = sandbox.window.UFUKSourceLearningFactory({element, state, topics: {jarvis: "Ev otomasyonu", tarim: "Tarım"}, post: async (url, payload) => { posts.push({url, payload: JSON.parse(JSON.stringify(payload))}); return handlers.post(url, payload); }, toast: (text, error) => notices.push({text, error}), formatDate: value => value, refreshAfterMutation: () => handlers.refresh(), changeView: view => views.push(view)});
  const container = element("div"); document.body.append(container);
  function render() { container.replaceChildren(); api.render(container); }
  function button(text) { const result = container.querySelectorAll("button").find(node => node.textContent === text); assert.ok(result, `Düğme bulunmalı: ${text}`); return result; }
  function detail(text) { const summary = container.querySelectorAll("summary").find(node => node.textContent === text); assert.ok(summary, `Bölüm bulunmalı: ${text}`); return summary.parentElement; }
  render(); return {api, state, settings, sourceRow, candidate, change, handlers, posts, notices, views, document, container, button, detail, render};
}

test("İngilizce kaynak ekranı ölçüm ve karar metnini çevirir, özgün aday içeriğini korur", async () => {
  const h = harness(undefined, "en");
  h.candidate.name = "Araştırma UFUK <img src=x>";
  h.candidate.reason = "Kullanıcının özgün kaynak gerekçesi";
  h.sourceRow.metrics.collected = 1234;
  h.render();
  assert.match(h.container.textContent, /SOURCE LIBRARY/);
  assert.match(h.container.textContent, /Every 24 hours/);
  assert.match(h.container.textContent, /1,234/);
  assert.match(h.container.textContent, /new sources: 20%/);
  assert.ok(h.container.textContent.includes(h.candidate.name));
  assert.ok(h.container.textContent.includes(h.candidate.reason));
  assert.equal(h.container.querySelectorAll("img").length, 0);
  await h.button("Start trial").dispatch("click");
  assert.deepEqual(h.posts[0].payload, {decision: "trial", expected_version: 1});
  assert.equal(h.notices[0].text, "Source added to a limited trial.");
});

test("eksik ölçüm ve gerçek sıfır ayrılır; veri olmadan iyimser sonuç gösterilmez", () => {
  const h = harness({});
  assert.match(h.container.textContent, /Ölçülmedi/);
  assert.match(h.container.textContent, /Öğrenme durumu alınamadı/);
  assert.equal(h.button("Otomatik öğrenmeyi aç").disabled, true);
  const actual = harness();
  const examined = actual.container.querySelectorAll("dt").find(node => node.textContent === "İncelenen kayıt");
  assert.equal(examined.parentElement.querySelector("dd").textContent, "0");
  assert.match(actual.container.textContent, /Parasal maliyet bu ekranda ölçülmüyor/);
  assert.match(actual.container.textContent, /bütün interneti kapsadığımızı göstermez/);
});

test("kaynak metni HTML olmaz; yalnız HTTP(S) bağlantısı ve güvenli yeni sekme kullanılır", () => {
  const h = harness();
  h.candidate.name = '<img src=x onerror="alert(1)">'; h.candidate.url = "javascript:alert(1)";
  h.candidate.reason = "<script>alert('x')</script>"; h.render();
  assert.match(h.container.textContent, /<img src=x/);
  assert.equal(h.container.querySelectorAll("img").length, 0);
  assert.equal(h.container.querySelectorAll("script").length, 0);
  assert.equal(h.container.querySelectorAll("a").length, 0);
  h.candidate.url = "https://user:secret@example.org/feed"; h.render();
  assert.equal(h.container.querySelectorAll("a").length, 0);
  h.candidate.url = "https://example.org/feed"; h.render();
  assert.equal(h.container.querySelector("a").rel, "noopener noreferrer");
  assert.equal(h.container.querySelector("a").target, "_blank");
});

test("koruma kararı sürümle gönderilir; odak ve açılan ayrıntılar korunur", async () => {
  const h = harness();
  const details = h.detail("Kaynak katkısını incele · 1"); details.open = true; await details.dispatch("toggle");
  const target = h.button("Bu kaynağın sıklığını koru"); target.focus();
  h.handlers.post = async () => { h.sourceRow.protected = true; h.sourceRow.version = 4; };
  await target.dispatch("click");
  assert.deepEqual(h.posts, [{url: "/api/source-learning/sources/s%2F1/protect", payload: {protected: true, expected_version: 3}}]);
  assert.equal(h.detail("Kaynak katkısını incele · 1").open, true);
  assert.equal(h.document.activeElement, h.button("Sıklık korumasını kaldır"));
  assert.equal(h.button("Sıklık korumasını kaldır").getAttribute("aria-pressed"), "true");
  assert.equal(h.state.pending.size, 0);
});

test("sürüm çakışması otomatik yeniden yazmaz ve güncel kararı kullanıcıya bırakır", async () => {
  const h = harness();
  h.handlers.post = async () => { throw Object.assign(new Error("Çakıştı"), {status: 409}); };
  h.handlers.refresh = async () => { h.sourceRow.version = 9; h.sourceRow.protected = true; return true; };
  await h.button("Bu kaynağın sıklığını koru").dispatch("click");
  assert.equal(h.posts.length, 1);
  assert.match(h.container.textContent, /önceki istek tekrarlanmadı/);
  assert.equal(h.button("Sıklık korumasını kaldır").disabled, false);
  h.handlers.post = async () => ({});
  await h.button("Sıklık korumasını kaldır").dispatch("click");
  assert.deepEqual(h.posts[1].payload, {protected: false, expected_version: 9});
});

test("süren yazı ve yenileme boyunca ikinci işlem gönderilmez", async () => {
  const h = harness(); let finishPost, finishRefresh;
  h.handlers.post = () => new Promise(resolve => { finishPost = resolve; });
  h.handlers.refresh = () => new Promise(resolve => { finishRefresh = resolve; });
  const first = h.button("Denemeye al").dispatch("click"); await turn();
  assert.equal(h.api.busy(), true); assert.equal(h.state.pending.has("source-learning"), true);
  await h.button("Keşif ve denetim yap").dispatch("click");
  assert.equal(h.posts.length, 1);
  finishPost({}); await turn();
  assert.equal(h.api.busy(), true);
  await h.button("Şimdilik takip etme").dispatch("click"); assert.equal(h.posts.length, 1);
  finishRefresh(true); await first;
  assert.equal(h.api.busy(), false); assert.equal(h.state.pending.size, 0);
});

test("yazı sonrası okuma hatası yeni yazıları kilitler; yenilemek aynı yazıyı tekrarlamaz", async () => {
  const h = harness();
  h.handlers.refresh = async () => { throw new Error("Ağ kesildi"); };
  await h.button("Otomatik öğrenmeyi duraklat").dispatch("click");
  assert.equal(h.posts.length, 1);
  assert.equal(h.button("Otomatik öğrenmeyi duraklat").disabled, true);
  assert.match(h.container.textContent, /Bir başka değişiklik yapmadan önce/);
  h.handlers.refresh = async () => { h.settings.enabled = false; h.settings.version = 3; return true; };
  await h.button("Güncel görünümü al").dispatch("click");
  assert.equal(h.posts.length, 1);
  assert.equal(h.button("Otomatik öğrenmeyi aç").disabled, false);
});

test("ayar, aday kararı ve geri alma yalnız ilgili sürümle yazılır", async () => {
  const h = harness();
  await h.button("Otomatik öğrenmeyi duraklat").dispatch("click");
  await h.button("Denemeye al").dispatch("click");
  await h.button("Bu değişikliği geri al").dispatch("click");
  assert.deepEqual(h.posts, [
    {url: "/api/source-learning/settings", payload: {enabled: false, expected_version: 2}},
    {url: "/api/source-learning/candidates/c%2F1/decision", payload: {decision: "trial", expected_version: 1}},
    {url: "/api/source-learning/changes/d%2F1/undo", payload: {expected_version: 4}},
  ]);
});

test("sürümü eksik adayın ve geri alınmış kararın eylemi açılmaz", () => {
  const h = harness(); delete h.candidate.version; h.change.reverted = true; h.render();
  assert.equal(h.button("Denemeye al").disabled, true);
  assert.equal(h.container.querySelectorAll("button").some(node => node.textContent === "Bu değişikliği geri al"), false);
});

test("çalışan keşif yeniden başlatılmaz; ilgi ayarı mevcut sayfaya yönlendirir", async () => {
  const h = harness(); h.state.data.source_scout.running = true; h.render();
  assert.equal(h.button("Keşif ve denetim sürüyor…").disabled, true);
  await h.button("Keşif ve denetim sürüyor…").dispatch("click");
  await h.button("İlgi alanlarını düzenle").dispatch("click");
  assert.equal(h.posts.length, 0); assert.deepEqual(h.views, ["settings"]);
});

test("kaynak katkısı konu ve Türkçe aramaya göre süzülür", () => {
  const h = harness();
  h.state.data.source_learning.sources.push({...h.sourceRow, source_id: "s2", name: "İklim bülteni", topic: "tarim"});
  h.state.topic = "tarim"; h.state.search = "iklim"; h.render();
  const outer = h.detail("Kaynak katkısını incele · 2");
  assert.match(outer.textContent, /İklim bülteni/);
  assert.doesNotMatch(outer.textContent, /Birincil yayın/);
});

test("hatalı negatif, boş ve NaN değerler gerçek sıfır gibi gösterilmez", () => {
  const h = harness();
  h.sourceRow.metrics = {collected: null, examined: -1, model_calls: NaN, reads: "0"}; h.render();
  const values = h.detail("Birincil yayın").querySelectorAll("dd");
  assert.equal(values.every(node => node.textContent === "Ölçülmedi"), true);
});

test("hata metni görünür olur ve başarısız işlem yerel ayarı iyimser değiştirmez", async () => {
  const h = harness(); h.handlers.post = async () => { throw new Error("Kaynağa ulaşılamadı."); };
  await h.button("Bu kaynağın sıklığını koru").dispatch("click");
  assert.equal(h.sourceRow.protected, false);
  assert.match(h.container.textContent, /Kaynağa ulaşılamadı/);
  assert.equal(h.container.querySelector(".source-learning-notice").getAttribute("role"), "alert");
  assert.equal(h.button("Bu kaynağın sıklığını koru").disabled, false);
});

test("erişimi doğrulanmamış aday doğrudan denemeye alınamaz", async () => {
  const h = harness(); h.candidate.status = "candidate"; h.render();
  assert.equal(h.container.querySelectorAll("button").some(node => node.textContent === "Denemeye al"), false);
  await h.button("Şimdilik takip etme").dispatch("click");
  assert.deepEqual(h.posts[0].payload, {decision: "dismiss", expected_version: 1});
  h.candidate.status = "failed"; h.render();
  assert.equal(h.container.querySelectorAll("button").some(node => node.textContent === "Denemeye al"), false);
});

test("bağımsız denetim örneği ilgisi doğrulanmış önemli bir kaçırma gibi gösterilmez", () => {
  const h = harness();
  h.state.data.source_scout.coverage = {status: "partial", sample_count: 1, counts: {missing_source: 1}, topics_checked: ["tarim"], samples: [{title: "Sera sulaması", url: "https://example.org/paper", stage: "missing_source", relevance: "unconfirmed", query: "greenhouse water", reason: "Başlangıç havuzunda yok"}], errors: [{topic: "ai", message: "Dizine ulaşılamadı"}]};
  h.render();
  const node = h.detail("Neleri kaçırıyoruz? Bağımsız örnek denetimi");
  assert.match(node.textContent, /İlgimize uygunluğu henüz doğrulanmadı/);
  assert.match(node.textContent, /Kontrol edilen alanlar: Tarım/);
  assert.match(node.textContent, /Dizine ulaşılamadı/);
  const missing = node.querySelectorAll("dt").find(row => row.textContent === "Takip kaynağı eksik");
  const rejected = node.querySelectorAll("dt").find(row => row.textContent === "Elenmiş");
  assert.equal(missing.parentElement.querySelector("dd").textContent, "1");
  assert.equal(rejected.parentElement.querySelector("dd").textContent, "0");
});

test("günlük ölçüm alanları ve keşif bütçesi sağlayıcı çağrılarından ayrılır", () => {
  const h = harness();
  h.state.data.source_learning.daily = {period_start: "2026-10-25", period_end: "2026-10-26", added: 11, changed: 3, model_calls: 4, reads: 2, collection_runs: 5, errors: 0, duplicates: null};
  h.state.data.source_scout.budget = {requests: 7, request_limit: 20, probes: 1, probe_limit: 5, trials: 1, trial_limit: 5, active_trial_limit: 3, resets_at: "2026-11-02"};
  h.render();
  const report = h.detail("Günlük ve haftalık gelişim");
  assert.match(report.textContent, /2026-10-25 — 2026-10-26/);
  assert.match(report.textContent, /Ağ isteği: 7 \/ 20/);
  assert.match(report.textContent, /Model çağrıları ve parasal maliyet ile aynı ölçü değildir/);
  const added = report.querySelectorAll("dt").find(node => node.textContent === "Yeni kayıt");
  assert.equal(added.parentElement.querySelector("dd").textContent, "11");
});

test("daha yeni tercih varken eski kararın geri al düğmesi açılmaz", () => {
  const h = harness(); h.change.can_revert = false; h.render();
  assert.equal(h.container.querySelectorAll("button").some(node => node.textContent === "Bu değişikliği geri al"), false);
  assert.match(h.container.textContent, /Daha yeni bir tercih bulunduğu için/);
});

test("uzayan deneme kullanıcı kararını görünür ister; kayıtları kendiliğinden kapatmaz", () => {
  const h = harness(); h.sourceRow.lifecycle = "trial"; h.sourceRow.trial_review_due = true;
  h.state.data.source_learning.summary = {trial_waiting_review: 1}; h.render();
  assert.match(h.container.textContent, /1 kaynak denemesi 30 günü aştı/);
  assert.match(h.container.textContent, /Kanıt eksikliği olumsuz oy sayılmadı/);
  assert.match(h.container.textContent, /Deneme kararı bekleniyor/);
  assert.equal(h.posts.length, 0);
});

test("uzun aday listesi kısalır ama kalan adaylar yüklenip yönetilebilir", async () => {
  const h = harness();
  h.state.data.source_scout.candidates = Array.from({length: 31}, (_, index) => ({...h.candidate, id: `c${index}`, name: `Aday numarası ${index}`})); h.render();
  assert.doesNotMatch(h.detail("Yeni kaynak adayları ve denemeler · 31").textContent, /Aday numarası 30/);
  await h.button("Daha fazla göster (1 kayıt)").dispatch("click");
  assert.match(h.detail("Yeni kaynak adayları ve denemeler · 31").textContent, /Aday numarası 30/);
  assert.equal(h.posts.length, 0);
});
