"""Claude bağlayıcısının araç, kaynak, oturum, bellek ve süreç sınırlarını gerçek modele bağlanmadan doğrular."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from radar import derin_arastirma


def messages(kind="deep"):
    return [{"role": "system", "content": "Return a Turkish report in strict JSON; keep source evidence."},
            {"role": "user", "content": json.dumps({"kind": kind, "sources": [{"item_id": "item-1",
                "public_text": "Kamusal deneme metnidir.", "quote_options": [{"id": 1, "quote": "Kamusal deneme metnidir."}]}]})}]


def report(**changes):
    return {"title_tr": "Deneme araştırması", "summary_tr": "Bu yalnız bağlantı denemesi için kullanılan Türkçe araştırma metnidir.",
            "benefit_tr": "Yerel deneme için kaynakları karşılaştırmaya yardımcı olabilir.",
            "limitations": ["Gerçek araştırma yapılmadı."], "next_steps": ["Kaynak koşullarını inceleyin."],
            "evidence": [{"item_id": "item-1", "quote_id": 1}], **changes}


def result(value=None, **changes):
    return {"type": "result", "subtype": "success", "is_error": False,
            "result": json.dumps(report() if value is None else value, ensure_ascii=False), **changes}


def use(name="WebFetch", identity="tool-1", **inputs):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": identity,
        "name": name, "input": inputs or {"url": "https://docs.python.org/3/"}}]}}


def tool_result(content="Kamusal kaynak metni", identity="tool-1", **changes):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": identity,
        "content": content, **changes}]}}


def external(url="https://docs.python.org/3/", **changes):
    return {"url": url, "title": "Python Documentation", "summary_tr": "Bu kaynak Python kullanım koşulları ve teknik açıklamaları içeriyor.", **changes}


class ClaudeAkisTestleri(unittest.TestCase):
    def parse(self, events, kind="deep"):
        stream = derin_arastirma._Akis(kind)
        for event in events:
            stream.satir(json.dumps(event, ensure_ascii=False).encode())
        return json.loads(stream.yanit())

    def test_six_fields_and_quote_ids_are_preserved(self):
        self.assertEqual(self.parse([result()]), report())

    def test_legacy_quote_is_not_rewritten(self):
        value = report(evidence=[{"item_id": "item-1", "quote": "Exact public quotation remains intact."}])
        self.assertEqual(self.parse([result(value)]), value)

    def test_successful_fetch_evidence_is_required(self):
        value = report(external_sources=[external()])
        self.assertEqual(self.parse([use(), tool_result(), result(value)]), value)
        for prior in ([], [use()], [use(), tool_result(is_error=True)]):
            with self.subTest(prior=prior), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                self.parse(prior + [result(value)])

    def test_search_result_links_json_and_markdown_and_structured_blocks(self):
        contents = [
            'Web search results for query "Python"\n\nLinks: [{"title":"Python","url":"https://docs.python.org/3/"}]\nDescription.',
            [{"type": "text", "text": "[Python](https://docs.python.org/3/)"}],
            [{"type": "search_result", "source": "https://docs.python.org/3/", "title": "Python"}],
        ]
        for content in contents:
            with self.subTest(content=content):
                parsed = self.parse([use("WebSearch", query="Python documentation"), tool_result(content),
                                     result(report(external_sources=[external()]))])
                self.assertEqual(parsed["external_sources"], [external()])

    def test_assistant_text_or_plain_url_is_not_source_evidence(self):
        for events in ([{"type": "assistant", "message": {"content": [{"type": "text", "text": "https://docs.python.org/3/"}]}}],
                       [use("WebSearch", query="Python"), tool_result("The snippet mentions https://docs.python.org/3/")]):
            with self.subTest(events=events), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                self.parse(events + [result(report(external_sources=[external()]))])

    def test_unrelated_or_failed_search_cannot_authorize_links(self):
        for events in ([tool_result('[Python](https://docs.python.org/3/)')],
                       [use("WebSearch", query="Python"), tool_result('[Python](https://docs.python.org/3/)', is_error=True)]):
            with self.subTest(events=events), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                self.parse(events + [result(report(external_sources=[external()]))])

    def test_unapproved_tool_and_review_tools_are_blocked(self):
        for name in ("Bash", "Read", "Edit", "mcp__mail__send", "Agent", "PowerShell"):
            with self.subTest(name=name), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                self.parse([use(name), result()])
        for kind in ("review", "verify", "revise"):
            with self.subTest(kind=kind), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                self.parse([use(), result()], kind=kind)

    def test_read_passages_come_only_from_successful_fetch_receipts(self):
        stream = derin_arastirma._Akis("deep")
        events = [use("WebSearch", "search", query="Python"),
                  tool_result('[Python](https://docs.python.org/3/)', identity="search"),
                  use(identity="failed", url="https://docs.python.org/failed"),
                  tool_result("Untrusted failure text", identity="failed", is_error=True),
                  use(identity="read", url="https://docs.python.org/3/"),
                  tool_result([{"type": "text", "text": "Actual tool output. " * 400},
                               {"type": "image", "text": "Not a text passage"}], identity="read"),
                  result(report(read_sources=[{"public_text": "Forged model receipt"}]))]
        for event in events:
            stream.satir(json.dumps(event).encode())
        reply = stream.yanit()
        self.assertIsInstance(reply, derin_arastirma.ClaudeYaniti)
        self.assertEqual(len(reply.read_sources), 1)
        receipt = reply.read_sources[0]
        self.assertEqual(receipt["url"], "https://docs.python.org/3/")
        self.assertEqual(receipt["content_kind"], "web_fetch_output")
        self.assertEqual(len(receipt["public_text"]), 6000)
        self.assertTrue(receipt["public_text"].startswith("Actual tool output"))
        self.assertNotIn("Forged", str(reply.read_sources))
        self.assertNotIn("Not a text", str(reply.read_sources))

    def test_search_links_and_model_text_do_not_become_read_passages(self):
        stream = derin_arastirma._Akis("deep")
        for event in [use("WebSearch", query="Python"), tool_result('[Python](https://docs.python.org/3/)'),
                      result(report(external_sources=[external()]))]:
            stream.satir(json.dumps(event).encode())
        reply = stream.yanit()
        self.assertEqual(reply.read_sources, ())
        self.assertEqual(json.loads(reply)["external_sources"], [external()])

    def test_unique_tool_budget_and_duplicate_stream_events(self):
        for name, limit in (("WebSearch", 3), ("WebFetch", 5)):
            events = [use(name, f"id-{number}", **({"query": "Python"} if name == "WebSearch" else {})) for number in range(limit)]
            with self.subTest(name=name):
                self.assertEqual(self.parse(events + [events[0], result()]), report())
                with self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                    self.parse(events + [use(name, "too-many"), result()])
        with self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
            self.parse([use(), use(url="https://www.python.org/"), result()])

    def test_private_credentialed_non_https_and_ip_urls_are_rejected(self):
        urls = ["http://docs.python.org/", "https://user:secret@docs.python.org/", "https://127.0.0.1/",
                "https://[::1]/", "https://169.254.169.254/", "https://localhost/", "https://printer.local/",
                "https://machine.internal/", "https://server/", "https://2130706433/", "https://127.1/",
                "https://127.0.0.1.nip.io/", "https://docs.python.org:8000/", "https://docs.python.org\\@localhost/",
                "https://docs.python.org/\n", "https://docs.python.org./", "https://file.invalid/", "file:///tmp/key"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                self.parse([use(url=url), tool_result(), result(report(external_sources=[external(url)]))])

    def test_https_address_normalization_only_changes_host_port_and_fragment(self):
        url = "https://DOCS.PYTHON.ORG:443/3/?a=1#part"
        parsed = self.parse([use(url=url), tool_result(), result(report(external_sources=[external(url)]))])
        self.assertEqual(parsed["external_sources"][0]["url"], "https://docs.python.org/3/?a=1")
        with self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
            self.parse([use(), tool_result(), result(report(external_sources=[external("https://docs.python.org/3/other")]))])

    def test_external_field_limits_and_extra_fields(self):
        invalid = [[external()] * 6, [external(), external()], [external(token="secret")],
                   [external(title="")], [external(summary_tr="This software provides better performance.")],
                   [external(summary_tr="Bu kaynak için 中文 başka açıklama var.")], "not-a-list"]
        for entries in invalid:
            with self.subTest(entries=entries), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                self.parse([use(), tool_result(), result(report(external_sources=entries))])
        self.assertEqual(self.parse([result(report(external_sources=[]))])["external_sources"], [])

    def test_review_and_legacy_research_do_not_accept_external_sources(self):
        for kind in ("review", "research"):
            with self.subTest(kind=kind), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                self.parse([result(report(external_sources=[]))], kind=kind)

    def test_error_or_missing_or_multiple_final_result_is_rejected(self):
        for events in ([], [result(subtype="error_max_turns")], [result(is_error=True)], [result(), result()],
                       [result([])], [result(result="```json\n{}\n```")], [result(result='{"a":1,"a":2}')],
                       [result(result='{"number":NaN}')]):
            with self.subTest(events=events), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                self.parse(events)

    def test_invalid_utf8_and_invalid_event_are_fixed_errors(self):
        for value in (b"\xff", b"{}suffix", b"[]", b'{"a":1,"a":2}', b'{"type":"assistant","message":null}'):
            with self.subTest(value=value), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                derin_arastirma._Akis("deep").satir(value)

    def test_tool_result_in_assistant_message_is_not_evidence(self):
        wrong_role = tool_result()
        wrong_role["type"] = "assistant"
        with self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
            self.parse([use(), wrong_role, result(report(external_sources=[external()]))])

    def test_external_text_bounds_match_report_validator(self):
        entry = external(title="A", summary_tr="Bu kaynak için uygun koşullar incelenmeli. " * 35)
        parsed = self.parse([use(), tool_result(), result(report(external_sources=[entry]))])
        self.assertEqual(parsed["external_sources"][0]["summary_tr"], entry["summary_tr"].strip())
        for entry in (external(title="A" * 241), external(summary_tr="Bu kaynak için kısa bilgi var.")):
            with self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                self.parse([use(), tool_result(), result(report(external_sources=[entry]))])


class ClaudeKomutTestleri(unittest.TestCase):
    def call(self, kind="deep", **kwargs):
        captured = {}

        def run(arguments, prompt, timeout, on_line, **options):
            captured.update(arguments=arguments, prompt=prompt, timeout=timeout, options=options)
            on_line(json.dumps(result()).encode())

        with patch.object(derin_arastirma, "claude_hazir", return_value=True), patch.object(derin_arastirma, "_calistir", side_effect=run):
            value = derin_arastirma.claude_yaniti(messages(kind), **kwargs)
        self.assertEqual(json.loads(value), report())
        return captured

    def test_web_command_is_strict_and_prompt_is_only_stdin(self):
        captured = self.call(api_key="DO-NOT-SEND-THIS-KEY", model="claude-opus")
        args = captured["arguments"]
        for flag in ("--safe-mode", "--restricted", "--strict-mcp-config", "--no-session-persistence", "--no-chrome", "--disable-slash-commands", "-p", "--verbose"):
            self.assertIn(flag, args)
        for flag, value in (("--tools", "WebSearch,WebFetch"), ("--allowedTools", "WebSearch,WebFetch"),
                            ("--permission-mode", "dontAsk"), ("--permission-prompts", "none"),
                            ("--mcp-config", '{"mcpServers":{}}'), ("--model", "opus"),
                            ("--effort", "high"), ("--output-format", "stream-json")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertNotIn("DO-NOT-SEND-THIS-KEY", repr(captured))
        self.assertNotIn("Kamusal", repr(args))
        self.assertIn(b"INPUT DATA", captured["prompt"])
        self.assertEqual(captured["timeout"], 240)

    def test_review_has_no_tools_and_shorter_deadline(self):
        captured = self.call("review")
        for flag in ("--tools", "--allowedTools"):
            self.assertEqual(captured["arguments"][captured["arguments"].index(flag) + 1], "")
        self.assertEqual(captured["timeout"], 90)
        self.assertIn(b"NO TOOLS OR WEB ACCESS", captured["prompt"])
        self.assertIn(b"relevance_reason_tr", captured["prompt"])

    def test_audit_and_correction_disable_tools_and_keep_existing_deadline(self):
        for kind in ("verify", "revise"):
            with self.subTest(kind=kind):
                captured = self.call(kind)
                for flag in ("--tools", "--allowedTools"):
                    self.assertEqual(captured["arguments"][captured["arguments"].index(flag) + 1], "")
                self.assertEqual(captured["timeout"], 90)
                self.assertIn(b"NO TOOLS OR WEB ACCESS", captured["prompt"])
                self.assertIn(b"separate evidence audit", captured["prompt"])

    def test_legacy_research_keeps_web_tools_but_requests_no_external_field(self):
        captured = self.call("research")
        self.assertIn("WebSearch,WebFetch", captured["arguments"])
        self.assertIn(b"WITHOUT external_sources", captured["prompt"])

    def test_cancellation_is_passed_and_pre_cancel_does_not_check_auth(self):
        event = threading.Event()
        self.assertIs(self.call(cancel_event=event)["options"]["cancel_event"], event)
        event.set()
        with patch.object(derin_arastirma, "claude_hazir") as ready:
            with self.assertRaises(derin_arastirma.DerinArastirmaIptal):
                derin_arastirma.claude_yaniti(messages(), cancel_event=event)
            ready.assert_not_called()

    def test_bad_kind_model_messages_and_bounds_do_not_launch(self):
        invalid = [([], {}), (messages("summary"), {}), (messages(), {"max_tokens": True}),
                   (messages(), {"max_tokens": 4801}), (messages(), {"model": "unapproved"}),
                   ([{"role": "user", "content": "{}"}] * 2, {}),
                   ([messages()[0], {"role": "user", "content": "x" * 150_000}], {})]
        with patch.object(derin_arastirma, "_calistir") as run:
            for value, options in invalid:
                with self.subTest(options=options), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                    derin_arastirma.claude_yaniti(value, **options)
            run.assert_not_called()

    def test_api_auth_environment_is_not_inherited(self):
        environment = {name: "DO-NOT-INHERIT" for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "UFUK_NVIDIA_API_KEY", "NVIDIA_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")}
        with patch.dict(os.environ, environment):
            filtered = derin_arastirma._ortam()
        self.assertTrue(all(name not in filtered for name in environment))

    def test_unavailable_auth_never_launches_model(self):
        with patch.object(derin_arastirma, "claude_hazir", return_value=False), patch.object(derin_arastirma, "_calistir") as run:
            with self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                derin_arastirma.claude_yaniti(messages())
            run.assert_not_called()

    def test_auth_boolean_is_cached_without_account_details(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "claude.exe"
            cli.write_bytes(b"fake")

            def auth(arguments, prompt, timeout, on_line, **options):
                self.assertEqual(arguments[-3:], ["auth", "status", "--json"])
                self.assertEqual(options["byte_limit"], 8192)
                on_line(b'{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty","email":"fake@example.org"}')

            with patch.object(derin_arastirma, "CLAUDE_YOLU", cli), patch.object(derin_arastirma, "_kimlik_onbellegi", (0.0, False)), patch.object(derin_arastirma, "_calistir", side_effect=auth) as run:
                self.assertTrue(derin_arastirma.claude_hazir())
                self.assertTrue(derin_arastirma.claude_hazir())
                self.assertEqual(run.call_count, 1)
                self.assertEqual(len(derin_arastirma._kimlik_onbellegi), 2)
                self.assertIs(derin_arastirma._kimlik_onbellegi[1], True)
                self.assertNotIn("fake@example.org", repr(derin_arastirma._kimlik_onbellegi))

    def test_auth_rejects_api_key_session_and_malformed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "claude.exe"
            cli.write_bytes(b"fake")
            for raw in (b'{"loggedIn":true,"authMethod":"api_key","apiProvider":"firstParty"}',
                        b'{"loggedIn":false}', b'{"loggedIn":"true"}', b'not-json'):
                with self.subTest(raw=raw), patch.object(derin_arastirma, "CLAUDE_YOLU", cli), patch.object(derin_arastirma, "_kimlik_onbellegi", (0.0, False)), patch.object(derin_arastirma, "_calistir", side_effect=lambda a, p, t, callback, **kw: callback(raw)):
                    self.assertFalse(derin_arastirma.claude_hazir())


class ClaudeSahteSurecTestleri(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.script = self.folder / "sahte_cli.py"
        self.script.write_text('''import json, os, sys, time
from pathlib import Path
spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not spec.get("ignore_stdin"):
    sys.stdin.buffer.read()
if spec.get("stderr"):
    sys.stderr.write(spec["stderr"])
for event in spec.get("events", []):
    print(json.dumps(event, ensure_ascii=False), flush=True)
if spec.get("raw"):
    sys.stdout.write(spec["raw"])
    sys.stdout.flush()
time.sleep(spec.get("sleep", 0))
sys.exit(spec.get("exit", 0))
''', encoding="utf-8")
        self.real_popen = subprocess.Popen
        self.processes = []
        self.options = []

    def run_spec(self, spec, *, timeout=3, byte_limit=derin_arastirma.EN_COK_AKIS, cancel_event=None):
        spec_path = self.folder / "case.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")

        def start(arguments, **options):
            self.options.append(options)
            process = self.real_popen([sys.executable, "-X", "utf8", "-u", str(self.script), str(spec_path)], **options)
            self.processes.append(process)
            return process

        stream = derin_arastirma._Akis("deep")
        with patch.object(derin_arastirma.subprocess, "Popen", side_effect=start):
            derin_arastirma._calistir(["never-the-real-cli"], b"public test input", timeout, stream.satir,
                                      byte_limit=byte_limit, cancel_event=cancel_event)
        return json.loads(stream.yanit())

    def test_real_child_process_delivers_json_without_stderr_or_shell(self):
        value = self.run_spec({"events": [use(), tool_result(), result(report(external_sources=[external()]))],
                               "stderr": "fake-sensitive-account-info"})
        self.assertEqual(value["external_sources"], [external()])
        self.assertFalse(self.options[0]["shell"])
        self.assertEqual(self.options[0]["stderr"], subprocess.DEVNULL)
        self.assertNotEqual(Path(self.options[0]["cwd"]), Path.cwd())
        self.assertFalse(Path(self.options[0]["cwd"]).exists())
        if os.name == "nt":
            self.assertEqual(self.options[0]["creationflags"], subprocess.CREATE_NO_WINDOW)
            self.assertEqual(self.options[0]["startupinfo"].wShowWindow, subprocess.SW_HIDE)
        self.assertEqual(self.processes[0].poll(), 0)

    def test_timeout_kills_only_owned_process(self):
        other = self.real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **derin_arastirma._gizli_pencere())
        try:
            started = time.monotonic()
            with self.assertRaises(derin_arastirma.DerinArastirmaZamanAsimi) as caught:
                self.run_spec({"sleep": 30}, timeout=0.3)
            self.assertEqual(derin_arastirma.claude_hata_bilgisi(caught.exception)["code"], "timeout")
            self.assertLess(time.monotonic() - started, 3)
            self.assertIsNotNone(self.processes[0].poll())
            self.assertIsNone(other.poll())
        finally:
            other.kill()
            other.wait(timeout=3)

    def test_cancel_event_kills_waiting_owned_process(self):
        event = threading.Event()
        timer = threading.Timer(0.25, event.set)
        timer.start()
        try:
            with self.assertRaises(derin_arastirma.DerinArastirmaIptal) as caught:
                self.run_spec({"sleep": 30}, cancel_event=event)
            self.assertEqual(derin_arastirma.claude_hata_bilgisi(caught.exception)["code"], "cancelled")
            self.assertIsNotNone(self.processes[0].poll())
        finally:
            timer.cancel()

    def test_tool_budget_terminates_running_child(self):
        events = [use("WebSearch", f"id-{number}", query="Python") for number in range(4)]
        with self.assertRaises(derin_arastirma.DerinArastirmaHatasi) as caught:
            self.run_spec({"events": events, "sleep": 30})
        self.assertIn("web aracı sınırında", str(caught.exception))
        self.assertEqual(derin_arastirma.claude_hata_bilgisi(caught.exception)["code"], "tool_budget")
        self.assertIsNotNone(self.processes[0].poll())

    def test_stream_total_and_line_size_are_bounded(self):
        for spec, options in (({"events": [result()]}, {"byte_limit": 100}),
                              ({"raw": "x" * (derin_arastirma.EN_COK_SATIR + 1), "sleep": 30}, {})):
            with self.subTest(options=options), self.assertRaises(derin_arastirma.DerinArastirmaHatasi):
                self.run_spec(spec, **options)
            self.assertIsNotNone(self.processes[-1].poll())

    def test_nonzero_exit_does_not_return_an_earlier_success(self):
        with self.assertRaises(derin_arastirma.DerinArastirmaHatasi) as caught:
            self.run_spec({"events": [result()], "stderr": "fake-secret", "exit": 3})
        self.assertNotIn("fake-secret", str(caught.exception))
        self.assertEqual(derin_arastirma.claude_hata_bilgisi(caught.exception)["code"], "cli_failed")


class ClaudeHataBilgisiTestleri(unittest.TestCase):
    def test_unknown_exception_cannot_export_sensitive_text(self):
        self.assertIsNone(derin_arastirma.claude_hata_bilgisi(ValueError("FAKE-SECRET account@example.org")))
        for value in ("FAKE-SECRET account@example.org", {"token": "FAKE-SECRET"}):
            error = derin_arastirma.DerinArastirmaHatasi(value)
            info = derin_arastirma.claude_hata_bilgisi(error)
            self.assertEqual(info["code"], "internal_error")
            self.assertNotIn("FAKE-SECRET", str(error))
            self.assertNotIn("account@example.org", repr(info))

    def test_changed_exception_message_or_code_cannot_export_sensitive_text(self):
        error = derin_arastirma.DerinArastirmaZamanAsimi()
        error.args = ("FAKE-SECRET",)
        self.assertEqual(derin_arastirma.claude_hata_bilgisi(error)["code"], "timeout")
        self.assertNotIn("FAKE-SECRET", repr(derin_arastirma.claude_hata_bilgisi(error)))
        for value in ("FAKE-SECRET", ["unhashable"]):
            error.code = value
            self.assertEqual(derin_arastirma.claude_hata_bilgisi(error)["code"], "internal_error")

    def test_json_failure_keeps_safe_specific_code_without_output_content(self):
        for raw, expected in ((b'{"a":1,"a":2}', "duplicate_json"), (b'```json\n{"account":"FAKE-SECRET"}\n```', "invalid_json")):
            with self.subTest(expected=expected), self.assertRaises(derin_arastirma.DerinArastirmaHatasi) as caught:
                derin_arastirma._json_oku(raw)
            info = derin_arastirma.claude_hata_bilgisi(caught.exception)
            self.assertEqual(info["code"], expected)
            self.assertNotIn("FAKE-SECRET", repr(info))

    def test_unverified_external_link_has_specific_safe_code(self):
        stream = derin_arastirma._Akis("deep")
        stream.satir(json.dumps(result(report(external_sources=[external("https://example.org/private-title")]))).encode())
        with self.assertRaises(derin_arastirma.DerinArastirmaHatasi) as caught:
            stream.yanit()
        info = derin_arastirma.claude_hata_bilgisi(caught.exception)
        self.assertEqual(info["code"], "unverified_external_source")
        self.assertNotIn("private-title", repr(info))


if __name__ == "__main__":
    unittest.main()
