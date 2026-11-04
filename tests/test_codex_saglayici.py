"""Codex bağlayıcısının seçim, gizlilik, kota, iptal ve akış sınırlarını gerçek model çağırmadan doğrular."""

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".deps"))

from radar import codex_saglayici, derin_arastirma, zeka
from radar.calisma_masasi import SurumCatismasi
from radar.depo import Depo


def messages(kind="deep", language="tr"):
    return [{"role": "system", "content": "Return exactly one JSON object using the supplied report schema."},
            {"role": "user", "content": json.dumps({"kind": kind, "output_language": language,
                                                       "sources": [{"item_id": "sample", "public_text": "Public source text."}]})}]


def events(value=None):
    return [{"type": "thread.started", "thread_id": "example-thread"}, {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": json.dumps(value or {"ok": True})}},
            {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 10}}]


def emit(arguments, prompt, timeout, on_line, **kwargs):
    for event in events():
        on_line(json.dumps(event).encode())


class CodexBaglayiciTestleri(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        codex_saglayici._kimlik_onbellegi = (None, 0.0, False)

    def parse(self, stream_events):
        stream = codex_saglayici.CodexAkisi()
        for event in stream_events:
            stream.satir(json.dumps(event).encode())
        return json.loads(stream.yanit())

    def test_json_akisi_tamamlanmis_tek_mesaj_gerektirir(self):
        self.assertEqual(self.parse(events()), {"ok": True})
        for stream in (events()[:-1], events()[0:2] + [events()[-1]], events() + [events()[-1]],
                       events({"external_sources": []}), events({"external_sources": [{"url": "https://example.org"}]})):
            with self.subTest(stream=stream), self.assertRaises(codex_saglayici.CodexArastirmaHatasi):
                self.parse(stream)
        for raw in (b'{"type":"turn.started","type":"turn.completed"}', b'{"value":NaN}', b'[]', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(codex_saglayici.CodexArastirmaHatasi):
                codex_saglayici.CodexAkisi().satir(raw)

    def test_araclar_web_ve_uzak_komut_olaylari_reddedilir(self):
        for tool in ("command_execution", "file_change", "mcp_tool_call", "web_search", "computer_use"):
            with self.subTest(tool=tool), self.assertRaises(codex_saglayici.CodexArastirmaHatasi) as caught:
                self.parse([{"type": "item.started", "item": {"id": "tool", "type": tool}}])
            self.assertEqual(caught.exception.code, "tool")

    def test_belgeli_bildirim_ve_plan_olayi_arac_sayilmaz_son_json_yine_zorunlu(self):
        notice = {"type": "item.completed", "item": {"id": "notice", "type": "error", "message": "PRIVATE-DIAGNOSTIC-NOT-FOR-OUTPUT"}}
        plan = {"type": "item.updated", "item": {"id": "plan", "type": "todo_list", "items": [{"text": "Review supplied text", "completed": False}]}}
        stream = codex_saglayici.CodexAkisi()
        for event in [events()[0], notice, plan, *events()[1:]]:
            stream.satir(json.dumps(event).encode())
        self.assertEqual(json.loads(stream.yanit()), {"ok": True})
        self.assertNotIn("PRIVATE-DIAGNOSTIC", str(stream.__dict__))
        self.assertEqual(stream.last_event_type, "turn.completed")
        self.assertEqual(stream.last_item_type, "agent_message")
        with self.assertRaises(codex_saglayici.CodexArastirmaHatasi):
            self.parse([events()[0], notice, events()[-1]])
        with self.assertRaises(codex_saglayici.CodexArastirmaHatasi) as caught:
            self.parse([notice, {"type": "item.started", "item": {"id": "tool", "type": "command_execution"}}])
        self.assertEqual(caught.exception.code, "tool")

    def test_bozuk_bildirim_reddedilir_tani_ham_metni_ve_yolu_saklamaz(self):
        for item in ({"id": "notice", "type": "error"}, {"type": "error", "message": "private"},
                     {"id": "notice", "type": "error", "message": []}):
            with self.subTest(item=item), self.assertRaises(codex_saglayici.CodexArastirmaHatasi):
                self.parse([{"type": "item.completed", "item": item}])
        stream = codex_saglayici.CodexAkisi()
        with self.assertRaises(codex_saglayici.CodexArastirmaHatasi):
            stream.satir(json.dumps({"type": "item.completed", "item": {"id": "x", "type": "C:/private/path", "message": "PRIVATE"}}).encode())
        self.assertEqual(stream.last_item_type, "unknown")
        self.assertNotIn("private", str(stream.__dict__).lower())

    def test_kota_ve_ham_hata_metni_disariya_tasinmaz(self):
        secret = "PRIVATE-ACCOUNT-DETAIL"
        for kind in ("error", "turn.failed"):
            with self.subTest(kind=kind), self.assertRaises(codex_saglayici.CodexArastirmaHatasi) as caught:
                self.parse([{"type": kind, "error": {"code": "usage_limit_reached", "message": secret}}])
            self.assertEqual(caught.exception.code, "quota")
            self.assertNotIn(secret, str(caught.exception))

    def test_model_uydurulmaz_yalniz_izinli_kullanici_secimi_tasinir(self):
        initial = codex_saglayici.codex_komutu("codex")
        self.assertFalse(any(option.startswith("model=") for option in initial))
        (self.home / "config.toml").write_text(
            'model="selected-by-user"\nmodel_reasoning_effort="high"\n'
            'developer_instructions="PRIVATE-CONFIG-INSTRUCTIONS"\n'
            '[mcp_servers.private]\ncommand="PRIVATE-TOOL"\n', encoding="utf-8")
        arguments = codex_saglayici.codex_komutu("codex")
        self.assertIn('model="selected-by-user"', arguments)
        self.assertIn('model_reasoning_effort="high"', arguments)
        self.assertIn("--ignore-user-config", arguments)
        self.assertIn("--ephemeral", arguments)
        self.assertIn("read-only", arguments)
        self.assertIn('web_search="disabled"', arguments)
        self.assertIn("features.shell_tool=false", arguments)
        self.assertIn("features.hooks=false", arguments)
        self.assertIn("features.plugins=false", arguments)
        self.assertNotIn("PRIVATE", str(arguments))
        self.assertIn('model="explicit-user-choice"', codex_saglayici.codex_komutu("codex", "explicit-user-choice"))

    def test_model_profili_ve_uygunsuz_saglayici_sessizce_degistirilmez(self):
        (self.home / "config.toml").write_text('model="base-choice"\nprofile="research"\n', encoding="utf-8")
        (self.home / "research.config.toml").write_text('model="profile-choice"\n', encoding="utf-8")
        self.assertEqual(codex_saglayici.codex_model_ayarlari()["model"], "profile-choice")
        (self.home / "config.toml").write_text('model_provider="other-provider"\n', encoding="utf-8")
        with self.assertRaises(codex_saglayici.CodexArastirmaHatasi) as caught:
            codex_saglayici.codex_model_ayarlari()
        self.assertEqual(caught.exception.code, "configuration")

    def test_cli_ortami_uygulamanin_api_anahtarlarini_devralmaz(self):
        values = {name: "PRIVATE-API-CREDENTIAL" for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "NVIDIA_API_KEY", "UFUK_NVIDIA_API_KEY", "OPENAI_BASE_URL")}
        with patch.dict(os.environ, values):
            environment = codex_saglayici.codex_ortami()
        for name in values:
            self.assertNotIn(name, environment)
        self.assertEqual(environment["CODEX_HOME"], str(self.home))

    def test_yalniz_chatgpt_oturumunu_kabul_eder_ve_onbellekler(self):
        def status(arguments, prompt, timeout, on_line, **kwargs):
            self.assertEqual(arguments[1:], ["login", "status"])
            self.assertTrue(kwargs["capture_stderr"])
            on_line(b"Logged in using ChatGPT\n")
        with patch.object(codex_saglayici, "codex_yolu", return_value="codex"), patch.object(codex_saglayici, "cli_calistir", side_effect=status) as runner:
            self.assertTrue(codex_saglayici.codex_hazir())
            self.assertTrue(codex_saglayici.codex_hazir())
            self.assertEqual(runner.call_count, 1)
        codex_saglayici._kimlik_onbellegi = (None, 0.0, False)
        with patch.object(codex_saglayici, "codex_yolu", return_value="codex"), patch.object(codex_saglayici, "cli_calistir", side_effect=lambda a, p, t, cb, **k: cb(b"Logged in using an API key: PRIVATE\n")):
            self.assertFalse(codex_saglayici.codex_hazir())

    def test_baglayici_sadece_stdin_verisini_gonderir_ve_json_dondurur(self):
        with patch.object(codex_saglayici, "codex_yolu", return_value="codex"), patch.object(codex_saglayici, "codex_hazir", return_value=True), patch.object(codex_saglayici, "cli_calistir", side_effect=emit) as runner:
            answer = codex_saglayici.codex_yaniti(messages(), api_key="PRIVATE-KEY-IGNORED")
        self.assertEqual(json.loads(answer), {"ok": True})
        arguments, prompt = runner.call_args.args[:2]
        self.assertEqual(arguments[-1], "-")
        self.assertNotIn("PRIVATE-KEY-IGNORED", str(arguments) + prompt.decode())
        self.assertIn("No tools, shell, files, web", prompt.decode())
        self.assertIn("Do not claim to search or visit sources", prompt.decode())

    def test_iptal_zamanasimi_ve_bozuk_girdi_sabit_hatalardir(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(codex_saglayici, "cli_calistir") as runner:
            with self.assertRaises(codex_saglayici.CodexArastirmaHatasi) as caught:
                codex_saglayici.codex_yaniti(messages(), cancel_event=cancelled)
            self.assertEqual(caught.exception.code, "cancelled")
            runner.assert_not_called()
        with patch.object(codex_saglayici, "codex_yolu", return_value="codex"), patch.object(codex_saglayici, "codex_hazir", return_value=True), patch.object(codex_saglayici, "cli_calistir", side_effect=derin_arastirma.DerinArastirmaZamanAsimi):
            with self.assertRaises(codex_saglayici.CodexArastirmaHatasi) as caught:
                codex_saglayici.codex_yaniti(messages())
            self.assertEqual(caught.exception.code, "timeout")
        for value in ([], messages("unknown"), [{"role": "system", "content": "x" * 130000}, messages()[1]]):
            with self.subTest(value=str(value)[:60]), self.assertRaises(codex_saglayici.CodexArastirmaHatasi) as caught:
                codex_saglayici.codex_yaniti(value)
            self.assertEqual(caught.exception.code, "input")


class CodexIsAkisiTestleri(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.depo = Depo(Path(self.temporary.name) / "test.sqlite3", [{"id": "source", "name": "Public source", "topic": "ai", "publisher": "Example", "benefit": "Public source review", "interval_hours": 24}])
        self.depo.kaydet("source", [{"external_id": "sample", "url": "https://example.org/source", "title": "Public source",
                                     "excerpt": "A public source describes sensor research and explicitly states its limitations. " * 3}])
        self.item = self.depo.gorunum()["items"][0]["id"]
        self.provider = Mock(side_effect=self.answer)
        self.manager = zeka.ZekaIsleri(self.depo, provider=self.provider, deep_provider=self.provider, deep_available=lambda: True, credential_provider=lambda: "TEST-ONLY")
        self.addCleanup(self.manager.kapan)
        self.claude = patch("radar.derin_arastirma.claude_hazir", return_value=True)
        self.codex = patch("radar.zeka.codex_hazir", return_value=True)
        self.claude.start()
        self.codex.start()
        self.addCleanup(self.claude.stop)
        self.addCleanup(self.codex.stop)

    def choose(self, **changes):
        saved = self.manager.saglayici_ayarlari()
        return self.manager.saglayici_ayarlarini_yaz({key: saved[key] for key in ("deep_provider", "codex_model", "output_language")} | {"expected_version": saved["version"], **changes})

    def answer(self, messages, **options):
        payload = json.loads(messages[1]["content"])
        if payload["kind"] == "verify":
            return json.dumps({"coverage_complete": True, "claims": [{"id": row["id"], "status": "uncertain", "reason_tr": "The supplied source does not establish this claim.", "evidence": []} for row in payload["claims"]]})
        return json.dumps({"title_tr": "A review of public sensor research", "summary_tr": "The public source describes sensor research and states its limitations. Independent tests were not performed.",
                           "benefit_tr": "This research may help compare available sensor approaches.", "limitations": ["Hardware compatibility has not been independently tested."],
                           "next_steps": ["Review the original publication before planning a test."],
                           "evidence": [{"item_id": self.item, "quote_id": 1}], "recommendation": "izle",
                           "compatibility_tr": "Compatibility with the intended local system remains untested.",
                           "requirements": ["Check the published hardware requirements."], "integration_steps": ["Prepare an isolated experiment after human approval."],
                           "verification_plan": ["Compare measured results with the original publication."], "open_questions": ["Which hardware configurations have been tested?"]})

    def finish(self):
        self.manager.worker.join(timeout=5)
        self.assertFalse(self.manager.worker.is_alive())

    def test_varsayilan_korunur_secim_surumlu_ve_kalicidir(self):
        initial = self.manager.saglayici_ayarlari()
        self.assertEqual(initial["deep_provider"], "claude")
        selected = self.choose(deep_provider="codex", output_language="en")
        self.assertEqual(selected["deep_provider"], "codex")
        self.assertIsNone(selected["codex_model"])
        with self.assertRaises(SurumCatismasi):
            self.manager.saglayici_ayarlarini_yaz({"deep_provider": "claude", "codex_model": None, "output_language": "tr", "expected_version": initial["version"]})
        self.manager.kapan()
        reopened = zeka.ZekaIsleri(self.depo, provider=self.provider, deep_provider=self.provider, deep_available=lambda: True)
        self.addCleanup(reopened.kapan)
        self.assertEqual(reopened.saglayici_ayarlari()["deep_provider"], "codex")
        self.assertEqual(reopened.output_language, "en")

    def test_codex_ayri_butce_ingilizce_rapor_ve_saglayici_bilgisi(self):
        self.choose(deep_provider="codex", output_language="en")
        self.manager.baslat(self.item, "deep")
        self.finish()
        saved = self.manager.oku(self.item, "deep")
        self.assertEqual(saved["status"], "hazir", saved["last_error"])
        self.assertEqual(saved["language"], "en")
        self.assertEqual(saved["model"], "codex/configured-default")
        self.assertIn("no new web", saved["result"]["research_plan"]["stop_condition"])
        self.assertIsNone(self.provider.call_args_list[0].kwargs["model"])
        self.assertEqual(self.manager.durum("deep")["daily_used"], 2)
        self.assertEqual(self.manager.durum("summary")["daily_used"], 0)
        self.choose(deep_provider="claude", output_language="tr")
        self.assertEqual(self.manager.durum("deep")["daily_used"], 0)
        self.assertTrue(self.manager.oku(self.item, "deep")["stale"])

    def test_codex_kotasi_model_secimiyle_sifirlanmaz_ve_calisirken_ayar_degismez(self):
        self.choose(deep_provider="codex", output_language="en", codex_model="user-selected-model")
        self.manager.baslat(self.item, "deep")
        self.finish()
        used = self.manager.durum("deep")["daily_used"]
        self.choose(codex_model="another-user-model")
        self.assertEqual(self.manager.durum("deep")["daily_used"], used)
        self.manager.deep_daily_limit = used
        with self.assertRaises(zeka.ZekaSiniri):
            self.manager.baslat(self.item, "deep")
        with patch.object(self.manager, "worker", Mock(is_alive=lambda: True)):
            with self.assertRaises(zeka.ZekaMesgul):
                self.choose(deep_provider="claude")


if __name__ == "__main__":
    unittest.main()
