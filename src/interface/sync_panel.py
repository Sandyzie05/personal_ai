"""Sync panel (R7): what is new / overdue / failed, expectations, and explicit-trigger portal fetches.

No network happens unless the user presses a Fetch button. Credentials come from the vault and never touch the UI
after being saved. Gmail attachment sync: `data_ingestion.gmail_attachments` + `pipeline.run_ingest` (headless; UI button
needs a verified Gmail MCP server, see docs/rag_design.md R8).
"""

import os
from typing import Any, Callable

import streamlit as st

from src.data_ingestion import pipeline
from src.data_ingestion.portal import store
from src.data_ingestion.portal.runner import PlaywrightDriver, RecipePortalFetcher

PROFILE_ROOT = os.path.expanduser("~/.personal_ai_vault/.portal_profiles")


def _fetch(vault, provider: str, on_indexed: Callable[[str, Any], None]) -> None:
    drivers = []

    def factory(recipe):
        d = PlaywrightDriver(recipe.allowed_hosts, os.path.join(PROFILE_ROOT, store._slug(recipe.provider)), headless=False)
        drivers.append((d, recipe))
        return d

    def ask_human(msg: str) -> bool:
        st.toast(msg)
        d, recipe = drivers[-1]
        return d.wait_until_clear(recipe.challenge_selectors)

    fetcher = RecipePortalFetcher(vault, factory, ask_human)
    with st.status(f"Fetching {provider} (a browser window will open)...") as status:
        rep = pipeline.run_ingest(vault, [lambda: fetcher.candidates(provider)], on_indexed=on_indexed)
        run = fetcher.last_runs.get(provider)
        status.update(label=rep.summary(), state="complete" if not (rep.failed or rep.needs_you) else "error")
    if run:
        st.caption("Run log: " + " > ".join(run.log))  # actions and hosts only


def render(vault, on_indexed: Callable[[str, Any], None]) -> None:
    st.subheader(":material/sync: Sync")
    s = pipeline.sync_status(vault)
    st.caption("Documents: " + (", ".join(f"{n} {k}" for k, n in sorted(s["counts"].items())) or "none ingested yet"))
    for o in s["overdue"]:
        st.warning(f"{o['provider']}: last bill period ended {o['last_period_end'] or 'never'}"
                   + (f", {o['days_overdue']} days overdue" if o["days_overdue"] is not None else ""))
    with st.expander("Expected bills"):
        st.caption(
            "Tell the app which bills should keep arriving. Each provider you add here is checked against the newest "
            "indexed bill from that provider; when one is late (31 days monthly, 92 quarterly) a warning appears above "
            "and chat answers offer a Sync. Add one row per provider; the name must match the provider on the bill."
        )
        current = pipeline.expectations(vault)
        late = {o["provider"]: o for o in s["overdue"]}
        for p, cad in sorted(current.items()):
            a, b, c = st.columns([3, 3, 1])
            a.text(p)
            o = late.get(p)
            b.caption(f"{cad} · " + ("no bill yet" if o and not o["last_period_end"] else "overdue" if o else "on time"))
            if c.button("Remove", key=f"sync_rm_{p}"):
                pipeline.remove_expectation(vault, p)
                st.rerun()
        known = [p for p in pipeline.known_providers(vault) if p not in current]
        c1, c2, c3 = st.columns([3, 2, 1])
        pick = c1.selectbox("Provider", known + ["Other (type below)"], key="sync_pick") if known else "Other (type below)"
        typed = c1.text_input("Provider (as in the bill)", key="sync_prov") if pick.startswith("Other") else pick
        cad = c2.selectbox("Cadence", list(pipeline.CADENCE_DAYS), key="sync_cad")
        if c3.button("Add", key="sync_add") and typed.strip():
            pipeline.set_expectation(vault, typed.strip(), cad)
            st.rerun()
    recipes = store.list_recipes(vault)
    if not recipes:
        st.caption("No portal recipes saved. Prefer paperless email (Gmail) where the provider offers it.")
    for p in recipes:
        if st.button(f"Fetch bills: {p}", key=f"sync_fetch_{p}", help="Explicit trigger. You complete any 2FA/CAPTCHA yourself."):
            try:
                _fetch(vault, p, on_indexed)
            except Exception as e:
                st.error(f"{type(e).__name__}: run did not complete")
