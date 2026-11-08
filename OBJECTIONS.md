# Private research objections

[Türkçe](OBJECTIONS.tr.md)

This optional HTTP/store foundation attaches a reasoned objection to an exact published research ID and version. It is disabled by default. Enabling it does not supply a completed human submission/review interface, model workflow or production acceptance. The caller must implement human preview and explicit permission before private delivery.

## Isolated setup

Inspect the available flags without starting the service:

    python -X utf8 topluluk.py serve --help

For an explicitly chosen loopback pilot:

    python -X utf8 topluluk.py --db data/community.sqlite3 serve --public-url http://127.0.0.1:8782 --local-pilot --feed-db data/feed.sqlite3 --objections-db private-objections/objections.sqlite3

Omitting --objections-db leaves the routes and store disabled. Use a dedicated private directory outside the web root, separate from the community, feed and source-memory directories. Only this DB and its SQLite sidecars may occupy that directory; keep backups and logs elsewhere. Symlinks, junctions and database collisions are rejected. The DB is created lazily when first used.

POSIX 0700/0600 or a protected Windows DACL restrict access to the operator/owner and, on Windows, SYSTEM and administrators. This is not encryption or protection from an authorized machine administrator. Copied backups need their own permissions.

## Client contract and private receipt

Before delivery, show the exact public claim, reason, source URLs and target report version to the person. The consent=true field is a payload condition, not proof that a model obtained human approval. There is no MCP or server-client exemption from the browser session controls.

The integrating client must generate and retain a random 43-character URL-safe receipt equivalent to secrets.token_urlsafe(32). Whoever holds it can read or withdraw that objection. Keep it in suitable private local storage, never in a URL, query, body, HTML, telemetry or logs. The server stores its hash and never returns the receipt. Do not print it as part of an example request.

Create the anonymous cookie and csrf_token with POST /api/community/session, an empty JSON object and the exact service Origin. GET only reads an existing session; it does not create one. Submit, private read and withdrawal require that cookie, the exact service Origin, X-Ufuk-CSRF, application/json, a valid Content-Length and exactly one X-DevTrellis-Objection-Receipt header. No account is needed for these receipt-controlled operations.

| Method and path under /api/community/objections | Request / access |
|---|---|
| POST at the base path | research_id, version, claim, reason, consent=true; optional sources |
| POST /read | Empty JSON object; receipt and session controls |
| POST /withdraw | expected_revision from the current objection; same controls |
| GET /review | Current authenticated moderator; optional status, offset, limit |
| GET /review/{objection_id} | Current authenticated moderator |
| POST /review/{objection_id}/decision | Moderator, same-origin session/CSRF, exact objection revision and report binding |
| GET /corrections/{research_id}/{version} | Public approved correction trace; optional offset/limit |

IDs are 32 lowercase hexadecimal characters and versions are positive integers. The target must be an accessible published research version. Claim is at most 800 characters, reason 2,000; up to three public HTTPS sources with title/url are allowed. Submission body limit is 16 KiB. Extra/private context fields are rejected. Pattern checks do not guarantee that arbitrary text contains no personal information.

A new submission returns 201 and pending; exact redelivery using the same receipt returns 200 and duplicate=true. A changed payload or report version under the same receipt is rejected. Withdrawal requires the latest objection revision; a stale one returns 409. The receipt is separate from the objection ID and its revision.

## Moderation and public correction trace

Only the current authenticated moderator can inspect private text or decide: pending → in_review/rejected, then in_review → accepted/rejected. Decision JSON binds expected_revision, research_id and version.

Acceptance also requires public_summary (at most 600 characters) and correction={research_id, version}. The correction must already be published: a newer version of the same report, or a research report whose lineage names the exact challenged report/version. Acceptance records the trace; it does not publish or rewrite research.

Public card reads include up to three public_corrections when this feature is enabled; the corrections endpoint paginates the remainder. Only the approved summary, correction link, review date and withdrawal marker are public. Private claim/reason/source text and the receipt hash are not returned there. Withdrawing later removes the private text but preserves the approved correction trace with withdrawn=true.

## Retention, limits and recovery gate

Private claim, reason and sources are purged on the first store operation after 90 days from initial submission; retries do not extend that deadline. Pending/in_review records become expired. Receipt/payload hashes, status, moderation audit and approved public traces remain. There is no background purge timer. Withdrawal purges private text immediately.

Submission has address-based in-memory limits (4/minute and 20/day), plus persistent queue limits: 5,000 total records, 200 open, 100 submissions/day and 10 per report version/day. Other service limits also apply. The objection store records no IP, but rate limiting uses the connection address temporarily. CLI access logging is disabled; configure reverse proxies not to log bodies, cookies, CSRF or receipt headers.

The existing community backup/restore command does not include this DB. Before public enablement, define separate protected backup copies, their retention and a demonstrated restore into a new isolated directory. Verify ownership/permissions, a retained receipt's read/withdraw path, expired-text cleanup, moderator access and correction links against the matching community snapshot. Do not test restoration over live data. Active-DB cleanup does not delete old backups; do not claim complete erasure while copies remain.

Implementation: radar/arastirma_itiraz_api.py and radar/arastirma_itirazlari.py. Offline contract tests: tests/test_arastirma_itiraz_api.py and tests/test_arastirma_itirazlari.py. Real human UI acceptance, host configuration and separate restore remain deployment gates.
