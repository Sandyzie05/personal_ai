"""Portal framework: schema, runner (fake driver: host drift, creds hygiene, 2FA) and a real Playwright browser
against an in-process mock portal (https fulfilled from tests/fixtures/portal; nothing touches the network)."""
import pathlib
import shutil

import pytest

from src.data_ingestion.pipeline import run_ingest
from src.data_ingestion.portal.recipe import PortalRecipe
from src.data_ingestion.portal.recipes import TEMPLATES
from src.data_ingestion.portal.runner import RecipePortalFetcher, run_recipe
from src.data_ingestion.portal.store import save_credentials, save_recipe
from tests.test_ingest_files import make_pdf
from tests.test_orchestration import FakeVault

PASSWORD = "hunter2-SECRET"
CREDS = {"username": "me@example.com", "password": PASSWORD}


class Fake:
    def __init__(self, host="portal.mock-power.test", challenge=False, drift_to=None, boom=False):
        self._host, self.challenge, self.drift_to, self.boom = host, challenge, drift_to, boom
        self.filled, self.closed = [], False

    def goto(self, url): pass
    def click(self, s): self._host = self.drift_to or self._host
    def fill(self, s, v):
        if self.boom:
            raise RuntimeError(f"timeout filling {s} with {v}")
        self.filled.append(v)
    def wait_for(self, s): pass
    def exists(self, s): return self.challenge and s == "#otp-input"
    def host(self): return self._host
    def collect_pdfs(self, s, n): return [make_pdf()][:n]
    def close(self): self.closed = True


R = TEMPLATES["electricity"]


def test_recipe_schema_is_closed_and_safe():
    ok = R.model_dump(mode="json")
    for mutate in (
        lambda d: d["steps"].append({"action": "goto", "url": "https://evil.test/x"}),
        lambda d: d["steps"].append({"action": "goto", "url": "http://portal.mock-power.test/x"}),
        lambda d: d["steps"].append({"action": "goto", "url": "https://portal.mock-power.test/autopay/pay"}),
        lambda d: d["steps"].append({"action": "click", "selector": "#pay-now"}),
        lambda d: d["steps"].append({"action": "shell", "selector": "ls"}),
        lambda d: d.update(extra="x"),
        lambda d: d["allowed_hosts"].append("https://x.com"),
    ):
        d = PortalRecipe.model_validate(ok).model_dump(mode="json")
        mutate(d)
        with pytest.raises(ValueError):
            PortalRecipe.model_validate(d)


def test_happy_path_and_credentials_never_logged():
    d = Fake()
    run = run_recipe(R, d, CREDS)
    assert run.status == "ok" and len(run.pdfs) == 1 and d.closed
    assert PASSWORD not in " ".join(run.log) and d.filled == [CREDS["username"], PASSWORD]


def test_driver_error_scrubbed():
    run = run_recipe(R, Fake(boom=True), CREDS)
    assert run.status == "failed" and PASSWORD not in " ".join(run.log) and "RuntimeError" in run.log[-1]


def test_redirect_off_allowlist_blocks_and_closes():
    d = Fake(drift_to="evil.test")
    run = run_recipe(R, d, CREDS)
    assert run.status == "blocked" and not run.pdfs and d.closed


def test_2fa_needs_human_then_resumes():
    run = run_recipe(R, Fake(challenge=True), CREDS)  # no human available -> stops
    assert run.status == "needs_you" and not run.pdfs
    asked = []
    run = run_recipe(R, Fake(challenge=True), CREDS, lambda m: asked.append(m) or True)
    assert run.status == "ok" and asked


def test_fetcher_feeds_pipeline_and_agent_path():
    v = FakeVault()
    save_recipe(v, R)
    save_credentials(v, R.provider, **CREDS)
    f = RecipePortalFetcher(v, lambda r: Fake())
    assert len(f.fetch(R.provider)) == 1 and "84.20" in f.fetch(R.provider)[0]["text"]
    rep = run_ingest(v, [lambda: f.candidates(R.provider)])
    assert len(rep.new) == 1
    assert PASSWORD not in str(v.d.get("ingest_manifest")) and PASSWORD not in rep.summary()


def test_fetcher_without_recipe_or_credentials_needs_you():
    f = RecipePortalFetcher(FakeVault(), lambda r: Fake())
    assert "no portal recipe" in run_ingest(FakeVault(), [lambda: f.candidates("Nobody")]).needs_you[0]


def test_all_templates_valid():
    assert set(TEMPLATES) == {"electricity", "gas", "phone"}


# ---- real browser against the mock portal -----------------------------------
FIX = pathlib.Path(__file__).parent / "fixtures" / "portal"


def _browser_available():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _browser_available(), reason="playwright browser not installed")
@pytest.mark.parametrize("login,bills,expect", [("login.html", "bills.html", "ok"),
                                                 ("login_2fa.html", "bills.html", "needs_you"),
                                                 ("login.html", "bills_redirect.html", "no_pdfs")])
def test_real_browser_against_mock_portal(tmp_path, login, bills, expect):
    from src.data_ingestion.portal.runner import PlaywrightDriver

    pdf, evil = make_pdf(), []  # evil: URLs the page tried to reach (all aborted, never fulfilled)
    page_for = {"/login": (FIX / login).read_text(), "/2fa": (FIX / "2fa.html").read_text(),
                "/bills": (FIX / bills).read_text()}

    class Mock(PlaywrightDriver):
        def _route(self, route):
            u = route.request.url
            if "evil.test" in u:
                evil.append(u)
                return route.abort()
            path = u.split("mock-power.test", 1)[1] if "mock-power.test" in u else None
            if path == "/bill1.pdf":
                return route.fulfill(body=pdf, headers={"content-type": "application/pdf",
                                                        "content-disposition": "attachment; filename=../../x.pdf"})
            if path in page_for:
                return route.fulfill(body=page_for[path], content_type="text/html")
            route.abort()

    d = Mock(R.allowed_hosts, str(tmp_path / "prof"), headless=True, timeout_ms=5000)
    run = run_recipe(R, d, CREDS)
    if expect == "no_pdfs":  # hostile redirect: off-allowlist navigation is aborted, nothing is collected, evil never served
        assert run.status in ("failed", "blocked") and not run.pdfs
    else:
        assert run.status == expect, run.log
    if expect == "ok":
        assert len(run.pdfs) == 1 and run.pdfs[0].startswith(b"%PDF-")
    assert (tmp_path / "prof").stat().st_mode & 0o777 == 0o700
