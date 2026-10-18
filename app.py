import os, re, io, socket, csv, json, sqlite3, smtplib, ssl, datetime as dt
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse, urljoin
import httpx
from dotenv import load_dotenv, set_key
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

ROOT = Path(__file__).parent
ENV = ROOT / ".env"
DB = os.environ.get("LEADS_DB", str(ROOT / "leads.db"))
load_dotenv(ENV)
app = FastAPI(title="AI Lead Finder")
SKIP = ("facebook.","instagram.","yelp.","linkedin.","tripadvisor.","youtube.","twitter.","x.com","wikipedia.","yellowpages.","google.","reddit.")

def env(k, d=""): return os.environ.get(k, d)
def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c
def init():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY, name TEXT, niche TEXT, location TEXT, website TEXT, domain TEXT UNIQUE,
      email TEXT, phone TEXT, evidence TEXT DEFAULT '[]', problems TEXT DEFAULT '[]', service TEXT, score INT DEFAULT 0, confidence TEXT DEFAULT 'low',
      reasons TEXT DEFAULT '{}', subject TEXT DEFAULT '', body TEXT DEFAULT '', draft_flag TEXT DEFAULT '', status TEXT DEFAULT 'new',
      notes TEXT DEFAULT '', tags TEXT DEFAULT '', source TEXT DEFAULT '', is_mock INT DEFAULT 0, created TEXT);
    CREATE TABLE IF NOT EXISTS sends(id INTEGER PRIMARY KEY, lead_id INT, to_email TEXT, subject TEXT, result TEXT, error TEXT, mode TEXT, ts TEXT);
    CREATE TABLE IF NOT EXISTS suppression(email TEXT PRIMARY KEY, reason TEXT, ts TEXT);
    """)
    for t, col in (("leads", "email_check TEXT DEFAULT ''"), ("sends", "kind TEXT DEFAULT 'initial'")):
        try: c.execute(f"ALTER TABLE {t} ADD COLUMN {col}")
        except sqlite3.OperationalError: pass
    c.commit(); c.close()
init()
now = lambda: dt.datetime.now().isoformat(timespec="seconds")

# ---------- Laya (verified status only) ----------
def laya_status():
    try:
        import laya
        from importlib.metadata import version
        r = getattr(laya, "Router", None)
        return {"importable": True, "version": version("laya"), "has_Router": r is not None,
                "router_public_api": [a for a in dir(r) if not a.startswith("_")] if r else [],
                "enabled": env("LAYA_ENABLED") == "1", "used_for": "recommended service (when confidence >= 0.6); score itself is rule-based",
                "needs": "torch + model download from HuggingFace on first use"}
    except Exception as e:
        return {"importable": False, "error": str(e), "enabled": False}


_router = None
# Laya's "system prompt" (Laya is a classifier, not a chat model, so this question definition
# is the entire instruction set it follows). Written to act like Harvey: a consultative analyst
# that leads with value, never fabricates, and scores conservatively — thin or doubtful
# evidence means no_clear_need, never a guess.
LAYA_Q = {"best_service": {"type": "choice",
  "instructions": ("You are Harvey, a consultative sales analyst. Based ONLY on the website observations "
   "in `message`, decide which service would genuinely help this business most. Lead with value, not "
   "assumptions: use only facts stated in the observations — never invent problems, never guess, never "
   "treat a hypothesis as verified. If the evidence is thin, stale, or contradictory, choose no_clear_need. "
   "Missing information means no clear need, not a guess. Be conservative rather than confident."),
  "criteria": {"website_redesign": "the observations show concrete, checkable problems with the site itself (outdated, insecure, or not mobile friendly)",
               "automation_chatbot": "the observations show enquiries or bookings are handled manually (no form, chat, or booking option found)",
               "no_clear_need": "the observations are thin, stale, contradictory, or show no concrete problem — when in doubt, choose this"}}}
LAYA_SERVICE = {"website_redesign": "Website redesign", "automation_chatbot": "AI chatbot / workflow automation"}
def laya_assess(name, evidence):
    """Real Laya call: laya.decide(Router, {'message': text}, questions=..., return_details=True). Needs torch + model download on first use."""
    global _router
    if env("LAYA_ENABLED") != "1": return None
    try:
        import laya
        _router = _router or laya.Router()
        text = f"Business: {name}. Observations: " + "; ".join(e["fact"] for e in evidence)
        r = laya.decide(_router, {"message": text}, questions=LAYA_Q, return_details=True)
        ans = str((getattr(r, "answers", {}) or {}).get("best_service", ""))
        conf = (getattr(r, "answer_confidence", None) or getattr(r, "confidence", {}) or {}).get("best_service")
        key = next((k for k in LAYA_Q["best_service"]["criteria"] if k in ans), None)
        if key is None: return {"error": f"Unrecognised Laya answer: {ans[:60]}", "source": "laya"}
        return {"source": "laya", "best_service": key, "confidence": round(float(conf), 3) if conf is not None else None}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:200], "source": "laya"}

# ---------- Research ----------
PROBLEMS = {
 "no_https": ("the site is served without HTTPS", "Website redesign", "I rebuild small-business sites to be secure, fast and mobile-friendly"),
 "no_viewport": ("the page has no mobile viewport tag, so it likely renders poorly on phones", "Website redesign", "I rebuild small-business sites to be mobile-friendly and fast"),
 "old_copyright": ("the footer copyright year is {y}, which suggests the site hasn't been updated recently", "Website redesign", "I refresh outdated sites so they look current and convert better"),
 "no_form_or_chat": ("I couldn't find a contact form, booking link or chat widget on the homepage", "AI chatbot / workflow automation", "I set up chatbots and automated enquiry/booking follow-up so leads aren't missed"),
}
UA = {"User-Agent": "Mozilla/5.0 LeadFinder (personal research)"}
def _get(url):
    r = httpx.get(url, timeout=12, follow_redirects=True, headers=UA); return r.text, str(r.url)
def check_email(e):
    """Syntax + DNS (MX, else A). No SMTP probing. Returns invalid | mx_ok | unknown | ''."""
    if not e: return ""
    if not re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", e): return "invalid"
    dom = e.split("@")[1]
    try:
        import dns.resolver, dns.exception
        for rt in ("MX", "A"):
            try: dns.resolver.resolve(dom, rt, lifetime=3); return "mx_ok"
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer): continue
        return "invalid"
    except Exception:
        try: socket.gethostbyname(dom); return "mx_ok"
        except socket.gaierror: return "unknown"
def analyze(url, name="", niche="", location=""):
    if not url.startswith("http"): url = "https://" + url
    ev, probs = [], []
    try: html, final = _get(url)
    except Exception as e: return {"error": f"Could not fetch {url}: {e}"}
    pages, host = {final: html}, urlparse(final).netloc
    links = []
    for h in re.findall(r'href=["\']([^"\'#]+)["\']', html, re.I):
        u = urljoin(final, h); p = urlparse(u)
        if p.netloc == host and re.search(r"contact|about|service|book|pricing|quote|appointment", p.path, re.I) and u not in links: links.append(u)
    for u in links[:3]:
        try: pages[u] = _get(u)[0]
        except Exception: pass
    def add(f, kind="verified", src=final): ev.append({"fact": f, "kind": kind, "source": src})
    add(f"Pages checked: {len(pages)} ({', '.join(urlparse(u).path or '/' for u in pages)})")
    t = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = re.sub(r"\s+", " ", t.group(1)).strip() if t else ""
    if title: add(f"Page title: {title[:100]}")
    emails, tel, esrc = [], None, final
    dom = host.replace("www.", "")
    for u, h in pages.items():
        for e in set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", h)):
            if not e.lower().endswith((".png", ".jpg", ".webp", ".svg", ".gif")) and e not in [x[0] for x in emails]: emails.append((e, u))
        tel = tel or re.search(r'href="tel:([+\d\s().-]{6,})"', h, re.I)
    emails.sort(key=lambda x: (dom not in x[0], x[0]))
    if emails: add(f"Public email listed: {emails[0][0]}", src=emails[0][1])
    if tel: add(f"Public phone listed: {tel.group(1).strip()}")
    low = html.lower()
    if not final.startswith("https"): probs.append("no_https"); add("Site loads over HTTP, not HTTPS")
    if "name=\"viewport\"" not in low and "name='viewport'" not in low: probs.append("no_viewport"); add("No <meta viewport> tag found on homepage")
    ys = [int(y) for h in pages.values() for y in re.findall(r"(?:©|&copy;|copyright)[^<]{0,40}?(20\d\d)", h, re.I)]
    if ys and max(ys) <= dt.date.today().year - 3:
        probs.append("old_copyright"); add(f"Latest copyright year in footer: {max(ys)}")
    if not any(re.search(r"<form|calendly|chat|book(ing| now)|whatsapp|tawk|intercom|drift", h.lower()) for h in pages.values()):
        probs.append("no_form_or_chat"); add(f"No form/booking/chat markers on {len(pages)} page(s) checked (hypothesis: enquiries may be handled manually)", "hypothesis")
    em = emails[0][0] if emails else ""
    return {"url": final, "title": title, "email": em, "email_check": check_email(em), "phone": tel.group(1).strip() if tel else "", "evidence": ev, "problems": probs}

def qualify(problems, evidence, email, phone):
    ver = [e for e in evidence if e["kind"] == "verified"]
    fit = min(len(problems), 3) / 3 * 100
    ev_q = min(len(ver), 5) / 5 * 100
    contact = 100 if email else (40 if phone else 0)
    deliver = 100 if any(p in problems for p in ("no_https", "no_viewport", "old_copyright")) else 70 if problems else 20
    value = 60 if problems else 20
    score = round(.3*fit + .2*ev_q + .2*contact + .15*deliver + .15*value)
    conf = "high" if len(ver) >= 4 and email else "medium" if len(ver) >= 2 else "low"
    return score, conf, {"service_fit": round(fit), "evidence_quality": round(ev_q), "contact_available": contact, "deliverable_by_you": deliver, "commercial_value(estimate)": value}

def draft(name, website, problems, evidence, email):
    cand = [p for p in problems if p != "no_form_or_chat" or len(problems) == 1]
    ver = [e for e in evidence if e["kind"] == "verified"]
    if not email or not cand or len(ver) < 2:
        return "", "", "Not enough verified evidence/contact to draft responsibly — research more."
    obs, svc, pitch = PROBLEMS[cand[0]]
    obs = obs.format(y=next((e["fact"].split(": ")[-1] for e in evidence if "copyright" in e["fact"]), ""))
    me = env("SENDER_NAME", "Hassen")
    body = (f"Hi {name or 'there'} team,\n\nI was looking at {website} and noticed {obs}. {pitch}.\n\n"
            f"If it's useful, I can send a short written outline of what I'd change — no obligation. Would that be helpful?\n\n"
            f"If you'd rather not hear from me, just reply \"stop\" and I won't contact you again.\n\n{me}")
    return f"Quick idea for {name or 'your website'}", body, ""

def upsert(d):
    dom = urlparse(d["website"] if d["website"].startswith("http") else "https://" + d["website"]).netloc.replace("www.", "").lower() or d.get("email", "")
    c = db()
    if c.execute("SELECT 1 FROM leads WHERE domain=?", (dom,)).fetchone(): c.close(); return None
    cur = c.execute("""INSERT INTO leads(name,niche,location,website,domain,email,phone,evidence,problems,service,score,confidence,reasons,subject,body,draft_flag,status,source,is_mock,created,email_check)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (d["name"], d.get("niche",""), d.get("location",""), d["website"], dom, d.get("email",""), d.get("phone",""), json.dumps(d.get("evidence",[])), json.dumps(d.get("problems",[])),
       d.get("service",""), d.get("score",0), d.get("confidence","low"), json.dumps(d.get("reasons",{})), d.get("subject",""), d.get("body",""), d.get("draft_flag",""),
       d.get("status","new"), d.get("source",""), d.get("is_mock",0), now(), d["email_check"] if "email_check" in d else check_email(d.get("email",""))))
    c.commit(); i = cur.lastrowid; c.close(); return i

def build_lead(url, name, niche, location, source):
    a = analyze(url, name, niche, location)
    if "error" in a: return None, a["error"]
    name = name or a["title"].split("|")[0].split("-")[0].strip() or urlparse(a["url"]).netloc
    score, conf, reasons = qualify(a["problems"], a["evidence"], a["email"], a["phone"])
    subj, body, flag = draft(name, a["url"], a["problems"], a["evidence"], a["email"])
    svc = PROBLEMS[a["problems"][0]][1] if a["problems"] else ""
    lay = laya_assess(name, a["evidence"]); svc_src = "rules"
    if lay and lay.get("best_service") in LAYA_SERVICE and (lay.get("confidence") or 0) >= 0.6:
        svc, svc_src = LAYA_SERVICE[lay["best_service"]], "laya"
    reasons["recommended_service_by"] = svc_src
    if lay: reasons["laya_output"] = lay
    status = "qualified" if subj and score >= 50 else "new"
    return {"name": name, "niche": niche, "location": location, "website": a["url"], "email": a["email"], "phone": a["phone"], "evidence": a["evidence"],
            "problems": a["problems"], "service": svc, "score": score, "confidence": conf, "reasons": reasons, "subject": subj, "body": body,
            "draft_flag": flag, "status": status, "source": source, "email_check": a["email_check"]}, ""

class SearchReq(BaseModel):
    niche: str; location: str; keywords: str = ""; provider: str = "playwright"; count: int = 10
@app.post("/api/search")
def search(r: SearchReq):
    log = []
    if r.provider == "demo":
        added = 0
        for i, n in enumerate(["Demo Dental (MOCK)", "Demo Plumbing (MOCK)"]):
            added += bool(upsert({"name": n, "niche": r.niche, "location": r.location, "website": f"mock{i}-{r.niche.replace(' ','')}.example", "email": f"info@mock{i}.example",
                    "evidence": [{"fact": "MOCK DATA — not a real business", "kind": "hypothesis", "source": "mock"}], "problems": ["no_viewport"], "service": "Website redesign",
                    "score": 55, "confidence": "low", "subject": "MOCK subject", "body": "MOCK body — dry-run only.", "status": "qualified", "source": "mock", "is_mock": 1, "email_check": "mock"}))
        return {"log": [f"Inserted {added} clearly-labelled MOCK leads (never sendable live)."], "added": added}
    if r.provider != "playwright":
        raise HTTPException(400, f"Unknown provider '{r.provider}'. Discovery runs through the Playwright MCP only.")
    # Discovery via the Playwright MCP: a real browser performs the search, so no
    # search API key is involved. See playwright_mcp.py.
    from playwright_mcp import PlaywrightMCP, MCPError
    queries = [f"{r.niche} {r.location} {r.keywords}".strip(), f"{r.niche} in {r.location} contact",
               f"best {r.niche} {r.location} {r.keywords}".strip()]
    seen, added = set(), 0
    try:
        with PlaywrightMCP() as browser:
            for q in queries:
                try:
                    found = browser.search(q, r.count)
                except MCPError as e:
                    log.append(f"Query '{q}' FAILED: {e}"); continue
                log.append(f"Playwright MCP '{q}': {len(found)} results")
                for link in found:
                    host = urlparse(link).netloc.replace("www.", "").lower()
                    if not host or host in seen or any(s in host for s in SKIP): continue
                    seen.add(host)
                    lead, err = build_lead("https://" + host, "", r.niche, r.location, f"playwright:{q}")
                    if err: log.append(f"  skip {host}: {err[:80]}"); continue
                    if upsert(lead): added += 1; log.append(f"  + {host} (score {lead['score']})")
                    else: log.append(f"  dup {host}")
                    if added >= r.count: break
                if added >= r.count: break
    except MCPError as e:
        raise HTTPException(502, f"Playwright MCP unavailable: {e}")
    except Exception as e:
        raise HTTPException(502, f"Playwright MCP failed: {type(e).__name__}: {e}")
    return {"log": log, "added": added}

class UrlReq(BaseModel):
    url: str; name: str = ""; niche: str = ""; location: str = ""
@app.post("/api/add-url")
def add_url(r: UrlReq):
    lead, err = build_lead(r.url, r.name, r.niche, r.location, "manual")
    if err: raise HTTPException(400, err)
    i = upsert(lead)
    if not i: raise HTTPException(409, "Duplicate (domain already in library)")
    return {"id": i}

def _import_rows(text):
    n = d = 0
    for row in csv.DictReader(io.StringIO(text)):
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if not row.get("website"): continue
        ok = upsert({"name": row.get("name", ""), "niche": row.get("niche", ""), "location": row.get("location", ""), "website": row["website"], "email": row.get("email", ""),
                     "phone": row.get("phone", ""), "source": "csv", "status": "new"})
        n += bool(ok); d += not ok
    return {"imported": n, "duplicates": d}
@app.post("/api/import")
async def import_csv(file: UploadFile): return _import_rows((await file.read()).decode("utf-8-sig"))
@app.post("/api/import-folder")
def import_folder():
    """Imports every CSV dropped in ./imports (e.g. your own LinkedIn/Sales Navigator export), then moves it to imports/done."""
    d = ROOT / "imports"; (d / "done").mkdir(parents=True, exist_ok=True); tot = {"imported": 0, "duplicates": 0, "files": 0}
    for f in d.glob("*.csv"):
        r = _import_rows(f.read_text(encoding="utf-8-sig")); tot["imported"] += r["imported"]; tot["duplicates"] += r["duplicates"]; tot["files"] += 1
        f.replace(d / "done" / f.name)
    return tot

def row(r):
    d = dict(r)
    for k in ("evidence", "problems", "reasons"): d[k] = json.loads(d[k] or "null")
    return d
@app.get("/api/leads")
def leads(status: str = "", q: str = ""):
    sql, a = "SELECT * FROM leads WHERE 1=1", []
    if status: sql += " AND status=?"; a.append(status)
    if q: sql += " AND (name||website||email||niche||tags||notes) LIKE ?"; a.append(f"%{q}%")
    c = db(); out = [row(r) for r in c.execute(sql + " ORDER BY score DESC", a)]; c.close(); return out

class Patch(BaseModel):
    name: str | None = None; email: str | None = None; subject: str | None = None; body: str | None = None; notes: str | None = None; tags: str | None = None; status: str | None = None
@app.patch("/api/leads/{i}")
def patch(i: int, p: Patch):
    f = {k: v for k, v in p.model_dump().items() if v is not None}
    if not f: return {}
    if 'email' in f: f['email_check'] = check_email(f['email'])
    c = db(); c.execute(f"UPDATE leads SET {','.join(k+'=?' for k in f)} WHERE id=?", [*f.values(), i]); c.commit(); c.close(); return {"ok": True}

@app.get("/api/export")
def export():
    c = db(); rows = c.execute("SELECT name,niche,location,website,email,phone,service,score,confidence,status,notes,tags FROM leads").fetchall(); c.close()
    o = io.StringIO(); w = csv.writer(o); w.writerow(rows[0].keys() if rows else ["name"]); [w.writerow(list(r)) for r in rows]
    return Response(o.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=leads.csv"})

# ---------- Suppression / sending ----------
class Sup(BaseModel): email: str; reason: str = "manual"
@app.post("/api/suppress")
def suppress(s: Sup):
    c = db(); c.execute("INSERT OR REPLACE INTO suppression VALUES(?,?,?)", (s.email.lower(), s.reason, now()))
    c.execute("UPDATE leads SET status='suppressed' WHERE lower(email)=?", (s.email.lower(),)); c.commit(); c.close(); return {"ok": True}
@app.get("/api/suppression")
def sup_list():
    c = db(); r = [dict(x) for x in c.execute("SELECT * FROM suppression")]; c.close(); return r

# ---------- Gmail SMTP preset: host/port/security are pre-filled; only address + app password are asked ----------
GMAIL = {"SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587", "SMTP_SECURE": "starttls"}
def smtp_cfg():
    """Resolved SMTP config. Host/port/security fall back to Gmail; SMTP_FROM falls back to SMTP_USER."""
    sec = (env("SMTP_SECURE", "") or GMAIL["SMTP_SECURE"]).lower()
    port = env("SMTP_PORT", "") or ("465" if sec == "ssl" else GMAIL["SMTP_PORT"])
    return {"host": env("SMTP_HOST", "") or GMAIL["SMTP_HOST"], "port": int(port), "secure": sec,
            "user": env("SMTP_USER", ""), "password": env("SMTP_PASS", ""),
            "from": env("SMTP_FROM", "") or env("SMTP_USER", "")}
def _smtp_creds_or_raise(cfg):
    if not cfg["user"] or not cfg["password"]:
        raise ValueError("Set SMTP_USER (your Gmail address) and SMTP_PASS (a Gmail app password) in Settings.")
    return cfg
def smtp_send(to, subject, body):
    c = _smtp_creds_or_raise(smtp_cfg())
    m = EmailMessage(); m["From"] = c["from"]; m["To"] = to; m["Subject"] = subject; m.set_content(body)
    ctx = ssl.create_default_context()
    if c["secure"] == "ssl": s = smtplib.SMTP_SSL(c["host"], c["port"], context=ctx, timeout=20)
    else:
        s = smtplib.SMTP(c["host"], c["port"], timeout=20); s.starttls(context=ctx)
    try: s.login(c["user"], c["password"]); s.send_message(m)
    finally: s.quit()

class SendReq(BaseModel): confirm: bool = False; subject: str | None = None; body: str | None = None
def followups_due(c):
    N, M, out = int(env("FOLLOWUP_DAYS", "5")), int(env("MAX_FOLLOWUPS", "1")), []
    for L in c.execute("SELECT * FROM leads WHERE status='sent'").fetchall():
        s_ = c.execute("SELECT kind,ts FROM sends WHERE lead_id=? AND result='sent' ORDER BY id", (L["id"],)).fetchall()
        if not s_: continue
        fu = sum(x["kind"] == "followup" for x in s_); days = (dt.datetime.now() - dt.datetime.fromisoformat(s_[-1]["ts"])).days
        if fu < M and days >= N:
            me = env("SENDER_NAME", "Hassen")
            out.append({"id": L["id"], "name": L["name"], "email": L["email"], "days_since": days, "followups_sent": fu, "subject": "Re: " + L["subject"],
                "body": f"Hi {L['name'] or 'there'} team,\n\nJust a quick follow-up on my note about {L['website']}. Happy to send the short outline if it'd be useful — and if not, no worries, I won't email again.\n\n{me}"})
    return out
def _guarded_send(i, kind, r):
    if not r.confirm: raise HTTPException(400, "Explicit confirmation required.")
    c = db(); L = c.execute("SELECT * FROM leads WHERE id=?", (i,)).fetchone()
    if not L: raise HTTPException(404, "No such lead")
    to, mode = L["email"].lower(), env("SMTP_MODE", "dry")
    subject, body = r.subject or L["subject"], r.body or L["body"]
    def fail(msg, code=400):
        c.execute("INSERT INTO sends(lead_id,to_email,subject,result,error,mode,ts,kind) VALUES(?,?,?,?,?,?,?,?)", (i, to, subject, "failed", msg, mode, now(), kind)); c.commit(); c.close(); raise HTTPException(code, msg)
    if kind == "initial":
        if L["status"] != "approved": raise HTTPException(409, f"Lead status is '{L['status']}', must be 'approved'.")
        if c.execute("SELECT 1 FROM sends WHERE lower(to_email)=? AND result='sent' AND mode='live'", (to,)).fetchone(): fail("Already emailed this address.", 409)
    elif not any(d["id"] == i for d in followups_due(c)): raise HTTPException(409, "No follow-up due for this lead (not sent yet, too soon, replied, or limit reached).")
    if not to or not body.strip(): raise HTTPException(400, "Missing recipient or body.")
    if L["email_check"] == "invalid": fail("Recipient email failed verification (invalid syntax or domain).")
    if c.execute("SELECT 1 FROM suppression WHERE email=?", (to,)).fetchone(): fail("Recipient is on the suppression list.")
    today = c.execute("SELECT COUNT(*) FROM sends WHERE result='sent' AND mode='live' AND ts>=?", (dt.date.today().isoformat(),)).fetchone()[0]
    if mode == "live" and today >= int(env("DAILY_LIMIT", "10")): fail(f"Daily limit ({env('DAILY_LIMIT','10')}) reached.", 429)
    last = c.execute("SELECT ts FROM sends WHERE result='sent' AND mode='live' ORDER BY id DESC LIMIT 1").fetchone()
    if mode == "live" and last and (dt.datetime.now() - dt.datetime.fromisoformat(last[0])).total_seconds() < 30: fail("Rate limit: wait 30s between sends.", 429)
    if mode == "live" and L["is_mock"]: fail("Mock leads can never be sent live.")
    try:
        if mode == "live": smtp_send(to, subject, body)
    except Exception as e: fail(f"SMTP error: {type(e).__name__}: {e}".replace(env("SMTP_PASS", "\0"), "***"), 502)
    c.execute("INSERT INTO sends(lead_id,to_email,subject,result,mode,ts,kind) VALUES(?,?,?,?,?,?,?)", (i, to, subject, "sent", mode, now(), kind))
    c.execute("UPDATE leads SET status='sent' WHERE id=?", (i,)); c.commit(); c.close(); return {"ok": True, "mode": mode}
@app.post("/api/leads/{i}/approve")
def approve(i: int):
    c = db(); c.execute("UPDATE leads SET status='approved' WHERE id=? AND status IN ('qualified','new','approved')", (i,)); c.commit(); c.close(); return {"ok": True}
@app.post("/api/leads/{i}/reject")
def reject(i: int):
    c = db(); c.execute("UPDATE leads SET status='rejected' WHERE id=?", (i,)); c.commit(); c.close(); return {"ok": True}
@app.post("/api/leads/{i}/send")
def send(i: int, r: SendReq): return _guarded_send(i, "initial", r)
@app.post("/api/leads/{i}/followup-send")
def followup_send(i: int, r: SendReq): return _guarded_send(i, "followup", r)
@app.get("/api/followups")
def followups():
    c = db(); r = followups_due(c); c.close(); return r
@app.post("/api/leads/{i}/replied")
def replied(i: int):
    c = db(); c.execute("UPDATE leads SET status='replied' WHERE id=?", (i,)); c.commit(); c.close(); return {"ok": True}
@app.get("/api/history")
def history():
    c = db(); r = [dict(x) for x in c.execute("SELECT s.*, l.name FROM sends s LEFT JOIN leads l ON l.id=s.lead_id ORDER BY s.id DESC")]; c.close(); return r

# ---------- Settings / status ----------
KEYS = ["FOLLOWUP_DAYS","MAX_FOLLOWUPS","LAYA_ENABLED","PLAYWRIGHT_MCP_CLI","SMTP_MODE","SMTP_HOST","SMTP_PORT","SMTP_USER","SMTP_PASS","SMTP_FROM","SMTP_SECURE","SENDER_NAME","DAILY_LIMIT"]
@app.get("/api/settings")
def get_settings(): return {k: ("" if k == "SMTP_PASS" else (env(k) or GMAIL.get(k, ""))) for k in KEYS} | {f"has_{k}": bool(env(k)) for k in ("SMTP_PASS",)}
@app.post("/api/settings")
def set_settings(d: dict):
    ENV.touch()
    for k, v in d.items():
        if k in KEYS and v != "": set_key(str(ENV), k, str(v), quote_mode="never"); os.environ[k] = str(v)
    return {"ok": True}
@app.post("/api/smtp/test")
def smtp_test():
    try:
        c = _smtp_creds_or_raise(smtp_cfg())
        s = smtplib.SMTP_SSL(c["host"], c["port"], timeout=15) if c["secure"] == "ssl" else smtplib.SMTP(c["host"], c["port"], timeout=15)
        if c["secure"] != "ssl": s.starttls(context=ssl.create_default_context())
        s.login(c["user"], c["password"]); s.quit(); return {"ok": True, "message": "Login OK. No email was sent."}
    except ValueError as e: raise HTTPException(400, str(e))
    except Exception as e: raise HTTPException(400, f"{type(e).__name__}: {e}".replace(env("SMTP_PASS", "\0"), "***"))
@app.get("/api/status")
def status():
    c = db(); cnt = {r[0]: r[1] for r in c.execute("SELECT status,COUNT(*) FROM leads GROUP BY status")}; c.close()
    from playwright_mcp import availability as mcp_availability
    return {"ok": True, "counts": cnt, "laya": laya_status(), "smtp_mode": env("SMTP_MODE", "dry"), "discovery": mcp_availability(), "followups_due": len(followups())}
@app.get("/")
def index(): return FileResponse(ROOT / "static" / "index.html")
