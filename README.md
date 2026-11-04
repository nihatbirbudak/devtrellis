# DevTrellis

**Discover. Research. Build together.**

[Türkçe](README.tr.md) · [MIT license](LICENSE) · [Research & free NVIDIA setup](GUIDE.md) · [Community service](COMMUNITY.md)

DevTrellis is an open technology knowledge community for hobbyists and professional developers. Its aim is to make useful research easier to find and reuse, helping reduce the time and tokens spent asking AI to investigate the same topic again. By bringing together current news, research, projects and developments, the community can also help people discover work and topics they did not know to look for.

Start at **[devtrellis.org](https://devtrellis.org)**: open a title or choose **Read research** to read the full shared report and follow its GitHub, project and original source links. Check its date, sources, scope and limitations. If it answers your question, reuse it; if something is missing or outdated, research that part with your own model. Reading on the site needs no account, installation, API key or AI assistant. Reading and importing an existing report do not call a model.

Your personal research stays on your computer. When it is ready, choose **Keep local** or **Preview and share**: review the exact text, sources, model details and content permission before sending a copy, anonymously by default, for moderation. Sharing is optional and requires neither an account nor manual file transfer. Finding, checking, reusing and contributing should build a useful shared library. The [product vision and roadmap](PLATFORM.md) distinguishes current features from planned work.

## Two ways to use it

This is an early alpha, previously named UFUK. Legacy `UFUK_*` environment variables, database keys and the `ufuk-share` format remain compatible. The community service is installed and operated separately. Living topic pages, an hourly community researcher and publisher connectors remain planned.

| Personal workspace | Community service |
| --- | --- |
| Follow sources and save interests, projects and research locally | Browse published research, projects and developments |
| Use your own optional model provider | Read without a model account or contribution requirement |
| Preview and send selected research directly; keep everything else local | Receive account-free contributions; moderators review publication |
| Keep private research on your computer | Version corrections and withdraw publications |

The developer space, advanced workspace and community screens offer English and Turkish. Source and contributor text keeps its original language; interface language does not rewrite other people's work. Model report language is a separate provider setting.

An optional [MCP connection](MCP.md) lets compatible assistants search/read existing research and offer a contribution with an explicit preview and approval. Reuse may reduce repeated model work; no particular token or time saving has been measured.

## Install

Python **3.12** is the tested runtime. Node.js is only needed for JavaScript tests. No Docker, administrator account or model subscription is needed for local browsing and drafts. Run from the extracted source directory on Windows:

```powershell
python -X utf8 kur.py
.\.venv\Scripts\python.exe -X utf8 calistir.py sun --elle-toplama
```

Open **http://127.0.0.1:8781/platform**. Stop with Ctrl+C. Setup creates `.venv`, installs `requirements-lock.txt` and checks dependencies without deleting application data. `python -X utf8 kur.py --check` checks an existing environment without installing anything.

Unix equivalents are `python3 kur.py` and `.venv/bin/python calistir.py sun --elle-toplama`. See release notes for tested operating systems; a command example is not platform validation.

`--elle-toplama` disables scheduled collection and research. A fresh installation has no community contributions. To fetch configured public sources explicitly:

```powershell
.\.venv\Scripts\python.exe -X utf8 calistir.py topla
```

Collection makes outbound requests. Reading stored content does not spend model quota. Starting without `--elle-toplama` enables scheduled collection and the configured research workflow; review settings first. The local service binds to **127.0.0.1** for one person. Shared access uses the separate [community service](COMMUNITY.md). Windows desktop launchers retain the legacy names `RADAR-BASLAT.cmd` and `RADAR-DURDUR.cmd`.

## Data and updates

The default personal database is `data/radar.sqlite3`. To keep data outside extracted release folders, use a stable `--db` path for every command:

```powershell
.\.venv\Scripts\python.exe -X utf8 calistir.py yedekle --db C:\DevTrellisData\personal.sqlite3
.\.venv\Scripts\python.exe -X utf8 calistir.py sun --elle-toplama --db C:\DevTrellisData\personal.sqlite3
```

Before updating, stop your instance and take a consistent backup with `yedekle`. Extract the new release separately, install its dependencies and use the same database path. Keep the old release and pre-update backup. Rollback after a schema change uses the pre-update backup; preserve the newer database separately so later edits are not discarded.

Do not publish `data/`, backups, `.deps/`, `.venv/`, logs, keys or private research. Release archives contain source and tests, without personal data or the parent repository's history.

## Optional model providers

- **NVIDIA:** summaries, screening and source-based research with your own API key. Free prototype access is available under NVIDIA's account/model conditions; see the [step-by-step guide](GUIDE.md). Use `nvidia_anahtar.py` for hidden key entry, environment variables `UFUK_NVIDIA_API_KEY` / `NVIDIA_API_KEY`, or Windows Credential Manager `ufuk-nvidia` / `api-key`.
- **Claude:** research using your own signed-in CLI; `UFUK_CLAUDE_CLI` selects an executable when needed.
- **Codex:** explicitly selected analysis of sources collected by DevTrellis, using your own signed-in CLI. This does not promise independent web research. The adapter restricts tools and supplies only the selected context. Install/sign in separately following the [official authentication guide](https://learn.chatgpt.com/docs/auth). If detection fails, set `UFUK_CODEX_CLI` to the native CLI executable; Windows requires a `.exe`, not a command shim. Model selection can use your existing CLI setting.

Access, limits and charges are your responsibility. Requests send selected context to that provider; credentials are not included in community sharing. Accounts are not pooled community quotas. Without a provider, reading, source management, drafts, backups and card exchange remain usable. Release notes distinguish simulated adapter tests from live model calls.

## Sharing and licenses

Save a card, choose its content permission and preview all fields before exporting. Research needs a source link; projects need a public project link. Importing previews before saving. Uploading to a community installation is a separate explicit action.

Software is **MIT licensed**, including commercial use. Authors independently choose **CC BY 4.0** or **CC0 1.0** for their own card text. These permissions do not change linked software licenses or source article rights. Link to publications and write your own summary. Account identity, author claims, model output and factual verification are distinct.

## Development

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.cjs
```

Tests use temporary data and fake providers unless documented otherwise; live probes are excluded from release archives. GitHub Actions runs the standard Python and JavaScript suites on Ubuntu and Windows with Python 3.12 and Node.js 22. Optional real Windows launcher tests remain a separate local acceptance step. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [PLATFORM.md](PLATFORM.md), and [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
