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
