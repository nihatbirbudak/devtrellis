// Sayfa içi onay penceresinin klavye, odak, vazgeçme ve açılamama davranışını; sayfaların confirm kullanmadığını sınar.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {test} = require("node:test");
const {createConfirm, WORDS} = require("../radar/static/confirm-dialog.js");
const {EN} = require("../radar/static/ui-text.js");
const STATIC = path.join(__dirname, "../radar/static");
const read = name => fs.readFileSync(path.join(STATIC, name), "utf8");
const turn = () => new Promise(resolve => setImmediate(resolve));

function page({language = "tr", showModal = true} = {}) {
  let doc;
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this.listeners = new Map();
      this.attributes = {}; this.dataset = {}; this.className = ""; this.id = ""; this.type = ""; this._text = "";
      this.hidden = false; this.disabled = false; this.open = false;
      if (this.tagName === "DIALOG" && showModal) {
        this.showModal = () => {
          if (this.open || !this.isConnected) throw new Error("InvalidStateError");
          this.open = true;
        };
      }
    }
    get classList() { return {contains: value => this.className.split(" ").includes(value)}; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
    set textContent(value) { this.children = []; this._text = String(value); }
    set innerHTML(value) { throw new Error(`Unsafe HTML: ${value}`); }
    get isConnected() { return this === doc.body || Boolean(this.parentNode?.isConnected); }
    append(...nodes) { for (const node of nodes) { node.remove(); node.parentNode = this; this.children.push(node); } }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(node => node !== this); this.parentNode = null; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener(name, listener) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(listener); }
    dispatch(name, extra = {}) {
      const event = {type: name, target: this, defaultPrevented: false, propagationStopped: false,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; }, ...extra};
      for (let node = this; node; node = node.parentNode) {
        for (const listener of node.listeners.get(name) || []) listener(event);
        if (event.propagationStopped || name === "close" || name === "cancel") break;
      }
      return event;
    }
    focus() { if (!this.disabled && this.isConnected) doc.activeElement = this; }
    closest(selector) {
      for (let node = this; node; node = node.parentNode) if (selector === "dialog[open]" && node.tagName === "DIALOG" && node.open) return node;
      return null;
    }
    close() {
      if (!this.open) return;
      this.open = false;
      // Tarayıcı close olayını sonraki görevde gönderir.
      setImmediate(() => this.dispatch("close"));
    }
    all() { return this.children.flatMap(child => [child, ...child.all()]); }
  }
  const timers = new Map(); let nextTimer = 0;
  doc = {documentElement: {lang: language}, activeElement: null, createElement: tag => new Element(tag),
    querySelectorAll: selector => selector === "dialog[open]" ? doc.body.all().filter(node => node.tagName === "DIALOG" && node.open) : []};
  doc.body = new Element("body"); doc.activeElement = doc.body;
  const clock = {setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; }, clearTimeout(id) { timers.delete(id); }};
  const trigger = new Element("button"); trigger.textContent = "Yayımla"; doc.body.append(trigger); trigger.focus();
  const confirm = createConfirm(doc, clock);
  const dialog = () => doc.body.all().find(node => node.tagName === "DIALOG" && node.classList.contains("dt-confirm")) || null;
  const notice = () => doc.body.all().find(node => node.classList.contains("dt-confirm-notice")) || null;
  const byClass = name => dialog()?.all().find(node => node.classList.contains(name));
  return {doc, Element, trigger, confirm, dialog, notice, byClass, timers};
}

test("onay penceresi erişilebilir alertdialog olarak açılır; güvenli seçenek odaklanır; onay true döner ve odak geri gelir", async () => {
  const p = page();
  const answer = p.confirm.ask({message: "Bu katkıyı şimdi yayımlamak istiyor musun?", confirmLabel: "Onayla ve yayımla"});
  const dialog = p.dialog();
  assert.ok(dialog); assert.equal(dialog.open, true);
  assert.equal(dialog.getAttribute("role"), "alertdialog"); assert.equal(dialog.getAttribute("aria-modal"), "true");
  const title = dialog.all().find(node => node.id === dialog.getAttribute("aria-labelledby"));
  const message = dialog.all().find(node => node.id === dialog.getAttribute("aria-describedby"));
  assert.equal(title.textContent, WORDS.title[0]); assert.equal(message.textContent, "Bu katkıyı şimdi yayımlamak istiyor musun?");
  assert.equal(p.byClass("dt-confirm-cancel").textContent, "Vazgeç"); assert.equal(p.byClass("dt-confirm-cancel").type, "button");
  assert.equal(p.byClass("dt-confirm-accept").textContent, "Onayla ve yayımla"); assert.equal(p.byClass("dt-confirm-accept").type, "button");
  assert.equal(p.doc.activeElement, p.byClass("dt-confirm-cancel"));
  p.byClass("dt-confirm-accept").dispatch("click");
  assert.equal(await answer, true);
  assert.equal(p.dialog(), null); assert.equal(dialog.open, false);
  assert.equal(p.doc.activeElement, p.trigger);
  assert.equal(p.notice().textContent, "", "Onayda ileti yazılmaz; yalnız boş canlı bölge kalır");
  await turn();
});

test("Vazgeç, Esc ve dışarıdan kapanma false döner; görünür vazgeçme iletisi iki dilde çıkar", async () => {
  for (const [language, index] of [["tr", 0], ["en", 1]]) {
    const p = page({language});
    const first = p.confirm.ask({message: "Bu katkıyı reddetmek istiyor musun?"});
    assert.equal(p.byClass("dt-confirm-cancel").textContent, WORDS.cancel[index]);
    assert.equal(p.byClass("dt-confirm-accept").textContent, WORDS.confirm[index]);
    // Canlı bölge pencere açılmadan önce boş ama erişilebilir olarak yerinde durur; ileti sonradan yazılır.
    assert.equal(p.notice().parentNode, p.doc.body); assert.equal(p.notice().textContent, ""); assert.equal(p.notice().hidden, false);
    assert.equal(p.notice().getAttribute("role"), "status");
    p.byClass("dt-confirm-cancel").dispatch("click");
    assert.equal(await first, false);
    assert.equal(p.notice().textContent, WORDS.cancelled[index]); assert.equal(p.notice().hidden, false);
    assert.equal(p.notice().getAttribute("role"), "status"); assert.equal(p.notice().getAttribute("aria-live"), "polite");
    assert.equal(p.notice().dataset.kind, "cancelled"); assert.equal(p.doc.activeElement, p.trigger);

    const second = p.confirm.ask({message: "Esc ile vazgeç"});
    assert.equal(p.notice().textContent, ""); assert.equal(p.notice().hidden, false);
    const cancel = p.dialog().dispatch("cancel");
    assert.equal(cancel.defaultPrevented, true);
    assert.equal(await second, false); assert.equal(p.dialog(), null);

    const third = p.confirm.ask({message: "Klavye Esc"});
    const key = p.byClass("dt-confirm-cancel").dispatch("keydown", {key: "Escape"});
    assert.equal(key.defaultPrevented, true); assert.equal(key.propagationStopped, true);
    assert.equal(await third, false);

    const fourth = p.confirm.ask({message: "Dışarıdan kapanır"});
    p.dialog().close();
    assert.equal(await fourth, false); assert.equal(p.dialog(), null);
    assert.equal(p.notice().textContent, WORDS.cancelled[index]);
    await turn();
  }
});

test("Tab ve Shift+Tab odağı iki düğme arasında tutar", async () => {
  const p = page();
  const answer = p.confirm.ask({message: "Taslağı bırakmak istiyor musun?", confirmLabel: "Taslağı bırak", tone: "danger"});
  const cancel = p.byClass("dt-confirm-cancel"), accept = p.byClass("dt-confirm-accept");
  assert.ok(accept.classList.contains("dt-confirm-danger"));
  assert.equal(cancel.dispatch("keydown", {key: "Tab"}).defaultPrevented, true); assert.equal(p.doc.activeElement, accept);
  accept.dispatch("keydown", {key: "Tab"}); assert.equal(p.doc.activeElement, cancel);
  cancel.dispatch("keydown", {key: "Tab", shiftKey: true}); assert.equal(p.doc.activeElement, accept);
  accept.dispatch("keydown", {key: "Tab", shiftKey: true}); assert.equal(p.doc.activeElement, cancel);
  assert.equal(cancel.dispatch("keydown", {key: "Enter"}).defaultPrevented, false);
  accept.dispatch("click");
  assert.equal(await answer, true);
  await turn();
});

// Bölge o anda yerleştirildiyse metin kısa bir gecikmeyle yazılır; ekran okuyucu değişikliği duyar.
function flushAnnouncement(p) {
  const pending = [...p.timers.entries()].filter(([, timer]) => timer.delay === 100);
  assert.equal(pending.length, 1, "Duyuru gecikmesi bekleniyor");
  const [[id, timer]] = pending; p.timers.delete(id); timer.callback();
}

test("pencere açılamazsa işlem yapılmaz; görünür açılamama iletisi çıkar ve DOM'da artık kalmaz", async () => {
  for (const [language, index] of [["tr", 0], ["en", 1]]) {
    const p = page({language, showModal: false});
    assert.equal(await p.confirm.ask({message: "Bu katkıyı yayından kaldırmak istiyor musun?"}), false);
    assert.equal(p.dialog(), null);
    assert.equal(p.notice().textContent, "", "Yeni yerleşen bölgeye metin aynı anda yazılmaz");
    flushAnnouncement(p);
    assert.equal(p.notice().textContent, WORDS.unavailable[index]); assert.equal(p.notice().dataset.kind, "unavailable");
  }
  const empty = page();
  assert.equal(await empty.confirm.ask({message: "   "}), false);
  assert.equal(empty.dialog(), null); flushAnnouncement(empty); assert.equal(empty.notice().textContent, WORDS.unavailable[0]);
});

test("açık onay varken ikinci istek false döner; ilk onay bozulmaz", async () => {
  const p = page({language: "en"});
  const first = p.confirm.ask({message: "First question"});
  assert.equal(await p.confirm.ask({message: "Second question"}), false);
  // Bölge ilk onay açılırken yerleşti; ikinci istekte ileti hemen yazılır.
  assert.equal(p.notice().textContent, WORDS.busy[1]);
  assert.equal(p.doc.body.all().filter(node => node.tagName === "DIALOG").length, 1);
  assert.equal(p.doc.activeElement, p.byClass("dt-confirm-cancel"));
  p.byClass("dt-confirm-accept").dispatch("click");
  assert.equal(await first, true);
  await turn();
});

test("ileti düz metin kalır, satır sonu korunur; çağıranın metinleri ve bildirim yeri kullanılır", async () => {
  const p = page({language: "en"});
  const hostile = "<img src=x onerror=alert(1)>\nresearch · v4 · Reject";
  const seen = [];
  const answer = p.confirm.ask({message: hostile, title: "Moderation", cancelLabel: "Keep", cancelledNotice: "Nothing was saved.", notify: (text, kind) => seen.push([text, kind])});
  assert.equal(p.dialog().all().find(node => node.classList.contains("dt-confirm-message")).textContent, hostile);
  assert.equal(p.dialog().all().find(node => node.classList.contains("dt-confirm-title")).textContent, "Moderation");
  assert.ok(p.dialog().all().every(node => !["IMG", "SCRIPT"].includes(node.tagName)));
  p.byClass("dt-confirm-cancel").dispatch("click");
  assert.equal(await answer, false);
  assert.deepEqual(seen, [["Nothing was saved.", "cancelled"]]); assert.equal(p.notice(), null);
  await turn();
});

test("vazgeçme iletisi tetikleyen açık pencerenin içine yazılır; süre dolunca boşalır; kopmuş düğmeye odak verilmez", async () => {
  const p = page();
  const host = new p.Element("dialog"); host.open = true; p.doc.body.append(host);
  const inside = new p.Element("button"); host.append(inside); inside.focus();
  const answer = p.confirm.ask({message: "Kaydedilmemiş değişikliklerin var. Bu taslağı bırakmak istiyor musun?"});
  assert.equal(p.dialog().parentNode, p.doc.body);
  assert.equal(p.notice().parentNode, host, "Canlı bölge onaydan önce açık pencerenin içinde hazır olmalı");
  const region = p.notice();
  inside.remove();
  p.byClass("dt-confirm-cancel").dispatch("click");
  assert.equal(await answer, false);
  assert.equal(p.notice(), region); assert.equal(p.notice().parentNode, host);
  assert.equal(p.notice().textContent, WORDS.cancelled[0]);
  assert.notEqual(p.doc.activeElement, inside);
  const timer = [...p.timers.values()].at(-1);
  assert.equal(timer.delay, 10000); timer.callback();
  assert.equal(p.notice().textContent, ""); assert.equal(p.notice().parentNode, host, "Bölge kalır, yalnız metin silinir");
  await turn();
});

test("vazgeçme iletisi önceden var olan canlı bölgeye yazılır; bölge her soruda yeniden oluşturulmaz", async () => {
  const p = page();
  const first = p.confirm.ask({message: "Birinci soru"});
  const region = p.notice();
  assert.ok(region); assert.equal(region.textContent, "");
  p.byClass("dt-confirm-cancel").dispatch("click");
  assert.equal(await first, false);
  assert.equal(p.notice(), region); assert.equal(region.textContent, WORDS.cancelled[0]);
  assert.equal([...p.timers.values()].filter(timer => timer.delay === 100).length, 0, "Var olan bölgede gecikme gerekmez");
  const second = p.confirm.ask({message: "İkinci soru"});
  assert.equal(p.notice(), region); assert.equal(region.textContent, "");
  assert.equal(p.doc.body.all().filter(node => node.classList.contains("dt-confirm-notice")).length, 1);
  p.byClass("dt-confirm-accept").dispatch("click");
  assert.equal(await second, true);
  await turn();
});

// Tarayıcının kendi confirm/alert/prompt penceresini açan her yazılış; yerel "function prompt(" tanımı çağrı sayılmaz.
const NATIVE_DIALOG_PATTERNS = [
  /\b(window|globalThis|self|top|parent|frames)\s*(\?\.|\.)\s*(confirm|alert|prompt)\b/,
  /\[\s*(["'`])(confirm|alert|prompt)\1\s*\]/,
  /(?<![\w.$])(?<!\bfunction\s+)(confirm|alert|prompt)\s*(\?\.\s*)?\(/m
];
const nativeDialogCall = code => NATIVE_DIALOG_PATTERNS.find(pattern => pattern.test(code)) || null;

test("koruma deseni tarayıcı penceresi açan her yazılışı yakalar; yerel adlara ve açıklamalara takılmaz", () => {
  for (const code of ["window.confirm('x')", "window?.confirm('x')", "window.confirm?.('x')", "globalThis.confirm('x')", "self.confirm('x')",
    "window[\"confirm\"]('x')", "globalThis['alert']('x')", "self[`prompt`]('x')", "prompt('x')", "if (!prompt(\"Ad?\")) return;",
    "confirm('x')", "alert('x')", "const ok = confirm ('x');", "top.alert('x')", "window.prompt", "x = confirm?.('y')"]) {
    assert.ok(nativeDialogCall(code), code);
  }
  for (const code of ["function prompt(ref, compact = false) {", "sharing.prompt({task_id: 1})", "return {prompt, open};", "confirmReplaceDraft(opener)",
    "api.ask({confirmLabel: t(\"Yeni taslak aç\")})", "const promptText = 'x';", "DevTrellisConfirm.ask({message})", "community-share-prompt"]) {
    assert.equal(nativeDialogCall(code), null, code);
  }
});

test("DevTrellis arayüzünde window.confirm veya alert kalmadı; onay kullanan her sayfa ortak bileşeni ve stilini yükler", () => {
  const scripts = fs.readdirSync(STATIC).filter(name => name.endsWith(".js"));
  for (const name of scripts) {
    // Açıklama satırları bu kusuru anlatabilir; yalnız çalışan kod denetlenir.
    const code = read(name).replace(/^\s*\/\/.*$/gm, "");
    assert.equal(nativeDialogCall(code), null, name);
  }
  const users = scripts.filter(name => name !== "confirm-dialog.js" && read(name).includes("DevTrellisConfirm"));
  assert.deepEqual(users.sort(), ["community.js", "contribution-submissions.js", "discovery.js", "objection-form.js", "objection-review.js", "platform.js"]);
  for (const name of fs.readdirSync(STATIC).filter(file => file.endsWith(".html"))) {
    const html = read(name);
    const sources = [...html.matchAll(/<script src="\/static\/([a-z0-9-]+\.js)"/g)].map(match => match[1]);
    if (!sources.some(source => users.includes(source))) continue;
    assert.ok(sources.includes("confirm-dialog.js"), `${name} confirm-dialog.js yüklemeli`);
    assert.match(html, /<link rel="stylesheet" href="\/static\/confirm-dialog\.css">/, `${name} confirm-dialog.css yüklemeli`);
    assert.doesNotMatch(html, /<script>(?!\s*<\/script>)/, `${name} satır içi betik eklenmemeli`);
  }
  const server = fs.readFileSync(path.join(__dirname, "../radar/topluluk_api.py"), "utf8");
  for (const script of ["objection-form.js", "objection-review.js"]) {
    assert.ok(server.includes(`<script src="{asset_url("confirm-dialog.js")}" defer></script><script src="{asset_url("${script}")}" defer></script>`), script);
  }
  const component = read("confirm-dialog.js"), styles = read("confirm-dialog.css");
  assert.doesNotMatch(component, /innerHTML|insertAdjacentHTML|\.style\b|setAttribute\("style"/);
  assert.doesNotMatch(styles, /@import|url\(/);
});

test("onay düğmeleri ve açılamama iletisinin EN karşılıkları ui-text sözlüğündedir", () => {
  for (const name of ["community.js", "platform.js", "discovery.js"]) {
    const lines = read(name).split(/\r?\n/).filter(line => /confirmLabel|Onay penceresi açılamadı|confirmAction\(\{message|ask\(\{message/.test(line));
    assert.ok(lines.length > 0, name);
    for (const line of lines) for (const match of line.matchAll(/\bt\("([^"\\]*(?:\\.[^"\\]*)*)"/g)) assert.ok(EN[match[1]], `${name}: ${match[1]}`);
  }
  for (const key of ["Taslağı bırak", "Değişiklikleri bırak", "Yeni taslak aç", "Onay penceresi açılamadı; işlem yapılmadı. Sayfayı yenileyip yeniden dene."]) assert.ok(EN[key], key);
});
