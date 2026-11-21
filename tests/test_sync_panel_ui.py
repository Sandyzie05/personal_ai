from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).with_name("_sync_app.py"))


def test_expected_bills_add_several_and_remove():
    at = AppTest.from_file(APP).run()
    assert not at.exception
    for name in ("Rocky Mountain Power", "Dominion Energy", "T-Mobile"):
        at.text_input(key="sync_prov").set_value(name)
        next(b for b in at.button if b.label == "Add").click().run()
        assert not at.exception
    assert [t.value for t in at.text] == ["Dominion Energy", "Rocky Mountain Power", "T-Mobile"]
    next(b for b in at.button if b.label == "Remove").click().run()
    assert [t.value for t in at.text] == ["Rocky Mountain Power", "T-Mobile"]
    assert any("Gmail is not connected" in c.value for c in at.caption)


def test_each_expected_bill_has_a_sync_button_disabled_without_a_source():
    at = AppTest.from_file(APP).run()
    at.text_input(key="sync_prov").set_value("Rocky Mountain Power")
    next(b for b in at.button if b.label == "Add").click().run()
    sync = [b for b in at.button if b.label == "Sync"]
    assert len(sync) == 1 and sync[0].disabled  # no Gmail sender / portal recipe yet: upload only
    assert any("upload only" in c.value for c in at.caption)
