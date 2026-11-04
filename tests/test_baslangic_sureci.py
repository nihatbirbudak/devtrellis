"""Windows başlatıcılarını ayrı klasör ve portlarda gerçek uygulamayla doğrular."""

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import sysconfig
import tempfile
import unittest
import urllib.error
import urllib.request
import venv
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path


KOK = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which(os.environ.get("UFUK_TEST_POWERSHELL", "powershell.exe"))
ETKIN = os.name == "nt" and os.environ.get("UFUK_WINDOWS_TESTLERI") == "1"


@unittest.skipUnless(ETKIN and POWERSHELL, "Gerçek Windows süreç testi için UFUK_WINDOWS_TESTLERI=1 gerekir.")
class BaslangicSureciTestleri(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Temiz kurulum bağımlılıkları .venv ortamına kurar. Testler bunları
        # kendi klasörüne kopyalar; paket kurulumu veya indirme başlatmaz.
        bagimliliklar = KOK / ".deps"
        if not bagimliliklar.is_dir():
            bagimliliklar = Path(sysconfig.get_path("purelib"))
        if not (bagimliliklar / "fastapi" / "__init__.py").is_file():
            raise RuntimeError("Windows süreç testlerini requirements.txt kurulmuş sanal ortamın Python'u ile çalıştırın.")
        cls.temel_python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        if not cls.temel_python.is_file():
            raise RuntimeError("Testin temel Python çalıştırıcısı bulunamadı.")
        cls.ortam = os.environ.copy()
        cls.ortam.pop("PYTHONPATH", None)
        cls.ortam.pop("PYTHONHOME", None)
        cls.ortam["PATH"] = str(cls.temel_python.parent) + os.pathsep + cls.ortam.get("PATH", "")
        cls.gecici = tempfile.TemporaryDirectory(prefix="ufuk-baslangic-")
        cls.klasor = Path(cls.gecici.name) / "Türkçe boşluklu UFUK"
        cls.klasor.mkdir()
        for ad in ("baslat.ps1", "durdur.ps1", "calistir.py"):
            shutil.copy2(KOK / ad, cls.klasor / ad)
        shutil.copytree(KOK / "radar", cls.klasor / "radar", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(bagimliliklar, cls.klasor / ".deps", ignore=shutil.ignore_patterns("__pycache__"))

    @classmethod
    def tearDownClass(cls):
        assert Path(cls.gecici.name).resolve().parent == Path(tempfile.gettempdir()).resolve()
        cls.gecici.cleanup()

    def setUp(self):
        with socket.socket() as soket:
            soket.bind(("127.0.0.1", 0))
            self.port = soket.getsockname()[1]
        self.adres = f"http://127.0.0.1:{self.port}"
        self.kimlik = self.klasor / "data" / f"surec-{self.port}.json"
        self.addCleanup(self.kapat)

    def komut(self, betik, *argumanlar):
        # Gizli sunucu, Windows'ta kalıtılan çıktı borusunu açık tutabilir.
        # Dosyaya kayıt, testin arka plandaki sunucunun kapanışını beklemesini önler.
        tanimlayici, yol = tempfile.mkstemp(prefix="komut-", suffix=".log", dir=self.klasor)
        with os.fdopen(tanimlayici, "wb") as kayit:
            sonuc = subprocess.run(
                [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.klasor / betik),
                 "-Port", str(self.port), *argumanlar],
                cwd=self.klasor, env=self.ortam, stdout=kayit, stderr=subprocess.STDOUT, timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        return subprocess.CompletedProcess(sonuc.args, sonuc.returncode, Path(yol).read_bytes(), b"")

    def klasoru_sil(self, klasor):
        self.assertIn(self.klasor.resolve(), klasor.resolve().parents)
        shutil.rmtree(klasor)

    def baslat(self):
        sonuc = self.komut("baslat.ps1", "-TarayiciAcma", "-ElleToplama")
        self.assertEqual(sonuc.returncode, 0, sonuc.stdout + sonuc.stderr)
        return self.kayit()

    def kayit(self):
        return json.loads(self.kimlik.read_text(encoding="utf-8-sig"))

    def kapat(self):
        sonuc = self.komut("durdur.ps1")
        self.assertEqual(sonuc.returncode, 0, sonuc.stdout + sonuc.stderr)

    def oku(self, yol="/api/state"):
        with urllib.request.urlopen(self.adres + yol, timeout=3) as yanit:
            return json.load(yanit)

    def yaz(self, yol, veri, token):
        istek = urllib.request.Request(
            self.adres + yol, json.dumps(veri).encode(),
            {"Content-Type": "application/json", "Origin": self.adres, "X-Radar-CSRF": token}, method="POST",
        )
        with urllib.request.urlopen(istek, timeout=3) as yanit:
            return json.load(yanit)

    def test_yeniden_acma_ayni_sureci_kullanir_ve_kapanis_portu_birakir(self):
        ilk = self.baslat()
        self.assertEqual(self.baslat()["pid"], ilk["pid"])
        self.assertFalse(self.oku()["settings"]["automatic_collection"])
        self.kapat()
        self.assertFalse(self.kimlik.exists())
        with self.assertRaises((urllib.error.URLError, TimeoutError)):
            self.oku("/api/health")

    def test_eszamanli_iki_acilis_tek_sureci_kullanir(self):
        with ThreadPoolExecutor(max_workers=2) as havuz:
            kayitlar = list(havuz.map(lambda _: self.baslat(), range(2)))
        self.assertEqual(kayitlar[0]["pid"], kayitlar[1]["pid"])
        self.assertEqual(self.oku("/api/health")["status"], "ok")

    def test_bozuk_ilk_python_adayindan_saglam_adaya_gecer(self):
        calistirici = self.klasor / ".venv" / "Scripts" / "python.exe"
        calistirici.parent.mkdir(parents=True)
        # Güvenilir Windows aracı Python seçeneklerini reddeder; ilk aday geçersizdir.
        shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "where.exe", calistirici)
        try:
            self.baslat()
            self.assertEqual(self.oku("/api/health")["status"], "ok")
        finally:
            self.kapat()
            self.klasoru_sil(self.klasor / ".venv")

    def test_eksik_bagimlilikta_kimlik_ve_surec_birakmaz(self):
        bagimliliklar = self.klasor / ".deps"
        saklanan = self.klasor / ".deps-saklanan"
        bagimliliklar.rename(saklanan)
        bagimliliklar.mkdir()
        (bagimliliklar / "fastapi.py").write_text("raise ImportError('Eksik test bagimliligi')\n", encoding="utf-8")
        try:
            sonuc = self.komut("baslat.ps1", "-TarayiciAcma", "-ElleToplama")
            self.assertNotEqual(sonuc.returncode, 0)
            self.assertIn(b"README.md", sonuc.stdout + sonuc.stderr)
            self.assertFalse(self.kimlik.exists())
            with self.assertRaises((urllib.error.URLError, TimeoutError)):
                self.oku("/api/health")
        finally:
            self.klasoru_sil(bagimliliklar)
            saklanan.rename(bagimliliklar)

    def test_sanal_ortam_python_alt_sureci_de_kapanir(self):
        sanal_ortam = self.klasor / ".venv"
        venv.EnvBuilder(with_pip=False).create(sanal_ortam)
        ortam_kaniti = self.klasor / "sanal-ortam-sunucusu.txt"
        (sanal_ortam / "Lib" / "site-packages" / "sitecustomize.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "if any(str(arg).endswith('calistir.py') for arg in sys.argv):\n"
            f"    Path({str(ortam_kaniti)!r}).write_text(sys.prefix, encoding='utf-8')\n",
            encoding="utf-8",
        )
        try:
            self.baslat()
            self.assertEqual(Path(ortam_kaniti.read_text(encoding="utf-8")).resolve(), sanal_ortam.resolve())
            self.assertEqual(self.oku("/api/health")["status"], "ok")
            self.kapat()
            with self.assertRaises((urllib.error.URLError, TimeoutError)):
                self.oku("/api/health")
        finally:
            self.kapat()
            self.klasoru_sil(sanal_ortam)

    def test_yanlis_surec_kimliginde_calisan_uygulamaya_dokunmaz(self):
        ilk = self.baslat()
        try:
            yanlis = {**ilk, "started_at": "2000-01-01T00:00:00Z"}
            self.kimlik.write_text(json.dumps(yanlis), encoding="utf-8")
            self.assertNotEqual(self.komut("durdur.ps1").returncode, 0)
            self.assertEqual(self.oku("/api/health")["status"], "ok")
            self.kimlik.write_text("{bozuk-json", encoding="utf-8")
            self.assertNotEqual(self.komut("durdur.ps1").returncode, 0)
            self.assertEqual(self.oku("/api/health")["status"], "ok")
            yanlis = {**ilk, "started_at": "gecersiz-tarih"}
            self.kimlik.write_text(json.dumps(yanlis), encoding="utf-8")
            self.assertNotEqual(self.komut("durdur.ps1").returncode, 0)
            self.assertEqual(self.oku("/api/health")["status"], "ok")
            yanlis = {**ilk, "script": str(self.klasor / "baska.py")}
            self.kimlik.write_text(json.dumps(yanlis), encoding="utf-8")
            self.assertNotEqual(self.komut("durdur.ps1").returncode, 0)
            self.assertEqual(self.oku("/api/health")["status"], "ok")
        finally:
            self.kimlik.write_text(json.dumps(ilk), encoding="utf-8")

    def test_dolu_portta_baska_dinleyiciye_dokunmaz(self):
        with socket.socket() as soket:
            soket.bind(("127.0.0.1", self.port))
            soket.listen()
            sonuc = self.komut("baslat.ps1", "-TarayiciAcma", "-ElleToplama")
            self.assertNotEqual(sonuc.returncode, 0)
            self.assertFalse(self.kimlik.exists())
            self.assertEqual(soket.getsockname()[1], self.port)

    def test_uygulama_acilamazsa_kimlik_ve_dinleyici_birakmaz(self):
        uygulama = self.klasor / "calistir.py"
        onceki = uygulama.read_bytes()
        uygulama.write_text("raise SystemExit('Beklenen acilis denemesi')\n", encoding="utf-8")
        try:
            sonuc = self.komut("baslat.ps1", "-TarayiciAcma", "-ElleToplama")
            self.assertNotEqual(sonuc.returncode, 0)
            self.assertFalse(self.kimlik.exists())
            with self.assertRaises((urllib.error.URLError, TimeoutError)):
                self.oku("/api/health")
        finally:
            uygulama.write_bytes(onceki)

    def test_acik_veritabaninin_wal_yedegi_ve_yeniden_acilis_tercihi_korur(self):
        self.baslat()
        veri = self.oku()
        kart = veri["items"][0]["id"]
        veritabani = self.klasor / "data" / "radar.sqlite3"
        with closing(sqlite3.connect(veritabani)) as tutulan:
            tutulan.execute("SELECT COUNT(*) FROM items").fetchone()
            self.yaz(f"/api/items/{kart}/feedback", {"value": "yararli"}, veri["csrf_token"])
            self.assertGreater(Path(str(veritabani) + "-wal").stat().st_size, 0)
            sonuc = subprocess.run(
                [str(self.temel_python), "-X", "utf8", str(self.klasor / "calistir.py"), "yedekle"],
                cwd=self.klasor, env=self.ortam, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.assertEqual(sonuc.returncode, 0, sonuc.stdout + sonuc.stderr)
            with closing(sqlite3.connect(self.klasor / "data" / "yedekler" / "radar-son.sqlite3")) as yedek:
                self.assertEqual(yedek.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(yedek.execute("SELECT feedback FROM items WHERE id=?", (kart,)).fetchone()[0], "yararli")
        self.kapat()
        self.baslat()
        kayit = next(kayit for kayit in self.oku()["items"] if kayit["id"] == kart)
        self.assertEqual(kayit["feedback"], "yararli")


if __name__ == "__main__":
    unittest.main()
