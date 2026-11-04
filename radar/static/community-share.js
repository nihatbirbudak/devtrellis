// Yerel araştırmayı koruyarak yalnız önizlenmiş ve açıkça izin verilmiş kopyayı topluluğa gönderir.
(() => {
  "use strict";
  const UI = globalThis.UfukUI || globalThis.window?.UfukUI;
  const t = (key, values = {}) => UI ? UI.t(key, values) : key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
  const safeURL = value => { try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; } };
  window.DevTrellisShareFactory = function ({element, post, formatDate}) {
    let active = null;
    const choices = new Map();
    const storageKey = ref => `devtrellis.share-choice.${ref.task_id ? "task" : "entry"}.${ref.task_id || ref.entry_id}`;
    const choice = ref => { if (choices.has(storageKey(ref))) return choices.get(storageKey(ref)); try { return localStorage.getItem(storageKey(ref)); } catch { return null; } };
    function remember(ref, value) { choices.set(storageKey(ref), value); try { localStorage.setItem(storageKey(ref), value); } catch { /* Yerel depolama kapalıysa seçim bu pencerede korunur. */ } }
    function button(label, handler, primary = false) { const node = element("button", `button button-${primary ? "primary" : "secondary"}`, label); node.type = "button"; node.addEventListener("click", handler); return node; }
    function link(label, address) { const url = safeURL(address); if (!url) return element("span", "", label); const node = element("a", "", label); node.href = url; node.target = "_blank"; node.rel = "noopener noreferrer"; return node; }
    function packetView(packet) {
      const box = element("div", "share-packet-view");
      box.append(element("h3", "", packet.title), element("p", "share-summary", packet.summary), element("p", "share-report-text", packet.body || ""));
      const facts = element("dl", "share-facts");
      for (const [key, value] of [[t("Yazar"), packet.author], [t("Metin izni"), packet.license], [t("Güncellendi"), formatDate(packet.updated_at, true)], ...(UI?.provenanceFacts?.(packet.provenance || {model: "unknown", generated_at: null}, t, value => formatDate(value, true)) || [])]) facts.append(element("dt", "", key), element("dd", "", value || t("Belirtilmedi")));
      box.append(facts);
      if (packet.provenance?.modified) box.append(element("p", "share-disclosure", t("Kart içeriği özgün rapordan sonra değiştirildi; yeniden kanıt denetimi yapılmadı.")));
      if (packet.url) box.append(link(t("Proje / araştırma bağlantısı ↗"), packet.url));
      const sources = element("ul", "share-sources");
      for (const source of packet.sources || []) { const row = element("li"); row.append(link(source.title || source.url, source.url)); sources.append(row); }
      box.append(element("h4", "", t("Kaynaklar")), sources, element("p", "share-disclosure", t("Modelin markası veya fiyatı doğruluk kanıtı değildir; kaynakları, tarihi ve raporun sınırlarını kontrol et.")));
      const full = element("details", "share-full-payload"); full.append(element("summary", "", t("Gönderilecek verinin tamamı")), element("pre", "", JSON.stringify(packet, null, 2))); box.append(full);
      return box;
    }
    function prompt(ref, compact = false) {
      const section = element("section", `community-share-prompt${compact ? " compact" : ""}`);
      function draw() {
        section.replaceChildren();
        const saved = choice(ref);
        section.append(element("h3", "", saved === "sent" ? t("Bir kopya topluluğa gönderildi") : saved === "kept" ? t("Bu araştırmayı yerelde tutuyorsun") : t("Toplulukla paylaşmak ister misin?")), element("p", "", t("Özgün rapor bu bilgisayarda kalır. Hesap açmadan, seçtiğin kopyayı önizleyip paylaşabilirsin.")));
        const actions = element("div", "community-share-actions");
        if (saved === "sent") { const status = element("a", "button button-secondary", t("Gönderimlerimi gör")); status.href = "/platform#community-submissions"; actions.append(status); }
        else actions.append(button(t(saved === "kept" ? "Paylaşmayı değerlendir" : "Önizle ve paylaş"), event => open(ref, event.currentTarget, draw), true));
        if (!saved) actions.append(button(t("Yerelde tut"), () => { remember(ref, "kept"); draw(); }));
        section.append(actions);
      }
      draw(); return section;
    }
    async function open(ref, opener, onChange = () => {}) {
      if (active) return;
      const dialog = element("dialog", "community-share-dialog"); dialog.setAttribute("aria-labelledby", "direct-share-title");
      const heading = element("h2", "", t("Toplulukla paylaşmadan önce")); heading.id = "direct-share-title";
      const header = element("div", "community-share-header");
      const close = button(t("Kapat"), () => closeSession(false)); header.append(heading, close);
      const body = element("div", "community-share-body");
      body.append(element("p", "share-disclosure", t("Bu önizleme yereldir. Gönder düğmesine basana kadar topluluğa içerik aktarılmaz. Özgün raporun değişmez.")));
      const destination = element("p", "share-destination"); body.append(destination);
      const fields = element("details", "share-edit-fields"); fields.append(element("summary", "", t("Paylaşılacak kopyayı düzenle")));
      const controls = {}, captions = {};
      for (const [name, label, tag, limit] of [["author", "Görünen ad (isteğe bağlı)", "input", 100], ["summary", "Kısa açıklama", "textarea", 600], ["body", "Paylaşılacak metin", "textarea", 12000], ["question", "Paylaşılacak araştırma sorusu", "textarea", 500], ["claim_text", "Paylaşılacak iddia", "textarea", 1000]]) {
        const caption = element("label", "", t(label)); const input = element(tag, "share-input"); input.id = `direct-share-${name}`; caption.htmlFor = input.id; input.maxLength = limit; if (tag === "textarea") input.rows = name === "body" ? 7 : 3; fields.append(caption, input); controls[name] = input; captions[name] = caption;
        if (["question", "claim_text"].includes(name)) input.hidden = caption.hidden = true;
      }
      const update = button(t("Önizlemeyi güncelle"), () => prepare(true)); fields.append(update, element("p", "share-disclosure", t("Yalnız paylaşılacak kopyayı düzenlersin. Değişiklikten sonra önizlemeyi yenileyip izni yeniden onayla."))); body.append(fields);
      const content = element("div"); content.setAttribute("aria-live", "polite"); body.append(content);
      const permission = element("label", "share-consent"); const consent = element("input"); consent.type = "checkbox"; consent.checked = false;
      permission.append(consent, element("span", "", t("Bu önizlemedeki içeriği paylaşmaya hakkım var ve CC BY 4.0 ile topluluğa gönderilmesini onaylıyorum."))); body.append(permission, link(t("CC BY 4.0 koşullarını oku ↗"), "https://creativecommons.org/licenses/by/4.0/"));
      body.append(element("p", "share-disclosure", t("İzin yalnız gönderdiğin metin içindir; bağlı kaynakların ve kodun izinlerini değiştirmez. Gönderim incelemeye gider.")));
      const error = element("p", "share-error"); error.hidden = true; error.setAttribute("role", "alert"); body.append(error);
      const footer = element("div", "community-share-actions"); const keep = button(t("Yerelde tut"), () => closeSession(true)); const send = button(t("Onayladığım kopyayı gönder"), submit, true); footer.append(keep, send);
      dialog.append(header, body, footer); document.body.append(dialog);
      const session = {dialog, ref, opener, onChange, busy: false, dirty: false, attempted: false, preview: null, controls, destination, content, consent, permission, send, keep, update, error, close, fields}; active = session;
      function updateButtons() { send.disabled = session.busy || !session.preview || session.dirty || !consent.checked; update.disabled = session.busy; close.disabled = session.busy; keep.disabled = session.busy; consent.disabled = session.busy || session.dirty || !session.preview; for (const input of Object.values(controls)) input.disabled = session.busy; }
      function fail(message) { error.textContent = message; error.hidden = false; }
      for (const input of Object.values(controls)) input.addEventListener("input", () => { session.dirty = true; consent.checked = false; updateButtons(); });
      consent.addEventListener("change", updateButtons);
      async function prepare(edited = false) {
        if (session.busy) return;
        session.busy = true; consent.checked = false; error.hidden = true; updateButtons();
        try {
          const payload = {...ref}; if (edited) for (const key of Object.keys(controls)) { if (!["question", "claim_text"].includes(key) || session.preview?.packet?.provenance) payload[key] = controls[key].value; }
          const value = await post("/api/platform/community/preview", payload);
          if (active !== session) return;
          if (!value?.preview_id || !value?.packet || value.packet.license !== "CC-BY-4.0") throw new Error(t("Paylaşım önizlemesi alınamadı. Yeniden deneyebilirsin."));
          session.preview = value; session.dirty = false;
          for (const key of Object.keys(controls)) {
            const metadata = ["question", "claim_text"].includes(key);
            controls[key].value = (metadata ? value.packet.provenance?.[key] : value.packet[key]) || "";
            if (metadata) controls[key].hidden = captions[key].hidden = !value.packet.provenance;
          }
          destination.textContent = t("Gönderim adresi: {destination}", {destination: value.destination});
          content.replaceChildren(packetView(value.packet));
          if (value.notice) content.append(element("p", "share-disclosure", t(value.notice)));
        } catch (failure) { fail(t(failure.message || "Paylaşım önizlemesi alınamadı. Yeniden deneyebilirsin.")); }
        finally { session.busy = false; updateButtons(); }
      }
      async function submit() {
        if (session.busy || session.dirty || !session.preview || !consent.checked) return;
        session.busy = true; session.attempted = true; error.hidden = true; updateButtons();
        try {
          const result = await post("/api/platform/community/send", {preview_id: session.preview.preview_id, consent: true});
          if (active !== session) return;
          if (!result?.submission?.id) throw new Error(t("Gönderim durumu doğrulanamadı. Aynı önizlemeyle yeniden deneyebilirsin."));
          remember(ref, "sent"); onChange();
          const status = result?.submission?.status;
          const names = {pending: "İnceleme bekliyor", published: "Yayımlandı", rejected: "Reddedildi", withdrawn: "Geri çekildi", hidden: "Yayından kaldırıldı"};
          content.replaceChildren(element("h3", "", t("Gönderimin durumu: {status}", {status: t(names[status] || "Bilinmiyor")})), element("p", "", t("Özgün raporun yerelde duruyor. Gönderimin durumunu ve geri çekme seçeneğini kişisel platformda bulabilirsin.")));
          const statusLink = element("a", "button button-secondary", t("Gönderimlerimi gör")); statusLink.href = "/platform#community-submissions"; content.append(statusLink);
          session.preview = null; permission.hidden = true; fields.hidden = true; send.hidden = true; keep.textContent = t("Kapat");
        } catch (failure) { fail(t(failure.message || "Gönderim tamamlanamadı. Önizlemen korunuyor; aynı onayla yeniden deneyebilirsin.")); keep.textContent = t("Kapat"); }
        finally { session.busy = false; updateButtons(); }
      }
      function closeSession(keepLocal) { if (session.busy) return; if (keepLocal && !session.attempted) { remember(ref, "kept"); onChange(); } dialog.close(); }
      dialog.addEventListener("cancel", event => { event.preventDefault(); closeSession(false); });
      dialog.addEventListener("close", () => { if (active === session) active = null; dialog.remove(); if (opener?.isConnected) opener.focus(); });
      dialog.showModal(); close.focus(); await prepare();
    }
    window.addEventListener("beforeunload", event => { if (active?.busy || active?.dirty) { event.preventDefault(); event.returnValue = ""; } });
    return {prompt, open, busy: () => Boolean(active)};
  };
})();
