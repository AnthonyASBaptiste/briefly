# Briefly

**A local-first filing assistant for legal work.** Briefly watches a folder, reads supported documents on your machine, asks an open-weight model to identify the matter and document type, and files only clear matches. Everything uncertain stays where it is for review.

> Weekend MVP for DEV's Build for a Friend challenge. Built around a lawyer's real habit: download now, file later.

## What works in this MVP

- Local browser dashboard served by Python; no account, cloud service, or telemetry.
- Configurable inbox and matter library, cooldown, file type allow/exclude lists, and confidence threshold.
- PDF, DOCX, TXT, and Markdown text extraction. Scanned PDFs are recognized as unsupported without optional OCR; no silent guess based on an empty extraction.
- Local Ollama chat API with Gemma (or another locally installed model), constrained JSON output.
- High-confidence files are moved into an existing matter folder and document-type subfolder. Low-confidence and unassigned documents remain in the inbox and appear in Review.
- SQLite audit log, duplicate-destination protection, and a manual “Run scan” for a clear demo.
- First launch creates an empty inbox and matter library; it does not seed fictional matters or inspect a real Downloads folder.
- First-pass mode analyzes inbox documents and proposes matter folder names for approval. It never moves files or creates proposed folders without approval.
- An optional fictional demo can be loaded from the dashboard.

## Run it

Requirements: Python 3.10+ and (for AI filing) [Ollama](https://ollama.com/) with `gemma4:e2b-it-qat` pulled locally. Briefly defaults to this compact Gemma 4 E2B build and limits inference context to 4,096 tokens to keep memory use modest. You can choose another installed model in Settings.

```bash
ollama pull gemma4:e2b-it-qat
python3 briefly.py
```

Open <http://127.0.0.1:8765>. First launch creates an empty workspace. Use **Settings** to choose the inbox and matter-library folders, then run **First pass** if the library has no matter subfolders. Start Ollama before asking Briefly to classify documents.

Briefly never creates matter folders based on a model response alone: first-pass suggestions need explicit approval, and normal filing destinations must match folders already inside the configured library. The first pass never moves documents. Briefly never deletes documents. The automatic watcher is off by default; enable it in Settings after checking the paths and rules.

Settings include native folder pickers, a dropdown of locally installed Ollama models, and dropdowns for cooldown, confidence, and watcher interval. If you have no matter folders, first pass can suggest them from documents already in the inbox. Briefly groups repeated suggestions and asks before creating any folder; low-signal documents remain unassigned for manual review.

## How filing decisions work

1. Wait for the configured cooldown and confirm the file size has stopped changing.
2. Skip excluded file types; extract text locally.
3. Send a bounded text excerpt and the known matter folder names to the local Ollama endpoint.
4. Validate the model's JSON and require an exact existing matter match.
5. Move only if the model confidence meets the configured threshold and the destination remains inside the matter library. Otherwise, keep the file in place and put it in Review.
6. Record each decision in the local audit database.

Model confidence is a signal, not a guarantee. The threshold is intentionally configurable and the review queue is part of the normal workflow.

## Privacy

Inference uses the local Ollama service at `127.0.0.1`; document text is not sent to a hosted model. The dashboard binds to localhost. File contents and audit data stay on this machine. Avoid exposing the server or Ollama port to a network.

## Current limits

- No OCR for scanned PDFs, email parsing, cloud drives, multi-user support, undo, or native desktop packaging yet.
- The model can misread legal documents. Review results before relying on them; this is a filing aid, not legal advice or a records-management system.
- PDF extraction uses `pdftotext` when installed. DOCX and text extraction use Python's standard library.
- The watcher polls rather than relying on a platform-specific file notification service.

## Hacktoberfest 2026: Built for a Friend

Briefly was built for the **DEV Hacktoberfest 2026 Challenge ("Build for a Friend")**.

### The Problem
My wife is a practicing attorney. Like many busy professionals, she downloads client files, court orders, disclosure documents, and billing receipts directly into her Downloads folder—intending to file them later. Over time, that folder turns into an unmanageable digital junk drawer where finding a critical filing means searching through hundreds of ambiguously named PDFs like `document (12).pdf`.

### Why Open-Source AI Matters
In the legal profession, attorney-client privilege and confidentiality are non-negotiable. Sending client documents, court pleadings, and sensitive financial data across third-party proprietary AI APIs creates serious ethical, compliance, and privacy risks.

Open-source, open-weight AI fundamentally changes this:
- **Zero data egress**: Using Google's open-weight **Gemma 4** (`gemma4:e2b-it-qat`) running locally via **Ollama**, all document understanding occurs strictly on the attorney's machine (`127.0.0.1`). No cloud endpoints, no telemetry, and no account required.
- **Auditable & constrained**: Strictly constrained JSON schema output guarantees the model only selects from verified, user-approved matter folders.
- **Human-in-the-loop**: The AI suggests; the attorney approves. Low-confidence files remain untouched in the inbox and surface in a transparent review queue.

## Project layout

```text
briefly.py          local server, filing workflow, Ollama integration
static/index.html   dashboard UI
workspace/          local inbox, matter library, settings, and audit database
```

The runtime workspace is created at `workspace/` and is ignored by Git; it contains the inbox, matter library, settings, and local audit database. Sample documents are added only when **Load fictional demo** is selected.

## License

MIT. See `LICENSE`.
