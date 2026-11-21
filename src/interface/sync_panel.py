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


def _expected(vault, s) -> None:
    st.markdown("**Expected bills**")
    st.caption(
        "Which bills should keep arriving. Each provider is checked against its newest indexed bill; when one is late "
        "(31 days monthly, 92 quarterly) a warning appears above and chat answers offer a Sync. One row per provider; "
        "the name must match the provider on the bill (adding a Gmail sender below adds it here too)."
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
    with st.form("sync_expect_form", clear_on_submit=True, border=False):
        c1, c2, c3 = st.columns([3, 2, 1], vertical_alignment="bottom")
        pick = c1.selectbox("Known provider", ["(type a new one)"] + known, key="sync_pick") if known else "(type a new one)"
        typed = c1.text_input("New provider (as in the bill)", key="sync_prov")
        cad = c2.selectbox("Cadence", list(pipeline.CADENCE_DAYS), key="sync_cad")
        if c3.form_submit_button("Add"):
            name = typed.strip() or ("" if pick.startswith("(") else pick)
            if name:
                pipeline.set_expectation(vault, name, cad)
                st.rerun()
            else:
                st.warning("Type or pick a provider.")


def _gmail(vault, on_indexed, quarantine_client) -> None:
    from src.agents.orchestration.service import GMAIL_PATTERN, find_server
    from src.data_extraction.categories import category_keys
    from src.data_ingestion import gmail_bills

    st.markdown("**Bills from Gmail (email text)**")
    st.caption(
        "For providers that email the amount instead of a PDF. Only senders listed here are read (read-only); each email "
        "goes through the quarantine extractor, which returns just the total, due date and period. The email itself is "
        "never stored or indexed."
    )
    server = find_server(vault, GMAIL_PATTERN)
    if not server:
        st.caption('Gmail is not connected: add an MCP server named "Gmail" in Agents → MCP Servers and authorize it.')
        return
    for row in gmail_bills.senders(vault):
        a, b, c = st.columns([3, 3, 1])
        a.text(row["provider"])
        b.caption(f"{row['category']} · from {row['domain']}")
        if c.button("Remove", key=f"gm_rm_{row['provider']}"):
            gmail_bills.remove_sender(vault, row["provider"])
            st.rerun()
    with st.form("gmail_sender_form", clear_on_submit=True, border=False):
        c1, c2, c3, c4 = st.columns([3, 2, 3, 1], vertical_alignment="bottom")
        prov = c1.text_input("Provider", placeholder="Rocky Mountain Power")
        cat = c2.selectbox("Category", category_keys())
        dom = c3.text_input("Sender domain", placeholder="rockymountainpower.net")
        if c4.form_submit_button("Add"):
            try:
                gmail_bills.add_sender(vault, prov, cat, dom)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    if st.button("Sync bills from Gmail", icon=":material/sync:", disabled=not gmail_bills.senders(vault)):
        if quarantine_client is None:
            st.error("No local model available for the quarantine extractor.")
            return
        from src.agents.mcp import McpManager
        from src.agents.orchestration.ingest import ReadOnlyMcp

        with st.status("Reading bill emails (read-only)...") as status:
            try:
                rep = gmail_bills.sync(vault, ReadOnlyMcp(McpManager(vault), server), quarantine_client, on_indexed)
                status.update(label=rep.summary(), state="complete" if not (rep.failed or rep.needs_you) else "error")
            except Exception as e:
                status.update(label=f"Gmail sync failed ({type(e).__name__}). Nothing was stored.", state="error")


def render(vault, on_indexed: Callable[[str, Any], None], quarantine_client: Any = None) -> None:
    st.subheader(":material/sync: Sync")
    s = pipeline.sync_status(vault)
    st.caption("Documents: " + (", ".join(f"{n} {k}" for k, n in sorted(s["counts"].items())) or "none ingested yet"))
    for o in s["overdue"]:
        st.warning(f"{o['provider']}: last bill period ended {o['last_period_end'] or 'never'}"
                   + (f", {o['days_overdue']} days overdue" if o["days_overdue"] is not None else ""))
    _expected(vault, s)
    _gmail(vault, on_indexed, quarantine_client)
    recipes = store.list_recipes(vault)
    if not recipes:
        st.caption("No portal recipes saved. Prefer paperless email (Gmail) where the provider offers it.")
    for p in recipes:
        if st.button(f"Fetch bills: {p}", key=f"sync_fetch_{p}", help="Explicit trigger. You complete any 2FA/CAPTCHA yourself."):
            try:
                _fetch(vault, p, on_indexed)
            except Exception as e:
                st.error(f"{type(e).__name__}: run did not complete")
