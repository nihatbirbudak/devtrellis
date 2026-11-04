// Makale metnini ve kaynaklara dayalı yapay zekâ raporlarını kalıcı iş durumlarıyla gösterir.
"use strict";

window.UFUKInsightsFactory = function (context) {
  const UI = globalThis.UfukUI || globalThis.window?.UfukUI;
  const t = (key, values = {}) => UI ? UI.t(key, values) : key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
  const { element, state, request, post, formatDate, sourceLink, refreshAfterMutation } = context;
  const entries = new Map();
  const labels = { summary: t("Türkçe özet"), article: t("Makale metni"), research: t("Kaynakları karşılaştır") };
  let disposed = false;

  function button(label, action, className = "button button-secondary") {
    const node = element("button", className, label);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  }
  function active(entry) { return !disposed && state.readerItemId === String(entry.item.id) && document.visibilityState !== "hidden"; }
  function working(data) { return ["okunuyor", "calisiyor"].includes(data?.status); }
  function modelStatus(mode) {
    const ai = state.data.ai || {};
    const provider = ai.deep_provider === "codex" ? "Codex" : "Claude";
    return mode === "research" ? {configured: ai.deep_configured, daily_remaining: ai.deep_daily_remaining, budget_resets_at: ai.deep_budget_resets_at || ai.budget_resets_at, model: ai.deep_model || provider, provider}
      : {...ai, provider: "NVIDIA"};
  }
  function endpoint(entry, mode) {
    const path = `/api/items/${encodeURIComponent(entry.item.id)}`;
    return mode === "article" ? `${path}/article` : `${path}/ai?kind=${mode}`;
  }
  function schedule(entry) {
    clearTimeout(entry.timer);
    entry.timer = null;
    if (!active(entry) || !Object.values(entry.data).some(working)) return;
    entry.timer = setTimeout(async () => {
      if (!active(entry)) return;
      await Promise.all(Object.keys(entry.data).filter(mode => working(entry.data[mode])).map(mode => load(entry, mode)));
      schedule(entry);
    }, 1800);
  }
  function load(entry, mode) {
    if (entry.requests.has(mode)) return entry.requests.get(mode);
    const epoch = entry.epochs[mode] || 0;
    entry.loading.add(mode);
    const operation = (async () => { try {
      const previous = entry.data[mode];
      const data = await request(endpoint(entry, mode));
      if (!data || typeof data.status !== "string") throw new Error(t("Okuma kaydı beklenen biçimde gelmedi."));
      if (disposed || epoch !== (entry.epochs[mode] || 0)) return;
      const changed = JSON.stringify(previous) !== JSON.stringify(data) || Boolean(entry.errors[mode]);
      entry.data[mode] = data;
      delete entry.errors[mode];
      if (working(previous) && !working(data)) refreshAfterMutation(true);
      if (mode === entry.mode && changed) entry.redraw = true;
    } catch (error) {
      if (!disposed && epoch === (entry.epochs[mode] || 0)) { entry.errors[mode] = error.message; if (mode === entry.mode) entry.redraw = true; }
    } finally {
      entry.loading.delete(mode); entry.requests.delete(mode);
      if (!disposed && mode === entry.mode && entry.redraw) { entry.redraw = false; draw(entry); }
    } })();
    entry.requests.set(mode, operation);
    return operation;
  }
  async function reload(entry, mode) {
    entry.epochs[mode] = (entry.epochs[mode] || 0) + 1;
    const pending = entry.requests.get(mode);
    if (pending) await pending;
    if (!disposed) await load(entry, mode);
  }
  async function start(entry, mode) {
    if (entry.posting) return;
    entry.epochs[mode] = (entry.epochs[mode] || 0) + 1;
    entry.posting = mode;
    draw(entry);
    try {
      const path = endpoint(entry, mode).split("?")[0];
      entry.data[mode] = await post(path, mode === "article" ? {} : { kind: mode });
      delete entry.errors[mode];
      if (entry.requests.has(mode)) await entry.requests.get(mode);
      await load(entry, mode);
    } catch (error) { entry.errors[mode] = error.message; }
    finally { entry.posting = null; if (!disposed && mode === entry.mode) draw(entry); schedule(entry); }
  }
  function listSection(parent, title, values) {
    if (!Array.isArray(values) || !values.length) return;
    parent.append(element("h4", "", title));
    const list = element("ul", "insight-list");
    for (const value of values) list.append(element("li", "", value));
    parent.append(list);
  }
  function draw(entry) {
    const focused = document.activeElement;
    const focusKey = entry.node.contains(focused) ? focused.dataset.insightAction : null;
    const mode = entry.mode;
    const data = entry.data[mode];
    const model = modelStatus(mode);
    const panel = entry.panel;
    for (const tab of entry.tabs.children) {
      const selected = tab.dataset.mode === mode;
      tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
    }
    panel.setAttribute("aria-labelledby", `insight-${entry.item.id}-${mode}`);
    panel.replaceChildren();
    const explanation = mode === "article"
      ? t("Asıl sayfanın okunabilen makale metnini bu bilgisayara al. Sonraki açılışta internet olmadan da okuyabilirsin.")
      : mode === "research"
        ? model.provider === "Codex"
          ? t("Codex, uygulamanın sağladığı kaynak metinlerini karşılaştırır. Bu bağlantı yeni web araması yapmaz.")
          : t("Claude, havuzdaki yayınları ve gerektiğinde web kaynaklarını karşılaştırır. Rapor, ortak bulguları, ayrılan noktaları ve belirsizlikleri gösterir.")
        : t("NVIDIA, kaynak metninden Türkçe özet, olası yarar ve sınırlamalar hazırlar. Değerlendirmeyi asıl kaynakla birlikte oku.");
    panel.append(element("p", "insight-help", explanation));
    if (!data && entry.loading.has(mode)) panel.append(element("p", "insight-status", t("Kayıtlı sonuç kontrol ediliyor…")));
    if (entry.errors[mode]) {
      const error = element("p", "insight-error", t(entry.errors[mode])); error.setAttribute("role", "alert");
      const retry = button(t("Durumu yeniden oku"), () => load(entry, mode).then(() => schedule(entry)));
      retry.dataset.insightAction = "retry";
      panel.append(error, retry);
    }
    if (data?.stale) panel.append(element("p", "insight-warning", t("Kaynak bu sonuçtan sonra değişmiş. Aşağıdaki eski kayıt korunuyor; güncellemek için yeniden hazırla.")));
    if (working(data)) {
      const status = element("p", "insight-status", mode === "article" ? t("Kaynak sayfası okunuyor…") : t("Kaynaklar inceleniyor ve Türkçe rapor hazırlanıyor…"));
      status.setAttribute("role", "status");
      panel.append(status, element("p", "insight-help", t("Bu pencereyi kapatabilirsin. Uygulama açıkken işlem devam eder; sonucu tekrar açtığında burada bulursun.")));
    } else if (["hata", "kesildi"].includes(data?.status)) {
      panel.append(element("p", "insight-error", data.last_error ? t(data.last_error) : (data.status === "kesildi" ? t("Önceki çalışma uygulama kapanınca yarıda kaldı.") : t("İşlem tamamlanamadı."))));
    }
    const ready = mode === "article" ? Boolean(data?.body) : Boolean(data?.result);
    const quotaExhausted = mode !== "article" && Number.isFinite(model.daily_remaining) && model.daily_remaining <= 0;
    if (ready) {
      if (mode === "article") {
        const details = element("details", "article-body");
        details.open = entry.articleOpen !== false;
        details.addEventListener("toggle", () => { entry.articleOpen = details.open; });
        details.append(element("summary", "", t("Kaynak metni · {count} kelime", {count: String(data.body).split(/\s+/).length})));
        if (data.title) details.append(element("h3", "", data.title));
        for (const paragraph of String(data.body).split(/\n+/).filter(Boolean)) details.append(element("p", "", paragraph));
        if (data.truncated) details.append(element("p", "insight-warning", t("Uzun makalenin okuma sınırına kadar olan bölümü saklandı. Tamamı için asıl kaynağı aç.")));
        panel.append(details);
      } else {
        const result = data.result;
        panel.append(element("p", "insight-label", t("YAPAY ZEKÂ DEĞERLENDİRMESİ")));
        if (result.title_tr) panel.append(element("h3", "insight-title", result.title_tr));
        panel.append(element("p", "insight-summary", result.summary_tr));
        if (result.benefit_tr) {
          const benefit = element("div", "benefit-box");
          benefit.append(element("h4", "benefit-title", t("Nasıl işine yarayabilir?")), element("p", "", result.benefit_tr));
          panel.append(benefit);
        }
        listSection(panel, t("Sınırlamalar ve belirsizlikler"), result.limitations);
        listSection(panel, t("Deneyebileceğin adımlar"), result.next_steps);
        const evidence = element("details", "insight-evidence");
        evidence.open = Boolean(entry.evidenceOpen[mode]);
        evidence.addEventListener("toggle", () => { entry.evidenceOpen[mode] = evidence.open; });
        evidence.append(element("summary", "", t("Dayanakları incele · {count} kaynak", {count: (data.sources || []).length})));
        for (const source of data.sources || []) {
          const section = element("section", "insight-source");
          section.append(sourceLink(source.url, source.title || source.title_tr || source.name || t("Asıl kaynak")));
          const metadata = [source.publisher || source.source_name, source.published_at ? formatDate(source.published_at) : ""].filter(Boolean).join(" · ");
          if (metadata) section.append(element("p", "insight-help", metadata));
          for (const quote of (result.evidence || []).filter(value => String(value.item_id) === String(source.item_id || source.id))) section.append(element("blockquote", "", quote.quote));
          evidence.append(section);
        }
        evidence.append(element("p", "insight-help", t("Alıntılar uygulamadaki kaynak metniyle eşleştirilmiştir. Yorum ve öneriler modelin değerlendirmesidir; bağımsız doğrulama sayılmaz.")));
        panel.append(evidence);
        if (Array.isArray(result.external_sources) && result.external_sources.length) {
          const external = element("section", "insight-source"); external.append(element("h4", "", t("Web araştırmasındaki yayınlar")));
          for (const source of result.external_sources) if (source && typeof source === "object") {
            external.append(sourceLink(source.url, source.title || t("Dış yayın")));
            if (typeof source.summary_tr === "string") external.append(element("p", "insight-help", source.summary_tr));
          }
          external.append(element("p", "insight-help", t("Web bağlantıları araştırma motorunun bildirdiği kaynaklardır; iddiaların bağımsız doğrulandığı anlamına gelmez."))); panel.append(external);
        }
      }
      panel.append(element("p", "insight-meta", (t("Kaydedildi: {date}", {date: formatDate(data.fetched_at || data.generated_at || data.updated_at, true)}) + (mode === "article" ? t(" · Bu bilgisayarda saklanıyor") : ` · ${data.model || model.model || model.provider}`))));
    }
    const actions = element("div", "insight-actions");
    if (!working(data)) {
      const startButton = button(ready ? mode === "article" ? t("Metni yeniden al") : t("Yeniden hazırla") : mode === "article" ? t("Makale metnini al") : mode === "research" ? t("Karşılaştırmalı rapor hazırla") : t("Türkçe özet hazırla"), () => start(entry, mode), "button button-primary");
      startButton.dataset.insightAction = "start";
      startButton.disabled = Boolean(entry.posting) || (!data && entry.loading.has(mode)) || (mode !== "article" && !model.configured) || quotaExhausted;
      if (entry.posting === mode) startButton.textContent = t("Başlatılıyor…");
      actions.append(startButton);
    }
    if (ready) {
      const save = element("a", "button button-secondary", t("Metni indir"));
      const itemPath = `/api/items/${encodeURIComponent(entry.item.id)}`;
      save.href = mode === "article" ? `${itemPath}/article/download` : `${itemPath}/ai/download?kind=${mode}`;
      save.download = mode === "article" ? "ufuk-makale.txt" : mode === "research" ? "ufuk-arastirma.txt" : "ufuk-ozet.txt";
      save.dataset.insightAction = "download";
      actions.append(save);
    }
    if (mode === "article") actions.append(sourceLink(data?.url || entry.item.url, t("Asıl sayfayı aç")));
    panel.append(actions);
    if (mode !== "article" && !model.configured) panel.append(element("p", "insight-warning", t("{provider} bağlantısı bu oturumda hazır görünmüyor. Bağlantı durumu ve kurulum bilgisi İlgi ve ayarlar bölümünde.", {provider: model.provider})));
    if (quotaExhausted) panel.append(element("p", "insight-help", t("Bugünkü {provider} denemesi sınırına ulaşıldı. Yeni üretim {date} tarihinde açılır. Kayıtlı raporları okumak ve indirmek sınırdan etkilenmez.", {provider: model.provider, date: formatDate(model.budget_resets_at, true)})));
    if (data?.retry_after_seconds > 0 && !working(data)) panel.append(element("p", "insight-help", t("Yeni deneme için yaklaşık {count} dakika bekle. Kayıtlı sonucu okumak kota kullanmaz.", {count: Math.ceil(data.retry_after_seconds / 60)})));
    if (focusKey && !focused?.isConnected) (panel.querySelector(`[data-insight-action="${focusKey}"]`) || entry.tabs.querySelector('[aria-selected="true"]'))?.focus({ preventScroll: true });
  }
  function mount(item) {
    const key = String(item.id);
    let entry = entries.get(key);
    if (!entry) {
      entry = { item, node: element("section", "insights"), tabs: element("div", "insight-tabs"), panel: element("div", "insight-panel"), mode: "summary", data: {}, errors: {}, loading: new Set(), requests: new Map(), epochs: {}, evidenceOpen: {}, posting: null, timer: null, needsReload: false, redraw: false };
      entries.set(key, entry);
      entry.node.setAttribute("aria-label", t("Okuma ve araştırma araçları"));
      entry.tabs.setAttribute("role", "tablist");
      entry.tabs.setAttribute("aria-label", t("İçerik türü"));
      entry.panel.id = `insight-panel-${item.id}`;
      entry.panel.setAttribute("role", "tabpanel");
      for (const [mode, label] of Object.entries(labels)) {
        const tab = button(label, () => { entry.mode = mode; draw(entry); load(entry, mode).then(() => schedule(entry)); }, "insight-tab");
        tab.dataset.mode = mode; tab.id = `insight-${item.id}-${mode}`;
        tab.setAttribute("role", "tab"); tab.setAttribute("aria-controls", entry.panel.id);
        entry.tabs.append(tab);
      }
      entry.tabs.addEventListener("keydown", event => {
        const tabs = [...entry.tabs.children]; let index = tabs.indexOf(document.activeElement);
        if (index < 0) return;
        if (event.key === "ArrowRight") index = (index + 1) % tabs.length;
        else if (event.key === "ArrowLeft") index = (index + tabs.length - 1) % tabs.length;
        else if (event.key === "Home") index = 0; else if (event.key === "End") index = tabs.length - 1; else return;
        event.preventDefault(); tabs[index].focus(); tabs[index].click();
      });
      entry.node.append(entry.tabs, entry.panel);
      draw(entry);
      Promise.all(Object.keys(labels).map(mode => load(entry, mode))).then(() => schedule(entry));
    } else {
      const revised = entry.item.revision !== item.revision;
      entry.item = item;
      if (revised || entry.needsReload) {
        entry.needsReload = false;
        draw(entry);
        Promise.all(Object.keys(labels).map(mode => reload(entry, mode))).then(() => schedule(entry));
      }
      schedule(entry);
    }
    return entry.node;
  }
  document.addEventListener("visibilitychange", () => {
    const entry = entries.get(String(state.readerItemId));
    if (entry && active(entry)) load(entry, entry.mode).then(() => schedule(entry));
  });
  window.addEventListener("pagehide", () => { disposed = true; for (const entry of entries.values()) clearTimeout(entry.timer); });
  window.addEventListener("pageshow", () => {
    disposed = false;
    const entry = entries.get(String(state.readerItemId));
    if (entry && active(entry)) Promise.all(Object.keys(labels).map(mode => load(entry, mode))).then(() => schedule(entry));
  });
  return {
    mount,
    activate(item) { const entry = entries.get(String(item.id)); if (entry) entry.needsReload = true; },
    close(itemId) { const entry = entries.get(String(itemId)); if (entry) { clearTimeout(entry.timer); entry.timer = null; } },
  };
};
