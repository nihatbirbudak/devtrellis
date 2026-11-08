# Local human contribution exercise

This helper prepares an isolated, disposable community on `127.0.0.1:18794` and a review service on `127.0.0.1:18795`. It does not install anything, modify an existing MCP configuration, call a model, fetch sources or contact the public service. The sample is a synthetic English document, not a research finding or an NVIDIA output.

## Scope of acceptance

The pilot uses the product's community API, contribution validation, frozen preview, consent controls, OS credential vault, delivery, moderation, exact report versions and withdrawal code. Only the destination and transport are adapted for this exercise. The helper changes the destination in its own process memory and rewrites the fixed destination in the served review assets. Product and installed files are not edited. The transport rejects every destination except the selected numeric loopback address and port; source-memory endpoints are disabled.

This is partial, local acceptance. It is not acceptance of the installed r5 package, a real Claude account, native MCP Apps buttons, the production proxy/CDN or the live service. The normal installed MCP connection still targets `https://devtrellis.org`; do not use its **Send for review** button for this local test. A localhost personal application is not, by itself, a localhost community.

## Prepare and run

Use the project's existing Python environment. Dependencies are the application and optional MCP dependencies; this helper never installs them. Choose a new directory outside the repository. An existing or unrecognized directory, symbolic link, junction or hard-linked data file is rejected.

```powershell
python -B -X utf8 araclar/r0-pilot.py --directory C:/DevTrellisR0Pilot prepare
python -B -X utf8 araclar/r0-pilot.py --directory C:/DevTrellisR0Pilot start
```

Leave that terminal open. Open `http://127.0.0.1:18795` yourself. Stop only this server with Ctrl+C in its terminal. No automatic publication, contribution decision or moderator login is performed. Previews expire after 30 minutes; restarting the pilot creates two new previews while preserving existing delivery records.

## Prepared Windows launcher

For a prepared pilot folder containing `pilot.ps1`, use a separate Windows PowerShell process. This works when the normal execution policy is Restricted; Bypass applies only to that child process and does not change system or CurrentUser policy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<pilot-folder>\pilot.ps1" -Action start
```

The prepared launcher must verify the accepted export's full file manifest on every action, including the optional MCP launch. Its runtime is a separate copy pinned to the accepted revision; it must not execute from a changing development worktree. A manifest or file mismatch stops before the pilot runs. Keep the existing `pilot-data` directory when updating the launcher. The prepared folder's `adimlar.md` contains the actual path and commands for start, snapshot, status and hidden moderator password input. If a command fails, copy the error and stop; do not change execution policy or try unrelated repair commands.

The optional MCP configuration uses the same wrapper with `-Action mcp` and the same pinned runtime. It is a later, separate exercise, not a prerequisite for the first local human run.

## Human sequence

1. In another interactive terminal, create the local reviewer. The standard product CLI prompts twice with hidden input; a terminal unable to hide the password is rejected. Only a salted password hash enters the pilot database. Do not use a production password.

   ```powershell
   python -B -X utf8 araclar/r0-pilot.py --directory C:/DevTrellisR0Pilot moderator-add
   ```

2. Capture `before`, open the first preview, verify its destination is the loopback community and choose **Keep local**. Capture `after-keep`. Both snapshots must have zero community entries, zero local deliveries and zero submission/write requests. The second must contain a successful `discard` action. The script cannot prove who clicked; the observer records that separately.
3. Open the second preview. Inspect all fields, select the two permission checkboxes and click **Send for review** once. Capture `after-share`. Expect one delivery, one `pending` community entry and no published revision. A repeated network request is not a second card: count entries and delivery IDs as well as requests.
4. At `http://127.0.0.1:18794`, use **Moderator sign-in**, username `r0-reviewer` and your password. Open **Review**, read the document and add a note clearly stating it is a local synthetic exercise. Choose **Publish** and confirm yourself. Capture `after-publish`.
5. Open the card in the reader and choose **Permanent link to this record**. The link opens the exact public revision (`/r/ENTRY_ID/v/PUBLISHED_REVISION`); keep it open for the withdrawal check. You do not need to copy IDs from evidence files. It must show the exercise document. The management `version` increments on decisions and is not necessarily the public content revision.
6. In **My submissions**, refresh status, choose **Withdraw submission** and confirm yourself. Capture `after-withdraw`. The same exact public revision must now return 404. This does not erase downloaded copies.

Capture a snapshot after each stage:

```powershell
python -B -X utf8 araclar/r0-pilot.py --directory C:/DevTrellisR0Pilot snapshot --phase before
```

Allowed phases: `before`, `after-keep`, `after-share`, `after-publish`, `after-withdraw`. Each writes a new timestamped JSON file under `evidence/`. It contains timestamps, report/delivery IDs, version/status fields, counts and operation names. It excludes passwords, cookies, CSRF/approval capabilities, vault keys, report bodies and moderator notes. The network counters describe this pilot process only and reset on restart. Never compare snapshots from different `process_id` values as one continuous run.

For password recovery, run `moderator-reset` in the same interactive terminal. It calls the standard hidden-password reset and revokes that reviewer's prior sessions. Sign in again. No password is accepted as a command argument or written to a log.

## Optional temporary MCP path

The existing MCP adapter accepts a local URL. A separate temporary connection can use:

```powershell
python -B -X utf8 mcp_topluluk.py --local-url http://127.0.0.1:18795
```

Keep the pilot running. Give this connection a distinct name such as `devtrellis-r0`; do not replace an existing configuration. The host-specific connection and account selection remain a human setup step. The skill alone cannot redirect the installed connection. Before any consent action, the panel must show `http://127.0.0.1:18794` as its destination. If it shows the public website, stop.

A model may prepare a synthetic preview and provide its review link. It must never call `contribution_decision`, extract an approval capability, click consent, publish, or withdraw for the human. If the host does not show inline MCP Apps buttons, the human opens the local review link. Record the real app/account/model and whether the buttons appeared; a scripted pilot test does not establish native host behavior.

## Evidence status

Automated tests verify isolation, safe directory ownership, read/preview behavior and delegation to the existing password CLI. They intentionally do not complete a human contribution or a moderator decision. R0 remains pending until a human follows the sequence and records observations.
