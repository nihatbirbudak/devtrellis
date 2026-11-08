// Özel itirazları sürüme bağlı gösterir; eski istek veya düğme başka kayıtta karar veremez.
(() => {
  "use strict";
  const tr = document.documentElement.lang === "tr";
  const text = (turkish, english) => tr ? turkish : english;
  const list = document.getElementById("objection-list");
  const detail = document.getElementById("objection-detail");
  const status = document.getElementById("objection-status");
  const node = (tag, value) => { const item = document.createElement(tag); if (value != null) item.textContent = String(value); return item; };
  const labels = {pending: text("Bekliyor", "Pending"), in_review: text("İncelemede", "In review"),
    accepted: text("Kabul edildi", "Accepted"), rejected: text("Reddedildi", "Rejected"),
    withdrawn: text("Geri çekildi", "Withdrawn"), expired: text("Süresi doldu", "Expired")};
  const PAGE_SIZE = 25;
  let csrf = null, current = null, busy = false, generation = 0, offset = 0;
  const note = value => { status.textContent = value; };
  const navigation = node("section"), filterLabel = node("label", text("İtiraz durumu ", "Objection status "));
  const filter = node("select"); filter.id = "objection-filter";
  for (const [value, label] of Object.entries({...labels, all: text("Tümü", "All")})) {
    const option = node("option", label); option.value = value; filter.append(option);
  }
  filter.value = "pending"; filterLabel.append(filter);
  const previous = node("button", text("Önceki", "Previous")), next = node("button", text("Sonraki", "Next"));
  previous.type = next.type = "button"; previous.id = "objection-previous"; next.id = "objection-next";
  const pageLabel = node("span"); pageLabel.id = "objection-page";
  navigation.append(filterLabel, previous, pageLabel, next); list.before(navigation);
  function resetDetail() { current = null; detail.hidden = true; detail.replaceChildren(); }
  // Gömülü tarayıcılar window.confirm'i sessizce "hayır" sayabilir; karar onayı sayfa içi pencereyle sorulur.
  function confirmDecision(message, confirmLabel, tone, returnFocus) {
    const dialog = globalThis.DevTrellisConfirm || globalThis.window?.DevTrellisConfirm;
    if (dialog?.ask) return dialog.ask({message, confirmLabel, tone, returnFocus});
    note(text("Onay penceresi açılamadı; karar kaydedilmedi. Sayfayı yenileyip yeniden deneyin.",
      "The confirmation dialog could not open; no decision was saved. Reload the page and try again."));
    return Promise.resolve(false);
  }
  async function request(path, payload) {
    const options = {credentials: "same-origin", cache: "no-store", headers: {Accept: "application/json"}};
    if (payload !== undefined) {
      options.method = "POST"; options.headers["Content-Type"] = "application/json";
      options.headers["X-Ufuk-CSRF"] = csrf; options.body = JSON.stringify(payload);
    }
    const response = await fetch("/api/community/objections" + path, options);
    if (!response.ok) throw new Error();
    return response.json();
  }
  async function load() {
    const selectedGeneration = ++generation, selectedOffset = offset, selectedFilter = filter.value;
    resetDetail(); list.replaceChildren(); previous.disabled = next.disabled = true;
    try {
      const response = await fetch("/api/community/session", {credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw new Error();
      const session = await response.json();
      if (selectedGeneration !== generation) return;
      if (session.user?.role !== "moderator" || !session.csrf_token) throw new Error();
      csrf = session.csrf_token;
      const data = await request(`/review?limit=${PAGE_SIZE}&offset=${selectedOffset}` +
        (selectedFilter === "all" ? "" : `&status=${encodeURIComponent(selectedFilter)}`));
      if (selectedGeneration !== generation) return;
      if (!Array.isArray(data.items) || !Number.isSafeInteger(data.total) || data.total < 0) throw new Error();
      note(text(`${data.total} itiraz`, `${data.total} objections`));
      pageLabel.textContent = `${Math.floor(selectedOffset / PAGE_SIZE) + 1} / ${Math.max(1, Math.ceil(data.total / PAGE_SIZE))}`;
      previous.disabled = selectedOffset === 0; next.disabled = selectedOffset + data.items.length >= data.total;
      for (const item of data.items) {
        const button = node("button", `${labels[item.status] || item.status} · ${item.research_id} · v${item.version}`);
        button.type = "button"; button.addEventListener("click", () => open(item.id)); list.append(button);
      }
    } catch {
      if (selectedGeneration !== generation) return;
      note(text("Kuyruk alınamadı. Moderatör oturumunu ve bağlantıyı kontrol edin.", "Could not load the queue. Check moderator sign-in and connection."));
      const link = node("a", text("Ana sayfa", "Home")); link.href = "/"; list.append(link);
    }
  }
  async function open(id) {
    if (busy) return;
    const selectedGeneration = ++generation;
    resetDetail();
    try {
      const result = await request("/review/" + encodeURIComponent(id));
      if (selectedGeneration !== generation) return;
      if (result.id !== id || !Number.isSafeInteger(result.revision)) throw new Error();
      const selected = Object.freeze({...result}); current = selected;
      const heading = node("h2", text("İtiraz ayrıntısı", "Objection details")); heading.tabIndex = -1;
      detail.append(heading, node("p", `${selected.research_id} · v${selected.version} · ${labels[selected.status] || selected.status}`));
      const report = node("a", text("İtiraz edilen tam sürümü aç", "Open disputed exact version"));
      report.href = `/r/${selected.research_id}/v/${selected.version}?lang=${tr ? "tr" : "en"}`; detail.append(report);
      for (const [label, value] of [[text("İddia", "Claim"), selected.claim], [text("Gerekçe", "Reason"), selected.reason]]) {
        detail.append(node("h3", label), node("p", value));
      }
      for (const source of selected.sources || []) {
        let url;
        try { url = new URL(source.url); if (url.protocol !== "https:" || url.username || url.password) continue; } catch { continue; }
        const link = node("a", source.title); link.href = url.href; link.rel = "noopener noreferrer"; link.target = "_blank";
        detail.append(link);
      }
      if (["pending", "in_review"].includes(selected.status)) {
        const controls = node("form"); controls.addEventListener("submit", event => event.preventDefault());
        controls.append(node("p", text("Kabul için önce yayımlanmış düzeltici sürümü ve kamu özetini girin.",
          "Publish a correcting version first, then enter its exact ID, version and public summary.")));
        const field = (element, label) => { const wrapper = node("label", label); wrapper.append(element); controls.append(wrapper); };
        const summary = node("textarea"); summary.maxLength = 600;
        const correctionId = node("input"); correctionId.maxLength = 32;
        const correctionVersion = node("input"); correctionVersion.type = "number"; correctionVersion.min = "1";
        field(summary, text("Kamu düzeltme özeti", "Public correction summary"));
        field(correctionId, text("Düzeltici araştırma kimliği", "Correcting research ID"));
        field(correctionVersion, text("Düzeltici sürüm", "Correcting version"));
        const action = (label, decision) => {
          const button = node("button", label); button.type = "button";
          button.addEventListener("click", async () => {
            if (busy || selectedGeneration !== generation || current !== selected) return;
            const payload = {expected_revision: selected.revision, research_id: selected.research_id,
              version: selected.version, status: decision};
            if (decision === "accepted") {
              if (!summary.value.trim() || !/^[a-f0-9]{32}$/.test(correctionId.value) || !Number.isSafeInteger(Number(correctionVersion.value)) || Number(correctionVersion.value) < 1) {
                note(text("Kamu özeti ve tam düzeltici sürüm gerekli.", "Public summary and exact correcting version required.")); return;
              }
              payload.public_summary = summary.value.trim();
              payload.correction = {research_id: correctionId.value, version: Number(correctionVersion.value)};
            }
            const confirmed = await confirmDecision(text("Bu moderasyon kararını kaydetmek istiyor musunuz?", "Save this moderation decision?") +
              `\n${selected.research_id} · v${selected.version} · ${label}`, label, decision === "rejected" ? "danger" : "default", button);
            // Onay beklenirken başka kayıt açıldıysa veya karar başladıysa eski onay bu kayda uygulanmaz.
            if (!confirmed || busy || selectedGeneration !== generation || current !== selected) return;
            const navigationState = {previous: previous.disabled, next: next.disabled};
            busy = true; button.disabled = true; filter.disabled = previous.disabled = next.disabled = true;
            try { await request(`/review/${selected.id}/decision`, payload); await load(); }
            catch {
              previous.disabled = navigationState.previous; next.disabled = navigationState.next;
              note(text("Karar kaydedilemedi; kaydı yeniden açıp sürüm ve yetkiyi kontrol edin.", "Decision failed; reopen the record and check version and permission."));
            }
            finally { busy = false; button.disabled = false; filter.disabled = false; }
          }); controls.append(button);
        };
        if (selected.status === "pending") action(text("İncelemeye al", "Start review"), "in_review");
        if (selected.status === "in_review") action(text("Kabul et", "Accept"), "accepted");
        action(text("Reddet", "Reject"), "rejected"); detail.append(controls);
      }
      detail.hidden = false; heading.focus();
    } catch { if (selectedGeneration === generation) note(text("İtiraz okunamadı.", "Could not read objection.")); }
  }
  filter.addEventListener("change", () => { if (!busy) { offset = 0; void load(); } });
  previous.addEventListener("click", () => { if (!busy && !previous.disabled) { offset = Math.max(0, offset - PAGE_SIZE); void load(); } });
  next.addEventListener("click", () => { if (!busy && !next.disabled) { offset += PAGE_SIZE; void load(); } });
  void load();
})();
