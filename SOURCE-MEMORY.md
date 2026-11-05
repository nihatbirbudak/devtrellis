# On-demand source memory pilot

[Türkçe](SOURCE-MEMORY.tr.md)

This optional alpha pilot stores a checked representation of a small, operator-selected public page list. It is separate from the research library and disabled unless the operator supplies both configuration and a separate database. It does not run a crawler, model or timer. Existing research search and installed assistant tools are unchanged.

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

There is no public summary-write endpoint, automatic model summary, MCP source-memory tool or automatic import of users' browsing history in this pilot. The internal summary-write method is reserved for a trusted future integration after actual public-content review and consent; a boolean from a model is not such consent. Existing research contribution still uses its human preview and moderation flow.

`checks` counts source-check attempts. `downloaded_bytes` counts target response bodies that were received; it is not total network traffic (robots requests, headers and interrupted transfers are excluded). `model_calls` is zero for this mechanism. These counters do not establish end-to-end token savings. Extend the pilot only after measuring source compatibility, correct invalidation and real reuse.
