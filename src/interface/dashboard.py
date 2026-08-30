"""Dashboard page: at-a-glance widgets over the vault's documents, with a
user-configurable set of widgets to show.

Mirrors `upload.py`'s pattern - a module-level `render_dashboard_page(vault)`
function called from a thin wrapper method in `main.py` - and consumes the
same pure aggregation helpers from `dashboard_data.py` that are unit tested
independently of Streamlit/vault.

Chart colors below are the fixed roles from this project's dataviz design
reference (categorical identity order, one sequential hue for magnitude, and
reserved status colors for a good/bad delta) so color always means the same
thing no matter which widget it appears in.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

import plotly.graph_objects as go
import streamlit as st

from src.data_extraction.categories import CATEGORIES
from src.data_vault import DataVault, is_internal_vault_key
from src.interface import dashboard_data
from src.interface import theme

DASHBOARD_CONFIG_KEY = "dashboard_config"

# --- Color roles ------------------------------------------------------------
# Fixed categorical order (identity - "which category is this"); never cycled
# or reassigned based on which categories happen to be present, so a given
# category always reads as the same color across widgets/sessions.
_CATEGORICAL_PALETTE = [
    "#2a78d6",  # 1 blue
    "#eb6834",  # 2 orange
    "#1baf7a",  # 3 aqua
    "#eda100",  # 4 yellow
    "#e87ba4",  # 5 magenta
    "#008300",  # 6 green
    "#4a3aa7",  # 7 violet
    "#e34948",  # 8 red
]
_SEQUENTIAL_BLUE = "#2a78d6"  # one hue for pure magnitude comparisons
_SEQUENTIAL_BLUE_FILL = "rgba(42, 120, 214, 0.10)"  # ~10% wash for area fills
_STATUS_GOOD = "#0ca30c"  # reserved: spend went down
_STATUS_CRITICAL = "#d03b3b"  # reserved: spend went up
_MUTED_INK = "#898781"
_GRIDLINE = "#e1e0d9"


def _surfaces() -> Dict[str, str]:
    """Active chart surface colors for the current theme mode.

    Pulled per-render from `theme` so dark mode paints the charts for a
    dark backdrop. Categorical/sequential hue roles stay mode-independent;
    only these surfaces change.
    """
    surfaces = theme.chart_surfaces()
    surfaces.setdefault("gridline", _GRIDLINE)
    surfaces.setdefault("pie_border", "#ffffff")
    surfaces.setdefault("fill", "rgba(42, 120, 214, 0.10)")
    surfaces.setdefault("font", "#1f2328")
    return surfaces


def _render_overview(records: List[Dict[str, Any]]) -> None:
    total_docs = dashboard_data.total_documents(records)
    counts = dashboard_data.count_by_category(records)
    categories_in_use = sum(1 for _key, _label, count in counts if count > 0)
    spend = dashboard_data.total_spend(records)
    mom_change = dashboard_data.month_over_month_change(records)

    # The KPI row is the "so what" of the page: total spend gets top billing
    # as a hero figure with its trend, while document/category counts are
    # supporting context at a smaller weight - all grouped in a bordered
    # card so the row reads as one unit, distinct from the widgets below it.
    with st.container(border=True):
        hero_col, doc_col, cat_col = st.columns([2, 1, 1])
        with hero_col:
            st.caption("Total tracked spend")
            st.markdown(
                f"<div style='font-size:2.75rem; font-weight:600; "
                f"line-height:1.15;'>${spend:,.2f}</div>",
                unsafe_allow_html=True,
            )
            if mom_change is None:
                st.caption("Not enough dated history yet for a month-over-month trend.")
            else:
                # Spend rising is the "bad" direction here, so it borrows the
                # reserved critical/good status colors rather than a generic
                # up-is-green delta.
                arrow = "▲" if mom_change > 0 else "▼"
                color = _STATUS_CRITICAL if mom_change > 0 else _STATUS_GOOD
                st.markdown(
                    f"<span style='color:{color}; font-weight:600;'>"
                    f"{arrow} {mom_change:+.1f}%</span>"
                    f"<span style='color:{_MUTED_INK};'> vs previous month</span>",
                    unsafe_allow_html=True,
                )
        with doc_col:
            st.metric("Total documents", total_docs)
        with cat_col:
            st.metric("Categories in use", categories_in_use)

    st.caption(
        "Spend total covers bill-like categories only (electricity, gas, "
        "credit card, checking, mobile) - excludes brokerage, whose total "
        "is portfolio value, not spend."
    )


def _render_by_category(records: List[Dict[str, Any]]) -> None:
    surfaces = _surfaces()
    counts = dashboard_data.count_by_category(records)
    nonzero = [(label, count) for _key, label, count in counts if count > 0]
    if not nonzero:
        st.info("No documents yet - upload something to see the category breakdown.")
        return

    # Ascending sort so the largest count sits at the top of a horizontal bar
    # (Plotly draws the first category at the bottom of the y-axis).
    nonzero.sort(key=lambda item: item[1])
    labels = [label for label, _count in nonzero]
    values = [count for _label, count in nonzero]

    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker_color=_SEQUENTIAL_BLUE,
            text=values,
            textposition="outside",
            hovertemplate="%{y}: %{x} document(s)<extra></extra>",
        )
    )
    fig.update_layout(
        margin=dict(l=0, r=32, t=8, b=0),
        height=max(220, 44 * len(labels)),
        xaxis=dict(
            showgrid=True,
            gridcolor=surfaces["gridline"],
            zeroline=False,
            title=None,
            tickfont=dict(color=surfaces["font"]),
        ),
        yaxis=dict(showgrid=False, title=None, tickfont=dict(color=surfaces["font"])),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        bargap=0.35,
    )
    st.plotly_chart(fig)


def _render_spend_by_category(records: List[Dict[str, Any]]) -> None:
    surfaces = _surfaces()
    totals = dashboard_data.spend_by_category(records)
    nonzero = [(label, total) for label, total in totals if total > 0]
    if not nonzero:
        st.info(
            "No bill-like spend recorded yet - amounts appear once a "
            "document's extracted total is available."
        )
        return

    labels = [label for label, _total in nonzero]
    values = [total for _label, total in nonzero]
    total_spend = sum(values)
    colors = _CATEGORICAL_PALETTE[: len(labels)]

    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.62,
            sort=False,
            marker=dict(
                colors=colors, line=dict(color=surfaces["pie_border"], width=2)
            ),
            textinfo="percent",
            textposition="outside",
            hovertemplate="%{label}: $%{value:,.2f} (%{percent})<extra></extra>",
        )
    )
    fig.add_annotation(
        text=f"<b>${total_spend:,.0f}</b><br><span style='font-size:12px'>total spend</span>",
        showarrow=False,
        font=dict(size=18, color=surfaces["font"]),
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=8, b=0),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.2,
            x=0.5,
            xanchor="center",
            font=dict(color=surfaces["font"]),
        ),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig)


def _render_spend_over_time(records: List[Dict[str, Any]]) -> None:
    surfaces = _surfaces()
    series = dashboard_data.spend_over_time(records)
    if not series:
        st.info(
            "No dated spend data yet - statements need a recognized statement "
            "period to appear here."
        )
        return

    mom_change = dashboard_data.month_over_month_change(records)
    if mom_change is not None:
        arrow = "▲" if mom_change > 0 else "▼"
        color = _STATUS_CRITICAL if mom_change > 0 else _STATUS_GOOD
        st.markdown(
            f"<span style='color:{color}; font-weight:600;'>"
            f"{arrow} {mom_change:+.1f}%</span>"
            f"<span style='color:{_MUTED_INK};'> vs previous month</span>",
            unsafe_allow_html=True,
        )
    elif len(series) == 1:
        st.caption(
            "Only one dated month so far - a trend appears after a second month."
        )

    months = [month for month, _value in series]
    values = [value for _month, value in series]

    fig = go.Figure(
        go.Scatter(
            x=months,
            y=values,
            mode="lines+markers",
            line=dict(color=_SEQUENTIAL_BLUE, width=2, shape="spline"),
            marker=dict(
                size=8,
                color=_SEQUENTIAL_BLUE,
                line=dict(width=2, color=surfaces["pie_border"]),
            ),
            fill="tozeroy",
            fillcolor=surfaces["fill"],
            hovertemplate="%{x}: $%{y:,.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        margin=dict(l=0, r=8, t=8, b=0),
        height=320,
        xaxis=dict(showgrid=False, title=None, tickfont=dict(color=surfaces["font"])),
        yaxis=dict(
            showgrid=True,
            gridcolor=surfaces["gridline"],
            zeroline=False,
            title=None,
            tickprefix="$",
            tickfont=dict(color=surfaces["font"]),
        ),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    st.plotly_chart(fig)


# Ordered registry of (widget_key, display_label, description, render_fn)
# tuples. Order here is the order widgets render in (and the order shown in
# the customization list). `description` is shown next to each widget's
# toggle in "Customize widgets" so the out-of-the-box choice is legible
# without having to enable a widget just to find out what it does.
WIDGETS: List[Tuple[str, str, str, Callable[[List[Dict[str, Any]]], None]]] = [
    (
        "overview",
        ":material/insights: Overview",
        "Total spend, document count, and month-over-month trend.",
        _render_overview,
    ),
    (
        "by_category",
        ":material/folder_open: Documents by Category",
        "How many documents you have per bill/data type.",
        _render_by_category,
    ),
    (
        "spend_by_category",
        ":material/pie_chart: Spend Share by Category",
        "Where your tracked spend is going, by category.",
        _render_spend_by_category,
    ),
    (
        "spend_over_time",
        ":material/show_chart: Spend Over Time",
        "Monthly spend trend across dated statements.",
        _render_spend_over_time,
    ),
]

# Each widget belongs to one of these sections. A section header is shown
# once, the first time an enabled widget from that section renders - this
# gives the page clear visual grouping ("the big picture" vs. "where's my
# money going") without needing tabs that could end up empty depending on
# widget selection.
_WIDGET_SECTIONS = {
    "overview": "Overview",
    "by_category": "Overview",
    "spend_by_category": "Spend Analysis",
    "spend_over_time": "Spend Analysis",
}

_WIDGET_LABELS = {key: label for key, label, _desc, _render_fn in WIDGETS}
_WIDGET_DESCRIPTIONS = {key: desc for key, _label, desc, _render_fn in WIDGETS}
_ALL_WIDGET_KEYS = [key for key, _label, _desc, _render_fn in WIDGETS]


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


_FILTER_CATEGORY_KEY = "dashboard_filter_categories"
_FILTER_VENDOR_KEY = "dashboard_filter_vendors"


def _render_filter_bar(
    records: List[Dict[str, Any]],
) -> Tuple[List[str], List[str]]:
    """Global filter row: bill/data type + vendor, driving every widget below.

    Selections live in `st.session_state` only (a view filter, not a saved
    preference) so they reset to "everything" on a fresh page load - the
    out-of-the-box view always shows all data.
    """
    all_category_keys = [category.key for category in CATEGORIES]
    category_labels = {category.key: category.label for category in CATEGORIES}
    vendors = dashboard_data.distinct_vendors(records)

    category_col, vendor_col, reset_col = st.columns([2, 2, 1])
    with category_col:
        selected_categories = st.multiselect(
            ":material/category: Bill / data type",
            options=all_category_keys,
            default=all_category_keys,
            format_func=lambda key: category_labels.get(key, key),
            key=_FILTER_CATEGORY_KEY,
        )
    with vendor_col:
        selected_vendors = st.multiselect(
            ":material/storefront: Vendor / provider",
            options=vendors,
            default=vendors,
            placeholder="All vendors" if vendors else "No vendors yet",
            key=_FILTER_VENDOR_KEY,
        )
    with reset_col:
        st.write("")
        if st.button(
            "Reset", icon=":material/filter_alt_off:", use_container_width=True
        ):
            st.session_state.pop(_FILTER_CATEGORY_KEY, None)
            st.session_state.pop(_FILTER_VENDOR_KEY, None)
            st.rerun()

    return selected_categories, selected_vendors


def _render_customize_widgets(vault: DataVault, enabled_widgets: List[str]) -> None:
    """Per-widget on/off toggles with a description, instead of a bare
    multiselect - so the customization surface doubles as a legend for what
    each widget shows."""
    enabled_set = set(enabled_widgets)
    with st.expander(
        f":material/tune: Customize widgets ({len(enabled_set)}/{len(_ALL_WIDGET_KEYS)} shown)"
    ):
        new_selection: List[str] = []
        for widget_key, label, description, _render_fn in WIDGETS:
            toggle_col, desc_col = st.columns([2, 3])
            with toggle_col:
                is_on = st.toggle(
                    label,
                    value=widget_key in enabled_set,
                    key=f"widget_toggle_{widget_key}",
                )
            with desc_col:
                st.caption(description)
            if is_on:
                new_selection.append(widget_key)

        if st.button("Save layout", icon=":material/save:", type="primary"):
            vault.store_data(DASHBOARD_CONFIG_KEY, {"enabled_widgets": new_selection})
            st.rerun()


def render_dashboard_page(vault: DataVault) -> None:
    """Render the Dashboard page: customizable at-a-glance widgets."""
    st.header(":material/dashboard: Dashboard")
    st.caption("A single at-a-glance view of the documents in your vault")

    records = _load_records(vault)

    if not records:
        st.info("No documents yet - upload something first.")
        return

    selected_categories, selected_vendors = _render_filter_bar(records)
    filtered_records = dashboard_data.filter_records(
        records, category_keys=selected_categories, vendors=selected_vendors
    )

    enabled_widgets = _load_enabled_widgets(vault)
    _render_customize_widgets(vault, enabled_widgets)

    if not filtered_records:
        st.warning("No documents match the current filters.")
        return

    enabled_set = set(enabled_widgets)
    last_section: Optional[str] = None
    for widget_key, _label, _description, render_fn in WIDGETS:
        if widget_key not in enabled_set:
            continue
        section = _WIDGET_SECTIONS.get(widget_key)
        if section != last_section:
            st.subheader(section)
            last_section = section
        with st.container():
            render_fn(filtered_records)
        st.divider()
