# Optional source memory

Use only when the connected host exposes `read_source_memory` and
`check_source_freshness`; these pilot tools are disabled by default. Missing support
does not block normal research. Do not install or change host settings to enable it.

Pass the selected report id and exact published version to `read_source_memory` to
map its public source URLs and language, or call without arguments for the small
reviewed catalog. Mapping does not prove an older report used that source hash.
Read a returned `source_id`. Reuse a suitable reviewed summary only when
`summary_reusable=true`; keep its scope, limitations, source version and attribution.
If freshness matters and a current check is absent, `check_source_freshness` accepts
only the catalog id and a bounded age. It may fetch the publisher and update shared
cache metadata; it calls no model and publishes no report. Do not poll or retry
automatically. A disabled pilot, absent mapping, failure or unsuitable summary means
inspect the necessary primary source normally. No arbitrary URL, user query, personal
data or private project context is accepted. Unchanged bytes do not establish truth,
rule out newer releases elsewhere, or satisfy every host verification requirement.
