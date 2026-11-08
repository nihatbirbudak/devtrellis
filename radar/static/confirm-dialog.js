// Tarayıcının confirm penceresi yerine sayfa içinde erişilebilir onay sorar; gömülü tarayıcılar confirm'i sessizce bastırabilir.
(() => {
  "use strict";
  const WORDS = {
    title: ["Onay gerekiyor", "Confirmation needed"],
    confirm: ["Evet, devam et", "Yes, continue"],
    cancel: ["Vazgeç", "Cancel"],
    cancelled: ["Vazgeçildi; hiçbir değişiklik yapılmadı.", "Cancelled; nothing was changed."],
    unavailable: ["Onay penceresi açılamadı; işlem yapılmadı. Sayfayı yenileyip yeniden dene.",
      "The confirmation dialog could not open; nothing was done. Reload the page and try again."],
    busy: ["Önce açık onay penceresini yanıtla; yeni işlem yapılmadı.", "Answer the open confirmation first; nothing new was done."]
  };
  const NOTICE_MS = 10000, ANNOUNCE_MS = 100;

  function createConfirm(doc, timers = globalThis) {
    let active = null, notice = null, noticeTimer = null, sequence = 0;
    const language = () => String(doc.documentElement?.lang || "").toLowerCase().startsWith("tr") ? 0 : 1;
    const text = key => WORDS[key][language()];
    const make = (tag, className, value) => {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (value !== undefined) node.textContent = String(value);
      return node;
    };
    const label = (value, key) => typeof value === "string" && value.trim() ? value : text(key);
    function hostFor(trigger) {
      const own = trigger?.closest?.("dialog[open]");
      if (own && !own.classList?.contains("dt-confirm")) return own;
      const open = Array.from(doc.querySelectorAll?.("dialog[open]") || []).filter(node => !node.classList?.contains("dt-confirm"));
      return open.at(-1) || doc.body;
    }
    function hideNotice() {
      timers.clearTimeout?.(noticeTimer);
      if (notice) notice.textContent = "";
    }
    // Ekran okuyucular yalnız önceden var olan canlı bölgedeki değişikliği okur; bölge boş ama erişilebilir olarak
    // pencere açılmadan önce yerleştirilir. Yeni yerleştirildiyse true döner.
    function attachNotice(host) {
      if (!notice) {
        notice = make("p", "dt-confirm-notice");
        notice.setAttribute("role", "status");
        notice.setAttribute("aria-live", "polite");
      }
      if (notice.parentNode === host) return false;
      timers.clearTimeout?.(noticeTimer);
      notice.textContent = "";
      host.append(notice);
      return true;
    }
    function showNotice(value, kind, host, attachedNow = false) {
      timers.clearTimeout?.(noticeTimer);
      const fresh = attachNotice(host) || attachedNow;
      notice.dataset.kind = kind;
      const write = () => { notice.textContent = value; noticeTimer = timers.setTimeout?.(hideNotice, NOTICE_MS); };
      // Bölge bu anda yerleştirildiyse metin bir sonraki görevde yazılır ki değişiklik duyurulsun.
      if (fresh && typeof timers.setTimeout === "function") { notice.textContent = ""; noticeTimer = timers.setTimeout(write, ANNOUNCE_MS); }
      else write();
    }
    function report(options, value, kind, host, attachedNow = false) {
      try {
        if (typeof options.notify === "function") options.notify(value, kind);
        else showNotice(value, kind, host, attachedNow);
      } catch { /* İleti gösterilemese de işlem yapılmamış olarak kalır. */ }
    }
    function restore(target) {
      if (!target || target.isConnected === false || target.disabled || typeof target.focus !== "function") return;
      try { target.focus(); } catch { /* Odak geri verilemezse sayfa kullanılabilir kalır. */ }
    }

    // Sonuç her zaman Promise<boolean>: yalnız açık "onay" düğmesi true döndürür.
    function ask(options = {}) {
      const trigger = options.returnFocus || doc.activeElement;
      const host = options.host || hostFor(trigger);
      const message = typeof options.message === "string" ? options.message.trim() : "";
      if (active) {
        report(options, text("busy"), "busy", host);
        restore(active.cancel);
        return Promise.resolve(false);
      }
      hideNotice();
      if (!message) {
        report(options, label(options.unavailableNotice, "unavailable"), "unavailable", host);
        return Promise.resolve(false);
      }
      const id = `dt-confirm-${++sequence}`;
      const dialog = make("dialog", "dt-confirm");
      dialog.setAttribute("role", "alertdialog");
      dialog.setAttribute("aria-modal", "true");
      dialog.setAttribute("aria-labelledby", `${id}-title`);
      dialog.setAttribute("aria-describedby", `${id}-message`);
      const body = make("div", "dt-confirm-body");
      const title = make("h2", "dt-confirm-title", label(options.title, "title")); title.id = `${id}-title`;
      const description = make("p", "dt-confirm-message", message); description.id = `${id}-message`;
      body.append(title, description);
      const actions = make("div", "dt-confirm-actions");
      const cancel = make("button", "dt-confirm-cancel", label(options.cancelLabel, "cancel")); cancel.type = "button";
      const accept = make("button", `dt-confirm-accept${options.tone === "danger" ? " dt-confirm-danger" : ""}`,
        label(options.confirmLabel, "confirm")); accept.type = "button";
      actions.append(cancel, accept);
      dialog.append(body, actions);

      return new Promise(resolve => {
        let settled = false, attachedNow = false;
        const finish = confirmed => {
          if (settled) return;
          settled = true; active = null;
          try { if (dialog.open) dialog.close(); } catch { /* Kapanmış pencere yeniden kapatılmaz. */ }
          dialog.remove?.();
          restore(trigger);
          if (!confirmed) report(options, label(options.cancelledNotice, "cancelled"), "cancelled", host);
          resolve(confirmed);
        };
        cancel.addEventListener("click", () => finish(false));
        accept.addEventListener("click", () => finish(true));
        // Esc tarayıcıda cancel olayı üretir; kapanışı burada denetleriz ki her yol aynı "vazgeç" sonucuna varsın.
        dialog.addEventListener("cancel", event => { event.preventDefault?.(); finish(false); });
        dialog.addEventListener("close", () => finish(false));
        dialog.addEventListener("keydown", event => {
          // Sayfanın kısayolları açık onayın altındaki içerikte çalışmasın.
          event.stopPropagation?.();
          if (event.key === "Escape") { event.preventDefault?.(); finish(false); return; }
          if (event.key !== "Tab") return;
          const order = [cancel, accept];
          const index = order.indexOf(doc.activeElement);
          const next = event.shiftKey ? (index <= 0 ? order.length - 1 : index - 1) : (index === order.length - 1 ? 0 : index + 1);
          event.preventDefault?.();
          order[next].focus();
        });
        try {
          if (typeof dialog.showModal !== "function") throw new Error("showModal unavailable");
          if (typeof options.notify !== "function") attachedNow = attachNotice(host);
          doc.body.append(dialog);
          dialog.showModal();
          if (!dialog.open) throw new Error("dialog did not open");
          active = {dialog, cancel};
          // Geri alınamaz işlerde güvenli seçenek önce odaklanır.
          cancel.focus();
        } catch {
          settled = true; active = null;
          try { if (dialog.open) dialog.close(); } catch { /* Açılamayan pencere temizlenir. */ }
          dialog.remove?.();
          report(options, label(options.unavailableNotice, "unavailable"), "unavailable", host, attachedNow);
          resolve(false);
        }
      });
    }
    return {ask, text, words: WORDS};
  }

  if (typeof module !== "undefined" && module.exports) module.exports = {createConfirm, WORDS};
  if (typeof document === "undefined") return;
  globalThis.DevTrellisConfirm = createConfirm(document);
})();
