// Soru üzerinden araştırma başlatır ve kalıcı keşif sonuçlarını gösterir.
(() => {
  "use strict";
  const UI = globalThis.UfukUI || globalThis.window?.UfukUI;
  const t = (key, values = {}) => UI ? UI.t(key, values) : key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
  const locale = UI?.language === "en" ? "en-US" : "tr-TR";
  window.UFUKDiscoveryFactory = function (context) {
    const {element, state, topics, post, toast, formatDate, refreshAfterMutation, changeView} = context;
    let dialog = null, posting = false, requestKey = null;
    const emptyDraft = topic => ({question: "", need: "", topic: topic || Object.keys(topics)[0] || "ai", source_url: "", claim_text: "", context_text: ""});
    let draft = emptyDraft();
    const hasDraft = () => ["question", "need", "source_url", "claim_text", "context_text"].some(key => draft[key].trim());
    const statuses = {awaiting_sources: t("Kaynak aranıyor"), discovering: t("Kaynaklar taranıyor"), no_sources: t("Uygun kaynak bulunamadı"), queued: t("Araştırma sırasında"), running: t("Araştırılıyor"), ready: t("Rapor hazır"), partial: t("Kısmi sonuç"), failed: t("İşlem tamamlanamadı"), cancelled: t("İptal edildi"), interrupted: t("Keşif yarıda kaldı"), completed: t("Keşif tamamlandı"), blocked: t("Devam etmek için bekliyor")};
    function button(text, action, primary = false) {
      const node = element("button", `button button-${primary ? "primary" : "secondary"}`, text);
      node.type = "button"; node.addEventListener("click", action); return node;
    }
    function open(opener, prefill = null) {
      if (dialog || posting || !state.data) return;
      if (prefill) {
        if (hasDraft() && !window.confirm?.(t("Mevcut araştırma taslağını bırakıp bu araştırmanın bilgileriyle yeni bir taslak açmak istiyor musun?"))) return;
        draft = {...emptyDraft(prefill.topic), ...Object.fromEntries(["question", "need", "source_url", "claim_text", "context_text"].map(key => [key, typeof prefill[key] === "string" ? prefill[key] : ""]))};
        requestKey = null;
      }
      if (!Object.hasOwn(topics, draft.topic)) draft.topic = Object.keys(topics)[0] || "ai";
      const returnTo = opener || document.activeElement;
      const panel = element("dialog", "workflow-dialog workspace-dialog discovery-dialog");
      panel.setAttribute("aria-labelledby", "discovery-heading");
      const bar = element("div", "reader-toolbar"), heading = element("h2", "", t("Bir konuyu araştır")); heading.id = "discovery-heading";
      bar.append(heading, button(t("Kapat"), () => { if (!posting) panel.close(); }));
      const form = element("form", "discovery-form");
      form.append(element("p", "workspace-help", t("Sorunu yaz. UFUK ilgili yayınları bulup bir derin araştırma hazırlasın. Geliştirme kararını rapordan sonra verirsin.")));
      if (prefill) form.append(element("p", "workflow-notice", t("Yeni bir araştırma isteği hazırlıyorsun. Özgün rapor ve önceki kararların korunur; yalnız başlatınca yeni işlem yapılır.")));
      const provider = {nvidia: "NVIDIA", codex: "Codex", claude: "Claude"}[state.data.ai?.deep_provider] || "Claude";
      form.append(element("p", "workspace-help", t("Araştırma sağlayıcısı: {provider} · Model: {model}", {provider, model: state.data.ai?.deep_model || t("Bilinmiyor")})));
      form.append(element("p", "workspace-help", t("Sağlayıcıyı değiştirmek için kişisel platformdaki Araştırma sağlayıcısı ayarını açabilirsin.")));
      const providerLink = element("a", "workspace-help", t("Sağlayıcı ayarını aç")); providerLink.href = "/platform?providers=1"; providerLink.target = "_blank"; providerLink.rel = "noopener noreferrer"; form.append(providerLink);
      const error = element("p", "inline-error"); error.hidden = true; error.setAttribute("role", "alert"); form.append(error);
      function field(label, name, tag, maxLength, required = false) {
        const wrapper = element("div", "discovery-field"), caption = element("label", "", label), input = element(tag);
        caption.htmlFor = `discovery-${name}`; input.id = caption.htmlFor; input.required = required; input.maxLength = maxLength;
        if (tag === "select") for (const [value, text] of Object.entries(topics)) { const option = element("option", "", text); option.value = value; input.append(option); }
        input.value = draft[name]; input.addEventListener("input", () => { draft[name] = input.value; requestKey = null; });
        wrapper.append(caption, input); form.append(wrapper); return input;
      }
      const question = field(t("Ne öğrenmek istiyorsun?"), "question", "textarea", 500, true);
      question.rows = 4; question.minLength = 8; question.placeholder = t("Örneğin: Kaynaklara dayalı araştırmayı geliştirmek için hangi yöntemleri deneyebiliriz?");
      field(t("Çalışma alanı"), "topic", "select", 20);
      const need = field(t("İhtiyacımızı kısaca anlat (isteğe bağlı)"), "need", "textarea", 300);
      need.rows = 2; need.placeholder = t("Genel ihtiyaç yeterli; parola veya gizli geliştirme ayrıntısı yazma.");
      const sourceUrl = field(t("Sosyal gönderi veya haber bağlantısı (isteğe bağlı)"), "source_url", "input", 2048); sourceUrl.type = "url"; sourceUrl.placeholder = "https://…";
      const claim = field(t("İncelenecek iddia (isteğe bağlı)"), "claim_text", "textarea", 1000); claim.rows = 2;
      const contextText = field(t("Açıklama veya transkript (isteğe bağlı)"), "context_text", "textarea", 6000); contextText.rows = 4;
      form.append(element("p", "workspace-help", t("Yalnız herkese açık HTTPS bağlantıları okunabilir. Oturum gerektiren veya erişilemeyen gönderiler için iddiayı ve paylaşma hakkın olan metni ekle. Video/ses izlenmez; eklediğin transkript bağımsız olarak doğrulanmış sayılmaz.")));
      form.append(element("p", "workspace-help", t("En fazla 10 kaynak adayı ve 1 derin araştırma. Günlük bütçe doluysa sırada bekler. Kapatınca taslak bu pencerede korunur.")));
      const submit = element("button", "button button-primary", t("Araştırmayı başlat")); submit.type = "submit"; form.append(submit);
      form.addEventListener("submit", async event => {
        event.preventDefault(); if (posting || !form.reportValidity()) return;
        if (draft.source_url.trim()) {
          try { const url = new URL(draft.source_url.trim()); if (url.protocol !== "https:" || url.username || url.password) throw new Error(); }
          catch { error.textContent = t("Kullanıcı adı veya parola içermeyen, herkese açık bir HTTPS bağlantısı gir."); error.hidden = false; sourceUrl.focus(); return; }
        }
        posting = true; error.hidden = true;
        requestKey ||= window.crypto?.randomUUID?.() || `ufuk-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        for (const control of panel.querySelectorAll("button,input,textarea,select")) control.disabled = true;
        state.pending.add("research-request");
        try {
          await post("/api/research-requests", {...draft, scope: [], budget: {max_candidates: 10, max_deep_tasks: 1}, idempotency_key: requestKey});
          draft = emptyDraft(draft.topic); requestKey = null; panel.close();
          await refreshAfterMutation(); changeView("research"); toast(t("Araştırma isteğin kaydedildi. Kaynak aramasını ve raporu buradan takip edebilirsin."));
        } catch (failure) { error.textContent = failure.message || t("Araştırma başlatılamadı."); error.hidden = false; }
        finally { posting = false; state.pending.delete("research-request"); for (const control of panel.querySelectorAll("button,input,textarea,select")) control.disabled = false; }
      });
      panel.addEventListener("cancel", event => { if (posting) event.preventDefault(); });
      panel.addEventListener("close", () => { panel.remove(); dialog = null; if (returnTo?.isConnected) returnTo.focus(); });
      panel.append(bar, form); document.body.append(panel); dialog = panel; panel.showModal(); question.focus();
    }
    async function cancel(row, control) {
      if (posting) return; posting = true; control.disabled = true;
      try { await post(`/api/research-requests/${encodeURIComponent(row.id)}/cancel`, {expected_version: row.version}); await refreshAfterMutation(); toast(t("Araştırma isteği iptal edildi; alınmış sonuçlar korunur.")); }
      catch (error) { toast(error.message, true); } finally { posting = false; control.disabled = false; }
    }
    function renderRequests(container) {
      const section = element("section", "discovery-requests"), header = element("div", "discovery-heading");
      header.append(element("h3", "", t("Sorduğun konular")), button(t("Yeni konu araştır"), event => open(event.currentTarget), true)); section.append(header);
      const rows = (state.data?.research_requests?.requests || []).filter(row => (state.topic === "all" || state.topic === row.topic) && (!state.search || `${row.question} ${row.need || ""} ${row.claim_text || ""} ${row.context_text || ""}`.toLocaleLowerCase(locale).includes(state.search.toLocaleLowerCase(locale))));
      if (!rows.length) section.append(element("p", "workspace-help", t("Bir haber seçmeden de araştırma isteyebilirsin. Soruyu sen ver, kaynakları UFUK bulsun.")));
      for (const row of rows) {
        const card = element("article", "discovery-request"); card.append(element("span", "workflow-stage", statuses[row.status] || t("Durum bekleniyor")), element("h4", "", row.question));
        if (row.message) card.append(element("p", "", row.message)); if (row.need) card.append(element("p", "workspace-help", row.need));
        if (row.source_url) {
          const access = row.link_access || {status: "pending"};
          const states = {pending: t("Bağlantı metni bekleniyor"), read: t("Sayfanın erişilebilen metni okundu"), unavailable: t("Bağlantı içeriğine erişilemedi"), not_provided: t("Bağlantı verilmedi")};
          const details = element("details", "workflow-evidence"); details.append(element("summary", "", states[access.status] || t("Durum bekleniyor")));
          details.append(context.sourceLink(row.source_url, t("Verilen bağlantı")));
          const reasons = {pending: "Bağlantı metni henüz alınmadı.", html_text: "Yalnız sayfanın erişilebilen HTML metni alındı.", access_restricted: "Sayfa giriş veya erişim izni gerektiriyor.", http_error: "Sayfa bir HTTP hatası döndürdü.", unsupported_content: "Bağlantı desteklenmeyen bir içerik türü döndürdü.", text_unavailable: "Okunabilir sayfa metni bulunamadı.", fetch_blocked: "Bağlantı güvenli erişim denetimini geçmedi.", fetch_failed: "Bağlantı metni alınamadı."};
          if (reasons[access.reason_code] || access.message) details.append(element("p", "workspace-help", t(reasons[access.reason_code] || access.message)));
          if (access.fetched_at) details.append(element("p", "workspace-help", t("Erişim denemesi: {date}", {date: formatDate(access.fetched_at, true)})));
          details.append(element("p", "workspace-help", t("Video veya ses işlenmedi. Sayfa metni ve kullanıcı açıklaması, medyanın izlendiği veya iddianın doğrulandığı anlamına gelmez.")));
          if (access.status === "unavailable") details.append(element("p", "workspace-help", t("Verdiğin açıklama ve bulunabilen bağımsız kaynaklarla araştırma sürdürülebilir; erişilemeyen gönderi kanıt olarak gösterilmez.")));
          card.append(details);
        }
        if (row.claim_text) card.append(element("p", "workspace-help", t("İncelenen iddia: {claim}", {claim: row.claim_text})));
        if (row.context_text) { const contextText = element("details"); contextText.append(element("summary", "", t("Eklediğin açıklama veya transkript")), element("p", "discovery-context", row.context_text)); card.append(contextText); }
        const ids = row.task_ids || (row.tasks || []).map(task => task.id || task.task_id), actions = element("div", "workflow-actions");
        for (const id of ids) actions.append(button(t("Araştırmayı aç"), event => window.UFUKWorkflow?.open(id, event.currentTarget)));
        if (!["ready", "failed", "cancelled", "partial", "no_sources"].includes(row.status)) { const control = button(t("İsteği iptal et"), () => cancel(row, control)); actions.append(control); }
        card.append(actions, element("small", "workspace-help", `${topics[row.topic] || row.topic} · ${formatDate(row.created_at, true)}`)); section.append(card);
      }
      container.append(section);
    }
    async function startDiscovery(topic = null) {
      if (posting) return;
      posting = true;
      try { await post("/api/discovery", {topic, query: null}); await refreshAfterMutation(); toast(t("Konu araması başladı. Bulunan yayınlar kaynak havuzuna eklenecek.")); }
      catch (error) { toast(error.message, true); } finally { posting = false; }
    }
    function renderDiscovery(container) {
      const data = state.data?.discovery || {}, section = element("section", "discovery-overview"), heading = element("div", "discovery-heading");
      heading.append(element("h3", "", t("Konu üzerinden kaynak keşfi")));
      const start = button(data.running ? t("Keşif sürüyor…") : t("Kaynak keşfet"), async () => {
        start.disabled = true;
        try { await startDiscovery(state.topic === "all" ? null : state.topic); } finally { start.disabled = Boolean(state.data?.discovery?.running); }
      });
      start.disabled = Boolean(data.running); heading.append(start); section.append(heading);
      section.append(element("p", "", data.message || t("Haftalık konu aramaları kayıtlı haber akışlarını tamamlar. Eski temel yayınlar tarihleri korunarak havuza alınır.")));
      if (data.next_due_at) section.append(element("p", "workspace-help", t("Sonraki konu taraması: {date}", {date: formatDate(data.next_due_at, true)})));
      const runs = data.runs || data.recent_runs || (data.last_run ? [data.last_run] : []);
      if (runs.length) {
        const history = element("details"); history.append(element("summary", "", t("Son keşiflerin sonuçları · {count}", {count: runs.length})));
        for (const row of runs.slice(0, 10)) {
          const line = element("div", "discovery-run");
          line.append(element("strong", "", row.query || topics[row.topic] || t("Tüm alanlarda konu araması")), element("p", "", row.message || t("{status} · {added} yeni kayıt · {existing} mevcut kayıt", {status: statuses[row.status] || t("Durum bekleniyor"), added: row.added ?? row.added_count ?? 0, existing: row.duplicates || 0})));
          for (const search of row.searches || []) line.append(element("p", "workspace-help", t("{provider} · {query} · {count} sonuç", {provider: search.provider, query: search.query, count: search.count})));
          if (row.relations?.length) line.append(element("p", "workspace-help", t("{count} yayın, bulunan çalışmaların kaynakçasından takip edildi.", {count: row.relations.length})));
          for (const error of row.errors || []) line.append(element("p", "inline-error", `${error.provider || t("Keşif")}: ${error.message}`));
          if (row.error) line.append(element("p", "inline-error", row.error));
          history.append(line);
        }
        section.append(history);
      }
      container.append(section);
    }
    window.addEventListener("beforeunload", event => {
      if (posting || hasDraft()) { event.preventDefault(); event.returnValue = ""; }
    });
    return {open, renderRequests, renderDiscovery, start: startDiscovery, entry: () => {
      const control = button(t("Bir konu araştır"), event => open(event.currentTarget)); control.classList.add("discovery-entry"); return control;
    }, busy: () => Boolean(dialog || posting)};
  };
})();
