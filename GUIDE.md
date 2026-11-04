# Research guide

[Türkçe](GUIDE.tr.md) · [Installation](README.md)

## Start with NVIDIA's free prototype API

A paid model subscription is optional. NVIDIA Developer Program members have free hosted API access for prototyping. Availability and quota depend on your account/model; this is not unlimited free production hosting. [NVIDIA FAQ](https://docs.api.nvidia.com/nim/docs/product)

1. Open [NVIDIA Build](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b). DevTrellis currently uses `nvidia/nemotron-3-ultra-550b-a55b`.
2. Choose **Get API Key**, sign in or create a developer account, and copy your personal key. The [official quickstart](https://docs.api.nvidia.com/nim/docs/api-quickstart) describes this flow.
3. After installing DevTrellis, run these commands in its directory. Hidden entry keeps the key out of shell history:

   ```powershell
   .\.venv\Scripts\python.exe -X utf8 nvidia_anahtar.py --save
   .\.venv\Scripts\python.exe -X utf8 calistir.py sun --elle-toplama
   ```

   `--save` uses Windows Credential Manager (`ufuk-nvidia` / `api-key`). To avoid saving, run `nvidia_anahtar.py` without `--save`; it starts the app with a temporary key. On Unix use `.venv/bin/python nvidia_anahtar.py`. Optional `--db` and `--port` select your existing data/local port. The hosted API needs no local GPU or Docker.

4. Open `http://127.0.0.1:8781/platform`, open **Research provider**, select **NVIDIA API**, choose report language and save. Claude/Codex accounts remain optional alternatives.
5. Start a small research request in the advanced workspace. Existing cards cost no model quota. Screening, research and evidence checks can use several calls. Local attempt counters and NVIDIA's own limits both apply.

Authentication/quota failures are shown; the app never silently switches to a paid provider. An existing `UFUK_NVIDIA_API_KEY` or `NVIDIA_API_KEY` environment value takes precedence over the Windows credential entry. Remove stale environment values and restart after changing credentials.

The [API trial terms](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf) restrict production use and redistribution of trial outputs. Check applicable terms before publishing outputs or serving real users; use appropriately licensed access. The software's MIT license does not replace provider terms. Selected text goes to the provider: use public, non-sensitive material. Never put keys in research or cards.

## Models, evidence and repeat research

Reports show the saved model, language, date, sources and evidence checks. Changing providers does not rewrite a past report's model. Older reports without recorded model information remain unknown.

Model name and price are context, not a truth score. Read original sources, dates, quoted passages, unsupported claims and limitations. Automated evidence checks are not independent human verification.

**Research again** opens a separate request. Select another provider first if desired, review the question and submit. The original stays intact; the new request uses your limits and may still find insufficient evidence.

**Create sharing draft** copies a completed report into a local card. Review it, add your author name/content permission, then preview/export. Editing report text marks its old evidence check unverified. Shared metadata is contributor-supplied, not a signed attestation. Version 2 cards carry model metadata; version 1 cards still import. Import a downloaded community card locally to research it again.

## Investigate a social post or news link

1. Open a research request with a concrete question and topic.
2. Add the public HTTPS URL and the claim to check. Example: “Does this robot's published test demonstrate autonomous operation?”
3. Add a short caption, description or transcript you may use, especially for Instagram/video.
4. Inspect the link access status, independent sources and unresolved questions.

Only accessible public page text is read. **Video frames and audio are not analyzed.** Your transcript is unverified context, not proof of authenticity. Login walls, robots restrictions, inaccessible pages and unsafe destinations are reported, never bypassed. Nothing is automatically shared with the community.

Independent discovery uses the configured developer catalogue and OpenAlex, Semantic Scholar and Crossref. It is not comprehensive news/social search. Missing evidence means unresolved; an unrelated paper or the original post does not prove a claim. Events outside this coverage may need sources the application cannot yet find.
