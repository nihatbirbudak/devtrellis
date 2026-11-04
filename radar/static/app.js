// Kaynaklı haber havuzunu güvenli DOM öğeleriyle gösterir; seçimleri ve araştırma kuyruğunu yerel API’ye kaydeder.
"use strict";

(() => {
  const UI = globalThis.UfukUI;
  const t = (key, values = {}) => UI ? UI.t(key, values) : key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
  UI?.initialize();
  const topics = {};
  const icons = {
    arrow: ["M7 17 17 7M7 7h10v10"], check: ["m5 12 4 4L19 6"],
    bookmark: ["M6 4h12v17l-6-4-6 4V4Z"], clock: ["M12 8v4l3 2", "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0"],
    close: ["m7 7 10 10M17 7 7 17"], search: ["M17 17l4 4", "M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0"],
    refresh: ["M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.5-1L20 9M4 15l2.4 3A7 7 0 0 0 18 17"],
    info: ["M12 11v6M12 7h.01", "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0"],
    newspaper: ["M5 4h14v16H5V4ZM8 8h8M8 12h3M8 16h3M14 12h2M14 16h2"],
    globe: ["M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z", "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0"],
  };
  const state = { data: null, view: "selection", topic: "all", search: "", sourceId: "", sourceBranch: "", sourceLanguage: "", sourceKind: "", sourceHealth: "", collectionId: "", readStatus: "all", sort: "relevance", showArchive: true, layout: "grid", readerItemId: null, pending: new Set(), loading: true };
  let workspace = null;
  let insights = null;
  let workflow = null;
  let discovery = null;
  let sourceLearning = null;
  const content = document.getElementById("content");
  const errorBox = document.getElementById("request-error");
  const collectButton = document.getElementById("collect-button");
  const readerDialog = document.getElementById("reader-dialog");
  const readerContent = document.getElementById("reader-content");
  const jobDialog = document.getElementById("job-dialog");
  const jobContent = document.getElementById("job-content");
  let jobSession = null;
  let readerOpener = null;
  let readerOpenerKey = null;
  let readerReturnFocus = null;
  let pollTimer = null;
  let toastTimer = null;
  let searchTimer = null;
  let fetchInFlight = null;
  let disposed = false;
  let writeEpoch = 0;
  let freshnessNotice = null;
  const itemCache = new Map();
  let pool = { key: null, items: [], total: 0, nextOffset: 0, hasMore: false, loading: false, error: "", requestId: 0 };
  try { if (localStorage.getItem("ufuk-layout") === "list") state.layout = "list"; } catch { /* Depolama kapalıysa kart düzeni kullanılır. */ }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    for (const shape of icons[name] || icons.info) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", shape);
      svg.append(path);
    }
    return svg;
  }

  function renderTopicControls() {
    const controls = document.querySelector(".topic-filters");
    if (!controls) return;
    controls.replaceChildren();
    for (const [id, label] of [["all", t("Tümü")], ...Object.entries(topics)]) {
      const button = element("button", `topic-button${state.topic === id ? " active" : ""}`);
      button.type = "button";
      button.dataset.topic = id;
      button.setAttribute("aria-pressed", String(state.topic === id));
      if (id !== "all") button.append(element("span", `topic-dot topic-${id}`));
      button.append(document.createTextNode(label));
      button.addEventListener("click", () => {
        state.topic = id;
        render();
        announce(t("{topic} konusu seçildi.", {topic: label}));
      });
      controls.append(button);
    }
  }

  async function loadTopics() {
    try {
      const rows = await request("/api/topics");
      if (!Array.isArray(rows) || !rows.length) throw new Error(t("Konular yüklenemedi. Sayfayı yenileyerek tekrar deneyebilirsin."));
      const labels = {};
      for (const row of rows) {
        if (!/^[a-z][a-z0-9_-]*$/.test(row.id) || typeof row.label_tr !== "string" || typeof row.label_en !== "string") throw new Error(t("Konular yüklenemedi. Sayfayı yenileyerek tekrar deneyebilirsin."));
        labels[row.id] = UI?.language === "en" ? row.label_en : row.label_tr;
      }
      for (const id of Object.keys(topics)) delete topics[id];
      Object.assign(topics, labels);
      renderTopicControls();
      if (state.data) render();
    } catch (error) { toast(error.message, true); }
  }

  function safeUrl(value) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }

  function sourceLink(url, label, className = "source-link") {
    const href = safeUrl(url);
    const link = element(href ? "a" : "span", className, label);
    if (href) {
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.append(icon("arrow"));
      link.setAttribute("aria-label", t("{label} — yeni sekmede açılır", {label}));
    }
    return link;
  }

  function dateValue(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatDate(value, includeTime = false) {
    const date = dateValue(value);
    if (!date) return t("Bilinmiyor");
    return new Intl.DateTimeFormat(UI?.language === "en" ? "en-US" : "tr-TR", { day: "numeric", month: "short", year: "numeric", ...(includeTime ? { hour: "2-digit", minute: "2-digit" } : {}) }).format(date);
  }

  function feedback(item) {
    return typeof item.feedback === "object" && item.feedback !== null ? item.feedback.value : item.feedback;
  }

  function findItem(itemId) {
    return itemCache.get(String(itemId)) || state.data?.items.find(item => String(item.id) === String(itemId));
  }

  function invalidatePool() { pool.key = null; }

  function preparePool() {
    const parameters = new URLSearchParams({ query: state.search, topic: state.topic, source: state.sourceId, include_archive: String(state.showArchive), limit: "100", collection_id: state.collectionId, read_status: state.readStatus, sort: state.sort });
    const key = parameters.toString();
    if (pool.key === key) return;
    pool = { key, items: [], total: 0, nextOffset: 0, hasMore: false, loading: true, error: "", requestId: 0 };
    const current = pool;
    queueMicrotask(() => { if (pool === current) loadPool(); });
  }

  async function loadPool(append = false) {
    const current = pool;
    const requestId = ++current.requestId;
    const offset = append ? current.nextOffset : 0;
    const previousCount = current.items.length;
    const returnToResults = append && document.activeElement?.dataset.focusKey === "pool-load-more";
    current.loading = true;
    current.error = "";
    if (state.view === "all") render();
    try {
      const result = await request(`/api/items?${current.key}&offset=${offset}`);
      if (pool !== current || current.requestId !== requestId) return;
      if (!result || !Array.isArray(result.items) || !Number.isSafeInteger(result.total) || result.total < 0 || result.offset !== offset) throw new Error(t("Havuz sayfası beklenen biçimde gelmedi. Yeniden dene."));
      for (const item of result.items) itemCache.set(String(item.id), item);
      const combined = append ? [...current.items, ...result.items] : result.items;
      current.items = [...new Map(combined.map(item => [String(item.id), item])).values()];
      current.total = result.total;
      current.nextOffset = offset + result.items.length;
      current.hasMore = result.items.length > 0 && current.nextOffset < current.total;
    } catch (error) {
      if (pool !== current || current.requestId !== requestId) return;
      current.error = error.message;
    } finally {
      if (pool === current && current.requestId === requestId) {
        current.loading = false;
        if (state.view === "all") {
          render();
          if (returnToResults && document.activeElement === content && !readerDialog?.open && !jobDialog?.open) {
            const target = current.error ? content.querySelector('[data-focus-key="pool-load-more"]') : content.querySelectorAll(".news-card")[previousCount]?.querySelector(".card-open");
            target?.focus({ preventScroll: true });
          }
        }
      }
    }
  }

  function snoozed(item) {
    const until = dateValue(item.snoozed_until);
    return feedback(item) === "sonra" && (!until || until.getTime() > Date.now());
  }

  function matches(item) {
    if (state.topic !== "all" && item.topic !== state.topic) return false;
    if (!state.search) return true;
    const translated = workflowSummary(item);
    return [translated?.title_tr, translated?.summary_tr, translated?.benefit_tr, item.ai_summary?.title_tr, item.ai_summary?.summary_tr, item.ai_summary?.benefit_tr, item.title, item.summary, item.excerpt, item.benefit, item.source_name, item.name, item.result, item.publisher, item.source_type, ...(item.branches || [])].filter(Boolean).join(" ").toLocaleLowerCase("tr-TR").includes(state.search.toLocaleLowerCase("tr-TR"));
  }

  function publicationAge(item, now = Date.now()) {
    const published = dateValue(item.published_at);
    if (!published || published.getTime() > now) return null;
    return (now - published.getTime()) / 86400000;
  }

  function isNew(item, now = Date.now()) {
    const age = publicationAge(item, now);
    return !item.is_archive && age !== null && age <= 14;
  }

  function workflowTask(item) {
    return (state.data?.workflow?.tasks || []).filter(task => !task.request_id && ["screen", "deep"].includes(task.kind) && String(task.item_id) === String(item.id) && task.status === "ready" && !task.stale && Number(task.source_revision) === Number(item.revision) && task.result?.title_tr && task.result?.summary_tr)
      .sort((a, b) => Number(b.kind === "deep") - Number(a.kind === "deep") || String(b.updated_at).localeCompare(String(a.updated_at)))[0] || null;
  }

  function workflowSummary(item) { return workflowTask(item)?.result || null; }

  function selectionReady(item) {
    const summary = workflowSummary(item);
    if (summary?.fit === "low" || summary?.recommendation === "uygun_degil") return false;
    return Boolean(summary || (item.ai_summary?.title_tr && item.ai_summary?.summary_tr) || ["editor_karti", "editor_ozeti"].includes(item.summary_status));
  }

  function selectedItems(items) {
    const now = Date.now();
    const limit = state.data?.personal?.settings?.daily_limit || 8;
    const compare = (a, b) => b.modelRelevance - a.modelRelevance || b.priority - a.priority || Number(b.fresh) - Number(a.fresh) || (dateValue(b.item.published_at)?.getTime() || 0) - (dateValue(a.item.published_at)?.getTime() || 0);
    const candidates = items.filter(item => selectionReady(item) && feedback(item) !== "gereksiz" && !snoozed(item) && item.personal_rank?.topic_weight !== 0 && !isRead(item)).map(item => {
      const age = publicationAge(item, now);
      const relevance = Math.max(0, Math.min(100, Number(item.score) || 0));
      // Yayın tarihi esas alınır; yeni taranmış eski bir kayıt güncellik kazanmaz.
      const freshness = age === null ? 0 : 55 * Math.exp(-age / 60);
      const personal = Number.isFinite(item.personal_score) ? item.personal_score : relevance * 0.45;
      const modelScore = workflowSummary(item)?.relevance_score;
      const modelRelevance = Number.isFinite(modelScore) ? Math.max(0, Math.min(100, modelScore)) : -1;
      const repository = /^https:\/\/(?:www\.)?github\.com\/([^/]+\/[^/#?]+)/i.exec(item.url || "");
      const project = repository ? `github:${repository[1].toLowerCase()}` : `item:${item.id}`;
      return { item, modelRelevance, priority: personal + freshness, fresh: isNew(item, now), topic: item.topic || "diger", source: item.source_id || item.source_name || "bilinmeyen", project };
    }).sort(compare);
    const chosen = [];
    const used = new Set();
    const topicCounts = new Map();
    const sourceCounts = new Map();
    const projects = new Set();
    const add = candidate => {
      chosen.push(candidate);
      used.add(candidate);
      projects.add(candidate.project);
      topicCounts.set(candidate.topic, (topicCounts.get(candidate.topic) || 0) + 1);
      sourceCounts.set(candidate.source, (sourceCounts.get(candidate.source) || 0) + 1);
    };
    // Her mevcut konuya önce bir yer; ardından az temsil edilen konulara sıra gelir.
    for (const candidate of candidates) {
      if (chosen.length === limit) break;
      if (!topicCounts.has(candidate.topic) && !projects.has(candidate.project) && (sourceCounts.get(candidate.source) || 0) < 2) add(candidate);
    }
    while (chosen.length < limit) {
      const available = candidates.filter(candidate => !used.has(candidate) && !projects.has(candidate.project) && (sourceCounts.get(candidate.source) || 0) < 2);
      if (!available.length) break;
      const minimum = Math.min(...available.map(candidate => topicCounts.get(candidate.topic) || 0));
      add(available.find(candidate => (topicCounts.get(candidate.topic) || 0) === minimum));
    }
    return chosen.sort(compare).map(candidate => candidate.item);
  }

  function isRead(item) {
    return Boolean(state.data?.personal?.read_states?.find(entry => String(entry.item_id) === String(item.id))?.read);
  }

  function displayTitle(item) { return workflowSummary(item)?.title_tr || item.ai_summary?.title_tr || item.title || t("Başlığı olmayan kaynak kaydı"); }
  function displayBenefit(item) { return workflowSummary(item)?.benefit_tr || item.ai_summary?.benefit_tr || item.benefit; }

  function shortSourceText(value, length = 210) {
    const text = String(value).replace(/\s+/g, " ").trim();
    const characters = Array.from(text);
    if (characters.length <= length) return text;
    const excerpt = characters.slice(0, length - 1).join("");
    const boundary = excerpt.lastIndexOf(" ");
    return `${boundary > length * 0.75 ? excerpt.slice(0, boundary) : excerpt}…`;
  }

  function optionalText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = String(value);
  }

  function summaryCaption(item) {
    const task = workflowTask(item);
    if (task) {
      const kind = task.kind === "screen" ? t("NVIDIA ön araştırması") : t("{provider} derin araştırması", {provider: {claude: "Claude", codex: "Codex", nvidia: "NVIDIA"}[task.engine] || t("Yapay zekâ")});
      const quality = task.result?.evidence_quality;
      const label = quality?.status === "verified" && quality.coverage_complete === true ? t("kaynakla desteklendi") : quality?.status === "contradicted" ? t("kaynakla çelişki var") : quality?.status === "partial" ? t("kanıt kısmi") : t("kanıt denetimi bekliyor");
      return `${kind} · ${label}`;
    }
    if (item.ai_summary?.summary_tr) return t("Türkçe yapay zekâ özeti · kaynak metnine dayalı");
    const isTurkish = /^tr(?:[-_]|$)/i.test(String(item.language || ""));
    if (item.summary_status === "editor_karti") return t("Editörün başlangıç kartı · güncel haber olarak sayılmaz");
    if (item.summary_status === "editor_ozeti") return t("Türkçe editör özeti · kaynak beyanı");
    if (item.summary_status === "kaynak_metni") return isTurkish ? t("Kaynağın açıklama metni") : t("Kaynak metni · Türkçe özet bekliyor");
    if (item.summary_status === "ozet_bekliyor") return t("Türkçe özet bekliyor");
    return t("Kaynak metni · özet türü doğrulanmadı");
  }

  function itemText(item) {
    return workflowSummary(item)?.summary_tr || item.ai_summary?.summary_tr || item.summary || item.excerpt || t("Kaynakta kısa açıklama bulunamadı. Ayrıntılar için asıl kaynağı açabilirsin.");
  }

  function setTextLanguage(node, item) {
    if (workflowSummary(item) || item.ai_summary?.summary_tr) {
      const task = workflowTask(item);
      const language = task?.language || task?.result?.language || item.ai_summary?.language || "tr";
      if (["en", "tr"].includes(language)) node.lang = language;
      return;
    }
    if (/^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/i.test(String(item.language || ""))) node.lang = item.language;
  }

  function sourceAvatar(item) {
    const source = String(item.source_name || item.name || t("Kaynak")).trim();
    const letters = source.split(/\s+/).slice(0, 2).map(word => Array.from(word)[0] || "").join("").toLocaleUpperCase("tr-TR");
    const avatar = element("span", `source-avatar avatar-${Object.hasOwn(topics, item.topic) ? item.topic : "jarvis"}`, letters);
    avatar.setAttribute("aria-hidden", "true");
    return avatar;
  }

  function announce(message) { document.getElementById("announcer").textContent = message; }

  function toast(message, isError = false, action = null) {
    clearTimeout(toastTimer);
    const node = document.getElementById("toast");
    const workspaceDialog = document.querySelector(".workspace-dialog[open]");
    if (workspaceDialog) workspaceDialog.append(node);
    else if (jobDialog?.open) jobDialog.append(node);
    else if (readerDialog?.open) readerDialog.append(node);
    else if (node.parentElement !== document.body) document.body.append(node);
    node.replaceChildren(element("span", "toast-message", message));
    if (action) {
      const button = element("button", "toast-undo", action.label);
      button.type = "button";
      button.addEventListener("click", () => {
        button.disabled = true;
        clearTimeout(toastTimer);
        action.run();
      }, { once: true });
      node.append(button);
    }
    node.classList.toggle("error", isError);
    node.hidden = false;
    toastTimer = setTimeout(() => { node.hidden = true; }, action ? 12000 : isError ? 8000 : 5000);
  }

  function showError(message, retry = false) {
    errorBox.replaceChildren(element("span", "", message));
    if (retry) {
      const button = element("button", "", t("Yeniden dene"));
      button.type = "button";
      button.addEventListener("click", () => refreshState());
      errorBox.append(button);
    }
    errorBox.hidden = false;
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options, signal: controller.signal });
      let data = null;
      try { data = await response.json(); } catch { /* Boş yanıt durum koduyla değerlendirilir. */ }
      if (!response.ok) {
        const error = new Error(typeof data?.detail === "string" ? t(data.detail) : t("İstek tamamlanamadı ({status}).", {status: response.status}));
        error.status = response.status;
        throw error;
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error(t("Yerel sunucudan yanıt alınamadı. Biraz sonra yeniden dene."));
      if (error instanceof TypeError) throw new Error(t("Yerel radara ulaşılamıyor. Uygulamanın açık olduğundan emin ol."));
      throw error;
    } finally { clearTimeout(timeout); }
  }

  function post(path, payload) {
    if (!state.data?.csrf_token) return Promise.reject(new Error(t("Oturum bilgisi henüz hazır değil. Sayfayı yenileyip tekrar dene.")));
    writeEpoch += 1;
    clearFreshnessNotice();
    return request(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Radar-CSRF": state.data.csrf_token }, body: JSON.stringify(payload) });
  }

  function pageVisible() {
    return !disposed && document.visibilityState !== "hidden";
  }

  function passiveUpdateSafe() {
    if (!pageVisible() || readerDialog?.open || jobDialog?.open || window.UFUKWorkspace?.busy?.() || window.UFUKWorkflow?.busy?.() || discovery?.busy() || sourceLearning?.busy() || state.pending.size) return false;
    const active = document.activeElement;
    if (active && (["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName) || active.isContentEditable)) return false;
    const selection = window.getSelection?.();
    return !selection || selection.isCollapsed;
  }

  function scheduleRefresh(delay) {
    clearTimeout(pollTimer);
    pollTimer = null;
    if (pageVisible()) pollTimer = setTimeout(() => { pollTimer = null; refreshState(true); }, delay);
  }

  function clearFreshnessNotice() {
    freshnessNotice?.remove();
    freshnessNotice = null;
  }

  function showFreshnessNotice() {
    if (freshnessNotice) return;
    const notice = element("div", "info-note freshness-notice");
    notice.setAttribute("role", "status");
    notice.append(icon("refresh"), element("p", "", t("Yeni bilgiler var. Okuduğun alanı ve yazdıklarını koruduk.")));
    const button = element("button", "button button-secondary", t("Görünümü güncelle"));
    button.type = "button";
    button.addEventListener("click", async () => {
      if (state.pending.size || readerDialog?.open || jobDialog?.open || window.UFUKWorkspace?.busy?.() || window.UFUKWorkflow?.busy?.()) return;
      button.disabled = true;
      await refreshAfterMutation();
      button.disabled = false;
    });
    notice.append(button);
    content.before(notice);
    freshnessNotice = notice;
  }

  function poolFingerprint(data) {
    return JSON.stringify([data?.items, data?.stats?.total, data?.stats?.new_count, data?.personal,
      data?.sources?.map(source => [source.id, source.last_success_at])]);
  }

  async function refreshState(passive = false) {
    if (passive && !pageVisible()) return false;
    if (fetchInFlight) return fetchInFlight;
    const startedEpoch = writeEpoch;
    fetchInFlight = (async () => {
      try {
        const data = await request("/api/state");
        if (!data || !Array.isArray(data.items) || !Array.isArray(data.sources) || !Array.isArray(data.jobs)) throw new Error(t("Haber havuzu beklenen biçimde gelmedi. Yeniden dene."));
        if (passive && (!pageVisible() || startedEpoch !== writeEpoch)) {
          if (pageVisible()) scheduleRefresh(60000);
          return false;
        }
        const changed = JSON.stringify(state.data) !== JSON.stringify(data);
        if (passive && changed && !passiveUpdateSafe()) {
          // Açık taslak eski kayda bağlı kalır; yeniden başlayan sunucunun oturum bilgisi yenilenir.
          if (state.data && typeof data.csrf_token === "string" && data.csrf_token) state.data.csrf_token = data.csrf_token;
          showFreshnessNotice();
        }
        else {
          clearFreshnessNotice();
          if (!passive || changed) {
            const poolChanged = poolFingerprint(state.data) !== poolFingerprint(data);
            state.data = data;
            for (const item of data.items) itemCache.set(String(item.id), item);
            if (poolChanged) invalidatePool();
            state.loading = false;
            render();
          }
        }
        errorBox.hidden = true;
        scheduleRefresh(data.collection?.running || data.workflow?.status?.running || data.discovery?.running || data.source_scout?.running || data.research_requests?.requests?.some(row => ["awaiting_sources", "discovering", "queued", "running"].includes(row.status)) ? 2500 : 60000);
        return true;
      } catch (error) {
        if (passive && (!pageVisible() || startedEpoch !== writeEpoch)) {
          if (pageVisible()) scheduleRefresh(60000);
          return false;
        }
        state.loading = false;
        if (!passive) content.setAttribute("aria-busy", "false");
        if (!state.data) renderConnectionError(error.message);
        else showError(t("{error} Son alınan bilgiler gösteriliyor.", {error: error.message}), true);
        scheduleRefresh(state.data?.collection?.running ? 5000 : 60000);
        return false;
      } finally { fetchInFlight = null; }
    })();
    return fetchInFlight;
  }

  async function refreshAfterMutation(passive = false) {
    // Yazıdan önce başlamış okuma yeni tercihi içermeyebilir; bitince yeni okuma gerekir.
    if (fetchInFlight) await fetchInFlight;
    return refreshState(passive);
  }

  function renderConnectionError(message) {
    document.getElementById("pool-status").textContent = t("Yerel havuza bağlantı kurulamadı");
    document.getElementById("view-description").textContent = t("Bağlantı kurulduğunda gerçek kaynaklar ve kayıtlar burada görünecek.");
    content.replaceChildren(emptyState(t("Gazeteyi henüz açamadık"), message, t("Yeniden dene"), () => refreshState(), "globe"));
  }

  function updateHeader() {
    const { items, sources, jobs, stats = {}, collection = {} } = state.data;
    const healthy = sources.filter(source => source.last_success_at && !source.last_error).length;
    const unread = items.filter(item => isNew(item) && feedback(item) !== "gereksiz").length;
    const total = Number.isSafeInteger(stats.total) && stats.total >= items.length ? stats.total : items.length;
    const limited = total > items.length;
    const selection = selectedItems(items);
    const saved = Number.isSafeInteger(stats.saved_count) ? stats.saved_count : items.filter(item => feedback(item) === "yararli").length;
    const later = Number.isSafeInteger(stats.later_count) ? stats.later_count : items.filter(snoozed).length;
    const sampleCount = count => `${count}${limited ? "+" : ""}`;
    const statsText = items.length ? t("{total} gelişme · {sources} kaynak · {unread} yeni", {total, sources: sources.length, unread: sampleCount(unread)}) + (limited ? t(" · Havuzdan {count} kayıt gösteriliyor; kaydettiklerin ve araştırmaların dahil", {count: items.length}) : "") : t("{count} kaynak hazır · İlk tarama seni bekliyor", {count: sources.length});
    document.getElementById("pool-status").textContent = statsText;
    document.getElementById("collection-status").textContent = collection.running ? t("Kaynaklar kontrol ediliyor…") : collection.last_finished_at ? t("Son tarama {date} · {healthy}/{total} kaynak sağlıklı", {date: formatDate(collection.last_finished_at, true), healthy, total: sources.length}) : t("Henüz kaynak taraması yapılmadı");
    const preparing = Boolean(state.data.workflow?.status?.running);
    collectButton.disabled = preparing || state.pending.has("workflow") || !state.data.workflow;
    collectButton.classList.toggle("is-running", preparing);
    collectButton.querySelector("span").textContent = preparing ? t("Hazırlanıyor…") : t("Günü hazırla");
    collectButton.setAttribute("aria-busy", String(preparing));
    const workflowCounts = workflow?.counts() || {research: 0, decisions: 0, development: 0, notifications: 0};
    const counts = { selection: selection.length, all: total, saved, later, ...workflowCounts, "legacy-notes": jobs.length, sources: sources.length, collections: state.data.personal?.stats?.collection_count || 0 };
    for (const [key, count] of Object.entries(counts)) {
      for (const node of document.querySelectorAll(`[data-count="${key}"]`)) node.textContent = count;
    }
    optionalText("sidebar-health", t("{healthy} / {total} kaynak sağlıklı", {healthy, total: sources.length}));
    optionalText("metric-total", total);
    optionalText("metric-sources", `${healthy} / ${sources.length}`);
    optionalText("metric-selected", workflowCounts.decisions);
    optionalText("metric-saved", saved);
    optionalText("selection-count", t("{count} gelişme", {count: selection.length}));
    const words = selection.reduce((count, item) => count + `${item.title || ""} ${itemText(item)} ${item.benefit || ""}`.trim().split(/\s+/).length, 0);
    optionalText("reading-time", words ? t("~{minutes} dk okuma", {minutes: Math.max(1, Math.ceil(words / 220))}) : t("İlk seçkiyi bekliyor"));
    const healthNode = document.getElementById("sidebar-health");
    if (healthNode) healthNode.dataset.health = sources.some(source => source.last_error) ? "warning" : healthy ? "healthy" : "unknown";
  }

  function emptyState(title, description, actionLabel, action, iconName = "newspaper") {
    const wrapper = element("div", "empty-state");
    const image = element("div", "empty-icon");
    image.append(icon(iconName));
    wrapper.append(image, element("h3", "", title), element("p", "", description));
    if (actionLabel && action) {
      const button = element("button", "button button-primary", actionLabel);
      button.type = "button";
      button.addEventListener("click", action);
      wrapper.append(button);
    }
    return wrapper;
  }

  function rubric(topic) {
    const label = element("span", "rubric");
    const dot = element("span", `topic-dot topic-${Object.hasOwn(topics, topic) ? topic : "jarvis"}`);
    dot.setAttribute("aria-hidden", "true");
    label.append(dot, document.createTextNode(topics[topic] || t("Gelişme")));
    return label;
  }

  function evidenceLabel(value) {
    const labels = { primary: t("Birincil kaynak"), birincil: t("Birincil kaynak"), birincil_duyuru: t("Birincil duyuru"), official: t("Resmî kaynak"), official_release: t("Resmî sürüm"), resmi_surum: t("Resmî sürüm"), release: t("Sürüm duyurusu"), paper: t("Araştırma yayını"), arastirma_projesi: t("Araştırma projesi"), teknik_yayin: t("Teknik yayın"), preprint: t("Ön baskı"), on_baski: t("Ön baskı · hakem değerlendirmesi doğrulanmadı"), official_blog: t("Resmî blog"), source_excerpt: t("Kaynak alıntısı"), announcement: t("Resmî duyuru"), uretici_duyurusu: t("Üretici duyurusu"), kurum_duyurusu: t("Kurum duyurusu"), rss: t("Kaynak akışı"), editorial: t("Editör kartı") };
    return labels[value] || t("Kaynak bağlantısı açık");
  }

  function detailRow(list, label, value) { list.append(element("dt", "", label), element("dd", "", value)); }

  function itemAgeLabel(item) {
    return item.is_archive ? t("Arşiv / ilk aktarım") : isNew(item) ? t("Yeni gelişme") : publicationAge(item) === null ? t("Tarihi doğrulanmadı") : t("Havuz kaydı");
  }

  function renderActions(item, context = "card") {
    const actions = element("div", context === "reader" ? "card-actions reader-actions" : "card-actions");
    actions.setAttribute("aria-label", t("{title} için seçimler", {title: displayTitle(item)}));
    const busy = state.pending.has(`item-${item.id}`);
    if (workflow) actions.append(workflow.researchButton(item, context));
    const extra = element("details", "card-extra-actions");
    const extraTitle = element("summary", "", t("Kaydet veya sonra oku"));
    extraTitle.dataset.focusKey = `${context}-extra-${item.id}`;
    const extraButtons = element("div", "card-feedback-options");
    extra.append(extraTitle, extraButtons);
    for (const [value, label, iconName] of [["yararli", t("Yararlı"), "check"], ["sonra", t("Sonra"), "clock"], ["gereksiz", t("Gereksiz"), "close"]]) {
      const button = element("button", "feedback-button");
      button.type = "button";
      button.dataset.value = value;
      button.dataset.focusKey = `${context}-feedback-${item.id}-${value}`;
      button.setAttribute("aria-pressed", String(feedback(item) === value));
      button.setAttribute("aria-label", `${label}: ${displayTitle(item)}`);
      button.title = feedback(item) === value ? t("Seçimi kaldır") : value === "sonra" ? t("7 gün sonraya ayır") : label;
      button.disabled = busy;
      button.append(icon(iconName), document.createTextNode(label));
      button.addEventListener("click", () => setFeedback(item, feedback(item) === value ? "yok" : value));
      extraButtons.append(button);
    }
    actions.append(extra);
    return actions;
  }

  function renderDates(item) {
    const dates = element("dl", "reader-meta");
    detailRow(dates, t("Yayın"), formatDate(item.published_at));
    detailRow(dates, t("Olay"), item.event_at ? formatDate(item.event_at) : t("Kaynakta belirtilmemiş"));
    detailRow(dates, t("İlk görüldü"), formatDate(item.first_seen_at, true));
    detailRow(dates, t("Son görüldü"), formatDate(item.last_seen_at, true));
    if (item.updated_at) detailRow(dates, t("Kaynak güncellemesi"), formatDate(item.updated_at, true));
    if (Number.isFinite(Number(item.score))) detailRow(dates, t("İlgi puanı"), Math.round(Number(item.score)));
    detailRow(dates, t("Kayıt sürümü"), item.revision || 1);
    if (item.source_title) detailRow(dates, t("Kaynak başlığı"), item.source_title);
    return dates;
  }

  function renderSnooze(item) {
    if (!snoozed(item)) return null;
    const note = element("div", "snooze-note");
    note.append(icon("clock"), document.createTextNode(item.snoozed_until ? t("{date} tarihine kadar sonraya ayrıldı", {date: formatDate(item.snoozed_until)}) : t("Sonraya ayrıldı")));
    return note;
  }

  function renderCard(item) {
    const card = element("article", "news-card");
    card.classList.toggle("is-read", isRead(item));
    card.dataset.itemId = String(item.id);
    const body = element("div", "card-content");
    const top = element("div", "card-topline");
    top.append(rubric(item.topic), element("span", isNew(item) ? "new-label" : "archive-label", itemAgeLabel(item)));
    body.append(top);
    const identity = element("div", "card-source-identity");
    const meta = element("div", "card-meta");
    meta.append(element("span", "source-name", item.source_name || t("Kaynak")), element("span", "source-date", item.published_at ? formatDate(item.published_at) : t("Yayın tarihi bilinmiyor")));
    identity.append(sourceAvatar(item), meta);
    body.append(identity);
    const title = element("h3");
    const openTitle = element("button", "headline-link card-open", displayTitle(item));
    openTitle.type = "button";
    openTitle.dataset.focusKey = `headline-${item.id}`;
    openTitle.setAttribute("aria-haspopup", "dialog");
    openTitle.addEventListener("click", () => openReader(item.id, openTitle));
    title.append(openTitle);
    body.append(title);
    const summary = element("p", "card-summary", shortSourceText(itemText(item)));
    setTextLanguage(summary, item);
    body.append(summary);
    const caption = element("p", "summary-caption");
    caption.append(icon("info"), document.createTextNode(summaryCaption(item)));
    body.append(caption);
    if (displayBenefit(item)) {
      const benefit = element("div", "benefit-box");
      benefit.append(element("span", "benefit-title", t("NEDEN BİZE YARAR?")), element("p", "", shortSourceText(displayBenefit(item), 150)));
      body.append(benefit);
    }
    const snooze = renderSnooze(item);
    if (snooze) body.append(snooze);
    const sourceLine = element("div", "card-source-line");
    const read = element("button", "card-open card-reading-label", t("Kartı oku"));
    read.type = "button";
    read.dataset.focusKey = `read-${item.id}`;
    read.setAttribute("aria-haspopup", "dialog");
    read.setAttribute("aria-label", t("Kartı oku: {title}", {title: item.title || t("Gelişme")}));
    read.append(icon("arrow"));
    read.addEventListener("click", () => openReader(item.id, read));
    sourceLine.append(read, sourceLink(item.url, t("Asıl kaynak")));
    body.append(sourceLine);
    if (isRead(item)) body.append(element("p", "card-read-label", "✓ Okundu"));
    card.append(body, renderActions(item));
    return card;
  }

  function openReader(itemId, opener) {
    if (!readerDialog || !readerContent || typeof readerDialog.showModal !== "function") {
      toast(t("Okuma alanı açılamadı. Sayfayı yenileyip tekrar dene."), true);
      return;
    }
    const item = findItem(itemId);
    if (!item) return;
    readerReturnFocus = null;
    state.readerItemId = String(itemId);
    insights?.activate(item);
    readerOpener = opener || document.activeElement;
    readerOpenerKey = readerOpener?.dataset.focusKey || null;
    renderReader();
    if (!readerDialog.open) readerDialog.showModal();
    readerDialog.scrollTop = 0;
    readerContent.scrollTop = 0;
    document.getElementById("reader-close")?.focus({ preventScroll: true });
  }

  function renderReader() {
    if (!readerContent || !state.readerItemId) return;
    const item = findItem(state.readerItemId);
    if (!item) {
      if (readerDialog?.open) readerDialog.close();
      return;
    }
    const active = document.activeElement;
    const hadFocus = readerContent.contains(active);
    const focusKey = active?.dataset.focusKey;
    const scrollTop = readerDialog.scrollTop;
    const body = element("article", "reader-body");
    const header = element("header", "reader-header");
    const top = element("div", "card-topline");
    top.append(rubric(item.topic), element("span", isNew(item) ? "new-label" : "archive-label", itemAgeLabel(item)));
    const title = element("h2", "reader-title", displayTitle(item));
    title.id = "reader-title";
    const identity = element("div", "card-source-identity");
    const meta = element("div", "card-meta");
    meta.append(element("span", "source-name", item.source_name || t("Kaynak")), element("span", "source-date", item.published_at ? formatDate(item.published_at) : t("Yayın tarihi bilinmiyor")));
    identity.append(sourceAvatar(item), meta);
    header.append(top, title, identity);
    const caption = element("p", "summary-caption", summaryCaption(item));
    const summary = element("p", "reader-summary", itemText(item));
    setTextLanguage(summary, item);
    body.append(header, caption, summary, renderActions(item, "reader"));
    if (displayBenefit(item)) {
      const benefit = element("section", "reader-benefit benefit-box");
      benefit.append(element("h3", "benefit-title", t("Neden bize yarar?")), element("p", "", displayBenefit(item)));
      body.append(benefit);
    }
    const evidence = element("section", "reader-evidence");
    evidence.append(element("h3", "", t("Kaynak ve dayanak")), element("p", "evidence-label", evidenceLabel(item.evidence_type)));
    if (item.limitation) evidence.append(element("p", "reader-limitation", item.limitation));
    evidence.append(sourceLink(item.url, t("Asıl kaynağı aç")));
    body.append(evidence);
    const details = element("details", "card-details reader-details");
    const detailsTitle = element("summary", "", t("Tarihler ve kayıt bilgisi"));
    detailsTitle.dataset.focusKey = `reader-details-${item.id}`;
    details.append(detailsTitle, renderDates(item));
    if (readerContent.querySelector(".reader-details[open]")) details.open = true;
    body.append(details);
    const snooze = renderSnooze(item);
    if (snooze) body.append(snooze);
    const extra = element("details", "reader-extra");
    const extraTitle = element("summary", "", t("Okuma araçları, koleksiyon ve kişisel not"));
    extraTitle.dataset.focusKey = `reader-extra-${item.id}`;
    extra.open = Boolean(readerContent.querySelector(".reader-extra[open]"));
    extra.append(extraTitle);
    if (workspace && state.data.personal) extra.append(workspace.readerControls(item));
    if (insights && state.data.personal) extra.append(insights.mount(item));
    const noteButton = element("button", "button button-secondary", t("İsteğe bağlı kişisel not"));
    noteButton.type = "button"; noteButton.dataset.focusKey = `reader-note-${item.id}`;
    noteButton.addEventListener("click", () => { readerDialog.close(); openPersonalNote(item); });
    extra.append(noteButton); body.append(extra);
    readerContent.replaceChildren(body);
    readerDialog.scrollTop = scrollTop;
    if (hadFocus) {
      const replacement = focusKey && [...readerContent.querySelectorAll("[data-focus-key]")].find(node => node.dataset.focusKey === focusKey && !node.disabled);
      (replacement || document.getElementById("reader-close"))?.focus({ preventScroll: true });
    }
  }

  function restoreReaderFocus() {
    const returningItemId = state.readerItemId;
    insights?.close(returningItemId);
    state.readerItemId = null;
    const toastNode = document.getElementById("toast");
    if (toastNode && toastNode.parentElement !== document.body) document.body.append(toastNode);
    const replacement = readerOpenerKey && [...content.querySelectorAll("[data-focus-key]")].find(node => node.dataset.focusKey === readerOpenerKey && !node.disabled);
    const target = readerOpener?.isConnected && !readerOpener.disabled ? readerOpener : replacement || content;
    target.focus({ preventScroll: true });
    // Kayıt yanıtı kapanıştan sonra gelirse yeniden çizilen kart odağı korunur.
    readerReturnFocus = state.pending.has(`item-${returningItemId}`) ? {
      itemId: returningItemId, focusKey: readerOpenerKey, view: state.view, element: target,
    } : null;
    readerOpener = null;
    readerOpenerKey = null;
    refreshAfterMutation(true);
  }

  function keepReaderReturnFocus() {
    if (!readerReturnFocus || readerDialog?.open) return;
    if (readerReturnFocus.view !== state.view) { readerReturnFocus = null; return; }
    const active = document.activeElement;
    const replacement = readerReturnFocus.focusKey && [...content.querySelectorAll("[data-focus-key]")].find(node => node.dataset.focusKey === readerReturnFocus.focusKey && !node.disabled);
    const target = replacement || content;
    if (!active?.isConnected || active === document.body || active === document.documentElement || active === content) {
      target.focus({ preventScroll: true });
      readerReturnFocus.element = target;
    } else if (active !== target && active !== readerReturnFocus.element) {
      // Kullanıcı arama, menü veya başka bir karta geçtiyse odağını geri çekmeyiz.
      readerReturnFocus = null;
      return;
    }
    if (!state.pending.has(`item-${readerReturnFocus.itemId}`)) readerReturnFocus = null;
  }

  async function backupNow() {
    if (state.pending.has("backup") || state.data?.backup?.running) return;
    state.pending.add("backup");
    render();
    try {
      await post("/api/backup", {});
      const refreshed = await refreshAfterMutation();
      toast(refreshed ? t("Koleksiyon yedeği hazır. Kaynaklar bölümünden indirebilirsin.") : t("Yedek alındı; görünüm yenilenemedi. Yeniden dene."));
    } catch (error) {
      toast(error.message, true);
      await refreshAfterMutation();
    } finally { state.pending.delete("backup"); render(); }
  }

  function renderBackup() {
    const backup = state.data.backup;
    if (!backup) return;
    const card = element("section", "backup-card");
    const body = element("div");
    body.append(element("h3", "", t("Koleksiyon yedeği")));
    const busy = backup.running || state.pending.has("backup");
    body.append(element("p", "", busy ? t("Yedek hazırlanıyor…") : backup.last_success_at ? t("Son başarılı yedek: {date}", {date: formatDate(backup.last_success_at, true)}) : backup.available ? t("Kayıtlı yedek dosyası indirilebilir.") : t("Henüz alınmış bir yedek yok.")));
    body.append(element("p", "backup-schedule", state.data.settings?.automatic_backup ? t("Uygulama açıkken 6 saatte bir otomatik yedek alınır.") : t("Bu oturumda otomatik yedek kapalı; şimdi bir kopya alabilirsin.")));
    if (backup.last_error) body.append(element("p", "backup-error", backup.last_error));
    const actions = element("div", "backup-actions");
    const button = element("button", "button button-secondary", busy ? "Yedekleniyor…" : t("Şimdi yedekle"));
    button.type = "button";
    button.disabled = busy;
    button.dataset.focusKey = "backup-now";
    button.addEventListener("click", backupNow);
    actions.append(button);
    if (backup.available) {
      const download = element("a", "job-export", t("Yedeği indir"));
      download.href = "/api/backup/download";
      download.download = "ufuk-yedek.sqlite3";
      actions.append(download);
    }
    card.append(body, actions);
    content.append(card);
  }

  function renderSources() {
    const scan = element("button", "button button-secondary", state.data.collection?.running ? t("Kaynaklar taranıyor…") : t("Kaynakları tara"));
    scan.type = "button"; scan.dataset.focusKey = "source-collect";
    scan.disabled = Boolean(state.data.collection?.running) || state.pending.has("collect");
    scan.addEventListener("click", () => collect());
    content.append(scan);
    renderBackup();
    if (workspace && state.data.personal) content.append(workspace.sourceToolbar());
    const allSources = state.data.sources;
    const overview = element("div", "catalog-overview");
    for (const [value, label] of [[allSources.length, "kaynak"], [new Set(allSources.map(source => source.publisher || source.name)).size, t("yayıncı")], [new Set(allSources.flatMap(source => source.branches || [])).size, "alt dal"]]) {
      const metric = element("div");
      metric.append(element("strong", "", value), element("span", "", label));
      overview.append(metric);
    }
    content.append(overview);
    const exportLink = element("a", "job-export", t("Kaynak listesini indir"));
    exportLink.href = "/api/sources/export";
    exportLink.download = "ufuk-kaynak-kutuphanesi.json";
    content.append(exportLink);
    const filters = element("div", "catalog-filters");
    const choices = [
      ["sourceBranch", t("Alt dal"), t("Bütün dallar"), [...new Set(allSources.flatMap(source => source.branches || []))].sort((a, b) => a.localeCompare(b, "tr"))],
      ["sourceLanguage", t("Kaynak dili"), t("Bütün diller"), [...new Set(allSources.map(source => source.language))].filter(Boolean).sort()],
      ["sourceKind", t("Kaynak türü"), t("Bütün türler"), [...new Set(allSources.map(source => source.source_type))].filter(Boolean).sort((a, b) => a.localeCompare(b, "tr"))],
      ["sourceHealth", t("Erişim durumu"), t("Bütün durumlar"), [t("Sağlıklı"), t("Kontrol gerekli"), t("Henüz taranmadı"), t("Kapalı")]],
    ];
    for (const [key, label, emptyLabel, values] of choices) {
      const control = element("label", "catalog-control", label);
      const select = element("select");
      select.dataset.focusKey = `catalog-${key}`;
      select.setAttribute("aria-label", label);
      select.append(new Option(emptyLabel, ""));
      for (const value of values) select.append(new Option(value === "tr" ? t("Türkçe") : value === "en" ? t("İngilizce") : value, value));
      select.value = state[key];
      select.addEventListener("change", () => { state[key] = select.value; render(); });
      control.append(select);
      filters.append(control);
    }
    content.append(filters);
    const sources = allSources.filter(source => matches(source)
      && (!state.sourceBranch || (source.branches || []).includes(state.sourceBranch))
      && (!state.sourceLanguage || source.language === state.sourceLanguage)
      && (!state.sourceKind || source.source_type === state.sourceKind)
      && (!state.sourceHealth || sourceHealth(source) === state.sourceHealth))
      .sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id, "tr"));
    content.append(element("p", "results-note catalog-result-count", t("{count} / {total} kaynak gösteriliyor. Başlık, yayıncı veya alt dal adıyla arayabilirsin.", {count: sources.length, total: allSources.length})));
    if (state.data.collection?.remaining_due > 0) content.append(element("p", "info-note", t("{due} kaynağın kontrol zamanı geldi. Her turda konulara dağılan en fazla {limit} kaynak okunur; sıradakiler sonraki turda devam eder.", {due: state.data.collection.remaining_due, limit: state.data.settings?.scan_batch_limit || 12})));
    if (!sources.length) {
      content.append(emptyState(t("Bu görünümde kaynak yok"), t("Başka bir konu seçebilir veya aramayı temizleyebilirsin."), t("Filtreleri temizle"), resetFilters, "globe"));
      return;
    }
    const failed = sources.filter(source => source.last_error && source.enabled !== false).length;
    if (failed) {
      const note = element("div", "info-note source-health-note");
      note.append(icon("info"), element("p", "", t("{count} kaynakta son kontrol tamamlanamadı. Daha önce alınan kayıtlar havuzda korunuyor; ayrıntıyı ilgili kaynak kartında görebilirsin.", {count: failed})));
      content.append(note);
    }
    const grid = element("div", "sources-grid");
    for (const source of sources) {
      const card = element("article", "source-card");
      const top = element("div", "card-topline");
      let status = t("Henüz taranmadı");
      let statusClass = "health-unknown";
      if (source.enabled === false) status = t("Kapalı");
      else if (Number(source.failures) >= 3) { status = t("Kontrol durakladı"); statusClass = "health-warning"; }
      else if (source.last_error) { status = t("Kontrol gerekli"); statusClass = "health-warning"; }
      else if (source.last_success_at) { status = t("Sağlıklı"); statusClass = "health-healthy"; }
      const health = element("span", `source-health ${statusClass}`);
      health.append(element("span", "health-dot"), document.createTextNode(status));
      top.append(rubric(source.topic), health);
      card.append(top, element("h3", "", source.name || source.id), sourceLink(source.url || source.fetch_url, t("Kaynağı aç")));
      if (source.benefit) card.append(element("p", "source-purpose", source.benefit));
      const scope = source.collection_scope || (typeof source.quality_notes === "string" ? source.quality_notes : "");
      if (scope) card.append(element("p", "source-scope", scope));
      const branches = element("div", "source-branches");
      for (const branch of source.branches || []) branches.append(element("span", "", branch));
      card.append(branches);
      const info = element("dl");
      const kinds = { rss: "RSS", atom: "Atom", github: t("GitHub sürümleri"), github_release: t("GitHub sürümleri"), github_releases: t("GitHub sürümleri"), html: t("Web sayfası"), page: t("Web sayfası"), project: t("Proje sayfası"), arxiv: "arXiv", openalex: "OpenAlex", crossref: "Crossref", discovery: t("Konu sorgusu ve kaynakça") };
      detailRow(info, t("Toplama yöntemi"), kinds[source.kind] || t("Kaynak akışı"));
      detailRow(info, t("Yayıncı"), source.publisher || source.name);
      detailRow(info, t("Kaynak türü"), source.source_type || t("Belirtilmemiş"));
      detailRow(info, t("Dil"), source.language === "tr" ? t("Türkçe") : source.language === "en" ? t("İngilizce") : source.language || t("Belirtilmemiş"));
      detailRow(info, t("Kontrol sıklığı"), source.interval_hours ? t("{hours} saatte bir", {hours: source.interval_hours}) : t("Belirtilmemiş"));
      detailRow(info, t("Son kontrol"), formatDate(source.last_checked_at, true));
      detailRow(info, t("Son başarılı kontrol"), formatDate(source.last_success_at, true));
      detailRow(info, t("Sonraki kontrol"), source.enabled === false ? t("Kapalı") : Number(source.failures) >= 3 ? t("Kaynak için teknik inceleme gerekiyor") : source.next_due_at ? formatDate(source.next_due_at, true) : t("İlk taramada"));
      card.append(info);
      const pagination = (state.data.collection?.pagination || []).find(row => row.source_id === source.id);
      if (pagination?.next_url || pagination?.truncated) card.append(element("p", "source-scope", pagination.next_url ? t("Geçmiş taramasının devamı kayıtlı. Sonraki kontrolde kaldığı sayfadan sürer.") : t("Kaynak kayıt sınırına ulaştı; bütün geçmişin toplandığı söylenemez.")));
      const maintenance = (state.data.collection?.maintenance || []).find(row => row.source_id === source.id);
      if (maintenance) {
        const note = element("p", maintenance.archived ? "inline-error" : "source-scope", maintenance.archived ? t("Proje arşivlenmiş. Yöntem kaynağı olarak incelenebilir; yeni otomatik geliştirme adayı olarak seçilmez.") : maintenance.disabled ? t("Proje deposu devre dışı; kullanılabilirliği ayrıca değerlendirilmelidir.") : maintenance.status === "ok" ? t("Depo son kontrolde arşivlenmiş değildi.") : t("Projenin güncel bakım durumu doğrulanamadı."));
        card.append(note, element("p", "workspace-help", t("Bakım kontrolü: {date}", {date: formatDate(maintenance.checked_at, true)}) + (maintenance.pushed_at ? t(" · Son depo güncellemesi: {date}", {date: formatDate(maintenance.pushed_at)}) : "")));
        if (maintenance.error) card.append(element("p", "source-scope", maintenance.error));
      }
      if (source.last_error) {
        const error = element("details", "source-error");
        const summary = element("summary", "", t("Son kontrol neden tamamlanamadı?"));
        error.append(summary, element("p", "", source.last_error));
        card.append(error);
      }
      const footer = element("div", "source-footer");
      const records = element("button", "source-records", t("{count} kaydı gör", {count: Number(source.item_count) || 0}));
      records.type = "button";
      records.disabled = !Number(source.item_count);
      records.dataset.focusKey = `source-records-${source.id}`;
      records.setAttribute("aria-label", t("{name}: kayıtları gör", {name: source.name}));
      records.addEventListener("click", () => {
        state.sourceId = source.id;
        state.topic = "all";
        state.search = "";
        state.showArchive = true;
        document.getElementById("search").value = "";
        document.getElementById("show-archive").checked = true;
        changeView("all");
        document.getElementById("view-title").focus({ preventScroll: true });
      });
      footer.append(records);
      const refresh = element("button", "source-refresh");
      refresh.type = "button";
      const isDiscovery = source.kind === "discovery";
      refresh.disabled = Boolean(isDiscovery ? state.data.discovery?.running : state.data.collection?.running) || state.pending.has("collect") || source.enabled === false || (!isDiscovery && Number(source.failures) >= 3);
      refresh.append(icon("refresh"), document.createTextNode(isDiscovery ? t("Konuyu keşfet") : t("Vadesi geldiyse tara")));
      refresh.setAttribute("aria-label", `${source.name || source.id}: ${isDiscovery ? t("konuyu keşfet") : t("vadesi geldiyse tara")}`);
      refresh.addEventListener("click", () => isDiscovery ? discovery?.start(source.topic) : collect(source.id));
      footer.append(refresh);
      card.append(footer);
      if (workspace && state.data.personal) card.append(workspace.sourceControls(source));
      grid.append(card);
    }
    content.append(grid);
  }

  function sourceHealth(source) {
    if (source.enabled === false) return t("Kapalı");
    if (source.last_error || Number(source.failures) >= 3) return t("Kontrol gerekli");
    return source.last_success_at ? t("Sağlıklı") : t("Henüz taranmadı");
  }

  function jobExportLink(jobId) {
    const link = element("a", "job-export", t("İnceleme dosyasını indir"));
    link.href = `/api/jobs/${encodeURIComponent(jobId)}/export`;
    link.download = "ufuk-inceleme.txt";
    return link;
  }

  function jobDirty() {
    return Boolean(jobSession?.data && jobSession.draft !== jobSession.data.note);
  }

  function showJobMessage(message, isError = false) {
    if (!jobSession) return;
    jobSession.message = message;
    jobSession.messageError = isError;
    const node = document.getElementById("job-message");
    if (node) {
      node.textContent = message;
      node.hidden = !message;
      node.classList.toggle("is-error", isError);
    }
  }

  function validJob(data) {
    return data && typeof data.note === "string" && Number.isSafeInteger(data.version)
      && Number.isSafeInteger(data.revision) && Array.isArray(data.notes);
  }

  async function openJob(jobId, opener) {
    if (!jobDialog || !jobContent || jobDialog.open) return;
    const session = { id: jobId, data: null, draft: "", opener, openerKey: opener?.dataset.focusKey,
      saving: false, loading: true, conflict: false, latest: null, message: "", messageError: false };
    jobSession = session;
    renderJob();
    jobDialog.showModal();
    document.getElementById("job-close")?.focus({ preventScroll: true });
    try {
      const data = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (!validJob(data)) throw new Error(t("İnceleme kaydı beklenen biçimde gelmedi. Kapatıp yeniden dene."));
      if (jobSession !== session) return;
      session.data = data;
      session.draft = data.note;
    } catch (error) {
      if (jobSession !== session) return;
      session.message = error.message;
      session.messageError = true;
    } finally {
      if (jobSession === session) { session.loading = false; renderJob(); }
    }
  }

  function requestJobClose() {
    if (!jobSession) return;
    if (jobSession.saving) {
      showJobMessage(t("Kayıt sürüyor. Sonuç gelince bu alanı kapatabilirsin."));
      return;
    }
    if (jobDirty()) {
      const warning = document.getElementById("job-unsaved");
      if (warning) { warning.hidden = false; warning.querySelector("button")?.focus(); }
      return;
    }
    jobDialog.close();
  }

  function restoreJobFocus() {
    const session = jobSession;
    jobSession = null;
    const toastNode = document.getElementById("toast");
    if (toastNode && toastNode.parentElement === jobDialog) document.body.append(toastNode);
    const replacement = session?.openerKey && [...content.querySelectorAll("[data-focus-key]")].find(node => node.dataset.focusKey === session.openerKey);
    const target = session?.opener?.isConnected ? session.opener : replacement || content;
    target.focus({ preventScroll: true });
  }

  async function compareLatestJob() {
    const session = jobSession;
    if (!session || session.loading || session.saving) return;
    session.loading = true;
    renderJob();
    try {
      const data = await request(`/api/jobs/${encodeURIComponent(session.id)}`);
      if (!validJob(data)) throw new Error(t("Güncel inceleme kaydı okunamadı. Taslağın korunuyor."));
      if (jobSession !== session) return;
      session.latest = data;
      session.message = t("Kayıtlı son not aşağıda. Kendi taslağın değişmedi; karşılaştırdıktan sonra devam edebilirsin.");
      session.messageError = false;
    } catch (error) {
      if (jobSession !== session) return;
      session.message = error.message;
      session.messageError = true;
    } finally {
      if (jobSession === session) { session.loading = false; renderJob(); }
    }
  }

  async function saveJob(status) {
    const session = jobSession;
    if (!session?.data || session.saving || session.loading || session.conflict) return;
    const textarea = document.getElementById("job-note");
    if (status === "tamamlandi" && Array.from(session.draft.trim()).length < 20) {
      textarea.setCustomValidity(t("İncelemeyi tamamlamak için en az 20 karakterlik bir sonuç notu yaz."));
      textarea.reportValidity();
      return;
    }
    session.saving = true;
    session.message = t("Notun kaydediliyor…");
    session.messageError = false;
    renderJob();
    try {
      const result = await post(`/api/jobs/${encodeURIComponent(session.id)}/note`, {
        note: session.draft, status, expected_version: session.data.version, source_revision: session.data.revision,
      });
      if (!validJob(result)) {
        session.conflict = true;
        throw new Error(t("Kayıt yanıtı doğrulanamadı. Taslağın korunuyor; güncel kaydı karşılaştır."));
      }
      if (jobSession !== session) return;
      session.data = result;
      session.draft = result.note;
      session.latest = null;
      session.message = status === "tamamlandi" ? t("Kişisel incelemen tamamlandı. Notun ve önceki sürümleri saklandı.") : status === "iptal" ? t("Bu incelemeden vazgeçildi. Notun saklandı; istediğinde yeniden açabilirsin.") : t("Notun kaydedildi. İnceleme beklemede.");
      await refreshAfterMutation();
    } catch (error) {
      if (jobSession !== session) return;
      session.message = error.status === 409 ? t("Bu kayıt ya da kaynak sen not yazarken değişmiş. Taslağın korunuyor. Güncel kaydı karşılaştırarak devam et.") : error.message;
      session.messageError = true;
      if (error.status === 409) { session.conflict = true; session.latest = null; }
    } finally {
      if (jobSession === session) { session.saving = false; renderJob(); }
    }
  }

  function renderJob() {
    const session = jobSession;
    if (!session) return;
    const scrollTop = jobDialog.scrollTop;
    const body = element("section", "reader-body job-body");
    const title = element("h2", "reader-title", session.data?.title || t("İnceleme notu"));
    title.id = "job-title";
    body.append(element("p", "eyebrow", t("KİŞİSEL İNCELEMEN")), title);
    const message = element("p", `job-message${session.messageError ? " is-error" : ""}`, session.message);
    message.id = "job-message";
    message.hidden = !session.message;
    message.setAttribute("role", "status");
    body.append(message);
    if (!session.data) {
      if (session.loading) body.append(element("p", "loading-state", t("İnceleme kaydı açılıyor…")));
      jobContent.replaceChildren(body);
      return;
    }
    const data = session.data;
    const busy = session.saving || session.loading;
    const statusLabels = { bekliyor: t("İncelemeni bekliyor"), tamamlandi: t("Kişisel inceleme tamamlandı"), iptal: t("Vazgeçildi") };
    const meta = element("div", "job-detail-meta");
    meta.append(element("span", "job-status", statusLabels[data.status] || t("İnceleme")), element("span", "", t("Not sürümü {version} · Kaynak revizyonu {revision}", {version: data.version, revision: data.revision})));
    body.append(meta, sourceLink(data.url, t("Asıl kaynağı aç")));
    if (data.source_changed) body.append(element("p", "job-source-warning", t("Kaynak, son inceleme notundan sonra değişmiş. Asıl kaynağı yeniden açıp notunu gözden geçir.")));
    const form = element("form", "job-form");
    const label = element("label", "job-note-label", t("Ne öğrendin, nasıl kullanacaksın?"));
    label.htmlFor = "job-note";
    const help = element("p", "job-note-help", t("Bulguyu, bizim için yararını ve sonraki adımı yaz. Tamamlamak için en az 20 karakterlik bir sonuç notu gerekir."));
    help.id = "job-note-help";
    const textarea = element("textarea", "job-note");
    textarea.id = "job-note";
    textarea.name = "note";
    textarea.maxLength = 2000;
    textarea.rows = 8;
    textarea.value = session.draft;
    textarea.placeholder = t("Örneğin: Kaynaktaki özelliği inceledim. Yerel denemede şu koşulu kontrol edeceğim…");
    textarea.disabled = busy;
    textarea.setAttribute("aria-describedby", "job-note-help job-note-count");
    const count = element("span", "job-note-count", `${Array.from(session.draft).length} / 2000`);
    count.id = "job-note-count";
    textarea.addEventListener("input", () => {
      session.draft = textarea.value;
      textarea.setCustomValidity("");
      count.textContent = `${Array.from(session.draft).length} / 2000`;
      const warning = document.getElementById("job-unsaved");
      if (warning) warning.hidden = true;
    });
    form.append(label, help, textarea, count);
    form.addEventListener("submit", event => { event.preventDefault(); saveJob(data.status); });
    const actions = element("div", "job-form-actions");
    const save = element("button", "button button-primary", session.saving ? t("Kaydediliyor…") : t("Notu kaydet"));
    save.type = "submit";
    save.disabled = busy || session.conflict;
    actions.append(save);
    const decisions = data.status === "bekliyor" ? [["tamamlandi", t("İncelemeyi tamamla")], ["iptal", t("Seçimden vazgeç")]] : [["bekliyor", t("Yeniden aç")]];
    for (const [status, labelText] of decisions) {
      const button = element("button", "button job-secondary", labelText);
      button.type = "button";
      button.disabled = busy || session.conflict;
      button.addEventListener("click", () => saveJob(status));
      actions.append(button);
    }
    form.append(actions);
    body.append(form);
    if (session.conflict) {
      const conflict = element("section", "job-conflict");
      conflict.append(element("h3", "", t("Taslağın korunuyor")));
      if (session.latest) {
        const latestNote = session.latest.notes.find(note => note.version === session.latest.version);
        const noteRevision = latestNote ? t("Notun kaynak revizyonu {revision}", {revision: latestNote.source_revision}) : t("Henüz kayıtlı not yok");
        conflict.append(element("p", "", t("Kayıtlı son not · sürüm {version} · {note} · Güncel kaynak revizyonu {revision}", {version: session.latest.version, note: noteRevision, revision: session.latest.revision})));
        if (session.latest.source_changed) conflict.append(element("p", "job-source-warning", t("Kaynak, kayıtlı nottan sonra değişmiş. Notun yeni kaynak içeriğini değerlendirdiği varsayılmaz; kaynağı yeniden gözden geçir.")));
        conflict.append(element("p", "job-result", session.latest.note || t("Kayıtlı not henüz yok.")));
        const accept = element("button", "button job-secondary", t("Kendi taslağımla devam et"));
        accept.type = "button";
        accept.addEventListener("click", () => {
          session.data = session.latest;
          session.latest = null;
          session.conflict = false;
          session.message = t("Taslağın korunuyor. Kaydettiğinde güncel kaydın ardından yeni bir sürüm oluşacak.");
          session.messageError = false;
          renderJob();
          document.getElementById("job-note")?.focus();
        });
        conflict.append(accept);
      } else {
        conflict.append(element("p", "", t("Son kaydı yüklemek taslağını değiştirmez. İki metni karşılaştırıp devam edebilirsin.")));
        const reload = element("button", "button job-secondary", session.loading ? t("Yükleniyor…") : t("Güncel kaydı karşılaştır"));
        reload.type = "button";
        reload.disabled = busy;
        reload.addEventListener("click", compareLatestJob);
        conflict.append(reload);
      }
      body.append(conflict);
    }
    const unsaved = element("div", "job-unsaved");
    unsaved.id = "job-unsaved";
    unsaved.hidden = true;
    unsaved.setAttribute("role", "alert");
    unsaved.append(element("p", "", t("Kaydedilmemiş bir notun var. Çıkarsan bu taslak kaybolacak.")));
    const keep = element("button", "button job-secondary", t("Yazmaya devam et"));
    keep.type = "button";
    keep.addEventListener("click", () => { unsaved.hidden = true; textarea.focus(); });
    const discard = element("button", "button job-discard", t("Kaydetmeden çık"));
    discard.type = "button";
    discard.addEventListener("click", () => jobDialog.close());
    unsaved.append(keep, discard);
    body.append(unsaved);
    const history = element("details", "job-history");
    history.append(element("summary", "", t("Not geçmişi ({count})", {count: data.notes.length})));
    for (const note of [...data.notes].sort((a, b) => b.version - a.version)) {
      const entry = element("article");
      entry.append(element("h3", "", t("Sürüm {version} · {date}", {version: note.version, date: formatDate(note.created_at, true)})), element("p", "job-history-meta", t("Kaynak revizyonu {revision}", {revision: note.source_revision})), element("p", "job-result", note.body || t("Not boş bırakıldı.")));
      history.append(entry);
    }
    body.append(history, jobExportLink(session.id));
    jobContent.replaceChildren(body);
    jobDialog.scrollTop = scrollTop;
  }

  function renderResearch() {
    const note = element("div", "info-note");
    note.append(icon("info"), element("p", "", t("Önceki kişisel notların burada korunur. Yeni araştırma raporlarını UFUK hazırlar; bu defteri kullanmak isteğe bağlıdır.")));
    content.append(note);
    const jobs = state.data.jobs.filter(job => {
      const item = state.data.items.find(entry => String(entry.id) === String(job.item_id));
      return matches({ ...item, ...job, title: item?.title || job.title, topic: item?.topic || job.topic });
    });
    if (!jobs.length) {
      content.append(emptyState(state.data.jobs.length ? t("Bu filtreye uyan araştırma yok") : t("Bir sonraki iyi fikre yer ayır"), state.data.jobs.length ? t("Aramayı veya konu filtresini değiştirerek diğer seçimlerini görebilirsin.") : t("Bir raporun ayrıntılarından kendi notunu ekleyebilirsin. Araştırma ve karar akışında not yazman gerekmez."), state.data.jobs.length ? t("Filtreleri temizle") : t("Seçkiye dön"), state.data.jobs.length ? resetFilters : () => changeView("selection"), "bookmark"));
      return;
    }
    const list = element("div", "jobs-list");
    const statuses = { queued: [t("Kuyrukta"), ""], pending: [t("Bekliyor"), ""], bekliyor: [t("İncelemeni bekliyor"), ""], tamamlandi: [t("Kişisel inceleme tamamlandı"), "completed"], iptal: [t("Vazgeçildi"), "cancelled"], selected: [t("Seçildi"), ""], running: [t("Araştırılıyor"), "running"], in_progress: [t("Araştırılıyor"), "running"], completed: [t("Tamamlandı"), "completed"], done: [t("Tamamlandı"), "completed"], bitti: [t("Tamamlandı"), "completed"], failed: [t("İnceleme gerekli"), "failed"], blocked: [t("Engel var"), "failed"] };
    for (const job of jobs) {
      const item = state.data.items.find(entry => String(entry.id) === String(job.item_id));
      const card = element("article", "job-card");
      const image = element("div", "job-icon");
      image.append(icon("bookmark"));
      const body = element("div");
      const title = element("h3");
      const titleText = item?.title || job.title || t("Araştırma seçimi");
      if (item) {
        const openTitle = element("button", "headline-link card-open", titleText);
        openTitle.type = "button";
        openTitle.dataset.focusKey = `job-${job.id || item.id}`;
        openTitle.setAttribute("aria-haspopup", "dialog");
        openTitle.addEventListener("click", () => openReader(item.id, openTitle));
        title.append(openTitle);
      } else title.textContent = titleText;
      body.append(title);
      const meta = element("div", "job-meta");
      meta.append(element("span", "", t("{date} tarihinde seçildi", {date: formatDate(job.created_at)})), element("span", "", t("Kişisel inceleme")));
      body.append(meta);
      if (item?.url) body.append(sourceLink(item.url, item.source_name || t("Asıl kaynak")));
      if (typeof job.result === "string" && job.result) {
        const characters = Array.from(job.result);
        body.append(element("p", "job-result", characters.length > 300 ? `${characters.slice(0, 300).join("")}…` : job.result));
      }
      const actions = element("div", "job-card-actions");
      const edit = element("button", "button button-primary job-edit", t("İnceleme notu"));
      edit.type = "button";
      edit.dataset.focusKey = `job-note-${job.id}`;
      edit.setAttribute("aria-haspopup", "dialog");
      edit.addEventListener("click", () => openJob(job.id, edit));
      actions.append(edit, jobExportLink(job.id));
      body.append(actions);
      const [label, className] = statuses[job.status] || [t("Durum doğrulanamadı"), ""];
      card.append(image, body, element("span", `job-status ${className}`, label));
      list.append(card);
    }
    content.append(list);
  }

  function renderPool() {
    if (workspace && state.data.personal) content.append(workspace.poolControls());
    preparePool();
    content.setAttribute("aria-busy", String(pool.loading));
    if (!pool.items.length) {
      if (pool.loading) {
        const loading = element("div", "loading-state");
        loading.setAttribute("role", "status");
        loading.append(element("span", "loading-dot"), document.createTextNode(t("Koleksiyonun tamamında aranıyor…")));
        content.append(loading);
      } else if (pool.error) content.append(emptyState(t("Havuz sayfası alınamadı"), pool.error, t("Yeniden dene"), () => loadPool(), "globe"));
      else if (state.search || state.topic !== "all" || state.sourceId) content.append(emptyState(t("Bu filtreye uyan gelişme yok"), t("Arama koleksiyonun tamamında yapıldı. Aramayı kısaltabilir, başka bir konu veya kaynak seçebilirsin."), t("Filtreleri temizle"), resetFilters, "search"));
      else if (!state.showArchive) content.append(emptyState(t("Arşiv dışında gelişme yok"), t("İlk aktarım arşiv olarak tutulur. Önceki kayıtları görmek için arşivi dahil edebilirsin."), t("Arşivi göster"), () => { state.showArchive = true; document.getElementById("show-archive").checked = true; render(); }));
      else content.append(emptyState(t("Havuz henüz boş"), t("İlk kaynak taramasından sonra gelişmeler burada görünecek."), state.data.collection?.running ? null : t("Kaynakları tara"), () => collect(), "newspaper"));
      return;
    }
    const grid = element("div", "news-grid");
    for (const item of pool.items) grid.append(renderCard(findItem(item.id) || item));
    content.append(grid);
    const footer = element("div", "pool-pagination");
    footer.append(element("p", "results-note", t("{count} / {total} gelişme gösteriliyor · Arama tüm koleksiyonda", {count: pool.items.length, total: pool.total})));
    if (pool.error) {
      const error = element("p", "pool-page-error", t("Sonraki sayfa alınamadı: {error}", {error: pool.error}));
      error.setAttribute("role", "alert");
      footer.append(error);
    }
    if (pool.hasMore || pool.error) {
      const more = element("button", "button button-secondary", pool.loading ? t("Yükleniyor…") : pool.error ? t("Sonraki sayfayı yeniden dene") : t("Daha fazla göster"));
      more.type = "button";
      more.disabled = pool.loading;
      more.dataset.focusKey = "pool-load-more";
      more.addEventListener("click", () => loadPool(true));
      footer.append(more);
    }
    content.append(footer);
  }

  function render() {
    if (!state.data) return;
    document.body.dataset.view = state.view;
    const focused = document.activeElement;
    const focusedInsideContent = content.contains(focused);
    const focusKey = focused?.dataset.focusKey;
    updateHeader();
    content.setAttribute("aria-busy", "false");
    content.setAttribute("aria-labelledby", `tab-${state.view}`);
    content.dataset.layout = state.layout;
    const hasCards = !["sources", "research", "decisions", "development", "legacy-notes", "collections", "settings"].includes(state.view);
    for (const layout of ["grid", "list"]) {
      const button = document.getElementById(`view-${layout}`);
      if (button) {
        button.setAttribute("aria-pressed", String(state.layout === layout));
        button.disabled = !hasCards;
      }
    }
    const clearButton = document.getElementById("clear-filters");
    if (clearButton) clearButton.disabled = !state.search && state.topic === "all" && !(state.view === "all" && (state.sourceId || state.collectionId || state.readStatus !== "all" || state.sort !== "relevance"))
      && !(state.view === "sources" && (state.sourceBranch || state.sourceLanguage || state.sourceKind || state.sourceHealth));
    for (const tab of document.querySelectorAll(".view-tab")) {
      const active = tab.dataset.view === state.view;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    }
    for (const button of document.querySelectorAll(".topic-button")) {
      const active = button.dataset.topic === state.topic;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    }
    for (const group of document.querySelectorAll(".view-tabs")) {
      if (!group.querySelector('[aria-selected="true"]')) {
        const first = group.querySelector(".view-tab");
        if (first) first.tabIndex = 0;
      }
    }
    document.getElementById("archive-control").hidden = state.view !== "all";
    const sourceControl = document.getElementById("source-filter-control");
    sourceControl.hidden = state.view !== "all";
    if (state.view === "all") {
      const select = document.getElementById("source-filter");
      const signature = JSON.stringify(state.data.sources.map(source => [source.id, source.name]));
      if (select.dataset.catalog !== signature) {
        select.replaceChildren(new Option(t("Bütün kaynaklar"), ""));
        for (const source of [...state.data.sources].sort((a, b) => a.name.localeCompare(b.name, "tr"))) select.append(new Option(source.name, source.id));
        select.dataset.catalog = signature;
      }
      select.value = state.sourceId;
    }
    content.replaceChildren();
    const dailyLimit = state.data.personal?.settings?.daily_limit || 8;
    const viewTitles = { selection: t("Önemli gelişmeler"), all: t("Bütün gelişmeler"), saved: t("Kaydettiklerin"), later: t("Sonra bakacakların"), research: t("Araştırılıyor"), decisions: t("Kararını bekleyenler"), development: t("Geliştirmeler"), "legacy-notes": t("Kişisel notların"), sources: t("Kaynak kütüphanesi"), collections: t("Koleksiyonların"), settings: t("İlgi ve ayarlar") };
    const viewDescriptions = { selection: t("Hazır Türkçe değerlendirmelerden en fazla {count} okunmamış gelişme. İlgini çekeni derin araştırmaya gönder.", {count: dailyLimit}), all: t("Türkçe değerlendirmesi hazırlanmamış kayıtlar dahil bütün kaynak havuzu."), saved: t("Yararlı bulduğun gelişmeler bir arada."), later: t("Yedi gün sonraya ayırdığın gelişmeler."), research: t("Kaynaklar okunur, mevcut düzeninle uygunluk değerlendirilir ve rapor hazırlanır. Not yazman gerekmez."), decisions: t("Raporu oku; geliştirmeye al, izlemeye al veya pas geç."), development: t("Seçtiğin fikirlerin deneme planları. Devir dosyasını Codex veya Claude ile uygulamak üzere indir."), "legacy-notes": t("İsteğe bağlı kişisel defterin ve önceki notların korunur."), sources: t("Yayıncılar, alt dallar, toplanan kayıtlar ve son erişim durumu."), collections: t("Fikirlerini projelere göre topla. Kayıtlı aramalarına dön."), settings: t("Günlük hazırlığı, ilgi alanlarını ve bağlantını düzenle.") };
    let title = viewTitles[state.view];
    let description = viewDescriptions[state.view];
    if (state.view === "sources") { sourceLearning?.render(content); discovery?.renderDiscovery(content); renderSources(); }
    else if (state.view === "collections" && workspace) workspace.renderCollections(content);
    else if (state.view === "settings" && workspace) { if (workflow) content.append(workflow.settingsPanel()); workspace.renderSettings(content); }
    else if (["research", "decisions", "development"].includes(state.view) && workflow) { if (state.view === "research") discovery?.renderRequests(content); workflow.renderView(state.view, content); }
    else if (state.view === "legacy-notes") renderResearch();
    else if (state.view === "all") renderPool();
    else {
      let items = state.data.items.filter(matches);
      if (state.view === "selection") { items = selectedItems(items); if (workflow) content.append(workflow.statusStrip()); }
      else if (state.view === "saved") items = items.filter(item => feedback(item) === "yararli");
      else if (state.view === "later") items = items.filter(snoozed);
      if (state.view === "selection" && items.length && items.every(item => item.is_archive)) {
        description += t(" Başlangıç havuzundaki eski yayınlar arşiv etiketiyle gösterilir.");
      }
      if (items.length) {
        const grid = element("div", `news-grid ${state.view === "selection" ? "selection-grid" : ""}`);
        for (const item of items) grid.append(renderCard(item));
        content.append(grid);
        content.append(element("p", "results-note", state.view === "selection" ? t("{count} gelişmelik seçki · Daha fazlası tüm havuzda", {count: items.length}) : t("{count} gelişme gösteriliyor", {count: items.length})));
      } else if (state.search || state.topic !== "all") content.append(emptyState(t("Bu filtreye uyan gelişme yok"), t("Aramayı kısaltabilir veya başka bir konuya göz atabilirsin."), t("Filtreleri temizle"), resetFilters, "search"));
      else if (state.view === "saved") content.append(emptyState(t("İyi fikirlerini burada biriktir"), t("Bir gelişmeyi “Yararlı” olarak işaretlediğinde burada bulabilirsin."), t("Seçkiye göz at"), () => changeView("selection"), "bookmark"));
      else if (state.view === "later") content.append(emptyState(t("Sonraya ayırdığın bir gelişme yok"), t("İlgini çeken ama bugün zamanı olmayan bir kartı “Sonra” ile 7 günlüğüne buraya bırakabilirsin."), t("Seçkiye dön"), () => changeView("selection"), "clock"));
      else if (state.data.items.length) content.append(emptyState(t("Türkçe seçkin hazırlanıyor"), t("Hazır ve güncel değerlendirmeler burada görünür. Okuduğun ya da sonraya ayırdığın kayıtlar tüm havuzda korunur."), t("Araştırma durumunu gör"), () => changeView("research"), "clock"));
      else content.append(emptyState(t("İlk seçkini hazırlayalım"), t("Kaynaklar toplansın, işe yarayabilecek gelişmeler Türkçe değerlendirilsin."), state.data.workflow?.status?.running ? null : t("Günü hazırla"), () => workflow?.run(), "newspaper"));
    }
    const total = Number(state.data.stats?.total) || state.data.items.length;
    if (state.view === "selection" && total > state.data.items.length) description += t(" Seçki {count} kayıt arasından hazırlanır; bütün koleksiyonda aramak için Tüm gelişmeler’i aç.", {count: state.data.items.length});
    document.getElementById("view-title").textContent = title;
    document.getElementById("view-description").textContent = description;
    document.querySelector(".header-location").textContent = t("Çalışma alanın / {view}", {view: viewTitles[state.view]});
    if (focusedInsideContent && !document.contains(focused)) {
      const replacement = focusKey && [...content.querySelectorAll("[data-focus-key]")].find(node => node.dataset.focusKey === focusKey && !node.disabled);
      (replacement || content).focus({ preventScroll: true });
    }
    renderReader();
    keepReaderReturnFocus();
    workflow?.sync();
  }

  async function setFeedback(item, value, offerUndo = true) {
    const key = `item-${item.id}`;
    if (state.pending.has(key)) return;
    state.pending.add(key);
    const previous = feedback(item) || "yok";
    render();
    try {
      await post(`/api/items/${encodeURIComponent(item.id)}/feedback`, { value });
      invalidatePool();
      const messages = { yararli: t("Yararlı olarak kaydedildi."), gereksiz: t("Seçkiden çıkarıldı. Kayıt tüm havuzda duruyor."), sonra: t("7 gün sonraya ayrıldı."), yok: t("Seçimin kaldırıldı.") };
      const refreshed = await refreshAfterMutation();
      const undo = offerUndo && previous !== value ? {
        label: t("Geri al"),
        run: () => setFeedback(findItem(item.id) || item, previous, false),
      } : null;
      toast(refreshed ? messages[value] : t("Seçimin kaydedildi; görünüm yenilenemedi. Yeniden dene."), false, undo);
    } catch (error) { toast(error.message, true); }
    finally { state.pending.delete(key); render(); }
  }

  async function openPersonalNote(item) {
    const existing = state.data.jobs.find(job => String(job.item_id) === String(item.id));
    if (existing) { openJob(existing.id, document.activeElement); return; }
    const key = `item-${item.id}`;
    if (state.pending.has(key)) return;
    state.pending.add(key);
    render();
    try {
      await post(`/api/items/${encodeURIComponent(item.id)}/research`, {});
      invalidatePool();
      const refreshed = await refreshAfterMutation();
      const job = state.data.jobs.find(entry => String(entry.item_id) === String(item.id));
      if (job) openJob(job.id, document.activeElement);
      else toast(refreshed ? t("Kişisel not kaydı oluşturuldu.") : t("Kişisel not kaydı oluşturuldu; görünüm yenilenemedi."));
    } catch (error) { toast(error.message, true); }
    finally { state.pending.delete(key); render(); }
  }

  async function collect(sourceId) {
    if (!state.data || state.pending.has("collect") || state.data.collection?.running) return;
    state.pending.add("collect");
    render();
    try {
      await post("/api/collect", sourceId ? { source_id: sourceId } : {});
      toast(t("Tarama isteği alındı. Yalnız vadesi gelen kaynaklar kontrol edilecek."));
      await refreshAfterMutation();
    } catch (error) {
      if (error.status === 409) { toast(t("Bir kaynak taraması zaten sürüyor.")); await refreshAfterMutation(); }
      else toast(error.message, true);
    } finally { state.pending.delete("collect"); render(); }
  }

  function resetFilters() {
    state.topic = "all";
    state.search = "";
    state.sourceId = "";
    state.sourceBranch = "";
    state.sourceLanguage = "";
    state.sourceKind = "";
    state.sourceHealth = "";
    state.collectionId = "";
    state.readStatus = "all";
    state.sort = "relevance";
    document.getElementById("search").value = "";
    render();
    announce(t("Filtreler temizlendi."));
  }

  function changeView(view) {
    if (!["selection", "all", "saved", "later", "research", "decisions", "development", "legacy-notes", "sources", "collections", "settings"].includes(view)) return;
    if (view === "all" && state.view !== "all") invalidatePool();
    state.view = view;
    const tab = document.querySelector(`[data-view="${view}"]`);
    const details = tab?.closest("details");
    if (details) details.open = true;
    render();
    announce(t("{view} görünümü açıldı.", {view: document.getElementById("view-title").textContent}));
  }

  const workspaceContext = { element, icon, post, request, refreshAfterMutation, toast, state, topics, formatDate, changeView, render, findItem, invalidatePool, openReader, sourceLink, openPersonalNote };
  workspace = window.UFUKWorkspaceFactory?.(workspaceContext) || null;
  window.UFUKWorkspace = workspace;
  insights = window.UFUKInsightsFactory?.(workspaceContext) || null;
  workflow = window.UFUKWorkflowFactory?.(workspaceContext) || null;
  window.UFUKWorkflow = workflow;
  discovery = window.UFUKDiscoveryFactory?.(workspaceContext) || null;
  window.UFUKDiscovery = discovery;
  sourceLearning = window.UFUKSourceLearningFactory?.(workspaceContext) || null;
  if (discovery) document.querySelector(".header-actions").append(discovery.entry());
  document.getElementById("today").textContent = new Intl.DateTimeFormat(UI?.language === "en" ? "en-US" : "tr-TR", { day: "numeric", month: "long", year: "numeric" }).format(new Date());
  const today = new Date();
  document.getElementById("today").dateTime = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  collectButton.addEventListener("click", () => workflow?.run());
  document.getElementById("workflow-notifications")?.addEventListener("click", () => changeView(workflow?.counts().decisions ? "decisions" : "research"));
  document.querySelectorAll(".view-tab").forEach(tab => tab.addEventListener("click", () => changeView(tab.dataset.view)));
  document.querySelectorAll(".view-tabs").forEach(group => group.addEventListener("keydown", event => {
    const tabs = [...group.querySelectorAll(".view-tab")];
    let index = tabs.indexOf(document.activeElement);
    if (index < 0) return;
    if (["ArrowRight", "ArrowDown"].includes(event.key)) index = (index + 1) % tabs.length;
    else if (["ArrowLeft", "ArrowUp"].includes(event.key)) index = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") index = 0;
    else if (event.key === "End") index = tabs.length - 1;
    else return;
    event.preventDefault();
    tabs[index].focus();
    changeView(tabs[index].dataset.view);
  }));
  if (typeof window.matchMedia === "function") {
    const compactNavigation = window.matchMedia("(max-width: 780px)");
    const updateNavigationOrientation = () => document.querySelectorAll(".view-tabs").forEach(group => group.setAttribute("aria-orientation", compactNavigation.matches ? "horizontal" : "vertical"));
    updateNavigationOrientation();
    compactNavigation.addEventListener("change", updateNavigationOrientation);
  }
  renderTopicControls();
  const initialTopics = loadTopics();
  document.getElementById("search").addEventListener("input", event => {
    clearTimeout(searchTimer);
    state.search = event.target.value.trim();
    if (state.search && ["settings", "collections"].includes(state.view)) state.view = "all";
    searchTimer = setTimeout(() => { render(); announce(t("Arama sonuçları güncellendi.")); }, 150);
  });
  document.getElementById("show-archive").addEventListener("change", event => {
    state.showArchive = event.target.checked;
    render();
  });
  document.getElementById("source-filter").addEventListener("change", event => {
    state.sourceId = event.target.value;
    render();
  });
  document.getElementById("clear-filters")?.addEventListener("click", resetFilters);
  for (const layout of ["grid", "list"]) {
    document.getElementById(`view-${layout}`)?.addEventListener("click", () => {
      state.layout = layout;
      try { localStorage.setItem("ufuk-layout", layout); } catch { /* Kapalı depolama görünüm değişimini engellemez. */ }
      render();
      announce(layout === "list" ? t("Liste görünümü açıldı.") : t("Kart görünümü açıldı."));
    });
  }
  document.getElementById("reader-close")?.addEventListener("click", () => readerDialog?.close());
  readerDialog?.addEventListener("close", restoreReaderFocus);
  document.getElementById("job-close")?.addEventListener("click", requestJobClose);
  jobDialog?.addEventListener("cancel", event => { event.preventDefault(); requestJobClose(); });
  jobDialog?.addEventListener("close", restoreJobFocus);
  window.addEventListener("beforeunload", event => {
    if (jobDirty() || jobSession?.saving) { event.preventDefault(); event.returnValue = ""; }
  });
  document.addEventListener("keydown", event => {
    const target = event.target;
    if (event.key === "/" && !readerDialog?.open && !jobDialog?.open && !workspace?.busy() && !workflow?.busy() && !discovery?.busy() && !sourceLearning?.busy() && !event.ctrlKey && !event.metaKey && !event.altKey && !["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) && !target.isContentEditable) {
      event.preventDefault();
      document.getElementById("search").focus();
    }
  });
  document.addEventListener("visibilitychange", () => {
    clearTimeout(pollTimer);
    pollTimer = null;
    if (pageVisible()) refreshAfterMutation(true);
  });
  window.addEventListener("focus", () => { if (pageVisible()) refreshAfterMutation(true); });
  window.addEventListener("pagehide", () => { disposed = true; clearTimeout(pollTimer); pollTimer = null; clearTimeout(toastTimer); clearTimeout(searchTimer); });
  window.addEventListener("pageshow", event => { if (event.persisted) { disposed = false; refreshAfterMutation(true); } });
  async function openResearchEntry() {
    const intent = new URLSearchParams(window.location.search);
    const communityCard = intent.get("community_card");
    if (communityCard && communityCard.length <= 100 && state.data && discovery) { discovery.communityIntent?.(communityCard, document.getElementById("view-title")); return; }
    const entryId = intent.get("research_entry");
    if (!entryId || !state.data || !discovery) return;
    try {
      const platform = await request("/api/platform");
      const entry = (platform.entries || []).find(row => String(row.id) === entryId && row.kind === "research");
      if (!entry) { toast(t("Yeniden araştırılacak yerel kayıt bulunamadı."), true); return; }
      const provenance = entry.provenance || {};
      discovery.open(document.getElementById("view-title"), {question: provenance.question || t("Bu araştırmayı güncel kaynaklarla yeniden değerlendir: {title}", {title: entry.title.slice(0, 400)}), topic: provenance.topic || "ai", source_url: provenance.source_url || entry.sources?.[0]?.url || entry.url || "", claim_text: typeof provenance.claim_text === "string" ? provenance.claim_text : ""});
    } catch (error) { toast(error.message, true); }
  }
  Promise.all([refreshState(), initialTopics]).then(openResearchEntry);
})();
