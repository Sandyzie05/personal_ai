# T-Mobile Bill Extraction System

## Summary

Created agent-based extraction system for T-Mobile bill PDFs that avoids slow embedding generation (10-30 seconds) by using fast pattern matching (<1 second).

## New Files Created

1. **`src/data_extraction/models.py`** - Pydantic models
   - `BillData`: Total due, dates, account number, charges, payment history
   - `Charge`: Category, amount, description
   - `PaymentRecord`: Date, amount, method

2. **`src/data_extraction/bill_extractor.py`** - Extractor class
   - Pattern-based detection (T-Mobile signature)
   - Regex extraction for all key fields
   - Structured data return (no embeddings)

3. **`src/data_extraction/bill_storage.py`** - SQLite storage
   - Indexed tables for fast queries
   - Supports: account, date range, total spent queries

4. **`src/data_extraction/__init__.py`** - Package exports

## Updated Files

**`src/data_ingestion/parsers/pdf_parser.py`**
- Added structured extraction method
- Auto-detects T-Mobile bills
- Falls back to text extraction

## Key Patterns Used

- **Total due**: `TOTAL\s+DUE\s*\$?(\d{1,3}(?:,\d{3})*\.?\d*)`
- **Dates**: `Bill issue date (\w+ \d{1,2}, \d{4})` / `due by (\w+ \d{1,2}, \d{4})`
- **Account**: `/(\d{6,12})/`
- **Charges**: Parsed from PLANS/EQUIPMENT/SERVICES sections

## Test with PDF

```bash
python3 -c "
from src.data_extraction import BillExtractor, BillStorage
from pathlib import Path

extractor = BillExtractor()
bill = extractor.extract(Path('~/Downloads/SummaryBillJul2026.pdf'))

print('Total:', bill.total_due)      # $326.57
print('Date:', bill.bill_date)       # Jul 24, 2026
print('Due:', bill.due_date)         # Aug 17, 2026
print('Account:', bill.account_number)  # 203227566
print('Charges:', [c.category for c in bill.charges])
"
```

## Performance

- **Before**: 10-30 seconds (embedding generation)
- **After**: <1 second (pattern matching only)

## Benefits

1. Fast extraction (no embeddings needed)
2. Structured data for direct querying
3. SQLite storage with indexes
4. Works with existing RAG system (fallback to text)
