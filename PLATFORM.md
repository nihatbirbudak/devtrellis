# DevTrellis platform

DevTrellis aims to become an open, community-maintained reference for technology: current news, research, practical development and reusable knowledge in one place. Hobbyists, learners, independent developers, researchers and professional teams should all be able to read, contribute and build on previous work. The first release combines a personal workspace with a separately operated community pilot. Worldwide coverage is a direction to grow toward; the current catalogue is limited.

## Product direction

The long-term goal is a living technology encyclopedia connected to a current developments feed. News records what changed; topic pages explain what is known, what remains uncertain and which sources or experiments support it. Research and projects contribute to those pages over time.

| Area | Reader and contributor value | Current state |
| --- | --- | --- |
| Developments | Follow releases, papers, developer posts, company announcements and independent reporting by interest | Personal collection and a public community feed exist; an integrated daily collection/summary/review pipeline is planned |
| Research | Reuse a sourced report, inspect its date/model/evidence and investigate again | Personal research and moderated sharing exist; comparison of related reports is planned |
| Knowledge pages | Find an accessible introduction, technical details, sources, open questions and a change history for a topic | Planned; card revisions are not yet a shared topic wiki |
| Projects | Show what you built, its repository/demo, progress and ways to contribute | Moderated project cards exist; project-linked news is planned |
| Publishers | Contribute updates from your own website and let tools search the public library | Manual contribution exists; publisher registration, connectors and MCP are planned |

Participation must not require an AI subscription or an expert title. A useful contribution can be a source, correction, reproduction result, translation, tutorial or project update. Beginner summaries and optional technical detail should share the same citations. Popularity and company size must not stand in for evidence; small projects need a discoverable place too.

Company announcements, independent findings, personal experiments and AI-generated summaries should have visible labels. A publisher's control of a website establishes who submitted the material, not whether its claims are true. Topic edits should preserve attribution, competing evidence, review status and revision history. Automated evidence checks remain distinct from human review.

## Personal workspace

Open `/platform` after following [setup](README.md). Browse dated developments, choose interests, maintain project and research cards, and exchange selected cards as files. The advanced workspace at `/` provides sources, saved searches, collections, research tasks and provider settings. Both interfaces offer English and Turkish; article, contributor and model text keeps its original language.

Opening a page or saving an interest does not start a model request. Source collection uses the internet. Optional research uses the person's own configured provider; [provider capabilities and limits](README.md#optional-model-providers) differ. Interface language and model report language are separate settings.

NVIDIA can also run source-based research with a personal API key. The [research guide](GUIDE.md) covers free prototype access, setup and provider terms. Reports preserve the model and generation date recorded for that output. Research again opens a separate request; creating a sharing draft leaves the original report intact.

A request can contain a public HTTPS social/news URL, a claim and a description/transcript. Public page text is untrusted context, not independent evidence. Inaccessible pages remain visibly inaccessible; video and audio are not processed. Independent discovery currently uses the developer catalogue and academic indexes, so unresolved claims and coverage limits remain explicit.

## Community pilot

[Operate the community](COMMUNITY.md) as a separate process and database:

1. Everyone can read and search published projects, research and collected developments.
2. Invited contributors preview selected cards before submitting them.
3. A different moderator reviews initial publication and corrections.
4. A correction remains private while the previous published revision stays available.
5. Authors can withdraw contributions; moderators can remove published material with a reason.
6. Readers can download published cards and preview/import them locally without contributing or calling a model.

The local workspace does not automatically synchronize with a server. File exchange is the current connection between installations. Downloaded copies cannot be recalled by withdrawing the server copy. Signed-in accounts identify submitters within an installation; they do not verify author claims or research conclusions.

The public developments feed reads a separate feed database populated by the collection command. It shows collected source descriptions; this is distinct from moderated research/project cards. The community process does not currently run its own daily collector or AI summary worker. The planned daily selection adds an explicit review stage for generated summaries.

## Source coverage

Topics are centrally defined: AI tools, home automation, robotics and perception, agriculture and greenhouse, media production, software development, software security, and data/databases. Existing topic identifiers remain compatible. The catalogue is a starting set of public feeds and project sources.

Registered sources, attempted checks, successful checks and failures are separate measurements. A recent check does not make an old article new or establish factual accuracy. Collection respects source access restrictions and has bounded requests; failed sources remain visible as failures. Historic coverage is incomplete.

## Card permissions and privacy

Code is MIT licensed, including commercial use. The author chooses CC BY 4.0 or CC0 1.0 for the card text. Source publications and linked software keep their own rights. Write your own findings and link to sources.

The export includes the selected card's title, summary, body, tags, links, author label, permission and dates. Research provenance can add the original question, claim, model, provider, generation time, language, source URL and evidence status. Raw request transcripts, other database records, preferences, raw chats and credentials are excluded. Private information in selected text, the question or claim will still be included, so read the preview. Shared provenance is contributor-supplied metadata, not a signed attestation or truth score. Editing report content marks the prior evidence status unverified.

Research requires at least one source link; a project requires a public project link. Unknown fields and invalid URLs are rejected. Import previews before saving; repeating the same packet does not create a duplicate. Different authors' semantically similar research is not automatically merged.

## API and compatibility

Personal reads: `GET /api/topics`, `/api/platform`, `/api/platform/feed`, `/api/providers`.

Personal writes include settings, entries, versioned edits/archive, export preview and import preview/confirmation under `/api/platform`. A download uses the exact previewed version; editing the card invalidates an earlier download URL. Host, Origin and CSRF checks remain in force.

The sharing format remains `ufuk-share`: version `1` without provenance, version `2` with provenance. Existing version 1 packets still import unchanged. Historical environment variable and database names remain compatible. The community has its own account/session and publication API under `/api/community`; the personal HTTP app must not be exposed as the public service.

## Next milestones

The following is the development plan, not a list of shipped capabilities. Public hosting still needs validation on the selected server.

### 1. A useful daily community feed

- Build a separate collection worker for the community service. Collect approved public RSS/Atom feeds, release feeds and research indexes according to each source's polling limits. The post-release researcher below checks for due work hourly. The existing collector handles a bounded batch, so the worker must resume remaining due sources within a time budget and report any backlog. The personal app keeps its existing collection mode.
- Normalize source URLs and upstream identifiers, preserve publication/update/fetch dates, and detect changed content. Re-running a job must not duplicate a story or repeat an unchanged model summary. Related coverage can be grouped while retaining every source.
- Use the operator's configured NVIDIA or another provider only for new or changed material: short sourced summaries, topics and optional beginner explanations. Show the producing model and generation time. Collection and reading must remain useful when the model quota is exhausted.
- Store candidates in a review queue before publishing the daily selection. Record failed sources, incomplete summaries and job progress. Retry bounded failures; separate automatic collection from approved publication.
- Begin with maintained project releases, primary research, developer blogs and selected technology news. Publish the source catalogue and its coverage gaps. Community members can propose additional sources; registration is reviewed.

Acceptance: the same collection run can be repeated without duplicates; a failed source does not erase previous material; quota exhaustion leaves links readable; a reader can reach the original source and distinguish its date from the summary date. A real pilot must confirm that daily review is manageable and that both a hobbyist and a professional can find relevant work.

NVIDIA documents free hosted access for prototyping. Its general API trial terms exclude production use of both the API service and generated content; a different applicable agreement is needed for public operation. See the [official FAQ](https://docs.api.nvidia.com/nim/docs/product), [API trial terms, sections 1.2 and 1.4](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf) and [setup guide](GUIDE.md). No recurring job is enabled by this plan.

### After publication: an hourly community researcher

Requested operating target: after the GitHub release and server deployment have passed acceptance, a server worker helps seed the library with current, sourced research. It checks once per hour, selects at most one new research question per run initially, and lists/publishes eligible results. Hourly checks do not promise a finished report every hour. This is a planned application worker, not an active Codex automation or an alpha.2 capability.

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

### 3. Website publishers and MCP

Start with an opt-in publisher registry and a documented submission API. A website can expose its existing feed or send a selected update from its CMS. Publisher identity, canonical URL, upstream item ID, revision, dates, content permission and affiliation travel with the submission. Domain control, rate limits and publisher-scoped credentials prevent one publisher from changing another's records. Repeated submissions are idempotent; corrections and withdrawals remain traceable.

Add an MCP adapter to this same service contract. Proposed capabilities are public search/read, preview a submission, submit a draft and propose a correction. Publishing remains a separate reviewer capability. Requests through MCP and the website must use the same permissions, validation and moderation rules. Tool names and wire schemas will be finalized during implementation; there is no DevTrellis MCP endpoint in alpha.2.

MCP connects AI applications to resources and tools; website display, daily scheduling and content federation still require application features. The design follows the [official MCP architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture) and [authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization). Ordinary publishers can use feeds or the API without an AI client. An optional site widget can later display their approved contributions.

MIT allows independent DevTrellis installations. Connecting an installation to the shared library is a separate opt-in feature; self-hosting does not automatically replicate its data or grant publication rights on another server. Automatic federation comes after the publisher workflow has proved useful.

Acceptance: a test publisher sends a real update from its own test site; duplicate delivery creates one item; another publisher cannot edit it; an authorized correction goes through review. Public search works from an MCP client, and publishing cannot be triggered by a read-only client.

Public adoption and external contribution numbers are not established. There is no hidden usage analytics. See [release notes](RELEASE-NOTES.md) for actual verification and remaining limitations.
