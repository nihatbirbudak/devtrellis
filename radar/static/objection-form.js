// İnsan onayıyla itiraz yollar; özel fişi yalnız açık sekmede durum ve geri çekme için tutar.
(() => {
  "use strict";
  const root = document.querySelector("main[data-research-id]");
  const form = document.getElementById("objection-form"), preview = document.getElementById("objection-preview");
  const status = document.getElementById("objection-status"), sendButton = document.getElementById("objection-send");
  const editButton = document.getElementById("objection-edit");
  const tr = document.documentElement.lang === "tr";
  const id = root.dataset.researchId, version = Number(root.dataset.version);
  let payload = null, receipt = null, attempted = false, busy = false, current = null;
  const message = (turkish, english) => tr ? turkish : english;
  const report = value => { status.textContent = value; };
  const labels = {pending: message("Bekliyor", "Pending"), in_review: message("İncelemede", "In review"),
    accepted: message("Kabul edildi", "Accepted"), rejected: message("Reddedildi", "Rejected"),
    withdrawn: message("Geri çekildi", "Withdrawn"), expired: message("Süresi doldu", "Expired")};
  const lossNote = document.createElement("p"); lossNote.id = "objection-receipt-note";
  lossNote.textContent = message("Durum sorgulama ve geri çekme yalnız bu açık sekmede kullanılabilir. Sekmeyi yenilemek veya kapatmak özel fişi siler; gönderilmiş itirazı silmez. Fiş kaydedilmez veya bağlantıya eklenmez.",
    "Status and withdrawal are available only in this open tab. Reloading or closing it loses the private receipt; it does not delete the submitted objection. The receipt is not saved or added to links.");
  form.before(lossNote);
  const controls = document.createElement("section"); controls.id = "objection-owner-controls"; controls.hidden = true;
  const readButton = document.createElement("button"), withdrawButton = document.createElement("button");
  readButton.type = withdrawButton.type = "button"; readButton.id = "objection-read"; withdrawButton.id = "objection-withdraw";
  readButton.textContent = message("Durumu sorgula", "Check status");
  withdrawButton.textContent = message("Özel itirazı geri çek", "Withdraw private objection");
  controls.append(readButton, withdrawButton); root.append(controls);
  function receiptToken() {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }
  function setBusy(value) {
    busy = value; readButton.disabled = withdrawButton.disabled = sendButton.disabled = editButton.disabled = value;
  }
  function checked(value) {
    if (!value || !/^[a-f0-9]{32}$/.test(value.id) || value.research_id !== id || value.version !== version ||
      !Number.isSafeInteger(value.revision) || value.revision < 1 || !Object.hasOwn(labels, value.status)) throw new Error();
    return value;
  }
  function show(value) {
    current = checked(value); controls.hidden = false; withdrawButton.hidden = current.status === "withdrawn";
    sendButton.hidden = editButton.hidden = true;
    report(message("İtiraz durumu: ", "Objection status: ") + labels[current.status] + ` · ${current.id}` +
      (current.private_purged ? message(" · Özel metin silinmiş.", " · Private text has been removed.") : ""));
    if (current.private_purged) {
      payload = null; form.reset(); document.getElementById("objection-preview-text").textContent = ""; preview.hidden = true;
    }
  }
  async function session() {
    const response = await fetch("/api/community/session", {method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"Content-Type": "application/json", Accept: "application/json"}, body: "{}"});
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (typeof data.csrf_token !== "string" || !data.csrf_token) throw new Error();
    return data.csrf_token;
  }
  async function ownerRequest(path, body) {
    const csrf = await session();
    const response = await fetch("/api/community/objections" + path, {method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"Content-Type": "application/json", Accept: "application/json",
        "X-Ufuk-CSRF": csrf, "X-DevTrellis-Objection-Receipt": receipt}, body: JSON.stringify(body)});
    if (!response.ok) throw new Error();
    return response.json();
  }
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (!form.reportValidity() || busy || attempted) return;
    const fields = new FormData(form);
    const sourceTitle = String(fields.get("source_title") || "").trim(), sourceURL = String(fields.get("source_url") || "").trim();
    if (Boolean(sourceTitle) !== Boolean(sourceURL)) {
      report(message("Kaynak başlığı ile bağlantısını birlikte girin.", "Enter both source title and link.")); return;
    }
    if (sourceURL) {
      try { const address = new URL(sourceURL); if (address.protocol !== "https:" || address.username || address.password) throw new Error(); }
      catch { report(message("Kamu kaynağı HTTPS adresi olmalı.", "The public source must use HTTPS.")); return; }
    }
    payload = {research_id: id, version, claim: String(fields.get("claim") || "").trim(),
      reason: String(fields.get("reason") || "").trim(), sources: sourceURL ? [{title: sourceTitle, url: sourceURL}] : [], consent: true};
    document.getElementById("objection-preview-text").textContent =
      `${message("İddia", "Claim")}: ${payload.claim}\n\n${message("Gerekçe", "Reason")}: ${payload.reason}` +
      (sourceURL ? `\n\n${message("Kaynak", "Source")}: ${sourceTitle} — ${sourceURL}` : "");
    form.hidden = true; preview.hidden = false; report(""); sendButton.focus();
  });
  editButton.addEventListener("click", () => {
    if (busy || attempted) return;
    preview.hidden = true; form.hidden = false; form.elements.claim.focus();
  });
  sendButton.addEventListener("click", async () => {
    if (!payload || busy || current) return;
    setBusy(true);
    try {
      if (!receipt) receipt = receiptToken();
      // Oturum açılmazsa düzenleme hâlâ mümkündür; gönderim belirsizleşirse aynı fiş ve metin korunur.
      const csrf = await session();
      attempted = true; controls.hidden = false; withdrawButton.hidden = true;
      const response = await fetch("/api/community/objections", {method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"Content-Type": "application/json", Accept: "application/json",
          "X-Ufuk-CSRF": csrf, "X-DevTrellis-Objection-Receipt": receipt}, body: JSON.stringify(payload)});
      if (!response.ok) throw new Error();
      show((await response.json()).objection);
    } catch {
      report(attempted ? message("Teslim doğrulanamadı. Durumu sorgulayın veya aynı metni ve fişi değiştirmeden yeniden deneyin.",
        "Delivery was not confirmed. Check status or retry the same text and receipt.") : message("Oturum açılamadı; bu denemede itiraz gönderilmedi.",
        "The session could not open; nothing was sent."));
    } finally { setBusy(false); }
  });
  readButton.addEventListener("click", async () => {
    if (!receipt || !attempted || busy) return;
    setBusy(true);
    try { show(await ownerRequest("/read", {})); }
    catch { report(message("Durum alınamadı; kayıt değiştirilmedi. Aynı sekmeden yeniden deneyin.", "Status could not be read; no record was changed. Retry in this tab.")); }
    finally { setBusy(false); }
  });
  // Gömülü tarayıcılar window.confirm'i sessizce "hayır" sayabilir; geri çekme sayfa içi pencereyle sorulur.
  function confirmWithdraw() {
    const dialog = globalThis.DevTrellisConfirm || globalThis.window?.DevTrellisConfirm;
    if (dialog?.ask) return dialog.ask({message: message("Özel iddia, gerekçe ve kaynak metnini silerek itirazı geri çekmek istiyor musunuz? Önceden yayımlanan kamu düzeltme izi kalır.",
      "Withdraw this objection and delete its private claim, reason and sources? Any previously published correction trace remains."),
      confirmLabel: withdrawButton.textContent, tone: "danger", returnFocus: withdrawButton});
    report(message("Onay penceresi açılamadı; işlem yapılmadı. Sayfayı yenileyip yeniden deneyin.",
      "The confirmation dialog could not open; nothing was done. Reload the page and try again."));
    return Promise.resolve(false);
  }
  withdrawButton.addEventListener("click", async () => {
    if (!receipt || !current || current.status === "withdrawn" || busy) return;
    const selected = current;
    // Onay beklenirken durum değiştiyse eski revizyon için verilen onay kullanılmaz.
    if (!(await confirmWithdraw()) || busy || current !== selected) return;
    const expected = selected.revision;
    setBusy(true);
    try { show(await ownerRequest("/withdraw", {expected_revision: expected})); }
    catch { report(message("Geri çekme doğrulanamadı. Durumu yeniden sorgulayın; değişmiş bir sürüm otomatik onaylanmaz.",
      "Withdrawal was not confirmed. Check status again; a changed revision is not approved automatically.")); }
    finally { setBusy(false); }
  });
})();
