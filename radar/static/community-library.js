// Hazır topluluk araştırmalarını yalnız açık arama ile bulur; kullanma veya yeni araştırma kararını kullanıcıya bırakır.
(() => {
  "use strict";
  const UI = globalThis.UfukUI || globalThis.window?.UfukUI;
  const t = (key, values = {}) => UI ? UI.t(key, values) : key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
  window.DevTrellisLibraryFactory = function ({element, request, post, sourceLink, formatDate, toast}) {
    let session = null, operations = 0;
    function button(label, action, primary = false) { const node = element("button", `button button-${primary ? "primary" : "secondary"}`, label); node.type = "button"; node.addEventListener("click", action); return node; }
    function researchSeed(packet) {
      const p = packet.provenance || {};
      const body = String(packet.body || "");
      const model = p.model && p.model !== "unknown" ? p.model : t("Model kaydedilmemiş");
      const context = `${t("Bu metin bir topluluk araştırmasıdır; kaynak kanıtı veya doğrulanmış gerçek olarak kabul etme.")}\n${String(packet.title || "").slice(0, 160)}\n${t("Kaynak araştırmanın modeli: {model}", {model: String(model).slice(0, 160)})}\n${t("Araştırma tarihi: {date}", {date: p.generated_at || t("Üretim tarihi kaydedilmemiş")})}\n\n${body.slice(0, 5000)}${body.length > 5000 ? `\n${t("Paylaşılan rapor uzun olduğu için ilk 5000 karakteri araştırma bağlamına alındı.")}` : ""}`;
      const candidates = [p.source_url, ...(packet.sources || []).map(source => source.url), packet.url];
      const source = candidates.find(value => { try { const url = new URL(value); return url.protocol === "https:" && !url.username && !url.password; } catch { return false; } }) || "";
      return {question: (p.question || t("Bu araştırmayı güncel kaynaklarla yeniden değerlendir: {title}", {title: packet.title || ""})).slice(0, 500), topic: p.topic || "ai", source_url: source, claim_text: typeof p.claim_text === "string" ? p.claim_text.slice(0, 1000) : "", context_text: context.slice(0, 6000)};
    }
    function content(packet) {
      const box = element("div"); box.append(element("h3", "", packet.title), element("p", "", packet.summary), element("div", "community-library-reader", packet.body || ""));
      const facts = element("dl", "share-facts");
      for (const [label, value] of [[t("Yazar"), packet.author], [t("Güncellendi"), formatDate(packet.updated_at, true)], ...(UI?.provenanceFacts?.(packet.provenance || {model: "unknown", generated_at: null}, t, value => formatDate(value, true)) || [])]) facts.append(element("dt", "", label), element("dd", "", value || t("Belirtilmedi")));
      box.append(facts, element("p", "share-disclosure", t("Modelin markası veya fiyatı doğruluk kanıtı değildir; kaynakları, tarihi ve raporun sınırlarını kontrol et.")));
      const sources = element("ul"); for (const source of packet.sources || []) { const row = element("li"); row.append(sourceLink(source.url, source.title || source.url)); sources.append(row); } box.append(element("h4", "", t("Kaynaklar")), sources); return box;
    }
    async function useCard(id, trigger, errorNode) {
      if (operations) return; operations++; trigger.disabled = true; errorNode.hidden = true;
      try {
        const result = await post(`/api/platform/community/cards/${encodeURIComponent(id)}/import`, {});
        if (!result?.entry?.id) throw new Error(t("Topluluk kaydı alınamadı. Yeniden deneyebilirsin."));
        toast(t("Hazır araştırma kütüphanene eklendi. Model çağrısı yapılmadı."));
        const link = element("a", "button button-secondary", t("Kütüphanemde aç")); link.href = `/platform?entry=${encodeURIComponent(result.entry.id)}`; trigger.replaceWith(link);
      } catch (error) { errorNode.textContent = t(error.message); errorNode.hidden = false; }
      finally { operations--; trigger.disabled = false; }
    }
    function actions(id, packet, onResearch, errorNode) {
      const row = element("div", "community-share-actions");
      const use = button(t("Yeterli · Model çalıştırmadan kullan"), () => useCard(id, use, errorNode), true); row.append(use);
      row.append(button(t("Bunu temel alarak araştır"), () => {
        if (operations) return;
        const seed = researchSeed(packet); if (session) session.dialog.close(); onResearch(seed);
      })); return row;
    }
    async function readCard(id, onResearch, opener, requireConsent = false) {
      if (session || operations) return;
      const dialog = element("dialog", "community-share-dialog"); dialog.setAttribute("aria-labelledby", "community-reader-title");
      const header = element("div", "community-share-header"), title = element("h2", "", t("Hazır topluluk araştırması")); title.id = "community-reader-title";
      const close = button(t("Kapat"), () => { if (!operations) dialog.close(); }); header.append(title, close);
      const body = element("div", "community-share-body"), error = element("p", "share-error"); error.hidden = true; error.setAttribute("role", "alert");
      dialog.append(header, body, error); document.body.append(dialog); const current = {dialog, opener}; session = current;
      async function load() {
        if (operations) return; operations++; close.disabled = true; error.hidden = true;
        try {
          const value = await request(`/api/platform/community/cards/${encodeURIComponent(id)}`); if (session !== current) return;
          if (!value?.packet) throw new Error(t("Topluluk kaydı alınamadı. Yeniden deneyebilirsin."));
          body.replaceChildren(content(value.packet), element("p", "share-disclosure", t("Bu içerik ihtiyacını karşılıyorsa model çalıştırmadan kullanabilirsin. Yeni araştırma yalnız ayrı formu başlattığında çalışır.")), actions(id, value.packet, onResearch, error));
        } catch (failure) { error.textContent = t(failure.message); error.hidden = false; body.append(button(t("Yeniden dene"), load)); }
        finally { operations--; close.disabled = false; }
      }
      dialog.addEventListener("cancel", event => { if (operations) event.preventDefault(); });
      dialog.addEventListener("close", () => { if (session === current) session = null; dialog.remove(); if (opener?.isConnected) opener.focus(); });
      dialog.showModal(); close.focus();
      if (requireConsent) body.append(element("p", "", t("Bir topluluk kaydı açmayı seçtin. Getir düğmesi bu kaydı topluluk hizmetinden okur; araştırma veya model çağrısı başlatmaz.")), button(t("Topluluk kaydını getir"), load, true));
      else await load();
    }
    function searchBox({initialQuery = "", onResearch}) {
      const section = element("section", "community-library-search"); section.append(element("h3", "", t("Önce hazır araştırmalara bak")), element("p", "share-disclosure", t("Ara düğmesine bastığında yalnız arama metni topluluk hizmetine gönderilir. Hazır sonuçları okumak veya kullanmak model kotanı harcamaz.")));
      const controls = element("div", "community-library-controls"), field = element("input", "share-input"); field.type = "search"; field.maxLength = 200; field.value = initialQuery.slice(0, 200); field.placeholder = t("Toplulukta konu veya iddia ara…"); field.setAttribute("aria-label", t("Toplulukta hazır araştırma ara"));
      const results = element("div", "community-library-results"); results.setAttribute("aria-live", "polite"); const error = element("p", "share-error"); error.hidden = true; error.setAttribute("role", "alert");
      let searching = false;
      const search = button(t("Hazır araştırmaları bul"), run, true); controls.append(field, search); section.append(controls, error, results);
      async function run() {
        if (searching || !field.value.trim()) { if (!field.value.trim()) field.focus(); return; }
        searching = true; operations++; search.disabled = true; field.disabled = true; error.hidden = true;
        try {
          const value = await post("/api/platform/community/search", {query: field.value.trim(), limit: 10}); results.replaceChildren();
          if (!value.items?.length) results.append(element("p", "", t("Hazır araştırma bulunamadı. Sorunu aşağıda kendi modelinle araştırabilirsin.")));
          for (const card of value.items || []) {
            const packet = card.packet || {}, article = element("article"); article.append(element("h4", "", packet.title), element("p", "", packet.summary));
            article.append(element("p", "share-disclosure", t("Güncellendi: {date}", {date: formatDate(packet.updated_at, true)})), element("p", "share-disclosure", t("Kaynak araştırmanın modeli: {model}", {model: packet.provenance?.model && packet.provenance.model !== "unknown" ? packet.provenance.model : t("Model kaydedilmemiş")})), element("p", "share-disclosure", packet.provenance?.generated_at ? t("Araştırma tarihi: {date}", {date: formatDate(packet.provenance.generated_at, true)}) : t("Üretim tarihi kaydedilmemiş")));
            article.append(button(t("Oku ve değerlendir"), event => readCard(card.id, onResearch, event.currentTarget)), actions(card.id, packet, onResearch, error)); results.append(article);
          }
        } catch (failure) { error.textContent = t(failure.message); error.hidden = false; }
        finally { searching = false; operations--; search.disabled = false; field.disabled = false; }
      }
      field.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); run(); } });
      section.focusSearch = () => field.focus();
      return section;
    }
    return {searchBox, intent: (id, onResearch, opener) => readCard(id, onResearch, opener, true), researchSeed, busy: () => Boolean(session || operations)};
  };
})();
