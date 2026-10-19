"""Usage: python run.py start | stop | status | setup | check"""
import os, subprocess, sys, time, webbrowser, signal, urllib.request
from pathlib import Path
ROOT = Path(__file__).parent; PID = ROOT / ".server.pid"; PORT = int(os.environ.get("PORT", "8000")); URL = f"http://127.0.0.1:{PORT}"
def up():
    try: return urllib.request.urlopen(URL + "/api/status", timeout=2).status == 200
    except Exception: return False
def setup():
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
    # Laya lives in ./vendor/ so a clone needs no separate laya setup.
    # --no-deps: laya's code is installed, but torch etc. are NOT forced
    # (they're only needed on machines that actually run Laya inference).
    try:
        import laya  # noqa
        print("laya already installed.")
    except ImportError:
        wheels = sorted((ROOT / "vendor").glob("laya-*.whl"))
        if not wheels: raise SystemExit("No laya wheel found in ./vendor/")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "--no-deps", str(wheels[-1])])
        print("laya installed from ./vendor/.")
    if not (ROOT / ".env").exists(): (ROOT / ".env").write_text((ROOT / ".env.example").read_text())
    print("Setup done.")
def check():
    import importlib, platform
    ok = lambda b: "OK " if b else "MISSING"
    print("Python", platform.python_version(), "(need 3.10+):", ok(sys.version_info >= (3, 10)))
    for m in ("fastapi", "uvicorn", "httpx", "dotenv", "multipart"):
        try: importlib.import_module(m); print(m, ok(True))
        except ImportError: print(m, ok(False), "-> run: python run.py setup")
    try:
        import laya; from importlib.metadata import version; print("laya", version("laya"), ok(hasattr(laya, "Router")), "(Router present)")
    except ImportError as e: print("laya MISSING:", e)
    try: import torch; print("torch", torch.__version__, "OK (needed only if LAYA_ENABLED=1)")
    except ImportError: print("torch MISSING (Laya inference disabled; app still works)")
    sys.path.insert(0, str(ROOT)); import app; c = app.db(); c.execute("select count(*) from leads"); print("database OK:", app.DB)
    try:
        s = app.laya_status()
        print("laya weights:", "cached" if s.get("weights_cached") else "MISSING -> python laya_setup.py download")
    except Exception as e: print("laya weights: unknown:", e)
    print("server:", "up" if up() else "down (run start)")
def start():
    if up(): print("Already running:", URL); return webbrowser.open(URL)
    try: import fastapi, uvicorn, httpx, dotenv, multipart  # noqa
    except ImportError: setup()
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log = open(ROOT / "server.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT)], cwd=ROOT, stdout=log, stderr=log, creationflags=flags)
    PID.write_text(str(p.pid))
    for _ in range(40):
        if up(): print("Running at", URL); return webbrowser.open(URL)
        if p.poll() is not None: break
        time.sleep(.5)
    print("Failed to start. See server.log:\n", (ROOT / "server.log").read_text()[-1500:])
def stop():
    if not PID.exists(): print("Not running (no pid file)."); return
    pid = int(PID.read_text())
    try:
        if os.name == "nt": subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else: os.kill(pid, signal.SIGTERM)
    except ProcessLookupError: pass
    PID.unlink(missing_ok=True); print("Stopped.")
if __name__ == "__main__":
    {"start": start, "stop": stop, "setup": setup, "check": check, "status": lambda: print("up" if up() else "down")}.get(sys.argv[1] if len(sys.argv) > 1 else "start", start)()
