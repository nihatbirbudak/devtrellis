# Public source tracking (K0, opt in)

This local CLI records document and section fingerprints. It checks whether a selected public document changed without calling a language model. It does not create a research summary, verify a claim, publish content, or implement the planned v4 source-card lookup API. The existing v1 source-memory API and database remain separate.

## Start with one public source

Use the existing application Python environment. No new dependency is required. Save this small catalog outside the application data stores:

```json
{
  "schema": "devtrellis-source-tracking-v2",
  "daily_limit": 100,
  "sources": [
    {
      "id": "postgres-select-18",
      "url": "https://www.postgresql.org/docs/18/sql-select.html",
      "language": "en",
      "allow_public_cookie": false
    }
  ]
}
```

The example is a selected public address, not a guarantee that its current response is supported. A catalog accepts at most 20 unique sources on the existing approved document hosts. Use an exact canonical HTTPS address. Credentials, private/tracking query fields and ambiguous URL forms are rejected rather than removed. A public-looking URL is not proof that its contents are nonprivate: the operator must select public documentation.

```sh
python araclar/source-track.py --config sources.json --db source-tracking-v2.sqlite3 --source postgres-select-18
python araclar/source-track.py --config sources.json --db source-tracking-v2.sqlite3 --source postgres-select-18 --check
```

The first command reads local metadata; it can initialize a new empty K0 database but makes no network request. `--check` explicitly enables a check for this source. `--max-age 0` requires revalidation even if an ordinary HTTP cache lifetime remains. A correctly pinned immutable object may still be reused. Exit 0 means a local read, successful check, or already-fresh result; 1 means failure; 2 means bad arguments or a deferred check (inspect JSON). Successful checks can still return `fresh:false` when the server requires revalidation. Inspect `check_succeeded`, `changes`, and the timestamps together.

Choose a **new, separate database path**. Existing foreign/empty database files, pending rollback recovery, symlinks, junctions and hard links are refused. The current five-store community backup does **not** include this experimental fingerprint database. Its metadata is rebuildable from the catalog; it contains no user research body. Preserve the catalog separately. This is not an established backup/restore integration.

## What the result means

| Field | Meaning |
| --- | --- |
| `address_key` | Full SHA-256 of canonical URL and language. Case-sensitive paths, query order, versions and trailing slash stay distinct. |
| `source_key` | Address identity plus fixed anonymous request variant, representation policy and extractor version. |
| `document.body_hash` | Semantic hash of the selected static HTML document, not a raw HTTP body hash. |
| `document.sections` | Section IDs and hashes, hierarchy, order, duplicate-heading ambiguity; no raw text or headings. |
| `changes` | Initial, unchanged, changed or ambiguous; separate added/removed/changed/moved sections. |
| `representation_checked_at` | Last successful check of this representation; can advance after a valid 304. |
| `content_fetched_at` | Last successful full-body fetch; never advanced by 304. |
| `signal_checked_at` | Last optional Git/sitemap hint check; never substitutes for representation verification. |
| `quote_verified_at` | Always null in K0. No quotation or claim verification was performed. |
| `fresh` | HTTP/pinning freshness only. It does not mean an ambiguous section is reusable or a claim is true. |

The fixed policy is `public-doc-anonymous-v2`, extractor `dom-sections-v2`. Requests send an explicit language, identity encoding and `UfukRadar/0.1`; no authorization or cookie. Private/no-store responses, unsupported Vary, redirects, language mismatches and unsafe/ambiguous representations fail closed. Set-Cookie is accepted only with the source's explicit `allow_public_cookie:true` **and** an explicit public cache directive, subject to all other restrictions. Cookie values are discarded.

A 304 requires the previous same representation and the validators actually sent. It merges cache restrictions and sets `byte_verified:false`. Bad or missing clocks do not grant freshness. Errors preserve earlier successful timestamps and close the freshness gate. The disk store contains safe HTTP validators for conditional requests; public CLI output omits their values. Raw HTML stays in memory and is discarded. URL hashes are identifiers, not encryption or anonymity.

## Supported documents and limits

The extractor requires one explicit main/article/document container. It preserves code whitespace, link targets, table structure and footnote relationships. Repeated headings need reliable matching; ambiguity is reported. It understands static HTML `hidden` and supported inline display/visibility rules. `aria-hidden` is not visual hiding. It does not evaluate external CSS, JavaScript or browser layout. Images, SVG/math and interactive/unsupported evidence structures are rejected rather than silently discarded. A hash match refers only to this declared static representation.

### Generator contracts (second slice)

A page with an explicit documentation-generator signature uses a contract written in advance for that generator (`radar/kaynak_aileleri.py`). The document's `extractor_version` names it, for example `dom-sections-v2.docbook-1`; pages without a signature keep the generic rule (`dom-sections-v2.generic`). A change of contract between two checks is reported as ambiguous, never unchanged.

| Contract | Selected by | Document root | Removed generator chrome |
| --- | --- | --- | --- |
| `docbook-1` | DocBook XSL component with title page or refentry name block | single outermost component | none; note/tip/warning titles are content, not sections |
| `sphinx-1` | `meta generator` Docutils | single `[role=main]` (theme article body when present) | `a.headerlink`, `button.copybtn` |
| `mkdocs-material-1` | `meta generator` mkdocs + mkdocs-material | `article.md-content__inner` | permalinks, edit button, `aside.md-source-file`, clipboard button |
| `docusaurus-1` | `meta generator` Docusaurus | `.theme-doc-markdown` | `a.hash-link`, code-block buttons; CSS-module class suffixes |
| `hugo-docsy-1` | `meta generator` Hugo | single `div.td-content` (other Hugo themes: mismatch) | `a.td-heading-self-link`, last-modified line |
| `starlight-1` | `meta generator` Starlight | `.sl-markdown-content` | anchor links, code-block loader script/style (src only), copy button; `astro-*` build classes |
| `vuepress1-1` (site experiment) | `meta generator` VuePress 1 | `.theme-default-content.content__default` | `a.header-anchor` |
| `nodered-docs-1` (site experiment) | host `nodered.org`, path `/docs/`, `div.docs-content > h1.docs-title` | `div.docs-content` | none; video frames kept as addresses |

Contracts do not relax the generic rule. A signature without its root, or two signatures, fail closed (`family_contract_mismatch`, `ambiguous_document_family`). Undeclared active content, forms, buttons, non-decorative SVG and other unsupported structures are still rejected. Inside a contract, link targets are hashed as their exact absolute address (http, https and mailto; `javascript:` is active content), images and declared frames by source address plus alt/title text, and decorative SVG icons (`aria-hidden="true"` or named in the contract) by their presence and class, not geometry. Image bytes, video content, responsive `srcset` variants and the page title outside the root are not part of the representation. Repeated headings are bound by the anchor the generator writes (DocBook section id, Sphinx section id, heading id); without such an anchor they stay ambiguous. The page structures were checked against the frozen 20-source list only for DocBook (PostgreSQL), Starlight (ESPHome), VuePress 1 (openHAB) and Node-RED; the other fixtures follow each generator's published markup and still need real-page acceptance.

### Failure codes

A failed check keeps the fixed message and also stores `error_code`, `error_stage` (`fetch`, `policy`, `extract`, `tracker`) and a derived `error_class`. No address, header or response text is stored.

| `error_class` | Example codes | Meaning |
| --- | --- | --- |
| `source_refused` | `robots_disallowed`, `http_forbidden` | The source does not allow this read. The address stays in the catalog with this state. |
| `rate_limited` | `http_rate_limited`, `crawl_delay_exceeds_limit` | The source asks for slower access. |
| `address_changed` | `redirect_html_suffix`, `redirect_trailing_slash`, `redirect_same_host`, `redirect_other_host`, `http_not_found` | Redirects are not followed; a new address needs a new catalog entry. |
| `network_failure` | `timeout`, `connection_closed`, `connection_refused`, `tls_error`, `dns_timeout`, `robots_connection_closed` | The connection did not complete; the cause is not a refusal. |
| `server_error` | `http_server_error`, `robots_unavailable` | The server or its robots file did not answer usably. |
| `security_block` | `tls_certificate`, `non_public_address`, `host_not_allowed` | A safety rule stopped the read. |
| `policy_refused` | `private_cache_policy`, `unsupported_content_type`, `public_cookie_not_authorized` | The response is outside the anonymous public-document policy. |
| `unsupported_document` | `ambiguous_document_root`, `family_contract_mismatch`, `unsupported_active_content` | The page was read but its structure is not supported. |
| `tracker_check` | `previous_representation_missing`, `sent_validator_mismatch`, `lease_expired` | The tracker's own consistency check stopped the update. |

Checks are on demand. Per database: up to two active checks, one 90-second lease per source, a 60-second failure retry delay, and a default 100 check attempts per UTC day. Failed attempts consume budget. Network operations inherit public-DNS/TLS, robots, time and body limits. No hourly crawler is installed.

`requests` counts logical document/hint attempts, excluding robots/TLS. `downloaded_bytes` counts returned body bytes observed by the tracker, not wire traffic, headers, failed partial reads or token use. `model_calls:0` is an architectural property of this check path, not measured research quality or token savings.

### Source usage signals (third slice)

Each successful check records `usage_signals`: the robots.txt `Content-Signal` lines for this agent's group (`search`, `ai-input`, `ai-train`; Python's robots parser drops them, so they are read separately from the same cached robots file), the `X-Robots-Tag` header (directives scoped to another agent, such as `googlebot: noindex`, are ignored) and the page's `<meta name="robots">` / `<meta name="ufukradar">`. Only recognized directives are kept, never raw values. Derived fields `model_input`, `model_training`, `quotation` and `search_index` are `restricted`, `not_restricted`, `ambiguous` (an unrecognized value; never treated as permission) or `not_checked`. `ai-input=no`, `noarchive`, `nocache`, `none` or `noai` make `model_input` restricted: such a source stays in hash-only checks and no model summary may be produced from its text (enforced by the K1 layer; the K0 check path has no model). On a 304 the page-level meta and an absent header are carried over from the previous record.

### Address canonicalization (third slice)

Dot segments are resolved (RFC 3986): `/a/./b` and `/a/c/../b` give the same key as `/a/b`; an encoded dot segment (`%2e`) or `..` above the root is rejected. A path segment of the form `name@1.2` (for example `enterprise-server@3.14`) is not treated as an email address; mailbox-like segments such as `user@example.com` or `user@10.0.0.1` are still rejected. Query fields stay limited to `v`, `version`, `tag`, `lang` and `language`. Migration: a K0 record created under a dot-segment address (for example `/a/./b`) is no longer reached by the catalog, which now requires the resolved form; it stays orphaned in the store and the source starts again with a first representation. K0 metadata is rebuildable from the catalog, so the old store can be replaced by a new one. Single requests may stay idle for up to 20 seconds; the 35-second source deadline is unchanged.

## Optional hints and pinned objects

Source fields `signal_kind` and `signal_url` can select **one** hint: `sitemap` (same-host exact XML URL) or `git_commit` (an explicit public `https://api.github.com/repos/OWNER/REPO/commits/REF` endpoint). The Git host exception is limited to this endpoint and request context; it does not expand the document catalog. Private repositories/authentication, redirects and recursive sitemap traversal are unsupported. `signal_interval_seconds` is 3600–604800, default 86400. Only the selected page's sitemap date is considered. A matching hint never skips a required HTTP check.

For an operator-verified immutable object mapping, set both `immutable_object_id` (full 40/64 hex, a complete path component) and `expected_content_sha256` (full raw body SHA-256 known independently). Only after the first actual byte match and acceptable cache policy may the pinned version be reused without another request; its old check timestamp remains unchanged. A tag or `/latest/` URL is not such a mapping. This optional behavior is covered by synthetic tests; real-site acceptance must be reported separately.

## Türkçe kısa rehber

K0, seçilmiş açık belgelerin değişimini model çağırmadan izleyen yerel bir temeldir. Araştırma özeti veya iddia doğrulaması üretmez; canlı siteye bağlanmış yeni v4 API değildir. Yukarıdaki katalogda en çok 20 kamu kaynağı seçilir. İlk komut yalnız yerel bilgiyi okur, `--check` kaynak kontrolüne izin verir. Ayrı ve yeni bir SQLite yolu kullanın; mevcut topluluk yedeği bu deneysel veritabanını kapsamaz.

“Güncel” yalnız seçilen temsilin HTTP/pin durumudur. Kaynağın veya iddianın doğru olduğunu göstermez. 304, gövdeyi yeniden indirme veya alıntı doğrulaması değildir. Hata eski başarı tarihini yenilemez. Bölüm eşlemesi belirsizse aynı sayılmaz. Git/site haritası yalnız ipucudur; gerekli HTTP kontrolü yapılır. Ham sayfa, çerez değeri veya araştırma metni saklanmaz; yerel depoda koşullu istek için güvenli HTTP doğrulayıcıları bulunur. Model çağrısı 0 olması token tasarrufunun veya araştırma kalitesinin ölçüldüğü anlamına gelmez.

Belge üreticisi açıkça belliyse (DocBook, Sphinx, MkDocs Material, Docusaurus, Hugo Docsy, Starlight; ayrı deney olarak VuePress 1 ve Node-RED) önceden yazılmış sözleşme uygulanır. Sözleşme yalnız adıyla bilinen arayüz parçalarını (kalıcı bağlantı işareti, kopyalama düğmesi, son değişiklik tarihi) çıkarır; adı geçmeyen etkin içerik yine reddedilir. Görsel ve video yalnız adresiyle temsil edilir, içeriği okunmaz. Aynı adlı başlıklar üreticinin verdiği kimlikle eşlenir; kimlik yoksa belirsiz kalır. Başarısız kontrol `error_code` ve `error_class` alanlarıyla nedenini söyler: `source_refused` kaynağın izin vermediğidir (robots, 401/403); bu adres listeden çıkarılmaz.

Her başarılı kontrol `usage_signals` kaydı tutar: robots dosyasındaki `Content-Signal` satırları, `X-Robots-Tag` başlığı ve sayfanın `meta robots` değeri; yalnız tanınan yönergeler saklanır. `ai-input=no`, `noarchive`, `nocache`, `none` ya da `noai` varsa `model_input` kısıtlıdır: kaynak yalnız hash kontrolünde kalır, metninden model özeti üretilmez. Tanınmayan değer belirsizdir, izin sayılmaz. Adreste `/a/./b` artık `/a/b` ile aynı anahtardır; `enterprise-server@3.14` gibi sürüm parçası kabul edilir, e-posta benzeri parça reddedilir. Tek istek en çok 20 saniye bekleyebilir. Göç notu: nokta parçalı eski adresle açılmış K0 kaydı yetim kalır; kaynak yeniden ilk temsille başlar (depo katalogdan yeniden üretilebilir).
