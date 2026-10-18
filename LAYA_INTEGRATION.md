# Task: finish the Laya integration in AI-Lead-Finder

You are an AI coding agent working on a local FastAPI project (`AI-Lead-Finder/`, files: `app.py`, `run.py`, `static/index.html`, `tests/test_core.py`). The user's machine: Windows 10/11, Python 3.13, `laya` 0.4.7 already installed. Do NOT reinstall or overwrite the user's Laya install.

## Already done (don't redo)
- `laya_assess(name, evidence)` in `app.py` calls Laya and returns `{"source","best_service","confidence"}` or `{"error":...}`.
- Off by default. Enabled with env `LAYA_ENABLED=1` (Settings page writes `.env`).
- It only picks the recommended service (used when confidence >= 0.6). The 0-100 score stays rule-based. Raw output is stored in `reasons["laya_output"]`.
- A unit test stubs `laya.decide`, so real inference has **never been run** by the previous author.

## Verified facts about laya 0.4.7 (from its installed source)
- `from laya import Router`; `Router(models=None, device=None, ..., preload=False, default='multilingual', ...)`.
- `Router.predict(state, questions, model=None, task=None, lang=None, min_confidence=None, ...)` -> dict. `state` may be str, dict, or list; the preset examples use `{"message": "..."}`.
- `laya.decide(runner, state, schema=None, *, questions=None, return_details=False, min_confidence=None)`. With `return_details=True` it returns `DecisionResult` with `.values`, `.answers`, `.confidence`, `.probabilities`, `.answer_confidence` (all keyed by field).
- Question format (from `laya.triage_questions()`): `{"field": {"type": "choice", "instructions": "...", "criteria": {"option_key": "description", ...}}}`. Other types exist (`noul`, etc.).
- Requires `torch`, `transformers`, `huggingface_hub`, `safetensors`, `numpy`. `import laya` itself is light; the model loads lazily on first predict.
- `laya.DEFAULT_MODELS` points to HuggingFace repo `convaiinnovations/laya` (variants: english, multilingual, typed-decisions). The Router docstring says models are downloaded and built on first use. Checkpoints are large (all three ~1.16B params), so first run is slow.
- Laya's own docs say shipped checkpoints are over-confident (multilingual has no fitted temperatures), so treat `answer_confidence` as relative, not as a true probability.

## What you need to do
1. **Weights:** on the user's PC, check whether the checkpoints are already cached (`%USERPROFILE%\.cache\huggingface\hub`, look for `models--convaiinnovations--laya`). If cached, set `HF_HUB_OFFLINE=1` so nothing is fetched. If not cached, a one-time download from HuggingFace is unavoidable unless the user has the weights locally; ask them. Inspect `laya.Router`'s `models=`, `standalone_repos=` and `revisions=` arguments in the installed source to see whether local paths are supported, and use that if so.
2. **torch:** check `python -c "import torch"`. If missing, tell the user and install the CPU build only if they agree (large download).
3. **Smoke test** with the real model: 
   ```python
   import laya
   r = laya.Router()   # keep max_loaded default
   res = laya.decide(r, {"message": "Business: X. Observations: No <meta viewport> tag found"},
                     questions=__import__("app").LAYA_Q, return_details=True)
   print(res.answers, res.answer_confidence)
   ```
   Confirm the shape of `res.answers["best_service"]`. `laya_assess` currently does `str(answer)` and looks for a criteria key inside it. If the real shape differs (e.g. dict or index), fix the parsing and update the stub test to match the real shape.
4. **Latency:** first call loads the model (seconds). Consider `Router(preload=True)` created once at startup in a background thread so the search request isn't blocked, or keep the lazy load and make the UI say "Laya loading…".
5. **Settings page:** add a "Test Laya" button (`POST /api/laya/test`) that runs the smoke test and returns the real result or error. Never report Laya as working unless that call succeeded.
6. **Keep honesty rules:** Laya does not browse, discover leads, or write emails. Keep its output separate from rule-based scoring in the UI. Do not invent API methods; read the installed source.
7. Run `python -m pytest -q` and `python run.py check`. Report exactly what was and wasn't tested.

## Don'ts
- Don't add LinkedIn login automation or CAPTCHA bypass.
- Don't send real emails in tests (`SMTP_MODE=dry`).
- Don't commit `.env` or the SQLite DB.
