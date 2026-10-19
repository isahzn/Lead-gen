import os, tempfile, sys
os.environ["LEADS_DB"] = tempfile.mktemp(suffix=".db"); os.environ["SMTP_MODE"] = "dry"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi.testclient import TestClient
import app as A
REAL_CHECK = A.check_email
A.check_email = lambda e: ('invalid' if e and '@' not in e else 'mx_ok') if e else ''
c = TestClient(A.app)
def test_qualify_and_draft():
    s, conf, _ = A.qualify(["no_viewport"], [{"kind": "verified"}] * 3, "a@b.com", "")
    assert s > 40
    sub, body, flag = A.draft("X", "https://x.com", ["no_viewport"], [{"kind": "verified", "fact": "a"}] * 2, "a@b.com")
    assert sub and "stop" in body and not flag
    assert A.draft("X", "u", [], [], "")[2]  # insufficient evidence flagged
def test_flow_dedupe_confirm_suppress():
    assert c.post("/api/search", json={"niche": "t", "location": "l", "provider": "demo"}).json()["added"] == 2
    assert c.post("/api/search", json={"niche": "t", "location": "l", "provider": "demo"}).json()["added"] == 0  # dupes
    L = c.get("/api/leads?status=qualified").json(); i = L[0]["id"]
    assert c.post(f"/api/leads/{i}/send", json={"confirm": True}).status_code == 409  # not approved
    c.post(f"/api/leads/{i}/approve")
    assert c.post(f"/api/leads/{i}/send", json={}).status_code == 400  # no confirm
    assert c.post(f"/api/leads/{i}/send", json={"confirm": True}).json()["mode"] == "dry"
    assert c.post(f"/api/leads/{i}/send", json={"confirm": True}).status_code == 409  # no resend
    j = L[1]["id"]; c.post(f"/api/leads/{j}/approve"); c.post("/api/suppress", json={"email": L[1]["email"]})
    assert c.post(f"/api/leads/{j}/send", json={"confirm": True}).status_code in (400, 409)
def test_csv_import_and_status():
    r = c.post("/api/import", files={"file": ("a.csv", b"name,website,email\nAcme,acme.test,hi@acme.test\n")}).json()
    assert r["imported"] == 1
    assert c.get("/api/status").json()["ok"]
def _laya_result(choice, conf=0.74):
    """The exact shape laya 0.4.7 returns (verified by real inference), not the guessed one."""
    import types
    probs = {k: (conf if k == choice else round((1 - conf) / 2, 4)) for k in A.LAYA_Q["best_service"]["criteria"]}
    return types.SimpleNamespace(
        answers={"best_service": {"type": "choice", "choice": choice, "probabilities": probs,
                                  "confidence": 0.35, "answer_confidence": conf}},
        answer_confidence={"best_service": conf})

def test_laya_adapter_parsing(monkeypatch):
    import laya, types
    os.environ["LAYA_ENABLED"] = "1"; A._router = object()
    # Every option appears in `probabilities`, so a substring match on the whole answer would
    # always return the first criteria key -- prove the picked `choice` wins instead.
    monkeypatch.setattr(laya, "decide", lambda *a, **k: _laya_result("automation_chatbot", 0.81))
    out = A.laya_assess("X", [{"fact": "No <meta viewport> tag found"}])
    assert out["best_service"] == "automation_chatbot" and out["confidence"] == 0.81
    assert out["source"] == "laya" and out["probabilities"]["automation_chatbot"] == 0.81
    # Older/simple shape: a bare option string still parses.
    monkeypatch.setattr(laya, "decide", lambda *a, **k: types.SimpleNamespace(
        answers={"best_service": "website_redesign"}, answer_confidence={"best_service": 0.91}))
    assert A.laya_assess("X", []) == {"source": "laya", "best_service": "website_redesign", "confidence": 0.91}
    monkeypatch.setattr(laya, "decide", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert "error" in A.laya_assess("X", [])
    os.environ["LAYA_ENABLED"] = "0"; assert A.laya_assess("X", []) is None

def test_laya_test_endpoint(monkeypatch):
    import laya
    monkeypatch.setattr(A, "_laya_router", lambda: object())   # never load the 644 MB checkpoint in tests
    monkeypatch.setattr(laya, "decide", lambda *a, **k: _laya_result("no_clear_need", 0.55))
    r = c.post("/api/laya/test").json()
    assert r["ok"] and r["answer"] == "no_clear_need" and r["confidence"] == 0.55 and "seconds" in r
    monkeypatch.setattr(laya, "decide", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    bad = c.post("/api/laya/test")
    assert bad.status_code == 400 and "boom" in bad.json()["detail"]

def test_gmail_smtp_defaults(monkeypatch):
    for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURE", "SMTP_USER", "SMTP_PASS", "SMTP_FROM"):
        monkeypatch.delenv(k, raising=False)
    s = c.get("/api/settings").json()
    assert s["SMTP_HOST"] == "smtp.gmail.com" and s["SMTP_PORT"] == "587" and s["SMTP_SECURE"] == "starttls"
    cfg = A.smtp_cfg()
    assert cfg["host"] == "smtp.gmail.com" and cfg["port"] == 587 and cfg["from"] == ""
    try: A.smtp_send("a@b.c", "s", "b"); assert False, "should refuse without creds"
    except ValueError as e: assert "app password" in str(e)
    assert c.post("/api/smtp/test").status_code == 400  # asks for address + app password, no network attempted
    monkeypatch.setenv("SMTP_USER", "me@gmail.com")
    assert A.smtp_cfg()["from"] == "me@gmail.com"  # FROM falls back to USER
def test_playwright_mcp_link_parsing():
    import playwright_mcp as M
    redirect = "https://www.bing.com/ck/a?!&&p=abc&u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9jb250YWN0&ntb=1"
    assert M._unwrap_bing(redirect) == "https://example.com/contact"
    raw = '### Result\n[\n  "' + redirect + '",\n  "https://direct.test/",\n  "bad"\n]'
    assert M._parse_links(raw) == ["https://example.com/contact", "https://direct.test/"]

def test_search_uses_playwright_mcp(monkeypatch):
    import playwright_mcp
    class FakeMCP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def search(self, query, limit=10): return ["https://mcp-found.test/", "https://facebook.com/skip"]
    monkeypatch.setattr(playwright_mcp, "PlaywrightMCP", FakeMCP)
    monkeypatch.setattr(A, "analyze", lambda *a, **k: {"url": "https://mcp-found.test/", "title": "Found Co", "email": "a@mcp-found.test",
        "email_check": "mx_ok", "phone": "", "problems": [],
        "evidence": [{"kind": "verified", "fact": "x", "source": "https://mcp-found.test/"}]})
    r = c.post("/api/search", json={"niche": "n", "location": "l", "provider": "playwright", "count": 1}).json()
    assert r["added"] == 1 and any("Playwright MCP" in line for line in r["log"])
    assert c.post("/api/search", json={"niche": "n", "location": "l", "provider": "brave"}).status_code == 400

def test_multipage_and_email_syntax(monkeypatch):
    import types
    pages = {"https://s.test/": '<title>S</title><a href="/contact">c</a><footer>&copy; 2015</footer>', "https://s.test/contact": 'mail <a>info@s.test</a>'}
    monkeypatch.setattr(A.httpx, "get", lambda u, **k: types.SimpleNamespace(text=pages[u], url=u))
    out = A.analyze("https://s.test/")
    assert out["email"] == "info@s.test" and "old_copyright" in out["problems"] and any("Pages checked: 2" in e["fact"] for e in out["evidence"])
    assert any(e["source"].endswith("/contact") for e in out["evidence"] if "email" in e["fact"])
def test_check_email_syntax():
    real = REAL_CHECK
    assert real("not-an-email") == "invalid" and real("") == ""
def test_followup_flow():
    c = TestClient(A.app); db = A.db()
    i = A.upsert({"name": "FU Co", "website": "fu.test", "email": "a@fu.test", "subject": "Hi", "body": "b", "status": "sent", "evidence": [], "problems": []})
    old = (A.dt.datetime.now() - A.dt.timedelta(days=6)).isoformat(timespec="seconds")
    db.execute("INSERT INTO sends(lead_id,to_email,subject,result,mode,ts,kind) VALUES(?,?,?,?,?,?,?)", (i, "a@fu.test", "Hi", "sent", "dry", old, "initial")); db.commit()
    assert [f["id"] for f in c.get("/api/followups").json()] == [i]
    assert c.post(f"/api/leads/{i}/followup-send", json={}).status_code == 400
    assert c.post(f"/api/leads/{i}/followup-send", json={"confirm": True}).json()["ok"]
    assert c.get("/api/followups").json() == []   # limit reached
    assert c.post(f"/api/leads/{i}/followup-send", json={"confirm": True}).status_code == 409
