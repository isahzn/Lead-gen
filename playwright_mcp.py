"""Minimal Playwright MCP client used for lead discovery.

The app talks to the official Playwright MCP server (`@playwright/mcp`) over
stdio using newline-delimited JSON-RPC. There is no separate search API and no
API key: the MCP browser performs a real Bing search and we read the result
links off the page.

The browser runs **headed**: Bing serves unrelated results (or none) to a
headless browser, so a visible window is what makes discovery reliable.
"""
import atexit
import base64
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_TIMEOUT = 90
NAV_TIMEOUT = 60
SETTLE_SECONDS = 3.0


class MCPError(RuntimeError):
    """The Playwright MCP server is unavailable or a tool call failed."""


def _cli_path() -> str | None:
    """Locate the Playwright MCP entry point, preferring an existing npx cache."""
    env = os.environ.get("PLAYWRIGHT_MCP_CLI")
    if env and Path(env).exists():
        return env
    roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "npm-cache" / "_npx",
             Path.home() / ".npm" / "_npx"]
    for root in roots:
        if not root.is_dir():
            continue
        for cand in root.glob("*/node_modules/@playwright/mcp/cli.js"):
            return str(cand)
    return None


def availability() -> dict:
    """What the status endpoint reports: is the MCP server runnable here?"""
    node = shutil.which("node")
    cli = _cli_path()
    npx = shutil.which("npx")
    if not node:
        return {"available": False, "reason": "Node.js not found (install installers/node-*.msi)"}
    if not cli and not npx:
        return {"available": False, "reason": "Playwright MCP not installed (run: npx @playwright/mcp@latest)"}
    return {"available": True, "node": node, "cli": cli or "npx @playwright/mcp@latest",
            "mode": "Bing via Playwright MCP (headed browser)"}


class PlaywrightMCP:
    """One browser session driven through the Playwright MCP server."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self._proc = None
        self._queue: queue.Queue = queue.Queue()
        self._next_id = 0
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self._proc and self._proc.poll() is None:
            return self
        cli = _cli_path()
        if cli:
            cmd = ["node", cli, "--isolated"]
        else:
            npx = shutil.which("npx")
            if not npx:
                raise MCPError("Node.js/npx not found. Install Node, then run: npx @playwright/mcp@latest")
            cmd = [npx, "-y", "@playwright/mcp@latest", "--isolated"]
        try:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                          bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            raise MCPError(f"Could not start Playwright MCP: {e}") from e
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "ai-lead-finder", "version": "1.0"}})
        self._notify("notifications/initialized")
        atexit.register(self.close)
        return self

    def _read_stdout(self):
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._queue.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    def _drain_stderr(self):
        # Playwright MCP logs to stderr; draining it stops the pipe filling up.
        for _ in self._proc.stderr:
            pass

    def close(self):
        proc, self._proc = self._proc, None
        if not proc:
            return
        try:
            if proc.poll() is None:
                proc.kill()
        except OSError:
            pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
        return False

    # -- JSON-RPC ----------------------------------------------------------
    def _send(self, obj):
        if not self._proc or self._proc.poll() is not None:
            raise MCPError("Playwright MCP is not running")
        try:
            self._proc.stdin.write(json.dumps(obj) + "\n")
            self._proc.stdin.flush()
        except OSError as e:
            raise MCPError(f"Playwright MCP closed the connection: {e}") from e

    def _notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def _call(self, method, params=None, timeout=None):
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            msg = {"jsonrpc": "2.0", "id": rid, "method": method}
            if params is not None:
                msg["params"] = params
            self._send(msg)
            deadline = time.time() + (timeout or self.timeout)
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise MCPError(f"Playwright MCP timed out on {method}")
                try:
                    reply = self._queue.get(timeout=remaining)
                except queue.Empty:
                    raise MCPError(f"Playwright MCP timed out on {method}") from None
                if reply.get("id") != rid:
                    continue  # notification or another request's reply
                if "error" in reply:
                    raise MCPError(f"{method}: {reply['error'].get('message', reply['error'])}")
                return reply.get("result", {})

    def tool(self, name, arguments=None, timeout=None):
        """Call an MCP tool and return its text payload."""
        result = self._call("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)
        if result.get("isError"):
            text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
            raise MCPError(f"{name} failed: {text[:300]}")
        return "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")

    # -- discovery ---------------------------------------------------------
    def search(self, query: str, limit: int = 10) -> list[str]:
        """Run a Bing search in the MCP browser and return result URLs."""
        from urllib.parse import quote_plus
        url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max(limit, 10)}"
        self.tool("browser_navigate", {"url": url}, timeout=NAV_TIMEOUT)
        time.sleep(SETTLE_SECONDS)
        js = ("() => Array.from(document.querySelectorAll('li.b_algo h2 a'))"
              ".map(a => a.href).filter(h => /^https?:/.test(h))")
        raw = self.tool("browser_evaluate", {"function": js})
        return _parse_links(raw)


def _parse_links(raw: str) -> list[str]:
    """Pull URLs out of the evaluate payload and unwrap Bing redirect links."""
    urls, seen = [], set()
    for line in raw.splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith('"http'):
            continue
        link = line.strip('"')
        target = _unwrap_bing(link)
        if target and target not in seen:
            seen.add(target)
            urls.append(target)
    return urls


def _unwrap_bing(link: str) -> str:
    """Bing wraps results in /ck/a?...&u=a1<base64-url>; decode the real target."""
    if "bing.com/ck/a" not in link:
        return link
    raw = parse_qs(urlparse(link).query).get("u", [""])[0]
    if not raw.startswith("a1"):
        return ""
    body = raw[2:]
    body += "=" * (-len(body) % 4)
    try:
        return base64.urlsafe_b64decode(body).decode("utf-8", "replace")
    except Exception:
        return ""
