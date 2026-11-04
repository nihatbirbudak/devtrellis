# Operate a DevTrellis community

[Personal setup](README.md) · [Türkçe kullanım](README.tr.md)

The community is a separate process and database. It provides public reading and search, invited contributor accounts, submission review and versioned publication. It does not store model credentials or run paid model jobs. The first release supports **one application process** with SQLite and a small moderated pilot.

## Local pilot

Install with `python -X utf8 kur.py`. The examples use Windows; on Unix replace `.venv\Scripts\python.exe` with `.venv/bin/python`.

Create separate contributor and moderator accounts in an interactive terminal. Passwords are entered without echo; only salted password hashes are stored. A terminal that cannot hide password input is rejected.

```powershell
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 account-add contributor --display-name "Pilot contributor"
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 account-add reviewer --display-name "Pilot reviewer" --role moderator
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 serve --public-url http://127.0.0.1:8782 --local-pilot --port 8782 --feed-db data/community-feed.sqlite3
```

Visit **http://127.0.0.1:8782**. A moderator cannot approve their own submission. The owner previews and exports a card from the personal workspace, then previews and submits that file on the community. Readers can download a published card and preview/import it into their own workspace.

New revisions enter review while the preceding published revision remains available. Withdrawal stops serving the card; it cannot recall previously downloaded copies. Duplicate request keys prevent repeated submission, not semantic duplicates between different authors. Accounts associate submissions with a contributor; they do not independently verify their factual claims.

## Public server

Use an isolated service account and a stable data directory outside release folders. Do not point this service at a personal database or expose the personal workspace through a reverse proxy.

1. Install dependencies on the selected host and create the invited accounts.
2. Bind the community process to its built-in `127.0.0.1` listener. Run exactly one worker.
3. Terminate HTTPS on a reverse proxy on the **same host**. Forward the exact public Host and set the scheme to HTTPS. Replace incoming forwarded headers at the proxy, rather than trusting caller-supplied values. The process trusts proxy headers only from `127.0.0.1`.
4. Start with the final `--public-url https://community.example.org`, without `--local-pilot`. The hostname is an example, not a configured deployment. An incorrect host or plain HTTP in production mode is rejected.
5. Keep private databases, backups and logs outside the web root. Restrict them to the service operator. Configure OS service supervision, restricted logs, external request limits and periodic backups for that host.
6. Verify HTTPS, cookie settings, the actual proxy/client-address chain, restart, source collection and restoration on the real host before announcing it.

The local pilot and simulated HTTPS tests do not certify a particular reverse proxy or operating system. Host-specific service and proxy configuration is prepared once the operator selects the server and domain; no ports or router settings are automatically changed by these scripts.

The public API contains no CORS exception. Session cookies are HttpOnly and SameSite=Strict, and Secure outside the explicit local pilot. Writes require a matching Origin and CSRF token. Initial publication is manually reviewed. Use moderator withdrawal for published content that must be removed and communicate corrections to affected contributors.

## Populate developments

The feed store is separate from accounts and community cards. Collect using the personal CLI against the shared feed database:

```powershell
.\.venv\Scripts\python.exe -X utf8 calistir.py topla --db data/community-feed.sqlite3
```

Schedule this command with the host's scheduler after the manual run succeeds. The collector respects configured source intervals and existing request restrictions. It does not run a model. Report source failures and stale material honestly; a successful fetch is not factual verification.

## Backup, restore and account recovery

```powershell
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 backup backups/community-before-update.sqlite3
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community-restored.sqlite3 restore backups/community-before-update.sqlite3
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 password-reset contributor
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 account-disable contributor
```

Backup and restore require a new destination and never overwrite an existing database. Restoration checks database integrity and removes old sessions. Password reset and account disable revoke that account's sessions. Disabled accounts remain disabled after a password reset; account recovery is an operator decision.

Back up the separate feed database with `calistir.py yedekle --db data/community-feed.sqlite3`. Before a release update, record the running version and preserve a tested backup. For rollback after a schema change, stop the service, preserve the newer data and restart the older release against its pre-update backup. A database hash or an integrity check alone does not replace a demonstrated restore.

## Pilot acceptance

- A contributor submits a sourced card and a moderator publishes it.
- Another person finds and imports it without a model call or contribution requirement.
- An unrelated account cannot edit it; stale updates fail with a conflict.
- A pending correction does not expose its private text in public search or replace the published version.
- Withdrawal makes the public card unavailable; backup restoration preserves publication history and revokes old sessions.
- The personal workspace remains usable when the community server is unavailable.

The MIT software license and contributor card permissions are separate. State the operator's contact route and data retention policy before accepting public contributors.
