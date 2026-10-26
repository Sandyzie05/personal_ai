"""Synthetic bill corpus + golden questions for `make eval-rag`. NO real data: every name/number is generated.

3 providers x 12 months (2025-10..2026-09) plus planted traps: duplicate re-uploads, a second account at the
same provider, a corrected bill that supersedes an earlier one, near-identical totals."""

import random
from typing import Any, Dict, List

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
PERIODS = [(2025, m) for m in (10, 11, 12)] + [(2026, m) for m in range(1, 10)]
# provider, category, query noun ("<provider> bill" must trip query_analysis category aliases), extra line label
PROVIDERS = [
    ("Aurora Electric", "electricity", "Solar credit", "kWh"),
    ("Basalt Gas", "gas", "Usage charge", "therms"),
    ("Cobalt Mobile", "mobile", "Device installment", "GB"),
]


def _last_day(y: int, m: int) -> int:
    return [31, 29 if y % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]


def _bill(provider, category, extra, unit, y, m, acct, rng, total=None, corrected=False, uploaded="2026-10-01"):
    energy = round(rng.uniform(30, 90), 2)
    delivery = round(rng.uniform(10, 25), 2)
    extra_amt = round(rng.uniform(5, 20), 2)
    taxes = round(rng.uniform(2, 8), 2)
    sign = -1 if category == "electricity" else 1
    calc = round(energy + delivery + sign * extra_amt + taxes, 2)
    total = calc if total is None else total
    usage = rng.randint(20, 480)
    due = f"{y + (m == 12)}-{(m % 12) + 1:02d}-{rng.randint(10, 25):02d}"
    start, end = f"{y}-{m:02d}-01", f"{y}-{m:02d}-{_last_day(y, m)}"
    xs = f"-${extra_amt:.2f}" if sign < 0 else f"${extra_amt:.2f}"
    note = "Corrected statement: replaces the statement previously issued for this period.\n" if corrected else ""
    text = (f"# {provider}\n\n{note}Statement for account ending {acct}\nBilling period: {start} to {end}\n"
            f"Usage: {usage} {unit}\n\n| Item | Amount |\n|---|---|\n| Energy charge | ${energy:.2f} |\n"
            f"| Delivery charge | ${delivery:.2f} |\n| {extra} | {xs} |\n| Taxes and fees | ${taxes:.2f} |\n"
            f"| Total due | ${total:.2f} |\n\nDue date: {due}\nQuestions? Call the number on the back of your card.")
    meta = {"category": category, "provider": provider, "period_start": start, "period_end": end,
            "account_label": acct, "upload_timestamp": uploaded + "T10:00:00", "original_filename": f"{provider}_{start}.pdf"}
    return {"text_content": text, "metadata": meta}, f"${total:.2f}", f"${extra_amt:.2f}", due, f"{usage} {unit}"


def build() -> Dict[str, Any]:
    """-> {docs: {key: vault-shaped dict}, facts: {key: {total, extra, due, usage}}, questions: [...]}"""
    rng = random.Random(7)
    docs: Dict[str, Dict] = {}
    facts: Dict[str, Dict] = {}
    pref = {"electricity": "elec", "gas": "gas", "mobile": "mob"}

    def put(key, *a, **kw):
        d, total, extra, due, usage = _bill(*a, **kw)
        docs[key] = d
        facts[key] = {"total": total, "extra": extra, "due": due, "usage": usage}

    for prov, cat, extra, unit in PROVIDERS:
        for y, m in PERIODS:
            kw = {}
            if (prov, y, m) == ("Basalt Gas", 2026, 4):
                kw["total"] = 88.40
            if (prov, y, m) == ("Basalt Gas", 2026, 5):
                kw["total"] = 88.04  # near-identical total
            put(f"{pref[cat]}-{y}-{m:02d}", prov, cat, extra, unit, y, m, "4417", rng, **kw)
    for m in (1, 2, 3, 4):  # second account, same provider
        put(f"elec2-2026-{m:02d}", "Aurora Electric", "electricity", "Solar credit", "kWh", 2026, m, "9023", rng)
    for k in ("elec-2025-12", "gas-2026-02", "mob-2026-05"):  # identical re-uploads
        docs[k + "-reupload"] = {**docs[k], "metadata": {**docs[k]["metadata"], "upload_timestamp": "2026-10-02T09:00:00"}}
        facts[k + "-reupload"] = facts[k]
    # corrected bill supersedes the original, uploaded later, different total
    put("gas-2025-12-corrected", "Basalt Gas", "gas", "Usage charge", "therms", 2025, 12, "4417", rng,
        total=77.77, corrected=True, uploaded="2026-10-05")
    return {"docs": docs, "facts": facts, "questions": _questions(facts)}


def _mn(y, m):
    return f"{MONTHS[m - 1]} {y}"


def _questions(f) -> List[Dict[str, Any]]:
    qs: List[Dict[str, Any]] = []

    def q(kind, text, gold, answers=(), stale=(), dup=None):
        qs.append({"id": f"q{len(qs) + 1:02d}", "kind": kind, "text": text, "gold": gold, "answers": list(answers),
                   "stale": list(stale), "dup": dup})

    noun = {"Aurora Electric": "Aurora Electric bill", "Basalt Gas": "Basalt Gas bill", "Cobalt Mobile": "Cobalt Mobile bill"}
    pref = {"Aurora Electric": "elec", "Basalt Gas": "gas", "Cobalt Mobile": "mob"}
    skip_acct = {("Aurora Electric", 2026, m) for m in (1, 2, 3, 4)}
    dup_keys = {"elec-2025-12", "gas-2026-02", "mob-2026-05", "gas-2025-12"}
    rr = random.Random(11)
    for prov, *_ in PROVIDERS:  # lookup_total: 8 each
        pool = [p for p in PERIODS if (prov, *p) not in skip_acct and f"{pref[prov]}-{p[0]}-{p[1]:02d}" not in dup_keys]
        for y, m in rr.sample(pool, min(8, len(pool))):
            k = f"{pref[prov]}-{y}-{m:02d}"
            q("lookup_total", f"What was the total due on my {noun[prov]} for {_mn(y, m)}?", [[k]], [f[k]["total"]])
    for prov, *_ in PROVIDERS:  # same lookup, different phrasing (no word "total")
        pool = [p for p in PERIODS if (prov, *p) not in skip_acct and f"{pref[prov]}-{p[0]}-{p[1]:02d}" not in dup_keys]
        for y, m in rr.sample(pool, 3):
            k = f"{pref[prov]}-{y}-{m:02d}"
            q("lookup_total", f"How much do I owe on the {noun[prov]} for {_mn(y, m)}?", [[k]], [f[k]["total"]])
    for prov, _, extra, unit in PROVIDERS:  # lookup_field: 4 each
        pool = [p for p in PERIODS if (prov, *p) not in skip_acct and f"{pref[prov]}-{p[0]}-{p[1]:02d}" not in dup_keys]
        for i, (y, m) in enumerate(rr.sample(pool, min(4, len(pool)))):
            k = f"{pref[prov]}-{y}-{m:02d}"
            if i % 2:
                q("lookup_field", f"What was the due date on my {noun[prov]} for {_mn(y, m)}?", [[k]], [f[k]["due"]])
            else:
                q("lookup_field", f"How much usage was on my {noun[prov]} for {_mn(y, m)}?", [[k]], [f[k]["usage"]])
    for prov, *_ in PROVIDERS:  # comparison: 3 each (+1) between two months
        for a, b in [((2025, 11), (2026, 1)), ((2026, 6), (2026, 8)), ((2026, 3), (2026, 4))][: 3 if prov != "Cobalt Mobile" else 4]:
            ka, kb = (f"{pref[prov]}-{a[0]}-{a[1]:02d}", f"{pref[prov]}-{b[0]}-{b[1]:02d}")
            if ka in dup_keys or kb in dup_keys or (prov, *a) in skip_acct or (prov, *b) in skip_acct:
                continue
            q("comparison", f"Was my {noun[prov]} higher in {_mn(*a)} or {_mn(*b)}?", [[ka], [kb]], [f[ka]["total"], f[kb]["total"]])
    for prov, *_ in PROVIDERS:  # aggregation over 3 consecutive months (span filter)
        for a, c in [((2026, 5), (2026, 7)), ((2026, 6), (2026, 8))]:
            ks = [f"{pref[prov]}-2026-{m:02d}" for m in range(a[1], c[1] + 1)]
            if any(k in dup_keys for k in ks):
                continue
            q("aggregation", f"What did I pay in total on my {noun[prov]} from {_mn(*a)} through {_mn(*c)}?",
              [[k] for k in ks], [f[k]["total"] for k in ks])
    for m in (1, 2, 3, 4):  # two accounts, same provider: name the account
        for acct, key in (("4417", f"elec-2026-{m:02d}"), ("9023", f"elec2-2026-{m:02d}")):
            if m in (1, 3) or acct == "9023":
                q("account", f"What was the total due on my Aurora Electric bill for {_mn(2026, m)} for the account ending {acct}?",
                  [[key]], [f[key]["total"]])
    for k, (y, m), prov in [("elec-2025-12", (2025, 12), "Aurora Electric"), ("gas-2026-02", (2026, 2), "Basalt Gas"),
                            ("mob-2026-05", (2026, 5), "Cobalt Mobile")]:  # identical re-uploads: one copy in context
        q("duplicate", f"What was the total due on my {noun[prov]} for {_mn(y, m)}?", [[k, k + "-reupload"]], [f[k]["total"]],
          dup=k)
    q("corrected", "What was the total due on my Basalt Gas bill for December 2025?", [["gas-2025-12-corrected"]],
      [f["gas-2025-12-corrected"]["total"]], stale=[f["gas-2025-12"]["total"]])
    q("corrected", "How much do I owe on the Basalt Gas bill for December 2025?", [["gas-2025-12-corrected"]],
      [f["gas-2025-12-corrected"]["total"]], stale=[f["gas-2025-12"]["total"]])
    for text in [  # absent: nothing in the corpus can answer; the pipeline must abstain
        "What was my Aurora Electric bill for March 2024?", "What was my Basalt Gas bill for July 2023?",
        "What was my Cobalt Mobile bill for January 2024?", "How much was my Aurora Electric bill for June 2027?",
        "What was my water bill for February 2026?", "What is my passport number?",
        "What was my Zenith Internet bill in April 2026?", "How much did I pay for car insurance last year?",
        "What is my mortgage interest rate?", "What was my Orchid Water statement balance for May 2026?",
        "How many shares of Quasar Corp do I own?", "When does my gym membership renew?",
        "What was my Delta Dental premium in 2026?", "What is my library card number?",
    ]:
        q("absent", text, [])
    return qs
