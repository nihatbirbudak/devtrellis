// Gelişmeleri araştırma, karar ve uygulanabilir devir adımlarında güvenli biçimde gösterir.
(() => {
  "use strict";
  const UI = globalThis.UfukUI || globalThis.window?.UfukUI;
  const t = (key, values = {}) => UI ? UI.t(key, values) : key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
  const locale = UI?.language === "en" ? "en-US" : "tr-TR";

  window.UFUKWorkflowFactory = function UFUKWorkflowFactory(context) {
    const {element, post, request, state, formatDate, sourceLink, toast, refreshAfterMutation, render, changeView, findItem, topics} = context;
    const labels = {queued: t("Sırada"), reading: t("Kaynaklar okunuyor"), analyzing: t("Araştırılıyor"), ready: t("Rapor hazır"), blocked: t("Devam etmek için bekliyor"), failed: t("Yeniden deneme gerekiyor"), cancelled: t("Durduruldu")};
    const activeStatuses = new Set(["queued", "reading", "analyzing"]);
    const decisions = {develop: t("Geliştirmeye alındı"), watch: t("İzleniyor"), dismiss: t("Pas geçildi")};
    let session = null;
    let operations = 0;
    let timer = null;
    let inFlight = null;
    let epoch = 0;
    let sequence = 0;
    let disposed = false;
    const seenPending = new Set();
    const data = () => state.data?.workflow || {};
    const manualMode = () => data().status?.automatic === false;
    const scheduled = () => data().status?.automatic === true && data().status?.enabled === true && data().settings?.enabled !== false;
    const manualMessage = () => t("Otomatik günlük takip bu oturumda kapalı. Günü hazırla veya Derin araştır ile başlatabilirsin.");
    const researchProvider = () => ({codex: "Codex", nvidia: "NVIDIA", claude: "Claude"}[state.data?.ai?.deep_provider] || "Claude");
    const statusText = message => {
      const daily = /^Bugün (\d+) gelişme konu çeşitliliği ve bekleme süresiyle ön incelemeye alındı\.$/.exec(message || "");
      return daily ? t("Bugün {count} gelişme konu çeşitliliği ve bekleme süresiyle ön incelemeye alındı.", {count: daily[1]}) : t(message || "");
    };
    const tasks = () => Array.isArray(data().tasks) ? data().tasks : [];
    const id = value => encodeURIComponent(String(value));
    const taskById = value => tasks().find(task => String(task.id) === String(value));
    const busy = () => Boolean(session?.dialog.open || operations);
    const visible = () => !disposed && document.visibilityState !== "hidden";
    const ready = task => Boolean(task?.status === "ready" && !task.stale && task.result);
    const evidenceVerified = task => task?.result?.evidence_quality?.status === "verified" && task.result.evidence_quality.coverage_complete === true;
    const current = (task, item) => ready(task) && Number(task.source_revision) === Number(item.revision);
    const title = task => task?.result?.title_tr || task?.title_tr || t("İncelenen gelişme");
    const engines = {nvidia: "NVIDIA", claude: "Claude", codex: "Codex"};
    const reviewDecisions = {research: t("Derin araştırmaya uygun"), watch: t("Şimdilik izle"), dismiss: t("Derin araştırmaya gerek görülmedi")};
    function stageTitle(task) {
      const engine = engines[task.engine] || (String(task.model || "").startsWith("nvidia/") ? "NVIDIA" : "");
      const stage = {interest: t("ilgi elemesi"), screen: t("ön araştırması"), review: t("uygunluk değerlendirmesi"), deep: t("derin araştırması")}[task.kind];
      return stage ? `${engine || (["interest", "screen"].includes(task.kind) ? "NVIDIA" : "")} ${stage}`.trim() : t("Araştırma");
    }

    function button(text, action, primary = false, key = "") {
      const node = element("button", `button button-${primary ? "primary" : "secondary"}`);
      node.type = "button"; node.textContent = text;
      if (key) node.dataset.focusKey = key;
      node.addEventListener("click", action);
      return node;
    }

    function taskForItem(item, kind = "deep") {
      return tasks().filter(task => !task.request_id && String(task.item_id) === String(item.id) && (!kind || task.kind === kind))
        .sort((a, b) => Number(current(b, item)) - Number(current(a, item)) || String(b.updated_at || b.created_at).localeCompare(String(a.updated_at || a.created_at)))[0];
    }

    function summaryFor(item) {
      return tasks().filter(task => !task.request_id && ["screen", "deep"].includes(task.kind) && String(task.item_id) === String(item.id) && current(task, item) && task.result?.title_tr && task.result?.summary_tr)
        .sort((a, b) => Number(b.kind === "deep") - Number(a.kind === "deep") || String(b.updated_at).localeCompare(String(a.updated_at)))[0]?.result || null;
    }

    function researchVisible(task) {
      if (activeStatuses.has(task.status) || ["blocked", "failed"].includes(task.status)) return true;
      if (task.kind === "review" && task.status === "ready" && ["watch", "dismiss"].includes(task.result?.research_decision)) return false;
      if (task.stale && tasks().some(replacement => {
        if (replacement.id === task.id || String(replacement.item_id) !== String(task.item_id) || String(replacement.request_id || "") !== String(task.request_id || "") || !ready(replacement)) return false;
        const oldRevision = Number(task.source_revision);
        const newRevision = Number(replacement.source_revision);
        const stages = {interest: 0, screen: 1, review: 2, deep: 3};
        return (replacement.kind === task.kind && newRevision > oldRevision)
          || (stages[replacement.kind] > stages[task.kind] && newRevision >= oldRevision);
      })) return false;
      return task.status === "cancelled" || Boolean(task.stale);
    }

    function counts() {
      return {
        research: tasks().filter(researchVisible).length,
        decisions: tasks().filter(task => ready(task) && task.kind === "deep" && !task.decision).length,
        development: tasks().filter(task => task.kind === "deep" && task.decision === "develop").length,
        notifications: tasks().filter(task => task.kind === "deep" && ["ready", "failed"].includes(task.status) && !task.notification_read).length,
      };
    }

    function statusStrip() {
      const wrapper = element("section", "workflow-strip");
      wrapper.setAttribute("aria-label", t("Günlük inceleme durumu"));
      const status = data().status || {};
      const pending = tasks().filter(task => activeStatuses.has(task.status));
      const idleMessage = !status.running && (!status.message || status.message === "Günlük takip hazır.");
      const message = idleMessage && manualMode() ? manualMessage() : idleMessage && data().settings?.enabled === false ? t("Günlük hazırlık duraklatıldı. İstediğinde Günü hazırla ile başlatabilirsin.") : statusText(status.message) || (pending.length ? t("{count} gelişmenin kaynakları inceleniyor. Hazır Türkçe raporlar burada yerini alacak.", {count: pending.length}) : t("Hazır gelişmelere göz at; ilgini çekeni derin araştırmaya gönder."));
      const copy = element("div");
      copy.append(element("strong", "workflow-label", status.running ? t("Günün incelemesi sürüyor") : manualMode() ? t("Manuel çalışma") : data().settings?.enabled === false ? t("Günlük inceleme duraklatıldı") : t("Gözat → Araştır → Karar ver")), element("p", "", message));
      if (status.budget_waiting) copy.append(element("p", "workflow-notice", t("Günlük kullanım sınırı nedeniyle bekleyen işler var. Kayıtlı raporları okuyabilir, kararlarını verebilirsin.")));
      if (scheduled() && status.next_run_at) copy.append(element("small", "", t("Sonraki günlük kontrol: {date}", {date: formatDate(status.next_run_at, true)})));
      wrapper.append(copy);
      const count = counts().decisions;
      if (count) wrapper.append(button(t("{count} rapor kararını bekliyor", {count: count}), () => changeView("decisions"), false, "workflow-decisions"));
      return wrapper;
    }

    function updateTask(result) {
      const task = result?.task || result;
      if (!task?.id || !task.item_id) return;
      state.data.workflow ||= {tasks: []};
      const rows = tasks().slice();
      const index = rows.findIndex(row => String(row.id) === String(task.id));
      if (index < 0) rows.push(task); else rows[index] = task;
      state.data.workflow.tasks = rows;
    }

    function showSessionError(message) {
      if (!session) { toast(message, true); return; }
      session.error.textContent = message;
      session.error.hidden = false;
      session.error.focus({preventScroll: true});
    }

    async function mutation(path, payload, success, apply = updateTask) {
      if (operations) return null;
      const activeSession = session;
      const opener = document.activeElement;
      const focusKey = opener?.dataset.focusKey;
      const view = state.view;
      operations += 1; epoch += 1;
      state.pending.add("workflow");
      if (activeSession) {
        activeSession.posting = true;
        activeSession.error.hidden = true;
        for (const node of activeSession.dialog.querySelectorAll("button,input,select")) node.disabled = true;
      } else if (opener?.tagName === "BUTTON") opener.disabled = true;
      try {
        const result = await post(path, payload);
        apply(result);
        if (inFlight) await inFlight;
        const refreshed = await refreshAfterMutation();
        toast(`${success}${refreshed === false ? t(" İşlem kaydedildi; görünüm için yeniden bağlantı bekleniyor.") : ""}`);
        return result;
      } catch (error) {
        showSessionError(error.status === 409 ? t("{error} Güncel durumu yeniden alıp kararını tekrar seçebilirsin.", {error: error.message}) : error.message);
        return null;
      } finally {
        operations -= 1; state.pending.delete("workflow");
        if (activeSession && session === activeSession) {
          activeSession.posting = false;
          activeSession.close.disabled = false;
          if (activeSession.kind === "report") drawReport(true);
          else for (const node of activeSession.dialog.querySelectorAll("button,input,select")) node.disabled = false;
        }
        render();
        if (document.activeElement === document.body || document.activeElement === opener || (activeSession && activeSession.dialog.contains(opener) && !opener.isConnected)) {
          const scope = session?.dialog || (state.view === view ? document.getElementById("content") : null);
          const replacement = focusKey && [...(scope?.querySelectorAll("[data-focus-key]") || [])].find(node => node.dataset.focusKey === focusKey && !node.disabled);
          (replacement || (opener?.isConnected && !opener.disabled ? opener : null) || session?.close || document.getElementById("view-title"))?.focus({preventScroll: true});
        }
        schedule();
      }
    }

    async function run() {
      if (!state.data?.workflow) { toast(t("Günlük inceleme henüz hazır değil. Sayfayı yenileyip tekrar dene."), true); return; }
      return mutation("/api/workflow/run", {}, t("Günlük inceleme isteği alındı. İlerlemeyi Araştırılıyor bölümünden izleyebilirsin."), () => {});
    }

    function researchButton(item, prefix = "card") {
      const task = taskForItem(item);
      const node = button(task ? t("Araştırmayı aç") : t("Derin araştır"), async () => {
        if (task) {
          if (session?.kind === "report") { session.taskId = String(task.id); epoch += 1; drawReport(true); schedule(); }
          else open(task.id, node);
          return;
        }
        const result = await mutation(`/api/workflow/items/${id(item.id)}/research`, {}, t("Derin araştırma sıraya alındı. Raporu UFUK hazırlayacak."));
        if (result) {
          const created = taskForItem(item);
          if (created && session?.kind === "report") { session.taskId = String(created.id); epoch += 1; drawReport(true); schedule(); }
          else if (created) open(created.id, document.querySelector(`[data-focus-key="${prefix}-research-${item.id}"]`) || node);
          else changeView("research");
        }
      }, true, `${prefix}-research-${item.id}`);
      node.classList.add("research-button");
      node.setAttribute("aria-haspopup", "dialog");
      node.disabled = Boolean(operations || state.pending.has(`item-${item.id}`) || !state.data?.workflow);
      return node;
    }

    function matchesTask(task) {
      const item = findItem(task.item_id);
      if (state.topic !== "all" && (task.profile_topic || item?.topic) !== state.topic) return false;
      const text = [title(task), task.result?.summary_tr, task.message, item?.title, item?.source_name].filter(Boolean).join(" ").toLocaleLowerCase(locale);
      return !state.search || text.includes(state.search.toLocaleLowerCase(locale));
    }

    function taskCard(task) {
      const card = element("article", "workflow-card");
      const meta = element("div", "workflow-card-meta");
      meta.append(element("span", "workflow-label", stageTitle(task)), element("span", `workflow-stage status-${task.status}`, task.stale ? t("Kaynak değişti") : labels[task.status] || t("Durum bekleniyor")));
      const heading = element("h3");
      const openButton = button(title(task), () => open(task.id, openButton), false, `workflow-open-${task.id}`);
      openButton.className = "headline-link";
      openButton.setAttribute("aria-haspopup", "dialog");
      heading.append(openButton);
      card.append(meta, heading);
      if (!task.result && task.source_title) card.append(element("p", "workspace-help", t("Asıl yayın: {title}", {title: task.source_title})));
      if (task.result?.summary_tr) card.append(element("p", "", task.result.summary_tr));
      else card.append(element("p", "", statusText(task.message) || t("Kaynaklar inceleniyor; sonucu Türkçe rapor olarak burada göreceksin.")));
      if (task.result?.relevance_reason_tr) card.append(element("p", "workflow-recommendation", task.result.relevance_reason_tr));
      if (task.result || task.report_text) {
        card.append(element("p", "workspace-help", t("Kaydedilen model: {model}", {model: task.model || t("Model kaydedilmemiş")})));
        card.append(element("p", "workspace-help", task.report_generated_at ? t("Rapor üretildi: {date}", {date: formatDate(task.report_generated_at, true)}) : t("Üretim tarihi kaydedilmemiş")));
        card.append(element("p", "workspace-help", t("Kaynak sayısı: {count}", {count: task.sources?.length || 0})));
      }
      if (task.result?.research_decision) card.append(element("p", "workflow-label", reviewDecisions[task.result.research_decision] || t("Uygunluk kararı kaydedildi")));
      if (task.decision) card.append(element("p", "workflow-label", decisions[task.decision] || t("Karar kaydedildi")));
      const actions = element("div", "workflow-actions");
      const action = button(task.status === "ready" ? task.kind === "deep" ? t("Raporu ve kararı aç") : t("Değerlendirmeyi aç") : t("İlerlemeyi aç"), () => open(task.id, action), false, `workflow-report-${task.id}`);
      actions.append(action);
      if (task.kind === "screen" && ready(task)) actions.append(researchButton(findItem(task.item_id) || {id: task.item_id, revision: task.source_revision}, `task-${task.id}`));
      if (task.decision === "develop" && ready(task) && task.kind === "deep" && !task.coverage?.limited && evidenceVerified(task)) actions.append(downloadLink(task, "handoff", t("Devir planını indir")));
      card.append(actions, element("small", "", `${topics[task.profile_topic] || t("Gelişme")} · ${formatDate(task.updated_at || task.created_at, true)}`));
      return card;
    }

    function renderView(view, content) {
      content.append(statusStrip());
      const rows = tasks().filter(matchesTask).sort((a, b) => String(b.updated_at || b.created_at).localeCompare(String(a.updated_at || a.created_at)));
      const selected = rows.filter(task => view === "development" ? task.decision === "develop" && task.kind === "deep"
        : view === "decisions" ? ready(task) && task.kind === "deep" && !task.decision
          : researchVisible(task));
      if (selected.length) {
        const list = element("div", "workflow-grid");
        for (const task of selected) list.append(taskCard(task));
        content.append(list);
      } else {
        const empty = element("div", "workspace-empty");
        const titles = {research: t("Süren bir araştırma yok"), decisions: t("Şu anda karar bekleyen rapor yok"), development: t("Geliştirmeye ayırdığın bir fikir yok")};
        const descriptions = {research: t("Bir gelişmede Derin araştır seç. Kaynakları UFUK inceleyecek; senden not yazman beklenmiyor."), decisions: t("Derin araştırma tamamlandığında rapor burada görünür. Geliştirmeye al, izlemeye al veya pas geç."), development: t("Hazır raporda Geliştirmeye al seçtiğinde deneme planını buradan indirebilirsin. Kod veya canlı sistem kendiliğinden değiştirilmez.")};
        empty.append(element("h3", "", titles[view]), element("p", "", state.search || state.topic !== "all" ? t("Bu filtreye uyan görev bulunamadı. Aramayı veya konu filtresini değiştirebilirsin.") : descriptions[view]), button(t("Önemli gelişmelere dön"), () => changeView("selection")));
        content.append(empty);
      }
      if (view === "decisions") {
        const history = rows.filter(task => ["watch", "dismiss"].includes(task.decision));
        if (history.length) {
          const details = element("details", "workflow-history");
          details.append(element("summary", "", t("Önceki kararların · {count}", {count: history.length})));
          const list = element("div", "workflow-grid");
          history.forEach(task => list.append(taskCard(task))); details.append(list); content.append(details);
        }
      }
      if (view === "research") {
        const completed = rows.filter(task => task.status === "ready" && ["interest", "review"].includes(task.kind) && !researchVisible(task));
        if (completed.length) {
          const details = element("details", "workflow-history");
          details.append(element("summary", "", t("Tamamlanan eleme ve uygunluk değerlendirmeleri · {count}", {count: completed.length})));
          const list = element("div", "workflow-grid");
          completed.forEach(task => list.append(taskCard(task))); details.append(list); content.append(details);
        }
      }
    }

    function downloadLink(task, action, label) {
      const link = element("a", "button button-secondary", label);
      link.href = `/api/workflow/tasks/${id(task.id)}/${action}`;
      link.download = `ufuk-${action === "handoff" ? "devir" : "rapor"}-${task.id}.txt`;
      return link;
    }

    function makeDialog(kind, opener) {
      if (session) return null;
      const dialog = element("dialog", "workflow-dialog workspace-dialog");
      const titleId = `workflow-title-${++sequence}`;
      dialog.setAttribute("aria-labelledby", titleId);
      const toolbar = element("div", "reader-toolbar");
      const heading = element("h2", "", kind === "settings" ? t("Günlük inceleme ayarları") : t("Araştırma raporu"));
      heading.id = titleId;
      const close = button(t("Kapat"), () => requestClose(), false, "workflow-close");
      close.setAttribute("aria-label", t("Araştırma penceresini kapat"));
      toolbar.append(heading, close);
      const error = element("p", "inline-error"); error.hidden = true; error.tabIndex = -1; error.setAttribute("role", "alert");
      const body = element("div", "workflow-report");
      dialog.append(toolbar, error, body);
      document.body.append(dialog);
      session = {kind, dialog, heading, close, error, body, opener: opener || document.activeElement, openerKey: opener?.dataset.focusKey, posting: false, signature: "", dirty: () => false};
      dialog.addEventListener("cancel", event => { event.preventDefault(); requestClose(); });
      dialog.addEventListener("close", () => {
        const old = session;
        if (!old || old.dialog !== dialog) return;
        epoch += 1; session = null; clearTimeout(timer); timer = null;
        const toastNode = document.getElementById("toast"); if (toastNode && dialog.contains(toastNode)) document.body.append(toastNode);
        dialog.remove();
        const replacement = old.openerKey && [...document.querySelectorAll("[data-focus-key]")].find(node => node.dataset.focusKey === old.openerKey && !node.disabled);
        (old.opener?.isConnected && !old.opener.disabled ? old.opener : replacement || document.getElementById("view-title"))?.focus({preventScroll: true});
      });
      dialog.showModal(); close.focus({preventScroll: true});
      return session;
    }

    function requestClose() {
      if (!session || session.posting) return;
      if (session.dirty()) {
        if (session.discard) { session.discard.focus({preventScroll: true}); return; }
        const warning = element("div", "workflow-notice");
        warning.append(element("p", "", t("Ayar değişikliklerin henüz kaydedilmedi.")));
        const discard = button(t("Kaydetmeden çık"), () => session?.dialog.close(), false, "workflow-discard");
        warning.append(discard); session.body.append(warning); session.discard = discard; discard.focus({preventScroll: true});
      } else session.dialog.close();
    }

    function section(body, heading, value) {
      if (value == null || value === "" || (Array.isArray(value) && !value.length)) return;
      const block = element("section", "workflow-section");
      block.append(element("h3", "", heading));
      if (Array.isArray(value)) {
        const list = element("ul");
        for (const line of value) if (typeof line === "string") list.append(element("li", "", line));
        if (!list.children.length) return;
        block.append(list);
      } else if (["string", "number"].includes(typeof value)) block.append(element("p", "", String(value)));
      else return;
      body.append(block);
    }

    function drawReport(force = false) {
      if (!session || session.kind !== "report" || session.posting) return;
      const task = taskById(session.taskId);
      const signature = JSON.stringify(task);
      if (!force && session.signature === signature) return;
      session.signature = signature;
      const focused = document.activeElement;
      const key = focused?.dataset.focusKey;
      const hadFocus = session.body.contains(focused);
      const openDetails = new Set([...session.body.querySelectorAll("details")].filter(node => node.open).map(node => node.dataset.section));
      const scroll = session.dialog.scrollTop;
      session.body.replaceChildren();
      const body = session.body;
      if (!task) { body.append(element("p", "", t("Bu araştırma şu anki görünümde bulunamadı. Güncel durumu tekrar alabilirsin.")), button(t("Durumu yenile"), () => refreshWorkflow(true))); return; }
      session.heading.textContent = title(task);
      body.append(element("p", "workflow-label", `${stageTitle(task)} · ${labels[task.status] || t("Durum bekleniyor")}`));
      body.append(element("p", "workspace-help", t(task.result || task.report_text ? "Kaydedilen model: {model}" : "Seçili model: {model}", {model: task.model || t(task.result || task.report_text ? "Model kaydedilmemiş" : "Bilinmiyor")})));
      if (task.result || task.report_text) body.append(element("p", "workspace-help", task.report_generated_at ? t("Rapor üretildi: {date}", {date: formatDate(task.report_generated_at, true)}) : t("Üretim tarihi kaydedilmemiş")));
      body.append(element("p", "workspace-help", t("Kayıt tarihi: {date}", {date: formatDate(task.created_at, true)})), element("p", "workspace-help", t("Son güncelleme: {date}", {date: formatDate(task.updated_at, true)})));
      body.append(element("p", "workspace-help", t("Modelin markası veya fiyatı doğruluk kanıtı değildir; kaynakları, tarihi ve raporun sınırlarını kontrol et.")));
      const researchRequest = (state.data?.research_requests?.requests || []).find(row => row.id === task.request_id);
      if (researchRequest?.source_url) {
        const accessNote = element("section", "workflow-notice");
        accessNote.append(sourceLink(researchRequest.source_url, t("Verilen bağlantı")), element("p", "", t("Video veya ses işlenmedi. Sayfa metni ve kullanıcı açıklaması, medyanın izlendiği veya iddianın doğrulandığı anlamına gelmez.")));
        if (researchRequest.link_access?.status === "unavailable") accessNote.append(element("p", "", t("Verdiğin açıklama ve bulunabilen bağımsız kaynaklarla araştırma sürdürülebilir; erişilemeyen gönderi kanıt olarak gösterilmez.")));
        body.append(accessNote);
      }
      if (task.message) body.append(element("p", "workflow-status", statusText(task.message)));
      if (!task.result && task.source_title) body.append(element("p", "workspace-help", t("Asıl yayın: {title}", {title: task.source_title})));
      if (task.stale) body.append(element("p", "workflow-notice", t("Kaynak bu rapordan sonra değişmiş. Eski rapor korunuyor; güncel araştırma tamamlanmadan geliştirmeye alınamaz.")));
      if (task.coverage?.limited) body.append(element("p", "workflow-notice", t("Asıl makale tam okunamadığı için bu rapor sınırlı. İzlemeye alabilir veya pas geçebilirsin; geliştirmeye almadan önce yeniden araştır.")));
      if (task.retry_at) body.append(element("p", "workspace-help", t("Bir sonraki deneme: {date}", {date: formatDate(task.retry_at, true)})));
      if (task.coverage?.attempted_count) body.append(element("p", "workspace-help", t("{read} / {total} makale metni okunabildi.", {read: task.coverage.read_count || 0, total: task.coverage.attempted_count})));
      const result = task.result;
      if (result) {
        const quality = result.evidence_quality || {status: "unverified", coverage_complete: false, claims: []};
        const qualityNames = {verified: t("İddialar kaynaklarla desteklendi"), partial: t("Kanıt desteği kısmi"), contradicted: t("Kaynakla çelişen iddia var"), unverified: t("İddialar henüz denetlenmedi")};
        const qualityBox = element("section", "evidence-quality"); qualityBox.dataset.status = quality.status;
        qualityBox.append(element("strong", "", qualityNames[quality.status] || qualityNames.unverified));
        qualityBox.append(element("p", "", quality.summary_tr || t("Bu eski rapor yeni kanıt denetiminden geçmedi. Geliştirmeye almadan önce yeniden araştır.")));
        if (!evidenceVerified(task)) qualityBox.append(element("p", "", t("Raporu okuyabilir veya izlemeye alabilirsin. Geliştirme devri için güncel ve yeterli kanıt denetimi gerekiyor.")));
        qualityBox.append(element("small", "workspace-help", t("Kaynak desteği, yayındaki iddianın bağımsız deneyle doğrulandığı anlamına gelmez.")));
        if (quality.claims?.length) {
          const details = element("details"); details.dataset.section = "claims"; details.open = openDetails.has("claims");
          details.append(element("summary", "", t("İddia ve kanıt eşleşmeleri · {count}", {count: quality.claims.length})));
          const verdicts = {supported: t("Kaynak destekliyor"), contradicted: t("Kaynak çelişiyor"), uncertain: t("Destek belirsiz"), inaccessible: t("Kanıta erişilemedi"), not_factual: t("Öneri veya açık soru")};
          for (const claim of quality.claims) {
            const row = element("article", "evidence-claim");
            row.append(element("strong", "", verdicts[claim.status] || t("Denetlenmedi")), element("p", "", claim.text), element("p", "workspace-help", claim.reason_tr));
            for (const proof of claim.evidence || []) {
              if (proof.url) row.append(sourceLink(proof.url, t("Kanıt kaynağı")));
              if (proof.content_kind === "web_fetch_output") row.append(element("p", "workspace-help", t("Web okuma aracının çıktısı; asıl sayfanın birebir kopyası olmayabilir.")));
              if (proof.quote) row.append(element("blockquote", "", proof.quote));
            }
            details.append(row);
          }
          qualityBox.append(details);
        }
        body.append(qualityBox);
        if (result.research_plan) { section(body, t("Araştırmanın soruları"), result.research_plan.questions); section(body, t("Araştırmanın sınırı"), result.research_plan.scope); section(body, t("Tamamlanma ölçütü"), result.research_plan.stop_condition); }
        section(body, t("Kısa özet"), result.summary_tr);
        section(body, t("Bize ne kazandırabilir?"), result.benefit_tr);
        section(body, t("Neden ilgili?"), result.relevance_reason_tr);
        if (result.research_decision) section(body, t("Derin araştırma uygunluğu"), reviewDecisions[result.research_decision] || result.research_decision);
        const fitLabels = {high: t("Yüksek uygunluk"), possible: t("Olası uygunluk"), low: t("Düşük uygunluk"), yes: t("Uygun"), good: t("Uygun"), suitable: t("Uygun"), partial: t("Koşullara bağlı"), conditional: t("Koşullara bağlı"), no: t("Şu an uygun değil"), unsuitable: t("Şu an uygun değil"), unknown: t("Henüz net değil")};
        if (result.fit) section(body, t("Uygunluk"), fitLabels[result.fit] || result.fit);
        const recommendationLabels = {denemeye_deger: t("Denemeye değer"), izle: t("İzle"), uygun_degil: t("Uygun değil"), develop: t("Deneme planıyla geliştirmeye aday"), watch: t("İzlemeye değer"), dismiss: t("Şimdilik uygun değil"), investigate: t("Ek araştırma gerekiyor")};
        section(body, t("Önerilen karar"), recommendationLabels[result.recommendation] || result.recommendation);
        section(body, t("Mevcut düzenle uyumluluk"), result.compatibility_tr);
        section(body, t("Gerekenler"), result.requirements);
        section(body, t("Entegrasyon adımları"), result.integration_steps);
        section(body, t("Deneme ve doğrulama planı"), result.verification_plan);
        section(body, t("Açık sorular"), result.open_questions);
        section(body, t("Engeller"), result.blockers);
        section(body, t("Sınırlamalar ve belirsizlikler"), result.limitations);
        section(body, t("Sonraki adımlar"), result.next_steps);
      } else if (task.report_text) section(body, t("Kaydedilmiş rapor"), task.report_text);
      section(body, t("Araştırmanın kapsamı"), task.scope_notes);
      const sources = Array.isArray(task.sources) ? task.sources : [];
      if (sources.length) {
        const evidence = element("details", "workflow-evidence"); evidence.dataset.section = "evidence";
        evidence.open = openDetails.has("evidence");
        evidence.append(element("summary", "", t("Dayanaklar ve asıl yayınlar · {count}", {count: sources.length})));
        for (const source of sources) {
          const block = element("section", "workflow-source");
          block.append(sourceLink(source.url, source.title || source.title_tr || t("Asıl yayın")));
          if (source.publisher) block.append(element("p", "", source.publisher));
          block.append(element("p", "workspace-help", t("Yayın tarihi: {date}", {date: formatDate(source.published_at, true)})));
          if (source.fetched_at || source.read_at) block.append(element("p", "workspace-help", t("Metin alındı: {date}", {date: formatDate(source.fetched_at || source.read_at, true)})));
          for (const quote of result?.evidence || []) if (String(quote.item_id) === String(source.item_id || source.id)) block.append(element("blockquote", "", quote.quote));
          evidence.append(block);
        }
        evidence.append(element("p", "workspace-help", t("Alıntılar kullanılan metinle eşleştirilir. Modelin yorumu ve kaynak beyanları bağımsız doğrulama değildir."))); body.append(evidence);
      }
      const externalSources = Array.isArray(result?.external_sources) ? result.external_sources.filter(source => source && typeof source === "object") : [];
      if (externalSources.length) {
        const external = element("details", "workflow-evidence"); external.dataset.section = "external"; external.open = openDetails.has("external");
        external.append(element("summary", "", t("Web araştırmasında kullanılan yayınlar · {count}", {count: externalSources.length})));
        for (const source of externalSources) {
          const block = element("section", "workflow-source");
          block.append(sourceLink(source.url, source.title || t("Dış yayın")));
          if (typeof source.summary_tr === "string") block.append(element("p", "", source.summary_tr));
          external.append(block);
        }
        external.append(element("p", "workspace-help", t("Bu yayınlar araştırma motorunun bildirdiği web kaynaklarıdır. Özet ve bağlantı, iddiaların bağımsız doğrulandığı anlamına gelmez."))); body.append(external);
      }
      const actions = element("div", "workflow-actions");
      if (task.report_text || task.result) actions.append(downloadLink(task, "download", t("Raporu indir")));
      if (task.result || task.report_text) {
        actions.append(button(t("Yeniden araştır"), () => {
          if (!window.UFUKDiscovery?.open || operations) return;
          const previous = (state.data?.research_requests?.requests || []).find(row => row.id === task.request_id);
          const prefill = {question: previous?.question || t("Bu araştırmayı güncel kaynaklarla yeniden değerlendir: {title}", {title: title(task).slice(0, 400)}), topic: previous?.topic || task.profile_topic || "ai", need: previous?.need || "", source_url: previous?.source_url || task.source_url || task.sources?.[0]?.url || "", claim_text: previous?.claim_text || "", context_text: previous?.context_text || ""};
          session?.dialog.close(); window.UFUKDiscovery.open(document.getElementById("view-title"), prefill);
        }, false, "workflow-new-research"));
      }
      if (task.kind === "deep" && task.status === "ready" && (task.result || task.report_text)) {
        actions.append(button(t("Paylaşım taslağı oluştur"), async () => {
          const result = await mutation(`/api/platform/research/${id(task.id)}/draft`, {}, t("Yerel paylaşım taslağı hazır. Yazar ve metin iznini tamamlayıp önizleyebilirsin."), () => {});
          if (result?.entry?.id) window.location.assign(`/platform?entry=${encodeURIComponent(result.entry.id)}`);
        }, false, "workflow-share-draft"));
      }
      if (["screen", "review"].includes(task.kind) && ready(task)) actions.append(researchButton(findItem(task.item_id) || {id: task.item_id, revision: task.source_revision}, "report"));
      if (task.kind === "deep" && ready(task)) {
        const decisionBox = element("section", "workflow-decision");
        decisionBox.append(element("h3", "", t("Nasıl devam edelim?")), element("p", "", t("Bir karar seçmen yeterli. Not yazmak zorunda değilsin; geliştirme seçimi kodu veya canlı sistemi değiştirmez.")));
        const buttons = element("div", "workflow-actions");
        for (const [value, label] of [["develop", t("Geliştirmeye al")], ["watch", t("İzlemeye al")], ["dismiss", t("Pas geç")]]) {
          const node = button(label, () => mutation(`/api/workflow/tasks/${id(task.id)}/decision`, {decision: value, expected_version: task.version}, value === "develop" ? t("Geliştirmelere eklendi. Devir planını indirip uygulayacak kişiye verebilirsin.") : t("Kararın kaydedildi.")), value === "develop", `workflow-decision-${value}`);
          node.setAttribute("aria-pressed", String(task.decision === value)); node.disabled = operations > 0 || (value === "develop" && (Boolean(task.coverage?.limited) || !evidenceVerified(task))); buttons.append(node);
        }
        decisionBox.append(buttons); body.append(decisionBox);
        if (task.decision === "develop" && !task.coverage?.limited && evidenceVerified(task)) actions.append(downloadLink(task, "handoff", t("Devir planını indir")));
      }
      if (task.stale || task.coverage?.limited || (task.kind === "deep" && ready(task) && !evidenceVerified(task)) || ["queued", "blocked", "failed", "cancelled"].includes(task.status)) actions.append(button(t("Araştırmayı yeniden dene"), () => mutation(`/api/workflow/tasks/${id(task.id)}/retry`, {expected_version: task.version}, t("Araştırma yeniden sıraya alındı.")), true, "workflow-retry"));
      actions.append(button(t("Durumu yenile"), () => refreshWorkflow(true), false, "workflow-refresh"));
      body.append(actions);
      const profile = task.profile_snapshot && Object.keys(task.profile_snapshot).length ? task.profile_snapshot : (data().profiles || []).find(value => value.topic === task.profile_topic);
      if (profile) {
        const details = element("details", "workflow-profile"); details.dataset.section = "profile"; details.open = openDetails.has("profile");
        details.append(element("summary", "", t("Değerlendirilen çalışma alanı: {name}", {name: profile.project_name || profile.label || topics[task.profile_topic] || t("Profil")})));
        section(details, t("Bağlam"), profile.summary); section(details, "Ortam", profile.environment); section(details, t("Sınırlar"), profile.constraints); section(details, t("Öncelikler"), profile.priorities); body.append(details);
      }
      const item = findItem(task.item_id);
      if (item && context.openPersonalNote) {
        const advanced = element("details", "workflow-personal"); advanced.dataset.section = "personal"; advanced.open = openDetails.has("personal");
        advanced.append(element("summary", "", t("İsteğe bağlı kişisel not")), element("p", "workspace-help", t("Sistem raporu hazırdır. Kendi deneme sonucunu ayrıca saklamak istersen bu alanı kullanabilirsin.")), button(t("Kişisel notu aç"), () => { session.dialog.close(); context.openPersonalNote(item); }, false, "workflow-personal-note")); body.append(advanced);
      }
      body.append(element("p", "workspace-help", `Son durum: ${formatDate(task.updated_at, true)} · Kaynak revizyonu ${task.source_revision ?? "—"}`));
      session.dialog.scrollTop = scroll;
      if (hadFocus && !focused.isConnected) ([...body.querySelectorAll("[data-focus-key]")].find(node => node.dataset.focusKey === key && !node.disabled) || session.close)?.focus({preventScroll: true});
    }

    async function markSeen(task) {
      if (task.notification_read || !["ready", "failed"].includes(task.status) || seenPending.has(task.id)) return;
      seenPending.add(task.id);
      try {
        await post(`/api/workflow/tasks/${id(task.id)}/seen`, {});
        const currentTask = taskById(task.id); if (currentTask) currentTask.notification_read = true;
        render();
      } catch { /* Okunma işareti hatası raporu kapatmaz veya model işi başlatmaz. */ }
      finally { seenPending.delete(task.id); }
    }

    async function open(taskId, opener) {
      if (session || operations) return;
      const reader = document.getElementById("reader-dialog");
      if (reader?.open) reader.close();
      const next = makeDialog("report", opener);
      if (!next) return;
      next.taskId = String(taskId); epoch += 1; drawReport(true);
      const task = taskById(taskId); if (task) await markSeen(task);
      if (inFlight) await inFlight;
      if (session === next) refreshWorkflow(false);
    }

    async function refreshWorkflow(showError = false) {
      if (!visible() || !session || session.kind !== "report" || session.posting) return false;
      if (inFlight) return inFlight;
      const capturedEpoch = epoch;
      const capturedSession = session;
      inFlight = (async () => {
        try {
          const snapshot = await request("/api/workflow");
          if (!Array.isArray(snapshot?.tasks)) throw new Error(t("Araştırma durumu alınamadı. Yeniden deneyebilirsin."));
          if (capturedEpoch !== epoch || session !== capturedSession || !visible()) return false;
          state.data.workflow = snapshot;
          capturedSession.error.hidden = true;
          render(); drawReport();
          return true;
        } catch (error) {
          if (session === capturedSession && capturedEpoch === epoch && visible()) {
            capturedSession.error.textContent = t("{error} Son alınan rapor korunuyor.", {error: error.message}); capturedSession.error.hidden = false;
            if (showError) capturedSession.error.focus({preventScroll: true});
          }
          return false;
        } finally { inFlight = null; schedule(); }
      })();
      return inFlight;
    }

    function schedule() {
      clearTimeout(timer); timer = null;
      if (!visible() || !session || session.kind !== "report" || session.posting) return;
      const task = taskById(session.taskId);
      const delay = task?.status === "blocked" ? 60000 : task && activeStatuses.has(task.status) ? 2500 : null;
      if (delay !== null) timer = setTimeout(() => { timer = null; refreshWorkflow(); }, delay);
    }

    function settingsPanel() {
      const panel = element("section", "workflow-settings workspace-card");
      const settings = data().settings || {};
      panel.append(element("h3", "", t("Günlük inceleme")), element("p", "", manualMode() ? manualMessage() : settings.enabled === false ? t("Günlük hazırlık duraklatıldı. İstediğinde Günü hazırla ile başlatabilirsin.") : t("Her gün Türkiye saatiyle {hour}:00 civarında NVIDIA ilgi elemesi yapar; ilgili gelişmelerden en fazla {count} tanesini ön araştırmaya alır.", {hour: String(settings.daily_hour ?? 8).padStart(2, "0"), count: settings.screen_limit ?? 12})), element("p", "workspace-help", t("Güçlü adaylardan günde en fazla {count} tanesi {provider} uygunluk değerlendirmesine geçer. Araştırma gerekli görülürse derinleştirilir. Senin Derin araştır seçimin bu aşamayı doğrudan başlatır; uygulama kararı sende kalır.", {count: settings.auto_deep_limit ?? 2, provider: researchProvider()})), button(t("Günlük düzeni ayarla"), () => editSettings(), false, "workflow-settings-open"));
      if (settings.ai_daily_limit != null) panel.append(element("p", "workspace-help", researchProvider() === "NVIDIA" ? t("NVIDIA: günde {ai} başlatılan iş; özet, eleme, derin araştırma ve kanıt denetimi aynı bütçeyi paylaşır. Makale okuma: günde {articles}.", {ai: settings.ai_daily_limit, articles: settings.article_daily_limit ?? "—"}) : t("NVIDIA: günde {ai} · Makale okuma: günde {articles} başlatılan iş. {provider} uygunluk ve derin araştırma bütçesi ayrıdır; kullanım aşağıda görünür.", {ai: settings.ai_daily_limit, articles: settings.article_daily_limit ?? "—", provider: researchProvider()})));
      if (scheduled() && data().status?.next_run_at) panel.append(element("p", "workspace-help", t("Sonraki kontrol: {date}", {date: formatDate(data().status.next_run_at, true)})));
      return panel;
    }

    function editSettings() {
      const next = makeDialog("settings", document.activeElement);
      if (!next) return;
      let settings = data().settings || {};
      let version = settings.version ?? 0;
      const form = element("form", "workspace-form");
      const controls = {};
      const enabled = element("input"); enabled.type = "checkbox"; enabled.checked = settings.enabled !== false;
      const label = element("label", "workspace-label"); label.append(enabled, element("span", "", t("Her gün otomatik hazırla"))); form.append(label); controls.enabled = enabled;
      if (manualMode()) form.append(element("p", "workspace-help", t("Bu tercih kaydedilir; otomatik günlük takip bu oturumda kapalı kalır.")));
      for (const [key, text, min, max, value] of [["daily_hour", t("Günün hazırlanacağı saat"), 0, 23, 8], ["screen_limit", t("Günlük ön inceleme sayısı"), 1, 24, 12], ["auto_deep_limit", t("Uygunluk değerlendirmesine geçecek aday sayısı"), 0, 5, 2], ["auto_threshold", t("Uygunluk değerlendirmesine geçiş puanı"), 80, 100, 90]]) {
        const field = element("div", "workspace-field");
        const caption = element("label", "workspace-label", text);
        const input = element("input", "workspace-input"); input.type = "number"; input.min = min; input.max = max; input.step = 1; input.required = true; input.value = String(settings[key] ?? value); input.id = `workflow-setting-${key}`; caption.htmlFor = input.id; field.append(caption, input); form.append(field); controls[key] = input;
      }
      const values = () => ({enabled: enabled.checked, ...Object.fromEntries(Object.entries(controls).filter(([key]) => key !== "enabled").map(([key, input]) => [key, Number(input.value)]))});
      let baseline = JSON.stringify(values()); next.dirty = () => JSON.stringify(values()) !== baseline;
      const save = button(t("Ayarları kaydet"), () => {}, true, "workflow-settings-save"); save.type = "submit";
      form.append(element("p", "workspace-help", t("Saat Türkiye saatidir. NVIDIA puanı aday gösterir; derin araştırmanın gerekliliğini seçili araştırma sağlayıcısı değerlendirir. Canlı sistemde değişiklik yapılmaz.")), save);
      const latest = button(t("Değişikliklerimi bırakıp güncel ayarları al"), async () => {
        if (operations) return;
        latest.disabled = true;
        try {
          const snapshot = await request("/api/workflow");
          if (!snapshot?.settings) throw new Error(t("Güncel ayarlar alınamadı."));
          settings = snapshot.settings; version = settings.version ?? 0;
          enabled.checked = settings.enabled !== false;
          for (const [key, input] of Object.entries(controls)) if (key !== "enabled") input.value = String(settings[key]);
          baseline = JSON.stringify(values()); state.data.workflow = snapshot; next.error.hidden = true;
          next.discard?.parentElement?.remove(); next.discard = null;
          save.focus({preventScroll: true});
        } catch (error) { showSessionError(error.message); }
        finally { latest.disabled = false; }
      }, false, "workflow-settings-latest");
      form.append(latest); next.body.append(form);
      form.addEventListener("submit", async event => {
        event.preventDefault(); if (!form.reportValidity()) return;
        const result = await mutation("/api/workflow/settings", {...values(), expected_version: version}, t("Günlük inceleme ayarları kaydedildi."), value => { if (value && state.data.workflow) state.data.workflow.settings = value.settings || value; });
        if (result && session === next) { baseline = JSON.stringify(values()); next.dialog.close(); }
      });
    }

    function sync() { drawReport(); }
    document.addEventListener("visibilitychange", () => { clearTimeout(timer); timer = null; if (visible() && session?.kind === "report") refreshWorkflow(); });
    window.addEventListener("focus", () => { if (visible() && session?.kind === "report") refreshWorkflow(); });
    window.addEventListener("pagehide", () => { disposed = true; epoch += 1; clearTimeout(timer); timer = null; });
    window.addEventListener("pageshow", event => { if (event.persisted) { disposed = false; if (session?.kind === "report") refreshWorkflow(); } });
    window.addEventListener("beforeunload", event => { if (session?.dirty() || session?.posting) { event.preventDefault(); event.returnValue = ""; } });
    return {summaryFor, taskForItem, counts, statusStrip, researchButton, renderView, settingsPanel, open, run, sync, busy};
  };
})();
