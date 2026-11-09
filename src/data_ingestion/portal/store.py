"""Recipes and credentials live in the encrypted vault (internal keys); credentials are loaded by code only."""

import re
from typing import Dict, List, Optional

from .recipe import PortalRecipe

KEY = "portal_recipes"


def _slug(provider: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", provider.lower()).strip("_")


def save_recipe(vault, recipe: PortalRecipe) -> None:
    d = vault.retrieve_data(KEY) or {}
    d[recipe.provider] = recipe.model_dump(mode="json")
    vault.store_data(KEY, d, encrypt=True)


def load_recipe(vault, provider: str) -> Optional[PortalRecipe]:
    raw = (vault.retrieve_data(KEY) or {}).get(provider)
    return PortalRecipe.model_validate(raw) if raw else None  # re-validated on every load


def list_recipes(vault) -> List[str]:
    return sorted((vault.retrieve_data(KEY) or {}).keys())


def save_credentials(vault, provider: str, username: str, password: str) -> None:
    vault.store_data(f"portal_cred_{_slug(provider)}", {"username": username, "password": password}, encrypt=True)


def load_credentials(vault, provider: str) -> Dict[str, str]:
    c = vault.retrieve_data(f"portal_cred_{_slug(provider)}")
    if not c:
        raise PermissionError(f"{provider}: no credentials saved")
    return {"username": c["username"], "password": c["password"]}
