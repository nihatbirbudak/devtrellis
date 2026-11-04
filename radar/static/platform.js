// Gelişmeleri süzer, yerel proje ve araştırma kartlarını saklar, paylaşım dosyalarını açık önizlemeyle aktarır.
(() => {
  "use strict";
  const UI = globalThis.UfukUI;
  const t = (key, values = {}) => UI ? UI.t(key, values) : key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);

  const MAX_PACKET_BYTES = 32768;
  const PAGE_SIZE = 30;

  function safeURL(value) {
    try {
      const address = new URL(String(value || ""));
      return ["https:", "http:"].includes(address.protocol) && !address.username && !address.password ? address.href : null;
    } catch { return null; }
  }

  function parseTags(value) {
    const found = new Set();
    const tags = [];
    for (const part of String(value || "").split(",")) {
      const tag = part.trim();
      const key = tag.toLocaleLowerCase("tr");
      if (!tag || found.has(key)) continue;
      if (tag.length > 40) throw new Error(t("Her ilgi alanı veya etiket en fazla 40 karakter olabilir."));
      found.add(key);
      tags.push(tag);
    }
    if (tags.length > 12) throw new Error(t("En fazla 12 ilgi alanı veya etiket ekleyebilirsin."));
    return tags;
  }

  function parseSources(value) {
    const lines = String(value || "").split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    if (lines.length > 10) throw new Error(t("En fazla 10 kaynak ekleyebilirsin."));
    return lines.map((line, index) => {
      const separator = line.indexOf("|");
      const address = (separator < 0 ? line : line.slice(separator + 1)).trim();
      const title = (separator < 0 ? address : line.slice(0, separator)).trim();
      if (!title) throw new Error(t("{number}. kaynağa bir başlık ekle.", {number: index + 1}));
      if (!safeURL(address) || address.length > 2048) throw new Error(t("{number}. kaynak için geçerli bir http veya https bağlantısı gir. Bağlantıda kullanıcı adı veya parola bulunmamalı.", {number: index + 1}));
      if (title.length > 160) throw new Error(t("{number}. kaynak başlığı en fazla 160 karakter olabilir.", {number: index + 1}));
      return {title, url: address};
    });
  }

  function parsePacket(text) {
    if (new TextEncoder().encode(text).length > MAX_PACKET_BYTES) throw new Error(t("Paylaşım dosyası en fazla 32 KB olabilir."));
    let packet;
    try { packet = JSON.parse(text); } catch { throw new Error(t("Bu dosya geçerli bir JSON dosyası değil.")); }
    if (!packet || typeof packet !== "object" || Array.isArray(packet)) throw new Error(t("Paylaşım dosyası bir UFUK kaydı içermeli."));
    return packet;
  }

  function dateLabel(value) {
    if (!value) return t("Bilinmiyor");
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? t("Bilinmiyor") : new Intl.DateTimeFormat(UI?.language === "en" ? "en-US" : "tr-TR", {day: "numeric", month: "long", year: "numeric"}).format(date);
  }

  function entryMatches(entry, query) {
    const normalize = value => String(value || "").normalize("NFKC").toLocaleLowerCase("tr");
    const words = normalize(query).trim().split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const text = normalize([entry.title, entry.summary, entry.body, entry.author, ...(entry.tags || []), ...(entry.sources || []).map(source => source.title)].filter(Boolean).join(" "));
    return words.every(word => text.includes(word));
  }

  function errorMessage(error) {
    if (error?.status === 409) return t("Bu kayıt başka bir pencerede değişti. Taslağın burada duruyor; üzerine yazılmadı. Taslağını kopyalayıp sayfayı yeniledikten sonra kaydın son sürümünü açabilirsin.");
    if (error?.status === 403) return t("Oturum doğrulanamadı. Taslağını kopyalayıp sayfayı yeniledikten sonra yeniden dene.");
    return error?.message ? t(error.message) : t("İşlem tamamlanamadı. Yeniden deneyebilirsin.");
  }

  if (typeof module !== "undefined" && module.exports) module.exports = {safeURL, parseTags, parseSources, parsePacket, dateLabel, entryMatches, errorMessage};
  if (typeof document === "undefined") return;

  UI?.initialize();
  const byId = id => document.getElementById(id);
  const state = {token: "", settings: {interests: [], version: 0}, entries: [], ready: false, tab: "feed", feedGeneration: 0, feedOffset: 0, feedTotal: 0, feedBusy: false, editor: null, editorSnapshot: "", editorBusy: false, interestSnapshot: "", interestVersion: 0, interestBusy: false, share: null, shareBusy: false, importBusy: false};

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function button(text, handler, className = "text-button") {
    const node = element("button", className, text);
    node.type = "button";
    node.addEventListener("click", handler);
    return node;
  }

  function link(text, address, className = "") {
    const url = safeURL(address);
    if (!url) return element("span", className, text);
    const node = element("a", className, text);
    node.href = url;
    node.target = "_blank";
    node.rel = "noopener noreferrer";
    return node;
  }

  function message(id, text) {
    const node = byId(id);
    node.textContent = text || "";
    node.hidden = !text;
  }

  function announce(text) { byId("global-status").textContent = text; }

  async function request(path, body) {
    const options = {credentials: "same-origin", headers: {Accept: "application/json"}};
    if (body !== undefined) {
      if (!state.token) throw new Error(t("Çalışma alanı henüz hazır değil. Sayfayı yeniden yükleyip dene."));
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.headers["X-Radar-CSRF"] = state.token;
      options.body = JSON.stringify(body);
    }
    let response;
    try { response = await fetch(path, options); } catch { throw new Error(t("Yerel UFUK bağlantısı kurulamadı. Uygulamanın çalıştığını kontrol edip yeniden dene.")); }
    let data;
    try { data = await response.json(); } catch { throw new Error(t("UFUK beklenen yanıtı veremedi. Yeniden deneyebilirsin.")); }
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : typeof data.error === "string" ? data.error : t("İşlem tamamlanamadı. Alanları kontrol edip yeniden dene.");
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function renderCoverage(coverage = {}) {
    const count = Number(coverage.source_count || 0);
    const checked = Number(coverage.checked_sources || 0);
    byId("coverage").textContent = t("{count} kayıtlı kaynak · {checked} kaynakta kontrol denendi", {count, checked});
    byId("coverage-health").textContent = t("{success} başarılı kaynak · {failed} başarısız kaynak", {success: Number(coverage.successful_sources || 0), failed: Number(coverage.failed_sources || 0)});
    const attempts = coverage.last_checked_at ? t("Son kontrol denemesi: {date}.", {date: dateLabel(coverage.last_checked_at)}) : t("Henüz kaynak kontrolü yok. Kapsam, kayıtlı kaynaklarla sınırlıdır.");
    byId("coverage-date").textContent = coverage.last_success_at ? `${attempts} ${t("En son başarılı kontrol: {date}. Başarı ve güncellik kaynağa göre değişir.", {date: dateLabel(coverage.last_success_at)})}` : attempts;
  }

  function renderInterests() {
    const select = byId("feed-interest");
    const previous = select.value;
    select.replaceChildren();
    const all = element("option", "", t("Tüm ilgi alanları"));
    all.value = "";
    select.append(all);
    for (const interest of state.settings.interests || []) {
      const option = element("option", "", interest);
      option.value = interest;
      select.append(option);
    }
    if ((state.settings.interests || []).includes(previous)) select.value = previous;
  }

  function emptyState(title, description, actionText, action) {
    const empty = element("div", "empty-state");
    const mark = element("span", "empty-mark", "↗");
    mark.setAttribute("aria-hidden", "true");
    empty.append(mark, element("h3", "", title), element("p", "", description));
    if (actionText) empty.append(button(actionText, action, "button secondary"));
    return empty;
  }

  function tagsNode(tags) {
    const container = element("div", "tags");
    for (const tag of tags || []) container.append(element("span", "tag", tag));
    return container;
  }

  function feedCard(item) {
    const card = element("article", "card");
    const body = element("div", "card-body");
    const meta = element("div", "card-meta");
    if (item.topic_label || item.topic) meta.append(element("span", "badge", item.topic_label || item.topic));
    if (item.is_archive) meta.append(element("span", "badge archive", t("Arşiv kaydı")));
    const heading = element("h3");
    heading.append(link(item.title || t("Başlıksız gelişme"), item.url));
    body.append(meta, heading);
    if (item.excerpt) body.append(element("p", "summary", item.excerpt));
    const source = element("div", "card-source");
    source.append(link(`${item.source_name || t("Kaynak")} ↗`, item.url));
    body.append(source);
    body.append(element("p", "entry-note", t("Yayın: {date}", {date: dateLabel(item.published_at)})));
    if (item.last_seen_at) body.append(element("p", "entry-note", t("Son görüldü: {date}", {date: dateLabel(item.last_seen_at)})));
    card.append(body);
    return card;
  }

  function pagination() {
    byId("feed-pagination").hidden = state.feedTotal <= PAGE_SIZE;
    byId("feed-prev").disabled = state.feedBusy || state.feedOffset <= 0;
    byId("feed-next").disabled = state.feedBusy || state.feedOffset + PAGE_SIZE >= state.feedTotal;
    byId("feed-page").textContent = `${Math.floor(state.feedOffset / PAGE_SIZE) + 1} / ${Math.max(1, Math.ceil(state.feedTotal / PAGE_SIZE))}`;
  }

  async function loadFeed(offset = 0) {
    const generation = ++state.feedGeneration;
    state.feedBusy = true;
    pagination();
    byId("feed-list").setAttribute("aria-busy", "true");
    byId("feed-count").textContent = t("Gelişmeler yükleniyor…");
    message("feed-error", "");
    const params = new URLSearchParams({query: byId("feed-query").value.trim(), interest: byId("feed-interest").value, offset: String(offset), limit: String(PAGE_SIZE)});
    try {
      const data = await request(`/api/platform/feed?${params}`);
      if (generation !== state.feedGeneration) return;
      state.feedOffset = data.offset;
      state.feedTotal = data.total;
      const list = byId("feed-list");
      list.replaceChildren();
      const items = data.items || [];
      items.forEach(item => list.append(feedCard(item)));
      if (!items.length) {
        const filtering = Boolean(byId("feed-query").value.trim() || byId("feed-interest").value);
        list.append(emptyState(filtering ? t("Bu aramada kayıt bulunamadı.") : t("İlk gelişmelerin için bir başlangıç."), filtering ? t("Başka bir sözcük dene veya ilgi alanı filtresini kaldır. Arama yalnız bu bilgisayardaki kayıtları kapsar.") : t("Kaynaklar henüz toplanmamış olabilir. Ana çalışma alanından kaynaklarını inceleyip toplama işlemini başlatabilirsin."), filtering ? t("Filtreleri temizle") : t("Ana çalışma alanına git"), () => {
          if (filtering) { byId("feed-query").value = ""; byId("feed-interest").value = ""; loadFeed(); }
          else window.location.assign("/");
        }));
      }
      byId("feed-count").textContent = data.total ? t("{total} kayıt · {start}–{end} gösteriliyor", {total: data.total, start: data.offset + 1, end: data.offset + items.length}) : t("0 kayıt");
    } catch (error) {
      if (generation !== state.feedGeneration) return;
      byId("feed-list").replaceChildren();
      byId("feed-count").textContent = t("Gelişmeler alınamadı.");
      message("feed-error", errorMessage(error));
      byId("feed-error").append(button(t("Yeniden dene"), () => loadFeed(offset), "button secondary"));
    } finally {
      if (generation === state.feedGeneration) {
        state.feedBusy = false;
        byId("feed-list").setAttribute("aria-busy", "false");
        pagination();
      }
    }
  }

  function upsertEntry(entry) {
    state.entries = [entry, ...state.entries.filter(record => record.id !== entry.id)];
    renderEntries();
  }

  function renderEntries() {
    for (const kind of ["project", "research"]) {
      const list = byId(`${kind}-list`);
      list.replaceChildren();
      const archived = byId(`${kind}-archived`).checked;
      const query = byId(`${kind}-query`).value.trim();
      const kindEntries = state.entries.filter(entry => entry.kind === kind);
      const entries = kindEntries.filter(entry => (archived || !entry.archived) && entryMatches(entry, query));
      entries.sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
      entries.forEach(entry => list.append(entryCard(entry)));
      if (!entries.length) {
        const project = kind === "project";
        if (query) {
          list.append(emptyState(t("Bu aramada kayıt bulunamadı."), t("Başlık, açıklama, ayrıntılar, etiketler, yazar ve kaynak başlıklarında başka sözcükler dene. Arşivlenenler yalnız ilgili seçenek açıkken aranır."), t("Aramayı temizle"), () => {
            byId(`${kind}-query`).value = "";
            renderEntries();
            byId(`${kind}-query`).focus();
          }));
        } else if (kindEntries.length && !archived) {
          list.append(emptyState(t("Kayıtların arşivde."), t("Arşivlediğin kayıtları görüntüleyebilir ve istediğini yeniden etkinleştirebilirsin."), t("Arşivlenenleri göster"), () => {
            byId(`${kind}-archived`).checked = true;
            renderEntries();
          }));
        } else {
          list.append(emptyState(project ? t("İlk projen için yer hazır.") : t("Araştırmaların burada biriksin."), project ? t("Bir proje kartıyla ne geliştirdiğini anlat. Burada saklayabilir, hazır olduğunda paylaşım dosyası oluşturabilirsin.") : t("Kendi kaynaklı araştırmanı ekle veya birinin paylaştığı UFUK dosyasını önizleyerek içeri al. Katkı yapmak zorunlu değil."), project ? t("Proje kartı oluştur") : t("Araştırma ekle"), () => openEditor(kind)));
        }
      }
    }
  }

  function entryCard(entry) {
    const card = element("article", "card");
    const body = element("div", "card-body");
    const meta = element("div", "card-meta");
    meta.append(element("span", `badge${entry.origin === "imported" ? " imported" : ""}`, entry.origin === "imported" ? t("İçe alınan katkı") : t("Yerel kayıt")));
    if (entry.archived) meta.append(element("span", "badge archive", t("Arşivlendi")));
    const heading = element("h3", "", entry.title);
    body.append(meta, heading, element("p", "summary", entry.summary));
    if (entry.tags?.length) body.append(tagsNode(entry.tags));
    body.append(element("p", "entry-note", t("Güncellendi: {date}", {date: dateLabel(entry.updated_at)})));
    if (entry.provenance) {
      body.append(element("p", "entry-note", t("Kaynak araştırmanın modeli: {model}", {model: !entry.provenance.model || entry.provenance.model === "unknown" ? t("Model kaydedilmemiş") : entry.provenance.model})));
      body.append(element("p", "entry-note", entry.provenance.generated_at ? t("Araştırma tarihi: {date}", {date: dateLabel(entry.provenance.generated_at)}) : t("Üretim tarihi kaydedilmemiş")));
    }
    if (entry.origin === "imported") body.append(element("p", "entry-note", t("Yazar kimliği ve içerik doğrulanmadı.")));
    const actions = element("div", "card-actions");
    actions.append(button(t("Oku"), () => openDetail(entry)));
    if (entry.origin === "local") actions.append(button(t("Düzenle"), () => openEditor(entry.kind, entry)));
    actions.append(button(t("Paylaşım dosyası"), event => openShare(entry, event.currentTarget)));
    if (entry.kind === "research") {
      const research = element("a", "text-button", t("Yeniden araştır")); research.href = `/?research_entry=${encodeURIComponent(entry.id)}`; actions.append(research);
    }
    actions.append(button(entry.archived ? t("Arşivden çıkar") : t("Arşivle"), event => archiveEntry(entry, event.currentTarget), "text-button archive-action"));
    card.append(body, actions);
    return card;
  }

  async function refreshRecords() {
    const data = await request("/api/platform");
    state.entries = data.entries || [];
    state.settings = data.settings;
    state.token = data.csrf_token;
    renderEntries();
    renderInterests();
    renderCoverage(data.coverage);
  }

  async function archiveEntry(entry, trigger) {
    if (trigger.disabled) return;
    trigger.disabled = true;
    message("global-error", "");
    try {
      const updated = await request(`/api/platform/entries/${encodeURIComponent(entry.id)}/archive`, {archived: !entry.archived, expected_version: entry.version});
      upsertEntry(updated);
      announce(updated.archived ? t("Kayıt arşivlendi. Arşivlenenleri göstererek geri alabilirsin.") : t("Kayıt arşivden çıkarıldı."));
    } catch (error) {
      message("global-error", error.status === 409 ? t("Kayıt başka bir pencerede değişti; arşiv işlemi yapılmadı. Son sürümü yükleyip yeniden deneyebilirsin.") : errorMessage(error));
      if (error.status === 409) byId("global-error").append(button(t("Son kayıtları yükle"), async event => {
        event.currentTarget.disabled = true;
        try { await refreshRecords(); message("global-error", ""); } catch (refreshError) { message("global-error", errorMessage(refreshError)); }
      }, "button secondary"));
    } finally { trigger.disabled = false; }
  }

  function selectTab(tab, focus = false) {
    state.tab = tab;
    for (const item of document.querySelectorAll("[data-tab]")) {
      const selected = item.dataset.tab === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
      byId(`panel-${item.dataset.tab}`).hidden = !selected;
      if (selected && focus) item.focus();
    }
  }

  function editorData() {
    return {kind: state.editor.kind, title: byId("entry-name").value.trim(), summary: byId("entry-summary").value.trim(), body: byId("entry-body").value.trim(), url: byId("entry-url").value.trim(), tags: parseTags(byId("entry-tags").value), sources: parseSources(byId("entry-sources").value), author: byId("entry-author").value.trim(), license: byId("entry-license").value || null, ...(state.editor.provenance ? {provenance: state.editor.provenance} : {})};
  }

  function editorSnapshot() {
    return JSON.stringify(["name", "summary", "body", "url", "tags", "sources", "author", "license"].map(key => byId(`entry-${key}`).value));
  }

  function openEditor(kind, entry = null) {
    state.editor = {kind, id: entry?.id || null, version: entry?.version, provenance: entry?.provenance || null};
    byId("entry-form").reset();
    byId("entry-title").textContent = `${entry ? t("Düzenle: ") : t("Yeni ")}${kind === "project" ? t("proje kartı") : t("araştırma")}`;
    byId("entry-share-help").textContent = kind === "project" ? t("Paylaşım dosyası için bir proje bağlantısı, yazar adı ve metin izni gerekir. Taslağı bunlar olmadan da saklayabilirsin.") : t("Paylaşım dosyası için en az bir kaynak, yazar adı ve metin izni gerekir. Taslağı bunlar olmadan da saklayabilirsin.");
    if (entry?.provenance) byId("entry-share-help").textContent += ` ${t("Model ve üretim tarihi özgün araştırmaya aittir. Kartın içeriğini değiştirirsen kanıt durumu denetlenmemiş olarak işaretlenir.")}`;
    byId("entry-name").value = entry?.title || "";
    byId("entry-summary").value = entry?.summary || "";
    byId("entry-body").value = entry?.body || "";
    byId("entry-url").value = entry?.url || "";
    byId("entry-tags").value = (entry?.tags || []).join(", ");
    byId("entry-sources").value = (entry?.sources || []).map(source => `${source.title} | ${source.url}`).join("\n");
    byId("entry-author").value = entry?.author || "";
    byId("entry-license").value = entry?.license || "";
    state.editorSnapshot = editorSnapshot();
    message("entry-error", "");
    byId("entry-dialog").showModal();
    byId("entry-name").focus();
  }

  async function saveEntry(event) {
    event.preventDefault();
    if (state.editorBusy || !byId("entry-form").reportValidity()) return;
    message("entry-error", "");
    let payload;
    try {
      payload = editorData();
      if (!payload.title || !payload.summary) throw new Error(t("Başlık ve kısa açıklama boş olamaz."));
      if (payload.url && !safeURL(payload.url)) throw new Error(t("Geçerli bir http veya https bağlantısı gir. Bağlantıda kullanıcı adı veya parola bulunmamalı."));
    } catch (error) { message("entry-error", errorMessage(error)); return; }
    state.editorBusy = true;
    byId("entry-fields").disabled = true;
    byId("entry-save").disabled = true;
    byId("entry-save").textContent = t("Kaydediliyor…");
    try {
      const path = state.editor.id ? `/api/platform/entries/${encodeURIComponent(state.editor.id)}` : "/api/platform/entries";
      if (state.editor.id) payload.expected_version = state.editor.version;
      const entry = await request(path, payload);
      upsertEntry(entry);
      selectTab(entry.kind);
      byId("entry-dialog").close();
      state.editor = null;
      announce(t("Kayıt bu bilgisayarda saklandı. Henüz paylaşılmadı."));
    } catch (error) { message("entry-error", errorMessage(error)); }
    finally {
      state.editorBusy = false;
      byId("entry-fields").disabled = false;
      byId("entry-save").disabled = false;
      byId("entry-save").textContent = t("Yerel olarak kaydet");
    }
  }

  function detailContent(entry, titleId) {
    const content = element("div");
    const title = element("h2", "detail-title", entry.title);
    if (titleId) title.id = titleId;
    content.append(title, element("p", "detail-summary", entry.summary));
    if (entry.origin === "imported") content.append(element("p", "notice", t("İçe alınan katkı: yazar kimliği ve içerik doğrulanmadı. Bulguları kaynaklarından kontrol edebilirsin.")));
    if (entry.tags?.length) content.append(tagsNode(entry.tags));
    const meta = element("dl", "detail-meta");
    const facts = [[t("Yazar"), entry.author || t("Belirtilmedi")], [t("Oluşturuldu"), dateLabel(entry.created_at)], [t("Güncellendi"), dateLabel(entry.updated_at)], [t("Metin izni"), entry.license || t("Henüz seçilmedi")]];
    if (entry.imported_at) facts.push([t("İçe alındı"), dateLabel(entry.imported_at)]);
    for (const [name, value] of facts) meta.append(element("dt", "", name), element("dd", "", value));
    content.append(meta);
    if (entry.provenance) {
      const provenance = element("section", "provenance-panel"); provenance.append(element("h3", "", t("Araştırmanın kökeni")));
      const facts = element("dl", "detail-meta");
      for (const [label, value] of UI?.provenanceFacts?.(entry.provenance, t, dateLabel) || []) facts.append(element("dt", "", label), element("dd", "", value));
      provenance.append(facts, element("p", "field-help", t("Bu bilgiler kaynak araştırmaya aittir; kart metni sonradan düzenlenmiş olabilir. İçe alınan kayıtların model ve kaynak bilgileri yazar beyanıdır.")), element("p", "field-help", t("Modelin markası veya fiyatı doğruluk kanıtı değildir; kaynakları, tarihi ve raporun sınırlarını kontrol et.")));
      if (entry.provenance.modified) provenance.append(element("p", "notice", t("Kart içeriği özgün rapordan sonra değiştirildi; yeniden kanıt denetimi yapılmadı.")));
      content.append(provenance);
    }
    if (entry.url) content.append(link(t("Proje / araştırma bağlantısı ↗"), entry.url));
    if (entry.body) content.append(element("div", "detail-body", entry.body));
    if (entry.sources?.length) {
      content.append(element("h3", "source-heading", t("Kaynaklar")));
      const sources = element("ul", "source-list");
      for (const source of entry.sources) {
        const item = element("li");
        item.append(link(`${source.title} ↗`, source.url));
        sources.append(item);
      }
      content.append(sources);
    }
    return content;
  }

  function openDetail(entry) {
    byId("detail-content").replaceChildren(detailContent(entry, "detail-title"));
    byId("detail-dialog").showModal();
  }

  async function openShare(entry, trigger) {
    if (trigger.disabled) return;
    trigger.disabled = true;
    message("global-error", "");
    try {
      const result = await request(`/api/platform/entries/${encodeURIComponent(entry.id)}/preview`, {expected_version: entry.version});
      showShare(result, "export", {id: entry.id, version: entry.version});
    } catch (error) {
      if (!entry.license && entry.origin === "local") {
        openEditor(entry.kind, entry);
        message("entry-error", t("Paylaşım dosyası için önce metnin paylaşım iznini seçip kaydet. Taslağını izin seçmeden de yerelde saklayabilirsin."));
        byId("entry-license").focus();
      } else {
        message("global-error", error.status === 409 ? t("Kayıt başka bir pencerede değişti. Paylaşmadan önce kaydın son sürümünü yükle.") : errorMessage(error));
        if (error.status === 409) byId("global-error").append(button(t("Son kayıtları yükle"), async event => {
          event.currentTarget.disabled = true;
          try { await refreshRecords(); message("global-error", ""); } catch (refreshError) { message("global-error", errorMessage(refreshError)); }
        }, "button secondary"));
      }
    } finally { trigger.disabled = false; }
  }

  function showShare(result, direction, record = null) {
    state.share = {packet: result.packet, direction, record};
    const importing = direction === "import";
    byId("share-direction").textContent = importing ? t("İÇE ALMA ÖNİZLEMESİ") : t("PAYLAŞIM ÖNİZLEMESİ");
    byId("share-title").textContent = importing ? t("Kütüphanene ne eklenecek?") : t("Dosyada ne yer alacak?");
    byId("share-notice").textContent = importing ? t("Bu dosya henüz kaydedilmedi. Yazar kimliği ve içerik doğrulanmış sayılmaz.") : t("Yalnız aşağıdaki içerik indirilir. Dosya oluşturmak internette yayımlamak anlamına gelmez.");
    byId("share-content").replaceChildren(detailContent(result.packet));
    byId("share-json").textContent = JSON.stringify(result.packet, null, 2);
    byId("share-confirm").textContent = importing ? t("Kütüphaneme ekle") : t("JSON dosyasını indir");
    byId("share-confirm").disabled = false;
    message("share-error", "");
    byId("share-dialog").querySelector("details").open = false;
    byId("share-dialog").showModal();
  }

  function setImportBusy(busy) {
    state.importBusy = busy;
    for (const trigger of document.querySelectorAll(".import-open")) trigger.disabled = busy || !state.ready;
  }

  async function previewImport(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || state.importBusy) return;
    message("global-error", "");
    setImportBusy(true);
    try {
      if (file.size > MAX_PACKET_BYTES) throw new Error(t("Paylaşım dosyası en fazla 32 KB olabilir."));
      const packet = parsePacket(await file.text());
      const result = await request("/api/platform/import/preview", {packet});
      showShare(result, "import");
    } catch (error) { message("global-error", errorMessage(error)); }
    finally { setImportBusy(false); }
  }

  async function confirmShare() {
    if (!state.share || state.shareBusy) return;
    state.shareBusy = true;
    byId("share-confirm").disabled = true;
    message("share-error", "");
    try {
      if (state.share.direction === "import") {
        const result = await request("/api/platform/import", {packet: state.share.packet});
        upsertEntry(result.entry);
        selectTab(result.entry.kind);
        byId("share-dialog").close();
        announce(result.duplicate ? t("Bu kayıt kütüphanende zaten var. İkinci bir kopya eklenmedi.") : t("Dosya kütüphanene eklendi."));
        state.share = null;
      } else {
        if (new TextEncoder().encode(JSON.stringify({packet: state.share.packet})).length > MAX_PACKET_BYTES) throw new Error(t("Dosya 32 KB sınırını aşıyor. Metni kısaltıp yeniden deneyebilirsin."));
        const record = state.share.record;
        await request(`/api/platform/entries/${encodeURIComponent(record.id)}/preview`, {expected_version: record.version});
        const anchor = document.createElement("a");
        anchor.href = `/api/platform/entries/${encodeURIComponent(record.id)}/download?expected_version=${record.version}`;
        anchor.download = `${(UI?.brand || "Ufuk").toLowerCase()}-${state.share.packet.kind}-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        byId("share-notice").textContent = t("Dosyanın indirilmesi istendi. İstersen dosyayı başkalarına kendin iletebilirsin; UFUK bir sunucuya yüklemedi.");
        byId("share-confirm").textContent = t("Dosyayı yeniden indir");
      }
    } catch (error) { message("share-error", errorMessage(error)); }
    finally { state.shareBusy = false; byId("share-confirm").disabled = false; }
  }

  function openInterests() {
    byId("interest-input").value = (state.settings.interests || []).join(", ");
    state.interestSnapshot = byId("interest-input").value;
    state.interestVersion = state.settings.version;
    message("interest-error", "");
    byId("interest-dialog").showModal();
    byId("interest-input").focus();
  }

  function providerSnapshot() { return JSON.stringify(["select", "model", "language"].map(key => byId(`provider-${key}`).value)); }
  function providerAvailability() {
    const provider = state.provider?.providers?.find(item => item.id === byId("provider-select").value);
    const status = provider?.id === "nvidia" ? t(provider.available ? "Kimlik bilgisi hazır" : "NVIDIA API anahtarı gerekli") : t(provider?.available ? "CLI kullanılabilir" : "CLI bulunamadı");
    byId("provider-availability").textContent = provider ? `${provider.label}: ${status}` : "";
    byId("provider-current-model").textContent = t("Seçili model: {model}", {model: provider?.model || t("Yerel CLI ayarı / henüz bilinmiyor")});
    byId("provider-model").disabled = byId("provider-select").value !== "codex";
    byId("provider-nvidia-help").hidden = byId("provider-select").value !== "nvidia";
  }
  async function openProviders() {
    byId("provider-open").disabled = true;
    message("global-error", "");
    try {
      state.provider = await request("/api/providers");
      byId("provider-select").value = state.provider.deep_provider;
      byId("provider-model").value = state.provider.codex_model || "";
      byId("provider-language").value = state.provider.output_language;
      state.providerSnapshot = providerSnapshot();
      providerAvailability();
      message("provider-error", "");
      byId("provider-dialog").showModal();
    } catch (error) { message("global-error", errorMessage(error)); }
    finally { byId("provider-open").disabled = false; }
  }
  async function saveProviders(event) {
    event.preventDefault();
    if (state.providerBusy || !state.provider || !byId("provider-form").reportValidity()) return;
    const selected = byId("provider-select").value;
    const payload = {deep_provider: selected, codex_model: selected === "codex" ? byId("provider-model").value.trim() || null : null, output_language: byId("provider-language").value, expected_version: state.provider.version};
    state.providerBusy = true;
    byId("provider-fields").disabled = true;
    byId("provider-save").disabled = true;
    message("provider-error", "");
    try {
      state.provider = await request("/api/providers", payload);
      byId("provider-dialog").close();
      announce(t("Sağlayıcı ayarları kaydedildi."));
    } catch (error) { message("provider-error", errorMessage(error)); }
    finally { state.providerBusy = false; byId("provider-fields").disabled = false; byId("provider-save").disabled = false; providerAvailability(); }
  }

  async function saveInterests(event) {
    event.preventDefault();
    if (state.interestBusy) return;
    message("interest-error", "");
    let interests;
    try { interests = parseTags(byId("interest-input").value); }
    catch (error) { message("interest-error", errorMessage(error)); return; }
    state.interestBusy = true;
    byId("interest-fields").disabled = true;
    byId("interest-save").disabled = true;
    try {
      state.settings = await request("/api/platform/settings", {interests, expected_version: state.interestVersion});
      renderInterests();
      byId("interest-dialog").close();
      announce(t("İlgi alanların kaydedildi."));
      loadFeed();
    } catch (error) { message("interest-error", errorMessage(error)); }
    finally { state.interestBusy = false; byId("interest-fields").disabled = false; byId("interest-save").disabled = false; }
  }

  function closeDialog(id) {
    if (id === "provider-dialog") {
      if (state.providerBusy) return;
      if (state.provider && providerSnapshot() !== state.providerSnapshot && !window.confirm(t("Kaydedilmemiş değişikliklerin var. Bu taslağı bırakmak istiyor musun?"))) return;
    }
    if (id === "entry-dialog") {
      if (state.editorBusy) return;
      if (state.editor && editorSnapshot() !== state.editorSnapshot && !window.confirm(t("Kaydedilmemiş değişikliklerin var. Bu taslağı bırakmak istiyor musun?"))) return;
      state.editor = null;
    }
    if (id === "interest-dialog") {
      if (state.interestBusy) return;
      if (byId("interest-input").value !== state.interestSnapshot && !window.confirm(t("İlgi alanlarındaki değişiklikler kaydedilmedi. Değişiklikleri bırakmak istiyor musun?"))) return;
    }
    if (id === "share-dialog") {
      if (state.shareBusy) return;
      state.share = null;
    }
    byId(id).close();
  }

  async function initialize() {
    message("global-error", "");
    try {
      await refreshRecords();
      state.ready = true;
      for (const id of ["interest-open", "provider-open", "new-project", "new-research"]) byId(id).disabled = false;
      setImportBusy(false);
      const entryId = new URLSearchParams(window.location.search).get("entry");
      const entry = state.entries.find(item => String(item.id) === entryId);
      if (entry) { selectTab(entry.kind); entry.origin === "local" ? openEditor(entry.kind, entry) : openDetail(entry); }
      else if (new URLSearchParams(window.location.search).get("providers") === "1") await openProviders();
      await loadFeed();
    } catch (error) {
      byId("coverage").textContent = t("Kaynak kapsamı alınamadı.");
      byId("feed-count").textContent = t("Çalışma alanı yüklenemedi.");
      byId("feed-list").setAttribute("aria-busy", "false");
      message("global-error", errorMessage(error));
      byId("global-error").append(button(t("Yeniden dene"), async event => { event.currentTarget.disabled = true; await initialize(); }, "button secondary"));
    }
  }

  document.querySelectorAll("[data-tab]").forEach(tab => {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
    tab.addEventListener("keydown", event => {
      const tabs = ["feed", "project", "research"];
      const current = tabs.indexOf(state.tab);
      let next;
      if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
      if (event.key === "ArrowLeft") next = (current + tabs.length - 1) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); selectTab(tabs[next], true); }
    });
  });
  document.querySelectorAll("[data-close]").forEach(trigger => trigger.addEventListener("click", () => closeDialog(trigger.dataset.close)));
  document.querySelectorAll("dialog").forEach(dialog => dialog.addEventListener("cancel", event => { event.preventDefault(); closeDialog(dialog.id); }));
  document.querySelectorAll(".import-open").forEach(trigger => trigger.addEventListener("click", () => byId("import-file").click()));
  byId("feed-form").addEventListener("submit", event => { event.preventDefault(); loadFeed(); });
  byId("feed-interest").addEventListener("change", () => loadFeed());
  byId("feed-prev").addEventListener("click", () => loadFeed(Math.max(0, state.feedOffset - PAGE_SIZE)));
  byId("feed-next").addEventListener("click", () => loadFeed(state.feedOffset + PAGE_SIZE));
  byId("new-project").addEventListener("click", () => openEditor("project"));
  byId("new-research").addEventListener("click", () => openEditor("research"));
  byId("project-archived").addEventListener("change", renderEntries);
  byId("research-archived").addEventListener("change", renderEntries);
  byId("project-query").addEventListener("input", renderEntries);
  byId("research-query").addEventListener("input", renderEntries);
  byId("entry-form").addEventListener("submit", saveEntry);
  byId("interest-open").addEventListener("click", openInterests);
  byId("interest-form").addEventListener("submit", saveInterests);
  byId("provider-open").addEventListener("click", openProviders);
  byId("provider-select").addEventListener("change", providerAvailability);
  byId("provider-form").addEventListener("submit", saveProviders);
  byId("import-file").addEventListener("change", previewImport);
  byId("share-confirm").addEventListener("click", confirmShare);
  window.addEventListener("beforeunload", event => {
    const editorDirty = state.editor && editorSnapshot() !== state.editorSnapshot;
    const interestDirty = byId("interest-dialog").open && byId("interest-input").value !== state.interestSnapshot;
    const providerDirty = byId("provider-dialog").open && state.provider && providerSnapshot() !== state.providerSnapshot;
    if (editorDirty || interestDirty || providerDirty || state.providerBusy || state.editorBusy || state.interestBusy || state.shareBusy) { event.preventDefault(); event.returnValue = ""; }
  });
  initialize();
})();
