import streamlit as st
from test_forecast_page import MemoryVault
from src.interface import sync_panel

st.session_state.setdefault("v", MemoryVault())
sync_panel.render(st.session_state["v"], lambda *a: None)
