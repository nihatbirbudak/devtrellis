// Sağlayıcı seçiminin modeli dürüst göstermesini ve NVIDIA seçiminde gizli Codex değerini göndermemesini doğrular.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {test} = require("node:test");
const {createTranslator, provenanceFacts} = require("../radar/static/ui-text.js");
const source = fs.readFileSync(require("node:path").join(__dirname, "../radar/static/platform.js"), "utf8");
const functions = source.slice(source.indexOf("  function providerSnapshot()"), source.indexOf("  async function saveInterests("));

test("NVIDIA seçimi gerçek model ve anahtar durumunu gösterir; kaydetme yalnız ayar yazar", async () => {
  for (const language of ["en", "tr"]) {
    const nodes = new Map();
    const byId = id => {
      if (!nodes.has(id)) nodes.set(id, {value: "", disabled: false, hidden: false, textContent: "", reportValidity: () => true, close() {this.closed = true;}});
      return nodes.get(id);
    };
    const provider = {deep_provider: "nvidia", version: 3, providers: [{id: "nvidia", label: "NVIDIA API", available: true, model: "nvidia/actual-model"}, {id: "codex", label: "Codex CLI", available: false, model: "codex/configured"}]};
    const calls = [];
    const context = vm.createContext({byId, state: {provider}, t: createTranslator(language), message() {}, announce() {}, errorMessage: error => error.message, request: async (url, body) => {calls.push({url, body}); return provider;}});
    vm.runInContext(functions, context);
    byId("provider-select").value = "nvidia"; byId("provider-model").value = "must-not-leak-as-nvidia-model"; byId("provider-language").value = "en";
    context.providerAvailability();
    assert.match(byId("provider-current-model").textContent, /nvidia\/actual-model/);
    assert.doesNotMatch(byId("provider-availability").textContent, /CLI/);
    assert.equal(byId("provider-model").disabled, true); assert.equal(byId("provider-nvidia-help").hidden, false); assert.equal(calls.length, 0);
    await context.saveProviders({preventDefault() {}});
    assert.equal(calls.length, 1); assert.equal(calls[0].url, "/api/providers"); assert.equal(calls[0].body.deep_provider, "nvidia"); assert.equal(calls[0].body.codex_model, null); assert.equal(calls[0].body.expected_version, 3);
    byId("provider-select").value = "codex"; context.providerAvailability(); assert.equal(byId("provider-model").disabled, false); assert.equal(byId("provider-nvidia-help").hidden, true);
  }
});

test("köken metni kaydedilmemiş modeli ve değiştirilmiş kanıtı dürüst gösterir", () => {
  const facts = provenanceFacts({model: "unknown", generated_at: null, question: "UFUK <img>", evidence_status: "verified", modified: true}, createTranslator("en"), value => value || "Unknown");
  assert.deepEqual(facts, [["Original research model", "Model not recorded"], ["Research generated on", "Generation date not recorded"], ["Research question", "UFUK <img>"], ["Card evidence status", "Not checked"]]);
});

test("opsiyonel iddia iki dilde çevrilmeden gösterilir; eski köken kayıtlarının satırları değişmez", () => {
  const value = {question: "Bu doğru mu?", claim_text: "UFUK <img> gerçek iddia"};
  for (const language of ["en", "tr"]) {
    const facts = provenanceFacts(value, createTranslator(language), () => "Unknown");
    assert.deepEqual(facts.at(-1), [language === "en" ? "Claim under review" : "İncelenen iddia", value.claim_text]);
    assert.equal(provenanceFacts({...value, claim_text: undefined}, createTranslator(language), () => "Unknown").length, 4);
  }
});
