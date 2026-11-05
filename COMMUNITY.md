# Operate a DevTrellis community

[Personal setup](README.md) · [Türkçe kullanım](README.tr.md) · [MCP tools](MCP.md) · [MCP Türkçe](MCP.tr.md)

The community is a separate process and database. Its main flow is **find existing research, reuse useful findings, then optionally contribute new research anonymously**. Public search, reading and reuse need no account or model call. Research happens in the personal application or the user's AI tool, with their own provider access. Only an explicitly approved contribution is sent to the community for moderation.

The service does not store model credentials, generate research or review submissions automatically. It supports **one application process** with SQLite and a small moderated pilot. An operator-created moderator account manages the normal review queue; reader and anonymous contributor accounts are not required. Invited accounts and manual file exchange remain optional paths.

## Public pages and private requests

These community pages offer English and Turkish without an account:

| Page | Purpose |
| --- | --- |
| [About](https://devtrellis.org/about) | The community's purpose, current scope and limitations. |
| [Guidelines](https://devtrellis.org/guidelines) | Sources, corrections, attribution and respectful participation. |
| [Volunteer application](https://devtrellis.org/contribute) | A private introduction for ongoing help with research, code, translation, design or sources. |
| [Support](https://devtrellis.org/support) | Setup guidance and a private route for a question, bug, content concern or privacy concern. |

Applying is not required to read, research or share a research report. Volunteer applications and support messages enter a separate private request inbox; they are not research submissions, do not appear in public search and grant no research-content license. The same page paths work on a local community installation.

The form first shows a local preview. Only **Send to operators** sends the reviewed details. Name and email are optional; an email address is needed if the sender wants a reply. The service confirms receipt on screen, but sends no automatic email and promises no response time or service-level agreement. Operators review messages manually. A lost or uncertain delivery response keeps the same message and retry identity to avoid duplicate records.

Do not ask senders for credentials, private conversations, full logs or private files. Account-free submission does not hide connection metadata from hosting infrastructure. The [private request operation](#private-request-operation) section describes access, retention and capacity.

## Reviewed editorial reports

An operator with direct server filesystem access can publish a source-checked public research file using `topluluk.py --db PATH publish-reviewed REPORT.json --sha256 REVIEWED_FILE_SHA256 --note "Review note"`. Review the exact file and its sources before running it. A mismatched hash or a packet requiring privacy redaction is rejected. The command uses disabled internal contributor/reviewer records for its two recorded stages; these cannot sign in and do not represent two independent human reviewers. Repeating the same file is idempotent, and a withdrawn or hidden report is not automatically republished. This local command is not exposed as an HTTP or model tool and does not automate the user's contribution controls.

Schema-3 research contributions preserve parent report identifiers, exact public versions and a public change summary. Older hidden revisions remain inaccessible after a later version is published. Approximate version-scoped engagement counters use separate web/MCP access and declared-feedback categories; see [the measurement and privacy boundaries](MCP.md). Update the local MCP client and community service together for these features.

## Reader and contributor flow

1. Search published research from the personal application or the [local MCP tools](MCP.md). Search terms go to the community, so use a short query without private information. Query words are normalized and matched together regardless of order; this is text search, not semantic matching.
2. Read the report's sources, dates, model and limitations. Import a suitable report without calling a model. A published report is not a guarantee of truth or current evidence; investigate gaps when necessary. Reuse can reduce repeated work, but does not guarantee token savings.
3. Complete new research locally. Choose the optional community action, review the exact text, sources, model information, destination and **CC BY 4.0** permission used by the direct UI, then explicitly confirm. There is no account or manual JSON transfer in this path. An external report sent through MCP preserves the author's chosen CC BY 4.0 or CC0 1.0 permission in its exact preview; submit only your own report or text you have permission to share.
4. The local application submits that preview to `https://devtrellis.org`. It starts as private `pending` content. A moderator can publish or reject it; the local application can read its status and withdraw it.

The direct UI and MCP contribution path always use **Anonymous contributor**. The original private question is replaced by the public title and claim metadata is removed. Common credential, email, phone, IP and local-path patterns are redacted before preview; source links must pass the public HTTPS URL filter. Sensitive search patterns are blocked before forwarding. These checks cannot recognize every person's name or private fact. Write a separate report from public sources and inspect every field. An imported report cannot be directly republished through this flow with its author's name replaced; preserve source attribution in new work.

The review issues an expiring capability for that frozen preview. The normal preview and model-visible result do not contain it; MCP Apps receives it only in UI metadata. Sharing requires both a public-content review and the displayed license permission. Keeping local invalidates the preview. The anonymous inbox rejects packets that do not already match the privacy filter, rather than silently changing an approved copy. This is an application consent boundary, not proof of human presence against malicious software on the same computer. Network metadata and the research provider remain separate privacy boundaries.

An anonymous contribution has no verified account identity. Moderation is performed by an authenticated moderator, but the service cannot prove that an anonymous author and a reviewer are different people. The rule preventing an account from approving its own account-owned card still applies.

## Anonymous delivery and management

`POST /api/community/inbox` accepts a validated `ufuk-share` packet, `consent: true` and an idempotency key. The local application creates a separate random management capability for the contribution **before** sending it. The capability is sent in the Authorization header with `X-DevTrellis-Inbox: 1`; it is never put in a URL. Status and withdrawal use the same capability at `GET /api/community/inbox/{id}` and `POST /api/community/inbox/{id}/withdraw`.

The local application stores this secret only in a supported operating-system secure vault and verifies the save before delivery. If the vault is unavailable, submission stops; it does not fall back to a plaintext file, database field or log. The server stores only its hash. A local database backup alone does not preserve the vault secret. Keep the OS vault available when moving or recovering an installation; there is no automatic account-based recovery for a lost anonymous management capability.

Repeating the same packet with the same capability returns the existing contribution and current status. A withdrawn contribution is not reopened by retrying delivery. A different packet needs a new preview and capability; anonymous revision editing is not implemented. Copies from different installations are not globally deduplicated or semantically merged, so search before generating or submitting new work. Withdrawal stops public access to the server copy, but cannot recall copies already downloaded.

## Local pilot

Install with `python -X utf8 kur.py`. The examples use Windows; on Unix replace `.venv\Scripts\python.exe` with `.venv/bin/python`.

Create the moderator account in an interactive terminal. Passwords are entered without echo; only salted password hashes are stored. A terminal that cannot hide password input is rejected.

```powershell
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 account-add reviewer --display-name "Pilot reviewer" --role moderator
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 serve --public-url http://127.0.0.1:8782 --local-pilot --port 8782 --feed-db data/community-feed.sqlite3
```

Visit **http://127.0.0.1:8782** and use the moderator sign-in for the review queue. The bundled personal client currently targets `https://devtrellis.org`; starting a local community pilot does not redirect it to localhost. Do not send test content to the public service to exercise a local installation.

An optional invited contributor can be created with `account-add contributor --display-name "Pilot contributor"`. That legacy workflow allows manual JSON submission and versioned corrections. New revisions enter review while the preceding published revision remains available. Accounts associate submissions with a contributor; they do not independently verify factual claims.

## Public server

Use an isolated service account and a stable data directory outside release folders. Do not point this service at a personal database or expose the personal workspace through a reverse proxy.

1. Install dependencies on the selected host and create a moderator account.
2. Bind the community process to its built-in `127.0.0.1` listener. Run exactly one worker.
3. Terminate HTTPS on a reverse proxy on the **same host**. Forward the exact public Host and set the scheme to HTTPS. Replace incoming forwarded headers at the proxy, rather than trusting caller-supplied values. The process trusts proxy headers only from `127.0.0.1`. Forward the actual client address: otherwise all anonymous submissions share a loopback quota. If a CDN is used, accept its real-IP headers only from that CDN's trusted addresses before setting forwarded headers. Do not widen Uvicorn's trusted-proxy setting to arbitrary clients.
4. Start with the final `--public-url https://community.example.org`, without `--local-pilot`. The hostname is an example, not a configured deployment. An incorrect host or plain HTTP in production mode is rejected.
5. Keep private databases, backups and logs outside the web root. Restrict them to the service operator. Configure OS service supervision, restricted logs, external request limits and periodic backups for that host.
6. Verify HTTPS, cookie settings, the actual proxy/client-address chain, restart, source collection and restoration on the real host before announcing it.

The shared site is [devtrellis.org](https://devtrellis.org). A local pilot or simulated HTTPS test does not certify another reverse proxy or operating system. Validate the selected host independently; these scripts do not automatically change ports or router settings.

The public API does not enable cross-origin browser access. Session cookies are HttpOnly and SameSite=Strict, and Secure outside the explicit local pilot. Account-based writes require a matching Origin and CSRF token. The inbox is a separate server-to-server protocol: it rejects Origin, Cookie and Sec-Fetch headers, requires its explicit protocol header and capability, and retains Host/HTTPS validation. It does not relax existing session mutations or fetch submitted URLs. Initial publication is manually reviewed. Moderators can remove published content with a recorded reason; this appears as `hidden` to the contributor.

### Intake limits and operational privacy

- A packet is at most 32 KiB; the complete community mutation body is at most 64 KiB. Content-Length and JSON content type are required. Keep the proxy body limit compatible.
- Anonymous delivery allows 4 requests per IP per minute and 20 per IP per UTC day; retries also count. Status/withdrawal allows 30 requests per IP per minute. Exceeding an IP budget returns HTTP 429.
- At most 200 anonymous cards can be pending and 5,000 anonymous cards can exist in this pilot database. Full capacity returns HTTP 503. Counts are checked atomically; no old contribution is silently deleted to admit a new one.
- IP request budgets are bounded in-memory counters for one process and reset after restart. Queue and total-record limits persist in SQLite. These limits are a pilot safeguard, not a complete abuse-prevention system; monitor backlog and capacity before expanding access.
- Do not log Authorization, Cookie, CSRF values, request bodies or private research queries. The application access log is disabled. Configure the reverse proxy's logging and retention deliberately; account-free contribution does not make network addresses invisible to hosting infrastructure.

## Private request operation

The operator inbox is `/requests` on the community site. Its HTML shell is public; listing, reading and changing private requests require an authenticated **moderator** session. A contributor account cannot access them. The browser form uses an anonymous session, same-origin checks and CSRF protection for delivery; it does not require sign-up. The service does not send these messages to a model or publish them automatically.

Operators with filesystem access to the existing community database can also use the local CLI:

```powershell
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 requests-list --status new --offset 0 --limit 25
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 requests-show REQUEST_ID
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 requests-status REQUEST_ID in_review
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 requests-status REQUEST_ID closed
```

Replace `REQUEST_ID` with the 32-character identifier from the list. Omitting `--status` lists every status; accepted values are `new`, `in_review` and `closed`. `requests-list` includes subjects and status, but excludes message bodies and contact fields. `requests-show` reveals the private message and any supplied name/email. `requests-status` changes only the review state and prints a small receipt; closing a request neither sends a reply nor deletes it. These commands rely on the operator's filesystem permission, not a browser login.

Treat subjects, messages and contact fields as private. Do not copy real requests or CLI output into model prompts, agent mail, public issues or research packets. Review them in the authorized operator interface or terminal. Status changes do not send email; any reply is a separate manual action using the supplied contact address. The submitted address is not verified identity.

### Private request retention and limits

- The first creation time starts a **90-day** retention period. Expired rows are deleted at the next valid private-request operation: submit, list, detail or status change. Browsing public research does not trigger this cleanup. There is no separate scheduled cleanup job, so an idle request service does not promise physical deletion exactly on day 90.
- Retrying the same message or changing its status does not extend retention. After a backup is restored, expired requests are removed before a valid private-request operation returns them. Backups can contain private requests; their access, retention and rotation are separate operator responsibilities. This cleanup does not erase existing backup copies.
- At most **1,000 total requests** and **200 requests in `new` status** can exist. Capacity is checked atomically. New delivery or reopening a request into a full `new` queue returns HTTP 429; no unexpired record is silently deleted to admit a new request. An unchanged retry can return its existing receipt even at capacity.
- Delivery allows **4 requests per IP per minute**, **20 per IP per UTC day**, and **60 requests per minute across the service**. Retries and rejected attempts can consume these budgets. The counters are in memory for the single application process and reset on restart; the database capacity persists.
- The complete delivery body is limited to **16 KiB** and must have JSON content type and a matching Content-Length. Subjects allow 160 characters, messages 4,000, optional names 80 and optional email addresses 254. Browser validation is also enforced by the server. Keep the reverse proxy's client-IP handling and body limits compatible.
- Keep the database, backups and logs outside the web root. Do not log request bodies, contact fields, Cookie or CSRF values. The request records are a separate table in the community database; this is a public-access boundary, not protection from a filesystem-authorized operator.

## Populate developments

The feed store is separate from accounts and community cards. Collect using the personal CLI against the shared feed database:

```powershell
.\.venv\Scripts\python.exe -X utf8 calistir.py topla --db data/community-feed.sqlite3
```

Schedule this command with the host's scheduler after the manual run succeeds. The collector handles a bounded batch and respects configured source intervals and existing request restrictions; one run is not a promise to check every source. Collected source descriptions become readable through the public feed database, separate from the card moderation queue. The community process itself runs no collector, model-summary worker or automatic reviewer. Report source failures and stale material honestly; a successful fetch is not factual verification.

## Backup, restore and account recovery

```powershell
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 backup backups/community-before-update.sqlite3
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community-restored.sqlite3 restore backups/community-before-update.sqlite3
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 password-reset reviewer
.\.venv\Scripts\python.exe -X utf8 topluluk.py --db data/community.sqlite3 account-disable reviewer
```

Backup and restore require a new destination and never overwrite an existing database. Restoration checks database integrity and removes old account sessions. Anonymous contribution records and capability hashes remain in the restored database; the corresponding secrets remain in their contributors' OS vaults. Password reset and account disable revoke that account's sessions. Disabled accounts remain disabled after a password reset; account recovery is an operator decision. Keep an active moderator available before disabling a reviewer.

Back up the separate feed database with `calistir.py yedekle --db data/community-feed.sqlite3`. Before a release update, record the running version and preserve a tested backup. For rollback after a schema change, stop the service, preserve the newer data and restart the older release against its pre-update backup. A database hash or an integrity check alone does not replace a demonstrated restore.

## Pilot acceptance

- An unsigned-in user searches for an existing research report, reads its sources and imports it without a model call or contribution requirement.
- A local research preview is sent only after explicit consent, without an account or file transfer; a moderator publishes it.
- Before review, public listing, search and download cannot reveal the contribution.
- Repeated delivery creates one contribution; an unrelated capability cannot read its private status or withdraw it. Missing secure-vault access prevents delivery.
- An unrelated account cannot edit it; stale updates fail with a conflict.
- A pending correction does not expose its private text in public search or replace the published version.
- Withdrawal or moderator removal makes the public card unavailable; backup restoration preserves publication history and anonymous management while revoking old account sessions.
- Proxy client-IP handling, body limits and queue exhaustion behave as documented on the selected host.
- The personal workspace remains usable when the community server is unavailable.

The software is MIT licensed. The direct UI shares the approved research text under CC BY 4.0 with a fixed anonymous author label. MCP reports and legacy file exchange support a chosen CC BY 4.0 or CC0 1.0 permission, shown before confirmation. Linked sources and software keep their own rights. State the operator's contact route and data retention policy, including rejected/withdrawn submissions and backups, before accepting public contributors.

The [consumer MCP tools](MCP.md) ([Türkçe](MCP.tr.md)) run locally and reuse this same search/preview/consent flow. They do not generate reports or bypass moderation. Website publisher registration, publisher-scoped API/MCP connectors and automated publication are future work described in [the platform plan](PLATFORM.md).
