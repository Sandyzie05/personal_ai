from datetime import date

from src.forecaster.sessions import draft_sessions, nyse_holidays


def test_holiday_rules_match_published_nyse_calendar():
    assert {date(2026, 4, 3), date(2026, 7, 3), date(2026, 11, 26), date(2026, 6, 19)} <= nyse_holidays(2026)
    assert date(2027, 12, 24) in nyse_holidays(2027) and date(2027, 6, 18) in nyse_holidays(2027)
    assert date(2027, 12, 31) not in nyse_holidays(2027)  # NYSE does not observe New Year's 2028 on a Friday


def test_draft_skips_weekends_and_holidays_and_closes_at_4pm_new_york():
    out = draft_sessions("XNAS", date(2026, 11, 25), before=2, after=3)
    assert [s.session for s in out] == [date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 27),
                                        date(2026, 11, 30), date(2026, 12, 1)]
    assert out[0].closes_at.isoformat() == "2026-11-24T21:00:00+00:00"
