# AGENTS.md — AI UI Generation Pipeline

## What this is

Demo/prototype: PSD → Unity/Cocos2dx code generation via DeepSeek API.  
Vanilla JS frontend (`index.html`) + FastAPI backend (`backend_api.py`).  
Note: `App.jsx`/`App.css` were dead React code and have been removed.

## Commands

```bash
pip install -r requirements.txt   # install Python deps
python backend_api.py             # start server on :8000
npm start                         # same as above
npm run dev                       # uvicorn with --reload
```

No test/lint/typecheck/format config exists — do not assume any.

## Key files

| File | Role |
|------|------|
| `backend_api.py` | FastAPI app — all endpoints: analyze-psd, generate-code, export-project, health |
| `index.html` | Self-contained frontend (works standalone with demo data, no backend needed) |
| `requirements.txt` | Pinned Python deps |

## Architecture gotchas

- **PSD parsing is now real** — `parse_psd_layers()` uses `psd-tools` to read actual PSD files. Text layers (TypeLayer) detected via `layer.kind == "type"`; text content and font properties extracted from `layer.text` and `layer.engine_dict`.
- `requirements.txt` uses pinned versions — if Python 3.13+, upgrade to unpinned (`>=`).
- **Uses DeepSeek API** via `openai` SDK (`base_url="https://api.deepseek.com"`). The many "Claude" mentions in docs are stale.
- **Requires `DEEPSEEK_API_KEY`** env var. Set via `$env:DEEPSEEK_API_KEY="sk-..."` (PowerShell) or `export DEEPSEEK_API_KEY="sk-..."` (bash).
- `package.json` lists Python packages as `dependencies` — it is metadata only, not a real npm project.
- No Dockerfile exists despite README's Docker section.
