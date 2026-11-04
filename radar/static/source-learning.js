// Kaynakların katkısını, keşif denemelerini ve geri alınabilir takip kararlarını gösterir.
(() => {
  "use strict";
  const UI = globalThis.UfukUI || globalThis.window?.UfukUI;
  const t = (key, values = {}) => UI ? UI.t(key, values) : key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
  const locale = UI?.language === "en" ? "en-US" : "tr-TR";

  window.UFUKSourceLearningFactory = function (context) {
    const {element, state, topics, post, toast, formatDate, refreshAfterMutation, changeView} = context;
    const opened = new Map();
    const visible = {candidates: 30, history: 30, coverage: 10};
    let posting = false, panel = null, notice = null, reloadRequired = false;
    const count = value => Number.isFinite(value) && value >= 0 ? new Intl.NumberFormat(locale).format(value) : t("Ölçülmedi");
    const versioned = row => Number.isInteger(row?.version) && row.version >= 0;
    const rows = value => Array.isArray(value) ? value : [];
    const labels = {regular: t("Düzenli takip"), trial: t("Deneme sürecinde"), slow: t("Seyrek takip"), candidate: t("Yeni aday"), verified: t("Erişim denemesi başarılı"), probing: t("Erişimi deneniyor"), activating: t("Takibe alınıyor"), duplicate: t("Zaten kayıtlı"), retired: t("Bekleme listesinden çıkarıldı"), dismissed: t("Takibe alınmadı"), active: t("Takipte"), promoted: t("Düzenli takibe geçti"), insufficient: t("Gözlem birikiyor"), insufficient_data: t("Gözlem birikiyor"), feedback_missing: t("Geri bildirim bekleniyor"), keep: t("Takip korunuyor"), protect: t("Korunan kaynak"), slow_down: t("Seyrek takip önerisi"), promote: t("Düzenli takip önerisi"), completed: t("Tamamlandı"), partial: t("Kısmen tamamlandı"), failed: t("Tamamlanamadı"), running: t("Sürüyor"), interrupted: t("Yarıda kaldı")};
    const coverageLabels = {missing_source: t("Takip kaynağı eksik"), not_collected: t("Kayıt havuza gelmemiş"), not_examined: t("Henüz incelenmemiş"), rejected: t("Elenmiş"), available: t("Havuzda ve incelenmiş"), unknown: t("Karşılaştırma belirsiz")};
    const label = value => labels[value] || t("Değerlendirme bekleniyor");
    const date = value => value ? formatDate(value, true) : t("Henüz yok");
    const interval = value => Number.isFinite(value) && value > 0 ? t("{count} saatte bir", {count: count(value)}) : t("Henüz belirlenmedi");

    function button(text, key, action, disabled = false, primary = false) {
      const control = element("button", `button button-${primary ? "primary" : "secondary"}`, text);
      control.type = "button"; control.dataset.focusKey = `source-learning-${key}`;
      control.disabled = Boolean(disabled || posting || reloadRequired);
      control.addEventListener("click", action);
      return control;
    }

    function detail(key, title) {
      const node = element("details", "source-learning-detail");
      const summary = element("summary", "", title);
      summary.dataset.focusKey = `source-learning-detail-${key}`;
      node.append(summary); node.open = opened.get(key) || false;
      node.addEventListener("toggle", () => { opened.set(key, node.open); });
      return node;
    }

    function more(node, key, length) {
      if (length <= visible[key]) return;
      const focusKey = `more-${key}`;
      node.append(button(t("Daha fazla göster ({count} kayıt)", {count: count(length - visible[key])}), focusKey, () => {
        visible[key] += key === "coverage" ? 10 : 30; replacePanel(`source-learning-${focusKey}`);
      }));
    }

    function metric(title, value, explanation = "") {
      const node = element("div", "source-learning-metric");
      node.append(element("dt", "", title), element("dd", "", count(value)));
      if (explanation) node.append(element("small", "", explanation));
      return node;
    }

    function safeLink(value, title) {
      try {
        const url = new URL(value);
        if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return element("span", "source-learning-muted", t("Kaynak bağlantısı doğrulanamadı"));
        const link = element("a", "source-learning-link", title || url.hostname);
        link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; return link;
      } catch { return element("span", "source-learning-muted", t("Kaynak bağlantısı doğrulanamadı")); }
    }

    function replacePanel(focusKey) {
      if (!panel?.isConnected) return;
      const previous = panel;
      panel = makePanel(); previous.replaceWith(panel);
      const target = [...panel.querySelectorAll("[data-focus-key]")].find(node => node.dataset.focusKey === focusKey && !node.disabled);
      (target || panel.querySelector("h3"))?.focus();
    }

    async function refreshView(focusKey) {
      try {
        const recovering = reloadRequired;
        const refreshed = await refreshAfterMutation();
        if (refreshed === false) throw new Error(t("Güncel görünüm alınamadı."));
        if (recovering) notice = {error: false, text: t("Güncel kayıt alındı. Kararlarını yeniden verebilirsin.")};
        reloadRequired = false; return true;
      } catch {
        reloadRequired = true;
        notice = {error: true, text: t("Güncel kayıt alınamadı. Bir başka değişiklik yapmadan önce görünümü yenile.")};
        return false;
      } finally { if (!posting) replacePanel(focusKey); }
    }

    async function mutate(key, path, payload, message) {
      if (posting || reloadRequired) return;
      posting = true; notice = null; state.pending?.add("source-learning");
      for (const control of panel?.querySelectorAll("button") || []) control.disabled = true;
      let changed = false;
      try {
        await post(path, payload); changed = true;
        if (await refreshView(`source-learning-${key}`)) {
          notice = {error: false, text: message}; toast(message);
        } else toast(t("Değişiklik kaydedildi; güncel görünüm alınamadı."), true);
      } catch (error) {
        const conflict = error.status === 409;
        notice = {error: true, text: conflict ? t("Bu kayıt başka bir işlemde değişti. Güncel durumu kontrol edip kararını yeniden seç; önceki istek tekrarlanmadı.") : (error.message || t("İşlem tamamlanamadı."))};
        toast(notice.text, true);
        if (conflict) await refreshView(`source-learning-${key}`);
        if (changed) reloadRequired = true;
      } finally {
        posting = false; state.pending?.delete("source-learning"); replacePanel(`source-learning-${key}`);
      }
    }

    function period(title, report) {
      const section = element("section", "source-learning-period");
      section.append(element("h4", "", title));
      if (!report) {
        section.append(element("p", "source-learning-muted", t("Bu dönem için henüz bir değerlendirme yok."))); return section;
      }
      if (report.period_start || report.period_end) section.append(element("p", "source-learning-muted", `${date(report.period_start)} — ${date(report.period_end)}`));
      if (report.measured_since) section.append(element("p", "source-learning-muted", t("Ölçüm başlangıcı: {date}", {date: date(report.measured_since)})));
      if (report.message || report.summary) section.append(element("p", "", report.message || report.summary));
      const values = report.metrics || report;
      const stats = element("dl", "source-learning-metrics");
      for (const [name, key] of [[t("Yeni kayıt"), "added"], [t("Güncellenen kayıt"), "changed"], [t("Yararlı buldukların"), "human_positive"], [t("Yararsız buldukların"), "human_negative"], [t("Geliştirmeye alınan"), "developed"], [t("Model çağrısı"), "model_calls"], [t("Kaynak okuması"), "reads"], [t("Toplama turu"), "collection_runs"], [t("Erişim hatası"), "errors"], [t("Tekrar kayıt"), "duplicates"]]) stats.append(metric(name, values[key]));
      section.append(stats, element("p", "source-learning-muted", t("Çağrı ve okuma sayıları işlem miktarıdır. Parasal maliyet bu ekranda ölçülmüyor.")));
      for (const text of rows(report.notes)) if (typeof text === "string") section.append(element("p", "source-learning-note", text));
      return section;
    }

    function renderCoverage(section, data) {
      const coverage = data.coverage || {}, node = detail("coverage", t("Neleri kaçırıyoruz? Bağımsız örnek denetimi"));
      node.append(element("p", "source-learning-muted", t("Farklı aramalarla bulunan örnekler, havuz ve araştırma sırasıyla karşılaştırılır. İlgi uygunluğu ayrıca değerlendirilir; eksik her örnek kaçırılmış önemli bir gelişme değildir. Sonuç bütün interneti kapsadığımızı göstermez.")));
      const stats = element("dl", "source-learning-metrics");
      stats.append(metric(t("Karşılaştırılan örnek"), coverage.sample_count));
      const measured = ["completed", "partial"].includes(coverage.status) && Number.isInteger(coverage.sample_count);
      for (const [key, title] of Object.entries(coverageLabels)) stats.append(metric(title, coverage.counts?.[key] ?? (measured ? 0 : undefined)));
      node.append(stats);
      if (coverage.note) node.append(element("p", "", coverage.note));
      if (coverage.observed_at) node.append(element("p", "source-learning-muted", t("Son karşılaştırma: {date}", {date: date(coverage.observed_at)})));
      if (Array.isArray(coverage.topics_checked)) node.append(element("p", "source-learning-muted", t("Kontrol edilen alanlar: {topics}.", {topics: coverage.topics_checked.map(topic => topics[topic] || topic).join(", ") || t("Henüz yok")})));
      const samples = rows(coverage.samples);
      for (const example of samples.slice(0, visible.coverage)) {
        const line = element("article", "source-learning-row");
        line.append(element("span", "source-learning-pill", coverageLabels[example.stage] || t("Karşılaştırma belirsiz")), element("h4", "", example.title || t("Denetim örneği")), element("p", "", example.reason || t("Ayrıntı bekleniyor")));
        if (example.relevance === "unconfirmed") line.append(element("p", "source-learning-muted", t("İlgimize uygunluğu henüz doğrulanmadı.")));
        if (example.query) line.append(element("p", "source-learning-muted", t("Bağımsız sorgu: {query}", {query: example.query})));
        if (example.url || example.landing_url) line.append(safeLink(example.url || example.landing_url, t("Örneğin kaynağı"))); node.append(line);
      }
      more(node, "coverage", samples.length);
      for (const failure of rows(coverage.errors)) node.append(element("p", "source-learning-notice is-error", typeof failure === "string" ? failure : failure.message || t("Bir denetim kaynağına erişilemedi.")));
      section.append(node);
    }

    function renderCandidates(section, scout) {
      const candidates = rows(scout.candidates), candidatesDetail = detail("candidates", t("Yeni kaynak adayları ve denemeler · {count}", {count: candidates.length}));
      candidatesDetail.append(element("p", "source-learning-muted", t("Yeni adaylar küçük bir deneme bütçesiyle gözlenir. Tek bir model puanı veya henüz okunmamış kayıt, yarar kararı değildir.")));
      if (!candidates.length) candidatesDetail.append(element("p", "", t("Henüz kaynak adayı yok. Keşif ve denetim tamamlandığında bulunan adaylar burada görünür.")));
      for (const row of candidates.slice(0, visible.candidates)) {
        const card = element("article", "source-learning-row"), header = element("div", "source-learning-row-heading");
        header.append(element("h4", "", row.name || row.source_id || t("Yeni kaynak")), element("span", "source-learning-pill", label(row.status))); card.append(header);
        if (row.topic) card.append(element("p", "source-learning-muted", topics[row.topic] || row.topic));
        card.append(element("p", "", row.reason || t("Adayın yararını değerlendirmek için gözlem gerekiyor.")));
        if (row.url) card.append(safeLink(row.url, t("Kaynağı aç")));
        if (typeof row.discovered_from === "string" && row.discovered_from) { const provenance = element("p", "source-learning-muted"); provenance.append(safeLink(row.discovered_from, t("Adayın bulunduğu kaynak"))); card.append(provenance); }
        if (row.probe?.validated) card.append(element("p", "source-learning-note", t("Erişim denemesinde {count} kayıt okundu. Bu, kaynağın yararlı bulunduğu anlamına gelmez; katkısı deneme süresinde ölçülür.", {count: count(row.probe.sample_count)})));
        if (["candidate", "verified", "failed"].includes(row.status)) {
          const actions = element("div", "source-learning-actions");
          for (const [decision, text, message] of [["trial", t("Denemeye al"), t("Kaynak sınırlı denemeye alındı.")], ["dismiss", t("Şimdilik takip etme"), t("Aday takibe alınmadı; karar geçmişte korunur.")]]) {
            if (decision === "trial" && row.status !== "verified") continue;
            const key = `candidate-${row.id}-${decision}`;
            actions.append(button(text, key, () => mutate(key, `/api/source-learning/candidates/${encodeURIComponent(row.id)}/decision`, {decision, expected_version: row.version}, message), !versioned(row), decision === "trial"));
          }
          card.append(actions);
        }
        candidatesDetail.append(card);
      }
      more(candidatesDetail, "candidates", candidates.length);
      section.append(candidatesDetail);
    }

    function renderSources(section, sourceRows) {
      const outer = detail("sources", t("Kaynak katkısını incele · {count}", {count: sourceRows.length}));
      outer.append(element("p", "source-learning-muted", t("Okunmayan, görülmeyen ve bütçe yüzünden bekleyen kayıtlar olumsuz tercih sayılmaz. Düşük yayın sıklığı tek başına geri plana alma nedeni değildir.")));
      const filtered = sourceRows.filter(row => (!state.topic || state.topic === "all" || row.topic === state.topic) && (!state.search || `${row.name} ${row.source_id}`.toLocaleLowerCase(locale).includes(state.search.toLocaleLowerCase(locale))));
      if (!filtered.length) outer.append(element("p", "", t("Seçili konu ve arama için kaynak değerlendirmesi yok.")));
      for (const row of filtered) {
        const card = detail(`source-${row.source_id}`, row.name || row.source_id), values = row.metrics || {};
        const badges = element("div", "source-learning-actions");
        badges.append(element("span", "source-learning-pill", label(row.lifecycle)));
        if (row.enabled === false) badges.append(element("span", "source-learning-pill", t("Takibi kapalı")));
        if (row.trial_review_due) badges.append(element("span", "source-learning-pill is-protected", t("Deneme kararı bekleniyor")));
        if (row.protected) badges.append(element("span", "source-learning-pill is-protected", t("Sıklığı korunuyor")));
        card.append(badges, element("p", "", t("Uygulanan takip: {actual} · Senin ayarın: {configured}", {actual: interval(row.effective_interval_hours), configured: interval(row.configured_interval_hours)})));
        card.append(element("p", "source-learning-note", row.evidence_label || row.recommendation?.reason || t("Henüz yarar kararı vermek için yeterli kanıt yok.")));
        if (row.evidence_label && row.recommendation?.reason) card.append(element("p", "source-learning-muted", row.recommendation.reason));
        if (values.observed_since) card.append(element("p", "source-learning-muted", t("Ölçüm başlangıcı: {date}. Otomatik kararlar bu tarihten sonra biriken gözlemlere dayanır.", {date: date(values.observed_since)})));
        const stats = element("dl", "source-learning-metrics");
        for (const [name, key] of [[t("Gözlem günü"), "observation_days"], [t("Toplanan kayıt"), "collected"], [t("İncelenen kayıt"), "examined"], [t("Yararlı buldukların"), "human_positive"], [t("Yararsız buldukların"), "human_negative"], [t("Geliştirmeye alınan"), "developed"], [t("Modelin uygun bulduğu"), "model_high_fit"], [t("Senin okudukların"), "human_read"], [t("Model çağrısı"), "model_calls"], [t("Kaynak okuması"), "reads"], [t("Tekrar kayıt"), "duplicates"], [t("Toplama turu"), "collection_runs"], [t("Erişim hatası"), "errors"]]) stats.append(metric(name, values[key]));
        card.append(stats);
        card.append(element("p", "source-learning-muted", t("Modelin ilgi değerlendirmesi, senin yararlı bulmanla ayrı sayılır.")));
        const key = `protect-${row.source_id}`;
        const protect = button(row.protected ? t("Sıklık korumasını kaldır") : t("Bu kaynağın sıklığını koru"), key, () => mutate(key, `/api/source-learning/sources/${encodeURIComponent(row.source_id)}/protect`, {protected: !row.protected, expected_version: row.version}, row.protected ? t("Sıklık koruması kaldırıldı; kararlar yeniden kanıta göre değerlendirilecek.") : t("Bu kaynağın takip sıklığı otomatik azaltılmayacak.")), !versioned(row));
        protect.setAttribute("aria-pressed", String(Boolean(row.protected))); card.append(protect);
        outer.append(card);
      }
      section.append(outer);
    }

    function renderChanges(section, changes, sourceRows) {
      const history = detail("history", t("Takip kararları ve geri alma · {count}", {count: changes.length}));
      if (!changes.length) history.append(element("p", "", t("Henüz otomatik takip değişikliği yapılmadı. Gözlem birikmeden kaynaklar geri plana atılmaz.")));
      for (const row of changes.slice(0, visible.history)) {
        const card = element("article", "source-learning-row");
        const name = sourceRows.find(source => source.source_id === row.source_id)?.name || row.source_id;
        card.append(element("h4", "", name || t("Kaynak kararı")), element("p", "", `${interval(row.before_interval)} → ${interval(row.after_interval)}`), element("p", "", row.reason || t("Gerekçe bulunamadı")), element("p", "source-learning-muted", `${date(row.created_at)}${row.reverted ? t(" · Geri alındı") : ""}`));
        if (!row.reverted && row.can_revert !== false) {
          const key = `undo-${row.id}`;
          card.append(button(t("Bu değişikliği geri al"), key, () => mutate(key, `/api/source-learning/changes/${encodeURIComponent(row.id)}/undo`, {expected_version: row.version}, t("Takip değişikliği geri alındı.")), !versioned(row)));
        } else if (!row.reverted) card.append(element("p", "source-learning-muted", t("Daha yeni bir tercih bulunduğu için bu karar doğrudan geri alınamaz.")));
        history.append(card);
      }
      more(history, "history", changes.length);
      section.append(history);
    }

    function makePanel() {
      const data = state.data?.source_learning || {}, scout = state.data?.source_scout || {}, settings = data.settings || {};
      const sourceRows = rows(data.sources), section = element("section", "source-learning");
      section.setAttribute("aria-labelledby", "source-learning-title");
      const header = element("div", "source-learning-heading"), copy = element("div");
      copy.append(element("span", "source-learning-eyebrow", t("KAYNAK KÜTÜPHANESİ")));
      const title = element("h3", "", t("Takip ettiklerimiz ne kadar işe yarıyor?")); title.id = "source-learning-title"; title.tabIndex = -1;
      copy.append(title, element("p", "", t("Yeni kaynakları dene, katkılarını izle ve takip kararlarını denetle."))); header.append(copy);
      const start = button(scout.running ? t("Keşif ve denetim sürüyor…") : t("Keşif ve denetim yap"), "scout", () => mutate("scout", "/api/source-learning/scout/start", {}, t("Yeni kaynak keşfi ve bağımsız örnek denetimi başladı.")), Boolean(scout.running), true);
      header.append(start); section.append(header);
      if (notice) {
        const message = element("p", `source-learning-notice${notice.error ? " is-error" : ""}`, notice.text);
        message.setAttribute("role", notice.error ? "alert" : "status"); section.append(message);
      }
      if (reloadRequired) {
        const refresh = button(t("Güncel görünümü al"), "refresh", () => refreshView("source-learning-refresh")); refresh.disabled = posting; section.append(refresh);
      }
      const status = element("div", "source-learning-status");
      status.append(element("span", "source-learning-pill", settings.enabled === true ? t("Otomatik öğrenme açık") : settings.enabled === false ? t("Otomatik öğrenme duraklatıldı") : t("Öğrenme durumu alınamadı")));
      status.append(element("span", "", t("Yeni kaynaklara ayrılan pay: {share}", {share: Number.isFinite(settings.exploration_percent) ? new Intl.NumberFormat(locale, {style: "percent"}).format(settings.exploration_percent / 100) : t("Ölçülmedi")})));
      if (scout.next_due_at) status.append(element("span", "", t("Sonraki keşif: {date}", {date: date(scout.next_due_at)}))); section.append(status);
      if (scout.schedule_error) section.append(element("p", "source-learning-notice is-error", t("Zamanlı keşif sorunu: {error}", {error: scout.schedule_error})));
      const summary = element("dl", "source-learning-overview");
      summary.append(metric(t("Takibi ölçülen"), Array.isArray(data.sources) ? sourceRows.length : undefined), metric(t("Deneme kaynağı"), Array.isArray(data.sources) ? sourceRows.filter(row => row.lifecycle === "trial").length : undefined), metric(t("Yeni aday"), Array.isArray(scout.candidates) ? scout.candidates.filter(row => ["candidate", "verified", "probing"].includes(row.status)).length : undefined), metric(t("Geri alınabilir karar"), Array.isArray(data.changes) ? data.changes.filter(row => !row.reverted && row.can_revert !== false).length : undefined)); section.append(summary);
      if (data.summary?.evidence_label || data.summary?.message) section.append(element("p", "source-learning-note", data.summary.evidence_label || data.summary.message));
      if (data.summary?.trial_waiting_review > 0) section.append(element("p", "source-learning-notice", t("{count} kaynak denemesi 30 günü aştı ve karar bekliyor. Kanıt eksikliği olumsuz oy sayılmadı. Yararlı bulduklarını işaretleyebilir; yeni denemeye yer açmak için kaynak kartındaki Takip ayarlarından takibi duraklatabilirsin.", {count: count(data.summary.trial_waiting_review)})));
      const reports = detail("reports", t("Günlük ve haftalık gelişim"));
      const periods = element("div", "source-learning-periods"); periods.append(period(t("Bugün"), data.daily), period(t("Son 7 gün"), data.weekly)); reports.append(periods);
      if (scout.last_run) {
        const run = scout.last_run; reports.append(element("p", "source-learning-note", t("Son keşif: {status} · {date}", {status: label(run.status), date: date(run.completed_at || run.finished_at || run.started_at)})));
        if (run.message) reports.append(element("p", "", run.message));
        for (const failure of rows(run.errors)) reports.append(element("p", "source-learning-notice is-error", typeof failure === "string" ? failure : failure.message || t("Keşif sırasında erişim sorunu oluştu.")));
      }
      if (scout.budget) {
        const budget = scout.budget, limits = element("section", "source-learning-period");
        limits.append(element("h4", "", t("Bu haftanın kaynak keşfi bütçesi")), element("p", "", t("Ağ isteği: {requests} / {requestLimit} · Erişim denemesi: {probes} / {probeLimit} · Denemeye alınan kaynak: {trials} / {trialLimit}", {requests: count(budget.requests), requestLimit: count(budget.request_limit), probes: count(budget.probes), probeLimit: count(budget.probe_limit), trials: count(budget.trials), trialLimit: count(budget.trial_limit)})), element("p", "source-learning-muted", t("Aynı anda en fazla {count} deneme kaynağı. Yenilenme: {date}.", {count: count(budget.active_trial_limit), date: date(budget.resets_at)})), element("p", "source-learning-muted", t("Bunlar kaynak keşfinin ağ istekleridir. Model çağrıları ve parasal maliyet ile aynı ölçü değildir.")));
        if (budget.note) limits.append(element("p", "source-learning-muted", budget.note)); reports.append(limits);
      }
      section.append(reports); renderCoverage(section, scout); renderCandidates(section, scout); renderSources(section, sourceRows); renderChanges(section, rows(data.changes), sourceRows);
      const controls = detail("settings", t("Takip kuralları ve senin kontrolün"));
      controls.append(element("p", "", t("Bir kaynak en az 30 gün, 20 inceleme ve 5 açık kullanıcı tepkisi birikmeden seyrekleştirilmez. Koruduğun kaynakların sıklığı azaltılmaz; kararların gerekçesini inceleyip geri alabilirsin.")));
      const key = "enabled";
      controls.append(button(settings.enabled ? t("Otomatik öğrenmeyi duraklat") : t("Otomatik öğrenmeyi aç"), key, () => mutate(key, "/api/source-learning/settings", {enabled: !settings.enabled, expected_version: settings.version}, settings.enabled ? t("Otomatik kaynak öğrenmesi duraklatıldı.") : t("Otomatik kaynak öğrenmesi açıldı.")), !versioned(settings) || typeof settings.enabled !== "boolean"));
      if (typeof changeView === "function") controls.append(button(t("İlgi alanlarını düzenle"), "interests", () => changeView("settings")));
      section.append(controls); return section;
    }

    return {render(container) { panel = makePanel(); container.append(panel); }, busy: () => posting};
  };
})();
