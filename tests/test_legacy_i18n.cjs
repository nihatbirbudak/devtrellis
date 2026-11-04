// Ana arayüzün API konu kaydını, iki dilli tarihlerini ve rapor dilini içerikten bağımsız korumasını doğrular.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const translations = require("../radar/static/ui-text.js");
translations.register(require("../radar/static/legacy-text.js"));
const source = fs.readFileSync(path.join(__dirname, "../radar/static/app.js"), "utf8");

test("kamu kayıt bağlantısı onay penceresine geçer ve başlangıçta içerik veya model istemez", async () => {
  const code = source.slice(source.indexOf("  async function openResearchEntry()"), source.indexOf("  Promise.all([refreshState(), initialTopics])"));
  const requests = [], intents = [], drafts = [];
  const context = vm.createContext({URLSearchParams, window: {location: {search: "?community_card=card-one"}}, state: {data: {}}, request: async url => { requests.push(url); }, discovery: {communityIntent(id) {intents.push(id);}, open(_opener, draft) {drafts.push(draft);}}, document: {getElementById: () => ({})}});
  vm.runInContext(code, context); await context.openResearchEntry();
  assert.deepEqual(requests, []); assert.deepEqual(intents, ["card-one"]); assert.deepEqual(drafts, []);
});

test("kütüphaneden yeniden araştırma yalnız yerel kaydı okuyup yeni formu ön doldurur", async () => {
  const code = source.slice(source.indexOf("  async function openResearchEntry()"), source.indexOf("  Promise.all([refreshState(), initialTopics])"));
  const entry = {id: "entry-one", kind: "research", title: "Original card", body: "Keep this report", provenance: {question: "Is this true?", claim_text: "The original claim <img> UFUK", topic: "security", source_url: "https://example.org/original"}};
  const original = JSON.stringify(entry); const requests = []; const drafts = [];
  const context = vm.createContext({URLSearchParams, window: {location: {search: "?research_entry=entry-one"}}, state: {data: {}}, request: async url => {requests.push(url); return {entries: [entry]};}, discovery: {open(_opener, draft) {drafts.push(draft);}}, document: {getElementById: () => ({})}, t: translations.createTranslator("en"), toast: message => {throw new Error(message);}});
  vm.runInContext(code, context); await context.openResearchEntry();
  assert.deepEqual(requests, ["/api/platform"]); assert.equal(drafts.length, 1);
  assert.equal(drafts[0].question, entry.provenance.question); assert.equal(drafts[0].topic, "security"); assert.equal(drafts[0].source_url, entry.provenance.source_url);
  assert.equal(drafts[0].claim_text, entry.provenance.claim_text);
  assert.equal(Object.hasOwn(drafts[0], "context_text"), false);
  assert.equal(JSON.stringify(entry), original);
  delete entry.provenance.claim_text;
  await context.openResearchEntry();
  assert.equal(drafts[1].claim_text, ""); assert.equal(drafts[1].question, entry.provenance.question);
});

test("ana filtreler sekiz API konusunu doğru dilde alır ve ortak harita referansını korur", async () => {
  const definitions = [
    ["jarvis", "Ev otomasyonu", "Home automation"], ["ai", "Yapay zekâ", "AI"],
    ["robotik", "Robotik", "Robotics"], ["tarim", "Tarım", "Agriculture"],
    ["medya", "Medya", "Media"], ["software", "Yazılım", "Software"],
    ["security", "Güvenlik", "Security"], ["data", "Veri", "Data"]
  ].map(([id, label_tr, label_en]) => ({id, label_tr, label_en}));
  for (const language of ["en", "tr"]) {
    const topics = {};
    const requests = [];
    const element = (tag, className, text = "") => ({tag, className, text, children: [], dataset: {}, attributes: {}, listeners: {},
      append(...children) { this.children.push(...children); }, replaceChildren(...children) { this.children = children; },
      setAttribute(name, value) { this.attributes[name] = value; }, addEventListener(name, listener) { this.listeners[name] = listener; }});
    const controls = element("div");
    const state = {topic: "all", data: null};
    const context = vm.createContext({UI: {language}, t: translations.createTranslator(language), topics, state, element,
      document: {querySelector: () => controls, createTextNode: text => ({text})},
      request: async url => { requests.push(url); return definitions; }, render() {}, announce() {}, toast(message) { throw new Error(message); }});
    vm.runInContext(source.slice(source.indexOf("  function renderTopicControls()"), source.indexOf("  function safeUrl(")), context);
    await context.loadTopics();
    assert.deepEqual(requests, ["/api/topics"]);
    assert.equal(context.topics, topics);
    assert.equal(Object.keys(topics).length, 8);
    assert.equal(controls.children.length, 9);
    assert.equal(topics.security, language === "en" ? "Security" : "Güvenlik");
    controls.children.find(button => button.dataset.topic === "security").listeners.click();
    assert.equal(state.topic, "security");
  }
});

test("ana tarih gösterimi dili izler, rapor dili ise raporun üretildiği dildir", () => {
  for (const language of ["en", "tr"]) {
    const context = vm.createContext({UI: {language}, t: translations.createTranslator(language), Date, Intl});
    vm.runInContext(source.slice(source.indexOf("  function dateValue("), source.indexOf("  function feedback(")), context);
    assert.match(context.formatDate("2026-11-04T12:00:00Z"), language === "en" ? /Nov/ : /Kas/);
    assert.equal(context.formatDate(null), language === "en" ? "Unknown" : "Bilinmiyor");
  }
  let task = {language: "en", result: {summary_tr: "Original English report"}};
  const context = vm.createContext({workflowTask: () => task, workflowSummary: () => task?.result});
  vm.runInContext(source.slice(source.indexOf("  function setTextLanguage("), source.indexOf("  function sourceAvatar(")), context);
  const english = {};
  context.setTextLanguage(english, {language: "tr"});
  assert.equal(english.lang, "en");
  task = null;
  const historical = {};
  context.setTextLanguage(historical, {ai_summary: {summary_tr: "Eski özgün rapor"}});
  assert.equal(historical.lang, "tr");
});
