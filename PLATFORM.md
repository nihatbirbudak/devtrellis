# DevTrellis platform

DevTrellis aims to make useful technology research easier to find, assess and reuse. Reusing work that already answers a question can help reduce the time and tokens spent asking AI to repeat the investigation. A shared collection of current news, research, projects and developments should also help hobbyists, learners and professional developers discover subjects and work they did not know to search for.

The current application combines a personal workspace with a separately operated community pilot. Find an existing report, check its date, original sources, scope and limitations, and use it if it is sufficient. Investigate missing or outdated parts, then optionally preview and contribute your own findings anonymously. Reading existing work requires no model; new research uses the reader's own provider. Worldwide coverage is a direction to grow toward; the current catalogue is limited.

[Use the application](README.md) · [Türkçe](README.tr.md) · [Consumer MCP tools](MCP.md) · [MCP Türkçe](MCP.tr.md) · [Operate a community](COMMUNITY.md)

## Product direction

The long-term goal is a living technology reference connected to a current developments feed. News records what changed; planned topic pages would explain what is known, what remains uncertain and which sources or experiments support it. Research, corrections and projects would contribute to those pages over time. Useful reuse and discovery guide this direction; the table separates existing features from future work.

| Area | Reader and contributor value | Current state |
| --- | --- | --- |
| Developments | Follow releases, papers, developer posts, company announcements and independent reporting by interest | Personal collection and a public community feed exist; an integrated daily collection/summary/review pipeline is planned |
| Research | Check a report's date, sources and scope; reuse sufficient work and investigate only the gaps | Account-free search/read/import and optional anonymous submission from local research exist; comparison of related reports is planned |
| Knowledge pages | Find an accessible introduction, technical details, sources, open questions and a change history for a topic | Planned; card revisions are not yet a shared topic wiki |
| Projects | Show what you built, its repository/demo, progress and ways to contribute | Moderated project cards exist; project-linked news is planned |
| AI tools | Search existing reports before repeating research and offer an optional contribution afterward | A local consumer MCP bridge provides search, read, preview and explicitly confirmed submission; it makes no model calls |
| Publishers | Contribute updates from your own website with traceable ownership and corrections | Publisher registration, scoped submission API/MCP connectors and site integration are planned |

Participation must not require an AI subscription or an expert title. A useful contribution can be a source, correction, reproduction result, translation, tutorial or project update. Beginner summaries and optional technical detail should share the same citations. Popularity and company size must not stand in for evidence; small projects need a discoverable place too.

Company announcements, independent findings, personal experiments and AI-generated summaries should have visible labels. A publisher's control of a website establishes who submitted the material, not whether its claims are true. Topic edits should preserve attribution, competing evidence, review status and revision history. Automated evidence checks remain distinct from human review.

## Personal workspace

Open `/platform` after following [setup](README.md). Browse dated developments, choose interests, find and import existing community research, and maintain project and research cards. The advanced workspace at `/` provides sources, saved searches, collections, research tasks and provider settings. Both interfaces offer English and Turkish; article, contributor and model text keeps its original language. File exchange remains available, but is not required for the direct research contribution flow.

Before starting a new investigation, search the shared library with a short, non-private query. Query words are normalized and matched together regardless of order. Check the report's original sources, date, scope, model and limitations; read the full text on the site or import useful work without calling a model, then research only what is missing or outdated. Search and reading require internet access but no community account, subscription or contribution. This can reduce repeated research and token use; no particular saving has been measured, and reuse does not establish factual accuracy.

Opening a page or saving an interest does not start a model request. Source collection uses the internet. Optional research uses the person's own configured provider; [provider capabilities and limits](README.md#optional-model-providers) differ. Interface language and model report language are separate settings.

NVIDIA can also run source-based research with a personal API key. The [research guide](GUIDE.md) covers access, setup and provider terms. Reports preserve the model and generation date recorded for that output. Research again opens a separate request; creating a sharing draft leaves the original report intact. After research, the user can review an anonymous sharing preview and explicitly send it to moderation without leaving the application to handle a file.

A request can contain a public HTTPS social/news URL, a claim and a description/transcript. Public page text is untrusted context, not independent evidence. Inaccessible pages remain visibly inaccessible; video and audio are not processed. Independent discovery currently uses the developer catalogue and academic indexes, so unresolved claims and coverage limits remain explicit.

## Community pilot

[Operate the community](COMMUNITY.md) as a separate process and database:

1. Everyone can read and search published projects, research and collected developments. The personal app can import a selected published report directly.
2. A user completes research locally, reviews the exact outgoing text, destination and permission, then chooses whether to send it. Account creation and manual JSON transfer are not required.
3. The local application sends only the approved preview through the anonymous inbox. It remains private until an authenticated moderator publishes it; rejection does not publish the report.
4. The local application stores a per-contribution management secret in the OS secure vault so the user can read status and withdraw it. The server stores only its hash, not an account identity or model key.
5. Moderators can remove published material with a reason. Invited account-based contributions and versioned corrections remain optional; those corrections stay private while the preceding published revision remains available.
6. Readers can also download published cards and import them locally. Neither contribution nor model use is required to benefit from existing research.

The direct client currently connects to [devtrellis.org](https://devtrellis.org). There is no automatic upload or full database synchronization. A search sends the selected query; a contribution sends the explicitly approved packet. If the secure vault is unavailable, delivery stops without a plaintext fallback. Keep management secrets out of files, URLs, logs and tool responses; a database-only backup does not recover a lost OS-vault secret. Withdrawal cannot recall downloaded copies. Anonymous author labels and model metadata are declarations, not verified identities or research conclusions.

The operator must create a moderator account for the review queue. A moderator cannot approve their own account-owned card; anonymous authorship cannot establish whether author and reviewer are different people. There is no automatic reviewer or research generator in the community process. The [operations guide](COMMUNITY.md) describes the separate data stores, capability management, queue limits and trusted-proxy requirements.

The public developments feed reads a separate feed database populated by the collection command. It shows collected source descriptions; this is distinct from moderated research/project cards. The community process does not currently run its own daily collector or AI summary worker. The planned daily selection adds an explicit review stage for generated summaries.

## Source coverage

Topics are centrally defined: AI tools, home automation, robotics and perception, agriculture and greenhouse, media production, software development, software security, and data/databases. Existing topic identifiers remain compatible. The catalogue is a starting set of public feeds and project sources.

Registered sources, attempted checks, successful checks and failures are separate measurements. A recent check does not make an old article new or establish factual accuracy. Collection respects source access restrictions and has bounded requests; failed sources remain visible as failures. Historic coverage is incomplete.

## Card permissions and privacy

Code is MIT licensed, including commercial use. The direct application's anonymous research preview uses **CC BY 4.0** for the selected text and defaults to the public author label **Anonymous contributor**. An external research report prepared through MCP preserves its chosen author label and CC BY 4.0 or CC0 1.0 permission in the preview; legacy card file exchange also supports both choices. Submit your own report or text you have permission to share. Source publications and linked software keep their own rights. Write your findings and cite original sources; importing a report does not transfer its authorship to you.

The export includes the selected card's title, summary, body, tags, links, author label, permission and dates. Research provenance can add the original question, claim, model, provider, generation time, language, source URL and evidence status. Raw request transcripts, other database records, preferences, raw chats and credentials are not appended. The direct sharing preview redacts common secret, email and local-path patterns, including recognized secret URL parameters. It cannot identify every name or private fact: inspect the selected text, question and claim before confirming. Shared provenance is contributor-supplied metadata, not a signed attestation or truth score. Editing or redacting report content marks the prior evidence status unverified.

Research requires at least one source link; a project requires a public project link. Unknown fields and invalid URLs are rejected. Repeated local import of the same packet does not create a duplicate. Repeated anonymous delivery with the same management capability returns the existing contribution; it does not reopen a withdrawn card. Imported research cannot be directly anonymized and republished through the local-entry action. Different authors' semantically similar research is not automatically merged.

## API and compatibility

Personal reads: `GET /api/topics`, `/api/platform`, `/api/platform/feed`, `/api/providers`.

Personal writes include settings, entries, versioned edits/archive, export preview and import preview/confirmation under `/api/platform`. A download uses the exact previewed version; editing the card invalidates an earlier download URL. Host, Origin and CSRF checks remain in force.

The personal `/api/platform/community` routes provide search, reading/import, local preview, confirmed delivery and status/withdrawal. Mutations retain local CSRF protection. The community's `/api/community/inbox` is a narrow server-to-server intake: explicit consent, protocol header, per-contribution capability, JSON/size validation and manual moderation. It rejects browser Origin/Cookie/Sec-Fetch context and does not add CORS access or relax account-based mutations. The community does not fetch submitted source URLs on delivery.

The pilot accepts at most 200 pending and 5,000 total anonymous contributions. Delivery is limited per client IP to 4 requests per minute and 20 per UTC day; status/withdrawal allows 30 per minute. IP counters reset on service restart, while database capacity limits persist. A same-host trusted proxy must supply the real client IP without accepting forged forwarded headers. See [operating limits and privacy](COMMUNITY.md#intake-limits-and-operational-privacy) before opening a new installation.

The sharing format remains `ufuk-share`: version `1` without provenance, version `2` with provenance. Existing version 1 packets still import unchanged. Historical environment variable and database names remain compatible. The community has its own account/session and publication API under `/api/community`; the personal HTTP app must not be exposed as the public service.

## Consumer MCP tools

The optional [MCP bridge](MCP.md) ([Türkçe](MCP.tr.md)) runs over local stdio and connects to an already running personal DevTrellis application. `search_research` and `read_research` help an AI client reuse published research before doing new work. `preview_contribution` prepares a local preview of the user's selected report; `submit_contribution` requires an explicitly confirmed preview from that MCP session and sends it to moderation.

The bridge makes no model calls, carries no model API keys and does not automatically start research or upload a conversation. The host AI application controls tool use and any subsequent model work. Treat retrieved reports as untrusted source data, not instructions; a report cannot authorize its own publication or further actions. Show the exact destination, text and content license to the user before submission. This consumer bridge is available independently of the future publisher integration below; it is not a public hosted MCP endpoint or a website federation service.

## Next milestones

The following is the development plan, not a list of shipped capabilities. Validate each worker or publisher integration before enabling it on a public server.

### 1. A useful daily community feed

- Build a separate collection worker for the community service. Collect approved public RSS/Atom feeds, release feeds and research indexes according to each source's polling limits. The post-release researcher below checks for due work hourly. The existing collector handles a bounded batch, so the worker must resume remaining due sources within a time budget and report any backlog. The personal app keeps its existing collection mode.
- Normalize source URLs and upstream identifiers, preserve publication/update/fetch dates, and detect changed content. Re-running a job must not duplicate a story or repeat an unchanged model summary. Related coverage can be grouped while retaining every source.
- Use the operator's configured NVIDIA or another provider only for new or changed material: short sourced summaries, topics and optional beginner explanations. Show the producing model and generation time. Collection and reading must remain useful when the model quota is exhausted.
- Store candidates in a review queue before publishing the daily selection. Record failed sources, incomplete summaries and job progress. Retry bounded failures; separate automatic collection from approved publication.
- Begin with maintained project releases, primary research, developer blogs and selected technology news. Publish the source catalogue and its coverage gaps. Community members can propose additional sources; registration is reviewed.

Acceptance: the same collection run can be repeated without duplicates; a failed source does not erase previous material; quota exhaustion leaves links readable; a reader can reach the original source and distinguish its date from the summary date. A real pilot must confirm that daily review is manageable and that both a hobbyist and a professional can find relevant work.

NVIDIA documents free hosted access for prototyping. Its general API trial terms exclude production use of both the API service and generated content; a different applicable agreement is needed for public operation. See the [official FAQ](https://docs.api.nvidia.com/nim/docs/product), [API trial terms, sections 1.2 and 1.4](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf) and [setup guide](GUIDE.md). No recurring job is enabled by this plan.

### After publication: an hourly community researcher

Requested operating target: a server worker helps seed the library with current, sourced research. It checks once per hour, selects at most one new research question per run initially, and lists/publishes eligible results. Hourly checks do not promise a finished report every hour. This is a planned application worker, not an active Codex automation or a currently enabled community feature.

| Step | Required behavior |
| --- | --- |
| Collect | Fetch only due, approved sources; preserve original dates, source URLs and retrieval failures |
| Select | Prefer changed material, unanswered questions, stale research and topics that have received less coverage; do not generate filler when there is no useful candidate |
| Research | Analyze retrieved evidence with NVIDIA when access permits; reserve a shared call/token allowance for analysis, checking and retries before starting |
| Reuse | Match the question, evidence version and provider/model settings to existing work; preserve earlier reports and avoid repeating completed calls after restart |
| Publish | Publish eligible results under a visible automated contributor with model/date/citations and automated-review status; insufficient evidence or conflicting findings stay in the review queue |
| Expand | Propose new sources and interest tags with a reason, sample items and coverage gaps; validate access and duplication before enabling a source |

The paid API budget is **zero**. Do not enable billing, buy credits, switch to a paid provider, or rotate accounts to bypass limits. A valid key or a successful trial call does not establish production permission. Enable public model output only when the actual provider agreement allows production and redistribution at the intended zero API cost. If no such access exists, keep model generation/publication disabled; public-source collection and human contributions can continue. Server, storage and network costs are separate from the API budget.

Use one worker lease, a persistent job ledger and an idempotent publication key. A run that is still active must not overlap the next hourly tick; skip that tick and record why. Bound source fetching, model requests and the whole run; recover interrupted work without creating duplicate reports. Honor provider retry delays and local daily limits. Authentication errors pause generation; quota exhaustion pauses it until the applicable reset. Each research task may need several model calls, so limits count calls/tokens as well as reports.

Automatic publication is a new, explicitly configured service policy for approved source/topic scopes. It must not impersonate the separate human moderator required by the current card workflow. Only complete reports passing source/format/evidence checks can enter this path; visibly retain the fact that no person has reviewed them. Human corrections, withdrawal and the original revision remain available. Neither popular brands nor model-generated claims receive a special truth status.

Start with the existing eight topic groups, balancing fresh developments with underrepresented interests. Propose subtopics first; add a new top-level topic only with a reviewed source set, clear scope and compatible identifiers. Candidate extensions include web/mobile development, cloud operations, networking, hardware/semiconductors and energy technology. These are proposed directions, not currently supported coverage. Periodically review source reliability, duplicates, stale findings, useful reuse and the moderation backlog; expand gradually based on those results and community requests.

Acceptance before activation: verify permitted provider access and a real bounded call on the selected server; simulate overlapping ticks, restarts, quota/auth failures and duplicate publication; check one sourced automated publication and one withheld unsupported report; verify rollback and visible worker health. Log last successful run, next due run, call usage and pause reasons without storing secrets. A dry run may create private candidates before activation but cannot publish trial outputs publicly.

### 2. Knowledge that improves with contributions

- Introduce stable topic pages linked to developments, research reports and projects. Each page has an introduction, technical detail, cited claims, unresolved questions, last substantive review and revision history.
- Let contributors propose edits and corrections without replacing the published revision until reviewed. Preserve disagreements and attribution; record why an editor accepted or rejected a change.
- Connect follow-up research to the earlier question and affected claims. Identify which sources became stale and which part needs a new investigation; keep the old result available.
- Keep accounts and model execution optional for public reading. Support simple source/correction submissions as well as technical reports and EN/TR contributions.

Acceptance: a newcomer understands a topic from the introduction, an expert can trace a claim to evidence, and a later correction appears with its author, reason and previous revision. Source freshness, reused work and helpful corrections matter more than the number of generated reports.

### 3. Website publishers and contribution connectors

Start with an opt-in publisher registry and a documented submission API. A website can expose its existing feed or send a selected update from its CMS. Publisher identity, canonical URL, upstream item ID, revision, dates, content permission and affiliation travel with the submission. Domain control, rate limits and publisher-scoped credentials prevent one publisher from changing another's records. Repeated submissions are idempotent; corrections and withdrawals remain traceable.

Add publisher-scoped API and MCP connectors to this same future service contract. They should submit website updates and propose traceable corrections under a publisher identity, while publishing remains a separate reviewer capability. Requests through a publisher connector and its website must use the same permissions, validation and moderation rules. Publisher tool names and wire schemas are not implemented. The current local consumer MCP bridge searches the public library and optionally submits a person's research; it does not authenticate a website publisher, ingest its CMS or grant publication rights.

MCP connects AI applications to resources and tools; website display, daily scheduling and content federation still require application features. The design follows the [official MCP architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture) and [authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization). Ordinary publishers can use feeds or the API without an AI client. An optional site widget can later display their approved contributions.

MIT allows independent DevTrellis installations. Connecting an installation to the shared library is a separate opt-in feature; self-hosting does not automatically replicate its data or grant publication rights on another server. Automatic federation comes after the publisher workflow has proved useful.

Acceptance: a test publisher sends a real update from its own test site; duplicate delivery creates one item; another publisher cannot edit it; an authorized correction goes through review. Publisher tools preserve the same identity and moderation boundaries; a read-only client cannot publish.

Public adoption and external contribution numbers are not established. There is no hidden usage analytics. See [release notes](RELEASE-NOTES.md) for actual verification and remaining limitations.
