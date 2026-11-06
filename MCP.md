# Use DevTrellis from your research assistant

[Türkçe](MCP.tr.md) · [Research guide](GUIDE.md) · [Community](COMMUNITY.md)

The optional MCP connection lets your assistant **look for existing research first**, read useful reports, and offer to contribute its finished research. You do not need a DevTrellis account. The assistant uses its own model access; this MCP server never calls a model or consumes a shared model quota.

The default flow is: **search summaries → reuse a suitable answer → check only uncertain or changing points → offer deeper research if needed**. A material new finding can separately lead to a local contribution review, your button choice and community review. An explicit request for deep investigation still takes priority. Installing MCP or the skill does not force an assistant to use it, and does not guarantee token savings or correct answers.

Search results omit report bodies and retain summaries, sources, report dates, model information and available metadata. MCP requests three candidates by default and reads a full report only when needed. When both the local bridge and community server support summary views, report bodies also stay out of search responses sent over the network. With older components, the bridge accepts their full search response and removes the bodies before returning results to the model. A recent report date, model reputation or an unchanged source page does not by itself prove that a claim is current. Report dates are not source-check timestamps. The assistant distinguishes reused findings from checks it actually performed, links the exact shared report version and offers further investigation after answering.

The public search endpoint accepts `view=summary`; its default remains the full packet for existing clients. The local search API also defaults to full packets and advertises supported `search_views` in its session response. MCP opts into summary mode only when that local capability is present. Summary packets omit `body` and are search results, not importable contribution packets; exact-version reading and downloading still return the full report. A caveat present only in the report body is unavailable in its summary, so read the report when the summary cannot support the requested answer.

MCP also requests `search_mode=ranked` when the local bridge advertises it in `search_modes`. The public API supports this opt-in for research searches; its existing `all` default remains unchanged. A separate SQLite FTS5 index contains only currently published research revisions. Word coverage comes first, then weighted title/summary/tag/body relevance, with stable date/id tie breaking. A small explicit TR/EN vocabulary and limited word-form matching help retrieval; this is not unrestricted semantic search. Recognized dotted versions and technical identifiers must match; ordinary quantities in a question may be claims to check, so they are not mandatory filters. Named release differences still require reading the scope.

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
| `search_research(query, limit=3)` | Searches shared research through your local app; 1–10 summary packets with `view=summary` and no `packet.body`. The default and the skill's initial request are 3. |
| `read_research(research_id, version=null)` | Reads the latest or exact public version, with sources and provenance; sends no counter event. |
| `record_research_use(research_id, version)` | Reports actual use of findings; sends only the report identifier/version. This is a declared signal, not a correctness vote. |
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

`search_research` now defaults to `view="brief"`: original sources, provenance,
limits, publication version and report URL remain; export boilerplate and popularity
are omitted. Use `view="summary"` for the legacy metadata shape. Brief output is
not an exportable contribution. A `size_notice` reports a size-target overrun without
silently truncating evidence.

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
