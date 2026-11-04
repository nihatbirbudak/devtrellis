"""Açık istekle makale metnini güvenle okur; kalıcı sonucu, sınırları ve kaynak değişimini izler."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import timedelta
from math import ceil
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment

from radar.depo import simdi, tarih
from radar.hizmet import TaramaKilidi
from radar.toplayici import CollectionError, SOURCE_TIMEOUT, guvenli_sayfa_oku


GUNLUK_SINIR = 20
TEKRAR_BEKLEME_SANIYE = 300
EN_AZ_METIN = 200
EN_COK_METIN = 40_000


class OkumaHatasi(ValueError):
    """Yanıtın güvenilir bir makale olarak okunamadığını açıklar."""


class OkumaMesgul(ValueError):
    """Başka makalenin okuması sürerken yeni ağ işi açılmasını engeller."""


class OkumaHazirDegil(ValueError):
    """Makale işçisi başlatılamadığında isteği kalıcı hata bırakmadan reddeder."""


class OkumaSiniri(ValueError):
    """Günlük bütçe veya aynı makaleyi yeniden okuma süresi dolmadığında kullanılır."""

    def __init__(self, message, retry_after_seconds=0):
        super().__init__(message)
        self.retry_after_seconds = max(0, ceil(retry_after_seconds))


def makale_ayristir(response):
    """Yalnız görünür anlamsal makale metnini döndürür; dış içerik hiçbir zaman yürütülmez."""
    if response.status != 200:
        raise OkumaHatasi(f"Makale başarılı HTTP yanıtı döndürmedi (HTTP {response.status}).")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if (content_type == "application/pdf" or response.body.lstrip().startswith(b"%PDF-")
            or urlsplit(response.url).path.lower().endswith(".pdf")):
        raise OkumaHatasi("PDF makale okuması henüz desteklenmiyor; özgün bağlantıyı açabilirsiniz.")
    if content_type not in {"", "text/html", "application/xhtml+xml"}:
        raise OkumaHatasi("Bu yanıt HTML makalesi değil; desteklenmeyen içerik türü okunmadı.")
    soup = BeautifulSoup(response.body, "html.parser")
    for element in soup.select(
            "script, style, noscript, iframe, nav, footer, aside, form, button, svg, canvas, "
            "[hidden], [aria-hidden='true'], [role='navigation'], [role='contentinfo'], "
            ".ad, .ads, .advertisement, .advertising, .ad-container, .cookie-banner, "
            ".newsletter, .related-posts, .related-articles, .social-share, .paywall, .banner-footer, "
            "[id^='ad-'], [id^='ad_'], .visually-hidden, .sr-only"):
        element.decompose()
    for element in soup.select("[style]"):
        if element.attrs is None:
            continue
        if re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", element.get("style", ""), re.I):
            element.decompose()
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    # İlgili yayın kartları ve iletişim tanıtımları ana metnin kanıtı değildir.
    for section in list(soup.find_all("section")):
        heading = section.find(["h2", "h3"])
        caption = " ".join(heading.get_text(" ", strip=True).casefold().split()).strip(".!… ") if heading else ""
        if not section.find("h1") and (caption in {
                "related readings and updates", "related articles", "related posts", "related research",
                "read more", "ilgili haberler", "ilgili yayınlar", "benzer içerikler",
                "why choose wur?", "questions about this project?"}):
            section.decompose()
    forum_body = soup.select_one("#post_1[itemtype$='/DiscussionForumPosting'] [itemprop='articleBody']")
    release_path = re.fullmatch(r'/[^/]+/([^/]+)/releases/tag/[^/]+/?', urlsplit(response.url).path)
    release_bodies = soup.select('[data-test-selector="body-content"].markdown-body')
    release_headings = soup.select('main h1.d-inline')
    release_title = None
    if (urlsplit(response.url).hostname == 'github.com' and release_path
            and len(release_bodies) == 1 and len(release_headings) == 1):
        release_title = release_path[1] + ' · ' + release_headings[0].get_text(' ', strip=True)
    articles = soup.find_all("article")
    if release_title:
        root = release_bodies[0]
    elif forum_body is not None:
        root = forum_body
    elif len(articles) > 1:
        raise OkumaHatasi("Sayfa birden fazla haber kartı içeriyor; liste tek makale gibi kaydedilmedi.")
    elif articles:
        root = articles[0]
    else:
        mains = soup.find_all("main")
        if len(mains) != 1:
            raise OkumaHatasi("Sayfada tek ve doğrulanabilir makale alanı bulunamadı.")
        root = mains[0]
    heading_links = {link.get("href", "").split("#", 1)[0]
                     for link in root.select("h2 a[href], h3 a[href]")
                     if link.get("href", "") and not link["href"].startswith("#")}
    if len(heading_links) > 1 and not release_title:
        raise OkumaHatasi("Birden fazla haber başlığı içeren liste makale gövdesi sayılmadı.")
    headings = root.find_all("h1")
    if len(headings) > 1:
        raise OkumaHatasi("Makalenin tekil başlığı doğrulanamadı.")
    if not headings and not release_title:
        headings = soup.find_all("h1")
    if not headings and articles:
        headings = root.find_all("h2")
    if len(headings) != 1 and not release_title:
        raise OkumaHatasi("Makalenin tekil başlığı doğrulanamadı.")
    title = release_title or " ".join(headings[0].get_text(" ", strip=True).split())
    generic = {"news", "latest news", "all news", "blog", "haberler", "son haberler",
               "sign in", "access denied", "just a moment", "just a moment...",
               "verify you are human", "checking your browser", "security verification",
               "attention required", "enable javascript", "page not found"}
    if not 12 <= len(title) <= 500 or title.casefold().strip(".!… ") in generic:
        raise OkumaHatasi("Sayfanın başlığı gerçek bir makaleyi doğrulamıyor.")
    paragraphs = []
    for element in root.find_all(["p", "li", "h2", "h3", "h4", "blockquote", "pre"]):
        # İç içe paragraf/liste aynı metni iki kez üretmesin.
        if element.find_parent(["p", "li", "blockquote", "pre"]) is not None:
            continue
        text = " ".join(element.get_text(" ", strip=True).split())
        if text and text != title and text not in paragraphs:
            paragraphs.append(text)
    body = "\n\n".join(paragraphs)
    if len(body) < (40 if release_title else EN_AZ_METIN):
        raise OkumaHatasi("Güvenilir makale metni 200 karakterden kısa; yalnız başlık veya özet kaydedilmedi.")
    link_text = sum(len(link.get_text(" ", strip=True)) for link in root.find_all("a", href=True))
    if len(root.find_all("a", href=True)) >= 5 and link_text > len(body) / 2:
        raise OkumaHatasi("Sayfa ağırlıklı olarak bağlantı listesi; makale metni doğrulanamadı.")
    truncated = len(body) > EN_COK_METIN
    body = body[:EN_COK_METIN]
    sentences = re.split(r"(?<=[.!?])\s+|\n\n", body)
    highlights = [sentence.strip() for sentence in sentences if 50 <= len(sentence.strip()) <= 600][:4]
    return {"title": title, "body": body, "highlights": highlights, "truncated": truncated,
            "body_hash": hashlib.sha256(body.encode("utf-8")).hexdigest()}


class OkumaIsleri:
    """Aynı depo için tek okuma hizmeti; oku/durum salt okunur, baslat açık ağ isteğidir.

    baslat aynı çalışan makaleye yeniden çağrılırsa started=False döndürür. Başka
    bir makale çalışıyorsa OkumaMesgul; günlük veya makale bekleme sınırı varsa
    OkumaSiniri yükselir. Arka plan sonucu oku(item_id) ile alınır. highlights,
    özgün dilde seçilmiş cümlelerdir; çeviri veya yapay zekâ özeti değildir.
    """

    def __init__(self, depo, *, fetcher=None, clock=None, daily_limit=GUNLUK_SINIR):
        if type(daily_limit) is not int or not 1 <= daily_limit <= 200:
            raise ValueError("Makale okuma bütçesi 1–200 arasında olmalı.")
        self.daily_limit = daily_limit
        self.depo = depo
        self.fetcher = fetcher or guvenli_sayfa_oku
        self.clock = clock or (lambda: tarih(simdi()))
        self.lock = threading.RLock()
        self.worker = None
        self.closed = False
        self.active_item_id = None
        self.ownership = TaramaKilidi(depo.path.with_name(depo.path.name + ".okuma"))
        if not self.ownership.al():
            raise OkumaMesgul("Bu deponun makale okuma hizmeti başka bir süreçte açık.")
        try:
            self._hazirla()
        except Exception:
            self.closed = True
            self.ownership.birak()
            raise

    def _hazirla(self):
        """Tabloları kurar; önceki uygulama çalışmasından kalan yarım okumayı işaretler."""
        with self.depo.baglanti(write=True) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS article_reads (
                item_id TEXT PRIMARY KEY REFERENCES items(id), status TEXT NOT NULL,
                last_error TEXT, title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
                url TEXT, source_url TEXT, source_revision INTEGER, fetched_at TEXT, body_hash TEXT,
                highlights TEXT NOT NULL DEFAULT '[]', truncated INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT, attempt_id TEXT, attempt_revision INTEGER, attempt_url TEXT
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS article_read_attempts (
                id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id),
                started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, last_error TEXT
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS article_attempts_started ON article_read_attempts(started_at)")
            interrupted = "Uygulama kapanırken okuma yarım kaldı; eski metin korundu. Yeniden okumayı başlatabilirsiniz."
            connection.execute("UPDATE article_reads SET status='kesildi',last_error=? WHERE status='okunuyor'",
                               (interrupted,))
            connection.execute("UPDATE article_read_attempts SET status='kesildi',finished_at=?,last_error=? "
                               "WHERE status='okunuyor'", (self.clock().isoformat(), interrupted))

    def oku(self, item_id):
        """Kalıcı metni döndürür; güncel kart revizyonu farklıysa stale=True işaretler."""
        with self.depo.baglanti() as connection:
            connection.execute("BEGIN")
            item = connection.execute("SELECT id,title,url,revision FROM items WHERE id=?", (item_id,)).fetchone()
            if item is None:
                raise KeyError(item_id)
            row = connection.execute("SELECT * FROM article_reads WHERE item_id=?", (item_id,)).fetchone()
        result = dict(row) if row else {
            "item_id": item_id, "status": "okunmadi", "last_error": None, "title": "", "body": "",
            "url": None, "source_url": None, "source_revision": None, "fetched_at": None,
            "body_hash": None, "highlights": "[]", "truncated": 0, "last_attempt_at": None,
            "attempt_id": None, "attempt_revision": None, "attempt_url": None,
        }
        result["highlights"] = json.loads(result["highlights"])
        result["truncated"] = bool(result["truncated"])
        result["has_body"] = bool(result["body"])
        result["stale"] = bool(result["body"] and (
            result["source_revision"] != item["revision"] or result["source_url"] != item["url"]))
        result.update(current_revision=item["revision"], current_url=item["url"], source_title=item["title"],
                      highlights_label="Kaynak metninden seçilen ilk cümleler")
        last_attempt = tarih(result["last_attempt_at"])
        result["retry_after_seconds"] = max(0, ceil((last_attempt + timedelta(seconds=TEKRAR_BEKLEME_SANIYE)
                                                    - self.clock()).total_seconds())) if last_attempt else 0
        return result

    def durum(self):
        """UTC günü için kullanılan bütçeyi ve çalışan makaleyi bildirir; ağ çağrısı yapmaz."""
        now = self.clock()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.depo.baglanti() as connection:
            used = connection.execute("SELECT COUNT(*) FROM article_read_attempts WHERE started_at>=? AND started_at<?",
                                      (start.isoformat(), (start + timedelta(days=1)).isoformat())).fetchone()[0]
        with self.lock:
            running = bool(self.worker and self.worker.is_alive())
            return {"running": running, "item_id": self.active_item_id if running else None,
                    "daily_limit": self.daily_limit, "daily_used": used,
                    "daily_remaining": max(0, self.daily_limit - used),
                    "cooldown_seconds": TEKRAR_BEKLEME_SANIYE,
                    "budget_resets_at": (start + timedelta(days=1)).isoformat(), "closed": self.closed}

    def baslat(self, item_id):
        """Tek kullanıcı isteği için kota ayırır ve bir makalenin arka plan okumasını başlatır."""
        with self.lock:
            if self.closed:
                raise OkumaMesgul("Makale okuma hizmeti kapalı.")
            if self.worker and self.worker.is_alive():
                if self.active_item_id == item_id:
                    return {**self.oku(item_id), "started": False}
                raise OkumaMesgul("Başka bir makale okunuyor; tamamlandıktan sonra yeniden deneyin.")
            now = self.clock()
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            with self.depo.baglanti(write=True) as connection:
                item = connection.execute("SELECT i.*,s.config FROM items i JOIN sources s ON s.id=i.source_id "
                                          "WHERE i.id=?", (item_id,)).fetchone()
                if item is None:
                    raise KeyError(item_id)
                if connection.execute("SELECT 1 FROM article_reads WHERE status='okunuyor' LIMIT 1").fetchone():
                    raise OkumaMesgul("Başka bir makale okuması sürüyor.")
                used = connection.execute("SELECT COUNT(*) FROM article_read_attempts WHERE started_at>=? AND started_at<?",
                                          (start.isoformat(), (start + timedelta(days=1)).isoformat())).fetchone()[0]
                if used >= self.daily_limit:
                    raise OkumaSiniri(f"Günlük {self.daily_limit} makale okuma sınırına ulaşıldı.",
                                     (start + timedelta(days=1) - now).total_seconds())
                last = connection.execute("SELECT MAX(started_at) FROM article_read_attempts WHERE item_id=?",
                                          (item_id,)).fetchone()[0]
                remaining = ((tarih(last) + timedelta(seconds=TEKRAR_BEKLEME_SANIYE) - now).total_seconds()
                             if last else 0)
                if remaining > 0:
                    raise OkumaSiniri("Aynı makale yeniden okunmadan önce 5 dakika beklenmeli.", remaining)
                attempt = uuid.uuid4().hex
                connection.execute("INSERT INTO article_read_attempts(id,item_id,started_at,status) VALUES(?,?,?,'okunuyor')",
                                   (attempt, item_id, now.isoformat()))
                connection.execute("INSERT INTO article_reads(item_id,status,last_attempt_at,attempt_id,attempt_revision,attempt_url) "
                                   "VALUES(?,'okunuyor',?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET "
                                   "status='okunuyor',last_error=NULL,last_attempt_at=excluded.last_attempt_at,"
                                   "attempt_id=excluded.attempt_id,attempt_revision=excluded.attempt_revision,attempt_url=excluded.attempt_url",
                                   (item_id, now.isoformat(), attempt, item["revision"], item["url"]))
                snapshot = dict(item)
            self.active_item_id = item_id
            self.worker = threading.Thread(target=self._calistir, args=(snapshot, attempt),
                                           name="ufuk-makale-okuma", daemon=True)
            try:
                self.worker.start()
            except Exception:
                self.worker = None
                self.active_item_id = None
                message = "Makale okuma işçisi başlatılamadı; eski metin korundu. Daha sonra yeniden deneyin."
                with self.depo.baglanti(write=True) as connection:
                    connection.execute("UPDATE article_reads SET status='hata',last_error=? WHERE item_id=? AND attempt_id=?",
                                       (message, item_id, attempt))
                    connection.execute("UPDATE article_read_attempts SET status='hata',finished_at=?,last_error=? WHERE id=?",
                                       (self.clock().isoformat(), message, attempt))
                raise OkumaHazirDegil(message) from None
            return {**self.oku(item_id), "started": True}

    def _calistir(self, item, attempt):
        extracted = None
        response = None
        error = None
        try:
            source = json.loads(item["config"])
            response = self.fetcher(item["url"], source)
            extracted = makale_ayristir(response)
        except (CollectionError, OkumaHatasi) as failure:
            error = str(failure)[:600]
        except Exception:
            error = "Makale okunurken beklenmeyen bir hata oluştu; eski metin korundu."
        try:
            with self.lock:
                status = "kesildi" if self.closed else "hata" if error else "hazir"
                if self.closed:
                    error = "Uygulama kapanırken okuma durduruldu; eski metin korundu."
                with self.depo.baglanti(write=True) as connection:
                    current = connection.execute("SELECT attempt_id,status FROM article_reads WHERE item_id=?",
                                                 (item["id"],)).fetchone()
                    if current is None or current["attempt_id"] != attempt or current["status"] != "okunuyor":
                        return
                    finished = self.clock().isoformat()
                    if status == "hazir":
                        connection.execute("UPDATE article_reads SET status='hazir',last_error=NULL,title=?,body=?,"
                                           "url=?,source_url=?,source_revision=?,fetched_at=?,body_hash=?,highlights=?,truncated=? "
                                           "WHERE item_id=? AND attempt_id=?",
                                           (extracted["title"], extracted["body"], response.url, item["url"], item["revision"],
                                            finished, extracted["body_hash"], json.dumps(extracted["highlights"], ensure_ascii=False),
                                            int(extracted["truncated"]), item["id"], attempt))
                    else:
                        connection.execute("UPDATE article_reads SET status=?,last_error=? WHERE item_id=? AND attempt_id=?",
                                           (status, error, item["id"], attempt))
                    connection.execute("UPDATE article_read_attempts SET status=?,finished_at=?,last_error=? WHERE id=?",
                                       (status, finished, error, attempt))
        finally:
            with self.lock:
                self.active_item_id = None
                if self.closed:
                    self.ownership.birak()

    def kapan(self):
        """Yeni işleri kapatır; mevcut ağ sınırı içinde çalışan işçinin bitmesini bekler."""
        with self.lock:
            self.closed = True
            worker = self.worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=SOURCE_TIMEOUT + 2)
        with self.lock:
            if not worker or not worker.is_alive():
                self.ownership.birak()
