from pathlib import Path

from scripts.state_report import main


def test_quick_report_is_redacted_and_complete():
    report = main(quick=True)
    assert "## Code" in report and "oMLX" in report and "<ROWS>" in report
    assert str(Path.home()) not in report
    assert "Bearer" not in report
