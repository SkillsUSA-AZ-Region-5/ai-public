# Cline / Roo custom instructions (paste into the extension's "Custom Instructions")

You have a persistent, cross-project memory via the `memori` MCP server.

- At the **start of every task**, call `recall_memory` with a short description of
  what you're about to do, to pull in relevant facts, decisions, and the user's
  preferences from past projects.
- When something durable is established - an architectural decision, a fixed gotcha,
  a user preference, a credential location, a project convention - call
  `record_memory` with a concise one-sentence statement of the fact. Use the default
  `scope="project"`; use `scope="global"` only for facts that are true across ALL
  projects (e.g. a personal preference, a machine-wide path, a standing convention).
- Do **not** record transient/per-task details (file contents, scratch work).
  Record the kind of thing you'd want to know months later on a different project.

## Cline API provider settings (for inference on LM Studio)

- API Provider: **LM Studio** (or "OpenAI Compatible")
- Base URL: `http://localhost:1234/v1`
- Model: `qwen/qwen3.6-35b-a3b`  (load it in LM Studio first)
- API Key: required - use the `OPENAI_API_KEY` value from `local-ai-stack/.env`
  (LM Studio now enforces key auth)
