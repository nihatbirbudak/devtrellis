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
  version is `k1-3.<ranking index version>` since alpha.12 (dotted and two-letter product names such as Node.js and Go;
  alpha.11 used `k1-2`), so the first start rebuilds it.
- **Journal mode (alpha.11):** the K1 store uses WAL like the v1 store, so a search read snapshot never makes a withdrawal or
  removal wait. The mode is set on every start after the role check and persists in the file; an alpha.10 store switches on first
  open, and alpha.10 code opens a WAL store unchanged. The `-wal` and `-shm` files sit next to `k1.sqlite3` in the same private
  directory (SQLite gives them the database file's permissions). After expired private text is purged the WAL is checkpointed
  with `TRUNCATE`. The backup tool still converts its private copy to a standalone DELETE file; a restored store returns to WAL
  on first open.
- **Reading additions (alpha.15), off by default:** the settings file key `"reading_extensions": true` turns them on.
  Off (default): the gate runs the v0.2.3 rules, submissions with the new fields get `422 reading_extensions_off`, capabilities
  list only `machine_not_covered`, every record is in the alpha.14 shape, and rolling back to alpha.14 needs nothing.
  (`gate.version` names the rule set that ran. Exception: an older-style submission that passed the gate while the setting was
  on and is published by a moderator after it was turned off keeps that result, so its record says `denetim-kapisi-v0.2.4`; it
  has no snapshots or absence checks and the alpha.14 code reads it.) On: records
  may carry `content.not_in_sources` / `content.not_researched`, linked `content.answers` items and
  `publication.gate.source_snapshots` / `absence_checks` (contract §12; capabilities `record_extensions`); the search index and the
  v1-shaped card take only the question text of linked answers and add the absence notes to the `Limits` field (with the page
  addresses the server resolved) marked as the contributor's statement. No database schema change. **Once the setting has been
  on, every record published since needs the rollback tool before the server goes back to alpha.14 or earlier.** With server and
  worker stopped: first decide or withdraw every `queued`, `retry_wait` or `pending_review` submission with the new fields (the
  tool lists them), then run `python araclar/okuma-eki-geri-al.py --db data/k1/k1.sqlite3` (dry run) and again with
  `--yaz --yedek <new file>` (an existing file is refused). Without it the older public dump stops on those records, `k1-reindex`
  fails and the older search index fails on linked answers. The tool exists only in the new release folder: keep that folder
  until the rollback is done. The backup file holds the original public record bodies; `--geri-yukle <file> --yaz` restores
  them after upgrading again. Optionally `topluluk.py k1-reindex` afterwards. Turning the setting off again (without rollback)
  is safe for reading; pending submissions with the new fields then get `409 reading_extensions_off` on publication, and
  **queued ones (`queued`, `retry_wait`) are rejected for good by the worker** (`sema.bilinmeyen_alan`, as alpha.14 would): let
  the worker finish them while the setting is still on, or have them withdrawn, before turning it off. While published records
  with the additions remain visible, capabilities keep listing all record extensions with the setting off (those records are
  still served); acceptance of the new fields is told by the submission answer.
- **Extension path (alpha.15, 14a), off by default:** the settings file key `"extensions": true` turns it on; limits
  `limits.extension_queue_per_record` (default 3) and `limits.extension_per_record_per_day` (default 5). Off: `kind:
  record_extension` gets `422 extensions_off`, queued extension submissions wait unprocessed, no table is created and every record
  stays in the alpha.14 shape. On: an extension is merged at gate time with the record's current version and published as the
  record's next version; the previous version stays readable at its version address but is no longer searched or listed; holds
  `extension_coverage_removal`, `extension_conflict`, `extension_daily_cap`; `429 parent_busy`. The first extension submission
  creates the side table `v4_extensions` (`user_version` stays 1). Moderators remove the newest version with the extension's
  submission id and the whole record with the original submission id. **Once the setting has been on, rolling back to alpha.14 or
  earlier needs the tool.** With server and worker stopped: let queued and held extension submissions be processed or decided (the
  tool refuses to write while any wait), run `python araclar/uzanti-geri-al.py --db data/k1/k1.sqlite3` (dry run) and again with
  `--yaz --yedek <new file>` (an existing file is refused), and remove `extensions` and the two limits from the settings file. The
  tool removes `lineage` and parks withdrawn tip versions in the backup; `--geri-yukle <file> --yaz` restores them after upgrading
  again. The tool exists only in the new release folder: keep that folder until the rollback is done. Turning the setting off again
  without rolling back is safe for reading; held extensions then get `409 extensions_off` on publication and queued ones wait.
  Known limit: with a setting off, every capabilities request scans the visible records (`LIKE` filter, then JSON) for reading
  additions and for `lineage`; fine at today's size, a cache is needed if the record count grows.
  Operator notes (acceptance round 1): **do not turn the setting on while alpha.13 or alpha.14 bridges are in wide use** (they do
  not mark which claims an extension added). A middle version cannot be removed on its own: remove the newer versions from the
  newest down (each with its own submission id) or the whole record (original submission id). `409 previous_version_missing`
  means the version below has no body (older purge); only the whole-record removal is left. Rolling back both features:
  run the extension tool first, then `okuma-eki-geri-al.py`; on upgrading again restore reading additions first, then
  `uzanti-geri-al.py --geri-yukle <file> --yaz` (it also marks earlier versions of records the older code took down). Delete the
  extension backup file after that restore (it keeps parked tip bodies). With the setting off a queued extension cannot be deleted
  by the operator: turn the setting on briefly and let a moderator decide it, or ask its contributor to withdraw it. Per record at
  most `extension_queue_per_record` extensions are `queued`; as many may wait for a retry, and a further one that cannot be
  checked becomes `unverifiable` at once. Anyone can fill that retry pile (acceptance round 2, K-d): extensions that quote the
  sender's own failing page wait for a retry; while they wait, a legitimate extension whose page fails briefly becomes
  `unverifiable` at once and has to be sent again. The `queued` limit is not affected. A moderator cannot act on an extension
  while it waits for a retry (a decision answers `409 not_in_review`); each one becomes `unverifiable` when its retries run
  out (`max_attempts`). If one sender floods a record, the stop switch sends extensions that pass the gate to the
  moderation queue instead of publishing them.
- **Hold instruction-like submissions (alpha.15 security), off by default:** the settings file key
  `"hold_instruction_like": true` turns it on (the owner's decision). Off (default): gate results, records and the publication
  decision are as before; agent-directed warnings (role imitation, addressing the agent, "run this"/"call the tool", new package)
  do not stop automatic publishing. On: (1) the gate also runs a wider instruction scan on the contributor's own text (not on
  quotes; an extension's `change_summary` included), warnings only: `yonerge.dogrulama_komutu` ("to verify, run `…`") and
  `yonerge.kurulum_on_kosulu` ("first `pip install …` is required"); the rule set name gets `+yonerge-genis-v1`; (2) a
  submission that carries any warning of the class goes to the moderation queue (`pending_review`, `hold_reason:
  instruction_like_content`) instead of being published; the contributor sees that reason and the warning codes in the
  submission status. The class is defined once in `radar/denetim/bekletme.py`: `yonerge.rol_taklidi`, `ajana_hitap`,
  `calistir_buyrugu`, `kullaniciya_sormadan`, `supheli_komut`, `gizli_yorum`, the two wide-scan codes, and `paket.cok_yeni`,
  `dogrudan_adres`, `ozel_dizin`, `ekosistem_desteklenmiyor`. No reject rule changes. Measured without network: 0 of 30 real
  packages (14 frozen records, 12 Grok bodies, 4 gate test packages) would be held; package age (`paket.cok_yeni`) needs the
  network and was not counted. The independent acceptance (round 2, small sets): 12 of 16 hostile, politely worded instructions
  passed (3 held, 1 rejected by an existing rule); 12 of 20 legitimate texts that contain a command were held; an instruction
  inside a quote from a page the attacker controls passed without a warning. Treat the setting as a **moderator pre-filter**
  (more moderator work, some catches), not as a security lock. A moderator decides held submissions as usual. Rolling back: remove the key from the settings
  file before starting alpha.14 code (older code refuses unknown keys); records published with it on carry the longer
  `gate.version` name, and held submissions keep `hold_reason: instruction_like_content`; the alpha.14 code treats both as plain
  strings (read in its code, not run against such a store). This is a mitigation, not a lock: the scan is heuristic, other wording
  passes, and a malicious package that has been on PyPI or npm for more than 30 days is not flagged.
- **Personal application and standalone bridge:** `GET /api/platform/community/{sources/{address_key},coverage,topics}` pass the
  community's 200/404/410 bodies, ETag and 304 through unchanged; an older community answers as `unsupported_contract`,
  an unreachable one as `temporarily_unavailable` (contract error bodies, never the remote error text). The community address
  comes from `DEVTRELLIS_COMMUNITY_URL` (default `https://devtrellis.org`; `https` only, or plain `http` on loopback for a
  local pilot such as `http://127.0.0.1:18941`).
