"""Yerel pilotun canlıya çıkmadığını ve kullanıcı kararı öncesi kayıt üretmediğini sınar."""

import importlib.util
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

SPEC = importlib.util.spec_from_file_location("r0_pilot", Path(__file__).resolve().parents[1] / "araclar/r0-pilot.py")
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "pilot"

    def test_foreign_and_existing_directory_are_never_adopted_or_overwritten(self):
        self.directory.mkdir()
        private = self.directory / "original.txt"
        private.write_text("preserve", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            pilot.prepare(self.directory)
        with self.assertRaises((OSError, ValueError)):
            pilot.safe_directory(self.directory)
        self.assertEqual(private.read_text(encoding="utf-8"), "preserve")
        self.assertEqual(list(self.directory.iterdir()), [private])

    def test_hardlink_is_rejected_before_pilot_or_account_changes(self):
        pilot.prepare(self.directory)
        original = Path(self.temporary.name) / "unrelated.txt"
        original.write_text("preserve", encoding="utf-8")
        try:
            (self.directory / "linked.txt").hardlink_to(original)
        except OSError as exc:
            self.skipTest(str(exc))
        with self.assertRaises(ValueError):
            pilot.safe_directory(self.directory)
        self.assertEqual(original.read_text(encoding="utf-8"), "preserve")

    def test_transport_blocks_live_hosts_other_ports_redirect_targets_and_credentials(self):
        transport = pilot.LocalTransport(18794)
        self.addCleanup(transport.close)
        with patch.object(transport.transport, "handle_request", side_effect=AssertionError("Must not connect")):
            for address in ("https://devtrellis.org/api/community/cards", "http://localhost:18794/api/community/cards",
                            "http://127.0.0.1:18796/api/community/inbox", "http://user:pass@127.0.0.1:18794/api/community/cards",
                            "http://127.0.0.1:18794/other"):
                with self.subTest(address=address), self.assertRaises(httpx.ConnectError):
                    transport.handle_request(httpx.Request("GET", address))
        self.assertEqual(transport.calls, {"requests": 0, "write_requests": 0, "submission_requests": 0, "blocked": 5})

    def test_previews_and_real_read_requests_make_no_submission_or_vault_access(self):
        pilot.prepare(self.directory)
        real_connect = socket.socket.connect
        destinations = []

        def connect(sock, address):
            if isinstance(address, tuple):
                destinations.append(address[0])
                if address[0] not in {"127.0.0.1", "::1"}:
                    raise AssertionError("External network forbidden")
            return real_connect(sock, address)

        with patch.object(socket.socket, "connect", new=connect), \
             patch("radar.topluluk_baglantisi.IsletimSistemiKasasi.get", side_effect=AssertionError("No vault read")), \
             patch("radar.topluluk_baglantisi.IsletimSistemiKasasi.set", side_effect=AssertionError("No vault write")):
            with pilot.running(self.directory, 0, 0) as running:
                with httpx.Client(trust_env=False) as client:
                    response = client.get(running["review"])
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("Yalnız yerel pilot", response.text)
                    self.assertEqual(response.text.count("/static/contribution-review.html?preview="), 2)
                    self.assertNotIn("approval_token", response.text)
                    session = client.get(running["review"] + "/api/platform/community/session").json()
                    self.assertEqual(session["destination"], running["community"])
                    self.assertFalse(session["source_memory"]["remote_enabled"])
                    headers = {"X-Radar-CSRF": session["csrf_token"]}
                    response = client.post(running["review"] + "/api/platform/community/search", headers=headers,
                                           json={"query": "Synthetic", "limit": 3, "view": "summary"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["total"], 0)
                    self.assertEqual(response.json()["destination"], running["community"])
                    response = client.get(running["review"] + "/static/contribution-submissions.js")
                    self.assertIn(running["community"], response.text)
                    self.assertNotIn("https://devtrellis.org", response.text)
                    for name in ("confirm-dialog.js", "confirm-dialog.css"):
                        self.assertEqual(client.get(running["review"] + "/static/" + name).status_code, 200)
                    for route in ("/api/platform/community/source-memory", "/api/providers", "/static/platform.html"):
                        self.assertEqual(client.get(running["review"] + route).status_code, 404)
                    for headers in ({"Host": "example.org"}, {"Origin": "https://example.org"}, {"Sec-Fetch-Site": "cross-site"}):
                        self.assertEqual(client.get(running["review"] + "/r0/status", headers=headers).status_code, 403)
                    self.assertEqual(client.post(running["review"] + "/api/platform/community/send", json={}).status_code, 403)
                    result = client.get(running["review"] + "/r0/status").json()
                    self.assertEqual(result["entries"], [])
                    self.assertEqual(result["local_deliveries"], [])
                    self.assertEqual(result["actions"], [])
                    self.assertEqual(result["active_moderators"], 0)
                    self.assertEqual(result["network"], {"requests": 1, "write_requests": 0, "submission_requests": 0, "blocked": 0})
                    self.assertNotIn("csrf", json.dumps(result))
                    self.assertNotIn("capability", json.dumps(result))
                review = running["review"]
            with httpx.Client(trust_env=False) as client, self.assertRaises((httpx.ConnectError, httpx.ConnectTimeout)):
                client.get(review + "/api/health")
        self.assertTrue(destinations)
        self.assertTrue(all(address in {"127.0.0.1", "::1"} for address in destinations))

    def test_moderator_commands_delegate_only_to_existing_hidden_password_cli(self):
        pilot.prepare(self.directory)
        with patch("topluluk.main", return_value=0) as command:
            self.assertEqual(pilot.main(["--directory", str(self.directory), "moderator-add"]), 0)
            self.assertEqual(command.call_args.args[0], ["--db", str(self.directory / "community.sqlite3"),
                             "account-add", "r0-reviewer", "--display-name", "Local pilot reviewer", "--role", "moderator"])
            self.assertEqual(pilot.main(["--directory", str(self.directory), "moderator-reset"]), 0)
            self.assertEqual(command.call_args.args[0], ["--db", str(self.directory / "community.sqlite3"), "password-reset", "r0-reviewer"])
        self.assertEqual(pilot.read_rows(self.directory / "community.sqlite3", "SELECT id FROM community_accounts"), [])


if __name__ == "__main__":
    unittest.main()
