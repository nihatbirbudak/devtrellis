# Version feed labels for `applies_to` (nightly, opt in)

This local job compares the product/version scope of published records with public release feeds and stores a label per record and product. It runs no language model, changes no record, publishes nothing and hides nothing. A label says only that a newer version or an end-of-support date exists after the version the record covers; the reader still sees the record, together with the triggering version and its date.

Engineering report item D2 (6 November 2026). The feature is **off by default**: with `"enabled": false` the command reads no store and makes no request.

## Sources and identity

| Source | What is read | Identity used |
| --- | --- | --- |
| [endoflife.date](https://endoflife.date/docs/api/v1/) | product list (once a day) and the product's release cycles: latest version, release date, `isEol`, `eolFrom` | endoflife.date product id (`products.<name>.endoflife`, or the product name when it is in the list) |
| [deps.dev v3](https://docs.deps.dev/api/v3/) | package versions with `publishedAt`; the default version counts as latest | purl in `products.<name>.purl` (`pkg:npm/…`, `pkg:cargo/…`, `pkg:golang/…`, `pkg:maven/group/artifact`, `pkg:nuget/…`) |
| [PyPI JSON](https://docs.pypi.org/api/json/) | `info.version` and its upload time | `products.<name>.purl: "pkg:pypi/…"` only. An unmapped product name is **not** looked up on PyPI even when its scope says `pep440`: a same-named unrelated package would produce a wrong label |
| GitHub releases (optional, default off) | `releases/latest` tag and date; anonymous, read only, no token | `products.<name>.github: "owner/repo"` only |

A product that none of these rules map stays `unknown` ("bilinmiyor"). Product names are matched as database keys, not as natural language.

**Code-named cycles.** Some endoflife.date products name their cycles instead of numbering them (`ros`: `noetic`, `melodic`, …; no `latest.name`). Without a mapping the source answer is "version names are not numeric" and the product stays `unknown`; the job continues with the other products. The optional `products.<name>.cycle_names` maps each cycle name to a numeric version the comparison can use, for example `"ros": {"endoflife": "ros", "cycle_names": {"kinetic": "1.12", "lunar": "1.13", "melodic": "1.14", "noetic": "1.15"}}` (the real ROS 1 distribution numbers). The same mapping resolves a code-named `versions` value in a record's scope (`"versions": "melodic"`). The label then carries `latest_cycle` next to `latest_version`.

## Records and scope

- **v4 records** (K1 store, `v4_records`, visible only): the `applies_to` items of the claims and of `content.applies_to`, in the K1 r2 shape `{product, versions, version_scheme}`.
- **v1 reports** (community store, current public research versions): v1 packets have no structured scope. The job uses the explicit mapping in `v1_reports.<report id>`; without one, it scans only title and summary for a configured **alias** (`products.<name>.aliases`, case-sensitive; the product key itself is not scanned) followed by a dotted version (`Python 3.8`). Range mentions are skipped (`Python 3.8+`, `Python >=3.8`, `Python 3.12.*`); when several versions are mentioned the newest is taken (`Python 2.7 and Python 3.12` → 3.12). Anything else stays `unknown` with reason `no_applies_to`. The scan is a convenience for the few existing reports; the explicit mapping is the reliable path.
- The reference version is the newest numeric version inside `versions` (`3.12.x` and `3.12.*` → 3.12; `>=3.11,<3.13` → 3.11, the excluded upper bound does not count). Comparison uses the reference's precision: `22` compares majors only, `3.12` majors and minors, `3.12.1` also patches.

## Labels

| Label | Turkish | Trigger |
| --- | --- | --- |
| `current` | güncel | no newer version at the reference precision; cycle not end-of-life |
| `newer_version` | yeni sürüm var | newer minor or patch version within the same major (`trigger: new_minor_version` / `new_patch_version`) |
| `maybe_outdated` | eskimiş olabilir | newer major version (`new_major_version`) or the reference cycle is end-of-life on endoflife.date (`support_ended`, with `eol_date`) |
| `unknown` | bilinmiyor | `no_applies_to`, `product_not_mapped`, `source_unavailable` (with `source_status`: HTTP class, `rate_limited`, network error), `source_payload_unparsed` (200 but not the expected shape), `version_names_not_numeric` (code-named cycles without `cycle_names`), `scope_version_unparsed`, `adapter_error` (unexpected exception for that product; `error_type` only) |

Every product label carries `latest_version`, `latest_date`, `source`, `source_url` and `checked_at`; `unknown` labels carry `reason` and a Turkish `reason_tr`. A record's label is the strongest of its products' labels. Labels are hints for readers and agents; they are not a correctness score and never remove or rank a record.

**Error isolation.** Each product is labelled inside its own guard: a source answer that cannot be parsed, or an unexpected exception in one adapter, makes that product `unknown` and the run continues; the run summary counts `adapter_errors`. The CLI prints a one-line message with the exception type instead of a traceback. Labels for all records are written at the end of the run.

## Network rules

HTTPS only, allow-listed hosts only, same-host redirects only, 2 MB body limit, 20-second timeout, `User-Agent: DevTrellis-VersionFeed/0.1`. Per source key (product list, product, package, repository) **at most one request per UTC day**; the response, including an error or `429`, is cached in the job's own SQLite file and reused until the next day. A `200` answer whose versions cannot be parsed is cached as `unparsed` (with its reason), not as `ok`, so it is reported honestly and not fetched again that day. Requests are spaced by `request_interval_seconds` (default 1). Tests use canned responses, including the real shape of the `ros` product; the real network was contacted once during acceptance with a handful of requests (see the delivery note).

## Run

```powershell
python -B -X utf8 araclar/surum-akisi.py example-config > version-feed.json   # edit: enabled, products, v1_reports
python -B -X utf8 araclar/surum-akisi.py run --config version-feed.json --db data/version-feed/feed.sqlite3 `
  --community data/community/community.sqlite3 --k1-db data/k1/k1.sqlite3 --out data/version-feed/labels-YYYYMMDD.json
python -B -X utf8 araclar/surum-akisi.py labels --db data/version-feed/feed.sqlite3 --kind v4 --id <record id>
```

The community and K1 stores are opened read-only. `--db` is a new, separate file; it is not part of the encrypted community backup and can be rebuilt by running the job again. Exit codes: 0 labels written, 2 feature disabled (nothing read, nothing requested), 1 error.

## Scheduling (documented, not installed)

Nothing in this delivery registers a task. If the operator decides to run it nightly:

Windows Task Scheduler, from the application folder, in the operator's own session:

```powershell
schtasks /Create /TN "DevTrellis Version Feed" /SC DAILY /ST 03:30 /TR "\"C:\path\to\.venv\Scripts\python.exe\" -B -X utf8 \"C:\path\to\araclar\surum-akisi.py\" run --config \"C:\path\to\version-feed.json\" --db \"C:\path\to\data\version-feed\feed.sqlite3\" --community \"C:\path\to\data\community\community.sqlite3\""
```

cron (Linux server):

```
30 3 * * * cd /srv/devtrellis && .venv/bin/python -B -X utf8 araclar/surum-akisi.py run --config version-feed.json --db data/version-feed/feed.sqlite3 --community data/community/community.sqlite3 --k1-db data/k1/k1.sqlite3 >> data/version-feed/run.log 2>&1
```

## Not in this delivery

- Showing the labels on report pages, in the MCP answer card or in the K1 `lookup` freshness field. `SurumAkisi.etiketleri_oku()` returns the stored labels for that integration.
- "Source changed" and "cited evidence missing" labels (they belong to K0 source tracking and the gate, not to release feeds).
- Any ranking, counter or hiding based on these labels.
