// Kamu raporu ve okuma rehberinde kayıtlı arayüz dilini korur; açık URL seçimi ve JS'siz dil bağlantıları önceliklidir.
(() => {
  "use strict";
  const valid = value => value === "tr" || value === "en";
  let storage;
  try { storage = window.localStorage; } catch { /* Tercih deposu kapalıysa sunucu dili kalır. */ }
  for (const anchor of document.querySelectorAll("[data-report-language]")) {
    anchor.addEventListener("click", () => {
      const language = anchor.dataset.reportLanguage;
      if (valid(language)) { try { storage?.setItem("devtrellis.ui.language", language); } catch {} }
    });
  }
  const url = new URL(window.location.href);
  if ((!url.pathname.startsWith("/r/") && url.pathname !== "/report-guide") || url.searchParams.has("lang")) return;
  let selected;
  for (const key of ["devtrellis.ui.language", "ufuk.ui.language"]) {
    try { const value = storage?.getItem(key); if (valid(value)) { selected = value; break; } } catch {}
  }
  if (selected && selected !== document.documentElement.lang) {
    url.searchParams.set("lang", selected);
    window.location.replace(url.href);
  }
})();
