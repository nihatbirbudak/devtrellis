/* Koleksiyonları, kişisel seçkiyi ve kaynak tercihlerini taslakları koruyarak yönetir. */
(() => {
  "use strict";
  const UI = globalThis.UfukUI;
  const t = (key, values = {}) => UI ? UI.t(key, values) : key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);


  window.UFUKWorkspaceFactory = function UFUKWorkspaceFactory(context) {
    const {element, icon, post, request, refreshAfterMutation, toast, state, topics,
      formatDate, changeView, render, invalidatePool} = context;
    let currentDialog = null;
    let operations = 0;
    let sequence = 0;
    const priorityLabels = [t("Seçkide gösterme"), t("Düşük"), t("Az"), t("Dengeli"), t("Yüksek"), t("Öncelikli")];
    const readLabels = {all: t("Okundu ve okunmadı"), unread: t("Yalnız okunmayanlar"), read: t("Yalnız okunanlar")};
    const sortLabels = {relevance: t("İlgime göre"), newest: t("Yeniden eskiye"), oldest: t("Eskiden yeniye")};
    const personal = () => state.data?.personal || {};
    const collections = () => personal().collections || [];
    const searches = () => personal().saved_searches || [];
    const sources = () => state.data?.sources || [];
    const pathId = value => encodeURIComponent(String(value));
    const busy = () => Boolean(currentDialog?.dialog.open || operations);
    window.addEventListener("beforeunload", event => {
      if (currentDialog?.dirty() || currentDialog?.submitting) {
        event.preventDefault();
        event.returnValue = "";
      }
    });

    function button(text, action, style = "secondary", symbol = null) {
      const node = element("button", `button button-${style} workspace-button`);
      node.type = "button";
      if (symbol) node.append(icon(symbol));
      node.append(element("span", "", text));
      node.addEventListener("click", action);
      return node;
    }

    function input(value = "", options = {}) {
      const node = element(options.multiline ? "textarea" : "input", "workspace-input");
      if (!options.multiline) node.type = options.type || "text";
      node.value = value == null ? "" : String(value);
      if (options.multiline) node.rows = options.rows || 3;
      for (const key of ["required", "minLength", "maxLength", "min", "max", "step", "placeholder", "autocomplete"]) {
        if (options[key] !== undefined) node[key] = options[key];
      }
      return node;
    }

    function select(options, selected = "") {
      const node = element("select", "workspace-input");
      for (const [value, label] of options) {
        const option = element("option", "", label);
        option.value = String(value);
        node.append(option);
      }
      node.value = String(selected ?? "");
      return node;
    }

    function field(label, control, help = "") {
      const wrapper = element("div", "workspace-field");
      const id = `workspace-field-${++sequence}`;
      control.id = id;
      const caption = element("label", "workspace-label", label);
      caption.htmlFor = id;
      wrapper.append(caption, control);
      if (help) {
        const hint = element("p", "workspace-help", help);
        hint.id = `${id}-help`;
        control.setAttribute("aria-describedby", hint.id);
        wrapper.append(hint);
      }
      return wrapper;
    }

    function heading(title, subtitle, action) {
      const wrapper = element("div", "workspace-heading");
      const copy = element("div");
      copy.append(element("h3", "", title), element("p", "workspace-help", subtitle));
      wrapper.append(copy);
      if (action) wrapper.append(action);
      return wrapper;
    }

    function empty(title, text, action = null) {
      const wrapper = element("div", "workspace-empty");
      wrapper.append(element("h4", "", title), element("p", "workspace-help", text));
      if (action) wrapper.append(action);
      return wrapper;
    }

    function setPersonalRecord(key, record) {
      if (!state.data || !record) return;
      state.data.personal ||= {};
      if (key === "settings") state.data.personal.settings = record;
      else {
        const rows = state.data.personal[key] || [];
        const idKey = key === "read_states" ? "item_id" : "id";
        const index = rows.findIndex(row => String(row[idKey]) === String(record[idKey]));
        if (index < 0) rows.push(record);
        else rows[index] = record;
        state.data.personal[key] = rows;
      }
      const stats = state.data.personal.stats;
      if (stats) {
        stats.collection_count = collections().filter(row => !row.archived).length;
        stats.saved_search_count = searches().filter(row => !row.archived).length;
        if (key === "read_states") {
          stats.read_count = (personal().read_states || []).filter(row => row.read).length;
          if (Number.isInteger(state.data.stats?.total)) stats.unread_count = Math.max(0, state.data.stats.total - stats.read_count);
        }
      }
    }

    function setSource(source) {
      if (!source?.id || !state.data) return;
      const index = sources().findIndex(row => row.id === source.id);
      if (index < 0) state.data.sources.push(source);
      else state.data.sources[index] = source;
    }

    async function updateView() {
      invalidatePool();
      try {
        const refreshed = await refreshAfterMutation();
        if (refreshed === false) render();
        return refreshed !== false;
      } catch {
        render();
        return false;
      }
    }

    function focusable(node) {
      if (!node?.isConnected || node.disabled) return false;
      for (let parent = node; parent; parent = parent.parentElement) {
        if (parent.hidden || (parent.tagName === "DETAILS" && !parent.open)) return false;
      }
      return true;
    }

    function restoreActionFocus(node, view, wasFocused) {
      const active = document.activeElement;
      if (!wasFocused || state.view !== view || (active !== document.body && active !== node && active?.isConnected)) return;
      const replacement = node.dataset.focusKey && [...document.querySelectorAll("[data-focus-key]")].find(control => control.dataset.focusKey === node.dataset.focusKey && focusable(control));
      const reader = document.getElementById("reader-dialog");
      (replacement || (focusable(node) ? node : null) || (reader?.open ? reader.querySelector("button") : document.getElementById("view-title")))?.focus({preventScroll: true});
    }

    async function directMutation(node, action, apply, message) {
      if (operations) return;
      const view = state.view;
      const wasFocused = document.activeElement === node;
      operations += 1;
      node.disabled = true;
      try {
        let result;
        try { result = await action(); }
        catch (error) {
          if (error.status === 409) {
            const refreshed = await updateView();
            toast(refreshed ? t("Bu kayıt başka bir pencerede değişti. Güncel bilgiler yüklendi; işlemi yeniden seçebilirsin.") : t("Bu kayıt başka bir pencerede değişti. Güncel bilgiler alınamadı; görünümü yenileyip yeniden dene."), true);
          } else toast(error.message, true);
          return;
        }
        apply(result);
        const refreshed = await updateView();
        toast(`${typeof message === "function" ? message(result) : message}${refreshed ? "" : t(" Görünüm yenilenemedi; kaydın saklandı.")}`);
      } finally {
        operations -= 1;
        node.disabled = false;
        restoreActionFocus(node, view, wasFocused);
      }
    }

    function dialog(title, description, submitText = t("Kaydet")) {
      if (currentDialog) {
        currentDialog.dialog.focus();
        return null;
      }
      const opener = document.activeElement;
      const node = element("dialog", "workspace-dialog");
      const titleId = `workspace-title-${++sequence}`;
      const descriptionId = `${titleId}-description`;
      node.setAttribute("aria-labelledby", titleId);
      node.setAttribute("aria-describedby", descriptionId);
      const header = element("div", "workspace-dialog-header");
      const titleNode = element("h2", "", title);
      titleNode.id = titleId;
      const closeButton = button(t("Kapat"), () => session.requestClose(), "secondary", "close");
      closeButton.setAttribute("aria-label", t("{title} penceresini kapat", {title}));
      header.append(titleNode, closeButton);
      const form = element("form", "workspace-form");
      const introduction = element("p", "workspace-introduction", description);
      introduction.id = descriptionId;
      const body = element("div", "workspace-form-body");
      const errorBox = element("div", "workspace-error");
      errorBox.setAttribute("role", "alert");
      errorBox.tabIndex = -1;
      errorBox.hidden = true;
      const comparison = element("div", "workspace-comparison");
      comparison.hidden = true;
      const discard = element("div", "workspace-discard");
      discard.setAttribute("role", "alert");
      discard.hidden = true;
      const footer = element("div", "workspace-form-footer");
      const cancel = button(t("Vazgeç"), () => session.requestClose());
      const save = button(submitText, () => {}, "primary", "check");
      save.type = "submit";
      save.hidden = !submitText;
      footer.append(cancel, save);
      form.append(introduction, body, errorBox, comparison, discard, footer);
      node.append(header, form);
      let baseline = "";
      let disabledControls = [];
      const fingerprint = () => JSON.stringify([...body.querySelectorAll("input,select,textarea")].map(control =>
        [control.id, control.type === "checkbox" ? control.checked : control.value]));
      const session = {
        dialog: node, form, body, save, errorBox, comparison, submitting: false, conflicted: false,
        markClean() { baseline = fingerprint(); discard.hidden = true; },
        dirty() { return baseline !== fingerprint(); },
        setBusy(value) {
          session.submitting = value;
          node.setAttribute("aria-busy", String(value));
          if (value) {
            disabledControls = [...node.querySelectorAll("button,input,select,textarea")].map(control => [control, control.disabled]);
            disabledControls.forEach(([control]) => { control.disabled = true; });
          } else {
            disabledControls.forEach(([control, disabled]) => { if (control.isConnected) control.disabled = disabled; });
            disabledControls = [];
            if (session.conflicted) save.disabled = true;
          }
        },
        error(message) {
          errorBox.textContent = message;
          errorBox.hidden = false;
          if (node.open) errorBox.focus({preventScroll: true});
        },
        close() {
          if (session.submitting) return;
          currentDialog = null;
          node.close();
          node.remove();
          if (opener?.isConnected) opener.focus();
          else {
            const replacement = opener?.dataset.focusKey && [...document.querySelectorAll("[data-focus-key]")].find(control => control.dataset.focusKey === opener.dataset.focusKey && !control.disabled);
            const reader = document.getElementById("reader-dialog");
            (replacement || (reader?.open ? reader.querySelector("button") : document.getElementById("view-title")))?.focus();
          }
        },
        requestClose() {
          if (session.submitting) return;
          if (!session.dirty()) { session.close(); return; }
          discard.replaceChildren(element("p", "", t("Kaydedilmemiş değişikliklerin var. Bu pencereden çıkarsan taslak silinir.")));
          const actions = element("div", "workspace-actions");
          const keep = button(t("Düzenlemeye devam et"), () => { discard.hidden = true; (body.querySelector("input,select,textarea") || closeButton).focus(); });
          const leave = button(t("Kaydetmeden çık"), () => session.close());
          actions.append(keep, leave);
          discard.append(actions);
          discard.hidden = false;
          keep.focus();
        },
        open() {
          session.markClean();
          document.body.append(node);
          currentDialog = session;
          node.showModal();
          (body.querySelector("input,select,textarea") || closeButton).focus();
        }
      };
      node.addEventListener("cancel", event => { event.preventDefault(); session.requestClose(); });
      form.addEventListener("submit", event => {
        event.preventDefault();
        if (!session.submitting && !session.conflicted && !operations && form.reportValidity()) session.onSubmit?.();
      });
      return session;
    }

    async function saveDialog(session, action, apply, message, conflict = null, afterClose = null) {
      if (session.submitting || operations) return;
      operations += 1;
      session.setBusy(true);
      session.errorBox.hidden = true;
      let result;
      let failure;
      try { result = await action(); } catch (error) { failure = error; }
      if (failure) {
        session.setBusy(false);
        operations -= 1;
        session.error(failure.status === 409 ? t("Bu kayıt başka bir pencerede değişti. Taslağın burada korunuyor. Devam etmeden önce güncel kaydı karşılaştır.") : failure.message);
        if (failure.status === 409 && conflict) showConflict(session, conflict);
        return;
      }
      try {
        apply(result);
        session.markClean();
        const refreshed = await updateView();
        session.setBusy(false);
        session.close();
        toast(`${message}${refreshed ? "" : t(" Görünüm yenilenemedi; kaydın saklandı.")}`);
      } finally { session.setBusy(false); operations -= 1; }
      if (afterClose) afterClose(result);
    }

    function showConflict(session, {load, describe, rebase, fill}) {
      session.conflicted = true;
      session.save.disabled = true;
      session.comparison.hidden = false;
      session.comparison.replaceChildren();
      const compare = button(t("Güncel kaydı karşılaştır"), async () => {
        if (session.submitting) return;
        session.setBusy(true);
        let latest;
        let failure;
        try { latest = await load(); } catch (error) { failure = error; }
        session.setBusy(false);
        if (failure || !latest) {
          session.error(failure?.message || t("Güncel kayıt bulunamadı. Taslağın değiştirilmedi."));
          return;
        }
        const details = element("dl", "workspace-details");
        for (const [label, value] of describe(latest)) details.append(element("dt", "", label), element("dd", "", value));
        const actions = element("div", "workspace-actions");
        const accept = button(t("Taslağı koru, bu sürümle devam et"), () => {
          rebase(latest);
          session.conflicted = false;
          session.save.disabled = false;
          session.comparison.replaceChildren(element("p", "workspace-help", t("Taslağın korundu. Kaydet'e bastığında yukarıdaki değerler, karşılaştırdığın güncel sürümün yerine yazılacak.")));
          session.errorBox.hidden = true;
          session.save.focus();
        }, "subtle");
        const reload = button(t("Kayıtlı değerleri forma yükle"), () => {
          fill(latest);
          rebase(latest);
          session.conflicted = false;
          session.save.disabled = false;
          session.markClean();
          session.comparison.hidden = true;
          session.errorBox.hidden = true;
          session.body.querySelector("input,select,textarea")?.focus();
        });
        actions.append(accept, reload);
        session.comparison.replaceChildren(element("h3", "", t("Güncel kayıt")), details,
          element("p", "workspace-help", t("Formdaki taslak henüz değişmedi. Kayıtlı değerleri yüklemek, taslağın yerine bu değerleri koyar.")), actions);
      }, "secondary", "refresh");
      session.comparison.append(compare);
    }

    async function latestPersonalRecord(key, id = null) {
      const snapshot = await request("/api/personal");
      return id === null ? snapshot[key] : (snapshot[key] || []).find(row => row.id === id);
    }

    async function latestSource(id) {
      return request(`/api/sources/${pathId(id)}`);
    }

    function editCollection(record = null, afterClose = null) {
      const session = dialog(record ? t("Koleksiyonu düzenle") : t("Yeni koleksiyon"), t("Bir projeye, fikre ya da okumak istediğin konuya ait gelişmeleri birlikte tut."), record ? t("Değişiklikleri kaydet") : t("Koleksiyonu oluştur"));
      if (!session) return;
      let version = record?.version;
      const name = input(record?.name || "", {required: true, maxLength: 60, placeholder: t("Örn. Sera için yeni fikirler")});
      const description = input(record?.description || "", {multiline: true, maxLength: 300, placeholder: t("Bu koleksiyonda ne biriktiriyorsun?")});
      session.body.append(field(t("Koleksiyon adı"), name, t("En fazla 60 karakter.")), field(t("Kısa açıklama"), description, t("İsteğe bağlı · En fazla 300 karakter.")));
      const fill = latest => { name.value = latest.name; description.value = latest.description || ""; };
      session.onSubmit = () => saveDialog(session, () => post(record ? `/api/collections/${pathId(record.id)}` : "/api/collections",
        {name: name.value.trim(), description: description.value.trim(), ...(record ? {expected_version: version} : {})}),
      result => setPersonalRecord("collections", result), record ? t("Koleksiyon güncellendi.") : t("Koleksiyon oluşturuldu."), record ? {
        load: () => latestPersonalRecord("collections", record.id),
        describe: latest => [[t("Ad"), latest.name], [t("Açıklama"), latest.description || "—"], [t("Durum"), latest.archived ? t("Arşivde") : t("Aktif")], [t("Sürüm"), latest.version]],
        rebase: latest => { version = latest.version; }, fill
      } : null, afterClose);
      session.open();
    }

    function setPoolFilters(values) {
      Object.assign(state, values);
      const search = document.getElementById("search");
      if (search) search.value = state.search || "";
      const source = document.getElementById("source-filter");
      if (source) source.value = state.sourceId || "";
      const archive = document.getElementById("show-archive");
      if (archive) archive.checked = state.showArchive;
      invalidatePool();
      if (state.view === "all") render();
      else changeView("all");
    }

    function openCollection(record) {
      setPoolFilters({collectionId: record.id, search: "", topic: "all", sourceId: "", readStatus: "all", sort: "relevance", showArchive: true});
    }

    function archiveButton(record, kind) {
      const archived = !record.archived;
      const node = button(archived ? t("Arşivle") : t("Arşivden çıkar"), () => directMutation(node,
        () => post(`/api/${kind}/${pathId(record.id)}/archive`, {archived, expected_version: record.version}),
        result => setPersonalRecord(kind === "searches" ? "saved_searches" : "collections", result),
        archived ? t("Arşivlendi. Dilediğinde arşivden geri alabilirsin.") : t("Arşivden çıkarıldı.")));
      node.setAttribute("aria-label", `${record.name}: ${archived ? t("arşivle") : t("arşivden çıkar")}`);
      node.dataset.focusKey = `workspace-archive-${kind}-${record.id}`;
      return node;
    }

    function collectionCard(record) {
      const card = element("article", "workspace-card");
      const mark = element("span", "workspace-card-icon");
      mark.append(icon("bookmark"));
      card.append(mark, element("h4", "", record.name), element("p", "workspace-help", record.description || t("Gelişmeleri bir arada tutmak için kişisel bir alan.")));
      card.append(element("p", "workspace-meta", t("{count} gelişme · {status}", {count: record.item_count ?? record.item_ids?.length ?? 0, status: record.archived ? t("Arşivde") : t("Aktif")})));
      const actions = element("div", "workspace-actions");
      actions.append(button(t("Gelişmeleri aç"), () => openCollection(record), "subtle", "arrow"),
        button(t("Düzenle"), () => editCollection(record)), archiveButton(record, "collections"));
      card.append(actions);
      return card;
    }

    function searchCard(record) {
      const card = element("article", "workspace-card workspace-search-card");
      card.append(element("h4", "", record.name));
      const sourceName = sources().find(source => source.id === record.source_id)?.name;
      const collectionName = collections().find(collection => collection.id === record.collection_id)?.name;
      card.append(element("p", "workspace-help", [record.query ? `“${record.query}”` : t("Bütün gelişmeler"), topics[record.topic] || t("Tüm konular"), sourceName || (record.source_id ? t("Kayıtlı kaynak") : t("Bütün kaynaklar")),
        collectionName || (record.collection_id ? t("Kayıtlı koleksiyon") : null), record.read_status && record.read_status !== "all" ? readLabels[record.read_status] : null,
        record.sort && record.sort !== "relevance" ? sortLabels[record.sort] : null].filter(Boolean).join(" · ")));
      const actions = element("div", "workspace-actions");
      actions.append(button(t("Aramayı aç"), () => setPoolFilters({search: record.query || "", topic: record.topic || "all", sourceId: record.source_id || "", collectionId: record.collection_id || "", readStatus: record.read_status || "all", sort: record.sort || "relevance", showArchive: true}), "subtle", "search"), archiveButton(record, "searches"));
      card.append(actions);
      return card;
    }

    function renderCollections(content) {
      const wrapper = element("section", "workspace-section");
      wrapper.append(heading(t("Koleksiyonların"), t("İlgini çeken gelişmeleri projelerine göre biriktir."), button(t("Yeni koleksiyon"), () => editCollection(), "primary", "bookmark")));
      const active = collections().filter(row => !row.archived);
      if (active.length) {
        const grid = element("div", "workspace-grid");
        active.forEach(record => grid.append(collectionCard(record)));
        wrapper.append(grid);
      } else wrapper.append(empty(t("İlk koleksiyonuna yer aç"), t("Bir gelişmenin okuma alanında “Koleksiyonlar” düğmesiyle onu istediğin koleksiyona ekleyebilirsin.")));
      wrapper.append(heading(t("Kayıtlı aramalar"), t("Sık baktığın konu, kaynak ve arama sözcüklerine tek adımda dön."), button(t("Bu aramayı kaydet"), () => saveSearch(), "secondary", "search")));
      const activeSearches = searches().filter(row => !row.archived);
      if (activeSearches.length) {
        const grid = element("div", "workspace-grid");
        activeSearches.forEach(record => grid.append(searchCard(record)));
        wrapper.append(grid);
      } else wrapper.append(empty(t("Aramanı bir kez kur, yeniden kullan"), t("Tüm gelişmelerde arama yaptıktan sonra “Aramayı kaydet” düğmesini kullan.")));
      const archivedCollections = collections().filter(row => row.archived);
      const archivedSearches = searches().filter(row => row.archived);
      if (archivedCollections.length || archivedSearches.length) {
        const archive = element("details", "workspace-archive");
        archive.append(element("summary", "", t("Arşiv · {collections} koleksiyon, {searches} arama", {collections: archivedCollections.length, searches: archivedSearches.length})));
        archive.append(element("p", "workspace-help", t("Arşivlediklerin korunur. Geri aldığında kaldığın yerden devam edebilirsin.")));
        const grid = element("div", "workspace-grid");
        archivedCollections.forEach(record => grid.append(collectionCard(record)));
        archivedSearches.forEach(record => grid.append(searchCard(record)));
        archive.append(grid);
        wrapper.append(archive);
      }
      content.append(wrapper);
    }

    function saveSearch() {
      const captured = {query: state.search || "", topic: state.topic || "all", source_id: state.sourceId || null,
        collection_id: state.collectionId || null, read_status: state.readStatus || "all", sort: state.sort || "relevance"};
      const session = dialog(t("Aramayı kaydet"), t("Arama metni, konu, kaynak, koleksiyon, okuma durumu ve sıralama birlikte saklanır. Kayıtlı aramalar arşivi de kapsar."), t("Aramayı kaydet"));
      if (!session) return;
      const name = input("", {required: true, maxLength: 60, placeholder: t("Örn. Yeni ses araçları")});
      const sourceName = sources().find(source => source.id === captured.source_id)?.name;
      const collectionName = collections().find(collection => collection.id === captured.collection_id)?.name;
      const details = element("dl", "workspace-details");
      for (const [label, value] of [[t("Arama"), captured.query || t("Bütün gelişmeler")], [t("Konu"), topics[captured.topic] || t("Tüm konular")], [t("Kaynak"), sourceName || t("Bütün kaynaklar")],
        [t("Koleksiyon"), collectionName || (captured.collection_id ? t("Seçili koleksiyon") : t("Bütün koleksiyonlar"))], [t("Okuma durumu"), readLabels[captured.read_status]], [t("Sıralama"), sortLabels[captured.sort]]]) {
        details.append(element("dt", "", label), element("dd", "", value));
      }
      session.body.append(details, field(t("Aramanın adı"), name, t("En fazla 60 karakter.")));
      session.onSubmit = () => saveDialog(session, () => post("/api/searches", {name: name.value.trim(), ...captured}),
        result => setPersonalRecord("saved_searches", result), t("Arama kaydedildi. Koleksiyonlar bölümünden açabilirsin."));
      session.open();
    }

    function wordList(value) {
      return [...new Set(value.split(/[\n,]+/).map(word => word.trim()).filter(Boolean))];
    }

    function editSettings() {
      const settings = personal().settings || {};
      let version = settings.version ?? 0;
      const session = dialog(t("Seçkini kişiselleştir"), t("Konuların ağırlığını ve günlük seçkinin büyüklüğünü belirle. Bu tercihler gelişmeleri kişisel ilgine göre sıralar."), t("Tercihleri kaydet"));
      if (!session) return;
      const weights = {};
      const weightGrid = element("div", "workspace-weights");
      for (const [topic, label] of Object.entries(topics)) {
        const weight = select(priorityLabels.map((text, value) => [value, `${value} · ${text}`]), settings.topic_weights?.[topic] ?? 3);
        weights[topic] = weight;
        weightGrid.append(field(label, weight));
      }
      const daily = input(settings.daily_limit ?? 8, {type: "number", min: 4, max: 20, step: 1, required: true});
      const keywords = input((settings.keywords || []).join("\n"), {multiline: true, maxLength: 2100, rows: 4, placeholder: t("Örn. sesli asistan\nrobot kolu")});
      const muted = input((settings.muted_keywords || []).join("\n"), {multiline: true, maxLength: 2100, rows: 4, placeholder: t("Daha az görmek istediğin ifadeler")});
      session.body.append(weightGrid, field(t("Günlük seçkideki gelişme sayısı"), daily, t("4–20 arasında. Havuzdaki bütün gelişmelere ayrıca ulaşabilirsin.")),
        field(t("Öne çıkarmak istediğin sözcükler"), keywords, t("Her satıra bir ifade yaz veya virgülle ayır. En fazla 20 ifade; her biri 100 karakter.")),
        field(t("Geri planda tutmak istediğin sözcükler"), muted, t("Bu ifadeler kişisel sıralamadaki ağırlığı azaltır; gelişmeleri silmez. En fazla 20 ifade; her biri 100 karakter.")));
      const fill = latest => {
        for (const [topic, control] of Object.entries(weights)) control.value = String(latest.topic_weights[topic]);
        daily.value = String(latest.daily_limit);
        keywords.value = latest.keywords.join("\n");
        muted.value = latest.muted_keywords.join("\n");
      };
      session.onSubmit = () => {
        const favoriteWords = wordList(keywords.value);
        const mutedWords = wordList(muted.value);
        if ([favoriteWords, mutedWords].some(words => words.length > 20 || words.some(word => word.length > 100))) {
          session.error(t("Her sözcük alanı en fazla 20 ifade içerebilir. Her ifade en fazla 100 karakter olmalı."));
          return;
        }
        const values = {topic_weights: Object.fromEntries(Object.entries(weights).map(([topic, control]) => [topic, Number(control.value)])),
          daily_limit: Number(daily.value), keywords: favoriteWords, muted_keywords: mutedWords, expected_version: version};
        saveDialog(session, () => post("/api/personal/settings", values), result => setPersonalRecord("settings", result), t("Seçki tercihlerin kaydedildi."), {
          load: () => latestPersonalRecord("settings"),
          describe: latest => [[t("Konu ağırlıkları"), Object.entries(topics).map(([topic, label]) => `${label}: ${latest.topic_weights[topic]}/5`).join(" · ")],
            [t("Günlük gelişme"), latest.daily_limit], [t("Öne çıkan sözcükler"), latest.keywords.join(", ") || "—"], [t("Geri planda"), latest.muted_keywords.join(", ") || "—"], [t("Sürüm"), latest.version]],
          rebase: latest => { version = latest.version; }, fill
        });
      };
      session.open();
    }

    function renderSettings(content) {
      const settings = personal().settings || {};
      const wrapper = element("section", "workspace-section");
      wrapper.append(heading(t("Sana göre bir seçki"), t("Önem verdiğin konulara daha fazla yer ayır."), button(t("İlgi alanlarını düzenle"), editSettings, "primary")));
      const card = element("div", "workspace-settings-card");
      const daily = element("div", "workspace-daily");
      daily.append(element("strong", "", String(settings.daily_limit ?? 8)), element("span", "", t("gelişmelik günlük seçki")));
      const weights = element("div", "workspace-priority-list");
      for (const [topic, label] of Object.entries(topics)) {
        const value = settings.topic_weights?.[topic] ?? 3;
        const row = element("div", "workspace-priority");
        row.append(element("span", "", label), element("strong", "", `${value}/5 · ${priorityLabels[value] || t("Dengeli")}`));
        const meter = element("div", "workspace-priority-meter");
        meter.setAttribute("aria-hidden", "true");
        for (let index = 0; index < 5; index += 1) meter.append(element("i", index < value ? "active" : ""));
        row.append(meter);
        weights.append(row);
      }
      card.append(daily, weights);
      const words = element("dl", "workspace-details");
      words.append(element("dt", "", t("Öne çıkan sözcükler")), element("dd", "", (settings.keywords || []).join(", ") || t("Henüz eklenmedi")),
        element("dt", "", t("Geri planda tutulanlar")), element("dd", "", (settings.muted_keywords || []).join(", ") || t("Henüz eklenmedi")));
      card.append(words, element("p", "workspace-help", t("Seçki; konu ağırlıkların, sözcüklerin ve kaynağın temel puanı kullanılarak sıralanır. Kaynak metnini okumak ve kişisel not tutmak ayrı adımlardır.")));
      wrapper.append(card);
      const stats = personal().stats || {};
      const reading = element("div", "workspace-reading-stats");
      reading.append(element("p", "", t("{read} okundu · {unread} okunmadı", {read: stats.read_count ?? 0, unread: stats.unread_count ?? 0})),
        button(t("Okunmayanları aç"), () => setPoolFilters({collectionId: "", search: "", topic: "all", sourceId: "", readStatus: "unread", sort: "relevance", showArchive: true}), "secondary", "arrow"));
      wrapper.append(reading);
      wrapper.append(connectionPanel());
      content.append(wrapper);
    }

    function connectionPanel() {
      const ai = state.data?.ai;
      const reading = state.data?.reading;
      const panel = element("section", "workspace-connections");
      const refresh = button(t("Durumu yenile"), async () => {
        if (operations) return;
        const view = state.view;
        const wasFocused = document.activeElement === refresh;
        operations += 1;
        refresh.disabled = true;
        try {
          const updated = await refreshAfterMutation();
          toast(updated === false ? t("Güncel durum alınamadı. Son alınan bilgiler gösteriliyor.") : t("Uygulamanın bağlantı ve kullanım bilgileri güncellendi."), updated === false);
        } catch (error) { toast(error.message, true); }
        finally { operations -= 1; refresh.disabled = false; restoreActionFocus(refresh, view, wasFocused); }
      }, "secondary", "refresh");
      refresh.dataset.focusKey = "workspace-connection-refresh";
      panel.append(heading(t("Okuma ve bağlantı"), t("Uygulama ve kayıtların bu PC’de. NVIDIA ilgi elemesi ve ön araştırmayı; Claude uygunluk değerlendirmesi ve derin araştırmayı yürütür."), refresh));
      const grid = element("div", "workspace-grid");
      function statusCard(title, data, provider = "") {
        const remote = Boolean(provider);
        const card = element("article", "workspace-status-card");
        card.append(element("h4", "", title));
        const status = !data ? t("Durum bilgisi henüz alınamadı") : data.closed ? t("Hizmet kapalı") : data.running ? t("Bir işlem sürüyor") : remote ? (data.configured ? provider !== "NVIDIA" ? t("Oturum bilgisi hazır") : t("Kimlik bilgisi hazır") : t("Bağlantı kurulumu gerekli")) : t("Okumaya hazır");
        const badge = element("span", `workspace-status-badge${data && !data.closed && (!remote || data.configured) ? " ready" : ""}`, status);
        card.append(badge);
        if (data) {
          const details = element("dl", "workspace-details");
          if (remote) details.append(element("dt", "", "Model"), element("dd", "", data.model || t("Bilinmiyor")));
          details.append(element("dt", "", t("Bugünkü denemeler")), element("dd", "", `${data.daily_used ?? "—"} / ${data.daily_limit ?? "—"}`),
            element("dt", "", t("Kalan hak")), element("dd", "", String(data.daily_remaining ?? "—")),
            element("dt", "", t("Sınırın yenilenmesi")), element("dd", "", formatDate(data.budget_resets_at, true)));
          card.append(details);
        }
        if (remote) {
          card.append(element("p", "workspace-help", provider !== "NVIDIA" ? t("Uygunluk değerlendirmesi, derin araştırma ve karşılaştırma aynı Claude bütçesini paylaşır. Kamusal kaynaklar ve proje özeti kullanılır; web araştırması yapılabilir. Kod veya canlı sistem değiştirilmez.") : t("İlgi elemesi, ön araştırma ve Türkçe özetler bu bütçeyi paylaşır. Kamusal kaynak metni ve sır içermeyen genel ilgi/proje özeti kullanılır; kişisel notlar gönderilmez.")));
          if (!data?.configured) card.append(element("p", "workspace-setup-help", t("Kurulum için uygulama klasöründeki README.md belgesinin {provider} bağlantısı bölümüne bak. Bu ekranda anahtar girmen veya dosyaya yazman gerekmez.", {provider})));
          else card.append(element("p", "workspace-help", provider !== "NVIDIA" ? t("Claude oturumu bulundu; model ve web erişimi araştırma sırasında sınanır. Bu uygulamanın günlük sınırı, sağlayıcının abonelik veya kullanım koşullarından ayrıdır.") : t("Kimlik bilgisi mevcut; NVIDIA erişimi işlem sırasında doğrulanır. “Durumu yenile” bir model çağrısı başlatmaz.")));
        } else card.append(element("p", "workspace-help", t("“Kaynak metnini oku” ile özgün makaleyi getir. Okuma, yapay zekâ özetinden ayrıdır; metin bu bilgisayarda saklanır.")));
        return card;
      }
      const deepProvider = {codex: "Codex", nvidia: "NVIDIA", claude: "Claude"}[ai?.deep_provider] || "Claude";
      const deep = ai && ai.deep_configured !== undefined ? {configured: ai.deep_configured, model: ai.deep_model || deepProvider, daily_limit: ai.deep_daily_limit, daily_used: ai.deep_daily_used, daily_remaining: ai.deep_daily_remaining, budget_resets_at: ai.deep_budget_resets_at || ai.budget_resets_at, closed: ai.closed, running: ai.running && ["review", "deep", "research"].includes(ai.kind)} : null;
      const nvidia = ai ? {...ai, running: ai.running && !["review", "deep", "research"].includes(ai.kind)} : null;
      grid.append(statusCard(t("Kaynak metni okuma"), reading), statusCard(t("NVIDIA · ilgi ve ön araştırma"), nvidia, "NVIDIA"), statusCard(t("{provider} · uygunluk ve derin araştırma", {provider: deepProvider}), deep, deepProvider));
      panel.append(grid);
      const officialLink = (url, text) => { const link = element("a", "", text); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; return link; };
      const nvidiaGuide = element("section", "workspace-card provider-start");
      nvidiaGuide.append(element("h4", "", t("NVIDIA ile başla")), element("p", "", t("NVIDIA Build hesabını aç ve Get API Key adımını izle.")), officialLink("https://build.nvidia.com/explore", t("NVIDIA Build · Get API Key ↗")), element("p", "workspace-help", t("Anahtarını kurulum rehberindeki ortam değişkeni veya kimlik deposu adımıyla kendi bilgisayarına tanıt.")), officialLink("https://docs.api.nvidia.com/nim/docs/api-quickstart", t("Resmî başlangıç rehberi ↗")), element("p", "workspace-help", t("Ücretsiz deneme/prototip erişimi; kota ve yayın/üretim kullanımı NVIDIA koşullarına bağlı.")), element("p", "workspace-help", t("API anahtarını forma, araştırma metnine veya paylaşım dosyasına yazma.")));
      if (deepProvider === "NVIDIA") nvidiaGuide.append(element("p", "workspace-help", t("Özet, ilgi elemesi, ön araştırma, derin araştırma ve kanıt denetimi aynı NVIDIA günlük bütçesini paylaşır.")));
      panel.append(nvidiaGuide);
      return panel;
    }

    function editSource(record) {
      let version = record.preference_version ?? 0;
      const session = dialog(t("Kaynak tercihleri"), record.name, t("Tercihleri kaydet"));
      if (!session) return;
      const enabled = select([["true", t("Etkin · yeni gelişmeleri takip et")], ["false", t("Duraklat · mevcut gelişmeler korunsun")]], String(record.enabled !== false));
      const interval = input(record.interval_hours || 48, {type: "number", min: 24, max: 720, step: 1, required: true});
      session.body.append(field(t("Takip durumu"), enabled), field(t("Kontrol aralığı (saat)"), interval, t("24–720 saat. Otomatik kontrol, uygulama açıkken yapılır.")));
      const fill = latest => { enabled.value = String(latest.enabled !== false); interval.value = String(latest.interval_hours); };
      session.onSubmit = () => saveDialog(session, () => post(`/api/sources/${pathId(record.id)}`, {enabled: enabled.value === "true", interval_hours: Number(interval.value), expected_version: version}),
        setSource, t("Kaynak tercihleri kaydedildi."), {
          load: () => latestSource(record.id),
          describe: latest => [[t("Kaynak"), latest.name], [t("Durum"), latest.enabled ? t("Etkin") : t("Duraklatıldı")], [t("Kontrol aralığı"), t("{hours} saat", {hours: latest.interval_hours})], [t("Sürüm"), latest.preference_version]],
          rebase: latest => { version = latest.preference_version; }, fill
        });
      session.open();
    }

    function addSource() {
      const session = dialog(t("Yeni kaynak ekle"), t("Takip etmek istediğin yayının herkese açık RSS veya Atom adresini ekle. Gelişmeler kaynak kontrol edildikçe havuzuna gelir."), t("Kaynağı ekle"));
      if (!session) return;
      const name = input("", {required: true, minLength: 2, maxLength: 200, placeholder: t("Kaynağın görünen adı")});
      const url = input("", {type: "url", required: true, maxLength: 4096, placeholder: "https://ornek.org/feed.xml", autocomplete: "url"});
      const publisher = input("", {required: true, minLength: 2, maxLength: 200, placeholder: t("Yayını hazırlayan kurum veya ekip")});
      const kind = select([["rss", "RSS"], ["atom", "Atom"]], "rss");
      const topic = select(Object.entries(topics), state.topic !== "all" && topics[state.topic] ? state.topic : "ai");
      const branches = input("", {required: true, maxLength: 1009, placeholder: t("Örn. Ses, Kurgu, Yayıncılık")});
      const language = input("bilinmiyor", {required: true, maxLength: 35, placeholder: "tr, en veya bilinmiyor"});
      const interval = input(48, {type: "number", min: 24, max: 720, step: 1, required: true});
      const type = select([t("Üretici"), t("Açık kaynak"), t("Araştırma"), t("Kurum"), t("Teknik yayın")].map(value => [value, value]), t("Teknik yayın"));
      session.body.append(field(t("Kaynak adı"), name), field(t("Besleme adresi"), url, t("Herkese açık bir HTTPS RSS/Atom adresi. Giriş veya anahtar gerektiren adresler kullanılamaz.")), field(t("Yayıncı"), publisher));
      const grid = element("div", "workspace-form-grid");
      grid.append(field(t("Besleme türü"), kind), field(t("Konu"), topic), field(t("Yayıncı türü"), type), field(t("Dil"), language, t("Örn. tr, en, en-us veya bilinmiyor.")));
      session.body.append(grid, field(t("Dallar"), branches, t("Virgülle ayır; 1–10 dal, her biri 2–100 karakter.")), field(t("Kontrol aralığı (saat)"), interval, t("24–720 saat. Başlangıç önerisi: 48 saat.")));
      session.onSubmit = () => {
        const values = wordList(branches.value);
        if (!values.length || values.length > 10 || values.some(value => value.length < 2 || value.length > 100)) {
          session.error(t("1–10 dal ekle. Her dalın adı 2–100 karakter olmalı."));
          branches.focus();
          return;
        }
        const payload = {name: name.value.trim(), fetch_url: url.value.trim(), publisher: publisher.value.trim(), kind: kind.value, topic: topic.value,
          branches: values, language: language.value.trim().toLowerCase(), interval_hours: Number(interval.value), source_type: type.value};
        saveDialog(session, () => post("/api/sources", payload), result => {
          setSource(result);
          Object.assign(state, {view: "sources", topic: "all", search: result.name,
            sourceBranch: "", sourceLanguage: "", sourceKind: "", sourceHealth: ""});
          const search = document.getElementById("search");
          if (search) search.value = result.name;
        }, t("Kaynak eklendi. Kontrol sonucunu kaynak kartında izleyebilirsin."));
      };
      session.open();
    }

    function sourceControls(record) {
      const wrapper = element("div", "workspace-source-controls");
      wrapper.append(button(t("Takip ayarları"), () => editSource(record)));
      if (record.last_error || record.failures || record.retry_pending) {
        const retry = button(record.retry_pending ? t("Deneme sırada") : t("Bir kez yeniden dene"), () => directMutation(retry,
          () => post(`/api/sources/${pathId(record.id)}/retry`, {expected_version: record.preference_version ?? 0}),
          result => setSource(result.source), result => result.queued ? t("Yeniden deneme sıraya alındı.") : result.started ? t("Kaynak yeniden kontrol ediliyor.") : t("Yeniden deneme isteği kaydedildi.")), "subtle", "refresh");
        retry.disabled = !record.can_retry;
        wrapper.append(retry);
        if (record.retry_pending) wrapper.append(element("p", "workspace-help", t("Kontrol sırası geldiğinde bir kez denenecek.")));
        else if (!record.can_retry && record.enabled !== false && record.retry_available_at) {
          wrapper.append(element("p", "workspace-help", t("Yeniden deneme: {date} sonrasında.", {date: formatDate(record.retry_available_at, true)})));
        } else if (record.enabled === false) wrapper.append(element("p", "workspace-help", t("Yeniden denemek için önce takibi etkinleştir.")));
      }
      return wrapper;
    }

    function sourceToolbar() {
      return heading(t("Kaynak kütüphanen"), t("Takibi duraklatabilir, kontrol aralığını değiştirebilir veya yeni bir yayın ekleyebilirsin."), button(t("Kaynak ekle"), addSource, "primary", "globe"));
    }

    function membership(item) {
      const session = dialog(t("Gelişmenin koleksiyonları"), item.title, null);
      if (!session) return;
      let records = collections().map(row => ({...row, item_ids: [...(row.item_ids || [])]}));
      const paint = () => {
        session.body.replaceChildren();
        const active = records.filter(row => !row.archived);
        if (!active.length) session.body.append(empty(t("Henüz bir koleksiyon yok"), t("İlk koleksiyonunu oluştur, sonra bu gelişmeyi içine ekle.")));
        else {
          session.body.append(element("p", "workspace-help", t("Ekleme ve çıkarma işlemleri hemen kaydedilir. Gelişmenin kendisi havuzda kalır.")));
          const list = element("div", "workspace-memberships");
          for (const collection of active) {
            const included = collection.item_ids.map(String).includes(String(item.id));
            const row = element("div", "workspace-membership");
            const label = element("div");
            label.append(element("strong", "", collection.name), element("span", "workspace-help", included ? t("Bu gelişme koleksiyonda") : t("{count} gelişme", {count: collection.item_count ?? collection.item_ids.length})));
            const toggle = button(included ? t("Çıkar") : t("Ekle"), async () => {
              if (session.submitting || operations) return;
              const wasFocused = document.activeElement === toggle;
              operations += 1;
              session.setBusy(true);
              session.errorBox.hidden = true;
              let result;
              let failure;
              try { result = await post(`/api/collections/${pathId(collection.id)}/items`, {item_ids: [item.id], remove: included, expected_version: collection.version}); }
              catch (error) { failure = error; }
              session.setBusy(false);
              if (failure) {
                operations -= 1;
                session.error(failure.status === 409 ? t("Koleksiyon başka bir pencerede değişti. İşlem uygulanmadı; üyelikleri güncelleyip yeniden seçebilirsin.") : failure.message);
                if (wasFocused) toggle.focus({preventScroll: true});
                return;
              }
              try {
                setPersonalRecord("collections", result);
                records = records.map(current => current.id === result.id ? result : current);
                paint();
                if (wasFocused) session.body.querySelectorAll("button").find(control => control.dataset.focusKey === toggle.dataset.focusKey)?.focus({preventScroll: true});
                const refreshed = await updateView();
                session.errorBox.hidden = true;
                const status = element("p", "workspace-help", `${included ? t("Koleksiyondan çıkarıldı.") : t("Koleksiyona eklendi.")}${refreshed ? "" : t(" Görünüm yenilenemedi; üyelik kaydedildi.")}`);
                status.setAttribute("role", "status");
                session.body.append(status);
              } finally { operations -= 1; }
            }, included ? "secondary" : "subtle", included ? null : "check");
            toggle.setAttribute("aria-label", `${collection.name}: ${included ? t("bu gelişmeyi çıkar") : t("bu gelişmeyi ekle")}`);
            toggle.dataset.focusKey = `workspace-membership-action-${collection.id}-${item.id}`;
            row.append(label, toggle);
            list.append(row);
          }
          session.body.append(list);
        }
        const actions = element("div", "workspace-actions");
        actions.append(button(t("Yeni koleksiyon"), () => {
          session.close();
          editCollection(null, () => membership(item));
        }, "secondary", "bookmark"));
        actions.append(button(t("Üyelikleri güncelle"), async () => {
          if (session.submitting || operations) return;
          session.setBusy(true);
          let snapshot;
          let failure;
          try { snapshot = await request("/api/personal"); } catch (error) { failure = error; }
          session.setBusy(false);
          if (failure) { session.error(failure.message); return; }
          records = snapshot.collections || [];
          session.errorBox.hidden = true;
          paint();
        }, "secondary", "refresh"));
        session.body.append(actions);
        session.markClean();
      };
      paint();
      session.open();
    }

    function readerControls(item) {
      const wrapper = element("div", "workspace-reader-controls");
      const isRead = (personal().read_states || []).some(row => String(row.item_id) === String(item.id) && row.read);
      const read = button(isRead ? t("Okunmadı olarak işaretle") : t("Okundu olarak işaretle"), () => directMutation(read,
        () => post(`/api/items/${pathId(item.id)}/read`, {read: !isRead}),
        result => {
          setPersonalRecord("read_states", result);
          const fresh = readerControls(item);
          wrapper.replaceWith(fresh);
        }, isRead ? t("Okunmadı olarak işaretlendi.") : t("Okundu olarak işaretlendi.")), isRead ? "secondary" : "subtle", "check");
      read.dataset.focusKey = `workspace-read-${item.id}`;
      const activeCount = collections().filter(row => !row.archived && (row.item_ids || []).map(String).includes(String(item.id))).length;
      const choose = button(activeCount ? t("Koleksiyonlar · {count}", {count: activeCount}) : t("Koleksiyonlar"), () => membership(item), "secondary", "bookmark");
      choose.dataset.focusKey = `workspace-membership-${item.id}`;
      wrapper.append(read, choose);
      return wrapper;
    }

    function poolControls() {
      const wrapper = element("div", "workspace-pool-controls");
      wrapper.setAttribute("role", "group");
      wrapper.setAttribute("aria-label", t("Kişisel havuz filtreleri"));
      const collectionOptions = [["", t("Bütün koleksiyonlar")], ...collections().filter(row => !row.archived || row.id === state.collectionId).map(row => [row.id, `${row.name}${row.archived ? t(" · arşivde") : ""}`])];
      const collection = select(collectionOptions, state.collectionId || "");
      const readStatus = select(Object.entries(readLabels), state.readStatus || "all");
      const sort = select(Object.entries(sortLabels), state.sort || "relevance");
      for (const [label, control, key] of [[t("Koleksiyon"), collection, "collectionId"], [t("Okuma durumu"), readStatus, "readStatus"], [t("Sıralama"), sort, "sort"]]) {
        control.dataset.focusKey = `workspace-pool-${key}`;
        control.addEventListener("change", () => { state[key] = control.value; invalidatePool(); render(); });
        wrapper.append(field(label, control));
      }
      wrapper.append(button(t("Aramayı kaydet"), saveSearch, "secondary", "search"));
      return wrapper;
    }

    return {renderCollections, renderSettings, sourceControls, sourceToolbar, readerControls, poolControls, busy};
  };
})();
