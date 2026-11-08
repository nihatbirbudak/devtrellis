# DevTrellis 0.1.0-alpha.12 — independently accepted 8 November 2026 (Claude-side); not yet deployed

## Fixes from the alpha.11 paired measurement

The paired measurement on the deployed alpha.11 bridge (7 question pairs, same agent with and without DevTrellis) found that
page fetches fell by 46%, but tokens rose 6%, time 30% and quality was slightly lower. Most of the extra steps came from bridge
defects. This release fixes them. It passed an independent acceptance on 8 November 2026 (Claude-side review; its condition, records without a structured scope hidden when a product is given, is fixed in this entry). It has not been deployed or published yet.

- **`read_research` and `record_research_use` on v4 records (was HTTP 502).** The local application read every report through
  the v1 card endpoint; machine-gate records (source cards, reports, topic summaries) are not there, the community answered 404
  and the local API turned it into 502. On a v1 404 the record is now read from `/api/community/v4/records/{id}/v/{n}` and the
  bridge returns the server's agent view inside the untrusted block (card ref, kind, path, `human_reviewed`, `report_url`; v4
  records have no sections, so only `view="full"`). Usage on a published v4 record answers `recorded: false,
  reason: usage_not_counted_for_v4_records` instead of an error (the server keeps no usage counter for v4 records yet).
- **`expected_product` accepts names as agents write them.** "Python", "PostgreSQL", "Go", "Node.js" are lowercased and mapped
  to the library slug (`cpython`, `postgresql`, `go`, `nodejs`; also `postgres`, `golang`, `k8s` and a few others); remaining
  dots are dropped. Inner spaces are still rejected (a version goes in `expected_version`). When the name was changed, the
  response shows the slug used.
- **Faster MCP start in `--standalone`.** The local review service (FastAPI, Uvicorn, the platform API chain) is built in a
  background thread; `initialize` no longer waits for it, the first tool request does. Warm measurement on the developer PC:
  `initialize` median 2.73 s → 2.04 s; the rest is the MCP SDK import. Under memory pressure the deferred imports were roughly half of the
  14.5 s seen in the measurement.
- **Search card short answer is not cut.** The card view cut the summary at 80 words; it is shown whole (package validation
  already limits it to 600 characters). On a high hit for a v4 record the bridge reads the record once in the same call and shows
  all its claims (at most 6, quotes exact, `not_covered`), because the v4 search summary is the claim texts cut at 600 characters.
- **`coverage.state` is consistent.** The coverage and topics lists are valid for 5 minutes; after expiry the refresh used to run
  in the background, so the first lookup said `stale` and the next `fresh`. The refresh is now waited for (at most 8 s). A list that
  really is stale or missing carries a note that it says nothing about card freshness.
- **Narrow cards no longer look complete.** A found lookup result (and an expanded v4 search card) carries `scope_hint`: a card
  holds selected claims, not the whole page; for all/which questions check `not_covered` or read the page. The contribution
  reference of the skill adds two rules: a claim says no more than its quote (a named function, option or parameter must be in
  the quote; no negation or limit the quote does not state), and list-type topics either include every item or name the missing
  ones in `not_covered`.
- **Gate claim-support rule: measured, not applied.** A network-free rule (code names in the claim missing from the quote; a
  negation in the claim but none in the quote) flagged 7 and 5 of the 42 published claims. Fetching the sources showed every
  flagged code name within 300 characters before its quote (a table row or heading), so most flags were wrong; as a rejection
  rule it would have refused 3 of the 5 published records. The gate stays `denetim-kapisi-v0.2.3`; the rule is documented for
  contributors instead (above).
- Small fixes: on the `machine_gate` path an empty evidence label reads "Not human-reviewed"; `topics` and search tags find
  dotted and two-letter product names ("Node.js" + nodejs.org → `nodejs`, "Go" + pkg.go.dev → `go`; search index version
  `k1-3.5`); the `lookup_source` description says "card or report".
- **Acceptance fixes (conditional acceptance, 8 November 2026).** Address lookup no longer hides a record without a structured
  scope (the two live GitHub reports) when `product` or `version` is given: the match is `unspecified` (card shown, version
  unchecked; contract r2 defines version matching only on structured items). A failed background start writes its reason to
  stderr and a missing module brings back the "Install dependencies" hint. A removed or withdrawn record answers "removed or
  withdrawn" (local 410) instead of HTTP 502. `expected_product` drops dots only for a `.js` suffix ("python3.14" and ".NET" stay
  errors) and keeps a name listed as-is in the topics copy. Records known to be v4 are read from the new local route
  `/api/platform/community/v4-records/{id}` without a v1 request first.

### Upgrade notes

- **Deploy the server and the installed local bridges together.** The 502 fix lives in the bridges (`mcp_topluluk.py`,
  `radar/topluluk_baglantisi.py`) and works against the alpha.11 server. Server files changed only for lookup version matching, `topics` and
  search tags (`radar/katki_v4.py`, `radar/katki_v4_depo.py`); the bridge also needs the new local route in
  `radar/platform_api.py` (an older local app falls back to the card route); the first start rebuilds the derived K1 search index (`k1-3.5`), no data
  migration. Rolling back to alpha.11 code is safe (it rebuilds its own index version).
- The installed MCP command runs Python with `-B`, so project modules are compiled on every start. With this candidate fewer
  modules load before `initialize`; precompiling the release folder (`python -m compileall`) would save a little more.
- Not included: deployment, GitHub push, release tag.

# DevTrellis 0.1.0-alpha.11 — independently accepted and deployed 8 November 2026

## K1 second slice and K2 card budget, merged on alpha.10

Merge of `claude/k1-ikinci-dilim-20261108` and `claude/k2-kart-butcesi-20261108` on the alpha.10 code (`a1832b02`), plus the fixes their independent acceptances asked for. Each branch passed its own acceptance, and the merged candidate passed an independent acceptance on 8 November 2026 (Claude-side review; its high finding, null record fields in the K2 bridge, is fixed in this entry). It was published as the GitHub release `v0.1.0-alpha.11` and deployed to devtrellis.org on 8 November 2026.

- K1 search: published v4 records (research reports, topic summaries, source cards) appear in the public `GET /api/community/cards` list and search with the v1 ranking keys. Items carry `record_type: "v4"`, `kind`, `human_reviewed`, `publication` and a `link` to the public record (`/r/{id}/v/{n}` answers `303` there). `library.published_research_total` includes v4 records; `library.published_v4_total` is listed separately. Withdrawn or removed records leave the index in the same transaction.
- K1 address index for every kind: lookup, coverage and topics list a record under every canonical address it cites (source card → report → topic summary at a shared address). Free-text `applies_to` fills `topics` by inference.
- Comparable ranking across the two stores: the v1 and K1 indexes computed bm25 with their own document counts, so on every coverage/weight tie a v4 record fell behind v1. In the merged ranked list a v4 record's bm25 is now recomputed with the v1 index statistics (same FTS5 formula); v1 keys keep SQLite's value, so the v1 order and K1-off search are unchanged.
- `topics` product rule: a product is a word of the free-text scope that equals the first label of a cited source's registered domain (or a part of a hyphenated label); sub-domain parts such as `blog`, `cloud` or `developer` no longer count. The two live GitHub reports still give `github`.
- A report with a free-text `applies_to` cannot be a lookup candidate next to a card at the same address (the schema needs a version item and none is invented); it stays listed in coverage. Known limit, recorded in contract r2 section 10.
- K1 store journal mode is WAL, like the v1 store: a search read no longer makes a withdrawal or removal wait. After expired private text is purged the WAL is checkpointed with `TRUNCATE`.
- K2 card view: `lookup_source` shows every claim of a card (at most 6, quotes exact). The first paired measurement on alpha.10 found that the 1,500-byte budget kept 1 of 6 claims, so the agent read the page anyway. The budget now shortens only secondary text (summary, title, label, correction note); with more than 6 claims, `omitted_claims` counts them and a one-line `hint` points to `view="full"`. A source URL shared by all shown claims is written once. The two real cards from the measurement are about 3.1 KB and 2.9 KB (alpha.10: about 1.5 KB with one claim). Search cards and candidate rows are unchanged.
- New operator command `topluluk.py k1-reindex --k1-db … --k1-config …` rebuilds the K1 address and search indexes (repair only; every start reconciles them).
- Measured (no network, frozen data): with K1 off the 94 golden and 40 frozen search queries return byte-identical id lists to alpha.10 (first rank 53 of 56, MRR@10 0.946, no wrong report on a hit query). With K1 on and the two live v4 reports beside their withdrawn v1 originals, scoring a v4 report as its v1 original gives the same 53 of 56 and 0.946. The assistant-client golden measurement is unchanged (gate blocks 0 of 56 hits, 53 of 56 first rank, 37 high cards, false high card 1 of 36, trap 0 of 8, unknown-product network 0 of 17).

### Upgrade notes

- **Deploy the server and the installed local bridges together.** Server files: `radar/katki_v4.py`, `radar/katki_v4_depo.py`, `radar/topluluk_depo.py`, `radar/topluluk_api.py`, `radar/arastirma_siralama.py`, static files, `topluluk.py`. Bridges: `mcp_topluluk.py` (K2) and `radar/topluluk_baglantisi.py` (personal application). An older bridge still accepts a v4 search card but shows it as a v1 card (no path or label) and keeps the 1-of-6 card budget.
- First start after the upgrade migrates the K1 store in place: it switches to WAL, builds the address and search indexes (index version `k1-2.5`) and keeps `user_version` 1. alpha.10 code can open the migrated store. Take an encrypted backup with `--k1` before the switch; the backup copy is still a standalone DELETE-mode file and a restored store returns to WAL on first open. The `k1.sqlite3-wal` and `k1.sqlite3-shm` files live in the same private K1 directory.
- **Public mode settings (required since alpha.10; missing from the alpha.10 notes).** Without `--local-pilot` the server refuses to start unless all three are set:
  - `DEVTRELLIS_PROXY_TOKEN`: at least 32 random characters, given through the service's secret store (for example a root-only `EnvironmentFile`), never on the command line or in logs.
  - `DEVTRELLIS_RATE_KEY`: at least 32 random bytes, hex encoded, from the same secret store. Keep it stable and separately recoverable; changing it resets stored request limits.
  - `--rate-db <private dir>/limits.sqlite3`: a separate SQLite file in its own private directory (persistent request limits). Include it in backups with `--rate-limits`.
- **Same-host reverse proxy headers (nginx).** The service accepts the forwarded client address only from a loopback peer that sends the right token. The proxy must send the exact public `Host`, `X-Forwarded-Proto: https`, a single `X-DevTrellis-Client-IP` (the validated client address, for example `$remote_addr` after Cloudflare `real_ip` ranges) and `X-DevTrellis-Proxy-Token` (kept in a root-only include file); it must clear `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Port`, `X-Forwarded-Prefix`, `X-Real-IP`, `Forwarded` and `CF-Connecting-IP`. Uvicorn proxy-header handling stays off. Before switching, test: missing token → 400, wrong token → 403, wrong Host → 403, several client addresses → 403, plain HTTP label → 403. See [Public server](COMMUNITY.md#public-server).
- Not included: deployment, GitHub push, release tag.

# DevTrellis 0.1.0-alpha.10 — independently accepted and deployed 8 November 2026

## Six independently accepted branches merged on the alpha.9-r5 preparation

Merge of `claude/onay-penceresi-20261107`, `claude/arama-duzeltme-20261107`, `claude/k0-ikinci-dilim-20261107`, `claude/k2-istemci-20261107`, `claude/k1-sunucu-20261107` and `claude/codex-isleri-2-20261108` on Codex's last clean tip (`70282ce8`). Each branch passed its own independent acceptance, and the merged tree passed an independent acceptance on 8 November 2026 (Claude-side review; its four low findings are fixed in this entry). It was published as the GitHub release `v0.1.0-alpha.10` and deployed to devtrellis.org on 8 November 2026 (the first activation was rolled back after 57 seconds because the required public mode settings were missing; see the alpha.11 upgrade notes above). The alpha.9-r5 candidate entries below are unchanged.

- Confirmation dialogs: every `window.confirm` in the community pages is replaced by an accessible in-page dialog that also works in embedded browser panes; cancel sends no request.
- Search: one shared normalizer for index and query, explicit field weights, optional `answers` index field, version-aware merges (`wcag 2` ↔ "WCAG 2.2"), linear-time note filter. Index version 5 is rebuilt on open.
- K0 public document tracking (second and third slices): source-family document contracts, fixed access-failure codes, usage signals, RFC 3986 dot-segment canonicalization, 20 s request timeout. Still opt-in; the real-20 gate is not yet passed.
- K1 v4 contribution path, default off (`--k1-config`, `--k1-db`): machine gate `radar/denetim` v0.2.3 as the single source, moderation queue, `auto_publish` off by default, stop switch, per-identity limits, retention, encrypted K1 backup role, source lookup/coverage/topics endpoints. See [operator notes](docs/community-k1.md).
- K2 assistant client: `lookup_source` (address canonicalized locally, only its SHA-256 key is sent), answer cards with server/declared/untrusted separation, coverage gate, shorter instructions; `search_research` defaults to the `card` view (`brief` and `summary` unchanged). The public query rule is one constant (`radar/kamu_sorgu.py`) shared by K0, the gate and the client.
- Merge resolution: a K2 test that expected a `./` path variant to keep a separate key now follows K0's dot-segment rule (same page, same key). The publication copy tool now includes the gate package, its tests, the contract schemas and the K1 operator notes.
- Fix round 1: the personal application and the standalone bridge pass `GET /api/platform/community/{sources/{key},coverage,topics}` through to the community unchanged (bodies, ETag, 304; older communities answer `unsupported_contract`, unreachable ones `temporarily_unavailable`); `DEVTRELLIS_COMMUNITY_URL` selects the community (`https`, or plain `http` on loopback for a pilot). The r2 contract schemas moved into `radar/sema/`. `stopped_by` reports `cli` for the operator stop. The gate's privacy test builds its key-block marker at run time, so it ships in the publication copy.
- [Version feed labels](docs/version-feed.md): opt-in nightly job (`araclar/surum-akisi.py`, default disabled) compares the `applies_to` scope of published v1 reports and K1 v4 records with endoflife.date, deps.dev and PyPI (GitHub only when mapped, anonymous); labels "newer version" / "maybe outdated" / "unknown" with the triggering version and date, at most one request per source per day. Labels live in a separate SQLite file; showing them on pages or in MCP answers is not wired yet. No scheduled task is installed.
- [Public data dump](docs/public-dump.md): `araclar/kamu-dokum.py` writes only visible published records (v1 cards, v4 records, minimal tombstones) to a dated JSON file with a manifest and license note; private candidate text, objections, moderator notes, identity buckets, keys and counters never enter the file (closed field sets, tested). A dump loads into a new empty read-only library store; optional local git commit, no push.
- Not included: deployment, GitHub push and any `auto_publish: true` configuration.

# DevTrellis 0.1.0-alpha.9

## Unreleased preparation after r5 — coordinated upgrade required

The local U1 product passed conditional independent acceptance. This preparation adds local human-test helpers and off-host backup tooling; it has not been deployed. Later backup and reader fixes need acceptance on their exact package.

**Update the community server and source-memory clients together before enabling source checks.** Anonymous sessions are now explicitly created with `POST /api/community/session` (JSON `{}` and the service `Origin`). A read-only `GET` does not create a cookie and can return `csrf_token: null`. Installed r5 clients use the old GET behavior and are not compatible with the new source-check session flow. Preserve old releases and use a maintenance window; update each affected local application/MCP bridge from the same tested release, restart its processes, then verify a source check. Updating the skill text alone does not replace the local client code. See [source-memory upgrade steps](SOURCE-MEMORY.md#coordinated-clientserver-upgrade).

- [Human contribution pilot](docs/r0-human-pilot.md): isolated loopback community and review pages; the real user makes every consent and moderator decision. Normal installed MCP still points to the public community. A pilot test does not establish native Claude/Codex panel acceptance.
- [Cloud backup preparation](docs/community-backup-cloud.md): encrypted copies plus restic transport/rehearsal helpers; default is plan-only. The cloud-safe profile intentionally excludes private support/contribution requests and objection text, so those fields cannot be recovered from that cloud copy. G4 private jobs and model budgets remain outside recovery coverage.
- Production still needs the real proxy/Cloudflare path, off-host recovery, secret backend, retention and human workflow acceptance. No general token-saving claim is made.
- Ranked research search (index version 5): one shared normalizer for index and query (ı/İ folding, combined/spaced technical names such as `ros2`/`ROS 2` without requiring the combined form, `C#`/`.NET`/`snake_case`/versions as single tokens, `karşı` as a connector); explicit field weights title 6, optional `answers` 5, tags 4, summary 3, body 1, `Limits` section 0.5 with bm25 only as tie breaker; model generation notes excluded from the index while `Limits` text stays searchable. The 29-group TR/EN vocabulary is frozen. Existing four-column indexes are rebuilt on open. Measured on the frozen 40-query evaluation set (30/30 unchanged) and a 94-query golden set (first-rank 52 → 53 of 56, no wrong report on a hit query; 5 of the 36 queries that should return nothing still return a lexical result, and the assistant client shows a wrong high-confidence card for 1 of those 36).

## Earlier local r5 candidate — historical scope

- Web research queries now request the same ranked path as MCP. Bounded TR/EN terms and ROS 2 spelling normalization preserve explicit product/release differences; candidate matching is not answer verification.
- MCP defaults to a smaller `brief` search view. Optional outline/section views bind to an exact report version and body hash. Each current read still retrieves one full report over HTTP; section views reduce model context, not that network transfer. Full reads remain the default for selected reports.
- Source-memory tools are opt-in (`--source-memory-tools`); checks use the normal anonymous session and CSRF protections. A separately invoked Python preparer can create a private hash-bound draft with an explicit model adapter. It has no automatic publication or provider setup. See [source memory](SOURCE-MEMORY.md).
- An optional private objection store and API attach reviewed corrections to exact report revisions. Public reads expose only moderator-written correction notices. The feature defaults off and requires a separate private backup/restore plan before enabling it; the human form and operating acceptance remain pending. See [objections](OBJECTIONS.md).
- The skill loads contribution/source-memory detail only when needed. Its installer and portable ZIP include both reference files, verify ownership during upgrades, and preserve user edits.

This candidate has no new live-host cost experiment or production deployment. A frozen eleven-report payload comparison measured smaller JSON candidate responses, not tokens, answer quality or general savings. Source-memory preparation, human consent, moderation and recovery must be accepted on the final package before their production features are enabled.

## Smaller research search responses

- Public and local research search can return summary views without report bodies. Existing API clients retain full packets by default; full and exact-version reads, downloads and contribution validation keep their existing contract.
- MCP requests summaries when the local bridge advertises support and starts with three candidates by default. With older components, full search results are validated and reduced locally. Existing personal data and host settings remain separate from the application release.
- Sources, report dates, model provenance and revision metadata remain available. The local bridge includes a canonical `report_url` for the exact validated revision in search/read results. A report date is not a source freshness check, and a body-only caveat may require opening the full report. The assistant reuses suitable findings and checks only necessary gaps while respecting explicit requests for depth.
- MCP can opt into ranked research search backed by a separate index of currently published revisions. Query coverage, weighted fields and limited TR/EN word normalization improve candidate retrieval without a model call. Partial matches carry their missing terms; lexical coverage is never presented as proof of answer sufficiency. Existing API defaults and older-server fallback remain supported.
- Full MCP report reads now use a read-only GET without counter events, so they do not require a write approval. Optional declared reuse remains a separate write operation. Existing MCP-access counters cover event-reporting clients, not every read.
- An optional, disabled-by-default source-memory pilot shares on-demand checks for a small selected public URL list, binds summaries to source revisions and retains the last successful check on errors. It uses a separate database and makes no model calls. See [pilot limits](SOURCE-MEMORY.md). The hourly worker and periodic crawler remain future work. Smaller responses alone do not establish an end-to-end token saving percentage.
- Previewing an imported report extension now preserves schema-3 lineage, including reports without model provenance. Eight search error messages now have English translations. A source-revision concurrency test uses deterministic synchronization on Windows.

# DevTrellis 0.1.0-alpha.8

## Community pages and private requests

- English and Turkish About, Guidelines, Contribute and Support pages are linked from the community home. Reading and research contribution remain account-free; the volunteer application is optional and separate.
- Visitors can preview a support or volunteer message locally before explicitly sending it. Optional contact fields are private. Language changes preserve drafts; uncertain delivery retries keep the same message and request identifier.
- Private requests have a moderator-only inbox and operator CLI. They never become public research cards. Role checks, session-bound CSRF, body limits, rate limits and atomic capacity checks protect intake. Malformed-message validation errors do not echo private text.
- Requests expire after 90 days from initial creation and are removed on subsequent valid request-store operations. Backups rotate separately. No automatic email, guaranteed reply time or complete anonymity is promised.

# DevTrellis 0.1.0-alpha.7

## Search improvements from diverse-topic trials

- Turkish natural queries retain their original terms while recognizing a bounded set of possessive/plural and apostrophe suffixes. Selected connecting words no longer block a query that has enough meaningful terms.
- This remains lexical retrieval. It does not promise general translation, semantic matching, accurate model answers or measured token savings. Unknown terms and negative controls remain meaningful.
- The research trial covers data analysis, accessibility, Python, robotics and extending release-monitoring research. Reviewed public reports include primary sources, model provenance and Turkish summaries; source review remains necessary even when model output is well formed.

# DevTrellis 0.1.0-alpha.6

## Research that builds on earlier work

- Contributions can cite up to five exact public report versions and describe material new findings. The public reader and contribution preview show these relationships; earlier reports remain intact. Hidden and withdrawn revisions stay unavailable even after a later revision is published.
- Web openings, MCP accesses, helpfulness, declared reuse, outdated-information reports and disputes have separate revision-scoped counters. These anonymous signals are approximate, rate limited and not reliability scores or unique-person counts.
- MCP offers exact-version reads, optional reuse reporting and a direct local submission-history link. Local history now paginates beyond 100 records. The skill offers extensions for meaningful new findings and avoids offers for unchanged paraphrases.
- A small explicit Turkish/English technical dictionary expands selected search terms. General semantic retrieval and measured token savings remain unproven.
- Server operators can publish reviewed public report files using a SHA256-bound local command. It creates no enabled login and does not change the normal contribution-review controls. Editorial publication is not an independent accuracy certification.

## Acceptance-test fixes

- Community review and personal lists now search and paginate on the server, including records beyond the first 100.
- A delivery whose response was lost can be recovered after a session restart with **Refresh status**, using its existing OS-vault management key. The check reads status without uploading the report again. This requires the new community inbox lookup endpoint on the server.
- Delayed status responses cannot overwrite newer withdrawal state or attach a different remote submission. Withdrawal versions are validated before any network or vault access.
- Local history offers status refresh for unconfirmed deliveries and stops showing a loading message after an initial load failure. English and Turkish guidance explains recovery and the limits of lexical search.

Upgrade the community server and local MCP installation together for schema-3 development links and counters. Existing schema-1/2 reports remain readable. The full three-provider comparison depends on each user's provider access; controlled tests are not proof of real-world token savings.

# DevTrellis 0.1.0-alpha.5

## Fixes from a real research pilot

- MCP contribution inputs now advertise the accepted category, research engine, language and content-license values. Packets are checked locally before the HTTP call, so a descriptive topic no longer leads to an unexplained HTTP 422.
- The review panel links to **My submissions**, available in standalone mode. It shows the latest 100 local delivery records, offers an explicit status refresh, links to published reports and supports confirmed withdrawal. Loading the page does not contact the community.
- English and Turkish instructions explain category codes and submission management. Anonymous attribution and the existing content-review controls are preserved.

## Validation of alpha.5

A separate research task completed three public-source reports. Six community searches returned no reusable reports. The first preview attempts exposed the input-schema mismatch; using accepted codes produced all three previews. No real report was submitted during this pilot stage; human review and initial moderator setup remained pending.

The clean export passed 52 focused Python tests, including the real MCP SDK and standalone lifecycle, and all 177 JavaScript tests. Browser checks with a synthetic transport covered English/Turkish history, explicit status refresh, the published-report link and the withdrawal confirmation prompt. Withdrawal execution was verified by automated tests; the browser automation was interrupted at its native confirmation dialog.

The list currently exposes the latest 100 local submissions. The community moderator interface also needs pagination beyond its first 100 review items; these limits do not affect the three-report pilot. Reuse and token savings cannot yet be measured without a published report.

## Previous release: 0.1.0-alpha.4

## Private contribution review and assistant skills

- Claude Code and Codex skill packages search existing public research before new work, then offer a public-source contribution preview. The installer preserves unrelated skills and refuses to overwrite edited installations.
- MCP Apps hosts receive Share / Keep local controls with the exact report, sources, model, destination and license. Other hosts receive a local review link. Model tools cannot submit with a boolean confirmation; the app-only decision requires a preview capability outside model-visible content and two explicit review confirmations.
- Contribution attribution is fixed anonymous. Private question/claim metadata is excluded. Common credentials, contact details, IPs and local paths are filtered; HTTPS source links and outbound search queries receive privacy checks. The remote anonymous inbox rejects unfiltered copies before storage.
- These controls reduce accidental disclosure; they cannot detect every private fact or hide network metadata. Host compliance and the user's review remain necessary. No model calls, automatic publication or shared provider quota are introduced.

## Validation of alpha.4

The clean export passed 58 focused Python tests covering privacy, consent, concurrent submissions, standalone lifecycle, the real MCP SDK and skill installation; all 33 community regression tests and 172 JavaScript tests passed. The broader local Python run discovered 721 tests with 11 optional skips and identified one old fixture using a non-anonymous author; that fixture was corrected and the entire 33-test community module passed again. Nine standalone lifecycle tests were added after that broader run.

Browser checks covered the actual English/Turkish local review page, initially unchecked permissions, Keep local with zero delivery requests, and a synthetic submission through an isolated fake transport. The installed Claude Code connection reported connected; Codex registration and the standard MCP protocol were checked separately. Inline controls were tested through the MCP Apps protocol and UI harness, not by manually operating every supported assistant. No real community contribution or paid model call was made.

## Previous release: 0.1.0-alpha.3

## Research first, optional contribution

- Find and read existing community research from the personal research form before starting a model job. Importing a suitable report does not call a model; a new request can use it as explicitly untrusted background.
- Completed reports offer **Keep local** or **Preview and share**. Sharing sends an exact, approved snapshot to moderation without a community account or manual file transfer. Original research stays local. An anonymous author is the default; models, sources and dates remain visible.
- Common credential/email/path patterns are redacted before preview. Report, question and claim can be reviewed and edited; private context and other records are excluded. This is not a guarantee that every personal detail is detected.
- Per-submission management keys live in the OS credential vault. Retry after a lost response reuses the same submission; status and withdrawal are available in the personal workspace. Public intake has bounded payloads, request limits and persistent queue limits.
- Optional MCP tools let compatible assistants search, read, preview and submit after explicit user approval. No model runs inside the MCP server. The SDK installs separately; see [MCP setup](MCP.md).
- Public navigation now explains how to research and contribute. Moderator sign-in and legacy file transfer are secondary options. EN/TR UI and existing personal/community data remain supported.

Reuse avoids another model job when the saved report is sufficient; token savings depend on the host and task. Published research and model names do not certify factual accuracy. Automated hourly research, semantic deduplication, automatic moderation and shared model training are not included.

## Validation of alpha.3

The Python regression run passed 681 tests, with 10 optional tests skipped (691 discovered). Two additional privacy tests passed after that run. All 157 JavaScript tests and all 9 optional MCP tests with the real SDK passed. Tests use isolated data and synthetic model results; no paid model or live contribution was used.

Browser checks covered an anonymous preview, keeping a report local with no outbound request, explicit submission to a local review queue, and importing a published report without a model call. Editing the question and removing a claim preserve the original local research; the submitted packet excludes private context and raw transcripts. These checks do not establish model accuracy or complete detection of personal information.

## Previous release: 0.1.0-alpha.2

Prepared 2026-11-04. Adds NVIDIA source-based research, model provenance and social/news claim context.

## Public alpha preview

The MIT source is published at [nihatbirbudak/devtrellis](https://github.com/nihatbirbudak/devtrellis), with an English/Turkish community preview at [devtrellis.org](https://devtrellis.org). Reading and search are public; contributor accounts remain invitation-only.

On 2026-11-04, the isolated Ubuntu host passed dependency checks, 21 community tests, service restart, consistent database backup and restoration to a new file. External HTTPS checks passed for certificate validation, secure cookies, Host/Origin boundaries, private-data exclusion, body size limits and HTTP redirection. The initial bounded collection checked 12 of 61 configured sources successfully and stored 257 records; 49 sources were not yet checked. These counts do not establish complete coverage or factual accuracy.

Daily database backups and certificate renewal are configured. A certificate renewal dry run succeeded. Automatic model research is not running; production-compatible provider access and the separate research worker remain pending. The hosted preview has no external adoption evidence yet.

The release also fixes Windows CI comparisons of short and canonical temporary-directory paths, localizes topic badges in both feed interfaces, and removes the ambiguous archive badge from the public feed while preserving personal archive behavior. Dates remain visible.

- NVIDIA can be selected for research as well as summaries/screening. All its calls share the local NVIDIA budget; quota/auth failures do not switch providers. Response model identity and report generation time are saved with the result.
- English/Turkish guide includes free NVIDIA prototype access, account/key steps and provider terms. A hidden-input helper uses Windows Credential Manager or a temporary process environment.
- Research accepts a public HTTPS URL, a claim and optional description/transcript. Bounded HTML access respects robots, DNS/IP checks, TLS, redirects and size/time limits. A post is untrusted context; independent sources remain necessary. Video/audio are not analyzed. Discovery remains limited to the developer catalogue and academic indexes.
- Reports and shared cards show saved model/date/evidence metadata. A separate request can re-research the question without overwriting the original. A completed report can become a local sharing draft; content edits invalidate its prior evidence status.
- Version 2 sharing packets carry provenance. Existing version 1 packets and personal records remain compatible. Imported provenance is author-supplied metadata, not a signed identity or factual guarantee.

## Validation of alpha.2

The clean exported Python run discovered **661 tests**: 651 passed, 9 opt-in Windows process tests were skipped and one assertion still expected the old report heading (346.620 seconds). That assertion now checks the saved model and generation time; all 12 workflow API tests passed on a fresh export. After the final claim-metadata change, all 58 sharing/platform/community tests passed. This validates **652 distinct Python tests** across the full run and focused retests; the Windows process tests were not repeated for alpha.2. **142 JavaScript tests passed**.

Browser checks covered NVIDIA settings, report metadata, sharing draft creation/editing, a separate re-research request, restricted social-link feedback and both guide/interface languages. Model responses and social-link failures in those checks were synthetic fixtures. A separate real-network read of NVIDIA's public API quickstart returned HTTP 200 and visible HTML text. These checks do not measure model accuracy.

No real NVIDIA model request was made: the application's own credential lookup reported no accessible key. Social login/video processing is outside the tested scope. The local acceptance below preceded the public host checks recorded above. Automated checks cover provider/budget errors, untrusted link context, request/cache separation, safe fetches, schema migration and packet compatibility. Raw user-supplied descriptions/transcripts are not copied into sharing metadata; the question, claim and source URL are visible in the draft for review.

## Previous alpha.1 acceptance

Prepared 2026-11-04. This is the first bilingual community pilot candidate, previously named UFUK.

## Included

- MIT software license, English main documentation and Turkish setup/use guide.
- English/Turkish personal, advanced and community interfaces.
- Separate moderated community service: invited contributors, anonymous reading/search, versioned revisions, withdrawal, session revocation and backup/restore.
- Optional Codex CLI adapter using the person's existing ChatGPT session. It analyzes supplied sources with tools disabled. Existing NVIDIA and Claude integrations remain available.
- Central topic registry with eight topics; existing records and topic identifiers migrate without resetting personal data.
- Four official source feeds added; accurate attempted/success/failed source counters.
- Isolated Python setup, Windows launchers and privacy-filtered source export.

## Validation

The exported candidate passed **607 Python tests** in the standard run (616 discovered, 9 opt-in Windows tests skipped). Those **9 Windows process tests passed separately** in 145.661 seconds. **134 JavaScript tests passed**. The full Python run took 436.187 seconds. Synthetic fixtures and isolated data were used; these counts are not production usage evidence.

| Check | Observed result |
| --- | --- |
| Clean setup | Windows, Python 3.12.14; `kur.py` created a new virtual environment, installed pinned dependencies and passed `pip check` |
| Windows launcher | Nine real tests passed: start/reuse, concurrent launches, process identity protection, occupied port, failed setup cleanup, virtual environment child shutdown, backup and restart |
| Browser workflow | Card creation, export preview/download, contributor submission, separate moderator publication, anonymous download and local preview/import passed |
| Languages | EN/TR navigation, advanced workspace and research dialog checked; source/user text retained |
| Community recovery | CLI backup and restore into a new database passed; integrity checked and prior sessions revoked |
| Real sources | Python 50 items, GitHub Changelog 10, PostgreSQL 10, Google Security 20 during bounded checks; complete historical coverage is not implied |
| Real Codex probe | CLI 0.155.0-alpha.16.4 with existing ChatGPT login and configured model; one successful synthetic evidence-check response after fixing two early parser failures; no tools or provider fallback |
| Claude/NVIDIA live calls | Not repeated during this release; adapters and error paths covered by simulated tests |

A feed containing HTML DOCTYPE text inside XML CDATA exposed a parser false positive; XML declarations are now checked structurally before parsing. Actual DTD/entity declarations are still rejected. A browser download issue was corrected with a version-checked attachment endpoint. A Codex nonfatal SDK notification was incorrectly treated as a tool request and is now classified correctly.

## Pilot limitations

- The selected host, domain, TLS proxy, service restart and restoration to a new local file were validated. Backups currently remain on that host; an off-host backup destination and recovery exercise are pending.
- Community mode supports a small moderated pilot with one application process. There is no self-service registration, account email recovery or large-scale operating evidence.
- The local app and community exchange files explicitly. Automatic server synchronization and semantic research deduplication are future work.
- Codex's live probe validates the connection and one evidence check; it is not an evaluation of full research quality. Provider quotas and availability remain the user's responsibility.
- Interface translation does not translate source articles or contributor content. Report language is a separate choice.
- There is no public adoption evidence yet. Source counts describe the configured catalogue, not global completeness.
- Tests emit an upstream Starlette TestClient deprecation warning; dependency checks still pass.

Before updating an existing installation, take a consistent backup. Preserve the original database and previous release. Follow [README](README.md), [community operations](COMMUNITY.md) and [security guidance](SECURITY.md).
