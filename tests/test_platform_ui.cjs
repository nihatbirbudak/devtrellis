// Paylaşım önizlemesindeki bağlantı güvenliğini, dosya sınırlarını ve kullanıcı girdisi denetimlerini doğrular.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const {safeURL, parseTags, parseSources, parsePacket, dateLabel, entryMatches, errorMessage} = require("../radar/static/platform.js");
const {createTranslator, loadTopicLabels} = require("../radar/static/ui-text.js");

test("kişisel haber etiketleri konu kaydının dilini kullanır; kişisel arşiv rozeti korunur", async () => {
  const definitions = [{id: "software", label_tr: "Yazılım geliştirme", label_en: "Software development"},
    {id: "data", label_tr: "Veri ve veritabanları", label_en: "Data and databases"},
    {id: "future_topic", label_tr: "Yeni konu", label_en: "New topic from registry"}];
  const source = fs.readFileSync(path.join(__dirname, "../radar/static/platform.js"), "utf8");
  const start = source.indexOf("  function feedCard(");
  const end = source.indexOf("  function pagination(", start);
  assert.ok(start >= 0 && end > start);
  const element = (tag, className, text = "") => ({tag, className, text, children: [],
    append(...children) { this.children.push(...children); }, get textContent() { return this.text + this.children.map(child => child.textContent).join(""); }});
  for (const language of ["en", "tr"]) {
    const calls = [];
    const labels = await loadTopicLabels(language, async (url, options) => { calls.push({url, options}); return {ok: true, json: async () => definitions}; });
    assert.deepEqual(calls.map(call => call.url), ["/api/topics"]);
    const context = vm.createContext({state: {topicLabels: labels}, element, link: text => element("a", "", text),
      t: createTranslator(language), dateLabel: value => value});
    vm.runInContext(source.slice(start, end), context);
    for (const topic of definitions) {
      const item = {topic: topic.id, topic_label: topic.label_tr, is_archive: true, title: "Original title", published_at: "2026-11-04", last_seen_at: "2026-11-05"};
      const card = context.feedCard(item);
      assert.ok(card.textContent.includes(topic[language === "en" ? "label_en" : "label_tr"]));
      assert.ok(card.textContent.includes(language === "en" ? "Archived record" : "Arşiv kaydı"));
      assert.ok(card.textContent.includes("Original title"));
      assert.ok(card.textContent.includes("2026-11-04"));
      assert.ok(card.textContent.includes("2026-11-05"));
      assert.equal(item.is_archive, true);
    }
  }
});

test("konu yükleme hatası veya bozuk cevap boş haritayla mevcut etikete dönüş sağlar", async () => {
  for (const fetcher of [async () => { throw new Error("offline"); }, async () => ({ok: false}),
    async () => ({ok: true, json: async () => null}), async () => ({ok: true, json: async () => [null, {id: "missing-label"}]})]) {
    assert.deepEqual(Object.keys(await loadTopicLabels("en", fetcher)), []);
  }
});

test("yalnız kimlik bilgisi içermeyen http(s) bağlantıları açılır", () => {
  assert.equal(safeURL("https://example.org/research?q=robotik#sources"), "https://example.org/research?q=robotik#sources");
  assert.equal(safeURL("http://example.org"), "http://example.org/");
  for (const url of ["javascript:alert(1)", "data:text/html,hello", "file:///C:/private.txt", "//example.org", "/private", "https://user:password@example.org", "https://user@example.org", ""]) {
    assert.equal(safeURL(url), null, url);
  }
});

test("etiketler Türkçe harflerle birleştirilir ve sınırlar korunur", () => {
  assert.deepEqual(parseTags(" Yapay zekâ,İLGİ, ilgi, Yapay zekâ, ,robotik "), ["Yapay zekâ", "İLGİ", "robotik"]);
  assert.deepEqual(parseTags(""), []);
  assert.throws(() => parseTags("a".repeat(41)), /40 karakter/);
  assert.throws(() => parseTags(Array.from({length: 13}, (_, index) => `konu${index}`).join(",")), /12 ilgi alanı/);
});

test("kaynak satırları açıklayıcı hata verir, güvensiz bağlantıyı geçirmez", () => {
  assert.deepEqual(parseSources("Makale | https://example.org/a\r\n\nhttps://example.org/b"), [{title: "Makale", url: "https://example.org/a"}, {title: "https://example.org/b", url: "https://example.org/b"}]);
  assert.throws(() => parseSources("Bir | https://example.org\nİki | javascript:alert(1)"), /2\. kaynak/);
  assert.throws(() => parseSources(" | https://example.org"), /başlık/);
  assert.throws(() => parseSources(Array(11).fill("Başlık | https://example.org").join("\n")), /10 kaynak/);
});

test("dosya boyutu Unicode karakter sayısıyla değil UTF8 baytlarıyla sınırlandırılır", () => {
  assert.deepEqual(parsePacket('{"schema":"ufuk-share"}'), {schema: "ufuk-share"});
  assert.throws(() => parsePacket(JSON.stringify({body: "ğ".repeat(17000)})), /32 KB/);
  for (const text of ["null", "[]", '"metin"', "42"]) assert.throws(() => parsePacket(text), /UFUK kaydı/);
  assert.throws(() => parsePacket("{bad}"), /geçerli bir JSON/);
});

test("eksik tarih güncel bir tarih gibi gösterilmez", () => {
  assert.equal(dateLabel(null), "Bilinmiyor");
  assert.equal(dateLabel("tarih-değil"), "Bilinmiyor");
  assert.match(dateLabel("2026-11-04T12:00:00+03:00"), /2026/);
});

test("sürüm çakışması taslağın korunduğunu ve açık yenileme gerektiğini söyler", () => {
  const message = errorMessage({status: 409, message: "conflict"});
  assert.match(message, /Taslağın burada duruyor/);
  assert.match(message, /sayfayı yeniledikten/);
  assert.match(errorMessage({status: 403}), /Taslağını kopyalayıp/);
});

test("kütüphane araması Türkçe I ve İ harflerini Unicode eşdeğerleriyle eşleştirir", () => {
  const entry = {title: "IŞIK sensörü", summary: "İLGİ alanı", body: "Sera notları", author: "Işıl", tags: ["İklim"], sources: [{title: "İnceleme", url: "https://example.org"}]};
  for (const query of ["ışık", "IŞIK", "ilgi", "İLGİ", "I\u0307LGI\u0307", "ışıl", "İKLİM", "inceleme", "ışık inceleme", "  "]) assert.equal(entryMatches(entry, query), true, query);
  assert.equal(entryMatches(entry, "bilinmeyen"), false);
  assert.equal(entryMatches(entry, "ışık olmayan"), false);
  assert.equal(entryMatches(entry, "https://example.org"), false);
});

test("kütüphane araması her istenen metin alanını ve boş kayıtları işler", () => {
  for (const key of ["title", "summary", "body", "author"]) assert.equal(entryMatches({[key]: "Robotik"}, "robotik"), true, key);
  assert.equal(entryMatches({tags: ["Robotik"]}, "robotik"), true);
  assert.equal(entryMatches({sources: [{title: "Robotik"}]}, "robotik"), true);
  assert.equal(entryMatches({}, ""), true);
  assert.equal(entryMatches({}, "robotik"), false);
});
