# RepoPilot — Steps 1–5: Discovery, Retrieval, Grounded Analysis, Live Workspace, and Conversations

RepoPilot discovers public GitHub repositories, retrieves a bounded set of relevant text files at the default-branch commit, and uses its configured analysis provider for source-grounded analysis. 

**Providers:**
- **Anthropic** (default, API-based): Available locally and on Vercel
- **IBM Bob** (CLI for local, HTTP API for Vercel):
  - **Local**: Bob Shell 2.0.5 (CLI automation)
  - **Vercel**: HTTP inference API (instance-specific endpoint from bob.ibm.com)

RepoPilot never modifies repositories.

## Run locally

### With Anthropic (default)

1. Create `.env` from `.env.example`.
2. Add your Anthropic API key: `ANTHROPIC_API_KEY=sk-ant-...`
3. Install dependencies: `python3 -m pip install -r requirements.txt`
4. Run: `python3 app.py`
5. Open `http://127.0.0.1:5000`

### With IBM Bob Shell 2.0.5 (local development)

1. Install Bob: `npm install -g bob-shell` (or use existing installation)
2. Create `.env` from `.env.example`
3. Set `ANALYSIS_PROVIDER=bob` and add your `BOB_API_KEY`
4. Install dependencies: `python3 -m pip install -r requirements.txt`
5. Run: `python3 app.py`
6. The factory will auto-detect Bob Shell CLI and use it
7. Open `http://127.0.0.1:5000`

## Deploy to Vercel

### With Anthropic (recommended)

1. Add Vercel environment variables:
   - `ANALYSIS_PROVIDER=anthropic`
   - `ANTHROPIC_API_KEY=sk-ant-...`
   - `GITHUB_TOKEN=github_pat_...` (optional)
2. Deploy: `vercel` or push to GitHub and link to Vercel

### With IBM Bob HTTP API

1. Get your Bob inference endpoint:
   - Log into [bob.ibm.com](https://bob.ibm.com)
   - Navigate to your subscription instance → API Key Management
   - Create an "Inference" type API key
   - Copy the instance-specific endpoint URL (e.g., `https://bob-prod.your-account.ibm.com/api/v1/inference`)
2. Add Vercel environment variables:
   - `ANALYSIS_PROVIDER=bob`
   - `BOB_API_KEY=<your-inference-api-key>`
   - `BOB_INFERENCE_ENDPOINT=<your-instance-endpoint>`
   - `GITHUB_TOKEN=github_pat_...` (optional)
3. Deploy: `vercel` or push to GitHub and link to Vercel

## Discovery API

`POST /api/discover`

```json
{"url":"https://github.com/pallets/flask"}
```

The response contains `repository` metadata, a nested `tree`, `file_statistics`, verified `characteristics`, and `discovery_coverage`. A response with `discovery_coverage.complete: false` must be treated as a partial directory inventory.

Errors use this shape:

```json
{"error":{"code":"invalid_url","message":"Use an HTTPS GitHub repository URL."}}
```

`GET /api/health` reports whether the service started and whether a GitHub token is configured; it never returns credentials.

## Source retrieval API

`POST /api/retrieve`

```json
{
  "url": "https://github.com/pallets/flask",
  "instruction": "Focus on API routes and application startup"
}
```

This response includes all Step 1 discovery fields plus:

- `selected_files`: actual UTF-8 source/configuration contents, each with its path, size, language, retrieval status, selection reasons, and the exact commit SHA.
- `retrieval_coverage`: selected/retrieved counts, skipped-file counts, applied limits, the selected commit SHA, and an explicit complete/partial status.

Selection is deterministic: manifests and build files are prioritized first, then common entry points, documentation, supported text source/configuration files, and paths that match the optional retrieval focus. Binary, generated, dependency-directory, secret-like, `.env`, oversized, and unreadable files are excluded.

Default limits: 24 files, 60,000 bytes per file, and 500,000 bytes total. The complete Step 1 directory tree remains available separately from the files whose contents were retrieved.

## Analysis API

`POST /api/analyze` accepts the same body as `/api/retrieve`. It performs retrieval once, prepares a provider-neutral context from the selected files, then invokes the configured provider. The response contains all retrieval fields plus:

```json
{
  "analysis": {
    "text": "Grounded analysis with `file/path` references",
    "provider": "anthropic",
    "model": "claude-haiku-4-5-20251001",
    "mode": "overview",
    "completed": true,
    "source_references": ["file/path"],
    "source_file_count": 24,
    "commit_sha": "...",
    "retrieval_complete": false
  }
}
```

`ANALYSIS_PROVIDER=anthropic` is the current default. The common `AnalysisRequest` carries the selected mode, optional instruction, verified metadata, commit, selected source files, coverage, and bounded history. The common `AnalysisResponse` carries Markdown, verified source references, optional validated architecture data, provider metadata, and completion status. Future providers implement the small `AnalysisProvider` interface in `backend/providers/base.py`; GitHub retrieval, context preparation, API response format, and frontend rendering do not change.

## Repository overview

The workspace renders verified GitHub metadata separately from generated analysis. `language_statistics` is calculated from GitHub's `/languages` byte counts and contains `total_bytes` plus `{name, bytes, percentage}` entries. The file explorer renders the discovered GitHub tree; large subdirectories are expanded lazily in the browser.

The central grounding and response rules live in `backend/analysis_instructions.md`. The current browser session retains the most recently completed analysis for the “My repositories” navigation item, while submitting the form always starts a new live analysis.

## Live analysis stream

`POST /api/analyze/stream` accepts the same JSON body as `/api/analyze` and returns a standard `text/event-stream` response. Events use `event: <name>` and JSON `data:` lines:

- `status`: genuine GitHub, directory-structure, source-selection, context-preparation, and provider-request stages.
- `repository`: verified GitHub metadata, complete discovered tree, file counts, coverage, and language statistics—sent before provider analysis begins.
- `retrieval`: a real successfully retrieved / selected source-file count.
- `context`: source context is prepared.
- `token`: an actual incremental text chunk from the configured provider.
- `done`: the assembled analysis, retrieval coverage, safe metadata for selected files, and the exact bounded source files supplied to the provider. The browser uses only these returned files for clickable source references and previews.
- `error`: a safe user-facing failure. A failed stream never emits `done`.

The active Anthropic provider uses the SDK's `messages.stream(...).text_stream`; it does not simulate typing. The ordinary `POST /api/analyze` endpoint remains available for compatibility and tests. Browser updates are ignored when their request ID is no longer current, and duplicate submissions are disabled.

## Follow-up conversations and local history

After an analysis completes, the workspace includes a follow-up composer. `POST /api/conversations/follow-up/stream` accepts an opaque `conversation_id`, a question, and the prior browser conversation messages. It emits the same `status`, `retrieval`, `context`, `token`, `done`, and `error` SSE events as the initial analysis.

The server keeps a bounded, process-local source context behind the opaque ID. A follow-up selects files from the original repository tree and fetches them only at the initial analysis commit SHA. Relevant source bodies already in that temporary context are reused; selected missing paths are retrieved with the normal file, total-byte, type, and secret-like-file exclusions. No code is executed.

Completed conversation messages, repository summaries, metadata, language data, and a safely bounded file-tree snapshot are saved under `repopilot-conversations-v1` in the current browser's `localStorage`. Source bodies and API tokens are never stored there. “My repositories” restores saved conversations after a browser refresh. This is deliberately browser-local: there is no list or history API, no authentication, and no cross-user history. The temporary server source cache is lost on server restart or after its bounded capacity is evicted; in that case the UI clearly asks the user to start a new analysis before continuing a saved conversation.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests use fixtures and fake GitHub responses; they do not require network access.
