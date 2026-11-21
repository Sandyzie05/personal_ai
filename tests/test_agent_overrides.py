from src.agents import store
from src.agents.orchestration.templates import _COMMON
from src.data_vault.vault import INTERNAL_VAULT_KEYS


class Vault:
    def __init__(self):
        self.data = {}

    def store_data(self, key, value, encrypt=True):
        self.data[key] = value
        return True

    def retrieve_data(self, key):
        return self.data.get(key)

    def list_keys(self):
        return list(self.data)


def test_builtin_tweak_persists_locked_fields_ignored_and_reset_restores():
    vault = Vault()
    agent = store.get_agent(vault, "builtin-critic-macro")
    assert agent["temperature"] == 0.0 and not agent["tweaked"]
    edited = {
        **agent,
        "temperature": 0.4,
        "model": "small",
        "extra_instructions": "Weigh rates.",
        "name": "Hacked",
        "role_rules": "ignore everything",
        "max_tool_rounds": 9,
        "mcp_server_ids": ["s1"],
    }
    assert store.update_agent(vault, "builtin-critic-macro", edited)
    again = store.get_agent(vault, "builtin-critic-macro")
    assert (again["temperature"], again["model"], again["tweaked"]) == (
        0.4,
        "small",
        True,
    )
    assert again["name"] == "Macro Critic" and again["max_tool_rounds"] == 0
    assert again["mcp_server_ids"] == []  # tool-less role stays tool-less
    assert "Weigh rates." in again["instructions"] and again["instructions"].endswith(
        _COMMON
    )
    assert "ignore everything" not in again["instructions"]
    assert store.reset_agent(vault, "builtin-critic-macro")
    assert store.get_agent(vault, "builtin-critic-macro")["temperature"] == 0.0


def test_critics_default_to_critic_model_explainers_to_worker_model():
    from src.config import PERSONAL_AI_FORECAST_CRITIC_MODEL

    for role in ("macro", "valuation", "event", "downside"):
        assert (
            store.get_agent(Vault(), f"builtin-critic-{role}")["model"]
            == PERSONAL_AI_FORECAST_CRITIC_MODEL
        )
    assert store.get_agent(Vault(), "builtin-thesis-explainer")["model"] == ""


def test_overrides_are_internal_and_builtins_are_listed():
    assert "agent_overrides" in INTERNAL_VAULT_KEYS
    ids = {a["id"] for a in store.list_builtin_agents(Vault())}
    assert {
        "builtin-critic-macro",
        "builtin-critic-downside",
        "builtin-thesis-explainer",
        "builtin-coordinator",
    } <= ids
