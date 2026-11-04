"""Anahtarın komut satırı ve dosyalar yerine geçici ortam veya Windows kasasıyla taşındığını doğrular."""

import contextlib
import io
import unittest
from unittest.mock import patch

import nvidia_anahtar


class NvidiaAnahtarTestleri(unittest.TestCase):
    def test_session_keeps_key_out_of_arguments_output_and_parent_environment(self):
        credential = "synthetic-credential-for-local-test"
        original = dict(nvidia_anahtar.os.environ)
        output = io.StringIO()
        captured = {}
        def run(command, **kwargs):
            captured.update(command=command, environment=dict(kwargs["env"]))
            return type("Result", (), {"returncode": 0})()
        with patch("nvidia_anahtar.getpass.getpass", return_value=credential), patch("nvidia_anahtar.subprocess.run", side_effect=run), contextlib.redirect_stdout(output):
            self.assertEqual(nvidia_anahtar.main(["--port", "8898", "--db", "example.sqlite3"]), 0)
        self.assertNotIn(credential, str(captured["command"]) + output.getvalue())
        self.assertEqual(captured["environment"]["UFUK_NVIDIA_API_KEY"], credential)
        self.assertEqual(dict(nvidia_anahtar.os.environ), original)
        self.assertIn("--elle-toplama", captured["command"])

    def test_invalid_key_never_starts_server(self):
        with patch("nvidia_anahtar.getpass.getpass", return_value="bad\nkey"), patch("nvidia_anahtar.subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(nvidia_anahtar.main([]), 2)
            run.assert_not_called()

    def test_visible_input_fallback_stops_before_reading_the_key(self):
        output, error = io.StringIO(), io.StringIO()
        original = dict(nvidia_anahtar.os.environ)
        def fallback(*args, **kwargs):
            return nvidia_anahtar.getpass.fallback_getpass("Synthetic prompt: ", stream=error)
        with patch("nvidia_anahtar.getpass.getpass", side_effect=fallback), \
                patch("nvidia_anahtar.getpass._raw_input") as raw_input, \
                patch("nvidia_anahtar.subprocess.run") as run, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            self.assertEqual(nvidia_anahtar.main([]), 2)
        raw_input.assert_not_called()
        run.assert_not_called()
        self.assertEqual(dict(nvidia_anahtar.os.environ), original)
        self.assertIn("Gizli giriş destekleyen terminal gerekli", error.getvalue())
        self.assertNotIn("Synthetic prompt", output.getvalue() + error.getvalue())

    def test_unavailable_or_cancelled_input_never_starts_server(self):
        for exception, code in ((EOFError, 2), (KeyboardInterrupt, 130)):
            with self.subTest(exception=exception), \
                    patch("nvidia_anahtar.getpass.getpass", side_effect=exception), \
                    patch("nvidia_anahtar.subprocess.run") as run, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(nvidia_anahtar.main([]), code)
                run.assert_not_called()

    @unittest.skipUnless(nvidia_anahtar.sys.platform == "win32", "Windows kimlik kasası")
    def test_windows_save_uses_only_designated_credential_entry(self):
        credential = "synthetic-credential-for-local-test"
        with patch("nvidia_anahtar.getpass.getpass", return_value=credential), patch("keyring.backends.Windows.WinVaultKeyring") as vault, patch("nvidia_anahtar.subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(nvidia_anahtar.main(["--save"]), 0)
            vault.return_value.set_password.assert_called_once_with("ufuk-nvidia", "api-key", credential)
            run.assert_not_called()
