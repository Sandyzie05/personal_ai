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

from src.data_extraction.categories import get_category
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
        xaxis=dict(showgrid=True, gridcolor=surfaces["gridline"], zeroline=False, title=None, tickfont=dict(color=surfaces["font"])),
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
            marker=dict(colors=colors, line=dict(color=surfaces["pie_border"], width=2)),
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


def _render_recent_uploads(records: List[Dict[str, Any]]) -> None:
    recent = dashboard_data.recent_uploads(records, 5)
    if not recent:
        st.caption("No uploads yet.")
        return

    rows: List[Dict[str, Any]] = []
    for record in recent:
        metadata = record.get("metadata") or {}
        filename = metadata.get("original_filename", record.get("storage_key"))
        category_key = metadata.get("category")
        category_label = get_category(category_key).label if category_key else "-"
        upload_timestamp = metadata.get("upload_timestamp")
        date_display = (
            upload_timestamp[:10] if isinstance(upload_timestamp, str) else "unknown"
        )
        extraction = record.get("extraction") or {}
        amount = extraction.get("total")
        amount_value: Optional[float] = (
            float(amount)
            if isinstance(amount, (int, float)) and not isinstance(amount, bool)
            else None
        )
        rows.append(
            {
                "Category": category_label,
                "File": filename,
                "Uploaded": date_display,
                "Amount": amount_value,
            }
        )

    st.dataframe(
        rows,
        hide_index=True,
        column_config={
            "Category": st.column_config.TextColumn(width="medium"),
            "File": st.column_config.TextColumn(width="large"),
            "Uploaded": st.column_config.TextColumn(width="small"),
            "Amount": st.column_config.NumberColumn(width="small", format="$%.2f"),
        },
    )


# Ordered registry of (widget_key, display_label, render_fn) tuples. Order
# here is the order widgets render in (and the order shown in the
# customization multiselect).
WIDGETS: List[Tuple[str, str, Callable[[List[Dict[str, Any]]], None]]] = [
    ("overview", "📈 Overview", _render_overview),
    ("by_category", "📂 Documents by Category", _render_by_category),
    (
        "spend_by_category",
        "💰 Spend Share by Category",
        _render_spend_by_category,
    ),
    ("spend_over_time", "📅 Spend Over Time", _render_spend_over_time),
    ("recent_uploads", "🕐 Recent Uploads", _render_recent_uploads),
]

# Each widget belongs to one of these sections. A section header is shown
# once, the first time an enabled widget from that section renders - this
# gives the page clear visual grouping (what someone actually wants: "the
# big picture", "where's my money going", "what did I just upload") without
# needing tabs that could end up empty depending on widget selection.
_WIDGET_SECTIONS = {
    "overview": "Overview",
    "by_category": "Overview",
    "spend_by_category": "Spend Analysis",
    "spend_over_time": "Spend Analysis",
    "recent_uploads": "Recent Activity",
}

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
    last_section: Optional[str] = None
    for widget_key, _label, render_fn in WIDGETS:
        if widget_key not in enabled_set:
            continue
        section = _WIDGET_SECTIONS.get(widget_key)
        if section != last_section:
            st.subheader(section)
            last_section = section
        with st.container():
            render_fn(records)
        st.divider()
