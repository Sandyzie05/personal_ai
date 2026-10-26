"""Portal recipe schema (R9 / S-R5): a closed action set, allowlisted hosts, no payment/settings URLs. Recipes are data, never code."""

import re
from typing import List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

HOST = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
DENY = re.compile(r"pay|billing-?settings|autopay|settings|profile|password|security|update|delete|cancel|transfer", re.I)
SEL = r"^[\w\s#.\[\]=\"'>:*^$~|(),@-]{1,200}$"
Action = Literal["goto", "fill_username", "fill_password", "click", "wait_for", "human_checkpoint", "collect_pdfs"]


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Action
    url: Optional[str] = None
    selector: Optional[str] = Field(default=None, pattern=SEL)
    message: Optional[str] = Field(default=None, max_length=160)  # shown to the user only


class PortalRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 .&'-]{0,39}$")
    category: str = Field(pattern=r"^[a-z_]{2,30}$")
    allowed_hosts: List[str] = Field(min_length=1, max_length=4)
    steps: List[Step] = Field(min_length=1, max_length=30)
    challenge_selectors: List[str] = Field(default_factory=list, max_length=6)  # 2FA/CAPTCHA markers -> human
    max_pdfs: int = Field(default=3, ge=1, le=6)

    @model_validator(mode="after")
    def _check(self):
        for h in self.allowed_hosts:
            if not re.match(HOST, h):
                raise ValueError("allowed_hosts must be bare lowercase hostnames")
        for c in self.challenge_selectors:
            if not re.match(SEL, c):
                raise ValueError("bad challenge selector")
        for s in self.steps:
            if s.action == "goto":
                if not s.url or not url_allowed(s.url, self.allowed_hosts):
                    raise ValueError("goto url must be https on an allowed host and not a payment/settings page")
            elif s.action in ("click", "wait_for", "fill_username", "fill_password"):
                if not s.selector:
                    raise ValueError(f"{s.action} needs a selector")
                if s.action == "click" and DENY.search(s.selector):
                    raise ValueError("click selector looks like a payment/settings control")
            elif s.action == "human_checkpoint" and not s.message:
                raise ValueError("human_checkpoint needs a message")
        return self


def url_allowed(url: str, hosts: List[str]) -> bool:
    u = urlparse(url)
    return (u.scheme == "https" and (u.hostname or "").lower() in [h.lower() for h in hosts]
            and not u.username and not DENY.search(u.path + "?" + (u.query or "")))
