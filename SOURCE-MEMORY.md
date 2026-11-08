# On-demand source memory pilot

## Coordinated client/server upgrade

For the U1 session change, update the community server and every source-memory client from the same tested release before re-enabling checks. Installed r5 clients call `GET /api/community/session`; the newer server leaves that read cookie-free and may return `csrf_token: null`. The newer client uses an explicit `POST` with JSON `{}` and the service `Origin`, then sends the returned CSRF value on the check. A skill-only update is insufficient: the local application or MCP bridge must load the new client module.

1. Keep source checks disabled during the maintenance window. Record the old server/client releases, preserve their configuration and create the accepted backup.
2. Install the same accepted package on the server and affected local clients. Preserve the previous packages and databases; no automatic reinstall is performed by this document.
3. Restart the affected application and MCP host processes so they load the updated client. Confirm their paths/versions; do not assume a running old process changed with a file copy.
4. Verify cookie-free GET, explicit POST session creation and an authorized source check against that server. Test the actual proxy/HTTPS configuration too. Read-only browsing is not evidence that the write-side source check works.
5. Re-enable the feature only after this passes. If it fails, leave checks disabled and restore the coordinated previous application set; do not open a migrated database with unsupported older code.

The installed r5 package is unchanged by the preparation. The real deployment and host acceptance remain separate steps.

[Türkçe](SOURCE-MEMORY.tr.md)

This optional alpha pilot stores a checked representation of a small, operator-selected public page list. It is separate from the research library and disabled unless the operator supplies both configuration and a separate database. It does not automatically run a crawler, model or timer. Existing research search and installed assistant tools are unchanged.

## Enable an isolated pilot

Review the exact public URLs and their permission to be fetched before enabling them. Hosts must already be in the application's approved source catalogue. Authenticated/personalized pages, identity parameters, redirects and truncated articles are excluded. Current support is bounded HTML article extraction up to 40,000 characters and a narrowly recognized single-section PostgreSQL DocBook document. Unsupported or ambiguous document structures are rejected; this is not a general web-page extractor. A rejected source remains unavailable; the service does not bypass the publisher's restrictions.

The PostgreSQL adapter applies only to `www.postgresql.org/docs/<numeric-version>/<file>.html`, one direct main section and one main heading. It accepts nonempty, ordered one-term/one-description `dt`/`dd` pairs with simple inline text and direct description paragraphs; empty, malformed, nested or unsupported lists are rejected. Tables, images and unsupported heading levels remain excluded. Extractor `article-v4-docbook-definitions` includes exact pre/code text and definition-pair structure in the content hash, so significant whitespace and term/description changes invalidate older summaries. Generic HTML keeps its existing extraction scope; additional code outside the article can conservatively invalidate a saved result.

Create `source-memory.json` outside the web root:

```json
{
  "schema_version": 1,
  "daily_limit": 100,
  "sources": [
    {"id": "selected-document", "url": "https://www.postgresql.org/docs/18/textsearch-indexes.html", "language": "en"}
  ]
}
```

The URL is an illustrative public catalogue host, not a promise that the page is compatible. Supply up to 20 reviewed exact URLs. Version and language query parameters are retained; private/search/session parameters are rejected. The database must be separate from the community and feed databases.

```powershell
python -X utf8 topluluk.py --db data/pilot-community.sqlite3 serve --public-url http://127.0.0.1:8782 --local-pilot --feed-db data/pilot-feed.sqlite3 --source-memory-config source-memory.json --source-memory-db data/pilot-memory.sqlite3
```

Omitting both memory options leaves the feature disabled. This command starts only the existing loopback community service. Keep its database in the operator's private data directory and include it in the backup plan if the pilot is used beyond a disposable test.

## Read and check

- `GET /api/community/source-memory` lists the selected sources without contacting them.
- `GET /api/community/source-memory/{id}` returns a short saved record without contacting the publisher.
- `POST /api/community/source-memory/{id}/check` accepts only `max_age_seconds` (60-86400, default 3600) and `freshness_required` (boolean, default true). It uses the existing same-origin JSON and anonymous-session CSRF controls; no account is required. It accepts no arbitrary URL, question or private context.

Use the session's CSRF value in `X-Ufuk-CSRF` and the service origin in `Origin`. Existing service and persistent daily request budgets apply. If freshness is unnecessary, the last record is returned without a fetch. Otherwise a recent reusable check is shared across callers; a conditional GET may reuse an unchanged representation. Concurrent requests for one representation share one check. Errors preserve the last successful check and impose a short retry backoff.

`fresh` concerns HTTP/source-check recency, not factual correctness. `claims_verified` stays false. `check_succeeded` and `fresh` are separate: a publisher requiring revalidation may permit a successful check without permitting later reuse. `checked_at` is not a report publication date. A page remaining unchanged does not establish that nothing elsewhere has changed.

## Summaries, privacy and limits

Source bodies and user questions are not stored in this memory. A summary is bound to an exact source revision and content hash. A changed revision invalidates it; a failed check does not make an old summary newly verified. Corrected summaries retain their earlier versions.

There is no public summary-write endpoint, automatically enabled model summary or import of users' browsing history in this pilot. Optional MCP tools now connect the existing read/check API. The internal summary-write method is reserved for a trusted future integration after actual public-content review and consent; a boolean from a model is not such consent. Existing research contribution still uses its human preview and moderation flow.

`checks` counts source-check attempts. `downloaded_bytes` counts target response bodies that were received; it is not total network traffic (robots requests, headers and interrupted transfers are excluded). `model_calls` is zero for the read/check mechanism. These counters do not establish end-to-end token savings. Extend the pilot only after measuring source compatibility, correct invalidation and real reuse.

## Optional MCP access

The pilot remains disabled on the server unless its separate configuration and DB
are supplied. For a compatible local adapter, add `--source-memory-tools` to the
existing MCP launch arguments to expose two additional tools. This flag does not
enable the remote pilot or start background checks. Leave it off for the smaller
default six-tool interface.

- `read_source_memory()` lists at most 20 reviewed source definitions; no publisher fetch.
- With `research_id` and `version`, it maps that exact report's source URLs and language
  to the catalog. URL fragments are ignored; query parameters remain exact. This is
  only URL/language mapping, not retrospective proof of the report's content hash.
- With `source_id`, it reads the saved record and only a currently reusable reviewed
  summary. Stale/error summaries are withheld rather than presented as current.
- `check_source_freshness(source_id, max_age_seconds=3600)` performs an explicit
  on-demand check when relevant. It may update shared cache metadata and fetch a
  publisher; no model call, report publishing, usage event or arbitrary URL is involved.

The fixed public-service client uses a temporary anonymous session and the existing
CSRF controls for checks. It never imports the user's browser cookie or credentials.
Redirects, compressed/unbounded responses and unexpected schemas are rejected. A
disabled pilot or failure returns an error without retrying. Local session capability
metadata describes adapter support; `remote_enabled="unknown"` is not an uptime claim.

## Optional private summary preparation

The source package includes KaynakOzetiHazirlayici in radar/kaynak_ozeti_hazirlama.py. This is a Python integration API, with a caller-supplied trusted model adapter; there is no preparation CLI, HTTP route or MCP tool. Enabling the read/check pilot does not enable model generation. Construction performs no fetch or model call.

The caller supplies an existing KaynakHafizasi instance, an explicit jobs database in its own empty or previously owned private directory, and a model callable. Source and jobs databases must differ. A minimal integration call, after the application has configured memory and adapter, is:

    from radar.kaynak_ozeti_hazirlama import KaynakOzetiHazirlayici

    record = memory.oku("selected-document")
    preparer = KaynakOzetiHazirlayici(
        memory, "private-summary/jobs.sqlite3", adapter, daily_limit=10
    )
    draft = preparer.prepare(
        "selected-document",
        expected_revision=record["source_revision"],
        expected_hash=record["content_hash"],
    )

The selected source must already have a checked revision and hash; an unknown record cannot supply these values. The first preparation performs one bounded source-check operation requiring a complete HTTP 200 representation with the same hash, then one adapter invocation. Publisher policy, extraction, source budgets and concurrency limits still apply; robots checks can add network requests. Source text stays in memory and is not stored in the jobs DB.

The adapter receives only the fixed system instruction, untrusted public document, exact source binding, requested summary fields, time limit and output-token limit. It returns summary, provenance with provider/model, and optional input/output token counts. It must enforce the supplied limits and must not add tools, private chat context or credentials to the prompt. The preparer cannot forcibly stop an arbitrary synchronous callable; an expired result is rejected and the attempt still consumes the daily budget. Provider retention and actual credential handling remain the integrating operator's responsibility.

Successful output has draft_status=private_pending, human_review_required=true, and publication_performed=false. Both summary_reusable and public_summary_reusable are false. It does not write the public summary table or approve a contribution. Provider/model identity is adapter_reported, not independently authenticated. Missing token usage remains unknown; character counts are not converted to tokens.

A still-valid identical draft with the same source revision/hash is reused while the stored source is fresh and error-free: the repeat performs zero publisher fetches and zero model calls. Its current-call metrics are zero; generation_metrics retain the original reported usage. Reuse does not turn the draft into a reviewed public summary.

There is no mandatory second GET after the model finishes. The final check compares the stored source identity, hash, freshness and error state. A publisher change that has not reached that database during generation can therefore remain unseen. Human review, a publication-time source check and the public publishing integration remain separate, unfinished acceptance gates; this document does not establish real-provider or host acceptance.

### Private job storage and recovery

- Ready drafts expire after seven days; failed records after one day. Reusing a draft does not extend its deadline.
- Expired ready, failed and running records are removed in the next preparation's claim transaction. Active leases are preserved. There is no background cleanup timer.
- Storage is capped at 200 job records. The default daily attempt budget is 10 (configurable 1–100), with at most two active jobs and a default 180-second lease (60–600). A full queue returns busy without a fetch/model call.
- The private directory contains only this component's ownership marker, database and SQLite sidecars. POSIX permissions or a protected Windows DACL restrict access; they do not encrypt data or secure copied backups.
- Jobs contain bounded validated drafts and provenance, not source bodies, raw model responses or private questions. Expiry in the active DB does not erase old backups.
- The community backup/restore commands do not cover source-memory or private job databases. Define separate protected copies, retention and a demonstrated restore before operational use. Restore into a separate directory with its ownership marker; confirm source-binding checks and expiry before using restored drafts.

See tests/test_kaynak_ozeti_hazirlama.py for offline adapter examples and failure cases. Those fixtures are not evidence of a real NVIDIA, Claude or Codex model run.
