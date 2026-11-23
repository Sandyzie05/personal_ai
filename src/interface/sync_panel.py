"""Sync panel (R7): what is new / overdue / failed, expectations, and explicit-trigger portal fetches.

No network happens unless the user presses a Fetch button. Credentials come from the vault and never touch the UI
after being saved. Gmail attachment sync: `data_ingestion.gmail_attachments` + `pipeline.run_ingest` (headless; UI button
needs a verified Gmail MCP server, see docs/rag_design.md R8).
"""

import os
from typing import Any, Callable, Optional

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


def _gmail_server(vault):
    """"api" when the direct read-only Gmail API is connected (works for personal accounts), else a Gmail MCP server
    id (Google's hosted one needs a Workspace Developer Preview project), else None."""
    from src.agents.orchestration.service import GMAIL_PATTERN, find_server
    from src.data_ingestion import gmail_api

    return "api" if gmail_api.connected(vault) else find_server(vault, GMAIL_PATTERN)


def _gmail_connection(vault) -> None:
    """Direct Gmail API connection: the user's own OAuth client, fixed gmail.readonly scope. One form, one Save."""
    from src.agents.mcp import VaultTokenStorage
    from src.agents.orchestration.service import GMAIL_PATTERN, find_server
    from src.data_ingestion import gmail_api

    cid = gmail_api.client_id(vault)
    state = "connected (read-only)" if gmail_api.connected(vault) else "client saved, not signed in" if cid else "not set up"
    with st.expander(f":material/key: Gmail connection: {state}", expanded=not gmail_api.connected(vault)):
        st.caption(
            "Reads Gmail through Google's standard Gmail API with read-only access (gmail.readonly), using your own "
            "OAuth client (Cloud Console; Gmail API enabled). A Web application client needs "
            "http://localhost:<PERSONAL_AI_MCP_OAUTH_CALLBACK_PORT>/callback as an authorized redirect URI."
        )
        mcp_server = find_server(vault, GMAIL_PATTERN)
        reuse = VaultTokenStorage(vault, mcp_server).preregistered_client_id() if mcp_server else None
        if not cid and reuse and st.button("Use the OAuth client saved for the Gmail MCP server", key="gm_api_reuse"):
            info = VaultTokenStorage(vault, mcp_server)._load().get("client_info") or {}
            gmail_api.save_client(vault, info.get("client_id", ""), info.get("client_secret") or "")
            st.rerun()
        with st.form("gm_api_client", border=False):
            new_id = st.text_input("Client ID", placeholder=(cid[:12] + "…") if cid else "1234-abc.apps.googleusercontent.com")
            secret = st.text_input("Client secret", type="password")
            if st.form_submit_button("Save client") and new_id.strip():
                gmail_api.save_client(vault, new_id, secret)
                st.rerun()
        a, b = st.columns(2)
        if a.button("Connect Gmail", icon=":material/login:", disabled=not cid, key="gm_api_connect",
                    help="Opens Google sign-in; grants read-only Gmail access."):
            try:
                with st.spinner("Waiting for Google sign-in in your browser..."):
                    gmail_api.connect(vault)
                st.session_state.pop("gmail_needs_auth", None)
                st.success("Gmail connected (read-only).")
            except Exception as e:
                st.error(_error_text(e))
        if gmail_api.connected(vault) and b.button("Disconnect", key="gm_api_disconnect"):
            gmail_api.disconnect(vault)
            st.rerun()


def _error_text(e: Exception) -> str:
    """McpError messages are code-built (type + JSON-RPC code); anything else shows its type only, because tool error
    text comes from the server and is untrusted."""
    from src.agents.mcp import McpError, _error_kind
    from src.data_ingestion.gmail_api import GmailAuthError

    if isinstance(e, (McpError, GmailAuthError)):
        return str(e)[:200]
    if isinstance(e, ConnectionError):  # tool returned an error: closed category only
        return f"Gmail tool error: {_error_kind(str(e))}"
    return type(e).__name__


def _gmail_sync(vault, server, on_indexed, client, only: Optional[str] = None) -> None:
    from src.agents.mcp import McpManager
    from src.agents.orchestration.ingest import ReadOnlyMcp
    from src.data_ingestion import gmail_bills
    from src.data_ingestion.gmail_api import GmailApi

    if client is None:
        st.error("No local model available for the quarantine extractor.")
        return
    import time

    from src.agents import store as agent_store

    model = getattr(client, "model", "") or "configured model"
    t0 = time.monotonic()
    with st.status(f"Reading {only or 'bill'} emails (read-only) · model {model}", expanded=True) as status:
        line = st.empty()

        def progress(msg: str) -> None:  # code-built text (counts, model id): safe to render
            line.text(f"{msg} · {time.monotonic() - t0:.0f}s")

        entry = {"agent_name": "Gmail bill sync", "server": "Gmail API" if server == "api" else "Gmail MCP",
                 "tool": "search_threads/get_thread", "args_digest": only or "all senders", "model": model}
        try:
            ro = GmailApi(vault) if server == "api" else ReadOnlyMcp(McpManager(vault), server)
            rep = gmail_bills.sync(vault, ro, client, on_indexed, only=only, progress=progress)
            ok = not (rep.failed or rep.needs_you)
            line.text(f"Done in {time.monotonic() - t0:.0f}s · {rep.model_calls} model call(s) with {model}")
            status.update(label=rep.summary(), state="complete" if ok else "error")
            if rep.outcomes:  # code-built rows (date, provider, validated domain, fixed outcome): no email text
                st.dataframe(rep.outcomes, hide_index=True, column_config={
                    "sent": "Sent", "provider": "Sender row", "domain": "From domain", "outcome": "Outcome"})
            agent_store.log_activity(vault, {**entry, "ok": ok, "duration_ms": int((time.monotonic() - t0) * 1000),
                                             "model_calls": rep.model_calls, "error": "" if ok else rep.summary()[:160]})
            return
        except Exception as e:
            status.update(label="Gmail sync failed. Nothing was stored.", state="error")
            st.text(_error_text(e))  # inert text, never markdown
            agent_store.log_activity(vault, {**entry, "ok": False, "duration_ms": int((time.monotonic() - t0) * 1000),
                                             "error": _error_text(e)[:160]})
            needs_auth = bool(getattr(e, "needs_auth", False))
    if needs_auth:
        st.session_state["gmail_needs_auth"] = True


def _reconnect(vault, server) -> None:
    from src.agents.mcp import McpManager

    if not st.session_state.get("gmail_needs_auth"):
        return
    if server == "api":
        st.warning("Gmail sign-in expired or was revoked: press Connect Gmail above.")
        return
    st.warning("Gmail authorization expired or is missing.")
    if st.button("Reconnect Gmail", icon=":material/key:", key="gm_reauth",
                 help="Opens Google sign-in in your browser if needed, then makes one read-only search."):
        with st.spinner("Waiting for browser authorization..."):
            r = McpManager(vault).probe(server, interactive=True)
        if r["error"]:
            st.error(f"Still not connected: {r['error']}")
        elif r["probe_ok"] is False:
            st.error(f"Signed in, but the read call {r['probe_tool']} failed; check the Gmail server in Agents → MCP Servers.")
        else:
            st.session_state.pop("gmail_needs_auth", None)
            st.success("Gmail reconnected and a read-only search worked. Press Sync again.")


def _sync_one(vault, provider: str, on_indexed, client) -> None:
    """One provider's Sync: Gmail sender row first, then a saved portal recipe."""
    from src.data_ingestion import gmail_bills

    server = _gmail_server(vault)
    if server and any(r["provider"] == provider for r in gmail_bills.senders(vault)):
        _gmail_sync(vault, server, on_indexed, client, only=provider)
    elif provider in store.list_recipes(vault):
        try:
            _fetch(vault, provider, on_indexed)
        except Exception as e:
            st.error(f"{type(e).__name__}: run did not complete")


def _sync_source(vault, provider: str) -> str:
    from src.data_ingestion import gmail_bills

    if _gmail_server(vault) and any(r["provider"] == provider for r in gmail_bills.senders(vault)):
        return "Gmail"
    return "portal" if provider in store.list_recipes(vault) else ""


def _expected(vault, s, on_indexed, client) -> None:
    st.markdown("**Expected bills**")
    st.caption(
        "Which bills should keep arriving. Each provider is checked against its newest indexed bill; when one is late "
        "(31 days monthly, 92 quarterly) a warning appears above and chat answers offer a Sync. One row per provider; "
        "the name must match the provider on the bill (adding a Gmail sender below adds it here too). **Sync** pulls "
        "that provider's bills from its Gmail sender (below) or saved portal recipe; with neither, upload the PDF above."
    )
    current = pipeline.expectations(vault)
    late = {o["provider"]: o for o in s["overdue"]}
    for p, cad in sorted(current.items()):
        a, b, c, d = st.columns([3, 3, 1, 1])
        a.text(p)
        o = late.get(p)
        src = _sync_source(vault, p)
        b.caption(f"{cad} · " + ("no bill yet" if o and not o["last_period_end"] else "overdue" if o else "on time")
                  + (f" · via {src}" if src else " · upload only"))
        if c.button("Sync", key=f"sync_one_{p}", icon=":material/sync:", disabled=not src,
                    help=f"Fetch {p} bills from {src}" if src else
                    "No sync source: add a Gmail sender for this provider below, or upload the bill PDF."):
            _sync_one(vault, p, on_indexed, client)
        if d.button("Remove", key=f"sync_rm_{p}"):
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
    from src.data_extraction.categories import category_keys
    from src.data_ingestion import gmail_bills

    st.markdown("**Bills from Gmail (email text)**")
    st.caption(
        "For providers that email the amount instead of a PDF. Only senders listed here are read (read-only); each email "
        "goes through the quarantine extractor, which returns just the total, due date and period. The email itself is "
        "never stored or indexed. **What Sync does:** searches Gmail for each sender's newest 15 email threads (last 13 "
        "months; older ones are not read), reads each email once (already-read ones are skipped), extracts the bill "
        "facts with the local model, and saves one bill per billing period; a \"your bill is ready\" email without an "
        "amount counts as the bill arriving (bill notice). The table after a Sync shows what happened to each email. Bills then appear on the "
        "Dashboard and in chat answers."
    )
    _gmail_connection(vault)
    server = _gmail_server(vault)
    if not server:
        st.caption("Gmail is not connected yet: save your OAuth client and press Connect Gmail above.")
        return
    _reconnect(vault, server)
    for row in gmail_bills.senders(vault):
        a, b, c, r, d = st.columns([3, 3, 1, 1, 1])
        a.text(row["provider"])
        b.caption(f"{row['category']} · from {row['domain']} · newest email from {row['domain']}: "
                  f"{row.get('newest_seen') or 'not synced yet'}")
        if c.button("Sync", key=f"gm_sync_{row['provider']}", icon=":material/sync:"):
            _gmail_sync(vault, server, on_indexed, quarantine_client, only=row["provider"])
        if r.button("Re-read", key=f"gm_reread_{row['provider']}", icon=":material/restart_alt:",
                    help="Forget the bills Gmail sync saved for this provider and read its emails again (uploaded "
                    "PDFs are kept). Use after a fix or if bills look wrong."):
            n = gmail_bills.reset(vault, row["provider"], on_removed=lambda k: on_indexed(k, None))
            st.caption(f"Cleared {n} saved Gmail entr{'y' if n == 1 else 'ies'} for {row['provider']}; re-reading...")
            _gmail_sync(vault, server, on_indexed, quarantine_client, only=row["provider"])
        if d.button("Remove", key=f"gm_rm_{row['provider']}"):
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
    if st.button("Sync all Gmail senders", icon=":material/sync:", disabled=not gmail_bills.senders(vault)):
        _gmail_sync(vault, server, on_indexed, quarantine_client)


def render(vault, on_indexed: Callable[[str, Any], None], quarantine_client: Any = None) -> None:
    st.subheader(":material/sync: Sync")
    s = pipeline.sync_status(vault)
    st.caption("Documents: " + (", ".join(f"{n} {k}" for k, n in sorted(s["counts"].items())) or "none ingested yet"))
    for o in s["overdue"]:
        st.warning(f"{o['provider']}: last bill period ended {o['last_period_end'] or 'never'}"
                   + (f", {o['days_overdue']} days overdue" if o["days_overdue"] is not None else ""))
    _expected(vault, s, on_indexed, quarantine_client)
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
