// Okuma panelinde geciken istekleri, yeniden açılışı, odak korumasını ve güvenli kaynak gösterimini doğrular.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const translator = require("./research-i18n.cjs");

const source = fs.readFileSync(path.join(__dirname, "..", "radar", "static", "insights.js"), "utf8");
const app = fs.readFileSync(path.join(__dirname, "..", "radar", "static", "app.js"), "utf8");
const links = app.slice(app.indexOf("  function safeUrl("), app.indexOf("  function dateValue("));
assert.ok(links.includes("function sourceLink"), "Bağlantı testi uygulamanın gerçek URL süzgecini kullanmalı.");
const turn = () => new Promise(resolve => setImmediate(resolve));

function harness(language = null) {
  let document;
  class Node {
    constructor(tag, className = "", text = "") {
      this.tagName = tag.toUpperCase(); this.className = className; this._text = String(text ?? "");
      this.children = []; this.parentElement = null; this.dataset = {}; this.attributes = {}; this.listeners = new Map();
      this.disabled = false; this.hidden = false; this.open = false;
    }
    get isConnected() { return this === document.body || Boolean(this.parentElement?.isConnected); }
    get textContent() { return this._text + this.children.map(node => node.textContent).join(""); }
    set textContent(value) { this.replaceChildren(); this._text = String(value); }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    remove() {
      if (this.contains(document.activeElement)) document.activeElement = document.body;
      if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this);
      this.parentElement = null;
    }
    append(...nodes) {
      for (const node of nodes) { if (node.parentElement) node.remove(); node.parentElement = this; this.children.push(node); }
    }
    replaceChildren(...nodes) { this.children.slice().forEach(node => node.remove()); this._text = ""; this.append(...nodes); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    matches(selector) {
      if (selector.startsWith(".")) return this.className.split(" ").includes(selector.slice(1));
      if (selector.startsWith("#")) return this.id === selector.slice(1);
      const attribute = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
      if (attribute) {
        const key = attribute[1];
        const value = key.startsWith("data-") ? this.dataset[key.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] : this.getAttribute(key);
        return attribute[2] === undefined ? value !== undefined : value === attribute[2];
      }
      return this.tagName.toLowerCase() === selector;
    }
    querySelectorAll(selector) {
      const selectors = selector.split(",").map(value => value.trim());
      const result = [];
      for (const node of this.children) {
        if (selectors.some(value => node.matches(value))) result.push(node);
        result.push(...node.querySelectorAll(selector));
      }
      return result;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    addEventListener(name, listener) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(listener); }
    focus() { if (this.isConnected && !this.disabled) document.activeElement = this; }
    click() { return this.dispatch("click"); }
    async dispatch(name, values = {}) {
      if (name === "click" && this.disabled) return;
      await Promise.all((this.listeners.get(name) || []).map(listener => listener({target: this, preventDefault() {}, ...values})));
      await turn();
    }
  }
  const documentListeners = new Map();
  const windowListeners = new Map();
  document = {body: new Node("body"), visibilityState: "visible", activeElement: null,
    getElementById(id) { return this.body.querySelector(`#${id}`); },
    querySelector(selector) { return this.body.querySelector(selector); },
    querySelectorAll(selector) { return this.body.querySelectorAll(selector); },
    addEventListener(name, listener) { documentListeners.set(name, listener); }};
  document.activeElement = document.body;
  const element = (tag, className, text) => new Node(tag, className, text);
  const container = element("div"); document.body.append(container);
  const state = {readerItemId: null, data: {ai: {configured: true, model: "deneme-modeli", deep_configured: true, deep_model: "claude-opus"}}};
  const requests = []; const posts = []; const timers = new Map(); let nextTimer = 0;
  function deferred(url, target) {
    return new Promise((resolve, reject) => target.push({url, settled: false,
      resolve(value) { this.settled = true; resolve(value); }, reject(error) { this.settled = true; reject(error); }}));
  }
  const ui = translator(language);
  const sandbox = vm.createContext({t: ui.t, window: {UfukUI: language ? ui : undefined, addEventListener(name, listener) { windowListeners.set(name, listener); }}, document,
    element, icon: () => element("svg"), URL, Blob,
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; },
    clearTimeout(id) { timers.delete(id); }});
  vm.runInContext(links, sandbox);
  vm.runInContext(source, sandbox);
  const api = sandbox.window.UFUKInsightsFactory({element, state, sourceLink: sandbox.sourceLink, formatDate: value => value || "Bilinmiyor",
    request: url => deferred(url, requests), post: (url, payload) => deferred(url, posts), refreshAfterMutation: async () => true});
  const item = id => ({id, title: `Kaynak ${id}`, url: `https://example.org/${id}`, revision: 1});
  function open(id) {
    const value = item(id);
    state.readerItemId = id;
    api.activate?.(value);
    container.replaceChildren(api.mount(value));
  }
  function close() { api.close?.(state.readerItemId); state.readerItemId = null; }
  const url = (id, mode) => `/api/items/${id}/${mode === "article" ? "article" : `ai?kind=${mode}`}`;
  function pending(id, mode) { return requests.filter(entry => entry.url === url(id, mode) && !entry.settled); }
  function resolve(id, mode, value) {
    const target = pending(id, mode)[0]; assert.ok(target, `${id}/${mode} isteği beklenmeli`); target.resolve(value);
  }
  function findButton(label) {
    const target = container.querySelectorAll("button").find(node => node.textContent === label);
    assert.ok(target, `Düğme bulunmalı: ${label}`); return target;
  }
  return {api, state, container, document, requests, posts, timers, windowListeners, documentListeners, open, close, pending, resolve, findButton, item, url};
}

function result(id, title = "Önceki Türkçe sonuç", extras = {}) {
  return {item_id: id, status: "hazir", stale: false, model: "deneme-modeli", generated_at: "2026-10-26T00:00:00Z",
    result: {title_tr: title, summary_tr: "Kaynak metnine dayalı örnek özet.", benefit_tr: "Yerel kullanım için değerlendirilebilir.",
      limitations: ["Bu yorum bağımsız doğrulama değildir."], next_steps: ["Özgün kaynağı incele."], evidence: [{item_id: id, quote: "Kaynağın doğrudan alıntısı."}]},
    sources: [{item_id: id, title: "Asıl yayın", url: `https://example.org/${id}`, publisher: "Yayıncı"}], ...extras};
}
async function initial(h, id, summary = result(id)) {
  h.resolve(id, "summary", summary);
  h.resolve(id, "article", {item_id: id, status: "okunmadi", body: ""});
  h.resolve(id, "research", {item_id: id, status: "yapilmadi", result: null});
  await turn();
}

test("İngilizce okuma arayüzü rapor ve alıntıyı değiştirmez; Codex web sınırını gösterir", async () => {
  const h = harness("en");
  h.state.data.ai.deep_provider = "codex";
  h.state.data.ai.deep_model = "codex/configured-default";
  h.open("a");
  const original = "Araştırma UFUK <img src=x>";
  await initial(h, "a", result("a", original));
  assert.ok(h.findButton("Summary"));
  assert.ok(h.findButton("Generate again"));
  assert.match(h.container.textContent, /Limitations and uncertainties/);
  assert.match(h.container.textContent, /Review the evidence · 1 sources/);
  assert.ok(h.container.textContent.includes(original));
  assert.ok(h.container.textContent.includes("Kaynağın doğrudan alıntısı."));
  assert.equal(h.container.querySelectorAll("img").length, 0);
  await h.findButton("Compare sources").dispatch("click");
  assert.match(h.container.textContent, /Codex compares source texts supplied by the application/);
  assert.match(h.container.textContent, /does not run new web searches/);
  assert.doesNotMatch(h.container.textContent, /Claude compares/);
  assert.equal(h.posts.length, 0);
});

test("POST öncesindeki GET yeni çalışma durumunu ezmez; yazıdan sonra taze GET gerekir", async () => {
  const h = harness(); h.open("a"); await initial(h, "a");
  await h.findButton("Türkçe özet").dispatch("click");
  assert.equal(h.pending("a", "summary").length, 1);
  const writing = h.findButton("Yeniden hazırla").dispatch("click");
  await turn(); assert.equal(h.posts.length, 1);
  h.posts[0].resolve({item_id: "a", status: "calisiyor", result: null});
  await turn();
  h.resolve("a", "summary", result("a", "POST öncesi eski cevap"));
  await turn();
  assert.equal(h.requests.filter(entry => entry.url === h.url("a", "summary")).length, 3,
    "Uçuşta eski GET sonrasında en az bir taze okuma başlamalı.");
  h.resolve("a", "summary", result("a", "Yeni hazırlanmış sonuç"));
  await writing; await turn();
  assert.match(h.container.textContent, /Yeni hazırlanmış sonuç/);
  assert.doesNotMatch(h.container.textContent, /POST öncesi eski cevap/);

  const failed = harness(); failed.open("a");
  failed.pending("a", "summary")[0].reject(new Error("Geçici bağlantı hatası"));
  failed.resolve("a", "article", {status: "okunmadi", body: ""});
  failed.resolve("a", "research", {status: "yapilmadi", result: null});
  await turn(); assert.equal(failed.timers.size, 0);
  const retry = failed.findButton("Durumu yeniden oku").dispatch("click");
  await turn();
  failed.resolve("a", "summary", {status: "calisiyor", result: null});
  await retry; await turn();
  assert.equal(failed.timers.size, 1, "Hata sonrası elle alınan çalışan durum da otomatik izlemeyi başlatmalı.");
});

test("aynı haber revizyonunu kapatıp açmak kayıtlı sonucu ve stale bilgisini yeniden okur", async () => {
  const h = harness(); h.open("a"); await initial(h, "a");
  h.close();
  h.open("a"); await turn();
  assert.equal(h.requests.length, 6, "Aynı revizyon başka penceredeki çalışma veya makale değişimini gizlememeli.");
  await initial(h, "a", result("a", "Başka pencerede kaydedildi", {stale: true}));
  assert.match(h.container.textContent, /Başka pencerede kaydedildi/);
  assert.match(h.container.textContent, /Kaynak bu sonuçtan sonra değişmiş/);
  const requestCount = h.requests.length;
  h.api.mount(h.item("a")); await turn();
  assert.equal(h.requests.length, requestCount, "Sıradan görünüm çizimi yeniden üç GET başlatmamalı.");

  h.close(); h.open("a");
  assert.equal(h.pending("a", "summary").length, 1);
  h.close(); h.open("a");
  await initial(h, "a", result("a", "Yeniden açmadan önceki GET"));
  assert.equal(h.pending("a", "summary").length, 1,
    "Önceki açılışın uçuşta GET'i beklenmeli, ardından yeni açılış için taze GET yapılmalı.");
  await initial(h, "a", result("a", "Son açılışın taze sonucu", {stale: true}));
  assert.match(h.container.textContent, /Son açılışın taze sonucu/);
});

test("kapatılan kartın geç cevabı açık kartın devam eden iş kontrolünü iptal etmez", async () => {
  const h = harness(); h.open("a");
  h.resolve("a", "summary", {item_id: "a", status: "calisiyor", result: null});
  h.resolve("a", "article", {item_id: "a", status: "okunmadi", body: ""});
  await turn(); h.close(); h.open("b");
  await initial(h, "b", {item_id: "b", status: "calisiyor", result: null});
  assert.equal(h.timers.size, 1, "Açık B kartının işi izlenmeli.");
  h.resolve("a", "research", {item_id: "a", status: "yapilmadi", result: null});
  await turn();
  assert.equal(h.timers.size, 1, "A kartının geç cevabı B kontrolünü silemez.");
  assert.match(h.container.textContent, /Kaynaklar inceleniyor/);
});

test("pagehide sonrası uçuşta kalan GET yeni zamanlayıcı kuramaz", async () => {
  const h = harness(); h.open("a");
  h.resolve("a", "summary", {item_id: "a", status: "calisiyor", result: null});
  h.resolve("a", "article", {item_id: "a", status: "okunmadi", body: ""});
  await turn();
  h.windowListeners.get("pagehide")();
  h.resolve("a", "research", {item_id: "a", status: "yapilmadi", result: null});
  await turn();
  assert.equal(h.timers.size, 0, "Sayfadan çıktıktan sonra kontrol yeniden canlanmamalı.");
});

test("başka sekmenin GET cevabı okunan sonuçtaki odağı ve açık dayanakları bozmaz", async () => {
  const h = harness(); h.open("a");
  h.resolve("a", "summary", result("a")); await turn();
  const download = h.container.querySelector('[data-insight-action="download"]'); download.focus();
  const evidence = h.container.querySelector(".insight-evidence"); evidence.open = true; await evidence.dispatch("toggle");
  h.resolve("a", "article", {item_id: "a", status: "okunmadi", body: ""}); await turn();
  assert.equal(h.document.activeElement, download, "Başka moda ait okuma indirme düğmesinin odağını kaldıramaz.");
  assert.equal(h.container.querySelector(".insight-evidence").open, true);
  h.resolve("a", "research", {item_id: "a", status: "yapilmadi", result: null}); await turn();
  assert.equal(h.document.activeElement, download);
});

test("model metni HTML olmaz, tehlikeli URL açılmaz ve alıntı eşleşmesi bağımsız doğrulama diye sunulmaz", async () => {
  const h = harness(); h.open("a");
  const record = result("a", '<img src=x onerror="alert(1)">');
  record.result.summary_tr = "<script>çalıştırılmamalı</script>";
  record.result.evidence[0].quote = "<b>Kaynakta geçen düz metin</b>";
  record.sources[0].url = "javascript:alert(1)";
  record.sources.push({item_id: "b", title: "İkinci kaynak", url: "https://example.org/b"});
  await initial(h, "a", record);
  assert.match(h.container.textContent, /<img src=x/);
  assert.match(h.container.textContent, /<script>/);
  assert.equal(h.container.querySelectorAll("script,img,b").length, 0);
  assert.ok(h.container.querySelectorAll("a").every(node => /^https?:\/\//.test(node.href) || node.href === "/api/items/a/ai/download?kind=summary"));
  assert.match(h.container.textContent, /YAPAY ZEKÂ DEĞERLENDİRMESİ/);
  assert.match(h.container.textContent, /bağımsız doğrulama sayılmaz/);
  assert.match(h.container.textContent, /Alıntılar uygulamadaki kaynak metniyle eşleştirilmiştir/);
});

test("model kotası dolunca üretim kapanır; kayıtlı rapor ve ayrı makale okuması kullanılabilir", async () => {
  const h = harness();
  h.state.data.ai.daily_remaining = 0;
  h.state.data.ai.deep_daily_remaining = 0;
  h.state.data.ai.budget_resets_at = "2026-10-27T00:00:00Z";
  h.open("a"); await initial(h, "a", result("a", "Kota dolmadan kaydedilmiş rapor"));
  const regenerate = h.findButton("Yeniden hazırla");
  assert.equal(regenerate.disabled, true);
  await regenerate.dispatch("click");
  assert.equal(h.posts.length, 0, "Kota doluyken düğme boşuna model isteği göndermemeli.");
  assert.match(h.container.textContent, /Kota dolmadan kaydedilmiş rapor/);
  assert.match(h.container.textContent, /2026-10-27T00:00:00Z/);
  assert.match(h.container.textContent, /Kayıtlı raporları okumak ve indirmek sınırdan etkilenmez/);
  assert.equal(h.container.querySelector('[data-insight-action="download"]').tagName, "A");
  assert.equal(h.container.querySelector('[data-insight-action="download"]').href, "/api/items/a/ai/download?kind=summary");
  await h.findButton("Kaynakları karşılaştır").dispatch("click");
  assert.equal(h.findButton("Karşılaştırmalı rapor hazırla").disabled, true);
  await h.findButton("Makale metni").dispatch("click");
  assert.equal(h.findButton("Makale metnini al").disabled, false, "Bağımsız makale okuma bütçesi model kotasından etkilenmemeli.");
  assert.doesNotMatch(h.container.textContent, /Bugünkü model denemesi sınırına/);
});

test("NVIDIA kotası dolsa da bağımsız Claude karşılaştırması kullanılabilir", async () => {
  const h = harness(); h.state.data.ai.daily_remaining = 0; h.state.data.ai.deep_daily_remaining = 4;
  h.open("a"); await initial(h, "a");
  assert.equal(h.findButton("Yeniden hazırla").disabled, true);
  await h.findButton("Kaynakları karşılaştır").dispatch("click");
  assert.equal(h.findButton("Karşılaştırmalı rapor hazırla").disabled, false);
  assert.match(h.container.textContent, /Claude, havuzdaki yayınları/);
  assert.doesNotMatch(h.container.textContent, /Bugünkü NVIDIA denemesi sınırına/);
});

test("Claude kurulumu veya kotası NVIDIA özetini etkilemez ve yanlış sağlayıcı uyarısı gösterilmez", async () => {
  const h = harness(); h.state.data.ai.daily_remaining = 8; h.state.data.ai.deep_configured = false; h.state.data.ai.deep_daily_remaining = 0;
  h.open("a"); await initial(h, "a");
  assert.equal(h.findButton("Yeniden hazırla").disabled, false);
  await h.findButton("Kaynakları karşılaştır").dispatch("click");
  assert.equal(h.findButton("Karşılaştırmalı rapor hazırla").disabled, true);
  assert.match(h.container.textContent, /Claude bağlantısı/); assert.match(h.container.textContent, /Bugünkü Claude denemesi sınırına/);
  assert.doesNotMatch(h.container.textContent, /NVIDIA bağlantısı/);
});
