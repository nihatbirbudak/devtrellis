// Kişisel arayüzde taslakları, sürüm çakışmalarını ve gerçek işlem sırasını sahte DOM ile doğrular.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const translations = require("../radar/static/ui-text.js");
translations.register(require("../radar/static/legacy-text.js"));

const source = fs.readFileSync(path.join(__dirname, "..", "radar", "static", "workspace.js"), "utf8");
const copy = value => JSON.parse(JSON.stringify(value));
const turn = () => new Promise(resolve => setImmediate(resolve));

function harness(options = {}) {
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
      this.children.forEach(node => { node.parentElement = null; });
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
      const matches = node => selectors.some(value => value.startsWith(".") ? node.className.split(" ").includes(value.slice(1)) : value.startsWith("#") ? node.id === value.slice(1) : value === "[data-focus-key]" ? Boolean(node.dataset.focusKey) : node.tagName.toLowerCase() === value);
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
    close() { this.open = false; }
    reportValidity() {
      return this.querySelectorAll("input,select,textarea").every(node => !node.required || Boolean(node.value));
    }
    async dispatch(name) {
      if (name === "click" && this.disabled) return;
      const event = {preventDefault() {}, target: this};
      await Promise.all((this.listeners.get(name) || []).map(listener => listener(event)));
      await turn();
    }
  }
  document = {body: new Node("body"), activeElement: null,
    getElementById(id) { return this.body.querySelector(`#${id}`); },
    querySelectorAll(selector) { return this.body.querySelectorAll(selector); }
  };
  document.activeElement = document.body;
  for (const [tag, id] of [["h2", "view-title"], ["input", "search"], ["select", "source-filter"], ["input", "show-archive"]]) {
    const node = new Node(tag);
    node.id = id;
    document.body.append(node);
  }
  const settings = {version: 0, daily_limit: 8, topic_weights: {jarvis: 3, ai: 3, robotik: 3, tarim: 3, medya: 3}, keywords: [], muted_keywords: []};
  const collection = {id: "c1", name: "Sera", description: "Denenecek fikirler", version: 1, archived: false, item_ids: [], item_count: 0};
  const sourceRecord = {id: "s1", name: "Resmî yayın", enabled: true, interval_hours: 48, preference_version: 3, can_retry: true, last_error: "Bir önceki istek alınamadı"};
  const state = {view: "all", search: "robot kolu", topic: "robotik", sourceId: "s1", collectionId: "", readStatus: "all", sort: "relevance", showArchive: true,
    data: {items: [{id: "i1", title: "Robot kolu"}], sources: [sourceRecord], stats: {total: 7}, personal: {settings, collections: [collection], saved_searches: [], read_states: [], stats: {read_count: 0, unread_count: 7, collection_count: 1, saved_search_count: 0}}}};
  const posts = [];
  const requests = [];
  const messages = [];
  const observations = {renders: 0, invalidations: 0, refreshes: 0, views: []};
  const handlers = {post: async () => { throw new Error("Beklenmedik POST"); }, request: async () => { throw new Error("Beklenmedik GET"); }, refresh: async () => true};
  const element = (tag, className, text) => new Node(tag, className, text);
  const context = {element, icon: () => new Node("svg"), state, topics: {jarvis: "Ev otomasyonu", ai: "Yapay zekâ", robotik: "Robotik", tarim: "Tarım", medya: "Medya üretimi"},
    post: async (url, payload) => { posts.push({url, payload: copy(payload)}); return handlers.post(url, payload); },
    request: async url => { requests.push(url); return handlers.request(url); },
    refreshAfterMutation: async () => { observations.refreshes++; return handlers.refresh(); },
    toast: (message, error) => messages.push({message, error}), formatDate: value => value || "Bilinmiyor",
    render: () => { observations.renders++; }, invalidatePool: () => { observations.invalidations++; },
    changeView: view => { state.view = view; observations.views.push(view); }, findItem: id => state.data.items.find(item => item.id === id), openReader() {}};
  const windowListeners = new Map();
  if (options.topics) context.topics = options.topics;
  const sandbox = vm.createContext({window: {addEventListener(name, listener) { windowListeners.set(name, listener); }}, document,
    ...(options.language ? {UfukUI: {language: options.language, t: translations.createTranslator(options.language)}} : {})});
  vm.runInContext(source, sandbox);
  const api = sandbox.window.UFUKWorkspaceFactory(context);
  const content = element("div");
  document.body.append(content);
  function findButton(label, root = document.body) {
    const node = root.querySelectorAll("button").find(button => button.textContent === label);
    assert.ok(node, `Düğme bulunmalı: ${label}`);
    return node;
  }
  function control(label, root = document.body) {
    const caption = root.querySelectorAll("label").find(node => node.textContent === label);
    assert.ok(caption, `Alan bulunmalı: ${label}`);
    return document.getElementById(caption.htmlFor);
  }
  return {api, state, settings, collection, sourceRecord, handlers, posts, requests, messages, observations, document, content, element, findButton, control, windowListeners,
    dialog: () => document.body.querySelector("dialog"), submit: async () => { await document.body.querySelector("form").dispatch("submit"); }};
}

test("düzenleme taslağı Escape ile sessizce kaybolmaz; sürerken ikinci modal açılmaz", async () => {
  const h = harness();
  h.api.renderSettings(h.content);
  await h.findButton("İlgi alanlarını düzenle").dispatch("click");
  h.control("Öne çıkarmak istediğin sözcükler").value = "sesli asistan";
  assert.equal(h.api.busy(), true);
  await h.dialog().dispatch("cancel");
  assert.equal(h.dialog().open, true);
  assert.equal(h.dialog().querySelector(".workspace-discard").hidden, false);
  await h.findButton("Düzenlemeye devam et").dispatch("click");
  assert.equal(h.control("Öne çıkarmak istediğin sözcükler").value, "sesli asistan");
  await h.findButton("İlgi alanlarını düzenle").dispatch("click");
  assert.equal(h.document.body.querySelectorAll("dialog").length, 1);
  await h.dialog().dispatch("cancel");
  await h.findButton("Kaydetmeden çık").dispatch("click");
  assert.equal(h.dialog(), null);
  assert.equal(h.api.busy(), false);
  assert.equal(h.posts.length, 0);
});

test("tarayıcı yenileme koruması yalnız açık taslak veya süren form işlemi için çalışır", async () => {
  const h = harness();
  const prevented = () => {
    let count = 0;
    h.windowListeners.get("beforeunload")({preventDefault() { count++; }});
    return count;
  };
  assert.equal(prevented(), 0);
  h.api.renderSettings(h.content);
  await h.findButton("İlgi alanlarını düzenle").dispatch("click");
  assert.equal(prevented(), 0);
  h.control("Öne çıkarmak istediğin sözcükler").value = "sera";
  assert.equal(prevented(), 1);
  await h.dialog().dispatch("cancel");
  await h.findButton("Kaydetmeden çık").dispatch("click");
  assert.equal(prevented(), 0);
});

test("kaynak 409 taslağı korur, karşılaştırmadan sürüm değiştirmez ve açık seçimle yeniden kaydeder", async () => {
  const h = harness();
  h.content.append(h.api.sourceControls(h.sourceRecord));
  await h.findButton("Takip ayarları").dispatch("click");
  h.control("Kontrol aralığı (saat)").value = "72";
  h.handlers.post = async () => { throw Object.assign(new Error("Değişti"), {status: 409}); };
  await h.submit();
  assert.equal(h.posts[0].payload.expected_version, 3);
  assert.equal(h.control("Kontrol aralığı (saat)").value, "72");
  assert.equal(h.findButton("Tercihleri kaydet").disabled, true);
  const latest = {...h.sourceRecord, interval_hours: 96, preference_version: 4};
  h.handlers.request = async () => latest;
  await h.findButton("Güncel kaydı karşılaştır").dispatch("click");
  assert.deepEqual(h.requests, ["/api/sources/s1"]);
  assert.match(h.dialog().querySelector(".workspace-comparison").textContent, /96 saat/);
  assert.equal(h.control("Kontrol aralığı (saat)").value, "72");
  assert.equal(h.posts.length, 1);
  await h.findButton("Taslağı koru, bu sürümle devam et").dispatch("click");
  h.handlers.post = async () => ({...latest, interval_hours: 72, preference_version: 5});
  await h.submit();
  assert.equal(h.posts[1].payload.expected_version, 4);
  assert.equal(h.posts[1].payload.interval_hours, 72);
  assert.equal(h.dialog(), null);
  assert.equal(h.state.data.sources[0].preference_version, 5);
  assert.equal(h.api.busy(), false);
});

test("POST ve onu izleyen GET boyunca form kilitli kalır; iki kayıt isteği gönderilmez", async () => {
  const h = harness();
  h.api.renderSettings(h.content);
  await h.findButton("İlgi alanlarını düzenle").dispatch("click");
  let completePost;
  let completeRefresh;
  h.handlers.post = () => new Promise(resolve => { completePost = resolve; });
  h.handlers.refresh = () => new Promise(resolve => { completeRefresh = resolve; });
  await h.submit();
  await h.submit();
  assert.equal(h.posts.length, 1);
  assert.equal(h.findButton("Tercihleri kaydet").disabled, true);
  await h.dialog().dispatch("cancel");
  assert.ok(h.dialog());
  completePost({...h.settings, version: 1});
  await turn();
  assert.equal(h.observations.refreshes, 1);
  assert.equal(h.findButton("Tercihleri kaydet").disabled, true);
  await h.submit();
  assert.equal(h.posts.length, 1);
  completeRefresh(false);
  await turn();
  assert.equal(h.dialog(), null);
  assert.equal(h.api.busy(), false);
  assert.match(h.messages.at(-1).message, /kaydın saklandı/);
  assert.equal(h.state.data.personal.settings.version, 1);
});

test("ayar formu sözcükleri böler, tekrarları kaldırır ve 20 ifade sınırını aşan yazıyı göndermeden korur", async () => {
  const h = harness();
  h.api.renderSettings(h.content);
  await h.findButton("İlgi alanlarını düzenle").dispatch("click");
  const words = h.control("Öne çıkarmak istediğin sözcükler");
  words.value = Array.from({length: 21}, (_, index) => `ifade ${index}`).join("\n");
  await h.submit();
  assert.equal(h.posts.length, 0);
  assert.match(h.dialog().querySelector(".workspace-error").textContent, /20 ifade/);
  words.value = "sesli asistan, robot\nrobot\n sera ";
  h.control("Günlük seçkideki gelişme sayısı").value = "12";
  h.control("Yapay zekâ").value = "0";
  h.handlers.post = async (url, payload) => ({...payload, version: 1});
  await h.submit();
  assert.deepEqual(h.posts[0].payload.keywords, ["sesli asistan", "robot", "sera"]);
  assert.equal(h.posts[0].payload.topic_weights.ai, 0);
  assert.equal(h.posts[0].payload.daily_limit, 12);
  assert.equal(h.posts[0].payload.expected_version, 0);
});

test("koleksiyon üyeliği sürüm artırarak eklenir ve çıkarılır; haberin kendisi korunur", async () => {
  const h = harness();
  h.content.append(h.api.readerControls(h.state.data.items[0]));
  await h.findButton("Koleksiyonlar").dispatch("click");
  h.handlers.post = async (url, payload) => ({...h.collection, version: payload.expected_version + 1, item_ids: payload.remove ? [] : ["i1"], item_count: payload.remove ? 0 : 1});
  h.findButton("Ekle").focus();
  await h.findButton("Ekle").dispatch("click");
  assert.deepEqual(h.posts[0], {url: "/api/collections/c1/items", payload: {item_ids: ["i1"], remove: false, expected_version: 1}});
  assert.match(h.dialog().textContent, /Bu gelişme koleksiyonda/);
  assert.equal(h.document.activeElement, h.findButton("Çıkar"));
  await h.findButton("Çıkar").dispatch("click");
  assert.equal(h.posts[1].payload.expected_version, 2);
  assert.equal(h.posts[1].payload.remove, true);
  assert.equal(h.state.data.items.length, 1);
  assert.equal(h.state.data.personal.collections[0].version, 3);
  assert.equal(h.document.activeElement, h.findButton("Ekle"));
  await h.dialog().dispatch("cancel");
  assert.equal(h.dialog(), null);
});

test("arşivlenen koleksiyon kapalı arşive taşındığında odak görünür başlığa döner", async () => {
  const h = harness();
  h.state.view = "collections";
  h.api.renderCollections(h.content);
  h.handlers.post = async () => ({...h.collection, archived: true, version: 2});
  h.handlers.refresh = async () => {
    h.content.replaceChildren();
    h.api.renderCollections(h.content);
    h.document.activeElement = h.document.body;
    return true;
  };
  h.findButton("Arşivle").focus();
  await h.findButton("Arşivle").dispatch("click");
  assert.equal(h.document.activeElement, h.document.getElementById("view-title"));
  assert.ok(h.findButton("Arşivden çıkar"));
});

test("işlem sürerken başka alana geçen kullanıcının odağı geri çekilmez", async () => {
  const h = harness();
  h.api.renderCollections(h.content);
  let complete;
  h.handlers.post = () => new Promise(resolve => { complete = resolve; });
  h.findButton("Arşivle").focus();
  const pending = h.findButton("Arşivle").dispatch("click");
  await turn();
  h.document.getElementById("search").focus();
  complete({...h.collection, archived: true, version: 2});
  await pending;
  assert.equal(h.document.activeElement, h.document.getElementById("search"));
});

test("üyelik çakışması otomatik tekrar yazmaz; güncel üyelik kullanıcı isteğiyle alınır", async () => {
  const h = harness();
  h.content.append(h.api.readerControls(h.state.data.items[0]));
  await h.findButton("Koleksiyonlar").dispatch("click");
  h.handlers.post = async () => { throw Object.assign(new Error("Değişti"), {status: 409}); };
  await h.findButton("Ekle").dispatch("click");
  assert.equal(h.posts.length, 1);
  assert.match(h.dialog().querySelector(".workspace-error").textContent, /İşlem uygulanmadı/);
  h.handlers.request = async () => ({collections: [{...h.collection, version: 9, item_ids: ["i1"], item_count: 1}]});
  await h.findButton("Üyelikleri güncelle").dispatch("click");
  assert.equal(h.posts.length, 1);
  assert.ok(h.findButton("Çıkar"));
  h.handlers.post = async () => ({...h.collection, version: 10});
  await h.findButton("Çıkar").dispatch("click");
  assert.equal(h.posts[1].payload.expected_version, 9);
});

test("kayıtlı arama koleksiyon, okundu ve sıralama dahil tam filtreyi saklar ve yeniden açar", async () => {
  const h = harness();
  h.state.collectionId = "c1";
  h.state.readStatus = "read";
  h.state.sort = "oldest";
  h.content.append(h.api.poolControls());
  await h.findButton("Aramayı kaydet").dispatch("click");
  h.control("Aramanın adı").value = "Robot araştırması";
  h.handlers.post = async (url, payload) => ({...payload, id: "search1", version: 1, archived: false});
  await h.submit();
  assert.deepEqual(h.posts[0], {url: "/api/searches", payload: {name: "Robot araştırması", query: "robot kolu", topic: "robotik", source_id: "s1", collection_id: "c1", read_status: "read", sort: "oldest"}});
  h.content.replaceChildren();
  h.api.renderCollections(h.content);
  h.state.search = "değişmiş";
  h.state.collectionId = "";
  h.state.readStatus = "all";
  h.state.sort = "relevance";
  await h.findButton("Aramayı aç").dispatch("click");
  assert.equal(h.state.search, "robot kolu");
  assert.equal(h.document.getElementById("search").value, "robot kolu");
  assert.equal(h.state.collectionId, "c1");
  assert.equal(h.state.readStatus, "read");
  assert.equal(h.state.sort, "oldest");
});

test("eski kayıtlı aramada yeni filtreler yoksa önceki görünümün filtreleri taşınmaz", async () => {
  const h = harness();
  h.state.collectionId = "c1";
  h.state.readStatus = "read";
  h.state.sort = "oldest";
  h.state.data.personal.saved_searches.push({id: "old", name: "Eski arama", query: "ses", topic: null, source_id: null});
  h.api.renderCollections(h.content);
  await h.findButton("Aramayı aç").dispatch("click");
  assert.equal(h.state.collectionId, "");
  assert.equal(h.state.readStatus, "all");
  assert.equal(h.state.sort, "relevance");
  assert.equal(h.state.topic, "all");
});

test("okundu yazısı yerel görünümü günceller; yeniden basıldığında okunmadı gönderir", async () => {
  const h = harness();
  h.content.append(h.api.readerControls(h.state.data.items[0]));
  h.handlers.post = async (url, payload) => ({item_id: "i1", read: payload.read, updated_at: "2026-10-26T00:00:00Z"});
  await h.findButton("Okundu olarak işaretle").dispatch("click");
  assert.equal(h.state.data.personal.stats.read_count, 1);
  assert.equal(h.state.data.personal.stats.unread_count, 6);
  await h.findButton("Okunmadı olarak işaretle").dispatch("click");
  assert.equal(h.posts[1].payload.read, false);
  assert.equal(h.state.data.personal.stats.read_count, 0);
});

test("kaynak ekleme boş dalı reddeder ve doğrulama hatasında form değerlerini korur", async () => {
  const h = harness();
  h.content.append(h.api.sourceToolbar());
  await h.findButton("Kaynak ekle").dispatch("click");
  h.control("Kaynak adı").value = "Yeni yayın";
  h.control("Besleme adresi").value = "https://ornek.org/feed.xml";
  h.control("Yayıncı").value = "Örnek yayıncı";
  h.control("Dallar").value = "x";
  await h.submit();
  assert.equal(h.posts.length, 0);
  h.control("Dallar").value = "Ses, Kurgu";
  h.handlers.post = async () => { throw Object.assign(new Error("Besleme erişime uygun değil"), {status: 422}); };
  await h.submit();
  assert.equal(h.posts[0].payload.fetch_url, "https://ornek.org/feed.xml");
  assert.deepEqual(h.posts[0].payload.branches, ["Ses", "Kurgu"]);
  assert.equal(h.control("Kaynak adı").value, "Yeni yayın");
  assert.equal(h.dialog().querySelector(".workspace-error").hidden, false);
  assert.equal(h.document.activeElement, h.dialog().querySelector(".workspace-error"));
  assert.equal(h.findButton("Kaynağı ekle").disabled, false);
});

test("eklenen kaynak önceki arama ve dal filtrelerinin arkasında gizlenmez", async () => {
  const h = harness();
  Object.assign(h.state, {view: "sources", topic: "tarim", search: "AGROS", sourceBranch: "Sera", sourceLanguage: "tr", sourceKind: "html", sourceHealth: "error"});
  h.content.append(h.api.sourceToolbar());
  await h.findButton("Kaynak ekle").dispatch("click");
  h.control("Kaynak adı").value = "Python duyuruları";
  h.control("Besleme adresi").value = "https://example.org/feed.xml";
  h.control("Yayıncı").value = "Python ekibi";
  h.control("Dallar").value = "Yazılım";
  h.handlers.post = async (url, payload) => ({...payload, id: "python", preference_version: 0});
  h.handlers.refresh = async () => false;
  await h.submit();
  assert.equal(h.state.view, "sources");
  assert.equal(h.state.search, "Python duyuruları");
  assert.equal(h.document.getElementById("search").value, "Python duyuruları");
  assert.equal(h.state.topic, "all");
  for (const key of ["sourceBranch", "sourceLanguage", "sourceKind", "sourceHealth"]) assert.equal(h.state[key], "");
  assert.ok(h.state.data.sources.some(row => row.id === "python"));
  assert.doesNotMatch(h.messages.at(-1).message, /kontrol başladı/i);
});

test("yeniden deneme hazır değilken gönderilmez; sıraya alındığı dürüstçe gösterilir", async () => {
  const h = harness();
  h.content.append(h.api.sourceControls({...h.sourceRecord, can_retry: false}));
  await h.findButton("Bir kez yeniden dene").dispatch("click");
  assert.equal(h.posts.length, 0);
  h.content.replaceChildren(h.api.sourceControls(h.sourceRecord));
  h.handlers.post = async () => ({source: {...h.sourceRecord, retry_pending: true, can_retry: false}, queued: true, started: false});
  await h.findButton("Bir kez yeniden dene").dispatch("click");
  assert.equal(h.posts[0].payload.expected_version, 3);
  assert.equal(h.state.data.sources[0].retry_pending, true);
  assert.match(h.messages.at(-1).message, /sıraya alındı/);
});

test("arşiv 409 ardından GET hatası varsa güncel kaydın yüklendiği iddia edilmez", async () => {
  const h = harness();
  h.api.renderCollections(h.content);
  h.handlers.post = async () => { throw Object.assign(new Error("Değişti"), {status: 409}); };
  h.handlers.refresh = async () => false;
  await h.findButton("Arşivle").dispatch("click");
  assert.match(h.messages.at(-1).message, /Güncel bilgiler alınamadı/);
  assert.equal(h.api.busy(), false);
  assert.equal(h.findButton("Arşivle").disabled, false);
});

test("bağlantı paneli kimlik varlığını gerçek erişim gibi sunmaz ve kontrolü model POST'u başlatmaz", async () => {
  const h = harness();
  h.state.data.ai = {configured: true, model: "nvidia/örnek", daily_used: 3, daily_limit: 20, daily_remaining: 17, budget_resets_at: "2026-10-27T00:00:00Z"};
  h.state.data.reading = {daily_used: 4, daily_limit: 20, daily_remaining: 16};
  h.api.renderSettings(h.content);
  assert.match(h.content.textContent, /Kimlik bilgisi hazır/);
  assert.match(h.content.textContent, /erişimi işlem sırasında doğrulanır/);
  assert.match(h.content.textContent, /3 \/ 20/);
  assert.match(h.content.textContent, /4 \/ 20/);
  await h.findButton("Durumu yenile").dispatch("click");
  assert.equal(h.observations.refreshes, 1);
  assert.equal(h.posts.length, 0);
  assert.equal(h.api.busy(), false);
});

test("kullanıcı adı ve arama metni HTML olarak yorumlanmaz", () => {
  const h = harness();
  h.state.data.personal.collections[0].name = '<img src=x onerror="alert(1)">';
  h.state.data.personal.saved_searches.push({id: "evil", name: "<script>alert(1)</script>", query: "<b>aranan</b>", topic: null, source_id: null});
  h.api.renderCollections(h.content);
  assert.match(h.content.textContent, /<img src=x/);
  assert.match(h.content.textContent, /<script>alert/);
  assert.equal(h.content.querySelectorAll("img,script,b").length, 0);
  assert.equal(source.includes("innerHTML"), false);
});

test("NVIDIA ve Claude sayaçları ve çalışan işi ayrı görünür; oturum bilgisi araştırma garantisi sayılmaz", () => {
  const h = harness();
  h.state.data.ai = {configured: true, model: "nvidia/test", daily_used: 18, daily_limit: 80, daily_remaining: 62, deep_configured: true, deep_model: "claude-opus", deep_daily_used: 3, deep_daily_limit: 12, deep_daily_remaining: 9, running: true, kind: "deep"};
  h.api.renderSettings(h.content);
  const cards = h.content.querySelectorAll(".workspace-status-card");
  const nvidia = cards.find(node => node.textContent.includes("NVIDIA ·")); const claude = cards.find(node => node.textContent.includes("Claude ·"));
  assert.match(nvidia.textContent, /18 \/ 80/); assert.doesNotMatch(nvidia.textContent, /3 \/ 12/);
  assert.match(claude.textContent, /3 \/ 12/); assert.match(claude.textContent, /claude-opus/);
  assert.match(claude.textContent, /model ve web erişimi araştırma sırasında sınanır/);
  assert.match(claude.textContent, /Bir işlem sürüyor/); assert.doesNotMatch(nvidia.textContent, /Bir işlem sürüyor/);
  assert.match(claude.textContent, /abonelik veya kullanım koşullarından ayrıdır/);
  assert.equal(h.posts.length, 0);
});

test("İngilizce ayarlar sekiz API konusunu gösterir; konu kimlikleri kayıtta çevrilmez", async () => {
  const topics = {jarvis: "Home automation", ai: "AI", robotik: "Robotics", tarim: "Agriculture", medya: "Media", software: "Software", security: "Security", data: "Data"};
  const h = harness({language: "en", topics});
  h.state.data.ai = {deep_provider: "codex", deep_configured: true, deep_model: "local-config", deep_daily_limit: 12, deep_daily_used: 1};
  h.api.renderSettings(h.content);
  assert.match(h.content.textContent, /A selection for you/);
  assert.match(h.content.textContent, /Codex · evaluation and deep research/);
  assert.match(h.content.textContent, /Codex analyzes supplied sources/);
  await h.findButton("Edit interests").dispatch("click");
  assert.equal(h.dialog().querySelectorAll(".workspace-weights")[0].querySelectorAll("select").length, 8);
  h.control("Security").value = "5";
  h.control("Keywords to prioritize").value = "Kaydet <img src=x>";
  h.handlers.post = async (url, payload) => ({...h.settings, ...payload, version: h.settings.version + 1});
  await h.submit();
  assert.equal(h.posts[0].payload.topic_weights.security, 5);
  assert.equal(Object.keys(h.posts[0].payload.topic_weights).length, 8);
  assert.deepEqual(h.posts[0].payload.keywords, ["Kaydet <img src=x>"]);
});

test("iki dilde sabit koleksiyon etiketleri çevrilir; aynı sözcüklü kullanıcı metni özgün kalır", () => {
  for (const language of ["en", "tr"]) {
    const h = harness({language});
    h.collection.name = "Kaydet";
    h.collection.description = "<script>UFUK kullanıcısının özgün notu</script>";
    h.api.renderCollections(h.content);
    assert.match(h.content.textContent, /Kaydet/);
    assert.match(h.content.textContent, /<script>UFUK kullanıcısının özgün notu<\/script>/);
    assert.equal(h.content.querySelectorAll("script").length, 0);
    h.findButton(language === "en" ? "New collection" : "Yeni koleksiyon");
  }
});
