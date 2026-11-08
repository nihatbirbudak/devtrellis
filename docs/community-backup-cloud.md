# Encrypted off-host backup preparation

This is an **operator preparation**, not an activated cloud backup. The wrapper defaults to a local plan. It does not create a bucket, initialize a restic repository, install software, register timers, stop a service, prune snapshots or activate a recovered database. A real restic executable and off-host recovery still need independent acceptance.

## Provider recommendation, checked 6 November 2026

Use a dedicated **Backblaze B2 private bucket with the S3-compatible endpoint** for the first pilot. B2 currently includes the first 10 GB of storage, standard API calls and egress up to three times average monthly storage; larger storage and excess egress cost money. R2 Standard is also suitable: its monthly allowance is 10 GB-month, one million Class A and ten million Class B operations, with free egress. A nightly small backup does not by itself demonstrate an R2 operation-limit problem. B2 is a simple default, not a perpetual-free guarantee. Check actual account usage before enabling recurring transfers. Sources: [B2 pricing](https://www.backblaze.com/cloud-storage/pricing), [B2 API update](https://www.backblaze.com/blog/backblaze-pricing-and-product-updates/), [R2 pricing](https://developers.cloudflare.com/r2/pricing/).

Restic recommends B2's S3 API over its native B2 backend. For S3 deletions, B2 can retain hidden versions; the dedicated bucket's lifecycle needs review. Do not apply a generic age-based deletion rule to live restic pack files: a recent snapshot can still reference old packs. [Restic repository guide](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html)

## What can be recovered

The five authenticated format-2 roles remain community, feed, source memory, objections and request limits. A sixth, optional role `k1` (K1 v4 contributions) is required whenever the server runs the K1 path; the cloud profile accepts it and removes every unpublished K1 candidate text and withdrawn/removed record body from the copy (published v4 records remain). **The private support/contribution table `community_requests` is inside the community database**, not a sixth store. Published research and the feed remain covered.

Use `community-backup.py backup --cloud-safe` to create the only profile accepted by the cloud wrapper. It removes, from the copy only:

- Every private support/contribution request, including recent requests and contact details.
- Every objection's private claim, reason and submitted source list. Approved public correction text, references and status metadata remain; unresolved objections become expired in the recovery copy.
- Account sessions.

**These excluded fields cannot be recovered from this cloud backup.** Live records are untouched. This is not a complete anonymity filter: moderator accounts, capability hashes and other community state still require encrypted, restricted storage. It is a deliberate recovery tradeoff for the two 90-day private record types. Full encrypted local backups are still possible without `--cloud-safe`, but this wrapper refuses to upload them.

The normal snapshot and restore paths also remove expired request rows and expired objection text at the original `created_at + 90 days` boundary. Restore performs this cleanup in memory before writing recovered SQLite files. It never renews the retention clock. This does not delete older local backups, existing cloud backups, withdrawn records in older backups, filesystem snapshots or provider versions.

**G4 private summary jobs, ownership marker and model budget remain excluded. G4 recovery stays closed.** The same separately held `DEVTRELLIS_RATE_KEY` is required to use restored request limits. Neither that key nor contributor vaults are backed up here. See [store-level backup and recovery](community-backup.md).

## Secret handling and installation gate

The installed application environment must already have `requirements-backup.txt`. Verify the restic binary's source, checksum and version separately; this change does not download it. The documented CLI contract was checked against restic's current stable documentation. Local tests use a restic substitute, not a real restic repository.

Provide these only through a secret manager's child-process environment or a hidden terminal prompt:

| Name | Purpose |
| --- | --- |
| `DEVTRELLIS_BACKUP_KEY` | Existing Fernet key for the SQLite package; separate recovery access is essential |
| `RESTIC_PASSWORD` | Independent restic repository encryption password |
| `AWS_ACCESS_KEY_ID` | Bucket-restricted standard B2 key ID, or R2 access ID |
| `AWS_SECRET_ACCESS_KEY` | Matching bucket secret |

The wrapper passes only the restic/S3 secrets and a small operating-system environment to the restic child. App/model secrets, password commands and credential-file settings are not inherited. It suppresses raw restic output in failure logs, checks the exit code, bounds output and terminates only its own timed-out process. Plaintext secrets are never written by these helpers. Process memory and environment remain visible to sufficiently privileged local users; this is not protection from a compromised host.

For an interactive operator run, `community-backup-secret-session.py snapshot -- <backup arguments>` or `... cloud -- <cloud arguments>` asks for hidden input, runs one command, then restores the previous process environment. A non-interactive input stream or inability to hide input fails. It does **not** persist a vault. An unattended server vault/backend and its recovery procedure must be selected and tested before scheduling; do not use `.env`, systemd `Environment=` secrets, password files, shell history or copied chat credentials.

## Create, upload and rehearse

First stop all writers and drain active jobs for the complete snapshot interval; see the store-level guide. This release uses a stopped-writer maintenance window, not an online multi-database transaction. The CLI acknowledgement alone does not acquire a service lock. Resume writers only after the encrypted package is complete; the upload can then run separately.

```text
python -B araclar/community-backup-secret-session.py snapshot -- backup \
  --community /srv/devtrellis/data/community.sqlite3 \
  --feed /srv/devtrellis/data/feed.sqlite3 \
  --source-memory /srv/devtrellis/data/source-memory.sqlite3 \
  --objections /srv/devtrellis/data/objections.sqlite3 \
  --rate-limits /srv/devtrellis/data/limits.sqlite3 \
  --target /srv/devtrellis/staging/NEW_TIMESTAMP --writers-stopped --cloud-safe
```

The paths above are examples, not detected server paths. New targets require an existing private parent. Every path component must be free of links/junctions; existing targets are not overwritten. The server cloud profile requires all five roles, so a disabled/missing store must receive a separate, explicit deployment decision.

This command is safe to inspect without credentials or restic: it only prints a plan.

```text
python -B araclar/community-backup-cloud.py upload
```

After bucket creation, one-time repository initialization, secret recovery and owner approval, the actual upload form is:

```text
python -B araclar/community-backup-secret-session.py cloud -- upload --execute \
  --restic /usr/local/bin/restic \
  --repository s3:https://S3_ENDPOINT/BUCKET/devtrellis-community-v2 \
  --source /srv/devtrellis/staging/NEW_TIMESTAMP \
  --health /srv/devtrellis/health/upload-NEW_TIMESTAMP.json
```

Only B2/R2 HTTPS S3 endpoints and the exact dedicated repository prefix are accepted. Secrets in the URL are rejected. The validated, encrypted package is streamed to restic under the fixed filename `devtrellis-backup.tar`. The pilot package limit is **64 MiB**, all five roles are checked, and unexpected files are rejected. Both encryption keys are needed for recovery. Fresh Fernet ciphertext limits restic's deduplication; budget storage as new ciphertext per backup, not as changed SQLite pages.

Monthly, use the same hidden-prompt helper with `cloud -- rehearse --execute`, the same `--restic` and `--repository`, a fresh `--target` and a fresh `--health`. `--snapshot` accepts an exact returned snapshot ID; its default `latest` is restricted by the fixed host/tag. The job runs restic's full data check, downloads only the fixed archive, rejects archive links/traversal/duplicates, authenticates every store, and restores into `<target>/recovered`. Failed targets are quarantined; they are not connected to the live service or silently deleted. The result explicitly leaves **application acceptance pending**: an operator still verifies report reads, public corrections, preserved source/rate budgets with the original rate key, excluded private records and the actual deployment's complete store inventory.

## Retention, scheduling and health

- Proposed recovery history: 30 days. `retention-plan --execute` invokes `restic forget --dry-run --keep-within 30d` for this host/tag and returns only proposed snapshot IDs. It never deletes or prunes. `--keep-within` is relative to the latest snapshot, so a stopped backup job does not implement a wall-clock expiry guarantee. [Restic removal rules](https://restic.readthedocs.io/en/stable/060_forget.html)
- After reviewing a concrete plan, an operator must separately approve/apply forget and prune, then verify provider version/lifecycle deletion. Snapshot removal alone does not remove unreferenced encrypted data. That maintenance has not been executed here.
- `90 days of snapshots` would not enforce `90 days from a private record's creation`: an 89-day-old record in a new backup could persist for 179 days. The supplied cloud profile avoids that problem by never uploading those private records. The pre-existing full local backup retention remains a separate open gate.
- A 64 MiB daily package is about 1.9 GiB for 30 packages before repository overhead. This is a planning bound, not measured cloud consumption or a spending cap. Old snapshots, hidden versions, account-wide use and downloads count. No automatic free-tier guarantee is made.

Suggested timing is one nightly stopped-writer snapshot followed by upload, and one monthly isolated recovery. Inspect existing timers first; reuse the existing backup window instead of running two concurrent backup systems. A scheduler must take one exclusive job lock, stop/drain the entire known writer set, restore only the services it stopped, provide secrets in process memory, choose fresh output paths, and treat any failed step as failure. These deployment-specific hooks are **not wired or enabled by this preparation**.

Disabled Linux timer sketches, for operator review only:

```ini
# devtrellis-cloud-backup.timer.example — do not install yet
[Timer]
OnCalendar=*-*-* 04:30:00
RandomizedDelaySec=300
Persistent=true
Unit=devtrellis-cloud-backup.service
# The service must implement the reviewed snapshot/vault/locking procedure above.
```

```ini
# devtrellis-cloud-rehearsal.timer.example — do not install yet
[Timer]
OnCalendar=*-*-01 05:00:00
Persistent=true
Unit=devtrellis-cloud-rehearsal.service
# Supply the verified secret backend and isolated fresh target before activation.
```

On Windows use an equivalent reviewed Task Scheduler action with **Do not start a new instance** and the dedicated account; no Windows task is installed by this change. Server evidence for this project is Linux, while the current implementation tests ran on Windows. Windows tests do not validate systemd syntax or Linux vault availability.

Each executed run writes a new, restricted JSON health record: operation, UTC finish time, success/failure and bounded metadata. It contains no credentials, record bodies or paths. Run `community-backup-cloud.py health --health <record>` to require successful upload/rehearsal freshness within 30 hours. For monthly rehearsal use `--max-age-hours 840` (35 days). A retention-plan result never satisfies upload/rehearsal health. Monitor the last attempted run as well as the last success; choosing an older success can hide a new failure. No remote notification is configured.

## Kullanıcı için kısa B2 kurulum adımları

1. Backblaze hesabında B2 Cloud Storage'ı aç; **Buckets → Create a Bucket → Private** seç. Yalnız DevTrellis için ayrı kova oluştur. Beklenen: **Private** ve bir **Endpoint** değeri. [Kova rehberi](https://www.backblaze.com/docs/cloud-storage-create-and-manage-buckets)
2. **Application Keys → Add a New Application Key**: yalnız bu kovaya erişim, `devtrellis-community-v2/` dosya öneki, Read and Write. Master anahtarı uygulamaya verme. Beklenen: ayrı `keyID` ve yalnız bir kez gösterilen `applicationKey`. Restic kilitlerini kaldırmak için ilgili dosya silme yetkisi de gerekebilir; izinler gerçek pilotta doğrulanır. Diğer kovalara erişim verme. [Anahtar rehberi](https://www.backblaze.com/docs/cloud-storage-create-and-manage-app-keys)
3. Anahtarları sohbet, metin dosyası veya terminal komutuna yapıştırma. Kendi parola yöneticinde koru; sunucuda gizli giriş yardımcısının ilgili istemine gir. Anahtarlar o işlem bitince bu yardımcı tarafından saklanmaz. Gecelik iş için sunucunun kalıcı güvenli kasası ayrıca kurulup test edilmeli.
4. Bağımsız Fernet anahtarını ve restic parolasını kendi güvenli kasanda ayrı kurtarma erişimiyle koru. **Anahtarların biri kaybolursa yedek kurtarılamaz.** Yardımcı bunları üretip sana sakladığını iddia etmez.
5. Kovada kullanım/faturalama uyarılarını aç. Şimdilik tam otomatik işi etkinleştirme: ilk gerçek aktarım, ayrı dizinde geri yükleme, özel alan kaybını kabul, eski yerel yedeklerin saklama kararı ve sağlayıcı sürüm temizliği birlikte doğrulanacak.

Bu profil destek taleplerini ve itirazların özel metinlerini buluttan kurtarmaz; yayımlanan araştırmaları korumaya öncelik verir. Daha kapsamlı özel veri kurtarma istenirse kayıt yaşına bağlı yedek/anahtar silme tasarımı ve gerçek silme kanıtı gerekir. Mevcut bir sunucu yedeği bu hazırlıkta silinmedi.
