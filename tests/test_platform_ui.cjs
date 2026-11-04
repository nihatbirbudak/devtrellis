// Paylaşım önizlemesindeki bağlantı güvenliğini, dosya sınırlarını ve kullanıcı girdisi denetimlerini doğrular.
const assert = require("node:assert/strict");
const {test} = require("node:test");
const {safeURL, parseTags, parseSources, parsePacket, dateLabel, entryMatches, errorMessage} = require("../radar/static/platform.js");

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
