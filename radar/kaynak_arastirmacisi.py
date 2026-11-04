"""Yeni kamusal kaynakları sınırlı bütçeyle bulur, beslemeyi doğrular ve geri izlenebilir deneme takibine alır."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import feedparser
from bs4 import BeautifulSoup

from radar.depo import tarih
from radar.hizmet import TaramaKilidi
from radar.kaynak_denetcisi import KaynakDenetcisi, KAPSAM_NOTU, kamusal_adres, kamusal_metin
from radar.kaynak_havuzu import KONULAR
from radar.konular import konu, konu_etiketleri
from radar.kaynak_yonetimi import KaynakYonetimi
from radar.toplayici import CollectionError, kamusal_veri_oku, kaynaklari_yetkilendir


HAFTALIK_ISTEK = 20
HAFTALIK_DENEME = 5
HAFTALIK_YENI_KAYNAK = 5
ETKIN_DENEME_SINIRI = 3
CALISMA_SURESI = 120
TURDA_KONU_SINIRI = 5
KONU_SIRASI = KONULAR
KONU_ADLARI = konu_etiketleri()
ARAMA_TERIMLERI = {topic: konu(topic)["github_queries"] for topic in KONULAR}
KONU_ISARETLERI = {topic: konu(topic)["markers"] for topic in KONULAR}
ODAK_SOZLUGU = {"memory": "memory", "hafıza": "memory", "retrieval": "retrieval", "citation": "citation",
                "robot": "robot", "sensor": "sensor", "sulama": "irrigation", "irrigation": "irrigation",
                "speech": "speech", "video": "video", "voice": "voice", "sesli": "voice"}


class KaynakArastirmaciMesgul(ValueError):
    """Aynı depo için ikinci araştırmacının çalışmasını önler."""


class KaynakArastirmaciSiniri(ValueError):
    """Kalıcı haftalık bütçe veya deneme kapasitesi tükendiğinde yeniden çalıştırmayı sınırlar."""
    def __init__(self, message, next_due_at=None):
        super().__init__(message)
        self.next_due_at = next_due_at


class KaynakAdayCatismasi(ValueError):
    """Başka pencerede değişen aday kararının üzerine yazılmasını önler."""


def tablolari_hazirla(connection):
    for statement in """
        CREATE TABLE IF NOT EXISTS source_scout_budget (
            week TEXT PRIMARY KEY, requests INTEGER NOT NULL DEFAULT 0,
            probes INTEGER NOT NULL DEFAULT 0, trials INTEGER NOT NULL DEFAULT 0,
            runs INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS source_scout_runs (
            id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_scout_schedule (
            id INTEGER PRIMARY KEY CHECK(id=1), next_due_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_scout_candidates (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
            topic TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
            reason TEXT NOT NULL, source_id TEXT REFERENCES sources(id),
            version INTEGER NOT NULL DEFAULT 0, discovered_from TEXT NOT NULL,
            created_at TEXT NOT NULL, checked_at TEXT, feed_url TEXT,
            probe TEXT NOT NULL DEFAULT '{}', activation_week TEXT);
    """.split(";"):
        if statement.strip():
            connection.execute(statement)


def _github_projesi(url):
    try:
        parsed = urlsplit(url)
    except (ValueError, TypeError):
        return None
    if parsed.hostname == "api.github.com":
        match = re.match(r"^/repos/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/|$)", parsed.path)
    elif parsed.hostname == "github.com":
        match = re.match(r"^/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/|$)", parsed.path)
    else:
        return None
    return match[1].lower() if match else None


class KaynakArastirmacisi:
    def __init__(self, depo, ogrenme, fetcher=None, clock=None):
        self.depo, self.ogrenme = depo, ogrenme
        self.fetcher = fetcher or kamusal_veri_oku
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.stop, self.lock = threading.Event(), threading.RLock()
        self.thread, self.active_id, self.schedule_error = None, None, None
        self.process_lock = TaramaKilidi(depo.path.with_name(depo.path.name + ".kaynak-arastirmaci"))
        self.yonetim = KaynakYonetimi(depo)
        self.denetci = KaynakDenetcisi(depo)
        with depo.baglanti(write=True) as connection:
            tablolari_hazirla(connection)
            connection.execute("INSERT OR IGNORE INTO source_scout_schedule VALUES(1,?)", (self._now().isoformat(),))
        self._havuzu_yukle()
        if self.process_lock.al():
            try:
                with depo.baglanti(write=True) as connection:
                    for row in connection.execute("SELECT * FROM source_scout_runs WHERE status='running'").fetchall():
                        result = json.loads(row["result"])
                        result.update(status="interrupted", finished_at=self._now().isoformat())
                        result["errors"].append({"message": "Önceki kaynak araştırması yarım kaldı; kullanılmış bütçe korunuyor."})
                        connection.execute("UPDATE source_scout_runs SET status=?,finished_at=?,result=? WHERE id=?",
                                           (result["status"], result["finished_at"], json.dumps(result, ensure_ascii=False), row["id"]))
                    connection.execute("UPDATE source_scout_candidates SET status='failed',reason=?,version=version+1 WHERE status='probing'",
                                       ("Besleme denemesi yarım kaldı; bütçe iade edilmedi.",))
                self._yarim_eklemeleri_tamamla()
            finally:
                self.process_lock.birak()

    def _now(self):
        value = self.clock()
        value = tarih(value) if isinstance(value, str) else value
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("Kaynak araştırmacısı saati saat dilimi taşımalı.")
        return value.astimezone(timezone.utc)

    def _week(self):
        now = self._now()
        beginning = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
        return beginning.date().isoformat(), (beginning + timedelta(days=7)).isoformat()

    def _butce(self, connection=None):
        if connection is None:
            with self.depo.baglanti() as opened:
                return self._butce(opened)
        week, next_due = self._week()
        row = connection.execute("SELECT * FROM source_scout_budget WHERE week=?", (week,)).fetchone()
        current = dict(row) if row else {"week": week, "requests": 0, "probes": 0, "trials": 0, "runs": 0}
        return {**current, "request_limit": HAFTALIK_ISTEK, "probe_limit": HAFTALIK_DENEME,
                "trial_limit": HAFTALIK_YENI_KAYNAK, "active_trial_limit": ETKIN_DENEME_SINIRI,
                "remaining_requests": max(0, HAFTALIK_ISTEK - current["requests"]), "resets_at": next_due,
                "note": "Model çağrısı yok. İstek sayısı kamusal belge/API okumalarıdır; robots ve izinli yönlendirme bağlantıları ağ katmanında ayrıca sınırlıdır."}

    def _tuket(self, field, limit):
        with self.depo.baglanti(write=True) as connection:
            budget = self._butce(connection)
            if budget[field] >= limit:
                raise KaynakArastirmaciSiniri("Haftalık kaynak araştırması bütçesi doldu.", budget["resets_at"])
            connection.execute("INSERT OR IGNORE INTO source_scout_budget(week) VALUES(?)", (budget["week"],))
            if field not in {"requests", "probes", "trials", "runs"}:
                raise ValueError("Bütçe türü geçersiz.")
            connection.execute(f"UPDATE source_scout_budget SET {field}={field}+1 WHERE week=?", (budget["week"],))

    def _ayar_acik(self):
        settings = self.ogrenme.gorunum().get("settings", {})
        return settings.get("enabled", True) is not False

    def gorunum(self):
        with self.depo.baglanti() as connection:
            last = connection.execute("SELECT result FROM source_scout_runs ORDER BY started_at DESC,rowid DESC LIMIT 1").fetchone()
            candidates = [self._aday_gorunumu(row) for row in connection.execute(
                "SELECT * FROM source_scout_candidates ORDER BY CASE status WHEN 'verified' THEN 0 WHEN 'trial' THEN 1 WHEN 'candidate' THEN 2 ELSE 3 END,created_at DESC LIMIT 200")]
            due = connection.execute("SELECT next_due_at FROM source_scout_schedule WHERE id=1").fetchone()[0]
            running = connection.execute("SELECT 1 FROM source_scout_runs WHERE status='running'").fetchone() is not None
            budget = self._butce(connection)
        result = json.loads(last[0]) if last else None
        return {"running": running, "last_run": result, "next_due_at": due, "budget": budget,
                "candidates": candidates, "schedule_error": self.schedule_error,
                "coverage": result.get("coverage", {"note": KAPSAM_NOTU, "samples": [], "counts": {}}) if result else {"note": KAPSAM_NOTU, "samples": [], "counts": {}},
                "weekly_interval_hours": 168}

    @staticmethod
    def _aday_gorunumu(row):
        value = dict(row)
        value["probe"] = json.loads(value["probe"])
        return value

    def _aday_ekle(self, *, name, url, topic, kind="page", discovered_from, reason="Yeni kaynak adayı; içerik kalitesi henüz bilinmiyor."):
        url, discovered_from = kamusal_adres(url), kamusal_adres(discovered_from)
        if topic not in KONULAR or kind not in {"page", "github", "rss", "atom"}:
            raise ValueError("Kaynak adayı türü veya konusu geçersiz.")
        name = kamusal_metin(name, 200)
        if len(name) < 2:
            raise ValueError("Kaynak adı çok kısa.")
        with self.depo.baglanti(write=True) as connection:
            previous = connection.execute("SELECT id FROM source_scout_candidates WHERE url=?", (url,)).fetchone()
            if previous:
                return previous[0]
            waiting = connection.execute("SELECT COUNT(*) FROM source_scout_candidates WHERE status IN ('candidate','failed','verified','probing')").fetchone()[0]
            if waiting >= 500:
                # Karar geçmişini silmeden eski, hiç doğrulanmamış adaya yer ayırmayı bırak.
                oldest = connection.execute("SELECT id FROM source_scout_candidates WHERE status IN ('candidate','failed') ORDER BY created_at,rowid LIMIT 1").fetchone()
                if oldest is None:
                    return None
                connection.execute("UPDATE source_scout_candidates SET status='retired',reason=?,version=version+1 WHERE id=?",
                                   ("Yeni adaylara yer ayırmak için bekleme listesinden çıkarıldı; eski kayıt korundu.", oldest[0]))
            identifier = "aday_" + hashlib.sha256(url.encode()).hexdigest()[:24]
            connection.execute("INSERT INTO source_scout_candidates(id,name,url,topic,kind,status,reason,discovered_from,created_at) VALUES(?,?,?,?,?,'candidate',?,?,?)",
                               (identifier, name, url, topic, kind, reason[:500], discovered_from, self._now().isoformat()))
            return identifier

    def _havuzu_yukle(self):
        path = Path(__file__).with_name("kaynak_adaylari.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        for candidate in payload.get("candidates", [])[:100]:
            try:
                self._aday_ekle(name=candidate["name"], url=candidate["url"], topic=candidate["topic"],
                               discovered_from=candidate["url"], reason="Önceki aday havuzu: " + candidate.get("reason", "Doğrulanmayı bekliyor."))
            except (KeyError, ValueError, TypeError):
                continue

    def _yetkilendir(self, url):
        host = urlsplit(kamusal_adres(url)).hostname
        source = {"id": "kaynak_arastirmaci", "name": "Kamusal kaynak araştırması", "topic": "ai", "url": url,
                  "fetch_url": url, "kind": "rss", "interval_hours": 168, "enabled": True,
                  "language": "bilinmiyor", "publisher": host, "source_type": "Araştırma", "branches": ["Kaynak keşfi"],
                  "benefit": "Sınırlı kamusal kaynak doğrulaması", "fetch_hosts": [host], "item_hosts": [host]}
        kaynaklari_yetkilendir([source])
        return host

    def _oku(self, url, hosts, deadline):
        if self.stop.is_set() or time.monotonic() >= deadline:
            raise CollectionError("Kaynak araştırması durdu veya süre sınırına ulaştı.")
        host = urlsplit(kamusal_adres(url)).hostname
        if host not in hosts:
            raise CollectionError("Kaynak alan adı doğrulanan adayla eşleşmiyor.")
        self._yetkilendir(url)
        self._tuket("requests", HAFTALIK_ISTEK)
        response = self.fetcher(url, [host], stop_event=self.stop, deadline=deadline)
        if len(response.body) > 2_000_000:
            raise CollectionError("Kaynak yanıtı boyut sınırını aştı.")
        final = kamusal_adres(response.url)
        if urlsplit(final).hostname != host:
            raise CollectionError("Kaynak başka bir alan adına yönlendi; otomatik izin verilmedi.")
        if response.status != 200:
            raise CollectionError(f"Kaynak okunamadı (HTTP {response.status}); erişim engeli aşılmadı.")
        return response

    def _hazirla(self):
        if self.stop.is_set() or self.active_id or not self.process_lock.al():
            raise KaynakArastirmaciMesgul("Kaynak araştırmacısı meşgul veya kapanıyor.")
        try:
            budget = self._butce()
            if budget["remaining_requests"] == 0 or budget["runs"] >= 3:
                raise KaynakArastirmaciSiniri("Haftalık kaynak araştırması bütçesi doldu.", budget["resets_at"])
            record = {"id": uuid.uuid4().hex, "status": "running", "started_at": self._now().isoformat(), "finished_at": None,
                      "errors": [], "searches": [], "probed": [], "added_sources": [], "coverage": {}, "budget_before": budget}
            with self.depo.baglanti(write=True) as connection:
                connection.execute("INSERT OR IGNORE INTO source_scout_budget(week) VALUES(?)", (budget["week"],))
                connection.execute("UPDATE source_scout_budget SET runs=runs+1 WHERE week=?", (budget["week"],))
                connection.execute("INSERT INTO source_scout_runs VALUES(?,?,NULL,'running',?)",
                                   (record["id"], record["started_at"], json.dumps(record, ensure_ascii=False)))
                connection.execute("UPDATE source_scout_schedule SET next_due_at=? WHERE id=1", (budget["resets_at"],))
            self.active_id = record["id"]
            return record
        except BaseException:
            self.process_lock.birak()
            raise

    def baslat(self):
        with self.lock:
            record = self._hazirla()
            try:
                self.thread = threading.Thread(target=self._yurut, args=(record,), daemon=True)
                self.thread.start()
            except BaseException:
                record.update(status="failed", finished_at=self._now().isoformat())
                self._sakla(record)
                self.active_id = None
                self.process_lock.birak()
                raise
        return {"started": True, "run_id": record["id"], "status": "running"}

    def calistir(self):
        with self.lock:
            record = self._hazirla()
        self._yurut(record)
        return record

    def _sakla(self, record):
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE source_scout_runs SET status=?,finished_at=?,result=? WHERE id=?",
                               (record["status"], record["finished_at"], json.dumps(record, ensure_ascii=False), record["id"]))

    def _sorgu(self, topic, week_index):
        base = ARAMA_TERIMLERI[topic][week_index % 2]
        with self.depo.baglanti() as connection:
            titles = [row[0].lower() for row in connection.execute(
                "SELECT title FROM items WHERE topic=? AND feedback='yararli' ORDER BY last_seen_at DESC LIMIT 30", (topic,))]
        synonyms = {}
        for token, word in ODAK_SOZLUGU.items():
            synonyms.setdefault(word, []).append(token)
        scores = {word: sum(any(re.search(r"\b" + re.escape(token) + r"\b", title) for token in tokens) for title in titles)
                  for word, tokens in synonyms.items()}
        focus = max(scores, key=lambda key: scores[key]) if titles else None
        if focus and scores[focus] >= 2 and focus not in base:
            base += " " + focus
        since = (self._now() - timedelta(days=730)).date().isoformat()
        return base + " archived:false fork:false stars:>=10 pushed:>=" + since

    def _depo_niteligi(self, row, topic):
        """Düşük eşiği geçen ilgi ve etkinlik sinyallerini ayırır; yararlılık veya güvenilirlik kararı vermez."""
        if not isinstance(row, dict) or row.get("archived") is not False or row.get("fork") is not False or row.get("disabled") is True:
            return None, "Kapalı, arşivlenmiş veya kopya depo."
        name = row.get("full_name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
            return None, "Depo kimliği doğrulanamadı."
        owner, project = name.lower().split("/")
        if owner == project:
            return None, "Kişisel profil deposu kaynak akışı sayılmadı."
        topics = row.get("topics") if isinstance(row.get("topics"), list) else []
        text = " ".join([name, kamusal_metin(row.get("description"), 600),
                         " ".join(value[:80] for value in topics[:30] if isinstance(value, str))]).lower()
        if re.search(r"(?:^|[^a-z])(?:crack(?:ed)?|keygen|serial[- ]?key|full[- ]?version|free[- ]?download|license[- ]?bypass)(?:$|[^a-z])", text):
            return None, "İndirme veya lisans aşma spam işareti bulundu."
        matched = [token for token in KONU_ISARETLERI[topic] if re.search(r"\b" + re.escape(token), text)]
        if not matched:
            return None, "Ad, açıklama ve etiketlerde konu işareti yok."
        stars, size = row.get("stargazers_count"), row.get("size")
        pushed, created = tarih(row.get("pushed_at")), tarih(row.get("created_at"))
        if type(stars) is not int or stars < 10 or type(size) is not int or size <= 0:
            return None, "Henüz yeterli kamusal etkinlik sinyali yok."
        if (not pushed or not created or self._now() - pushed > timedelta(days=730)
                or pushed > self._now() + timedelta(days=1) or self._now() - created < timedelta(days=14)):
            return None, "Depo yaşı veya son etkinlik tarihi deneme ölçütünü karşılamıyor."
        return {"validated": True, "stars": stars, "pushed_at": pushed.isoformat(), "created_at": created.isoformat(),
                "matched_terms": matched, "minimum_stars": 10,
                "note": "Yıldız, konu etiketi ve yakın etkinlik yalnız aday sıralama sinyalidir; yararlılık kanıtı değildir."}, None

    def _github_ara(self, topic, week_index, record, deadline):
        query = self._sorgu(topic, week_index)
        url = "https://api.github.com/search/repositories?" + urlencode({"q": query, "sort": "stars", "order": "desc", "per_page": 10})
        response = self._oku(url, ["api.github.com"], deadline)
        payload = json.loads(response.body)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise CollectionError("GitHub beklenen depo listesini döndürmedi.")
        count, homepage_added, skipped = 0, False, {}
        for row in payload["items"][:10]:
            signals, reason = self._depo_niteligi(row, topic)
            if signals is None:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            name = row.get("full_name", "")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
                continue
            repo_url = "https://github.com/" + name
            if row.get("html_url", "").lower() != repo_url.lower():
                continue
            if self._mevcut_kaynak(repo_url):
                continue
            identifier = self._aday_ekle(name=name, url=repo_url, topic=topic, kind="github", discovered_from=url,
                                         reason="Konu, yıldız ve yakın etkinlik sinyali var; sürüm beslemesi ve yararı henüz doğrulanmadı.")
            count += bool(identifier)
            if identifier:
                with self.depo.baglanti(write=True) as connection:
                    saved = connection.execute("SELECT probe,status FROM source_scout_candidates WHERE id=?", (identifier,)).fetchone()
                    probe = json.loads(saved["probe"])
                    probe["discovery_signals"] = signals
                    connection.execute("UPDATE source_scout_candidates SET probe=? WHERE id=?", (json.dumps(probe, ensure_ascii=False), identifier))
            if not homepage_added and row.get("homepage"):
                try:
                    homepage = kamusal_adres(row["homepage"])
                    if urlsplit(homepage).hostname != "github.com" and not self._mevcut_kaynak(homepage):
                        homepage_added = bool(self._aday_ekle(name=name + " · proje sitesi", url=homepage, topic=topic,
                            discovered_from=repo_url, reason="Depo kendi proje sitesini bildirdi; besleme ve konu yararı henüz doğrulanmadı."))
                except (ValueError, TypeError):
                    pass
            if count >= 4:
                break
        record["searches"].append({"topic": topic, "query": query, "provider": "github", "candidates": count,
                                   "order": "stars", "skipped": skipped,
                                   "incomplete_results": bool(payload.get("incomplete_results", False))})

    def _mevcut_kaynak(self, url):
        project = _github_projesi(url)
        with self.depo.baglanti() as connection:
            for row in connection.execute("SELECT id,config FROM sources"):
                source = json.loads(row["config"])
                if url in {source.get("url"), source.get("fetch_url")} or (project and project in {
                        _github_projesi(source.get("url")), _github_projesi(source.get("fetch_url"))}):
                    return row["id"]
        return None

    def _aday_guncelle(self, identifier, **fields):
        allowed = {"status", "reason", "checked_at", "feed_url", "kind", "probe", "source_id", "activation_week"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("Aday alanı geçersiz.")
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE source_scout_candidates SET " + ",".join(key + "=?" for key in fields) + ",version=version+1 WHERE id=?",
                               (*fields.values(), identifier))

    def _besleme_dene(self, candidate, deadline):
        self._tuket("probes", HAFTALIK_DENEME)
        self._aday_guncelle(candidate["id"], status="probing", checked_at=self._now().isoformat(), reason="Besleme güvenli biçimde okunuyor.")
        host = urlsplit(candidate["url"]).hostname
        previous_probe = json.loads(candidate.get("probe") or "{}")
        original_kind = previous_probe.get("original_kind", candidate["kind"])
        project = _github_projesi(candidate["url"]) if original_kind == "github" else None
        feed_url = candidate.get("feed_url") or ("https://github.com/" + project + "/releases.atom" if project else candidate["url"])
        response = self._oku(feed_url, [host], deadline)
        parsed = feedparser.parse(response.body)
        if not parsed.get("version") and original_kind == "page":
            soup = BeautifulSoup(response.body, "html.parser")
            links = [node.get("href") for node in soup.find_all("link", limit=100)
                     if "alternate" in node.get("rel", []) and node.get("type", "").lower() in {"application/rss+xml", "application/atom+xml"}]
            addresses = []
            for href in links[:10]:
                try:
                    address = kamusal_adres(urljoin(response.url, href))
                except (ValueError, TypeError):
                    continue
                if urlsplit(address).hostname == host:
                    addresses.append(address)
            if not addresses:
                raise CollectionError("Sayfa aynı alan adında bir RSS/Atom beslemesi ilan etmiyor; otomatik kaynak yapılmadı.")
            feed_url = addresses[0]
            response = self._oku(feed_url, [host], deadline)
            parsed = feedparser.parse(response.body)
        if not parsed.get("version") or parsed.get("bozo") or not parsed.get("entries"):
            raise CollectionError("Besleme geçerli, dolu RSS/Atom olarak doğrulanamadı.")
        accepted = []
        for entry in parsed["entries"][:50]:
            try:
                url = kamusal_adres(entry.get("link"))
            except (ValueError, TypeError):
                continue
            if urlsplit(url).hostname != host or not kamusal_metin(entry.get("title")):
                continue
            if project and not urlsplit(url).path.lower().startswith("/" + project + "/releases/"):
                continue
            accepted.append({"title": kamusal_metin(entry.get("title"), 200), "url": url})
        if not accepted:
            raise CollectionError("Beslemede doğrulanan yayıncıya ait okunabilir kayıt bulunamadı.")
        probe = {"validated": True, "method": "same_host_feed_parse", "sample_count": len(accepted),
                 "sample": accepted[:3], "checked_at": self._now().isoformat(), "original_kind": original_kind,
                 "discovery_signals": previous_probe.get("discovery_signals"),
                 "note": "Besleme yapısı ve yayıncı bağlantısı doğrulandı; içerik yararı ve iddialar henüz doğrulanmadı."}
        self._aday_guncelle(candidate["id"], status="verified", feed_url=feed_url,
                           kind="atom" if parsed["version"].startswith("atom") else "rss", probe=json.dumps(probe, ensure_ascii=False),
                           reason="Besleme doğrulandı; sınırlı deneme takibine uygun.")
        return probe

    def _aktif_deneme_sayisi(self):
        view = self.ogrenme.gorunum()
        sources = view.get("sources", [])
        with self.depo.baglanti() as connection:
            pending = [dict(row) for row in connection.execute("SELECT id,source_id,status FROM source_scout_candidates WHERE status IN ('trial','activating')")]
        if not sources:
            return len(pending)
        active = {source.get("source_id", source.get("id", "test_" + str(index))) for index, source in enumerate(sources)
                  if source.get("lifecycle") == "trial" and source.get("enabled", True)}
        # Öğrenme işaretlemesinden önce çöken ekleme de yer ayırır; yeniden açılış kapasiteyi aşamaz.
        active.update(row["source_id"] or "pending:" + row["id"] for row in pending if row["status"] == "activating")
        return len(active)

    def _denemeye_al(self, candidate):
        if candidate["status"] == "trial":
            return candidate["source_id"]
        if candidate["status"] not in {"verified", "activating"} or not json.loads(candidate["probe"]).get("validated"):
            raise ValueError("Yalnız beslemesi doğrulanan aday denemeye alınabilir.")
        if candidate["status"] != "activating":
            existing = self._mevcut_kaynak(candidate["feed_url"])
            if existing:
                self._aday_guncelle(candidate["id"], status="duplicate", source_id=existing, reason="Bu kaynak zaten takip ediliyor; mevcut tercihler korundu.")
                return None
            if self._aktif_deneme_sayisi() >= ETKIN_DENEME_SINIRI:
                raise KaynakArastirmaciSiniri("Aynı anda en fazla üç yeni kaynak denemesi yapılabilir.", self._week()[1])
            with self.depo.baglanti() as connection:
                if connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] >= 200:
                    raise KaynakArastirmaciSiniri("Kaynak kütüphanesi 200 kayıt sınırına ulaştı.", self._week()[1])
            if not tarih(candidate["checked_at"]) or self._now() - tarih(candidate["checked_at"]) > timedelta(days=7):
                raise ValueError("Besleme doğrulaması eskidi; yeni haftalık deneme gerekli.")
            self._tuket("trials", HAFTALIK_YENI_KAYNAK)
            self._aday_guncelle(candidate["id"], status="activating", activation_week=self._week()[0], reason="Doğrulanan kaynak deneme takibine ekleniyor.")
        source_id = candidate["source_id"] or self._mevcut_kaynak(candidate["feed_url"])
        if not source_id:
            source = self.yonetim.ekle(name=candidate["name"], fetch_url=candidate["feed_url"], kind=candidate["kind"],
                                       topic=candidate["topic"], publisher=urlsplit(candidate["feed_url"]).hostname,
                                       branches=[KONU_ADLARI[candidate["topic"]], "Yeni kaynak denemesi"], interval_hours=72,
                                       source_type="Açık kaynak" if _github_projesi(candidate["url"]) else "Teknik yayın")
            source_id = source["id"]
            self._aday_guncelle(candidate["id"], source_id=source_id)
        # Özel kaynak okuyucusunun sınırlarını koru; kaynağı kimin seçtiğini doğru kaydet.
        with self.depo.baglanti(write=True) as connection:
            row = connection.execute("SELECT config FROM custom_sources WHERE source_id=?", (source_id,)).fetchone()
            if row:
                config = json.loads(row[0])
                config.update(benefit="UFUK araştırmacısının deneme kaynağı; yararı henüz doğrulanmadı.",
                              discovered_from=candidate["discovered_from"], scout_candidate_id=candidate["id"])
                encoded = json.dumps(config, ensure_ascii=False)
                connection.execute("UPDATE sources SET config=? WHERE id=?", (encoded, source_id))
                connection.execute("UPDATE custom_sources SET config=? WHERE source_id=?", (encoded, source_id))
        self.ogrenme.kaynak_denemeye_al(source_id, provenance={"candidate_id": candidate["id"], "discovered_from": candidate["discovered_from"],
                                                            "validated_at": candidate["checked_at"], "method": "bounded_source_scout"})
        self._aday_guncelle(candidate["id"], status="trial", source_id=source_id, reason="Sınırlı deneme takibinde; katkısı ve maliyeti ölçülüyor.")
        kaynaklari_yetkilendir(self.yonetim.ozel_kaynaklar())
        return source_id

    def _yarim_eklemeleri_tamamla(self):
        with self.depo.baglanti() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM source_scout_candidates WHERE status='activating'")]
        for row in rows:
            try:
                self._denemeye_al(row)
            except Exception:
                self.schedule_error = "Yarım kalan kaynak eklemesi tamamlanamadı; mevcut kayıt korunuyor."

    def aday_karari(self, identifier, decision, expected_version):
        if decision not in {"trial", "dismiss"} or type(expected_version) is not int or expected_version < 0:
            raise ValueError("Kaynak adayı kararı veya sürümü geçersiz.")
        with self.lock:
            if self.active_id or not self.process_lock.al():
                raise KaynakArastirmaciMesgul("Kaynak araştırması bitince aday kararı verilebilir.")
            try:
                with self.depo.baglanti() as connection:
                    row = connection.execute("SELECT * FROM source_scout_candidates WHERE id=?", (identifier,)).fetchone()
                if row is None:
                    raise KeyError(identifier)
                candidate = dict(row)
                if candidate["version"] != expected_version:
                    raise KaynakAdayCatismasi("Kaynak adayı başka pencerede değişti; güncel sonucu açın.")
                if candidate["status"] in {"trial", "activating"} and decision == "dismiss":
                    raise ValueError("Takibe alınmış kaynağın ayarları kaynak kartından yönetilir.")
                if decision == "trial":
                    self._denemeye_al(candidate)
                else:
                    self._aday_guncelle(identifier, status="dismissed", reason="Kullanıcı bu kaynak adayını uygun görmedi.")
                with self.depo.baglanti() as connection:
                    result = self._aday_gorunumu(connection.execute("SELECT * FROM source_scout_candidates WHERE id=?", (identifier,)).fetchone())
                return result
            finally:
                self.process_lock.birak()

    def _yurut(self, record):
        deadline = time.monotonic() + CALISMA_SURESI
        try:
            self._yarim_eklemeleri_tamamla()
            week_index = self._now().date().toordinal() // 7
            offset = week_index % len(KONU_SIRASI)
            topics = (KONU_SIRASI[offset:] + KONU_SIRASI[:offset])[:TURDA_KONU_SINIRI]
            record["coverage"] = self.denetci.calistir(run_id=record["id"], now=self._now(), topics=topics,
                week_index=week_index, fetcher=lambda url, hosts: self._oku(url, hosts, deadline))
            # Karşılaştırma anlık havuzda tamamlandıktan sonra yeni yayıncı bağlantıları aday olur.
            # Bir makale sayfası yalnız kendi ilan ettiği aynı alan adlı beslemeyi kanıtlayabilir.
            seen_topics = set()
            for sample in record["coverage"].get("samples", []):
                landing = sample.get("landing_url")
                if sample["stage"] != "missing_source" or not landing or sample["topic"] in seen_topics:
                    continue
                if urlsplit(landing).hostname in {"doi.org", "openalex.org", "arxiv.org", "www.arxiv.org"}:
                    continue
                self._aday_ekle(name=urlsplit(landing).hostname, url=landing, topic=sample["topic"],
                               discovered_from=sample["url"], reason="Bağımsız sorguda yeni yayıncı bulundu; beslemesi ve yararı henüz doğrulanmadı.")
                seen_topics.add(sample["topic"])
            self._sakla(record)
            for topic in topics:
                try:
                    self._github_ara(topic, week_index, record, deadline)
                except Exception as exc:
                    message = str(exc)[:300] if isinstance(exc, (CollectionError, KaynakArastirmaciSiniri)) else "Kaynak arama yanıtı işlenemedi."
                    record["errors"].append({"topic": topic, "message": message})
            for topic_index, topic in enumerate(topics):
                if self.stop.is_set() or time.monotonic() >= deadline or self._butce()["probes"] >= HAFTALIK_DENEME:
                    break
                with self.depo.baglanti() as connection:
                    preferred_kind = "github" if topic_index < 3 else "page"
                    candidates = [dict(row) for row in connection.execute(
                        "SELECT * FROM source_scout_candidates WHERE topic=? AND status IN ('candidate','failed','verified') ORDER BY checked_at IS NOT NULL,checked_at,created_at LIMIT 100", (topic,))]
                candidates = [row for row in candidates if not row["checked_at"] or tarih(row["checked_at"]) < self._now() - timedelta(days=7)]
                def priority(row):
                    probe = json.loads(row["probe"])
                    original_kind = probe.get("original_kind", row["kind"])
                    signals = probe.get("discovery_signals") or {}
                    if original_kind == "github" and not signals.get("validated"):
                        return (-1, 0, 0)
                    path = urlsplit(row["url"]).path.lower()
                    known_feed = bool(row.get("feed_url")) or row["kind"] in {"rss", "atom"}
                    score = 50 if known_feed else 25 if re.search(r"(?:blog|news|feed|rss|atom)", path) else 0
                    if original_kind == "github":
                        score = min(signals.get("stars", 0), 1000000)
                    return (int(original_kind == preferred_kind), int(not row["checked_at"]), score)
                candidates = [row for row in candidates if priority(row)[0] >= 0]
                candidate = max(candidates, key=priority, default=None)
                if not candidate:
                    continue
                existing = self._mevcut_kaynak(candidate["url"])
                if existing:
                    self._aday_guncelle(candidate["id"], status="duplicate", source_id=existing, reason="Bu proje veya kaynak zaten takip ediliyor.")
                    continue
                try:
                    self._besleme_dene(candidate, deadline)
                    record["probed"].append(candidate["id"])
                    with self.depo.baglanti() as connection:
                        current = dict(connection.execute("SELECT * FROM source_scout_candidates WHERE id=?", (candidate["id"],)).fetchone())
                    try:
                        added = self._denemeye_al(current)
                        if added:
                            record["added_sources"].append(added)
                    except KaynakArastirmaciSiniri:
                        pass
                except Exception as exc:
                    message = str(exc)[:300] if isinstance(exc, (CollectionError, KaynakArastirmaciSiniri)) else "Aday beslemesi işlenemedi veya takip kaydı tamamlanamadı."
                    with self.depo.baglanti() as connection:
                        state = connection.execute("SELECT status FROM source_scout_candidates WHERE id=?", (candidate["id"],)).fetchone()[0]
                    if state != "activating":
                        self._aday_guncelle(candidate["id"], status="failed", reason=message)
                    record["errors"].append({"candidate_id": candidate["id"], "topic": topic, "message": message})
                self._sakla(record)
            good = bool(record["searches"] or record["probed"] or record["coverage"].get("topics_checked"))
            errors = record["errors"] or record["coverage"].get("errors")
            record["status"] = "interrupted" if self.stop.is_set() else "partial" if good and errors else "completed" if good else "failed"
        except Exception:
            record["status"] = "failed"
            record["errors"].append({"message": "Kaynak araştırması tamamlanamadı; mevcut kaynaklar ve bütçe kayıtları korundu."})
        finally:
            record.update(finished_at=self._now().isoformat(), budget_after=self._butce())
            try:
                self._sakla(record)
            finally:
                self.active_id = None
                self.process_lock.birak()

    def zamanli_calistir(self):
        """Günlük yerel ölçümü yeniler; etkinse haftalık kaynak araştırmasını bağımsız işçide çalıştırır."""
        while not self.stop.is_set():
            try:
                self.ogrenme.degerlendir()
                if self._ayar_acik() and (tarih(self.gorunum()["next_due_at"]) or self._now()) <= self._now():
                    self.baslat()
                self.schedule_error = None
            except (KaynakArastirmaciMesgul, KaynakArastirmaciSiniri):
                pass
            except Exception:
                self.schedule_error = "Kaynak öğrenme denetimi tamamlanamadı; sonraki kontrolde yeniden denenecek."
            if self.stop.wait(900):
                break

    def kapan(self):
        self.stop.set()
        if self.thread and self.thread is not threading.current_thread() and self.thread.is_alive():
            self.thread.join(timeout=40)
