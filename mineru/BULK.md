# MinerU Bulk PDF Extraction

Use the GPU/VLM profile first:

```powershell
stack profile extract-gpu
```

Then process a folder:

```powershell
stack mineru bulk "D:\pdfs" --output "D:\pdfs\mineru-results"
```

Defaults:

- API: `http://localhost:8000`
- backend: `vlm-auto-engine`
- pages: `0..999` (1000 pages)
- table recognition: on
- display formula recognition: on
- image analysis: on
- result format: zip when MinerU returns one
- concurrency: `--jobs 1`

Useful options:

```powershell
stack mineru bulk "D:\pdfs" --recursive
stack mineru bulk "D:\pdfs" --dry-run
stack mineru bulk "D:\pdfs" --force
stack mineru bulk "D:\pdfs" --jobs 2
```

The raw script is also callable:

```powershell
python C:\Users\<you>\Documents\local-ai-stack\mineru\bulk_mineru.py "D:\pdfs"
```

Output layout:

- One folder per source PDF under the output directory.
- `submit.json`: raw `/tasks` response.
- `status.json`: final task status response.
- `result.zip` or `result.json`: downloaded `/tasks/{task_id}/result`.
- `extracted\`: unzipped result contents when MinerU returns a zip.
- `manifest.jsonl`: append-only run log, useful for resume/debug.

The script reads `MINERU_AUTH_USER` and `MINERU_AUTH_PASS` from the stack `.env`.
