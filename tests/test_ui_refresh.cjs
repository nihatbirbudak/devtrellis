// Kullanıcı yazısından sonra eski okumanın yeni tercih gibi gösterilmediğini doğrular.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const t = (key, values = {}) => key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);

const source = fs.readFileSync(path.join(__dirname, "..", "radar", "static", "app.js"), "utf8");
const refreshStart = source.indexOf("  function pageVisible()");
const refreshEnd = source.indexOf("  function renderConnectionError", refreshStart);
assert.ok(refreshStart >= 0 && refreshEnd > refreshStart, "Gerçek arayüz yenileme işlevleri bulunmalı.");
const refreshCode = source.slice(refreshStart, refreshEnd);

test("sessiz kelimeli yeni kart eski fakat ilgili kartı seçkide geçmez", () => {
  const start = source.indexOf("  function publicationAge(");
  const end = source.indexOf("  function displayTitle(", start);
  assert.ok(start >= 0 && end > start, "Sıralama testi gerçek seçki işlevini kullanmalı.");
  const now = Date.parse("2026-10-26T12:00:00Z");
  const context = vm.createContext({
    t,
    Date: class extends Date { static now() { return now; } },
    dateValue: value => value ? new Date(value) : null,
    feedback: item => item.feedback || "yok", snoozed: () => false,
    state: {data: {personal: {settings: {daily_limit: 4}, read_states: []}}},
  });
  vm.runInContext(source.slice(start, end), context);
  const olderRelevant = {id: "ilgili", topic: "ai", source_id: "eski-kaynak", score: 80,
    personal_score: 180, personal_rank: {topic_weight: 5}, summary_status: "editor_ozeti", published_at: "2026-08-26T12:00:00Z", is_archive: true};
  const freshMuted = {id: "sessiz", topic: "ai", source_id: "yeni-kaynak", score: 100,
    personal_score: -900, personal_rank: {topic_weight: 5}, summary_status: "editor_ozeti", published_at: "2026-10-26T10:00:00Z", is_archive: false};
  const result = context.selectedItems([freshMuted, olderRelevant]);
  assert.deepEqual(Array.from(result, item => item.id), ["ilgili", "sessiz"],
    "Yayın tazeliği sessiz kelime tercihinin önüne geçmemeli; düşük puanlı kayıt da havuzdan silinmemeli.");
});

function snapshot(values = ["yok"]) {
  return { items: values.map((feedback, index) => ({ id: String(index), feedback })), sources: [], jobs: [], collection: { running: false } };
}

function harness(initial = null) {
  const requests = [];
  const errors = [];
  const timers = new Map();
  const listeners = new Map();
  const observations = { renders: 0, invalidations: 0, notices: [] };
  let timerId = 0;
  function node(tag, className, text) {
    return { tag, className, text, children: [], listeners: new Map(),
      append(...children) { this.children.push(...children); },
      remove() { this.removed = true; },
      setAttribute() {},
      addEventListener(event, listener) { this.listeners.set(event, listener); },
    };
  }
  const context = vm.createContext({
    t,
    state: { data: initial, pending: new Set() }, fetchInFlight: null, itemCache: new Map(), errorBox: {},
    disposed: false, pollTimer: null, toastTimer: null, searchTimer: null, writeEpoch: 0, freshnessNotice: null,
    readerDialog: { open: false }, jobDialog: { open: false }, discovery: null, sourceLearning: null,
    document: { visibilityState: "visible", activeElement: { tagName: "BODY" },
      addEventListener(event, listener) { listeners.set(`document:${event}`, listener); } },
    window: { getSelection() { return { isCollapsed: true }; },
      addEventListener(event, listener) { listeners.set(`window:${event}`, listener); } },
    request(url) {
      return new Promise((resolve, reject) => requests.push({ url, resolve, reject }));
    },
    invalidatePool() { observations.invalidations++; }, render() { observations.renders++; },
    clearTimeout(id) { timers.delete(id); },
    setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId; },
    element: node, icon() { return node("svg"); },
    content: { setAttribute() {}, before(notice) { observations.notices.push(notice); } },
    renderConnectionError(message) { errors.push(message); },
    showError(message) { errors.push(message); },
  });
  vm.runInContext(refreshCode, context);
  const eventsStart = source.indexOf('  document.addEventListener("visibilitychange"');
  const eventsEnd = source.indexOf("  async function openResearchEntry()");
  assert.ok(eventsStart >= 0 && eventsEnd > eventsStart);
  vm.runInContext(source.slice(eventsStart, eventsEnd), context);
  return { context, requests, errors, timers, listeners, observations };
}

async function turn() { await new Promise(resolve => setImmediate(resolve)); }

test("olağan paralel okumalar aynı isteği paylaşır", async () => {
  const { context, requests } = harness();
  const first = context.refreshState();
  const second = context.refreshState();
  assert.equal(requests.length, 1);
  requests[0].resolve(snapshot());
  assert.deepEqual(await Promise.all([first, second]), [true, true]);
});

test("POST öncesi başlayan okuma bitince yeni bir GET yapılır", async () => {
  const { context, requests } = harness();
  const oldRead = context.refreshState();
  let finished = false;
  const afterWrite = context.refreshAfterMutation().then(result => { finished = true; return result; });
  requests[0].resolve(snapshot(["yok"]));
  await oldRead;
  await turn();
  assert.equal(requests.length, 2);
  assert.equal(finished, false, "Eski okuma yazının görünür olduğu anlamına gelmez.");
  assert.equal(requests[1].url, "/api/state");
  requests[1].resolve(snapshot(["yararli"]));
  assert.equal(await afterWrite, true);
  assert.equal(context.state.data.items[0].feedback, "yararli");
});

test("aynı eski okumayı bekleyen iki yazı sonrasında ortak taze okuma yeterlidir", async () => {
  const { context, requests } = harness();
  const oldRead = context.refreshState();
  const firstWrite = context.refreshAfterMutation();
  const secondWrite = context.refreshAfterMutation();
  requests[0].resolve(snapshot(["yok", "yok"]));
  await oldRead;
  await turn();
  assert.equal(requests.length, 2);
  requests[1].resolve(snapshot(["yararli", "sonra"]));
  assert.deepEqual(await Promise.all([firstWrite, secondWrite]), [true, true]);
  assert.deepEqual(context.state.data.items.map(item => item.feedback), ["yararli", "sonra"]);
});

test("ikinci yazı ilk yazının okumasından sonra gelirse bir okuma daha gerekir", async () => {
  const { context, requests } = harness();
  const firstWrite = context.refreshAfterMutation();
  const secondWrite = context.refreshAfterMutation();
  requests[0].resolve(snapshot(["yararli", "yok"]));
  await firstWrite;
  await turn();
  assert.equal(requests.length, 2);
  requests[1].resolve(snapshot(["yararli", "sonra"]));
  assert.equal(await secondWrite, true);
  assert.deepEqual(context.state.data.items.map(item => item.feedback), ["yararli", "sonra"]);
});

test("önceki okumanın hatası yazı sonrası taze okumayı engellemez", async () => {
  const { context, requests, errors } = harness();
  const oldRead = context.refreshState();
  const afterWrite = context.refreshAfterMutation();
  requests[0].reject(new Error("Geçici bağlantı hatası"));
  assert.equal(await oldRead, false);
  await turn();
  assert.equal(requests.length, 2);
  requests[1].resolve(snapshot(["yararli"]));
  assert.equal(await afterWrite, true);
  assert.equal(context.state.data.items[0].feedback, "yararli");
  assert.equal(errors.length, 1);
});

test("yazı sonrası okuma hatası eski görünümü başarılı saymaz ve yeniden denenebilir", async () => {
  const initial = snapshot(["yok"]);
  const { context, requests, errors } = harness(initial);
  const afterWrite = context.refreshAfterMutation();
  requests[0].reject(new Error("Yerel sunucu yanıt vermedi"));
  assert.equal(await afterWrite, false);
  assert.equal(context.state.data, initial);
  assert.match(errors[0], /Son alınan bilgiler gösteriliyor/);
  const retry = context.refreshAfterMutation();
  assert.equal(requests.length, 2);
  requests[1].resolve(snapshot(["yararli"]));
  assert.equal(await retry, true);
});

test("not kaydının POST sonucu eski havuz okuması beklenirken geri alınmaz", async () => {
  const { context, requests } = harness();
  const saveStart = source.indexOf("  async function saveJob(status)");
  const saveEnd = source.indexOf("  function renderJob()", saveStart);
  assert.ok(saveStart >= 0 && saveEnd > saveStart);
  const saved = { id: "job", item_id: "0", version: 1, revision: 1, note: "Kaydedilmiş inceleme notu", status: "bekliyor", notes: [] };
  context.jobSession = { id: "job", data: { version: 0, revision: 1, note: "" }, draft: "Kaydedilmiş inceleme notu", saving: false, loading: false, conflict: false };
  context.document = { getElementById() { return {}; } };
  context.post = async () => saved;
  context.validJob = value => value === saved;
  context.renderJob = () => {};
  vm.runInContext(source.slice(saveStart, saveEnd), context);
  const oldRead = context.refreshState();
  const save = context.saveJob("bekliyor");
  await turn();
  assert.equal(context.jobSession.draft, saved.note);
  assert.equal(context.jobSession.data.version, 1);
  assert.equal(context.jobSession.saving, true);
  requests[0].resolve(snapshot());
  await oldRead;
  await turn();
  assert.equal(requests.length, 2);
  assert.equal(context.jobSession.draft, saved.note);
  requests[1].resolve(snapshot());
  await save;
  assert.equal(context.jobSession.saving, false);
  assert.equal(context.jobSession.data.version, 1);
});

test("görünür ve boşta sayfa yeni havuzu alır ve bir dakika sonrasını planlar", async () => {
  const { context, requests, timers, observations } = harness(snapshot());
  const refresh = context.refreshState(true);
  requests[0].resolve(snapshot(["yararli"]));
  assert.equal(await refresh, true);
  assert.equal(context.state.data.items[0].feedback, "yararli");
  assert.equal(observations.renders, 1);
  assert.equal(observations.invalidations, 1);
  assert.equal([...timers.values()][0].delay, 60000);
});

test("aynı havuz yeniden çizilmez ve yüklenmiş sayfalar sıfırlanmaz", async () => {
  const values = Array.from({ length: 200 }, () => "yok");
  const { context, requests, timers, observations } = harness(snapshot(values));
  for (let index = 0; index < 2; index++) {
    const refresh = context.refreshState(true);
    requests[index].resolve(snapshot(values));
    assert.equal(await refresh, true);
  }
  assert.equal(observations.renders, 0);
  assert.equal(observations.invalidations, 0);
  assert.equal(timers.size, 1);
});

test("yalnız yedek durumu değişince havuz sayfalaması sıfırlanmaz", async () => {
  const { context, requests, observations } = harness(snapshot());
  const refresh = context.refreshState(true);
  requests[0].resolve({ ...snapshot(), backup: { available: true, last_success_at: "2026-10-26T04:00:00Z" } });
  await refresh;
  assert.equal(observations.renders, 1);
  assert.equal(observations.invalidations, 0);
});

for (const [name, guard] of [
  ["okuyucu açıkken", context => { context.readerDialog.open = true; }],
  ["inceleme notu açıkken", context => { context.jobDialog.open = true; }],
  ["metin girilirken", context => { context.document.activeElement = { tagName: "TEXTAREA", value: "Kaydedilmemiş taslak" }; }],
  ["metin seçiliyken", context => { context.window.getSelection = () => ({ isCollapsed: false }); }],
  ["kullanıcı işlemi sürerken", context => { context.state.pending.add("item-0"); }],
]) {
  test(`${name} yeni veri yalnız odak almayan bildirim oluşturur`, async () => {
    const initial = snapshot();
    const { context, requests, observations } = harness(initial);
    const refresh = context.refreshState(true);
    guard(context); // Kullanıcı, istek başladıktan sonra da yazmaya başlamış olabilir.
    requests[0].resolve(snapshot(["yararli"]));
    assert.equal(await refresh, true);
    assert.equal(context.state.data, initial);
    assert.equal(observations.renders, 0);
    assert.equal(observations.invalidations, 0);
    assert.equal(observations.notices.length, 1);
    assert.ok(context.freshnessNotice);
  });
}

test("yeni bilgi düğmesi saklanmış eski cevabı uygulamak yerine taze kayıt alır", async () => {
  const { context, requests, observations } = harness(snapshot());
  context.document.activeElement = { tagName: "INPUT" };
  const check = context.refreshState(true);
  requests[0].resolve(snapshot(["yararli"]));
  await check;
  const button = observations.notices[0].children.find(child => child.tag === "button");
  const refresh = button.listeners.get("click")();
  assert.equal(requests.length, 2);
  requests[1].resolve(snapshot(["sonra"]));
  await refresh;
  assert.equal(context.state.data.items[0].feedback, "sonra");
  assert.equal(observations.notices[0].removed, true);
});

test("sunucu yeniden başlarsa açık notun taslağı ve kaynak sürümü korunurken oturum yenilenir", async () => {
  const initial = { ...snapshot(), csrf_token: "eski-ornek-oturum" };
  initial.items[0].revision = 1;
  const { context, requests, observations } = harness(initial);
  context.jobDialog.open = true;
  context.jobSession = { draft: "Henüz kaydetmediğim inceleme notu", data: { version: 3, revision: 1, note: "Önceki not" } };
  const originalItems = initial.items;
  const originalNote = context.jobSession.data;
  const refresh = context.refreshState(true);
  const latest = { ...snapshot(["yararli"]), csrf_token: "yeni-ornek-oturum" };
  latest.items[0].revision = 2;
  requests[0].resolve(latest);
  assert.equal(await refresh, true);
  assert.equal(context.state.data.csrf_token, "yeni-ornek-oturum");
  assert.equal(context.state.data.items, originalItems);
  assert.equal(context.state.data.items[0].revision, 1);
  assert.equal(context.state.data.items[0].feedback, "yok");
  assert.equal(context.jobSession.data, originalNote);
  assert.equal(context.jobSession.data.version, 3);
  assert.equal(context.jobSession.data.revision, 1);
  assert.equal(context.jobSession.draft, "Henüz kaydetmediğim inceleme notu");
  assert.equal(observations.renders, 0);
  assert.equal(observations.invalidations, 0);
  assert.equal(observations.notices.length, 1);
});

test("kontrol sürerken yapılan yazı önceki cevabı geçersiz kılar ve kontroller sürer", async () => {
  const initial = { ...snapshot(), csrf_token: "eski-ornek-oturum" };
  const { context, requests, timers, observations } = harness(initial);
  const refresh = context.refreshState(true);
  context.writeEpoch += 1;
  requests[0].resolve({ ...snapshot(["yararli"]), csrf_token: "gecersiz-eski-istek-oturumu" });
  assert.equal(await refresh, false);
  assert.equal(context.state.data, initial);
  assert.equal(context.state.data.csrf_token, "eski-ornek-oturum");
  assert.equal(observations.renders, 0);
  assert.equal(observations.notices.length, 0);
  assert.equal([...timers.values()][0].delay, 60000);
});

test("görünmez sekmede zamanlayıcı ve yeni arka plan isteği kalmaz", async () => {
  const { context, requests, timers, listeners } = harness(snapshot());
  context.scheduleRefresh(60000);
  assert.equal(timers.size, 1);
  context.document.visibilityState = "hidden";
  listeners.get("document:visibilitychange")();
  listeners.get("window:focus")();
  assert.equal(await context.refreshState(true), false);
  assert.equal(timers.size, 0);
  assert.equal(requests.length, 0);
});

test("sayfadan çıkınca geç gelen yanıt zamanlayıcı veya çizim başlatmaz", async () => {
  const { context, requests, timers, listeners, observations } = harness({ ...snapshot(), csrf_token: "eski-ornek-oturum" });
  context.scheduleRefresh(60000);
  const refresh = context.refreshState(true);
  listeners.get("window:pagehide")();
  requests[0].resolve({ ...snapshot(["yararli"]), csrf_token: "gecersiz-eski-istek-oturumu" });
  assert.equal(await refresh, false);
  assert.equal(timers.size, 0);
  assert.equal(observations.renders, 0);
  assert.equal(context.state.data.csrf_token, "eski-ornek-oturum");
});

test("sekmeye dönünce önceki okuma tamamlandıktan sonra yeni kontrol yapılır", async () => {
  const { context, requests, listeners } = harness(snapshot());
  const oldRead = context.refreshState(true);
  context.document.visibilityState = "hidden";
  listeners.get("document:visibilitychange")();
  context.document.visibilityState = "visible";
  listeners.get("document:visibilitychange")();
  requests[0].resolve(snapshot());
  await oldRead;
  await turn();
  assert.equal(requests.length, 2);
  requests[1].resolve(snapshot(["yararli"]));
  await turn();
  assert.equal(context.state.data.items[0].feedback, "yararli");
});

test("süren ağ hatası mevcut hata alanını kullanır ve içeriği tekrar çizmez", async () => {
  const { context, requests, timers, errors, observations } = harness(snapshot());
  for (let index = 0; index < 2; index++) {
    const refresh = context.refreshState(true);
    requests[index].reject(new Error("Yerel bağlantı kesildi"));
    assert.equal(await refresh, false);
  }
  assert.equal(errors.length, 2);
  assert.equal(observations.renders, 0);
  assert.equal(observations.notices.length, 0);
  assert.equal(timers.size, 1);
  assert.equal([...timers.values()][0].delay, 60000);
});
test("koleksiyon veya ayar taslağı açıkken arka plan yenilemesi formu bozmaz", async () => {
  const { context, requests, observations } = harness(snapshot());
  context.window.UFUKWorkspace = { busy: () => true };
  const pending = context.refreshState(true);
  requests[0].resolve(snapshot(["yararli"]));
  await pending;
  assert.equal(observations.renders, 0);
  assert.equal(observations.notices.length, 1);
  assert.equal(context.state.data.items[0].feedback, "yok");
});

test("araştırma raporu veya günlük ayar açıkken ana durum okuması görünümü değiştirmez", async () => {
  const { context, requests, observations } = harness(snapshot());
  context.window.UFUKWorkflow = { busy: () => true };
  const pending = context.refreshState(true);
  requests[0].resolve(snapshot(["yararli"]));
  await pending;
  assert.equal(observations.renders, 0);
  assert.equal(observations.notices.length, 1);
  assert.equal(context.state.data.items[0].feedback, "yok");
});

test("kaynak öğrenmesi yazarken geç gelen okuma bağlı karar sürümünü ve odağı korur", async () => {
  const initial = { ...snapshot(), csrf_token: "eski-ornek-oturum",
    source_learning: { settings: { enabled: true, version: 2 }, sources: [{ source_id: "s1", protected: false, version: 4 }] },
    source_scout: { running: false } };
  const { context, requests, observations, timers } = harness(initial);
  let writing = false;
  context.sourceLearning = { busy: () => writing };
  const focused = { tagName: "BUTTON", id: "kaynak-koruma" };
  context.document.activeElement = focused;
  const refresh = context.refreshState(true);
  writing = true; // Yanıt dönerken kaynak paneli yazmayı bekliyor olabilir.
  requests[0].resolve({ ...snapshot(), csrf_token: "yeni-ornek-oturum",
    source_learning: { settings: { enabled: false, version: 3 }, sources: [{ source_id: "s1", protected: true, version: 5 }] },
    source_scout: { running: true } });
  assert.equal(await refresh, true);
  assert.equal(context.state.pending.size, 0, "Bu regresyon ortak pending kilidinden bağımsız olarak bileşenin busy korumasını sınar.");
  assert.equal(context.state.data, initial);
  assert.equal(context.state.data.source_learning.settings.version, 2);
  assert.equal(context.state.data.source_learning.sources[0].version, 4);
  assert.equal(context.state.data.source_learning.sources[0].protected, false);
  assert.equal(context.state.data.csrf_token, "yeni-ornek-oturum");
  assert.equal(context.document.activeElement, focused);
  assert.equal(observations.renders, 0);
  assert.equal(observations.invalidations, 0);
  assert.equal(observations.notices.length, 1);
  assert.equal([...timers.values()][0].delay, 2500, "Görünüm korunurken başlayan keşfin takibi devam etmeli.");
});

test("kaynak yazısı bitmeden yenileme düğmesi karar görünümünü ezmez; bitince yeni GET gerekir", async () => {
  const initial = { ...snapshot(), source_learning: { settings: { enabled: true, version: 2 } } };
  const { context, requests, observations } = harness(initial);
  let writing = true;
  context.sourceLearning = { busy: () => writing };
  context.state.pending.add("source-learning");
  const check = context.refreshState(true);
  requests[0].resolve({ ...snapshot(), source_learning: { settings: { enabled: false, version: 3 } } });
  await check;
  const button = observations.notices[0].children.find(child => child.tag === "button");
  await button.listeners.get("click")();
  assert.equal(requests.length, 1);
  assert.equal(context.state.data, initial);
  assert.equal(observations.renders, 0);
  writing = false;
  context.state.pending.delete("source-learning");
  const apply = button.listeners.get("click")();
  assert.equal(requests.length, 2);
  requests[1].resolve({ ...snapshot(), source_learning: { settings: { enabled: true, version: 4 } } });
  await apply;
  assert.equal(context.state.data.source_learning.settings.version, 4, "Bildirimdeki eski sürüm doğrudan uygulanmamalı.");
  assert.equal(context.state.data.source_learning.settings.enabled, true);
  assert.equal(observations.renders, 1);
  assert.equal(observations.invalidations, 0, "Kaynak öğrenmesi değişikliği tek başına haber sayfalarını sıfırlamamalı.");
  assert.equal(observations.notices[0].removed, true);
});

test("yalnız kaynak keşfi çalışırken 2,5 saniyede izler; bitince bir dakikaya döner", async () => {
  const running = { ...snapshot(), source_scout: { running: true, last_run: { id: "kesif1", status: "running" } } };
  const { context, requests, timers, observations } = harness(running);
  const refresh = context.refreshState(true);
  requests[0].resolve({ ...snapshot(), source_scout: { running: true, last_run: { id: "kesif1", status: "running" } } });
  await refresh;
  assert.equal(observations.renders, 0, "Aynı ilerleme gereksiz çizim başlatmamalı.");
  const [identifier, timer] = [...timers.entries()][0];
  assert.equal(timer.delay, 2500);
  timers.delete(identifier); timer.callback();
  assert.equal(requests.length, 2);
  const next = context.fetchInFlight;
  requests[1].resolve({ ...snapshot(), source_scout: { running: false, last_run: { id: "kesif1", status: "completed" } } });
  await next;
  assert.equal(context.state.data.source_scout.last_run.status, "completed");
  assert.equal(observations.renders, 1);
  assert.equal(timers.size, 1);
  assert.equal([...timers.values()][0].delay, 60000);
});

test("okunma veya koleksiyon değişimi tüm havuzun filtre sonucunu yeniletir", async () => {
  const initial = { ...snapshot(), personal: { read_states: [], collections: [] } };
  const { context, requests, observations } = harness(initial);
  const pending = context.refreshState(true);
  requests[0].resolve({ ...initial, personal: { read_states: [{ item_id: "0", read: true }], collections: [] } });
  await pending;
  assert.equal(observations.invalidations, 1);
  assert.equal(context.state.data.personal.read_states[0].read, true);
});
