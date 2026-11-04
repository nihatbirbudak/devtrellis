"""Vadesi gelen kamusal kaynakları sınırlı sayıda tarar ve sonucu depoya işler."""

import errno
import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from radar.depo import simdi, tarih

LOGGER = logging.getLogger("radar")


def bakim_bilgileri(connection):
    """Kaynağın son doğrulanmış proje bakım durumunu verir; hata eski arşiv bilgisini silmez."""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_maintenance'").fetchone():
        return {}
    return {row["source_id"]: dict(row) for row in connection.execute("SELECT * FROM source_maintenance")}


def siradaki_kaynaklar(sources, now, source_id=None):
    """Vadesi gelenleri eski kontrolden başlayarak konulara sırayla dağıtır."""
    groups = defaultdict(deque)
    for source in sorted(sources, key=lambda item: (item.get("last_checked_at") or "", item["id"])):
        due = tarih(source.get("next_due_at"))
        if (source_id and source["id"] != source_id) or not source.get("enabled", True):
            continue
        if source.get("kind") == "discovery":
            continue
        if (due and due > now) or (source.get("failures", 0) >= 3 and not source.get("retry_pending")):
            continue
        groups[source.get("topic", "diger")].append(source)
    result = []
    while any(groups.values()):
        for topic in sorted(groups):
            if groups[topic]:
                result.append(groups[topic].popleft())
    return result


class TaramaKilidi:
    """Aynı veri deposunu okuyan süreçlerin birlikte kaynak taramasını önler."""

    def __init__(self, database_path):
        database_path = Path(database_path).resolve()
        self.path = database_path.with_name(database_path.name + ".tarama.lock")
        self.file = None

    def al(self):
        # Dosya silinmez: başka süreç aynı dosyada kilit bekliyor olabilir.
        handle = self.path.open("a+b", buffering=0)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        except BaseException:
            handle.close()
            raise
        self.file = handle
        return True

    def birak(self):
        if self.file is not None:
            # İşletim sistemi dosya kapandığında veya süreç çöktüğünde kilidi bırakır.
            self.file.close()
            self.file = None


class Toplama:
    def __init__(self, depo, collector=None):
        from radar.kaynak_yonetimi import KaynakYonetimi
        from radar.kaynak_ogrenme import KaynakOgrenme
        self.depo = depo
        self.kaynak_yonetimi = KaynakYonetimi(depo)
        self.kaynak_ogrenme = KaynakOgrenme(depo)
        self.collector = collector
        self.lock = threading.Lock()
        self.tarama_kilidi = TaramaKilidi(depo.path)
        self.stop = threading.Event()
        self.thread = None
        self.state = {"running": False, "last_finished_at": None, "message": "Henüz tarama yapılmadı."}
        with depo.baglanti() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS source_fetch_progress ("
                               "source_id TEXT PRIMARY KEY REFERENCES sources(id),fetch_url TEXT NOT NULL,"
                               "next_url TEXT,pages_completed INTEGER NOT NULL DEFAULT 0,"
                               "last_checked_at TEXT,last_error TEXT,truncated INTEGER NOT NULL DEFAULT 0)")
            connection.execute("CREATE TABLE IF NOT EXISTS source_maintenance (source_id TEXT PRIMARY KEY REFERENCES sources(id),"
                               "checked_at TEXT,archived INTEGER,disabled INTEGER,pushed_at TEXT,last_release_at TEXT,"
                               "repository_id INTEGER,status TEXT NOT NULL,error TEXT)")

    def gorunum(self):
        with self.lock:
            result = dict(self.state)
        sources = self.depo.kaynaklar()
        result["remaining_due"] = len(siradaki_kaynaklar(sources, tarih(simdi())))
        result["batch_limit"] = 12
        with self.depo.baglanti() as connection:
            result["pagination"] = [dict(row) for row in connection.execute("SELECT * FROM source_fetch_progress ORDER BY source_id")]
            result["maintenance"] = list(bakim_bilgileri(connection).values())
        checked = [date for source in sources if (date := tarih(source.get("last_checked_at")))]
        successful = [date for source in sources if (date := tarih(source.get("last_success_at")))]
        result["last_checked_at"] = max(checked).isoformat() if checked else None
        result["last_success_at"] = max(successful).isoformat() if successful else None
        result["last_finished_at"] = result["last_checked_at"]
        if checked and not result.get("error") and not result["running"]:
            failed = sum(bool(source.get("last_error")) or source.get("failures", 0) > 0
                         for source in sources)
            healthy = sum(bool(source.get("last_success_at")) and not source.get("last_error")
                          and not source.get("failures", 0) for source in sources)
            unseen = sum(not source.get("last_checked_at") for source in sources)
            result["message"] = (f"Kaydedilmiş kontroller: {healthy} kaynak başarılı, "
                                 f"{failed} kaynak sorunlu, {unseen} kaynak henüz kontrol edilmedi.")
        return result

    def _hazirla(self, source_id):
        """Bellek kilidi tutulurken tarama hakkını işletim sisteminden alır."""
        if self.stop.is_set() or self.state["running"]:
            return False
        if source_id and not any(source["id"] == source_id for source in self.depo.kaynaklar()):
            raise KeyError(source_id)
        if not self.tarama_kilidi.al():
            return False
        self.state.update(running=True, error=None, message="Vadesi gelen kaynaklar kontrol ediliyor.")
        return True

    def baslat(self, source_id=None):
        with self.lock:
            if not self._hazirla(source_id):
                return False
            try:
                self.thread = threading.Thread(target=self._tara, args=(source_id,), daemon=True)
                self.thread.start()
            except BaseException:
                self.state.update(running=False, message="Tarama başlatılamadı.")
                self.tarama_kilidi.birak()
                raise
        return True

    def calistir(self, source_id=None):
        """Doğrudan çağrıldığında da aynı tarama kilidini alarak çalışır."""
        with self.lock:
            if not self._hazirla(source_id):
                return False
        self._tara(source_id)
        return True

    def _tara(self, source_id=None):
        successful = failed = added = changed = checked = 0
        text = "Toplama tamamlanamadı; yerel uygulama kaydı incelenmeli."
        error = None
        try:
            if self.collector is None:
                from radar.toplayici import collect_source
                collector = collect_source
            else:
                collector = self.collector
            now = tarih(simdi())
            candidates = siradaki_kaynaklar(self.depo.kaynaklar(), now, source_id)
            for source in candidates[:12]:
                if self.stop.is_set():
                    break
                # Önceki kaynak okunurken değişen kapatma, aralık ve yeniden deneme
                # tercihleri sonraki ağ çağrısından önce yeniden okunur.
                source = self.kaynak_yonetimi.oku(source["id"])
                if not siradaki_kaynaklar([source], tarih(simdi()), source["id"]):
                    continue
                if source.get("retry_pending"):
                    if not self.kaynak_yonetimi.yeniden_deneme_al(source["id"]):
                        continue
                    source = self.kaynak_yonetimi.oku(source["id"])
                    if not source.get("enabled", True):
                        continue
                checked += 1
                started_at, started_clock = simdi(), time.monotonic()
                measurement = {'status': 'error'}
                try:
                    options = {}
                    if self.collector is None:
                        with self.depo.baglanti() as connection:
                            progress = connection.execute("SELECT * FROM source_fetch_progress WHERE source_id=? AND fetch_url=?",
                                                          (source["id"], source["fetch_url"])).fetchone()
                            maintenance = bakim_bilgileri(connection).get(source["id"])
                            if maintenance:
                                source = {**source, "maintenance": maintenance}
                        options = {"continuation": progress["next_url"] if progress else None, "stop_event": self.stop}
                    result = collector(source, etag=source.get("etag"), modified=source.get("modified"), **options)
                    measurement.update(status='partial' if result.get('error') else 'not_modified' if result.get('not_modified') else 'ok',
                                       pages=result.get('pages'), error=result.get('error'))
                    if result.get('not_modified'):
                        measurement.update(returned=0, added=0, changed=0, duplicates=0)
                    if result.get("maintenance"):
                        maintenance = result["maintenance"]
                        with self.depo.baglanti(write=True) as connection:
                            previous = bakim_bilgileri(connection).get(source["id"], {})
                            values = {**previous, **{key: value for key, value in maintenance.items() if value is not None}}
                            values["error"] = maintenance.get("error")
                            connection.execute("INSERT OR REPLACE INTO source_maintenance VALUES(?,?,?,?,?,?,?,?,?)",
                                               (source["id"], values.get("checked_at"), values.get("archived"), values.get("disabled"),
                                                values.get("pushed_at"), values.get("last_release_at"), values.get("repository_id"),
                                                values["status"], values.get("error")))
                    if not result.get("not_modified"):
                        items = result.get("items", [])
                        if not items and source.get("kind") not in {"rss", "atom", "github_releases"}:
                            raise ValueError("Sayfa açıldı fakat geçerli haber çıkarılamadı; kaynak denetimi gerekiyor.")
                        from radar.toplayici import MAX_ITEMS, MAX_PAGES
                        counts = self.depo.kaydet(source["id"], items[:MAX_ITEMS * MAX_PAGES])
                        processed = len(items[:MAX_ITEMS * MAX_PAGES])
                        measurement.update(returned=processed, added=counts['added'], changed=counts['changed'],
                                           duplicates=max(0, processed - counts['added'] - counts['changed']))
                        added += counts["added"]
                        changed += counts["changed"]
                    if "pages" in result:
                        with self.depo.baglanti(write=True) as connection:
                            connection.execute("INSERT INTO source_fetch_progress VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
                                               "pages_completed=CASE WHEN fetch_url=excluded.fetch_url THEN pages_completed+excluded.pages_completed ELSE excluded.pages_completed END,"
                                               "fetch_url=excluded.fetch_url,next_url=excluded.next_url,last_checked_at=excluded.last_checked_at,"
                                               "last_error=excluded.last_error,truncated=excluded.truncated",
                                               (source["id"], source["fetch_url"], result.get("next_url"), result.get("pages", 0),
                                                simdi(), result.get("error"), int(result.get("truncated", False))))
                    self.depo.tarama_sonucu(source["id"], error=result.get("error"), etag=result.get("etag"), modified=result.get("modified"))
                    failed += bool(result.get("error"))
                    successful += not bool(result.get("error"))
                except Exception as exc:
                    # Kimlik, tam cevap veya URL sorgusu günlüğe dökülmez.
                    from radar.toplayici import FetchError
                    message = str(exc) if isinstance(exc, (ValueError, FetchError)) else "Kaynak erişimi tamamlanamadı; ağ veya kaynak yanıtı denetlenmeli."
                    self.depo.tarama_sonucu(source["id"], error=message)
                    measurement.update(status='error', error=message)
                    failed += 1
                finally:
                    # Ölçüm arızası tamamlanmış kaynak kaydını başarısız diye tekrar yazmaz.
                    try:
                        self.kaynak_ogrenme.tarama_kaydet(source['id'], started_at=started_at,
                            finished_at=simdi(), duration_seconds=max(0, time.monotonic() - started_clock), **measurement)
                    except Exception:
                        LOGGER.error('Kaynak turunun maliyet ölçümü kaydedilemedi; içerik kaydı korundu.')
                self.stop.wait(0.3)
            text = (f"{successful} kaynak alındı, {failed} kaynak bekliyor; {added} yeni kayıt, {changed} revizyon."
                    if checked else "Yeni tarama yapılmadı; kontrol zamanları ve sorunlar Kaynaklar bölümünde.")
        except Exception:
            text = "Toplama tamamlanamadı; yerel uygulama kaydı incelenmeli."
            error = text
            LOGGER.error("Toplama tamamlanamadı.")
        finally:
            with self.lock:
                self.state.update(running=False, error=error, message=text, last_finished_at=simdi())
                self.tarama_kilidi.birak()

    def kapan(self):
        self.stop.set()
        worker = self.thread
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=40)
        # Süren kaynak okuması varken kilit erken bırakılmaz; _tara sonunda bırakır.

    def zamanli_calistir(self):
        """Uygulama açıkken vadesi gelen kaynakları aralıklı olarak denetler."""
        while not self.stop.is_set():
            try:
                self.baslat()
            except Exception:
                message = "Zamanlı tarama başlatılamadı; sonraki kontrolde yeniden denenecek."
                with self.lock:
                    self.state.update(error=message, message=message)
                LOGGER.error(message)
            self.stop.wait(900)
