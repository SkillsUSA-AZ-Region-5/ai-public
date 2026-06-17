# SkillsUSA Knowledge Workflow

This folder holds the SkillsUSA ingestion workflow. Source PDFs, extraction
output, and generated helper cards are local artifacts and are ignored by git.

## Folders

- `output/`: MinerU extraction output, rebuilt locally from PDFs.
- `knowledge-cards-generated/`: generated helper cards for OpenWebUI retrieval,
  rebuilt from `output/`; do not hand-edit them.
- `knowledge-cards-equipment-stage/`: temporary local staging folder used when
  only equipment cards need to be replaced.

## Why Helper Cards Exist

OpenWebUI retrieval can over-match generic words such as `equipment`, `tools`,
and `bring` across many competitions. The generated cards add contest aliases
near the relevant section so a question such as `IOT equipment` routes to
`Internet of Things (IOT) Smart Home` instead of Internetworking or Technical
Computer Applications.

The original extracted Markdown remains in OpenWebUI. The helper cards are an
additional retrieval layer, not a replacement for the source documents.

## Add Or Update PDFs

1. Put the new or changed PDFs under this folder or another working folder.
2. Run MinerU. Keep `jobs` at `1` for GPU/VLM work.

```powershell
cd C:\Users\<you>\Documents\local-ai-stack
stack profile extract-gpu
stack mineru bulk C:\Users\<you>\Documents\local-ai-stack\skillsusa `
  --output C:\Users\<you>\Documents\local-ai-stack\skillsusa\output `
  --backend vlm-auto-engine `
  --max-pages 1000 `
  --jobs 1 `
  --recursive
```

Use `--force` only when you intentionally want to reprocess PDFs that already
have completed output.

## Remove Old Documents

Remove or move the old PDF and its extracted output folder from `output/`.
Then rebuild the generated cards and replace the OpenWebUI generated cards as a
set. This prevents stale generated cards from removed documents staying in the
Knowledge collection.

## Rebuild And Replace Generated Cards

Set `OPENWEBUI_API_TOKEN` in the current shell or pass `--token` to the scripts.
Do not write tokens into this repo.

```powershell
cd C:\Users\<you>\Documents\local-ai-stack

# 1. Regenerate helper cards from MinerU Markdown.
stack skillsusa generate-cards `
  --source C:\Users\<you>\Documents\local-ai-stack\skillsusa\output `
  --output C:\Users\<you>\Documents\local-ai-stack\skillsusa\knowledge-cards-generated `
  --clean

# 2. Remove old generated cards from OpenWebUI, leaving source docs alone.
C:\Users\<you>\Documents\local-ai-stack\memori\.venv\Scripts\python.exe `
  openwebui\remove_knowledge_files.py `
  --knowledge "SkillsUSA 2024-26" `
  --pattern "^SkillsUSA .* Card\.md$"

# 3. Import the regenerated helper cards.
stack openwebui import-knowledge `
  C:\Users\<you>\Documents\local-ai-stack\skillsusa\knowledge-cards-generated `
  --knowledge "SkillsUSA 2024-26" `
  --group skillsusa `
  --force
```

After import, use OpenWebUI admin tools to reindex Knowledge, or call the API:

```powershell
$headers = @{ Authorization = "Bearer $env:OPENWEBUI_API_TOKEN" }
Invoke-WebRequest http://localhost:3000/api/v1/knowledge/reindex `
  -Method POST `
  -Headers $headers `
  -UseBasicParsing
```

## Equipment Card Fast Patch

If only equipment/tool matching changed, regenerate all cards but replace just
the equipment cards:

```powershell
cd C:\Users\<you>\Documents\local-ai-stack
stack skillsusa generate-cards --clean

$stage = "skillsusa\knowledge-cards-equipment-stage"
if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
Get-ChildItem skillsusa\knowledge-cards-generated -Filter "* - Equipment and Materials Card.md" |
  Copy-Item -Destination $stage

C:\Users\<you>\Documents\local-ai-stack\memori\.venv\Scripts\python.exe `
  openwebui\remove_knowledge_files.py `
  --knowledge "SkillsUSA 2024-26" `
  --pattern "^SkillsUSA .* - Equipment and Materials Card\.md$"

stack openwebui import-knowledge `
  C:\Users\<you>\Documents\local-ai-stack\skillsusa\knowledge-cards-equipment-stage `
  --knowledge "SkillsUSA 2024-26" `
  --group skillsusa `
  --force
```

Then reindex.

## Verify Retrieval

### Automated smoke suite (preferred)

`stack skillsusa smoke` queries OpenWebUI's retrieval endpoint directly (no LLM in
the loop, so it is fast and deterministic) and checks that each query surfaces the
expected competition's card. Each case scores PASS (expected card ranks first),
WEAK@n (in top-k but not first), or FAIL (not retrieved); the command exits with
the FAIL count.

```powershell
$env:OPENWEBUI_API_TOKEN = "<your OpenWebUI API key>"   # never commit this
stack skillsusa smoke                 # 22 cases, all competitions, confusable clusters
stack skillsusa smoke --raw           # also dump the top-k sources per query
stack skillsusa smoke --cases skillsusa\smoke-cases-hard.json   # alias-only, no contest name
```

Cases live in `skillsusa/smoke-cases.json` (names the contest) and
`skillsusa/smoke-cases-hard.json` (domain/alias terms only, no contest name, the
real disambiguation stress test). Add a case as `{query, expect, note}` where
`expect` is matched against the retrieved source filename. Run this after every
reindex; both tiers should be all-PASS.

### Manual spot check

Use a fresh OpenWebUI chat after reindexing (old chats can carry stale context):

```text
What equipment or tools do I need to bring for the IOT competition?
```

Expected source `SkillsUSA Internet of Things (IOT) Smart Home - Equipment and
Materials Card.md`; the answer should include laptop computer, surge protector,
screwdrivers, multimeter, cable tester, RJ11/RJ45 crimpers, zip ties, safety
glasses, socket set, wrench set, and the required resume.

## Gotchas

- Generated helper cards must be replaced as a set after adding or removing
  documents; otherwise stale cards can remain in OpenWebUI.
- OpenWebUI may keep old vector chunks if a file is reuploaded with similar
  content. Removing matching generated files first avoids most stale-vector
  issues.
- The helper-card generator is SkillsUSA-specific. Other document sets need
  their own generator or a generic workflow.
- Keep source docs and helper cards conceptually separate. A cleaner future
  setup would use one Knowledge collection for full source docs and another for
  generated retrieval cards.
