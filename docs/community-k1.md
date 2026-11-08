# K1 v4 contribution path (operator notes)

The K1 path accepts v4 source cards, research reports and topic summaries, runs them through the machine gate
(`radar/denetim`, the single source of the gate) and publishes or queues them. It is **disabled by default**: without
`--k1-config` and `--k1-db` none of its routes exist. Contract: `arastirma/…/k0-kaynak-takibi/K1-V4-SOZLESME-TASLAGI-r2.md`.

```powershell
python -B -X utf8 topluluk.py k1-settings-example > k1-settings.json
python -B -X utf8 topluluk.py serve --public-url https://community.example.org `
  --k1-config k1-settings.json --k1-db data/k1/k1.sqlite3 …
```

- **Automatic publishing is off by default** (`auto_publish: false`). While off, gate-passed records wait in the moderation
  queue (`/api/community/v4/review`). Turning it on is a settings change that needs the project owner's decision.
- **Stop switch:** a moderator (`POST /api/community/v4/publishing/stop`), the operator (`topluluk.py k1-stop`) or the settings
  file (`publishing_stopped: true`) stops new automatic publication. Published records stay. While stopped, gate-passed records
  go to the moderation queue, and **a moderator can still publish them by hand** (a human decision, not automatic
  publication). A moderator cannot clear the settings-file switch. The public capabilities document does not reveal the switch.
- **Limits:** per anonymous identity per minute and per day, plus a global hourly total. When the gate or moderation queue
  is full, only new submissions receive 429; reads, status and moderation keep working.
- **Retention:** private candidate text of submissions that ended without publication, and the body of withdrawn or removed
  records, is deleted after `private_retention_days` (default 30). Tombstones, counters and reason codes remain.
- **Backup:** include `--k1 data/k1/k1.sqlite3` in the encrypted operator backup (`araclar/community-backup.py`, see
  [community-backup.md](community-backup.md)). It is the only copy of published v4 records. There is no plain copy command.
- **Logs:** worker errors go to the `devtrellis.k1` logger with the error type only, never candidate text.
- **Store permissions:** `--k1-db` must point into a directory used only by the K1 store. On every start the directory is
  restricted to the owner (POSIX `0700`, Windows protected DACL) and the database file to `0600`, independent of the process
  umask and of whether the directory already existed.
- **Stop status:** `k1-status` and the moderator publishing view report `stopped_by` as `config` (settings file), `moderator`
  or `cli` (`topluluk.py k1-stop`).
- **Contract schemas:** the r2 JSON schemas (`public-record-v4-r2`, `contribution-candidate-v4-r2`, `lookup-coverage`) live in
  `radar/sema/` and are byte copies of the research folder's files; tests read them from there. The contract text stays in
  `arastirma/…/k0-kaynak-takibi/`.
- **Search (second slice, 8 Nov 2026):** published v4 records take part in the public `GET /api/community/cards` list and search
  (`kind=research` or empty; `search_mode=all` and `ranked`) with the same ranking keys as v1 reports (coverage, field weight,
  bm25, date, id). The two FTS indexes compute bm25 with their own document counts, so in the merged ranked list a v4 record's
  bm25 is recomputed with the same FTS5 formula and the v1 index statistics (row count, average length, per-term document
  count), as if it were in the v1 library; v1 keys keep SQLite's own bm25 and their order is unchanged (alpha.11). Indexed fields: title, `answers[]`, scope product names as tags, summary + short answer, claims with quotes and
  recommendations as body, `not_covered` as the `Limits` field. Items carry `record_type: "v4"`, `kind`, `human_reviewed`,
  `publication` (path, label, policy and gate version), `answers` and `link`; the `packet` keeps the v1 shape so the web UI, the
  summary view and the K2 bridge read it unchanged. `library.published_research_total` includes v4 records
  (`library.published_v4_total` is listed separately). Withdrawn or removed records leave the index in the same transaction.
  v4 records have no HTML report page: `/r/{id}/v/{n}` answers `303` to `/api/community/v4/records/{id}/v/{n}` for them.
- **Address index for every kind (second slice):** source cards, research reports and topic summaries are found under every
  canonical address they cite (`claims[].evidence.address_key` and `content.sources[]`). When several records share an address,
  lookup orders them source card → report → topic summary, newest first; the rest appear as `candidates` when they carry a
  list-shaped `applies_to`. Coverage lists every record under every address; tombstones are per address. `topics` fills
  `products`/`terms` from a free-text `applies_to` by inference (product = a word of the text that equals the first label of a
  cited source's registered domain, or a part of a hyphenated label: `docs.github.com` + "GitHub" → `github`,
  `blog.rust-lang.org` + "Rust" → `rust`; sub-domain parts such as `blog`, `cloud`, `developer` never count; terms = the content
  words of the text). A report with a free-text `applies_to` cannot be a lookup `candidate` (the schema needs at least one
  `{product, versions, version_scheme}` item and no version is invented); it stays listed in coverage at that address.
- **Index migration and repair:** the address and search indexes are derived data. Every start reconciles them with the stored
  records (an alpha.10 store, where only source cards had an `address_key` column, migrates on first start; purged tombstones keep
  their column address). `topluluk.py k1-reindex --k1-db … --k1-config …` rebuilds both indexes completely and prints counts. The
  database `user_version` stays 1; encrypted backups made before the slice restore and migrate the same way. The search index
  version is `k1-2.<ranking index version>` since alpha.11 (new product rule), so the first start rebuilds it.
- **Journal mode (alpha.11):** the K1 store uses WAL like the v1 store, so a search read snapshot never makes a withdrawal or
  removal wait. The mode is set on every start after the role check and persists in the file; an alpha.10 store switches on first
  open, and alpha.10 code opens a WAL store unchanged. The `-wal` and `-shm` files sit next to `k1.sqlite3` in the same private
  directory (SQLite gives them the database file's permissions). After expired private text is purged the WAL is checkpointed
  with `TRUNCATE`. The backup tool still converts its private copy to a standalone DELETE file; a restored store returns to WAL
  on first open.
- **Personal application and standalone bridge:** `GET /api/platform/community/{sources/{address_key},coverage,topics}` pass the
  community's 200/404/410 bodies, ETag and 304 through unchanged; an older community answers as `unsupported_contract`,
  an unreachable one as `temporarily_unavailable` (contract error bodies, never the remote error text). The community address
  comes from `DEVTRELLIS_COMMUNITY_URL` (default `https://devtrellis.org`; `https` only, or plain `http` on loopback for a
  local pilot such as `http://127.0.0.1:18941`).
