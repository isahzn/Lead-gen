# AI Lead Finder (local web app)
Python + FastAPI + SQLite, single-page UI. No Node, no Docker.

## Run (Windows)
1. Double-click **`Start AI Lead Finder`** (the shortcut, or `start.bat` / `python run.py start`). First run auto-installs requirements **including laya (from `vendor/`, no separate laya setup)**, then opens http://127.0.0.1:8000
2. Stop: **`Stop AI Lead Finder`** shortcut (or `stop.bat` / `python run.py stop`). Other: `python run.py setup|status`. Logs: `server.log`.

## Fresh clone checklist (for your friend)
1. `git clone <this-repo>` + `cd` into it. Needs Python 3.10+ only.
2. Double-click **Start AI Lead Finder** — deps + laya install themselves on first run.
3. Optional: set `LAYA_ENABLED=1` in Settings for Laya recommendations (needs `torch` + `python laya_setup.py download`; app works fine without it).
3. Tests: `python -m pytest -q`

## What you need
- Nothing for Demo (MOCK) mode / Add-by-URL / CSV import.
- **Node.js** (already needed for discovery) — installer included at `installers/node-v24.14.1-x64.msi`. Automated discovery runs through the **Playwright MCP**, so no search API key is required. A browser window opens while it searches.
- **SMTP** details only to send for real. Default is `SMTP_MODE=dry` (logs only). Gmail host/port/security come pre-filled — you only type your Gmail address + an app password (Google Account → Security → 2-Step Verification → App passwords).

## Added in this version
- **Multi-page analysis:** homepage + up to 3 contact/about/services/booking pages; each piece of evidence records the page it came from.
- **Email verification:** syntax + DNS (MX, else A record) check, shown in Library/Review; invalid addresses can't be sent. No SMTP probing.
- **Follow-ups:** tab lists leads emailed >= `FOLLOWUP_DAYS` (default 5) ago with no reply, using stored dates whenever the app is open (no background service). One follow-up max by default (`MAX_FOLLOWUPS`). Editable draft, explicit confirm, "Mark replied" stops it.
- **imports/ folder:** drop your own LinkedIn/Sales Navigator CSV exports there and press *Import folder* (Find Leads page).
- `PLAYWRIGHT_AGENT_PROMPT.md`: prompt for the agent that drives Playwright MCP and writes CSVs into `imports/`.
- `LAYA_INTEGRATION.md`: the brief that was used to finish and test Laya (kept for reference).

## Deliberately not built
Logging into LinkedIn with your credentials / scraping it, and scraping Google Maps pages. Both break those sites' rules and risk your accounts. Use the Google Places API provider and the CSV/folder import instead.

## Safety
Swipe right only approves. Send needs a separate confirm dialog. No resend to same address, suppression list, daily limit (default 10), mock leads can't send live.

## Laya (real inference verified on this machine)
- Model: `convaiinnovations/laya`, `multilingual` checkpoint (644 MB), already downloaded into `%USERPROFILE%\.cache\huggingface\hub`, so inference needs no network.
- Setup/repair: `python laya_setup.py download` then `python laya_setup.py test` (one real decision). Settings → **Test Laya** runs the same check through the app.
- Adapter in `app.py` → `laya_assess()` calls `laya.decide(_laya_router(), {"message": evidence_text}, questions=LAYA_Q, return_details=True)`. laya 0.4.7 returns a dict per field (`{'type':'choice','choice':'<option>','probabilities':{...}}`), so the adapter reads `choice` — a substring match would always pick the first option, because every option appears in `probabilities`.
- Laya only picks the **recommended service** (used when its confidence >= 0.6); the raw output (`best_service`, `confidence`, `probabilities`) is stored under `laya_output`, and the 0-100 score stays rule-based. Laya does not browse, discover, or write emails.
- **Vendored:** `vendor/laya-0.4.7-py3-none-any.whl` ships in this repo; `run.py setup` installs it with `--no-deps` (torch is only needed to run inference).
- **Measured here (CPU torch 2.14):** ~15–25 s for the first call (checkpoint load), then **~2 s per lead** through the API. With `LAYA_ENABLED=1` the checkpoint preloads in a background thread at startup (verified: `resident: ['multilingual']`), so the first search doesn't pay the load.
- **Calibration caveat:** Laya's shipped checkpoints are over-confident (laya itself warns it is using fallback temperatures). In live tests it returned 0.74 on a site with a clear mobile problem but 0.99 on one with no problem at all. Read `confidence` as relative, not as a true probability.

## Check your setup
`python run.py check` (or `setup.ps1`) verifies Python, deps, Laya import, torch, database.

## Providers
Automated discovery is **Playwright MCP only**: the app starts the official Playwright MCP server (`@playwright/mcp`) and drives a real browser to search Bing, then analyses each result's site. No search API key, and the browser runs headed because Bing serves unrelated results to a headless browser. Also available: Add URL, CSV import, and Demo (MOCK). No LinkedIn scraping or Maps scraping; add LinkedIn leads via CSV.

## Not tested
Real DNS email verification and live-site fetching from an offline sandbox, real SMTP delivery, browser swipe UI, Windows execution. Playwright MCP discovery was tested live end-to-end on 2026-10-18 (headed browser, Bing); it needs a visible browser window and a working Node.js.
