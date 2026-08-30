"""Light/dark theme for the Streamlit UI.

Streamlit 1.62 (the version this project pins) bakes its base theme into its
compiled frontend, so there is no first-class *runtime* "switch the whole
app's theme" call: `st.set_page_config` has no `theme=` parameter, and
`[theme]` in `.streamlit/config.toml` only takes effect at server start. Two
consequences:

- We do NOT rewrite `config.toml`: it pins `server.address = "localhost"`, a
  security boundary (see `.streamlit/config.toml` and `docs/security_design.md`).
- The user's light/dark choice lives in `st.session_state` and is applied the
  only way available mid-session: a `<style>` block that overrides the CSS
  surfaces Streamlit exposes, plus a properly-themed Plotly config for the
  charts.

The categorical chart hues (`dashboard.py`'s `_CATEGORICAL_PALETTE`,
`_SEQUENTIAL_BLUE`, status colours) stay mode-independent - color must mean the
same thing in light and dark. Only the *surfaces the figures sit on* and the
*app chrome* change, and those are exactly what `chart_surfaces()` /
`apply_app_shell()` return / apply.
"""

import streamlit as st

THEME_MODE_KEY = "ui_theme_mode"
THEME_MODES = ("light", "dark")

# App-chrome colors each mode paints via the injected <style> block.
APP_SHELL = {
    "light": {
        "bg": "#f4f6f9",
        "sidebar_bg": "#ffffff",
        "card_bg": "#f4f6f9",
        "card_border": "#e6e8ec",
        "text": "#1f2328",
    },
    "dark": {
        "bg": "#0e1014",
        "sidebar_bg": "#15181e",
        "card_bg": "#1d2027",
        "card_border": "#2b2e36",
        "text": "#e6e8ec",
    },
}

# Plotly layout colors a figure needs to read on its backdrop.
CHART_SURFACES = {
    "light": {
        "gridline": "#e1e0d9",
        "font": "#1f2328",
        "pie_border": "#ffffff",
    },
    "dark": {
        "gridline": "#3a3d46",
        "font": "#c9ccd1",
        "pie_border": "#0e1014",
    },
}

# A translucent fill tint that reads as a wash on either background.
CHART_FILL = {
    "light": "rgba(42, 120, 214, 0.10)",
    "dark": "rgba(90, 160, 232, 0.18)",
}


def _session_state():
    """Live session_state, or an empty dict outside a script run.

    Tests call these helpers outside a running script run, where
      `st.session_state` raises; fall back to an empty dict so resolution
    stays pure and testable.
    """
    try:
        state = st.session_state
    except Exception:
        return {}
    if hasattr(state, "get"):
        return state
    return {}


def resolve_mode() -> str:
    """Active mode: an explicit user choice (session_state) wins, else 'light'.

    The 'light' fallback keeps existing users on exactly what they saw before
    this toggle existed (the app's historically-pinned default).
    """
    raw = _session_state().get(THEME_MODE_KEY)
    if raw in THEME_MODES:
        return raw
    # The sidebar checkbox owns THEME_MODE_KEY and stores a bool, not a mode
    # string. True means the user checked "Dark mode".
    if raw is True:
        return "dark"
    return "light"


def set_mode(mode: str) -> None:
    """Persist the user's chosen mode to session_state for this session."""
    if mode in THEME_MODES:
        _session_state()[THEME_MODE_KEY] = mode


def chart_surfaces():
    """Plotly layout colors for the active mode (see module docstring)."""
    mode = resolve_mode()
    return {**CHART_SURFACES[mode], "fill": CHART_FILL[mode]}


def render_theme_toggle() -> None:
    """Sidebar control that flips light/dark and persists the choice.

    A checkbox so the choice is unambiguous: unchecked = light, checked = dark.
    Persisting to session_state makes it survive navigation between reruns.
    """
    # The widget itself owns THEME_MODE_KEY in session_state, so Streamlit
    # auto-persists the user's checked/unchecked value there on the rerun. We
    # must NOT also write THEME_MODE_KEY from here: that would run *after* the
    # widget in the same run and raise "cannot be modified after the widget with
    # key ... is instantiated". Persistence is a side effect of the widget, and
    # apply_app_shell() (called in run() after this toggle) reads it to paint.
    st.checkbox(
        "Dark mode",
        value=resolve_mode() == "dark",
        key=THEME_MODE_KEY,
    )


def apply_app_shell() -> None:
    """Inject the active mode's <style> block that repaints the app chrome.

    No-op in light mode - dark's injected styles are scoped to dark selectors
    and never fire in light, so there is nothing to undo.
    """
    if resolve_mode() != "dark":
        return
    c = APP_SHELL["dark"]
    style = (
        "<style>\n"
        # Streamlit components read their surfaces from these CSS vars, not
        # from inherited color. Chat bubbles (st.chat_message), the chat
        # input bar, st.container(border=True) on the Upload page, and
        # bordered alerts all pull from --background-color /
        # --secondary-background-color / --text-color / --border-color, so
        # overriding the vars darks every one of them at once - the
        # per-component paint below was leaving their *backgrounds* light.
        ':root, [data-testid="stApp"] {\n'
        f"    --background-color: {c['bg']};\n"
        f"    --secondary-background-color: {c['card_bg']};\n"
        f"    --text-color: {c['text']};\n"
        f"    --border-color: {c['card_border']};\n"
        f"    --code-background-color: {c['card_bg']};\n"
        "}\n"
        'html, body, [data-testid="stApp"], '
        '[data-testid="stAppViewContainer"] {\n'
        f"    background-color: {c['bg']} !important;\n"
        f"    color: {c['text']} !important;\n"
        "}\n"
        # Sidebar: its own bg, and light text on every control inside it
        # (nav radio, key buttons, metric, captions).
        '[data-testid="stSidebar"] {\n'
        f"    background-color: {c['sidebar_bg']} !important;\n"
        "}\n"
        '[data-testid="stSidebar"] * {\n'
        f"    color: {c['text']} !important;\n"
        "}\n"
        # Streamlit sets its own dark text color on the elements below, so the
        # body-level light color never reaches them - repaint each of these
        # explicitly, or chat answers, captions and control values stay
        # dark-on-dark (the "messed up" colors).
        "h1, h2, h3, h4, h5, h6, li, label,\n"
        ".stMarkdown, .stMarkdown p, .stMarkdown blockquote,\n"
        '.stText, [data-testid="stText"],\n'
        '.stCaption, [data-testid="stCaption"],\n'
        # Chat bubbles, their timestamp captions, and the chat input bar's
        # own text.
        '[data-testid="stChatMessage"] *, '
        '[data-testid="stChatInputTextArea"],\n'
        # Bordered containers (st.container(border=True)) used on Upload.
        '[data-testid="stVerticalBlockBorderWrapper"],\n'
        # Metric label/value/delta - these are separate leaf elements, not
        # children of a shared span, so the dashboard's "Total documents" /
        # "Categories in use" numbers were rendering in Streamlit's default
        # (dark-on-dark) color and reading as blank.
        '[data-testid="stMetricLabel"], [data-testid="stMetricValue"], '
        '[data-testid="stMetricDelta"],\n'
        # Alert boxes (st.info/success/warning/error).
        '[data-testid="stAlertContainer"],\n'
        # Expander/status body text (st.expander, st.status share this).
        '[data-testid="stExpanderDetails"],\n'
        # Modal dialogs (st.dialog - the Clear chat / Delete confirmations).
        '[data-testid="stDialog"] {\n'
        f"    color: {c['text']} !important;\n"
        "}\n"
        # Form controls: text inputs, text areas, and select/multiselect boxes.
        "input, textarea,\n"
        '[data-baseweb="control"],\n'
        'div[data-ststyle="true"] {\n'
        f"    background-color: {c['card_bg']} !important;\n"
        f"    color: {c['text']} !important;\n"
        f"    border-color: {c['card_border']} !important;\n"
        "}\n"
        # Select/multiselect dropdown rows live in a baseweb popover.
        '[data-baseweb="menu"],\n'
        '[data-baseweb="popover"],\n'
        '[data-baseweb="menu-item"] * {\n'
        f"    background-color: {c['card_bg']} !important;\n"
        f"    color: {c['text']} !important;\n"
        "}\n"
        # Buttons: one raised surface so secondary (default) buttons stay
        # legible on the dark bg.
        ".stButton > button {\n"
        f"    background-color: {c['card_bg']} !important;\n"
        f"    border-color: {c['card_border']} !important;\n"
        f"    color: {c['text']} !important;\n"
        "}\n"
        # Metric card, expander/status shell, alert boxes, the chat input's
        # bottom bar, dialogs, and the file uploader dropzone all default to
        # a light card surface Streamlit bakes into the component itself -
        # none of these read `--secondary-background-color`, so each needs
        # its background repainted explicitly or it stays a white box.
        '[data-testid="stMetric"],\n'
        '[data-testid="stExpander"],\n'
        '[data-testid="stExpanderDetails"],\n'
        '[data-testid="stAlertContainer"],\n'
        '[data-testid="stBottom"],\n'
        '[data-testid="stBottomBlockContainer"],\n'
        '[data-testid="stChatInput"],\n'
        '[data-testid="stDialog"],\n'
        '[data-testid="stFileUploader"],\n'
        '[data-testid="stFileUploaderDropzone"] {\n'
        f"    background-color: {c['card_bg']} !important;\n"
        f"    border-color: {c['card_border']} !important;\n"
        "}\n"
        'section[data-testid="stSidebar"] [data-testid="stMetric"] {\n'
        f"    background: {c['sidebar_bg']} !important;\n"
        "}\n"
        ".stDataFrame {\n"
        f"      --bg-color: {c['card_bg']};\n"
        f"      --fg-color: {c['text']};\n"
        "}\n"
        "</style>\n"
    )
    st.markdown(style, unsafe_allow_html=True)
