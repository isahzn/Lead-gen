# AI Lead Finder (local web app)
Python + FastAPI + SQLite, single-page UI. No Node, no Docker.

## Run (Windows)
1. Double-click **`Start AI Lead Finder`** (the shortcut, or `start.bat` / `python run.py start`). First run auto-installs requirements **including laya (from `vendor/`, no separate laya setup)**, then opens http://127.0.0.1:8000
2. Stop: **`Stop AI Lead Finder`** shortcut (or `stop.bat` / `python run.py stop`). Other: `python run.py setup|status`. Logs: `server.log`.

## Fresh clone checklist (for your friend)
1. `git clone <this-repo>` + `cd` into it. Needs Python 3.10+ only.
2. Double-click **Start AI Lead Finder** — deps + laya install themselves on first run.
3. Optional: set `LAYA_ENABLED=1` in Settings for Laya recommendations (needs `torch` + a one-time HuggingFace model download; app works fine without it).
3. Tests: `python -m pytest -q`

## What you need
- Nothing for Demo (MOCK) mode / Add-by-URL / CSV import.
- **Brave Search API key** (free tier) in Settings for automated discovery.
- **SMTP** details only to send for real. Default is `SMTP_MODE=dry` (logs only). Gmail host/port/security come pre-filled — you only type your Gmail address + an app password (Google Account → Security → 2-Step Verification → App passwords).

## Added in this version
- **Multi-page analysis:** homepage + up to 3 contact/about/services/booking pages; each piece of evidence records the page it came from.
- **Email verification:** syntax + DNS (MX, else A record) check, shown in Library/Review; invalid addresses can't be sent. No SMTP probing.
- **Follow-ups:** tab lists leads emailed >= `FOLLOWUP_DAYS` (default 5) ago with no reply, using stored dates whenever the app is open (no background service). One follow-up max by default (`MAX_FOLLOWUPS`). Editable draft, explicit confirm, "Mark replied" stops it.
- **imports/ folder:** drop your own LinkedIn/Sales Navigator CSV exports there and press *Import folder* (Find Leads page).
- `PLAYWRIGHT_AGENT_PROMPT.md`: prompt for the agent that drives Playwright MCP and writes CSVs into `imports/`.
- `LAYA_INTEGRATION.md`: brief for your other AI agent to finish and test Laya.

## Deliberately not built
Logging into LinkedIn with your credentials / scraping it, and scraping Google Maps pages. Both break those sites' rules and risk your accounts. Use the Google Places API provider and the CSV/folder import instead.

## Safety
Swipe right only approves. Send needs a separate confirm dialog. No resend to same address, suppression list, daily limit (default 10), mock leads can't send live.

## Laya (verified against the real laya 0.4.7 source)
- Adapter in `app.py` → `laya_assess()` calls `laya.decide(laya.Router(), {"message": evidence_text}, questions=..., return_details=True)`.
- Off by default. Turn on with `LAYA_ENABLED=1` (Settings). Needs `torch` + a model download from HuggingFace on first use (large, slow first run). Install torch yourself if you want it: `pip install torch`.
- Laya only picks the **recommended service** (used when its confidence >= 0.6); its raw output is stored separately under `laya_output`. The 0-100 score is rule-based. Laya does not browse, discover, or write emails.
- **Vendored:** `vendor/laya-0.4.7-py3-none-any.whl` ships in this repo; `run.py setup` installs it with `--no-deps`, so a fresh clone never needs a separate laya install (and never forces a torch download).
- **Tested:** import, API signatures, answer-parsing (with a stub). **Not tested:** real model inference (no torch / no HuggingFace access in my sandbox). If it errors on your PC, the error is saved in `laya_output` and the app falls back to rules.

## Check your setup
`python run.py check` (or `setup.ps1`) verifies Python, deps, Laya import, torch, database.

## Providers
Brave Search (key), Google Places official API (key, finds businesses with websites), Add URL, CSV import, Demo (MOCK). No LinkedIn scraping or Maps scraping; add LinkedIn leads via CSV.

## Not tested
Real DNS email verification and live-site fetching (my sandbox is offline), real Brave/Google calls, real SMTP, browser swipe UI, Windows execution, fetching live sites from my sandbox.
