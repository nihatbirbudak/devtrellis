# DevTrellis 0.1.0-alpha.2

Prepared 2026-11-04. Adds NVIDIA source-based research, model provenance and social/news claim context.

- NVIDIA can be selected for research as well as summaries/screening. All its calls share the local NVIDIA budget; quota/auth failures do not switch providers. Response model identity and report generation time are saved with the result.
- English/Turkish guide includes free NVIDIA prototype access, account/key steps and provider terms. A hidden-input helper uses Windows Credential Manager or a temporary process environment.
- Research accepts a public HTTPS URL, a claim and optional description/transcript. Bounded HTML access respects robots, DNS/IP checks, TLS, redirects and size/time limits. A post is untrusted context; independent sources remain necessary. Video/audio are not analyzed. Discovery remains limited to the developer catalogue and academic indexes.
- Reports and shared cards show saved model/date/evidence metadata. A separate request can re-research the question without overwriting the original. A completed report can become a local sharing draft; content edits invalidate its prior evidence status.
- Version 2 sharing packets carry provenance. Existing version 1 packets and personal records remain compatible. Imported provenance is author-supplied metadata, not a signed identity or factual guarantee.

## Validation of alpha.2

The clean exported Python run discovered **661 tests**: 651 passed, 9 opt-in Windows process tests were skipped and one assertion still expected the old report heading (346.620 seconds). That assertion now checks the saved model and generation time; all 12 workflow API tests passed on a fresh export. After the final claim-metadata change, all 58 sharing/platform/community tests passed. This validates **652 distinct Python tests** across the full run and focused retests; the Windows process tests were not repeated for alpha.2. **142 JavaScript tests passed**.

Browser checks covered NVIDIA settings, report metadata, sharing draft creation/editing, a separate re-research request, restricted social-link feedback and both guide/interface languages. Model responses and social-link failures in those checks were synthetic fixtures. A separate real-network read of NVIDIA's public API quickstart returned HTTP 200 and visible HTML text. These checks do not measure model accuracy.

No real NVIDIA model request was made: the application's own credential lookup reported no accessible key. Social login/video processing and a production server deployment are outside the tested scope. Automated checks cover provider/budget errors, untrusted link context, request/cache separation, safe fetches, schema migration and packet compatibility. Raw user-supplied descriptions/transcripts are not copied into sharing metadata; the question, claim and source URL are visible in the draft for review.

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

## Limits before public operation

- The real server, operating system, domain, TLS proxy, service restart and off-host restoration have not been validated. Windows localhost tests do not establish production readiness for another host.
- Community mode supports a small moderated pilot with one application process. There is no self-service registration, account email recovery or large-scale operating evidence.
- The local app and community exchange files explicitly. Automatic server synchronization and semantic research deduplication are future work.
- Codex's live probe validates the connection and one evidence check; it is not an evaluation of full research quality. Provider quotas and availability remain the user's responsibility.
- Interface translation does not translate source articles or contributor content. Report language is a separate choice.
- There is no public adoption evidence yet. Source counts describe the configured catalogue, not global completeness.
- Tests emit an upstream Starlette TestClient deprecation warning; dependency checks still pass.

Before updating an existing installation, take a consistent backup. Preserve the original database and previous release. Follow [README](README.md), [community operations](COMMUNITY.md) and [security guidance](SECURITY.md).
