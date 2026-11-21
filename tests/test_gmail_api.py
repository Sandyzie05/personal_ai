import base64
import io
import json
import time
import urllib.parse
from datetime import date
from types import SimpleNamespace

import pytest

from src.data_ingestion import gmail_api, gmail_bills
from src.data_vault.vault import is_internal_vault_key
from test_forecast_page import MemoryVault
from test_gmail_bills import BODY, Extractor


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


THREAD = {"messages": [{"id": "m1", "snippet": "snip", "payload": {
    "headers": [{"name": "From", "value": "RMP <no-reply@mail.rockymountainpower.net>"}, {"name": "Subject", "value": "Bill"}],
    "mimeType": "multipart/alternative",
    "parts": [{"mimeType": "text/html", "body": {"data": b64("<b>html</b>")}},
              {"mimeType": "text/plain", "body": {"data": b64(BODY)}}]}}]}


class Opener:
    def __init__(self):
        self.urls = []

    def __call__(self, req, timeout=None):
        self.urls.append((req.full_url, req.get_method(), req.headers.get("Authorization")))
        path = urllib.parse.urlparse(req.full_url).path
        out = {"threads": [{"id": "t1", "snippet": "x"}]} if path.endswith("/threads") else THREAD
        return io.BytesIO(json.dumps(out).encode())


def connected_vault():
    v = MemoryVault()
    v.store_data(gmail_api.KEY, {"client_id": "c", "client_secret": "s", "refresh_token": "r",
                                 "access_token": "tok", "expires_at": time.time() + 600})
    return v


def test_only_two_fixed_reads_and_body_from_text_plain():
    o = Opener()
    api = gmail_api.GmailApi(connected_vault(), opener=o)
    assert json.loads(api.call("search_threads", {"query": "from:a.com", "pageSize": 999})) == {"threads": [{"id": "t1"}]}
    msg = json.loads(api.call("get_thread", {"threadId": "t1", "messageFormat": "PLAIN_TEXT"}))["messages"][0]
    assert msg["plaintextBody"] == BODY and "rockymountainpower" in msg["sender"]
    assert all(u.startswith("https://gmail.googleapis.com/gmail/v1/users/me/threads") and m == "GET" and a == "Bearer tok"
               for u, m, a in o.urls)
    assert "maxResults=50" in o.urls[0][0]  # page size capped
    for tool in ("send_message", "trash_thread", "create_draft", "modify_thread"):
        with pytest.raises(PermissionError):
            api.call(tool, {})
    with pytest.raises(ValueError):
        api.call("get_thread", {"threadId": "../../drafts"})


def test_bill_sync_end_to_end_through_the_api_adapter():
    v = connected_vault()
    gmail_bills.add_sender(v, "Rocky Mountain Power", "electricity", "rockymountainpower.net")
    rep = gmail_bills.sync(v, gmail_api.GmailApi(v, opener=Opener()), Extractor(), today=date(2026, 11, 20))
    assert rep.new == ["Rocky Mountain Power 2026-11-14"]
    assert BODY not in json.dumps(v.records)  # body never stored


def test_expired_token_refreshes_and_non_readonly_grant_is_rejected(monkeypatch):
    v = connected_vault()
    v.store_data(gmail_api.KEY, {**v.retrieve_data(gmail_api.KEY), "expires_at": 0})
    posts = []
    monkeypatch.setattr(gmail_api, "_post", lambda url, data: posts.append((url, data)) or
                        {"access_token": "new", "expires_in": 3600, "scope": gmail_api.SCOPE})
    gmail_api.GmailApi(v, opener=Opener()).call("search_threads", {"query": "x"})
    assert posts[0][0] == "https://oauth2.googleapis.com/token" and posts[0][1]["grant_type"] == "refresh_token"
    assert v.retrieve_data(gmail_api.KEY)["access_token"] == "new" and v.retrieve_data(gmail_api.KEY)["refresh_token"] == "r"
    v.store_data(gmail_api.KEY, {**v.retrieve_data(gmail_api.KEY), "expires_at": 0})
    monkeypatch.setattr(gmail_api, "_post", lambda url, data: {"access_token": "x", "scope": "https://mail.google.com/"})
    with pytest.raises(gmail_api.GmailAuthError):
        gmail_api.GmailApi(v, opener=Opener()).call("search_threads", {"query": "x"})


def test_connect_requests_readonly_offline_with_pkce(monkeypatch):
    import src.agents.mcp as m

    seen = {}

    class CB:
        redirect_uri = "http://localhost:8765/callback"

        def start(self):
            pass

        def stop(self):
            pass

        def wait(self, timeout):
            return SimpleNamespace(code="code1", state=seen["state"])

    def browser(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        seen.update(q)

    monkeypatch.setattr(m, "_OAuthCallbackServer", CB)
    monkeypatch.setattr(gmail_api, "_post", lambda url, data: seen.update(post=data) or
                        {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": gmail_api.SCOPE})
    v = MemoryVault()
    gmail_api.save_client(v, "cid", "sec")
    gmail_api.connect(v, open_browser=browser)
    assert seen["scope"] == gmail_api.SCOPE and seen["access_type"] == "offline" and seen["code_challenge_method"] == "S256"
    assert seen["post"]["code_verifier"] and seen["post"]["client_secret"] == "sec"
    assert gmail_api.connected(v) and is_internal_vault_key(gmail_api.KEY)


def test_email_digest_uses_api_when_connected():
    from src.agents.orchestration.ingest import gmail_fetcher

    mails = gmail_fetcher(gmail_api.GmailApi(connected_vault(), opener=Opener()))()
    assert mails[0]["body"] == BODY
