"""GitHub sürüm Atom kaynaklarının eski URL kayıtlarını çoğaltmadan toplandığını sınar."""

import json
import tempfile
import unittest
from pathlib import Path

from radar.depo import Depo
from radar.toplayici import _parse_feed


ROOT = Path(__file__).resolve().parents[1]


class GitHubAtomTest(unittest.TestCase):
    def test_nine_github_releases_use_bounded_atom(self):
        sources = json.loads((ROOT / "radar" / "kaynaklar.json").read_text(encoding="utf-8"))
        github = [source for source in sources if source.get("fetch_url", "").startswith("https://github.com/")
                  and source["fetch_url"].endswith("/releases.atom")]
        self.assertEqual(len(github), 9)
        self.assertTrue(all(source["kind"] == "atom" and source["fetch_hosts"] == ["github.com"] for source in github))

    def test_atom_and_old_api_identity_share_url_card(self):
        source = next(source for source in json.loads((ROOT / "radar" / "kaynaklar.json").read_text(encoding="utf-8"))
                      if source["id"] == "llama_cpp")
        url = "https://github.com/ggml-org/llama.cpp/releases/tag/v1"
        atom = f'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Releases</title>
        <entry><id>tag:github.com,2008:Repository/1/v1</id><title>Release v1</title><link href="{url}"/>
        <updated>2026-11-06T00:00:00Z</updated><summary>Source-backed release.</summary></entry></feed>'''.encode()
        items = _parse_feed(atom, source, source["fetch_url"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], url)
        with tempfile.TemporaryDirectory() as directory:
            depot = Depo(Path(directory) / "feed.sqlite3", [source])
            depot.kaydet(source["id"], [{**items[0], "external_id": "old-api-id"}])
            depot.kaydet(source["id"], items)
            depot.kaydet(source["id"], items)
            with depot.baglanti() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM items WHERE url=?", (url,)).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
