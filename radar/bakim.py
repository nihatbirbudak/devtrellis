"""Yerel koleksiyonu tutarlı ve doğrulanmış SQLite yedekleriyle düzenli olarak korur."""

import logging
import os
import shutil
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path


LOGGER = logging.getLogger("radar")
YEDEK_ARALIGI = 6 * 60 * 60


class Bakim:
    """Son yedeği ve haftanın yedi gününe ait dönüşümlü kopyaları yönetir."""

    def __init__(self, depo):
        self.depo = depo
        database_path = Path(depo.path).resolve()
        backup_directory = ("yedekler" if database_path.name.casefold() == "radar.sqlite3"
                            else f"{database_path.stem}-yedekler")
        self.klasor = database_path.parent / backup_directory
        self.son_yedek = self.klasor / "radar-son.sqlite3"
        self.stop = threading.Event()
        self.thread = None
        self._yedek_kilidi = threading.Lock()
        self._durum_kilidi = threading.Lock()
        self._durum = {"running": False, "last_success_at": None, "last_error": None}

    def gorunum(self):
        with self._durum_kilidi:
            result = dict(self._durum)
        try:
            available = self.son_yedek.is_file()
            if available:
                result["last_success_at"] = datetime.fromtimestamp(
                    self.son_yedek.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        except OSError:
            available = False
        return {**result, "available": available, "filename": self.son_yedek.name}

    def dosya(self):
        if not self.son_yedek.is_file():
            raise FileNotFoundError("Henüz indirilebilir yerel yedek yok.")
        return self.son_yedek

    def _gecici_dosya(self):
        with tempfile.NamedTemporaryFile(prefix="ufuk-yedek-", suffix=".tmp",
                                         dir=self.klasor, delete=False) as temporary:
            return Path(temporary.name)

    def _dogrula(self, path):
        with path.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\0":
                raise sqlite3.DatabaseError("Yedek geçerli SQLite dosyası değil.")
        connection = sqlite3.connect(path, timeout=10)
        try:
            # İndirilen tek dosyanın ayrıca günlük veya ortak bellek dosyasına
            # ihtiyaç duymaması için yalnız geçici yedek bağımsız biçime çevrilir.
            connection.execute("PRAGMA journal_mode=DELETE")
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise sqlite3.DatabaseError("Yedek bütünlük denetiminden geçmedi.")
        finally:
            connection.close()

    def yedekle(self):
        """Çakışan isteği bekletmez; başarısız yedek son sağlam dosyanın yerini almaz."""
        if not self._yedek_kilidi.acquire(blocking=False):
            return False
        temporary_paths = []
        with self._durum_kilidi:
            self._durum.update(running=True)
        try:
            self.klasor.mkdir(parents=True, exist_ok=True)
            temporary = self._gecici_dosya()
            temporary_paths.append(temporary)
            self.depo.yedekle(temporary)
            self._dogrula(temporary)

            daily_temporary = self._gecici_dosya()
            temporary_paths.append(daily_temporary)
            shutil.copyfile(temporary, daily_temporary)
            daily = self.klasor / f"ufuk-{datetime.now().weekday()}.sqlite3"
            os.replace(daily_temporary, daily)

            completed_at = datetime.fromtimestamp(
                temporary.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            os.replace(temporary, self.son_yedek)
            with self._durum_kilidi:
                self._durum.update(last_success_at=completed_at, last_error=None)
            return True
        except Exception as error:
            message = ("Yedek doğrulanamadı; önceki sağlam yedek korundu."
                       if isinstance(error, sqlite3.DatabaseError) else
                       "Yedek yazılamadı; disk alanı ve dosya izinlerini denetleyin. Önceki yedek korundu.")
            with self._durum_kilidi:
                self._durum.update(last_error=message)
            raise
        finally:
            for temporary in temporary_paths:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning("Tamamlanamayan geçici UFUK yedeği temizlenemedi.")
            with self._durum_kilidi:
                self._durum.update(running=False)
            self._yedek_kilidi.release()

    def _zamanli_yedekle(self):
        while not self.stop.is_set():
            try:
                self.yedekle()
            except Exception:
                LOGGER.error("Otomatik UFUK yedeği alınamadı; sonraki dönemde yeniden denenecek.")
            self.stop.wait(YEDEK_ARALIGI)

    def baslat(self):
        with self._durum_kilidi:
            if self.stop.is_set() or (self.thread is not None and self.thread.is_alive()):
                return False
            self.thread = threading.Thread(target=self._zamanli_yedekle,
                                           name="ufuk-yedek", daemon=True)
            try:
                self.thread.start()
            except Exception:
                self.thread = None
                self._durum.update(last_error="Otomatik yedekleme başlatılamadı.")
                raise
        return True

    def kapan(self):
        self.stop.set()
        worker = self.thread
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=40)
