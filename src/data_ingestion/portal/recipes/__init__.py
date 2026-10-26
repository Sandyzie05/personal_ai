"""Starter recipes (R10). TEMPLATES target the mock portal fixtures (tests/fixtures/portal) so the framework is
fixture-tested. They are NOT real provider recipes: selectors/hosts for a real provider must be recorded with
`playwright codegen` in a supervised run, then saved via `store.save_recipe` (re-validated by the schema)."""

from ..recipe import PortalRecipe


def template(provider: str, category: str, host: str) -> PortalRecipe:
    base = f"https://{host}"
    return PortalRecipe.model_validate({
        "provider": provider, "category": category, "allowed_hosts": [host],
        "challenge_selectors": ["#otp-input", ".captcha"],
        "steps": [
            {"action": "goto", "url": f"{base}/login"},
            {"action": "fill_username", "selector": "#user"},
            {"action": "fill_password", "selector": "#pass"},
            {"action": "click", "selector": "#submit"},
            {"action": "goto", "url": f"{base}/bills"},
            {"action": "collect_pdfs", "selector": "a.bill-pdf"},
        ],
    })


TEMPLATES = {
    "electricity": template("Mock Power", "electricity", "portal.mock-power.test"),
    "gas": template("Mock Gas", "gas", "portal.mock-gas.test"),
    "phone": template("Mock Mobile", "mobile", "portal.mock-mobile.test"),
}
