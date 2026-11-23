"""Generate synthetic bill/statement PDFs into dummy_data/ (one or two per category) plus extractions.json.

Every name, address, account and amount is invented. Line items sum to each total, so the
Upload page's extraction consistency check and the Dashboard agree with what the PDF says.
Run: `make dummy-pdfs` (the PDFs are committed; rerun only after editing DOCS below).
"""

import json
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parents[1] / "dummy_data"
HOLDER = "Alex Sample<br>100 Example Way, Springfield, ST 00000"
BANNER = "SAMPLE DOCUMENT - SYNTHETIC DATA FOR TESTING ONLY"

DOCS = [
    dict(
        file="electricity_summit_peak_power_2025-08.pdf",
        category="electricity",
        provider="Summit Peak Power",
        account="****4821",
        period=("2025-08-01", "2025-08-31"),
        billed="Sep 3, 2025",
        due="Sep 24, 2025",
        title="Summit Peak Power - Electric Utility Statement",
        usage="Meter 77310452 · Aug 1 - Aug 31, 2025 · 31 days · Previous 18,204 / Current 18,816 · 612 kWh used (19.7 kWh/day)",
        items=[
            ("Basic Charge - Single Phase", 8.00),
            ("Energy Charge 612 kWh @ $0.1150 per kWh", 70.38),
            ("Renewable Energy Adjustment", -1.20),
            ("Wildfire Mitigation Balancing Account", 2.15),
            ("State and Municipal Energy Tax", 5.42),
        ],
        notes="Net metering: no solar credit this period. Questions? Call 1-800-555-0100.",
    ),
    dict(
        file="electricity_summit_peak_power_2025-09.pdf",
        category="electricity",
        provider="Summit Peak Power",
        account="****4821",
        period=("2025-09-01", "2025-09-30"),
        billed="Oct 3, 2025",
        due="Oct 24, 2025",
        title="Summit Peak Power - Electric Utility Statement",
        usage="Meter 77310452 · Sep 1 - Sep 30, 2025 · 30 days · Previous 18,816 / Current 19,661 · 845 kWh used (28.2 kWh/day)",
        items=[
            ("Basic Charge - Single Phase", 8.00),
            ("Energy Charge 845 kWh @ $0.1150 per kWh", 97.18),
            ("Renewable Energy Adjustment", -1.20),
            ("Wildfire Mitigation Balancing Account", 2.15),
            ("State and Municipal Energy Tax", 7.29),
        ],
        notes="Usage rose 38% versus August (air conditioning). Questions? Call 1-800-555-0100.",
    ),
    dict(
        file="gas_canyon_natural_gas_2025-08.pdf",
        category="gas",
        provider="Canyon Natural Gas",
        account="****3307",
        period=("2025-08-01", "2025-08-31"),
        billed="Sep 2, 2025",
        due="Sep 15, 2025",
        title="Canyon Natural Gas - Gas Utility Bill",
        usage="Aug 1 - Aug 31, 2025 · 18 therms used (17 CCF)",
        items=[
            ("Basic Service Charge", 9.50),
            ("Natural Gas Usage 18 therms @ $0.9500 per therm", 17.10),
            ("Delivery Charge", 3.80),
            ("Taxes and Franchise Fees", 1.70),
        ],
        notes="Total due $32.10 for the August 2025 period.",
    ),
    dict(
        file="gas_canyon_natural_gas_2025-09.pdf",
        category="gas",
        provider="Canyon Natural Gas",
        account="****3307",
        period=("2025-09-01", "2025-09-30"),
        billed="Oct 2, 2025",
        due="Oct 15, 2025",
        title="Canyon Natural Gas - Gas Utility Bill",
        usage="Sep 1 - Sep 30, 2025 · 34 therms used (33 CCF)",
        items=[
            ("Basic Service Charge", 9.50),
            ("Natural Gas Usage 34 therms @ $0.9500 per therm", 32.30),
            ("Delivery Charge", 6.20),
            ("Taxes and Franchise Fees", 2.80),
        ],
        notes="Total due $50.80 for the September 2025 period.",
    ),
    dict(
        file="mobile_nimbus_wireless_2025-08.pdf",
        category="mobile",
        provider="Nimbus Wireless",
        account="(555) 010-2233",
        period=("2025-08-01", "2025-08-31"),
        billed="Sep 1, 2025",
        due="Sep 21, 2025",
        title="Nimbus Wireless - Monthly Bill Summary",
        usage="3 voice lines on the Unlimited Plan: unlimited talk, text and data plan. AutoPay is on.",
        items=[
            ("Plans - Unlimited Plan, 3 lines (monthly recurring charge)", 140.00),
            ("Equipment - Phone installment", 35.00),
            ("Equipment - Promotional credit", -35.00),
            ("Services - Device protection", 5.00),
            ("Taxes and Fees", 12.40),
        ],
        notes="Your last bill was $157.40. Thanks for paying on time.",
    ),
    dict(
        file="mobile_nimbus_wireless_2025-09.pdf",
        category="mobile",
        provider="Nimbus Wireless",
        account="(555) 010-2233",
        period=("2025-09-01", "2025-09-30"),
        billed="Oct 1, 2025",
        due="Oct 21, 2025",
        title="Nimbus Wireless - Monthly Bill Summary",
        usage="3 voice lines on the Unlimited Plan: unlimited talk, text and data plan. International roaming used on one line.",
        items=[
            ("Plans - Unlimited Plan, 3 lines (monthly recurring charge)", 140.00),
            ("Equipment - Phone installment", 35.00),
            ("Equipment - Promotional credit", -35.00),
            ("Services - Device protection", 5.00),
            ("One-time charges - International roaming", 4.95),
            ("Taxes and Fees", 12.40),
        ],
        notes="This month's charges are $4.95 more than last month (international roaming).",
    ),
    dict(
        file="credit_card_harbor_bank_visa_2025-09.pdf",
        category="credit_card",
        provider="Harbor Bank Visa",
        account="****9012",
        period=("2025-08-26", "2025-09-25"),
        billed="Sep 25, 2025",
        due="Oct 20, 2025",
        title="Harbor Bank Visa - Credit Card Statement",
        usage="Statement balance $1,250.12 · Minimum payment $35.00 · Payment due date Oct 20, 2025 · Credit limit $8,000.00 · Available credit $6,749.88 · Purchase APR 22.49%",
        items=[
            ("Previous balance", 912.30),
            ("Payment - thank you", -912.30),
            ("Purchases: Fresh Market groceries", 182.44),
            ("Purchases: Fuel Stop", 61.20),
            ("Purchases: Corner Coffee", 14.75),
            ("Purchases: Online Retailer", 389.99),
            ("Purchases: Bistro dinner", 96.40),
            ("Purchases: Skyway Airlines", 412.80),
            ("Purchases: Main St Pharmacy", 27.54),
            ("Purchases: Streaming subscription", 15.99),
            ("Purchases: Hardware store", 49.01),
        ],
        notes="New balance $1,250.12. No interest charged this period.",
        total_label="New balance",
    ),
    dict(
        file="checking_harbor_bank_2025-09.pdf",
        category="checking",
        provider="Harbor Bank",
        account="****5566",
        period=("2025-09-01", "2025-09-30"),
        billed="Sep 30, 2025",
        due=None,
        title="Harbor Bank - Checking Account Statement",
        usage="Account summary for Everyday Checking. Beginning balance $4,215.60 · Deposits and other credits $5,400.00 · Withdrawals $3,336.55 · Ending balance $6,279.05",
        items=[
            ("Beginning balance", 4215.60),
            ("Deposit - Payroll Sep 5", 2700.00),
            ("Deposit - Payroll Sep 19", 2700.00),
            ("Rent - Oak Street Apartments", -1950.00),
            ("Harbor Bank Visa payment", -912.30),
            ("Canyon Natural Gas autopay", -32.10),
            ("Summit Peak Power autopay", -84.75),
            ("Nimbus Wireless autopay", -157.40),
            ("ATM withdrawal", -200.00),
        ],
        notes="Ending balance $6,279.05.",
        total_label="Ending balance",
    ),
    dict(
        file="brokerage_example_invest_2025-09.pdf",
        category="brokerage",
        provider="Example Invest",
        account="****7788",
        period=("2025-09-01", "2025-09-30"),
        billed="Sep 30, 2025",
        due=None,
        title="Example Invest - Brokerage Account Statement",
        usage="Portfolio value $25,430.18 as of Sep 30, 2025. Dividends received this period: $48.62.",
        items=[
            ("VTI - 40 shares @ $285.10", 11404.00),
            ("AAPL - 25 shares @ $228.40", 5710.00),
            ("MSFT - 12 shares @ $430.15", 5161.80),
            ("Cash and sweep (includes $48.62 dividends)", 3154.38),
        ],
        notes="Prices are illustrative, not real closing prices.",
        total_label="Portfolio value",
    ),
]


def _html(d, total):
    rows = "".join(
        f"<tr><td>{label}</td><td style='text-align:right'>{amt:,.2f}</td></tr>"
        for label, amt in d["items"]
    )
    due = f"<b>Due date:</b> {d['due']}<br>" if d["due"] else ""
    return f"""
<div style="font-family:sans-serif;font-size:10pt">
<p style="color:#b00020;font-size:8pt"><b>{BANNER}</b></p>
<h2 style="color:#2a78d6">{d["title"]}</h2>
<p>{HOLDER}</p>
<p><b>Provider:</b> {d["provider"]}<br><b>Account:</b> {d["account"]}<br>
<b>Statement date:</b> {d["billed"]}<br>
<b>Statement period:</b> {d["period"][0]} to {d["period"][1]}<br>{due}
<b>{d.get("total_label", "Total amount due")}:</b> ${total:,.2f}</p>
<p>{d["usage"]}</p>
<table style="width:100%;border-collapse:collapse" border="1" cellpadding="4">
<tr style="background:#eef3fb"><th style="text-align:left">Item</th><th style="text-align:right">Amount ($)</th></tr>
{rows}
<tr><td><b>{d.get("total_label", "Total amount due")}</b></td><td style='text-align:right'><b>{total:,.2f}</b></td></tr>
</table>
<p>{d["notes"]}</p>
</div>"""


def main():
    OUT.mkdir(exist_ok=True)
    extractions = {}
    for d in DOCS:
        total = round(sum(a for _, a in d["items"]), 2)
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        page.insert_htmlbox(pymupdf.Rect(48, 48, 564, 744), _html(d, total))
        doc.set_metadata(
            {
                "title": d["title"],
                "author": "personal_ai dummy data",
                "creationDate": "D:20251001000000",
                "modDate": "D:20251001000000",
            }
        )
        doc.save(OUT / d["file"], garbage=4, deflate=True, no_new_id=True)
        extractions[d["file"]] = {
            "category": d["category"],
            "provider": d["provider"],
            "account_identifier": d["account"],
            "period_start": d["period"][0],
            "period_end": d["period"][1],
            "total": total,
            "line_items": [{"label": label, "amount": a} for label, a in d["items"]],
        }
    (OUT / "extractions.json").write_text(json.dumps(extractions, indent=2) + "\n")
    print(f"Wrote {len(DOCS)} PDFs + extractions.json to {OUT}")


if __name__ == "__main__":
    main()
