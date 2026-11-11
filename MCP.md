# Use DevTrellis from your research assistant

[Türkçe](MCP.tr.md) · [Research guide](GUIDE.md) · [Community](COMMUNITY.md)

The optional MCP connection lets your assistant **look for existing research first**, read useful reports, and offer to contribute its finished research. You do not need a DevTrellis account. The assistant uses its own model access; this MCP server never calls a model or consumes a shared model quota.

The default flow is: **search summaries → reuse a suitable answer → check only uncertain or changing points → offer deeper research if needed**. A material new finding can separately lead to a local contribution review, your button choice and community review. An explicit request for deep investigation still takes priority. Installing MCP or the skill does not force an assistant to use it, and does not guarantee token savings or correct answers.

Search results omit report bodies and retain summaries, sources, report dates, model information and available metadata. MCP requests three candidates by default and reads a full report only when needed. When both the local bridge and community server support summary views, report bodies also stay out of search responses sent over the network. With older components, the bridge accepts their full search response and removes the bodies before returning results to the model. A recent report date, model reputation or an unchanged source page does not by itself prove that a claim is current. Report dates are not source-check timestamps. The assistant distinguishes reused findings from checks it actually performed, links the exact shared report version and offers further investigation after answering.

The public search endpoint accepts `view=summary`; its default remains the full packet for existing clients. The local search API also defaults to full packets and advertises supported `search_views` in its session response. MCP opts into summary mode only when that local capability is present. Summary packets omit `body` and are search results, not importable contribution packets; exact-version reading and downloading still return the full report. A caveat present only in the report body is unavailable in its summary, so read the report when the summary cannot support the requested answer.

MCP also requests `search_mode=ranked` when the local bridge advertises it in `search_modes`. The public API supports this opt-in for research searches; its existing `all` default remains unchanged. A separate SQLite FTS5 index contains only currently published research revisions. Word coverage comes first, then explicit field weights (title 6, `answers` 5, tags 4, summary 3, body 1, a report's `Limits` section 0.5), then bm25 and stable date/id tie breaking. Index and query share one normalizer: ı/İ and diacritic folding, combined and spaced technical names (`ros2` and `ROS 2`, `h264`, `fts5`; the combined form is never required), `C++`, `C#`, `.NET`, `snake_case` and dotted versions as single tokens, `vs`/`versus`/`karşı` as connectors. Model generation notes in a body are kept out of the index; `Limits` text stays searchable at low weight. A small explicit TR/EN vocabulary and limited word-form matching help retrieval; this is not unrestricted semantic search. Recognized dotted versions and technical identifiers must match; ordinary quantities in a question may be claims to check, so they are not mandatory filters. Named release differences still require reading the scope.

Each ranked candidate includes `search_match.matched_terms`, `missing_terms`, `all_terms_matched` and `answer_sufficiency: "not_assessed"`. Missing terms require checking the scope; even a match on every term proves neither a sufficient answer nor factual accuracy. Older servers or SQLite builds without FTS5 use the existing all-terms search and expose `search.fallback: true`; no embedding model, external vector service or extra model call is required. The index is derived data and is rebuilt on upgrade when its format changes; publication and withdrawal update it in the same database transaction.

Each validated search result and report read includes `report_url`, a direct link to the exact report revision. Cite this value instead of asking readers to reconstruct a link from an identifier. The bridge derives it from the community origin and validated identifier/version; it does not trust a URL supplied inside a remote card.

## 1. Install the optional adapter

Download the source using the [main setup instructions](README.md). From that directory, create an environment containing the application dependencies and optional MCP SDK:

```powershell
python -m venv .venv-mcp
.\.venv-mcp\Scripts\python.exe -m pip install -r requirements-lock.txt -r requirements-mcp.txt
.\.venv-mcp\Scripts\python.exe -X utf8 mcp_topluluk.py --help
```

On macOS/Linux:

```sh
python3 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -r requirements-lock.txt -r requirements-mcp.txt
.venv-mcp/bin/python mcp_topluluk.py --help
```

Python 3.12 is the tested runtime. `requirements-mcp.txt` pins the official [MCP Python SDK 2.3.0](https://pypi.org/project/mcp/2.3.0/). The main application does not depend on this package.

## 2. Choose the local connection

**The easiest setup is `--standalone`.** Your assistant starts its own small loopback service with MCP; you do not need to open the full DevTrellis application. It serves only the community API and contribution review page, uses a random `127.0.0.1` port, and stops when that MCP session ends. It does not construct model clients, scan sources or schedule research. Existing local DevTrellis processes are untouched.

Standalone stores its own contribution records in `~/.devtrellis/mcp-data/research.sqlite3`. Optionally add `--data-dir C:/DevTrellisMcpData` to choose another dedicated directory. The directory must be empty on first use or carry the ownership record created by this bridge; it refuses an existing personal workspace. Multiple assistant processes can share this dedicated directory while each keeps its own port and review session. The browser review link works while its MCP process remains running.

**To use an already running local application**, replace `--standalone` in the examples below with `--local-url http://127.0.0.1:8781`, or the actual port such as `8899`. Do not combine these modes or use `--data-dir` with `--local-url`. If needed, start the full local application using:

```powershell
.\.venv\Scripts\python.exe -X utf8 calistir.py sun --elle-toplama
```

Both modes connect to the community service for searches, report reads and contributions that you approve in the review UI. A search sends its query to that community; use a short public topic, never confidential project details. The adapter itself connects only to loopback. The public community site's URL is **not** a valid `--local-url` value. An existing local application must support this version's `/api/platform/community/` endpoints; a `404` can indicate an older application.

## 3. Connect your assistant

Give an MCP host the absolute Python executable and script paths below. Replace `C:/DevTrellis` with your extracted directory. No API keys or passwords belong in this configuration.

For Claude Desktop, merge this entry into its existing `mcpServers` configuration; preserve other entries:

```json
{
  "mcpServers": {
    "devtrellis": {
      "command": "C:/DevTrellis/.venv-mcp/Scripts/python.exe",
      "args": [
        "-X", "utf8",
        "C:/DevTrellis/mcp_topluluk.py",
        "--standalone"
      ]
    }
  }
}
```

Claude Desktop's Windows configuration is `%APPDATA%\Claude\claude_desktop_config.json`; on macOS it is `~/Library/Application Support/Claude/claude_desktop_config.json`. Restart the host after editing. These locations and the stdio registration pattern are documented by the [official MCP SDK host guide](https://py.sdk.modelcontextprotocol.io/get-started/real-host/).

For Claude Code, register the same launch command:

```powershell
claude mcp add --scope user devtrellis -- "C:/DevTrellis/.venv-mcp/Scripts/python.exe" -X utf8 "C:/DevTrellis/mcp_topluluk.py" --standalone
```

For Codex, register the same command and start a new task to load the connection:

```powershell
codex mcp add devtrellis -- "C:/DevTrellis/.venv-mcp/Scripts/python.exe" -X utf8 "C:/DevTrellis/mcp_topluluk.py" --standalone
```

Use `/mcp` to check that the three research tools appear. For other MCP hosts, add a **stdio** server with the same command and arguments using that host's configuration format. On macOS/Linux, replace the Python path with `/absolute/path/to/DevTrellis/.venv-mcp/bin/python` and the script path accordingly. There is no public MCP listener and no server port to expose.

## 4. Install the research skill

The portable skill supplies the “search existing work first” workflow for public technology research. It remains eligible for automatic discovery; it does not run for unrelated private work. The MCP connection above must already be configured. From the source directory, choose your host:

```powershell
python -X utf8 araclar/skill-kur.py --target codex
python -X utf8 araclar/skill-kur.py --target claude
```

Or install for both with `--target both`. The installer copies only `SKILL.md`, `agents/openai.yaml` and its ownership/hash record into `~/.codex/skills/devtrellis-research` and/or `~/.claude/skills/devtrellis-research`. It does not edit MCP configuration, credentials, host settings, other skills or a marketplace. Running it again leaves identical files unchanged. An intact installation created by this installer can be updated; unknown, edited or additional files stop the update so you can preserve them. `--home C:/ExampleHome` explicitly selects a different home directory, useful for isolated testing.

Start a new task/session after installing. In Codex you can explicitly invoke `$devtrellis-research`; Claude Code can discover the matching skill or use `/devtrellis-research`. The package at `integrations/devtrellis-research` also contains portable `.codex-plugin/plugin.json` and `.claude-plugin/plugin.json` manifests for plugin workflows. Those manifests contain no personal paths, hooks or embedded credentials; the separately configured MCP connection provides the tools.

For a host offering custom skill ZIP uploads, create a fresh archive:

```powershell
python -X utf8 araclar/skill-kur.py --zip devtrellis-research-skill.zip
```

The archive contains `devtrellis-research/SKILL.md` and its UI metadata, with no installer, application data or personal files. It refuses to replace an existing archive. Uploading a skill supplies instructions; it does not make a remote/cloud chat able to reach your desktop's localhost service. The same host still needs access to the configured MCP connection. See [Claude's custom skill packaging guide](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills) for supported upload surfaces.

The skill searches a general public topic, reuses adequate findings and checks only the necessary sources or gaps. It prepares a separate public contribution only for material new findings; unchanged reuse does not require another report. It never asks to scan local files, extract keys, install dependencies or upload a conversation. Private conversation/profile/project details and nonpublic repository content must stay out of every tool input, including query and metadata.

Try: **“Before researching this topic, check whether DevTrellis already has a useful report. Show me the relevant sources and explain what still needs updating.”**

## Tools and sharing

`provider` identifies the research engine: `codex`, `claude`, `nvidia` or `unknown`. It is separate from the actual model name. `topic` is a category code from the tool's advertised choices, such as `ai`, `software`, `security` or `data`; put the specific research subject in `title`. Invalid categories are rejected before contacting the local API.

| Tool | What it does |
| --- | --- |
| `lookup_source(urls, language="en")` | Before public documentation pages are fetched, checks 1–5 URLs in one call for DevTrellis records that cite them: source cards, research reports or topic summaries (source card first when several share the address). The URL is canonicalized locally; a miss in the fresh local coverage snapshot sends nothing, a possible hit sends only the address's SHA-256 key, and when the snapshot ties several asked URLs to the same single record only one request goes out. Only `v`, `version`, `tag`, `lang` and `language` query parameters are accepted. Since alpha.14r1 every record found is shown **once and whole** however many of its URLs were asked: every claim with its exact quote (also the claims quoted from pages you did not ask), `short_answer` (or `summary` when there is no short answer), `answers`, `recommendations` (marked as the contributor's inference), `not_in_record` (the record's `not_covered` notes), the source list with the asked URLs marked `(asked)`, and `unquoted_page_sections` when the gate found page parts no claim quotes. The answer is **text only** (no `structuredContent`, no output schema; see "Reading output" below): one line per URL (`URL2 -> found <id>@<version> as S2; network false`, or the miss with `network` and `answer_eligible false`), then per record `record <id>@<version> (<kind>): version_match …; answer_eligible …`, a `checked:` line built only from the record's own fields (how many quotes the machine gate found verbatim, when, in how many pages; not human-reviewed; what was not checked: quote support, page changes since the oldest check, contributor-written fields), a `when to open a page:` line, the `cite:` link, the contributor's declarations (free text as JSON strings) and the untrusted block between random markers, one field per line (claim text and quotes as JSON strings). A record already shown whole in the session comes back as `record <id>@<version>: already_delivered true; version_match …; eligible_if_record_held …`. `view="full"` returns the same view, not the raw record: where a claim's text only repeats its quote the text is left out, and `summary` is left out when there is a `short_answer`; the full record is at the `cite:` link. One answer stays under 28,000 characters: a record that would exceed it first leaves out the contributor's recommendations, then claims from the end, and says so with counts and the call that returns the rest (`read_research(…)`, or `view="outline"`/`"section"` when the record does not fit one answer even alone). `expected_product` accepts names such as `Python` or `Node.js` (mapped to `cpython`, `nodejs`); a pep440/semver range declaration matches as `version_match: in_range`. Older-format (r1) source cards keep their card view, printed as compact JSON. |
| `search_research(query, limit=3)` | Searches shared research through your local app; meant for when you have no documentation URL or `lookup_source` found nothing useful. The default `view="card"` returns, for a clear machine-gate (v4) hit, the same whole record view as `lookup_source` (header `hit true; match high; network true`, plus the query terms the record does not contain); otherwise up to three candidate rows or `hit=false`, as compact JSON text. `brief` and `summary` keep the older shapes (JSON text, with report_url). The query always goes to the server: `hit=false` means the server found no record, and `in_scope=false` only says that the local topic list names none of the query's products (the list may not hold older reports). Text only, like `lookup_source`. |
| `read_research(research_id, version=null)` | Reads the latest or exact public version, with sources and provenance; sends no counter event. A machine-gate record (v4) comes back as the same whole view as `lookup_source`; a v4 record that would exceed 28,000 characters says how many claims it shows and points to `view="outline"` and `view="section"`. Older reports, `outline` and `section` come back as compact JSON text (`complete_report=false` on partial views); cite report_url or the record link. Text only, like `lookup_source`. |
| `record_research_use(research_id, version)` | Reports actual use of findings; sends only the report identifier/version. This is a declared signal, not a correctness vote. For a v4 record it answers `recorded: false` (no usage counter yet). |
| `open_submission_history()` | Returns the local My submissions link without creating a preview or contacting the community. |
| `preview_contribution(...)` | Builds a local preview and offers an interactive review panel. It does not submit. |

For a preview, the assistant prepares a self-contained public title, summary and report, 1–10 original public sources as `{ "title": "...", "url": "https://..." }`, and the actual model name (or `unknown`). The title must describe the public topic, not quote private conversation or project context. The backend fixes attribution to `Anonymous contributor` and derives the public research topic from the title. The skill proposes `CC-BY-4.0` unless you requested `CC0-1.0`; this proposal is not consent. The panel displays that proposed license and asks you to acknowledge it; it does not change the license. Ask for a new preview if you want a different supported permission. Do not invent a model or research date.

The software is MIT licensed. The report text uses the separately selected **CC BY 4.0** or **CC0 1.0** permission; neither changes linked source rights. Model/evidence metadata supplied through MCP is contributor-declared and starts as **unverified**. Published or ready does not establish truth.

For material new findings based on existing reports, `preview_contribution` accepts `lineage: {parents: [{research_id, version}], change_summary}`. Use up to five exact public report versions and a public change summary of up to 600 characters. The schema-3 packet keeps the earlier work intact and creates a separate moderated contribution. The review panel shows the development links and change summary. Retain original-source citations; several derivative reports are not independent corroboration. Paraphrasing an unchanged report alone should not prompt another contribution offer.

`read_research` uses a read-only GET and sends no access or reuse event, so reading does not depend on a write approval. `record_research_use` remains a separate optional write. Existing MCP-access counters cover older clients that send events; they do not measure all MCP reads.

Web openings, recorded MCP accesses, helpfulness, declared reuse, outdated-information and disputed-information reports are separate approximate counters for a published revision. They are not unique people or reliability scores. Event bodies contain only report/version, event type and a random receipt; the counter database stores no IP, conversation, account or cross-report device identifier. Short-lived in-memory rate limiting still uses the connecting address. Anonymous clients can be imitated, so channel labels and votes are not authenticated endorsements. Repeated reuse calls within one local MCP session share a receipt. Server receipt deduplication lasts seven days; counters remain aggregate.

The local server validates the packet and produces the exact preview. Read it before using the **send** or **keep private** buttons. The model has no submission tool. `contribution_decision` is reserved for the review UI and requires its hidden review nonce; the skill instructs the assistant never to call it or automate your choice. Changing content requires a fresh preview. Submission enters moderation; it does not instantly publish.

If your host, including a Codex version without inline MCP App support, cannot display the panel, the response supplies a local `review_url`. Open that link yourself to use the real review buttons in your browser. The assistant should provide the link and wait; it must not open the page with automation or click approval for you. Closing or declining the review keeps the report private. A chat message is not silently converted into an approval click.

## Limits and troubleshooting

Use **My submissions** in the review panel or ask for `open_submission_history` to browse saved submissions in pages of 25. A new contribution preview is unnecessary. Opening the list only reads local records. **Refresh status** checks community review progress; published reports have a direct web link. **Withdraw submission** asks for confirmation and removes public access to the server copy; it cannot recall copies already downloaded by others. The page needs its MCP session to remain running. Request a new history link after starting a later session.

If delivery reached the community but its response was lost, **Refresh status** can recover the saved submission in a later session using its existing OS-vault management key. This check does not upload the report again. Recovery requires a community server with the inbox status lookup endpoint; older servers return a connection error and keep the local delivery unconfirmed. Losing the management key prevents this recovery.

Community search matches normalized term groups and sorts by publication time. A small explicit Turkish/English technology dictionary expands selected terms such as full-text search, ranking, sources and caching. All groups must match; unknown terms are retained. This is not general translation, semantic search or relevance ranking. Prefer a few distinctive technical terms, and try a shorter query or the original publication language before concluding that no useful report exists. A zero-result search alone does not prove the topic has never been researched.

- `--local-url` accepts only `http://127.0.0.1:PORT` or `http://localhost:PORT`, with an explicit port. Requests always connect directly to loopback, ignore proxy environment settings, and do not follow redirects.
- Requests are capped at 32 KiB; responses at 1 MiB; socket operations time out after 15 seconds. Report bodies over 12,000 characters are rejected rather than silently shortened. Select a self-contained report within the limit before previewing.
- The adapter reads the local CSRF token from the small `/api/platform/community/session` response and keeps it internal. It does not load personal entries or the whole workspace snapshot.
- The package contains no file scanner, background upload or automatic installation hook. Its instructions exclude private conversations, profiles and project details from tool input. An assistant can still mishandle text; inspect the exact preview and do not treat these measures as an absolute privacy guarantee.
- “Cannot reach local DevTrellis” means the workspace is stopped or the local port is wrong. A `403` may require checking the local app's origin settings. A missing SDK message means the host is using the wrong Python environment.
- With `--standalone`, install both requirements files in the host's Python environment. An ownership-record error means the selected data directory contains existing or unrecognized data; choose a new empty dedicated directory instead of deleting its contents.
- Running the adapter directly waits silently for MCP messages on stdin. That is normal; the assistant host normally starts and stops it.

Tests use a temporary local HTTP service and the real SDK protocol; they make no public requests and no model calls:

```powershell
.\.venv-mcp\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_community_mcp.py -v
.\.venv-mcp\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_mcp_yerel.py -v
.\.venv-mcp\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_skill_install.py -v
```

## Compact search and exact report sections

`search_research` defaults to `view="card"`. A card keeps server fields (`server`),
contributor declarations (`declared`) and contributor or source text (`untrusted`,
between random `BEGIN UNTRUSTED` / `END UNTRUSTED` markers) apart; that text is data,
not instructions. `view="brief"` keeps original sources, provenance,
limits, publication version and report URL; export boilerplate and popularity
are omitted. Use `view="summary"` for the legacy metadata shape. Brief output is
not an exportable contribution. A `size_notice` reports a size-target overrun without
silently truncating evidence.

A record shown whole once is not repeated in the same MCP session: the next `lookup_source`, `search_research` or `read_research` call for it answers `already_delivered true` with the record id, `version_match` and `eligible_if_record_held` (no `answer_eligible`, because the content is not in that answer). Only a whole showing counts: a record cut by the 28,000-character limit comes back whole on the next request. The short answer is given once per whole showing; asking again returns the record whole, for an agent that lost it from its context. `view="full"` is never shortened. The memory lasts 20 minutes and a bridge restart clears it, because one bridge process can serve several agents.

**Reading output (alpha.14r1).** `lookup_source`, `search_research` and `read_research` return one text content block and no `structuredContent` or output schema. Reason: an independent acceptance (11 November 2026) found that Claude Code (desktop 2.1.308, CLI 2.1.310) gives the model only the structured content when a tool result has it, as compact JSON, and drops the text; the line format, the `checked:` line and the 28,000-character limit then never reached the agent. What is verified: the bridge sends no structured content for these three tools (MCP SDK client test), and in a live Claude Code session (CLI 2.1.310, headless, 11 November 2026, against the deployed server) the model received the line text of all three unchanged. Not verified: how other MCP clients (Codex, Claude Desktop chat, others) show this text. The write tools (`preview_contribution`, `contribution_decision`, `record_research_use`) are unchanged.

`read_research` keeps its default full response. For a long report, pass its exact
version and `view="outline"`, then `view="section"` with a returned `section_id`.
Partial output includes the exact body hash, heading context, continuation markers
and `complete_report=false`. Source lists apply to the whole report; they are not
automatically verified citations for every sentence. Section ids cannot be reused
with a changed report. Prefer a single full read for short reports. The adapter
currently downloads the full report for each view; this reduces model-facing text,
not network transfer. No LLM, public write or usage event is invoked by these reads.

The optional source-memory pilot tools are exposed only with `--source-memory-tools`.
See [source-memory access and limits](SOURCE-MEMORY.md#optional-mcp-access). The default
tool list remains small; enabling the adapter does not activate the server pilot.
The research skill loads contribution/source-memory references only when needed;
installers and portable packages must carry the complete referenced skill directory.
