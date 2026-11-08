# Public data dump (nightly JSON, optional git)

`araclar/kamu-dokum.py` writes the library's **published public records** to one dated JSON file so the library survives if its host or maintainer disappears. It is not a backup of the service: accounts, sessions, private candidate texts, objections, moderator notes, request limits and counters are deliberately absent. The encrypted operator backup ([community-backup.md](community-backup.md)) remains the recovery path for the running service.

Engineering report item D5 (6 November 2026).

## What goes in, what never does

| Included | Source | Excluded (never read or written) |
| --- | --- | --- |
| Every **visible published version** of v1 research and project cards (`packet`, id, version, `submitted_at` = revision time, `published_at` = moderator publish decision time or `null`, page link) | community store: `community_public_revisions`, `community_entries`, `community_revisions`; from `community_decisions` only the `created_at` of `publish` decisions, never the note | accounts, password hashes, sessions, anonymous inbox hashes, moderation notes, rejections, pending/rejected/hidden revisions, engagement counters, event receipts, private requests |
| Every **visible v4 record** exactly as the API serves it (`devtrellis-record`, schema 4) | K1 store: `v4_records` with `visible=1` | `v4_submissions` (candidate text, consent, management-key hashes, notes), `v4_events`, identity buckets |
| Minimal **tombstones** for withdrawn or removed v4 records: id, version, state, time (plus the contract name read from the record body, nothing else) | `v4_records` with `visible=0` | the removed body, the reason, the actor |

Both stores are opened read-only. The record field sets are **closed at every level**, not only at the top: v1 `packet`, `packet.sources[]`, `packet.provenance`, `packet.lineage` and `lineage.parents[]`; v4 `record`, `content`, `content.applies_to[]`, `content.sources[]`, `claims[]`, `claims[].applies_to[]`, `claims[].evidence`, `quote_match`, `claim_review`, `provenance` and `stages[]`, `rights` and `rights.quotes`, `publication`, `publication.gate`, `publication.label`, `corrections` (the K1 r2 schema declares `additionalProperties: false` on each). The chosen policy is **stop, not drop**: a field the exporter does not know, at any depth, stops the export with the field's path, so a future private field cannot leave the store unnoticed; when K1 adds a legitimate public field, the matching set here must be extended on purpose. `tests/test_kamu_dokum.py` plants private sentinel values in every private table and in nested positions and asserts that none reaches the file, the manifest or the console.

## File layout

`<target>/devtrellis-public-dump-v1-YYYYMMDD.json` plus `…-YYYYMMDD.manifest.json` (SHA-256, byte count, counts). A second run on the same UTC day refuses to overwrite. The document:

```json
{
  "schema": "devtrellis-public-dump", "schema_version": 1,
  "generated_at": "…", "generator": "devtrellis kamu-dokum 1",
  "site": {"public_url": "https://devtrellis.org", "sitemap": "…/sitemap.xml", "llms_txt": "…/llms.txt", "feed": "…/feed.atom"},
  "license": {"records": "…", "dump_structure": "CC0-1.0", "attribution": "…", "note_tr": "…", "legal_advice": false},
  "excluded": ["private candidate text", "…"],
  "counts": {"v1_records": 0, "v4_records": 0, "v4_tombstones": 0},
  "records": {"v1": [{"schema": "devtrellis-share-public", "kind": "research", "id": "…", "version": 1,
                      "submitted_at": "…", "published_at": "…|null", "link": "https://devtrellis.org/r/<id>/v/1", "packet": {"…": "…"}}],
              "v4": [{"schema": "devtrellis-record", "…": "…"}]},
  "tombstones": {"v4": [{"schema": "devtrellis-record-tombstone", "id": "…", "version": 1, "state": "withdrawn", "removed_at": "…"}]}
}
```

`site.sitemap` and `site.llms_txt` point to the live lists of v1 report pages; each v1 record's `link` is the exact-version page that `llms.txt` and the sitemap already use, and each v4 record carries its own API `link`. Publishing the dump file itself under the site (for example as a static download linked from `llms.txt`) is a server change that this delivery does not make.

## License note in the dump

The `license` object is written into every dump and checked on read. Each record keeps its own license field (v1 `packet.license`, v4 `rights.own_text_license`: CC0-1.0 or CC-BY-4.0, covering the contributor's own text only, as in the final plan's layer 5 and KD-013). v4 quotes are short source-linked quotes (at most 300 characters) and the quoted sources keep their own licenses. The dump's structure is CC0-1.0. The note states that it is not legal advice.

## Run

```powershell
python -B -X utf8 araclar/kamu-dokum.py export --community data/community/community.sqlite3 `
  --k1-db data/k1/k1.sqlite3 --target dumps --public-url https://devtrellis.org
python -B -X utf8 araclar/kamu-dokum.py inspect dumps/devtrellis-public-dump-v1-20261108.json
python -B -X utf8 araclar/kamu-dokum.py load dumps/devtrellis-public-dump-v1-20261108.json --db mirror/library.sqlite3
```

`inspect` validates the schema, the closed field sets, the counts and the manifest (`file`, `bytes`, SHA-256). **The manifest is required**: a dump without its `….manifest.json` beside it, or with a mismatching hash, is refused by both `inspect` and `load`. `load` opens a **new, empty** SQLite file (`public_records`, `public_tombstones`, `public_dump_meta`) and reports how many records were read back; an existing file is refused. This mirror is a read-only library copy, not a community database: it has no accounts, moderation or submission path.

`--git-commit` adds the two files to the target directory's git working tree and makes a local commit (author `DevTrellis public dump`); it never pushes. The target must be the **root** of a repository created for dumps; a subdirectory of another repository (for example `dumps/` inside the application checkout) is refused, and the dump files stay on disk uncommitted.

## Scheduling (documented, not installed)

```powershell
schtasks /Create /TN "DevTrellis Public Dump" /SC DAILY /ST 04:00 /TR "\"C:\path\to\.venv\Scripts\python.exe\" -B -X utf8 \"C:\path\to\araclar\kamu-dokum.py\" export --community \"C:\path\to\data\community\community.sqlite3\" --k1-db \"C:\path\to\data\k1\k1.sqlite3\" --target \"C:\path\to\dumps\" --public-url https://devtrellis.org"
```

```
0 4 * * * cd /srv/devtrellis && .venv/bin/python -B -X utf8 araclar/kamu-dokum.py export --community data/community/community.sqlite3 --k1-db data/k1/k1.sqlite3 --target dumps --public-url https://devtrellis.org >> dumps/run.log 2>&1
```

## Not in this delivery

Serving or announcing the dump from the live site, pushing a dump repository anywhere, Wayback or other third-party archiving (K6), and importing a dump into a running community service.
