# Core Memory template (fill in locally; never commit a filled copy)

Paste a filled copy into **Agents → Core Memory → Import template** (roadmap Track M, M2). Only you edit Core Memory;
agents read only the sections they are scoped to (M3) and never write it. Keep it lean: facts that change how an agent
should answer, not documents or history. Leave a line empty (or delete it) if it does not apply.

```yaml
version: 1

about:                      # who the answers are for
  preferred_name: ""        # how agents address you
  timezone: ""              # e.g. America/Denver
  currency: "USD"
  locale: "en-US"           # date/number formatting
  home_region: ""           # city/state level only; used for utility seasonality, never a street address

household:                  # helps disambiguate "our", "my wife's", shared bills
  members: []               # first names or roles only, e.g. ["me", "partner"]
  shared_bills: []          # categories split with someone, e.g. ["electricity", "internet"]

providers:                  # "my gas bill" -> which provider; drives Gmail sync, overdue checks, digest scope (S2)
  - category: electricity   # one of: electricity, gas, mobile, credit_card, checking, brokerage, other
    provider: ""            # name as printed on the bill
    sender_domains: []      # e.g. ["example-power.com"]; Sync reads only these
    cadence: monthly        # monthly | quarterly
    account_nickname: ""    # optional label you use ("home", "cabin"); never the account number
    autopay: false
  - category: gas
    provider: ""
    sender_domains: []
    cadence: monthly
    account_nickname: ""
    autopay: false

accounts:                   # how you refer to accounts in questions; nicknames only, never numbers
  - nickname: ""            # e.g. "travel card"
    category: credit_card
    institution: ""

preferences:                # how answers should look
  answer_style: concise     # concise | detailed
  show_sources: true
  number_format: "1,234.56"
  week_starts_on: monday
  notify_overdue_after_days: 5

finance:                    # context for expense and forecast answers (no balances, no account numbers)
  budget_targets: {}        # category -> monthly target, e.g. {electricity: 120}
  risk_tolerance: moderate  # low | moderate | high (Forecast/Trader framing only; advice-only system)
  investing_horizon: ""     # e.g. "10+ years"
  watchlist: []             # tickers you follow

health:                     # optional; only for the health records agent if you add one
  conditions_to_track: []
  care_providers: []

do_not:                     # hard preferences agents must respect
  - "never suggest switching providers"   # example; delete or edit
```

Rules (enforced by M1/M3, not by this file): text fields ≤ 200 chars, no account/card numbers, SSNs, passwords or full
addresses (rejected on import); stored encrypted in the vault; never sent to public research providers, on a bus edge, into
RAG or into telemetry.
