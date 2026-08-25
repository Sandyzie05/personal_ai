"""Dashboard page: at-a-glance widgets over the vault's documents, with a
user-configurable set of widgets to show.

Mirrors `upload.py`'s pattern - a module-level `render_dashboard_page(vault)`
function called from a thin wrapper method in `main.py` - and consumes the
same pure aggregation helpers from `dashboard_data.py` that are unit tested
independently of Streamlit/vault.
"""

from typing import Any, Callable, Dict, List, Tuple

import streamlit as st

from src.data_extraction.categories import get_category
from src.data_vault import DataVault, is_internal_vault_key
from src.interface import dashboard_data

DASHBOARD_CONFIG_KEY = "dashboard_config"


def _render_overview(records: List[Dict[str, Any]]) -> None:
    total_docs = dashboard_data.total_documents(records)
    counts = dashboard_data.count_by_category(records)
    categories_in_use = sum(1 for _key, _label, count in counts if count > 0)
    spend = dashboard_data.total_spend(records)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total documents", total_docs)
    with col2:
        st.metric("Categories in use", categories_in_use)
    with col3:
        st.metric("Total tracked spend", f"${spend:,.2f}")
    st.caption(
        "Spend total covers bill-like categories only (electricity, gas, "
        "credit card, checking, mobile) - excludes brokerage, whose total "
        "is portfolio value, not spend."
    )


def _render_by_category(records: List[Dict[str, Any]]) -> None:
    counts = dashboard_data.count_by_category(records)
    chart_data = {label: count for _key, label, count in counts}
    st.bar_chart(chart_data)


def _render_spend_by_category(records: List[Dict[str, Any]]) -> None:
    totals = dashboard_data.spend_by_category(records)
    chart_data = {label: total for label, total in totals}
    st.bar_chart(chart_data)


def _render_spend_over_time(records: List[Dict[str, Any]]) -> None:
    series = dashboard_data.spend_over_time(records)
    if not series:
        st.info("No dated spend data yet.")
        return
    chart_data = {month: total for month, total in series}
    st.bar_chart(chart_data)


def _render_recent_uploads(records: List[Dict[str, Any]]) -> None:
    recent = dashboard_data.recent_uploads(records, 5)
    if not recent:
        st.caption("No uploads yet.")
        return
    for record in recent:
        metadata = record.get("metadata") or {}
        filename = metadata.get("original_filename", record.get("storage_key"))
        category_key = metadata.get("category")
        category_label = get_category(category_key).label if category_key else "-"
        upload_timestamp = metadata.get("upload_timestamp", "unknown")
        st.write(f"📄 **{filename}** - {category_label} - {upload_timestamp}")


# Ordered registry of (widget_key, display_label, render_fn) tuples. Order
# here is the order widgets render in (and the order shown in the
# customization multiselect).
WIDGETS: List[Tuple[str, str, Callable[[List[Dict[str, Any]]], None]]] = [
    ("overview", "📈 Overview", _render_overview),
    ("by_category", "📂 Documents by Category", _render_by_category),
    ("spend_by_category", "💰 Spend by Category", _render_spend_by_category),
    ("spend_over_time", "📅 Spend Over Time", _render_spend_over_time),
    ("recent_uploads", "🕐 Recent Uploads", _render_recent_uploads),
]

_WIDGET_LABELS = {key: label for key, label, _render_fn in WIDGETS}
_ALL_WIDGET_KEYS = [key for key, _label, _render_fn in WIDGETS]


def _load_records(vault: DataVault) -> List[Dict[str, Any]]:
    keys = vault.list_keys()
    file_keys = [k for k in keys if not is_internal_vault_key(k)]

    records: List[Dict[str, Any]] = []
    for key in file_keys:
        try:
            data = vault.retrieve_data(key)
        except Exception:
            continue
        if not data or not isinstance(data, dict):
            continue
        records.append(
            {
                "storage_key": key,
                "metadata": data.get("metadata", {}),
                "extraction": data.get("extraction"),
            }
        )
    return records


def _load_enabled_widgets(vault: DataVault) -> List[str]:
    try:
        config = vault.retrieve_data(DASHBOARD_CONFIG_KEY)
    except Exception:
        config = None
    if not config or not isinstance(config, dict):
        return list(_ALL_WIDGET_KEYS)
    enabled = config.get("enabled_widgets")
    if not isinstance(enabled, list):
        return list(_ALL_WIDGET_KEYS)
    return [key for key in enabled if key in _WIDGET_LABELS]


def render_dashboard_page(vault: DataVault) -> None:
    """Render the Dashboard page: customizable at-a-glance widgets."""
    st.header("📊 Dashboard")
    st.caption("A single at-a-glance view of the documents in your vault")

    records = _load_records(vault)

    if not records:
        st.info("No documents yet - upload something first.")
        return

    enabled_widgets = _load_enabled_widgets(vault)

    with st.expander("⚙️ Customize widgets"):
        selection = st.multiselect(
            "Widgets to show",
            options=_ALL_WIDGET_KEYS,
            default=enabled_widgets,
            format_func=lambda key: _WIDGET_LABELS.get(key, key),
        )
        if st.button("💾 Save"):
            vault.store_data(DASHBOARD_CONFIG_KEY, {"enabled_widgets": selection})
            st.rerun()

    enabled_set = set(enabled_widgets)
    for widget_key, _label, render_fn in WIDGETS:
        if widget_key not in enabled_set:
            continue
        with st.container():
            render_fn(records)
        st.divider()
