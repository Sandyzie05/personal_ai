"""Read-only Gmail via the standard Gmail REST API (works for personal @gmail.com accounts).

Google's hosted Gmail MCP server needs a Workspace Developer Preview project, which personal accounts cannot join, so
Sync and the Email Digest agent use this instead. It exposes the same `call(tool, args) -> json text` surface as
`ingest.ReadOnlyMcp`, but only two operations exist (`search_threads`, `get_thread`), both fixed GETs on
gmail.googleapis.com. The scope is fixed to gmail.readonly and is not configurable. The user's own OAuth client and the
tokens live in one encrypted internal vault record. Message bodies are returned to the caller only, which passes them to
`quarantine.extract_facts`; nothing here stores or logs them.
"""
import base64
import hashlib
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Any, Dict, List, Optional

from src.config import MCP_OAUTH_CALLBACK_PORT, MCP_OAUTH_TIMEOUT_SECONDS

KEY = "gmail_api_oauth"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
THREAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_PAGE = 50
MAX_BODY_CHARS = 20_000
TIMEOUT_S = 30


class GmailAuthError(Exception):
    """Not connected or the grant was revoked: the user must press Connect again."""

    needs_auth = True


def _record(vault) -> Dict[str, Any]:
    return dict(vault.retrieve_data(KEY) or {})


def configured(vault) -> bool:
    return bool(_record(vault).get("client_id"))


def connected(vault) -> bool:
    r = _record(vault)
    return bool(r.get("client_id") and r.get("refresh_token"))


def client_id(vault) -> Optional[str]:
    return _record(vault).get("client_id")


def save_client(vault, cid: str, secret: str) -> None:
    """New client = new grant: old tokens are dropped."""
    if not cid.strip():
        raise ValueError("client ID is required")
    vault.store_data(KEY, {"client_id": cid.strip(), "client_secret": secret.strip()}, encrypt=True)


def disconnect(vault) -> None:
    r = _record(vault)
    vault.store_data(KEY, {k: r[k] for k in ("client_id", "client_secret") if k in r}, encrypt=True)


def _post(url: str, data: Dict[str, str]) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # Google's error body is not shown: status only
        if e.code in (400, 401):
            raise GmailAuthError(f"token request rejected (HTTP {e.code}); press Connect Gmail again") from None
        raise ConnectionError(f"token endpoint HTTP {e.code}") from None


def _store_tokens(vault, r: Dict[str, Any], tok: Dict[str, Any]) -> None:
    if SCOPE not in str(tok.get("scope", SCOPE)).split():
        raise GmailAuthError("Google did not grant read-only Gmail access")
    r["access_token"] = tok["access_token"]
    r["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    if tok.get("refresh_token"):
        r["refresh_token"] = tok["refresh_token"]
    vault.store_data(KEY, r, encrypt=True)


def connect(vault, open_browser=webbrowser.open, timeout: float = MCP_OAUTH_TIMEOUT_SECONDS) -> None:
    """Browser sign-in (authorization code + PKCE, loopback redirect). Asks for gmail.readonly only, offline access."""
    from src.agents.mcp import _OAuthCallbackServer

    r = _record(vault)
    if not r.get("client_id"):
        raise GmailAuthError("save your OAuth client ID first")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    cb = _OAuthCallbackServer()
    cb.start()
    try:
        open_browser(AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": r["client_id"], "redirect_uri": cb.redirect_uri, "response_type": "code", "scope": SCOPE,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
            "access_type": "offline", "prompt": "consent", "include_granted_scopes": "false",
        }))
        result = cb.wait(timeout)
    finally:
        cb.stop()
    if result is None:
        raise GmailAuthError(f"timed out after {int(timeout)}s waiting for browser sign-in")
    if not result.state or not secrets.compare_digest(result.state, state):
        raise GmailAuthError("sign-in state mismatch; try again")
    data = {"grant_type": "authorization_code", "code": result.code, "redirect_uri": cb.redirect_uri,
            "client_id": r["client_id"], "code_verifier": verifier}
    if r.get("client_secret"):
        data["client_secret"] = r["client_secret"]
    _store_tokens(vault, r, _post(TOKEN_URL, data))
    if not _record(vault).get("refresh_token"):
        raise GmailAuthError("Google returned no refresh token; remove the app's access in your Google account and retry")


def _access_token(vault) -> str:
    r = _record(vault)
    if not r.get("refresh_token"):
        raise GmailAuthError("Gmail is not connected; press Connect Gmail")
    if r.get("access_token") and time.time() < float(r.get("expires_at") or 0):
        return r["access_token"]
    data = {"grant_type": "refresh_token", "refresh_token": r["refresh_token"], "client_id": r["client_id"]}
    if r.get("client_secret"):
        data["client_secret"] = r["client_secret"]
    _store_tokens(vault, r, _post(TOKEN_URL, data))
    return _record(vault)["access_token"]


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    except Exception:
        return ""


def _plain_text(payload: Dict[str, Any]) -> str:
    """First text/plain part (depth-first); HTML-only mail falls back to tag-stripped text/html."""
    html = ""
    stack = [payload or {}]
    while stack:
        p = stack.pop(0)
        mime, body = p.get("mimeType", ""), (p.get("body") or {}).get("data")
        if mime == "text/plain" and body:
            return _decode(body)[:MAX_BODY_CHARS]
        if mime == "text/html" and body and not html:
            html = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", _decode(body)))
        stack.extend(p.get("parts") or [])
    return re.sub(r"\s+", " ", html)[:MAX_BODY_CHARS]


class GmailApi:
    """Same call surface as ReadOnlyMcp; anything but the two fixed reads raises PermissionError."""

    mcp = None  # gmail_bills._Args probes ro.mcp.list_tools; None keeps our preferred tool names
    server_id = "gmail_api"

    def __init__(self, vault, opener=urllib.request.urlopen):
        self.vault, self._open = vault, opener

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{API}/{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_access_token(self.vault)}"})
        from src.observability import otel

        tool = "get_thread" if path.startswith("threads/") else "search_threads"
        otel.count("personal_ai.tool.calls", tool=tool, server="gmail_api")
        try:
            with otel.span(f"tool {tool}", tool=tool, server="gmail_api") as sp, self._open(req, timeout=TIMEOUT_S) as r:
                sp.set(**{"http.status": getattr(r, "status", 200)})
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise GmailAuthError("Gmail sign-in expired; press Connect Gmail") from None
            raise ConnectionError(f"Gmail API HTTP {e.code}") from None  # body is server text: not kept

    def call(self, tool: str, args: Dict[str, Any]) -> str:
        if tool == "search_threads":
            q = str(args.get("query") or args.get("q") or "")[:500]
            size = max(1, min(int(args.get("pageSize") or 20), MAX_PAGE))
            data = self._get("threads", {"q": q, "maxResults": size})
            return json.dumps({"threads": [{"id": t.get("id")} for t in data.get("threads") or []]})
        if tool == "get_thread":
            tid = str(args.get("threadId") or args.get("id") or "")
            if not THREAD_ID.match(tid):
                raise ValueError("invalid thread id")
            data = self._get(f"threads/{tid}", {"format": "full"})
            out = []
            for m in data.get("messages") or []:
                headers = {h.get("name", "").lower(): h.get("value", "") for h in (m.get("payload") or {}).get("headers") or []}
                out.append({"id": m.get("id"), "sender": headers.get("from", ""), "subject": headers.get("subject", ""),
                            "date": headers.get("date", ""), "plaintextBody": _plain_text(m.get("payload") or {})
                            or m.get("snippet", "")})
            return json.dumps({"messages": out})
        raise PermissionError(f"tool '{tool}' is not available (read-only Gmail API adapter)")
