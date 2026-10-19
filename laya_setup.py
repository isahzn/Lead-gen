"""Download Laya's weights and prove real inference works.

Usage:
    python laya_setup.py download [--model multilingual|english|typed-decisions]
    python laya_setup.py test     [--model multilingual|english|typed-decisions]

`download` pulls only the files the chosen checkpoint needs from
https://huggingface.co/convaiinnovations/laya into the standard HuggingFace cache
(%USERPROFILE%\\.cache\\huggingface\\hub), so Laya can load it offline afterwards.
`test` runs one real decision through laya.decide() and prints the answer.

Laya is a classifier, not a chat model: it fills in answers to a fixed set of
questions. Nothing here touches the lead database.
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
REPO = "convaiinnovations/laya"
SUBFOLDERS = {"english": "", "multilingual": "multilingual", "typed-decisions": "typed-decisions"}
# The exact file set laya.agent.Agent asks snapshot_download for.
FILES = ("rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*")


def download(model: str) -> str:
    """Fetch the checkpoint into the HuggingFace cache and return the snapshot dir."""
    from huggingface_hub import snapshot_download

    prefix = f"{SUBFOLDERS[model]}/" if SUBFOLDERS[model] else ""
    print(f"Downloading {REPO} ({model}) -- about {0.7 if model == 'multilingual' else 0.9} GB...")
    started = time.time()
    path = snapshot_download(REPO, allow_patterns=[prefix + f for f in FILES])
    print(f"Done in {time.time() - started:.0f}s -> {path}")
    return path


def test(model: str) -> int:
    """Run one real Laya decision. Returns a process exit code."""
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    import laya

    sys.path.insert(0, str(ROOT))
    import app

    print(f"Loading Laya ({model})... first load is slow on CPU.")
    started = time.time()
    router = laya.Router(default=model)
    res = laya.decide(
        router,
        {"message": "Business: a London dental practice. Observations: No <meta viewport> "
                    "tag found; site is not mobile friendly; pages look dated."},
        questions=app.LAYA_Q,
        return_details=True,
    )
    elapsed = time.time() - started
    print("answers:", res.answers)
    print("answer_confidence:", res.answer_confidence)
    print(f"took {elapsed:.1f}s")
    if not res.answers.get("best_service"):
        print("FAIL: Laya returned no best_service answer.")
        return 1
    print("OK: real Laya inference worked.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["download", "test"])
    ap.add_argument("--model", default="multilingual", choices=sorted(SUBFOLDERS))
    args = ap.parse_args()
    if args.action == "download":
        download(args.model)
    else:
        raise SystemExit(test(args.model))
