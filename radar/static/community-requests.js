// Özel talepleri yetkili oturumla okur; iletişim verisini yalnız güvenli düz metin olarak gösterir.
(() => {
  "use strict";
  const translations = {
    "DevTrellis — Başvuru ve destek kutusu":"DevTrellis — Applications and support inbox",
    "Başvuru ve destek kutusu":"Applications and support inbox", "Dil":"Language",
    "Bu talepler özeldir. İletişim bilgilerini kamuya açık araştırmalara veya model istemlerine taşımayın.":"These requests are private. Do not copy contact details into public research or model prompts.",
    "Ana sayfa ve moderatör girişi":"Home and moderator sign in", "Durum":"Status", "Yeni":"New", "İnceleniyor":"In review", "Kapalı":"Closed", "Tümü":"All", "Listeyi yenile":"Refresh list", "Talep sayfaları":"Request pages", "Önceki":"Previous", "Sonraki":"Next", "Mesaj":"Message", "Talep durumu":"Request status", "Durumu kaydet":"Save status",
    "Durum değişikliği e-posta göndermez. Yanıt gerekiyorsa kişinin verdiği iletişim adresini kullanın.":"Changing status does not send email. Use the supplied contact address if a response is needed.",
    "Bu alan için moderatör girişi gerekli.":"Moderator sign in is required for this area.", "Talepler alınamadı. Yeniden deneyin.":"Requests could not be loaded. Please retry.", "İşlem tamamlanamadı. Yeniden deneyin.":"The action could not be completed. Please retry.", "Talep bulunmuyor.":"No requests found.", "{count} talep":"{count} requests", "Talep numarası":"Reference", "Tür":"Type", "Katkı başvurusu":"Contribution application", "Destek":"Support", "Kategori":"Category", "Ad":"Name", "E-posta":"Email", "Belirtilmedi":"Not provided", "Oluşturuldu":"Created", "Dil tercihi":"Language preference", "Durum kaydedildi.":"Status saved.", "Yükleniyor…":"Loading…"
  };
  if (typeof document === "undefined") return;
  const UI = globalThis.UfukUI;
  UI.register({en: translations}); UI.initialize();
  const t = UI.t, byId = id => document.getElementById(id);
  const state = {session: null, offset: 0, total: 0, current: null, busy: false, generation: 0};
  const note = message => { byId("requests-notice").textContent = t(message); };
  function clearPrivate() {
    ++state.generation;
    state.current = null;
    byId("requests-workspace").hidden = true;
    byId("request-detail").hidden = true;
    byId("requests-list").replaceChildren(); byId("request-facts").replaceChildren();
    byId("request-title").textContent = ""; byId("request-message").textContent = "";
  }
  async function request(path, payload) {
    const options = {credentials: "same-origin", cache: "no-store", headers: {Accept: "application/json"}};
    if (payload !== undefined) {
      options.method = "POST"; options.headers["Content-Type"] = "application/json";
      options.headers["X-Ufuk-CSRF"] = state.session.csrf_token; options.body = JSON.stringify(payload);
    }
    const response = await fetch(`/api/community${path}`, options);
    if (response.status === 401 || response.status === 403) { clearPrivate(); throw new Error("Bu alan için moderatör girişi gerekli."); }
    if (!response.ok) throw new Error("İşlem tamamlanamadı. Yeniden deneyin.");
    return response.json();
  }
  function textNode(tag, text) { const element = document.createElement(tag); element.textContent = String(text); return element; }
  async function open(id) {
    const generation = ++state.generation;
    byId("request-detail").hidden = true; state.current = null;
    try {
      const record = await request(`/requests/${encodeURIComponent(id)}`);
      if (generation !== state.generation) return;
      state.current = record;
      byId("request-title").textContent = record.subject;
      const facts = byId("request-facts"); facts.replaceChildren();
      for (const [key, value] of [["Talep numarası", record.id], ["Tür", t(record.kind === "contribute" ? "Katkı başvurusu" : "Destek")], ["Kategori", record.category], ["Ad", record.name], ["E-posta", record.email], ["Oluşturuldu", record.created_at], ["Dil tercihi", record.language]]) facts.append(textNode("dt", t(key)), textNode("dd", value || t("Belirtilmedi")));
      byId("request-message").textContent = record.message;
      byId("request-status").value = record.status;
      byId("request-detail").hidden = false; byId("request-title").focus(); note("");
    } catch (error) { if (generation === state.generation) note(error.message); }
  }
  async function load() {
    if (state.busy) return;
    state.busy = true; const generation = ++state.generation;
    byId("requests-list").setAttribute("aria-busy", "true"); note("Yükleniyor…");
    try {
      const params = new URLSearchParams({status: byId("requests-state").value, offset: String(state.offset), limit: "25"});
      const result = await request(`/requests?${params}`);
      if (generation !== state.generation) return;
      const list = byId("requests-list"); list.replaceChildren(); state.total = result.total;
      for (const item of result.items) { const button = textNode("button", `${item.subject} · ${item.created_at}`); button.type = "button"; button.className = "button secondary"; button.addEventListener("click", () => open(item.id)); list.append(button); }
      byId("requests-count").textContent = t("{count} talep", {count: result.total});
      note(result.items.length ? "" : "Talep bulunmuyor.");
      byId("requests-prev").disabled = state.offset === 0;
      byId("requests-next").disabled = state.offset + 25 >= state.total;
    } catch (error) { note(error.message || "Talepler alınamadı. Yeniden deneyin."); }
    finally { state.busy = false; byId("requests-list").setAttribute("aria-busy", "false"); }
  }
  byId("requests-filter").addEventListener("submit", event => { event.preventDefault(); state.offset = 0; load(); });
  byId("requests-prev").addEventListener("click", () => { if (!state.busy) { state.offset = Math.max(0, state.offset - 25); load(); } });
  byId("requests-next").addEventListener("click", () => { if (!state.busy && state.offset + 25 < state.total) { state.offset += 25; load(); } });
  byId("request-status-form").addEventListener("submit", async event => {
    event.preventDefault(); if (!state.current || byId("request-save").disabled) return;
    const id = state.current.id; byId("request-save").disabled = true;
    try { await request(`/requests/${encodeURIComponent(id)}/status`, {status: byId("request-status").value}); byId("request-detail").hidden = true; state.current = null; await load(); note("Durum kaydedildi."); }
    catch (error) { note(error.message); }
    finally { byId("request-save").disabled = false; }
  });
  (async () => {
    try {
      state.session = await request("/session");
      if (state.session.user?.role !== "moderator") { clearPrivate(); note("Bu alan için moderatör girişi gerekli."); return; }
      byId("requests-workspace").hidden = false; await load();
    } catch (error) { clearPrivate(); note(error.message || "Talepler alınamadı. Yeniden deneyin."); }
  })();
})();
