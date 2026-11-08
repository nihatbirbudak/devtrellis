// Gelişme özetleri gösterimde temizlenir ve yalnız ardışık aynı kaynak sürümleri gruplanır.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {cleanFeedSummary, groupedFeed} = require("../radar/static/community.js");

test("blog kalıbı ve işaretler görünmez, özgün metne dokunulmaz", () => {
  const original = "## Release **notes** [details](https://example.org) The post Release notes appeared first on Example.com.";
  const cleaned = cleanFeedSummary(original);
  assert.equal(cleaned, "Release notes details…");
  assert.match(original, /appeared first on/);
});

test("uzun ve kesik özet son tam cümlede biter", () => {
  const original = "A complete sentence describes the first finding. " + "Another complete sentence describes the second finding. ".repeat(7) + "A cut off sentence";
  assert.match(cleanFeedSummary(original), /finding\.$/);
});

test("yalnız art arda aynı kaynağın sürümleri bir gruptur", () => {
  const row = (source_name, url) => ({source_name, url});
  const groups = groupedFeed([row("A", "https://github.com/a/repo/releases/tag/v2"),
    row("A", "https://github.com/a/repo/releases/tag/v1"),
    row("B", "https://example.org/news"),
    row("A", "https://github.com/a/repo/releases/tag/v0")]);
  assert.deepEqual(groups.map(group => group.items.length), [2, 1, 1]);
});

test("bağlantı etiketi, kod içeriği ve sorun numarası kalır; liste işaretleri temizlenir",()=>{
  const value="## What's Changed\n* Fix `bug` #14\n* Adds [links](https://example.org/a) and **support**.";
  assert.equal(cleanFeedSummary(value),"What's Changed Fix bug #14 Adds links and support.");
  assert.equal(cleanFeedSummary("What's Changed * Fix bug 4"),"What's Changed: Fix bug 4…");
  assert.equal(cleanFeedSummary("Use 2 * 3 for issue #14."),"Use 2 * 3 for issue #14.");
});
test("kısa kesik özet tamamlanmış cümle gibi sunulmaz ve uzun özet 400 karakteri aşmaz",()=>{
  assert.equal(cleanFeedSummary("Adds links and"),"Adds links and…");
  assert.equal(cleanFeedSummary("Already cut…"),"Already cut…");
  assert.ok(cleanFeedSummary("word ".repeat(120)).length<=400);
  assert.ok(cleanFeedSummary("Complete sentence. ".repeat(60)).length<=400);
});

test("tek satıra düşmüş sürüm listesinde sonraki imler ve tek yıldızlı vurgu temizlenir",()=>{
  assert.equal(cleanFeedSummary("What's Changed * Fix bug #14 * Adds `code` support"),
    "What's Changed: Fix bug #14; Adds code support…");
  assert.equal(cleanFeedSummary("Release notes * Adds *emphasis* and [links](https://example.org) * Fixes *two words*."),
    "Release notes: Adds emphasis and links; Fixes two words.");
  assert.equal(cleanFeedSummary("Changes * Use 2 * 3 for issue #14 * Add docs."),
    "Changes: Use 2 * 3 for issue #14; Add docs.");
});
test("vurgu temizliği matematik yıldızını, eşleşmemiş yıldızı ve sorunun numarasını silmez",()=>{
  assert.equal(cleanFeedSummary("Keep *emphasis*, *two words* and issue #14."),
    "Keep emphasis, two words and issue #14.");
  assert.equal(cleanFeedSummary("Use x * y and 2 * 3."),"Use x * y and 2 * 3.");
  assert.equal(cleanFeedSummary("Unmatched *literal marker."),"Unmatched *literal marker.");
});

test("sürüm listeleri rakam, sürüm, tek büyük harf ve sorun numarasıyla başlayabilir",()=>{
  for (const item of ["2FA login added", "v2 API added", "X server support", "#12 resolved"]) {
    assert.equal(cleanFeedSummary("What's Changed * Fix crash * " + item),
      "What's Changed: Fix crash; " + item + "…");
    assert.equal(cleanFeedSummary("New release * " + item + " * Adds docs"),
      "New release: " + item + "; Adds docs…");
  }
});

test("alt çizgili vurgu temizlenir; tanımlayıcı, glob, çarpma, sürüm ve bağlantı anlamı kalır",()=>{
  assert.equal(cleanFeedSummary("New release * Adds _alt çizgi vurgusu_ * Fixes _one_ issue."),
    "New release: Adds alt çizgi vurgusu; Fixes one issue.");
  assert.equal(cleanFeedSummary("Use a*b*c, *.py, snake_case and api_v2_name."),
    "Use a*b*c, *.py, snake_case and api_v2_name.");
  assert.equal(cleanFeedSummary("Changes * Use x * y, width * height and 2 * 3 * Adds docs."),
    "Changes: Use x * y, width * height and 2 * 3; Adds docs.");
  assert.equal(cleanFeedSummary("New release * v2.1 API added * See [#12](https://example.org/issues/12)."),
    "New release: v2.1 API added; See #12.");
  assert.equal(cleanFeedSummary("New release * Use https://example.org/a_b/v2#12 * Adds `a*b*c` and `*.py`."),
    "New release: Use https://example.org/a_b/v2#12; Adds a*b*c and *.py.");
  assert.equal(cleanFeedSummary("Unmatched _literal marker and snake_case."),
    "Unmatched _literal marker and snake_case.");
  assert.equal(cleanFeedSummary("Changes * Use 2 * X for area and x * v2 for scale * Adds docs."),
    "Changes: Use 2 * X for area and x * v2 for scale; Adds docs.");
  assert.equal(cleanFeedSummary("A formula uses width * height and 2 * X."),
    "A formula uses width * height and 2 * X.");
});

test("çok harfli değişken ile sürüm benzeri çarpan arasındaki yıldız korunur",()=>{
  for (const expression of ["rate * v2 for scale", "rate * v2 + bias", "rate * v2 and offset", "rate * v2"]) {
    assert.equal(cleanFeedSummary("Changes * Use " + expression + "."),
      "Changes: Use " + expression + ".");
  }
  assert.equal(cleanFeedSummary("Changes * Use rate * v2 for scale * Adds docs."),
    "Changes: Use rate * v2 for scale; Adds docs.");
  assert.equal(cleanFeedSummary("Changes * Fix crash * v2 API added."),
    "Changes: Fix crash; v2 API added.");
});

test("alt çizgiyle sınırlı globlar vurgu sanılıp genişletilmez",()=>{
  for (const glob of ["_*_", "_*.py_", "_?_", "_[abc]_", "_file?name_"]) {
    assert.equal(cleanFeedSummary("Match " + glob + " files."),
      "Match " + glob + " files.");
  }
  assert.equal(cleanFeedSummary("Keep _emphasis_ and _two words_."),
    "Keep emphasis and two words.");
});
