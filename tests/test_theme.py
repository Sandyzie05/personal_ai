"""Unit tests for src.interface.theme."""

import streamlit as st

from src.interface import theme


def test_default_mode_is_light():
    # No user choice yet -> the historically-pinned light default.
    assert theme.resolve_mode() == "light"


def test_set_mode_persists_within_session():
    theme.set_mode("dark")
    assert theme.resolve_mode() == "dark"
    theme.set_mode("light")
    assert theme.resolve_mode() == "light"


def test_unknown_mode_is_ignored():
    theme.set_mode("high-contrast")  # not a known mode
    assert theme.resolve_mode() == "light"


def test_dark_surfaces_differ_from_light():
    theme.set_mode("dark")
    dark = theme.chart_surfaces()
    theme.set_mode("light")
    light = theme.chart_surfaces()

    assert dark["gridline"] == theme.CHART_SURFACES["dark"]["gridline"]
    assert light["gridline"] == theme.CHART_SURFACES["light"]["gridline"]
    assert dark["gridline"] != light["gridline"]
    assert dark["fill"] == theme.CHART_FILL["dark"]
    assert light["fill"] == theme.CHART_FILL["light"]


def test_surface_keys_present_for_all_modes():
    for mode in theme.THEME_MODES:
        theme.set_mode(mode)
        surfaces = theme.chart_surfaces()
        for key in ("gridline", "font", "pie_border", "fill"):
            assert key in surfaces and isinstance(surfaces[key], str)


def test_app_shell_is_noop_in_light_mode(monkeypatch):
    theme.set_mode("light")
    calls = []
    monkeypatch.setattr(st, "markdown", lambda *a, **k: calls.append(a))
    theme.apply_app_shell()
    assert calls == []  # light needs no override, so nothing is injected


def test_app_shell_injects_when_dark(monkeypatch):
    theme.set_mode("dark")
    calls = []

    def fake_markdown(block, *_a, **_k):
        calls.append(block)

    monkeypatch.setattr(st, "markdown", fake_markdown)
    theme.apply_app_shell()

    assert len(calls) == 1
    injected = calls[0]
    assert theme.APP_SHELL["dark"]["bg"] in injected
    assert "!important" in injected


def test_app_shell_covers_components_reported_as_still_light(monkeypatch):
    """Regression test: st.metric's value, st.expander/st.status bodies, alert
    boxes, the chat input's bottom bar, and st.dialog all render their own
    background/text via component-specific testids that don't read
    `--secondary-background-color` - each needs an explicit selector or it
    stays a white box (or dark-on-dark, invisible text) under dark mode."""
    theme.set_mode("dark")
    calls = []
    monkeypatch.setattr(st, "markdown", lambda block, *a, **k: calls.append(block))
    theme.apply_app_shell()
    injected = calls[0]

    for testid in (
        "stMetricValue",
        "stMetricLabel",
        "stExpanderDetails",
        "stAlertContainer",
        "stBottom",
        "stBottomBlockContainer",
        "stChatInput",
        "stChatInputTextArea",
        "stDialog",
        "stFileUploader",
        "stFileUploaderDropzone",
    ):
        assert f'"{testid}"' in injected, f"missing selector for {testid}"
