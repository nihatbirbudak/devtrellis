// Topluluk kayıtlarını güvenli metin olarak gösterir; oturum, katkı önizlemesi ve sürümlü inceleme akışlarını yönetir.
(() => {
  "use strict";
  const UI = globalThis.UfukUI;
  const t = (key, values = {}) => UI ? UI.t(key, values) : key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
  const MAX_BYTES = 32768;
  const PAGE_SIZE = 24;
  const validCardId = value => typeof value === "string" && /^[a-f0-9]{32}$/i.test(value);
  const METRICS = {web_views: "Web görüntülemesi", mcp_reads: "AI/MCP erişimi", helpful: "Yararlı bulundu", used_in_research: "Araştırmada kullanıldı", needs_update: "Güncelleme bildirimi", disputed: "Doğruluk itirazı"};
  const FEEDBACK = {helpful: "Yararlı buldum", used_in_research: "Araştırmamda kullandım", needs_update: "Güncelliğini yitirmiş", disputed: "Doğruluğuna itiraz et"};
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
  function publicCardVersion(search) {
    const values = new URLSearchParams(search).getAll("version");
    return values.length === 1 && /^[1-9][0-9]*$/.test(values[0]) && Number.isSafeInteger(Number(values[0])) ? Number(values[0]) : null;
  }
  function createEventRegistry(storage, random) {
    const records = new Map();
    return (id, version, kind) => {
      if (!validCardId(id) || !Number.isSafeInteger(version) || version < 1 || !["web_view", ...Object.keys(FEEDBACK)].includes(kind)) throw new Error("Invalid research event");
      const key = `devtrellis:event:${id.toLowerCase()}:${version}:${kind}`;
      if (!records.has(key)) {
        let saved;
        try { saved = JSON.parse(storage?.getItem(key) || "null"); } catch { saved = null; }
        const eventId = /^[a-f0-9]{32}$/.test(saved?.event_id || "") ? saved.event_id : Array.from(random.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, "0")).join("");
        const record = {event_id: eventId, sent: saved?.sent === true, busy: false, error: false};
        record.save = () => { try { storage?.setItem(key, JSON.stringify({event_id: record.event_id, sent: record.sent})); } catch { /* Sekme belleğiyle devam edilir. */ } };
        record.save(); records.set(key, record);
      }
      return records.get(key);
    };
  }
  function publicCardId(search) {
    const params = new URLSearchParams(search);
    const values = [...params.getAll("card"), ...params.getAll("research")];
    if (params.has("version") && !publicCardVersion(search)) return null;
    return values.length === 1 && validCardId(values[0]) ? values[0].toLowerCase() : null;
  }
  const safeURL = value => {
    try { const url = new URL(String(value || "")); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
    catch { return null; }
  };
  function parsePacket(text) {
    if (new TextEncoder().encode(text).length > MAX_BYTES) throw new Error(t("Paylaşım dosyası en fazla 32 KB olabilir."));
    let packet;
    try { packet = JSON.parse(text); } catch { throw new Error(t("Bu dosya geçerli bir JSON dosyası değil.")); }
    if (!packet || Array.isArray(packet) || packet.schema !== "ufuk-share" || ![1, 2, 3].includes(packet.schema_version) || !["project", "research"].includes(packet.kind)) throw new Error(t("Bu dosya desteklenen bir UFUK paylaşım paketi değil."));
    return packet;
  }
  function validatePacket(packet) {
    if (!packet.title?.trim() || !packet.summary?.trim() || !packet.author?.trim()) throw new Error(t("Başlık, özet ve yazar gerekli."));
    if (!["CC-BY-4.0", "CC0-1.0"].includes(packet.license)) throw new Error(t("Paylaşım izni seç."));
    if (packet.kind === "project" && !packet.url) throw new Error(t("Proje için bir bağlantı gerekli."));
    if (packet.kind === "research" && !packet.sources?.length) throw new Error(t("Araştırma için en az bir kaynak gerekli."));
    for (const url of [packet.url, ...(packet.sources || []).map(source => source.url)].filter(Boolean)) if (!safeURL(url)) throw new Error(t("Geçersiz bağlantı. Yalnız kullanıcı bilgisi içermeyen http(s) bağlantıları kullanılabilir."));
    if (new TextEncoder().encode(JSON.stringify({packet})).length > MAX_BYTES) throw new Error(t("Paylaşım dosyası en fazla 32 KB olabilir."));
    return packet;
  }
  function filterCards(cards, query) {
    const normalize = value => String(value || "").normalize("NFKC").toLocaleLowerCase("tr");
    const words = normalize(query).trim().split(/\s+/).filter(Boolean);
    return cards.filter(card => {
      const packet = card.packet || {};
      const text = normalize([packet.title, packet.summary, packet.body, packet.author, ...(packet.tags || []), ...(packet.sources || []).map(source => source.title)].filter(Boolean).join(" "));
      return words.every(word => text.includes(word));
    });
  }
  function errorText(error) {
    if (error.status === 409) return t("Kayıt değişti. Taslağın korunuyor; kopyalayıp sayfayı yenileyerek son sürümü al.");
    if (error.status === 401 || error.status === 403) return t("Oturumun sona ermiş olabilir. Taslağını koruyup yeniden giriş yap.");
    return t(error.message || "İşlem tamamlanamadı. Yeniden deneyebilirsin.");
  }
  if (typeof module !== "undefined" && module.exports) module.exports = {safeURL, parsePacket, validatePacket, filterCards, errorText, publicCardId, publicCardVersion, createEventRegistry};
  if (typeof document === "undefined") return;
  UI?.initialize();
  const byId = id => document.getElementById(id);
  const state = {session: null, topicLabels: {}, tab: "feed", offset: 0, total: 0, generation: 0, loading: false, loginBusy: false, logoutBusy: false, fileBusy: false, composer: null, composerSnapshot: "", submitBusy: false, detail: null, detailLoading: false, publicCardOpen: false, reviewBusy: false};
  let eventStorage;
  try { eventStorage = window.sessionStorage; } catch { eventStorage = null; }
  const eventRecord = createEventRegistry(eventStorage, globalThis.crypto);
  const eventBusy = new Set();

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }
  function button(label, handler, className = "text-button") {
    const item = node("button", className, label);
    item.type = "button";
    item.addEventListener("click", handler);
    return item;
  }
  function link(label, address, className = "") {
    const url = safeURL(address);
    if (!url) return node("span", className, label);
    const item = node("a", className, label);
    item.href = url;
    item.target = "_blank";
    item.rel = "noopener noreferrer";
    return item;
  }
  function setPermalink(id = null, version = null) {
    if (!window.history?.replaceState || !window.location?.href) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("card"); url.searchParams.delete("research"); url.searchParams.delete("version");
    if (validCardId(id)) url.searchParams.set("card", id.toLowerCase());
    if (validCardId(id) && Number.isSafeInteger(version) && version > 0) url.searchParams.set("version", String(version));
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }
  function originalLinks(packet, compact = false) {
    const entries = [], seen = new Set();
    for (const [label, value] of [[t(packet.kind === "project" ? "Proje bağlantısı" : "Araştırma bağlantısı"), packet.url], [t("Özgün yayın"), packet.provenance?.source_url], ...(packet.sources || []).map(source => [source.title || t("Kaynak"), source.url])]) {
      const url = safeURL(value); if (!url || seen.has(url)) continue;
      seen.add(url); const host = new URL(url).hostname;
      const github = host === "github.com" || host.endsWith(".github.com");
      entries.push({url, github, label: github ? `GitHub · ${label}` : label});
    }
    if (!entries.length) return null;
    const block = node("section", compact ? "card-original-links" : "reader-original-links");
    block.append(node(compact ? "h4" : "h3", "source-heading", t("Kaynaklar ve özgün bağlantılar")));
    const list = node("ul", "source-list");
    const visible = compact ? [...entries.filter(item => item.github), ...entries.filter(item => !item.github)].slice(0, 3) : entries;
    for (const item of visible) { const row = node("li"); row.append(link(`${item.label} ↗`, item.url)); list.append(row); }
    block.append(list);
    if (compact && entries.length > 3) block.append(node("p", "field-help", t("Tüm kaynaklar tam metnin altında yer alır.")));
    return block;
  }
  function researchLink(item) {
    const label = t("{title} · sürüm {version}", {title: item.title || t("Araştırma"), version: item.version});
    if (item.available === false || !validCardId(item.id) || !Number.isSafeInteger(item.version) || item.version < 1) return node("span", "field-help", t("Önceki araştırma artık yayımlanmıyor · sürüm {version}", {version: item.version}));
    const anchor = node("a", "", label);
    anchor.href = `/?card=${item.id.toLowerCase()}&version=${item.version}`;
    return anchor;
  }
  function lineagePanel(packet, relationships) {
    const parents = relationships?.parents || packet.lineage?.parents?.map(parent => ({id: parent.research_id, version: parent.version, title: parent.research_id}));
    const children = relationships?.children || [];
    if (!parents?.length && !children.length && !packet.lineage?.change_summary) return null;
    const section = node("section", "research-lineage");
    section.append(node("h3", "", t("Araştırmanın gelişimi")));
    if (parents?.length) {
      section.append(node("h4", "", t("Temel alınan araştırmalar")));
      const list = node("ul", "source-list");
      for (const parent of parents) {
        const row = node("li"); row.append(researchLink(parent));
        if (parent.available !== false && (parent.author || parent.license)) row.append(node("p", "field-help", [parent.author, parent.license].filter(Boolean).join(" · ")));
        list.append(row);
      }
      section.append(list, node("p", "field-help", t("Bağlantılar araştırmada kullanılan yayımlanmış sürümü açar.")));
    }
    if (packet.lineage?.change_summary) section.append(node("h4", "", t("Bu çalışmada eklenenler")), node("p", "lineage-summary", packet.lineage.change_summary));
    if (children.length) {
      section.append(node("h4", "", t("Bu araştırmayı geliştiren çalışmalar ({count})", {count: count(relationships?.children_total || children.length)})));
      const list = node("ul", "source-list");
      for (const child of children) { const row = node("li"); row.append(researchLink(child)); list.append(row); }
      section.append(list);
      if (relationships.children_total > children.length) section.append(node("p", "field-help", t("Son {count} çalışma gösteriliyor.", {count: children.length})));
    }
    return section;
  }
  function metricList(engagement, compact = false) {
    const list = node("dl", compact ? "research-metrics compact" : "research-metrics");
    for (const [key, label] of Object.entries(METRICS)) {
      const fact = node("div", "research-metric");
      fact.append(node("dt", "", t(label)), node("dd", "", count(engagement?.[key]))); list.append(fact);
    }
    return list;
  }
  function engagementPanel(card) {
    if (!card.engagement || !validCardId(card.id) || !Number.isSafeInteger(card.version) || card.version < 1) return null;
    const key = `${card.id}:${card.version}`, section = node("section", "research-engagement"), metrics = node("div"), actions = node("div", "engagement-actions");
    const feedback = node("p", "field-help"); feedback.setAttribute("role", "status"); feedback.setAttribute("aria-live", "polite");
    section.append(node("h3", "", t("Kullanım ve geri bildirim · sürüm {version}", {version: card.version})), metrics, node("p", "field-help", t("Bunlar benzersiz kişi sayısı veya doğruluk puanı değildir. AI erişimi, yararlanıldığı anlamına gelmez; bildirimler okuyucu beyanıdır.")), actions, feedback);
    if (card.published_version && card.published_version !== card.version) {
      const current = node("a", "", t("Güncel yayımlanmış sürümü aç")); current.href = `/?card=${card.id}`;
      section.append(node("p", "notice", t("Önceki bir sürümü okuyorsun. Sayaçlar ve bildirimler bu sürüme aittir.")), current);
    }
    const controls = new Map();
    for (const [kind, label] of Object.entries(FEEDBACK)) { const control = button(t(label), () => send(kind), "button secondary"); controls.set(kind, control); actions.append(control); }
    const retry = button(t("Görüntüleme kaydını yeniden dene"), () => send("web_view"), "text-button"); retry.hidden = true; section.append(retry);
    let lastKind = null;
    function refresh() {
      metrics.replaceChildren(metricList(card.engagement));
      const busy = eventBusy.has(key);
      for (const [kind, control] of controls) {
        const record = eventRecord(card.id, card.version, kind);
        control.disabled = busy || record.sent || !state.session?.csrf_token;
        control.textContent = record.sent ? `${t(FEEDBACK[kind])} ✓` : record.busy ? t("Kaydediliyor…") : t(FEEDBACK[kind]);
      }
      const view = eventRecord(card.id, card.version, "web_view"); retry.hidden = !view.error; retry.disabled = busy;
      if (lastKind) {
        const record = eventRecord(card.id, card.version, lastKind);
        feedback.textContent = record.busy ? t("Kaydediliyor…") : record.error ? (record.errorMessage || t("Bildirim kaydedilemedi. Yeniden denediğinde aynı bildirim kullanılır.")) : record.sent && lastKind !== "web_view" ? t("Bildirimin bu sürüm için kaydedildi.") : "";
      }
    }
    async function send(kind) {
      const record = eventRecord(card.id, card.version, kind);
      if (record.sent || eventBusy.has(key)) return;
      record.busy = true; record.error = false; eventBusy.add(key); lastKind = kind; refresh();
      try {
        const result = await request(`/cards/${encodeURIComponent(card.id)}/events`, {kind, version: card.version, event_id: record.event_id});
        if (!result.engagement || typeof result.engagement !== "object") throw new Error("Invalid event response");
        card.engagement = result.engagement; record.sent = true; record.save();
        if (state.detail?.id === card.id && state.detail.version === card.version) state.detail.engagement = result.engagement;
      } catch (error) { record.error = true; record.errorMessage = error.status === 409 ? t("Bu sürüme artık bildirim eklenemiyor. Kaydı yeniden açıp yayın durumunu kontrol et.") : null; }
      finally { record.busy = false; eventBusy.delete(key); refresh(); if (state.engagementKey === key) state.refreshEngagement?.(); }
    }
    state.engagementKey = key; state.refreshEngagement = refresh;
    refresh();
    void send("web_view");
    return section;
  }
  function message(id, text) { byId(id).textContent = text || ""; byId(id).hidden = !text; }
  function dateLabel(value) {
    const date = value ? new Date(value) : null;
    return !date || Number.isNaN(date.getTime()) ? t("Bilinmiyor") : new Intl.DateTimeFormat(UI?.language === "en" ? "en-US" : "tr-TR", {day: "numeric", month: "long", year: "numeric"}).format(date);
  }
  async function request(path, payload) {
    const options = {credentials: "same-origin", headers: {Accept: "application/json"}};
    if (payload !== undefined) {
      if (!state.session?.csrf_token) throw new Error(t("Çalışma alanı henüz hazır değil. Sayfayı yeniden yükleyip dene."));
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.headers["X-Ufuk-CSRF"] = state.session.csrf_token;
      options.body = JSON.stringify(payload);
    }
    let response;
    try { response = await fetch(`/api/community${path}`, options); }
    catch { throw new Error(t("Bağlantı kurulamadı. Yeniden deneyebilirsin.")); }
    let data;
    try { data = await response.json(); } catch { throw new Error(t("Sunucu beklenen yanıtı veremedi.")); }
    if (!response.ok) {
      const error = new Error(typeof data.detail === "string" ? data.detail : t("İşlem tamamlanamadı. Alanları kontrol edip yeniden dene."));
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function renderSession() {
    const user = state.session?.user;
    byId("login-open").hidden = Boolean(user);
    byId("login-open").disabled = !state.session;
    byId("logout").hidden = !user;
    byId("account-name").hidden = !user;
    byId("account-name").textContent = user?.display_name || user?.username || "";
    byId("community-tab-mine").hidden = !user;
    byId("community-tab-review").hidden = user?.role !== "moderator";
    if (byId("request-management")) byId("request-management").hidden = user?.role !== "moderator";
    byId("contribute").disabled = !state.session || state.fileBusy;
  }
  async function loadSession() { state.session = await request("/session"); renderSession(); }
  function selectTab(tab, focus = false) {
    if ((tab === "mine" && !state.session?.user) || (tab === "review" && state.session?.user?.role !== "moderator")) tab = "feed";
    state.tab = tab;
    for (const item of document.querySelectorAll("[data-tab]")) {
      const active = item.dataset.tab === tab;
      item.setAttribute("aria-selected", String(active));
      item.tabIndex = active ? 0 : -1;
      if (active && focus) item.focus();
    }
    byId("community-panel").setAttribute("aria-labelledby", `community-tab-${tab}`);
    byId("feed-interest-label").hidden = tab !== "feed";
    byId("search-query").value = "";
    loadRecords();
  }
  function paging() {
    byId("community-pagination").hidden = state.total <= PAGE_SIZE;
    byId("page-prev").disabled = state.loading || state.offset <= 0;
    byId("page-next").disabled = state.loading || state.offset + PAGE_SIZE >= state.total;
    byId("page-label").textContent = `${Math.floor(state.offset / PAGE_SIZE) + 1} / ${Math.max(1, Math.ceil(state.total / PAGE_SIZE))}`;
  }
  function emptyState(title, description, clear = false) {
    const empty = node("div", "empty-state");
    const mark = node("span", "empty-mark", "↗");
    mark.setAttribute("aria-hidden", "true");
    empty.append(mark, node("h3", "", title), node("p", "", description));
    if (clear) empty.append(button(t("Aramayı temizle"), () => { byId("search-query").value = ""; byId("search-interest").value = ""; loadRecords(); }, "button secondary"));
    return empty;
  }
  async function loadRecords(offset = 0) {
    const generation = ++state.generation;
    const tab = state.tab;
    state.loading = true;
    paging();
    byId("community-list").setAttribute("aria-busy", "true");
    byId("community-count").textContent = t("Kayıtlar yükleniyor…");
    message("community-error", "");
    const query = byId("search-query").value.trim();
    const rankedRequested = tab === "research" && Boolean(query);
    const params = new URLSearchParams({query, offset: String(offset), limit: String(PAGE_SIZE)});
    try {
      let data;
      if (tab === "feed") {
        params.set("interest", byId("search-interest").value.trim());
        data = await request(`/feed?${params}`);
      } else if (tab === "mine" || tab === "review") {
        data = await request(`/${tab}?${params}`);
      } else {
        params.set("kind", tab);
        if (rankedRequested) params.set("search_mode", "ranked");
        data = await request(`/cards?${params}`);
      }
      if (generation !== state.generation) return;
      state.offset = offset;
      state.total = data.total || 0;
      const list = byId("community-list");
      list.replaceChildren();
      if (rankedRequested) {
        if (data.search?.mode === "all" && data.search?.fallback === true) {
          list.append(node("p", "field-help search-status", t("Sıralamalı arama kullanılamıyor; sözcük araması sonuçları gösteriliyor.")));
        } else if (data.search?.mode !== "ranked" || data.search?.fallback !== false) {
          list.append(node("p", "field-help search-status", t("Sunucu arama sıralaması bilgisini sağlamıyor; sonuçların ilgililik sırası doğrulanamadı.")));
        }
      }
      for (const item of data.items || []) list.append(tab === "feed" ? feedCard(item) : contributionCard(item, rankedRequested));
      if (!(data.items || []).length) {
        if (query || (tab === "feed" && byId("search-interest").value.trim())) list.append(emptyState(t("Bu aramada kayıt bulunamadı."), t("Onaylanan katkılar burada görünür. Aramayı değiştirebilir veya daha sonra tekrar bakabilirsin."), true));
        else if (tab === "mine") list.append(emptyState(t("Henüz katkı yok."), t("Bir paylaşım dosyası getirerek ilk katkını hazırlayabilirsin.")));
        else if (tab === "review") list.append(emptyState(t("İnceleme sırası boş."), t("Yeni gönderimler burada inceleme bekleyecek.")));
        else if (tab === "feed") list.append(emptyState(t("Gelişmeler henüz toplanmadı."), t("Bu kurulumun yöneticisi kayıtlı kaynakları topladığında burada görünecek.")));
        else list.append(emptyState(t("Henüz yayımlanmış kayıt yok."), t("Onaylanan katkılar burada görünür. Aramayı değiştirebilir veya daha sonra tekrar bakabilirsin.")));
      }
      byId("community-count").textContent = t("{total} kayıt", {total: state.total});
    } catch (error) {
      if (generation !== state.generation) return;
      byId("community-list").replaceChildren();
      byId("community-count").textContent = t("Gelişmeler alınamadı.");
      message("community-error", errorText(error));
      byId("community-error").append(button(t("Yeniden dene"), () => loadRecords(offset), "button secondary"));
    } finally {
      if (generation === state.generation) { state.loading = false; byId("community-list").setAttribute("aria-busy", "false"); paging(); }
    }
  }
  function feedCard(item) {
    const article = node("article", "card");
    const body = node("div", "card-body");
    const meta = node("div", "card-meta");
    if (item.topic_label || item.topic) meta.append(node("span", "badge", state.topicLabels[item.topic] || item.topic_label || item.topic));
    const heading = node("h3");
    heading.append(link(item.title, item.url));
    body.append(meta, heading, node("p", "summary", item.excerpt || ""), link(`${item.source_name || t("Kaynak")} ↗`, item.url, "card-source"));
    body.append(node("p", "entry-note", t("Yayın: {date}", {date: dateLabel(item.published_at)})));
    if (item.last_seen_at) body.append(node("p", "entry-note", t("Son görüldü: {date}", {date: dateLabel(item.last_seen_at)})));
    article.append(body);
    return article;
  }
  const statusLabel = status => t({pending: "İnceleme bekliyor", published: "Yayımlandı", rejected: "Reddedildi", withdrawn: "Geri çekildi", hidden: "Yayından kaldırıldı"}[status] || "Bilinmiyor");
  function searchMatchInfo(match) {
    if (!match || typeof match !== "object" || Array.isArray(match)) return null;
    const section = node("div", "search-match");
    for (const [key, label] of [["matched_terms", "Eşleşen terimler: {terms}"], ["missing_terms", "Eşleşmeyen terimler: {terms}"]]) {
      const terms = match[key];
      if (Array.isArray(terms) && terms.length <= 40 && terms.every(term => typeof term === "string" && term.length <= 200) && terms.length) {
        section.append(node("p", "field-help", t(label, {terms: terms.join(", ")})));
      }
    }
    section.append(node("p", "field-help", t(match.answer_sufficiency === "not_assessed"
      ? "Sözcük eşleşmesi; bu soruyu yanıtlamaya yeterliliği veya doğruluğu değerlendirilmedi."
      : "Cevap yeterliliği bilgisi yok; araştırmanın kapsamını ve kaynaklarını kontrol et.")));
    return section;
  }
  function contributionCard(card, showSearchMatch = false) {
    const packet = card.packet;
    const article = node("article", "card");
    const body = node("div", "card-body");
    const meta = node("div", "card-meta");
    meta.append(node("span", "badge", t(packet.kind === "project" ? "Proje" : "Araştırma")));
    const privateView = state.tab === "mine" || state.tab === "review";
    if (privateView) meta.append(node("span", `badge status-${card.status}`, statusLabel(card.status)));
    const heading = node("h3");
    if (privateView) heading.textContent = packet.title;
    else if (validCardId(card.id)) {
      const titleLink = node("a", "card-title-link", packet.title); titleLink.href = `/?card=${card.id}`;
      titleLink.addEventListener("click", event => {
        if (event.button > 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault(); return openCard(card, "read", event.currentTarget);
      }); heading.append(titleLink);
    } else heading.append(button(packet.title, event => openCard(card, "read", event.currentTarget), "card-title-link"));
    body.append(meta, heading, node("p", "summary", packet.summary));
    const matchInfo = showSearchMatch ? searchMatchInfo(card.search_match) : null;
    if (matchInfo) body.append(matchInfo);
    if (!privateView && card.engagement) body.append(metricList(card.engagement, true));
    if (!privateView && card.relationships?.children_total) body.append(node("p", "entry-note", t("Geliştiren çalışmalar: {count}", {count: count(card.relationships.children_total)})));
    const sources = originalLinks(packet, true); if (sources) body.append(sources);
    if (packet.tags?.length) { const tags = node("div", "tags"); packet.tags.forEach(tag => tags.append(node("span", "tag", tag))); body.append(tags); }
    body.append(node("p", "entry-note", t("Güncellendi: {date}", {date: dateLabel(packet.updated_at)})));
    if (packet.provenance) {
      body.append(node("p", "entry-note", t("Kaynak araştırmanın modeli: {model}", {model: !packet.provenance.model || packet.provenance.model === "unknown" ? t("Model kaydedilmemiş") : packet.provenance.model})));
      body.append(node("p", "entry-note", packet.provenance.generated_at ? t("Araştırma tarihi: {date}", {date: dateLabel(packet.provenance.generated_at)}) : t("Üretim tarihi kaydedilmemiş")));
    }
    body.append(node("p", "submission-owner", `${t("Yazar beyanı")}: ${packet.author || t("Belirtilmedi")}`));
    if (card.submission_type === "anonymous" || card.owner?.verified === false) body.append(node("p", "entry-note", t("Hesapsız katkı · Yazar kimliği doğrulanmamış")));
    if (privateView && card.published_version && ["pending", "rejected"].includes(card.status)) body.append(node("p", "private-version-note", t("Yayımlanan sürüm: {version}. Bu taslak onaylanana kadar önceki sürüm görünür kalır.", {version: card.published_version})));
    if (privateView && (card.moderation_note || card.review_note || card.note)) body.append(node("p", "private-version-note", t("İnceleme notu: {note}", {note: card.moderation_note || card.review_note || card.note})));
    const actions = node("div", "card-actions community-card-actions");
    actions.append(button(t(privateView ? "Oku" : packet.kind === "project" ? "Ayrıntıları oku" : "Araştırmayı oku"), event => openCard(card, "read", event.currentTarget), privateView ? "text-button" : "button read-primary"));
    if (!privateView) actions.append(button(t("Kendi modelimle araştır"), () => openResearchApp(card), "text-button research-optional"));
    if (state.tab === "mine") {
      actions.append(button(t("Düzelt"), () => openComposer(card.packet, card)));
      if (card.status !== "withdrawn") actions.append(button(t("Geri çek"), event => withdraw(card, event.currentTarget)));
    }
    if (state.tab === "review") actions.append(button(t("İncele"), event => openCard(card, "review", event.currentTarget)));
    if (!privateView && state.session?.user?.role === "moderator") actions.append(button(t("Yayından kaldır"), event => openCard(card, "hide", event.currentTarget)));
    article.append(body, actions);
    return article;
  }
  function packetContent(packet, titleId, relationships = null) {
    const content = node("div");
    const heading = node("h2", "detail-title", packet.title);
    if (titleId) heading.id = titleId;
    content.append(node("p", "card-preview-kind", t(packet.kind === "project" ? "Proje" : "Araştırma")), heading, node("p", "detail-summary", packet.summary));
    content.append(node("h3", "reader-body-heading", t(packet.kind === "project" ? "Projenin ayrıntıları" : "Tam araştırma metni")), node("div", "detail-body", packet.body || t("Bu kayıtta ek metin bulunmuyor. Özet ve özgün kaynakları okuyabilirsin.")));
    const originals = originalLinks(packet); if (originals) content.append(originals);
    const lineage = lineagePanel(packet, relationships); if (lineage) content.append(lineage);
    const meta = node("dl", "detail-meta");
    for (const [key, value] of [["Yazar", packet.author], ["Oluşturuldu", dateLabel(packet.created_at)], ["Güncellendi", dateLabel(packet.updated_at)], ["Metin izni", packet.license]]) meta.append(node("dt", "", t(key)), node("dd", "", value || t("Belirtilmedi")));
    content.append(meta);
    if (packet.provenance || packet.kind === "research") {
      const provenance = node("section", "provenance-panel"); provenance.append(node("h3", "", t("Araştırmanın kökeni")));
      const facts = node("dl", "detail-meta");
      for (const [label, value] of UI?.provenanceFacts?.(packet.provenance || {model: "unknown", generated_at: null}, t, dateLabel) || []) facts.append(node("dt", "", label), node("dd", "", value));
      provenance.append(facts, node("p", "field-help", t("Bu bilgiler kaynak araştırmaya aittir; kart metni sonradan düzenlenmiş olabilir. İçe alınan kayıtların model ve kaynak bilgileri yazar beyanıdır.")), node("p", "field-help", t("Modelin markası veya fiyatı doğruluk kanıtı değildir; kaynakları, tarihi ve raporun sınırlarını kontrol et.")));
      if (packet.provenance?.modified) provenance.append(node("p", "notice", t("Kart içeriği özgün rapordan sonra değiştirildi; yeniden kanıt denetimi yapılmadı.")));
      content.append(provenance);
    }
    if (packet.tags?.length) { const tags = node("div", "tags"); packet.tags.forEach(tag => tags.append(node("span", "tag", tag))); content.append(tags); }
    content.append(node("p", "field-help", t("Yalnız kart metni seçilen izinle paylaşılır; bağlantılı kod ve kaynak yayınları yeniden lisanslanmaz.")));
    return content;
  }
  async function openCard(card, mode, trigger, publicOnly = false, requestedVersion = null) {
    if (trigger.disabled || state.detailLoading) return;
    state.detailLoading = true;
    trigger.disabled = true;
    message("community-error", "");
    const privateView = !publicOnly && (state.tab === "mine" || state.tab === "review");
    try {
      const suffix = Number.isSafeInteger(requestedVersion) && requestedVersion > 0 ? `?version=${requestedVersion}` : "";
      const latest = privateView ? card : await request(`/cards/${encodeURIComponent(card.id)}${suffix}`);
      state.detail = latest.card || latest;
      byId("card-content").replaceChildren(packetContent(state.detail.packet, "card-title", state.detail.relationships));
      if (!privateView) {
        const engagement = engagementPanel(state.detail); if (engagement) byId("card-content").append(engagement);
        const optional = node("section", "reader-optional");
        optional.append(node("p", "field-help", t("Bu metni burada okumak için hesap, yerel uygulama veya yapay zekâ modeli gerekmez. Yeni bir araştırma yapmak istersen kendi modelini kullanabilirsin.")));
        if (!state.detail.published_version || state.detail.published_version === state.detail.version) optional.append(button(t("Kendi modelimle araştır"), () => openResearchApp(state.detail), "button secondary"));
        if (validCardId(card.id)) { const permalink = node("a", "reader-permalink", t("Bu kaydın kalıcı bağlantısı")); permalink.href = `/?card=${card.id}${requestedVersion ? `&version=${requestedVersion}` : ""}`; optional.append(permalink); }
        byId("card-content").append(optional);
      }
      byId("card-download").hidden = privateView;
      byId("card-file-options").hidden = privateView;
      if (!privateView) byId("card-download").href = `/api/community/cards/${encodeURIComponent(card.id)}/packet${suffix}`;
      state.detailMode = mode;
      byId("review-controls").hidden = mode === "read";
      byId("review-publish").hidden = mode !== "review";
      byId("review-reject").hidden = mode !== "review";
      byId("review-hide").hidden = mode !== "hide";
      byId("review-action-note").textContent = t(mode === "hide" ? "Bu işlem yayımlanmış kartı okuyuculardan kaldırır. Gerekçe katkı sahibine görünür." : "Bu işlem kartı bu hizmetin okuyucularına açar. Önce kaynakları ve paylaşım iznini kontrol et.");
      byId("review-note").value = "";
      message("review-error", "");
      const own = state.detail.owner?.username === state.session?.user?.username;
      byId("review-publish").disabled = own;
      byId("review-reject").disabled = own;
      byId("review-hide").disabled = false;
      if (mode === "review" && own) message("review-error", t("Bu katkı sana ait. Kendi katkını onaylayamazsın."));
      byId("card-dialog").showModal();
      byId("card-dialog").scrollTop = 0;
      state.publicCardOpen = !privateView;
      if (!privateView && validCardId(card.id)) setPermalink(card.id, requestedVersion);
    } catch (error) { message("community-error", !privateView && error.status === 404 ? t("Bu kayıt bulunamadı veya artık yayımlanmıyor.") : errorText(error)); }
    finally { trigger.disabled = false; state.detailLoading = false; }
  }
  function openLogin() { message("login-error", ""); byId("login-password").value = ""; byId("login-dialog").showModal(); byId("login-username").focus(); }
  function openResearchApp(card = null) {
    const dialog = node("dialog", "local-app-dialog");
    const heading = node("h2", "", t("Kendi bilgisayarında araştır"));
    heading.id = "local-app-title";
    dialog.setAttribute("aria-labelledby", heading.id);
    const header = node("div", "dialog-header"); header.append(heading);
    const body = node("div", "dialog-body");
    body.append(node("p", "", t("Yerel uygulamayı çalıştır; ardından aşağıdaki bağlantıyı aç. Hesap gerekmez. Model ve API kullanımını kendi ayarların belirler.")));
    if (card) body.append(node("p", "notice", t("Bu bağlantı yalnız bir kayıt seçer. Yerel uygulama içeriği getirmeden önce sorar; model kendiliğinden çalışmaz.")), node("p", "", card.packet.title));
    const address = new URL("http://127.0.0.1:8781/");
    if (card) address.searchParams.set("community_card", card.id);
    const actions = node("div", "dialog-actions");
    actions.append(link(t("Çalışan yerel uygulamamı aç"), address.href, "button"), link(t("Kurulum adımlarını aç"), "https://github.com/nihatbirbudak/devtrellis#installation", "button secondary"), button(t("Kapat"), () => dialog.close(), "button secondary"));
    body.append(node("p", "field-help", t("Varsayılan yerel adres: 127.0.0.1:8781. Uygulama açılmıyorsa önce kurulum adımlarını tamamla ve programı çalıştır.")));
    dialog.append(header, body, actions);
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog); dialog.showModal();
  }
  async function login(event) {
    event.preventDefault();
    if (state.loginBusy || !byId("login-form").reportValidity()) return;
    state.loginBusy = true;
    byId("login-fields").disabled = true;
    byId("login-submit").disabled = true;
    byId("login-submit").textContent = t("Giriş yapılıyor…");
    message("login-error", "");
    try {
      state.session = await request("/login", {username: byId("login-username").value.trim(), password: byId("login-password").value});
      renderSession();
      byId("login-dialog").close();
      message("community-status", t("Giriş yapıldı."));
      loadRecords();
    } catch (error) { message("login-error", errorText(error)); }
    finally { byId("login-password").value = ""; state.loginBusy = false; byId("login-fields").disabled = false; byId("login-submit").disabled = false; byId("login-submit").textContent = t("Giriş yap"); }
  }
  async function logout() {
    if (state.logoutBusy) return;
    state.logoutBusy = true;
    byId("logout").disabled = true;
    try {
      await request("/logout", {});
      await loadSession();
      selectTab("feed");
      message("community-status", t("Çıkış yapıldı."));
    } catch (error) { message("community-error", errorText(error)); }
    finally { state.logoutBusy = false; byId("logout").disabled = false; }
  }
  function snapshot() { return JSON.stringify(["name", "summary", "body", "url", "tags", "author", "sources", "license"].map(key => byId(`compose-${key}`).value)); }
  function openComposer(packet, card = null) {
    state.composer = {original: packet, card, packet: null, idempotencyKey: null};
    byId("compose-name").value = packet.title || "";
    byId("compose-summary").value = packet.summary || "";
    byId("compose-body").value = packet.body || "";
    byId("compose-url").value = packet.url || "";
    byId("compose-tags").value = Array.isArray(packet.tags) ? packet.tags.join(", ") : "";
    byId("compose-author").value = packet.author || "";
    byId("compose-sources").value = Array.isArray(packet.sources) ? packet.sources.map(source => `${source.title || ""} | ${source.url || ""}`).join("\n") : "";
    byId("compose-license").value = packet.license || "";
    state.composerSnapshot = snapshot();
    byId("compose-form").hidden = false;
    byId("compose-preview").hidden = true;
    message("compose-error", "");
    byId("compose-dialog").showModal();
    byId("compose-name").focus();
  }
  async function importFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || state.fileBusy) return;
    if (!state.session?.user) { openLogin(); return; }
    state.fileBusy = true;
    renderSession();
    message("community-error", "");
    try {
      if (file.size > MAX_BYTES) throw new Error(t("Paylaşım dosyası en fazla 32 KB olabilir."));
      const packet = parsePacket(await file.text());
      openComposer(packet);
    } catch (error) { message("community-error", errorText(error)); }
    finally { state.fileBusy = false; renderSession(); }
  }
  function composePacket() {
    const original = state.composer.original;
    const tags = [...new Set(byId("compose-tags").value.split(",").map(tag => tag.trim()).filter(Boolean))];
    if (tags.length > 12 || tags.some(tag => tag.length > 40)) throw new Error(t("En fazla 12 ilgi alanı veya etiket ekleyebilirsin."));
    const lines = byId("compose-sources").value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    if (lines.length > 10) throw new Error(t("En fazla 10 kaynak ekleyebilirsin."));
    const sources = lines.map((line, index) => {
      const separator = line.indexOf("|");
      const title = (separator < 0 ? line : line.slice(0, separator)).trim();
      const url = (separator < 0 ? line : line.slice(separator + 1)).trim();
      if (!title || title.length > 160) throw new Error(t("{number}. kaynak başlığı en fazla 160 karakter olabilir.", {number: index + 1}));
      return {title, url};
    });
    const packet = {schema: "ufuk-share", schema_version: 1, kind: original.kind, title: byId("compose-name").value.trim(), summary: byId("compose-summary").value.trim(), body: byId("compose-body").value.trim(), url: byId("compose-url").value.trim(), tags, sources, author: byId("compose-author").value.trim(), license: byId("compose-license").value, created_at: original.created_at, updated_at: new Date().toISOString()};
    if (original.lineage) packet.lineage = {parents: original.lineage.parents.map(parent => ({research_id: parent.research_id, version: parent.version})), change_summary: original.lineage.change_summary};
    if (original.provenance) {
      const changed = ["title", "summary", "body", "url", "sources"].some(key => JSON.stringify(packet[key]) !== JSON.stringify(original[key]));
      packet.schema_version = 2;
      packet.provenance = {...original.provenance, modified: Boolean(original.provenance.modified || changed)};
      if (packet.provenance.modified) packet.provenance.evidence_status = "unverified";
    }
    if (packet.lineage) packet.schema_version = 3;
    return validatePacket(packet);
  }
  function previewSubmission(event) {
    event.preventDefault();
    if (state.submitBusy || !byId("compose-form").reportValidity()) return;
    message("compose-error", "");
    try {
      const packet = composePacket();
      state.composer.packet = packet;
      state.composer.idempotencyKey = crypto.randomUUID();
      byId("compose-preview-content").replaceChildren(packetContent(packet));
      byId("compose-json").textContent = JSON.stringify(packet, null, 2);
      byId("compose-form").hidden = true;
      byId("compose-preview").hidden = false;
      byId("compose-dialog").scrollTop = 0;
      byId("compose-submit").focus();
    } catch (error) { message("compose-error", errorText(error)); }
  }
  async function submitContribution() {
    if (state.submitBusy || !state.composer?.packet) return;
    state.submitBusy = true;
    byId("compose-submit").disabled = true;
    byId("compose-back").disabled = true;
    byId("compose-submit").textContent = t("Gönderiliyor…");
    message("compose-error", "");
    const composer = state.composer;
    try {
      const path = composer.card ? `/submissions/${encodeURIComponent(composer.card.id)}` : "/submissions";
      await request(path, {packet: composer.packet, expected_version: composer.card?.version || 0, idempotency_key: composer.idempotencyKey});
      state.composer = null;
      byId("compose-dialog").close();
      message("community-status", t("Katkı incelemeye gönderildi."));
      selectTab("mine");
    } catch (error) {
      message("compose-error", errorText(error));
      if (error.status === 401 || error.status === 403) byId("compose-error").append(button(t("Giriş yap"), openLogin, "button secondary"));
    } finally { state.submitBusy = false; byId("compose-submit").disabled = false; byId("compose-back").disabled = false; byId("compose-submit").textContent = t("İncelemeye gönder"); }
  }
  async function withdraw(card, trigger) {
    if (trigger.disabled || !window.confirm(t("Katkıyı geri çekmek istiyor musun? Yayımlanmış sürümü de okuyuculardan kaldırılır."))) return;
    trigger.disabled = true;
    message("community-error", "");
    try { await request(`/submissions/${encodeURIComponent(card.id)}/withdraw`, {expected_version: card.version}); message("community-status", t("Katkı geri çekildi.")); loadRecords(); }
    catch (error) { message("community-error", errorText(error)); }
    finally { trigger.disabled = false; }
  }
  async function decide(decision) {
    if (state.reviewBusy || !state.detail) return;
    const note = byId("review-note").value.trim();
    message("review-error", "");
    if (["reject", "hide"].includes(decision) && !note) { message("review-error", t("Bu karar için bir gerekçe ekle.")); return; }
    if (!window.confirm(t(decision === "publish" ? "Bu katkıyı şimdi yayımlamak istiyor musun?" : decision === "hide" ? "Bu katkıyı yayından kaldırmak istiyor musun?" : "Bu katkıyı reddetmek istiyor musun?"))) return;
    state.reviewBusy = true;
    byId("review-publish").disabled = true;
    byId("review-reject").disabled = true;
    byId("review-hide").disabled = true;
    byId("review-note").disabled = true;
    try {
      if (decision === "hide") await request(`/submissions/${encodeURIComponent(state.detail.id)}/hide`, {expected_version: state.detail.moderation_version, note});
      else await request(`/submissions/${encodeURIComponent(state.detail.id)}/decision`, {decision, expected_version: state.detail.version, note});
      byId("card-dialog").close();
      state.detail = null;
      message("community-status", t("İnceleme kararı kaydedildi."));
      loadRecords();
    } catch (error) { message("review-error", errorText(error)); }
    finally { state.reviewBusy = false; byId("review-publish").disabled = false; byId("review-reject").disabled = false; byId("review-hide").disabled = false; byId("review-note").disabled = false; }
  }
  function closeDialog(id) {
    if (id === "login-dialog" && state.loginBusy) return;
    if (id === "compose-dialog") {
      if (state.submitBusy) return;
      if (state.composer && snapshot() !== state.composerSnapshot && !window.confirm(t("Kaydedilmemiş değişikliklerin var. Bu taslağı bırakmak istiyor musun?"))) return;
      state.composer = null;
    }
    if (id === "card-dialog" && state.reviewBusy) return;
    if (id === "login-dialog") byId("login-password").value = "";
    byId(id).close();
  }
  async function initialize() {
    try {
      const [, labels] = await Promise.all([loadSession(), UI?.loadTopicLabels?.()]);
      state.topicLabels = labels || {};
      await loadRecords();
      const id = publicCardId(window.location?.search || "");
      if (id) await openCard({id}, "read", byId("community-main"), true, publicCardVersion(window.location.search));
    }
    catch (error) {
      byId("community-count").textContent = t("Topluluk başlatılamadı.");
      byId("community-list").setAttribute("aria-busy", "false");
      message("community-error", errorText(error));
      byId("community-error").append(button(t("Yeniden dene"), async event => { event.currentTarget.disabled = true; await initialize(); }, "button secondary"));
    }
  }
  document.querySelectorAll("[data-tab]").forEach(tab => {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
    tab.addEventListener("keydown", event => {
      const tabs = [...document.querySelectorAll("[data-tab]")].filter(item => !item.hidden);
      const index = tabs.indexOf(tab);
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); selectTab(tabs[next].dataset.tab, true); }
    });
  });
  document.querySelectorAll("[data-close]").forEach(trigger => trigger.addEventListener("click", () => closeDialog(trigger.dataset.close)));
  document.querySelectorAll("dialog").forEach(dialog => dialog.addEventListener("cancel", event => { event.preventDefault(); closeDialog(dialog.id); }));
  byId("login-open").addEventListener("click", openLogin);
  byId("community-browse").addEventListener("click", () => selectTab("research", true));
  byId("card-dialog").addEventListener("close", () => { if (state.publicCardOpen) setPermalink(); state.publicCardOpen = false; });
  byId("community-start").addEventListener("click", () => openResearchApp());
  byId("login-form").addEventListener("submit", login);
  byId("logout").addEventListener("click", logout);
  byId("community-search").addEventListener("submit", event => { event.preventDefault(); loadRecords(); });
  byId("page-prev").addEventListener("click", () => loadRecords(Math.max(0, state.offset - PAGE_SIZE)));
  byId("page-next").addEventListener("click", () => loadRecords(state.offset + PAGE_SIZE));
  byId("contribute").addEventListener("click", () => { if (!state.session?.user) openLogin(); else byId("contribute-file").click(); });
  byId("contribute-file").addEventListener("change", importFile);
  byId("compose-form").addEventListener("submit", previewSubmission);
  byId("compose-back").addEventListener("click", () => { if (!state.submitBusy) { byId("compose-form").hidden = false; byId("compose-preview").hidden = true; byId("compose-name").focus(); } });
  byId("compose-submit").addEventListener("click", submitContribution);
  byId("review-publish").addEventListener("click", () => decide("publish"));
  byId("review-reject").addEventListener("click", () => decide("reject"));
  byId("review-hide").addEventListener("click", () => decide("hide"));
  window.addEventListener("beforeunload", event => { if (state.submitBusy || state.reviewBusy || (state.composer && snapshot() !== state.composerSnapshot)) { event.preventDefault(); event.returnValue = ""; } });
  initialize();
})();
