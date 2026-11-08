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
- **Personal application and standalone bridge:** `GET /api/platform/community/{sources/{address_key},coverage,topics}` pass the
  community's 200/404/410 bodies, ETag and 304 through unchanged; an older community answers as `unsupported_contract`,
  an unreachable one as `temporarily_unavailable` (contract error bodies, never the remote error text). The community address
  comes from `DEVTRELLIS_COMMUNITY_URL` (default `https://devtrellis.org`; `https` only, or plain `http` on loopback for a
  local pilot such as `http://127.0.0.1:18941`).
