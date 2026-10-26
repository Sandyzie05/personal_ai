"""Deterministic portal runner (R9 / S-R5). No LLM in the loop: code fills credentials, enforces hosts, stops for humans.

`BrowserDriver` is the seam: PlaywrightDriver in production, a fake in tests. Credentials are passed to the driver only
inside fill steps and never reach logs, exceptions, reports or any model.
"""

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol

from src.agents.orchestration.ingest import PortalFetcher  # noqa: F401  (protocol this module's fetcher implements)

from ..files import UnsafeFile, pdf_text, validate_pdf
from ..pipeline import Candidate
from .recipe import PortalRecipe, url_allowed


class BrowserDriver(Protocol):
    def goto(self, url: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def wait_for(self, selector: str) -> None: ...
    def exists(self, selector: str) -> bool: ...
    def host(self) -> str: ...
    def collect_pdfs(self, selector: Optional[str], limit: int) -> List[bytes]: ...
    def close(self) -> None: ...


@dataclass
class PortalRun:
    status: str = "ok"  # ok | needs_you | blocked | failed
    pdfs: List[bytes] = field(default_factory=list)
    log: List[str] = field(default_factory=list)  # actions + hosts only


def run_recipe(
    recipe: PortalRecipe,
    driver: BrowserDriver,
    creds: Dict[str, str],
    ask_human: Optional[Callable[[str], bool]] = None,
) -> PortalRun:
    run = PortalRun()
    hosts = [h.lower() for h in recipe.allowed_hosts]
    secrets = [v for v in creds.values() if v]

    def human(msg: str) -> bool:
        run.log.append("human_checkpoint")
        if ask_human and ask_human(msg):
            return True
        run.status = "needs_you"
        return False

    def on_allowed_host() -> bool:
        if driver.host().lower() not in hosts:
            run.status = "blocked"
            run.log.append("blocked: navigated off allowed hosts")
            return False
        return True

    try:
        for step in recipe.steps:
            run.log.append(step.action + (f" {step.url}" if step.action == "goto" else ""))
            a = step.action
            if a == "goto":
                if not url_allowed(step.url, recipe.allowed_hosts):
                    run.status = "blocked"
                    return run
                driver.goto(step.url)
            elif a == "click":
                driver.click(step.selector)
            elif a == "wait_for":
                driver.wait_for(step.selector)
            elif a == "fill_username":
                driver.fill(step.selector, creds["username"])
            elif a == "fill_password":
                driver.fill(step.selector, creds["password"])
            elif a == "human_checkpoint":
                if not human(step.message):
                    return run
            elif a == "collect_pdfs":
                run.pdfs.extend(driver.collect_pdfs(step.selector, recipe.max_pdfs - len(run.pdfs)))
            if not on_allowed_host():
                return run
            if any(driver.exists(c) for c in recipe.challenge_selectors):  # 2FA / CAPTCHA: never bypass
                if not human("The site is asking for a verification step. Complete it in the browser window."):
                    return run
                if not on_allowed_host():
                    return run
    except Exception as e:  # driver errors may echo page text: log the type only, scrub anyway
        run.status = "failed"
        msg = type(e).__name__
        for s in secrets:
            msg = msg.replace(s, "***")
        run.log.append(f"error: {msg}")
    finally:
        driver.close()
    return run


class RecipePortalFetcher:
    """ingest.PortalFetcher for the agent path + `candidates` for the ingestion pipeline. Explicit user trigger only."""

    def __init__(self, vault, driver_factory: Callable[[PortalRecipe], BrowserDriver],
                 ask_human: Optional[Callable[[str], bool]] = None):
        self.vault, self.driver_factory, self.ask_human = vault, driver_factory, ask_human
        self.last_runs: Dict[str, PortalRun] = {}

    def _run(self, provider: str) -> Optional[PortalRun]:
        from .store import load_credentials, load_recipe

        recipe = load_recipe(self.vault, provider)
        if recipe is None:
            return None
        run = run_recipe(recipe, self.driver_factory(recipe), load_credentials(self.vault, provider), self.ask_human)
        self.last_runs[provider] = run
        return run

    def candidates(self, provider: str) -> List[Candidate]:
        run = self._run(provider)
        if run is None:
            raise PermissionError(f"{provider}: no portal recipe saved")
        if run.status != "ok":
            raise PermissionError(f"{provider}: {run.status} (see run log)")
        from .store import load_recipe

        r = load_recipe(self.vault, provider)
        return [Candidate(b, "portal", provider, r.provider, r.category) for b in run.pdfs]

    def fetch(self, provider: str) -> List[dict]:
        out = []
        for c in self.candidates(provider):
            try:
                out.append({"id": validate_pdf(c.data)[:16], "text": pdf_text(c.data)})
            except UnsafeFile:
                continue
        return out


class PlaywrightDriver:
    """Real browser: dedicated 0700 persistent profile, navigation limited to allowed hosts, popups closed, downloads quarantined."""

    def __init__(self, hosts: List[str], profile_dir: str, headless: bool = False, timeout_ms: int = 20000):
        from playwright.sync_api import sync_playwright  # lazy: optional dependency

        os.makedirs(profile_dir, mode=0o700, exist_ok=True)
        os.chmod(profile_dir, 0o700)
        self.hosts = [h.lower() for h in hosts]
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(profile_dir, headless=headless, accept_downloads=True)
        self._ctx.set_default_timeout(timeout_ms)
        self._ctx.route("**/*", self._route)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._ctx.on("page", lambda p: p.close() if p is not self.page else None)  # no popups

    def _route(self, route):
        req = route.request
        host = (re.match(r"^\w+://([^/:?#]+)", req.url) or [None, ""])[1].lower()
        if req.is_navigation_request() and host not in self.hosts:
            return route.abort()
        route.continue_()

    def goto(self, url): self.page.goto(url)
    def click(self, selector): self.page.click(selector)
    def fill(self, selector, value): self.page.fill(selector, value)
    def wait_for(self, selector): self.page.wait_for_selector(selector)
    def exists(self, selector): return self.page.locator(selector).count() > 0
    def host(self): return (re.match(r"^\w+://([^/:?#]+)", self.page.url) or [None, ""])[1]

    def collect_pdfs(self, selector, limit):
        out = []
        links = self.page.locator(selector or "a[href$='.pdf']")
        with tempfile.TemporaryDirectory(prefix="pai_dl_") as q:  # 0700, removed on exit
            for i in range(min(links.count(), limit)):
                with self.page.expect_download() as dl:
                    links.nth(i).click()
                path = os.path.join(q, f"{i}.bin")  # never the remote filename
                dl.value.save_as(path)
                with open(path, "rb") as f:
                    out.append(f.read(16 * 1024 * 1024 + 1))
                os.unlink(path)
        return out

    def wait_until_clear(self, selectors: List[str], seconds: int = 180) -> bool:
        """Human-in-the-loop: the user completes 2FA/CAPTCHA in the visible window; poll until the marker disappears."""
        import time

        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if not any(self.exists(c) for c in selectors):
                return True
            self.page.wait_for_timeout(1000)
        return False

    def close(self):
        try:
            self._ctx.close()
        finally:
            self._pw.stop()
