# Encrypted community backup and isolated recovery

This command copies **community, feed, optional source memory, optional objections, persistent request limits and optional K1 v4 contribution** stores. It is an operator tool. It neither schedules backups nor stops services, transfers files, chooses an off-host destination or activates a restored service. The separate [cloud preparation guide](community-backup-cloud.md) supplies a restic transfer/rehearsal wrapper with an explicit private-data exclusion profile; real cloud acceptance is still separate.

## Acceptance boundaries

- Every SQLite file has its own consistent snapshot. **There is no online atomic snapshot across stores.** Stop every community writer, feed collector, source checker, moderator operation and model worker; drain ongoing work before acknowledging `--writers-stopped`. The flag records an operator statement, not a verified service lock. Active source-memory leases also make backup fail. Keep writers stopped until the backup finishes.
- **G4 private source-summary jobs, their ownership marker and daily model budget are not included.** Their budgets and recovery state are not preserved by this command. Full G4 recovery acceptance remains open until a separate validated backup/restore procedure includes them. Do not silently create empty replacement stores, reset budgets or automatically resume old model jobs.
- Include `--rate-limits` for a public service. Its stored request counts and expiry times are preserved, but correct recovery also requires the **same `DEVTRELLIS_RATE_KEY`**, held separately in the operator's secret store. This key is not included in the backup. Changing it makes existing address hashes unreachable and effectively resets those limits; neither this command nor a new empty rate DB is a substitute for preserving it. The role is optional only for local installations that have no persistent limiter.
- Account sessions are removed from the restored community DB. Anonymous capability hashes and source-check budgets are preserved. Contributors' corresponding secrets remain in their OS vaults; this backup cannot recover a lost vault.
- Support/contribution requests (`community_requests`) share the community database and are included by the ordinary backup. Both snapshot and restore purge expired request rows and expired objection fields at the original 90-day boundary. Restore applies this in memory before writing the new SQLite image, without waiting for a later application operation. It does not remove independent copies in older backups.
- Include `--k1` whenever the server runs with `--k1-config/--k1-db`: it holds every v4 submission, the moderation queue, published v4 records, tombstones, the publishing stop switch and the library revision. Without it, published v4 records cannot be recovered. Both snapshot and restore purge the private candidate text of submissions that ended without publication (rejected, unverifiable, moderator-rejected, withdrawn, removed) and the body of withdrawn/removed records after 30 days (`private_retention_days` default); restore clears unfinished gate leases. There is no separate plain `k1-backup` command.
- Optional `--cloud-safe` removes **all** private request rows, objection claim/reason/submitted-source text, all unpublished K1 candidate text and sessions from the copy, including unexpired records; sources remain unchanged. This profile cannot recover those private fields. It is the only profile accepted by the cloud wrapper; full private recovery and old full-backup retention require separate decisions.
- Format 2 binds each encrypted store to its role, snapshot identifier and complete role list. The earlier unreleased format 1 prototype is rejected; create a fresh backup with this version.

## Before running

1. Install the optional dependencies from `requirements-backup.txt` in the intended operator environment. Run the backup tests there; a skipped test because cryptography is absent is **not** backup acceptance.
2. Use an isolated service/operator account. Source and destination path components must not be symlinks, junctions or other reparse points. Files must be ordinary files with one hard link. Prepare the parent directory of each new target beforehand; a target that already exists is never overwritten.
3. Provide a Fernet key through the operator's secret manager as `DEVTRELLIS_BACKUP_KEY`. Do not put its value in the shell command, repository, report or backup directory. Preserve separate, tested recovery access to the key.
4. Stop and drain the writers described above. Record the release, stores, operator and maintenance time without copying private records into the report.

## Create and inspect a backup

```powershell
python -B -X utf8 araclar/community-backup.py backup `
  --community data/community/community.sqlite3 `
  --feed data/feed/community-feed.sqlite3 `
  --source-memory data/source-memory/source-memory.sqlite3 `
  --objections data/objections/objections.sqlite3 `
  --rate-limits data/private-rate/limits.sqlite3 `
  --k1 data/k1/k1.sqlite3 `
  --target backups/new-backup-directory --writers-stopped
```

Omit only optional stores that are disabled. Each database is limited to 256 MiB by this implementation. The script uses SQLite's backup API, including committed WAL pages, then changes **only its private temporary copy** to `journal_mode=DELETE`. It applies private retention (and the optional cloud exclusions) to that copy and compacts it before encryption. It checks integrity and the expected store schema before encryption. It never changes the source journal mode or resumes a worker.

The temporary SQLite copy is plaintext inside a unique directory restricted to the operator (Windows: owner, SYSTEM and administrators; POSIX: directory 0700 and file 0600). It is removed on normal completion and handled exceptions. A process or machine crash can leave that restricted temporary directory; inspect and remove only the identified `devtrellis-backup-*` directory after the process has stopped. File deletion is not a physical erasure guarantee. Memory also contains plaintext during copying and recovery.

All source snapshots are validated before the new backup target is created. Output uses exclusive file creation and restricted permissions. On a handled write failure, only files and directories created by that invocation are removed. If cleanup fails, the command reports that the target must not be used. A crash may leave an incomplete directory; success requires the completed manifest and a successful restore rehearsal.

## Rehearse recovery

```powershell
python -B -X utf8 araclar/community-backup.py restore `
  --source backups/new-backup-directory --target recovery/new-isolated-directory
```

All encrypted files, authenticated role bindings, SQLite images and store schemas are validated before creating the new restore target. A corrupt later store or wrong key leaves no newly created restore directory. Stores are restored to separate restricted directories:

| Role | Path below the restore target |
| --- | --- |
| Community | `community/community.sqlite3` |
| Feed | `feed/community-feed.sqlite3` |
| Source memory | `source_memory/source-memory.sqlite3` |
| Objections | `objections/objections.sqlite3` |
| Request limits | `rate_limits/limits.sqlite3` |
| K1 v4 contributions | `k1/k1.sqlite3` |

Keep this target isolated. Open it with the same application release and verify actual read operations, account sessions being revoked, published correction references, objection access/retention, source-check budget preservation and request limits with the same separately recovered key. Check that the selected release's optional stores are all accounted for. Record the evidence and operator decision before any separate live activation. An integrity check alone is not this acceptance.

Copy only the completed encrypted backup directory to the owner's chosen off-host destination, verify it there, and repeat the restore rehearsal on a schedule chosen by the operator. Keep the encryption key separate. This document does not authorize a deployment, scheduler change or external transfer.

## Türkçe kısa işletim notu

Yedekten önce tüm yazıcıları durdurup devam eden işleri bitirin; `--writers-stopped` bunun işletmeci beyanıdır. Ortak çevrimiçi snapshot garantisi yoktur. Her depo ayrı özel dizine geri yüklenir; mevcut hedefin üzerine yazılmaz. WAL dönüşümü yalnız izinleri sınırlandırılmış geçici düzmetin kopyada yapılır. Halka açık hizmette `--rate-limits` deposunu, K1 açıksa `--k1` deposunu da ekleyin (yayımlanan v4 kayıtlarının tek kopyası); aynı `DEVTRELLIS_RATE_KEY` ayrı gizli kasadan korunmalıdır. G4 özel taslak iş DB'si kapsam dışıdır ve tam G4 kurtarma kabulü henüz kapalıdır. Gerçek kurtarma provası ve işletmeci kararı olmadan yeni hedefi canlıya bağlamayın.

Yeni bulut hazırlığı: [bulut yedeği rehberi](community-backup-cloud.md). `--cloud-safe`, destek/başvuru kayıtları ve itiraz özel metinlerini kaynakta değil yalnız kopyada çıkarır; bu alanlar buluttan kurtarılamaz. Otomatik bulut işi ve gerçek sunucu dışı kurtarma kabulü henüz etkin değildir.
