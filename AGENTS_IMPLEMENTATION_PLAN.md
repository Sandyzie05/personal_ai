# Personal AI Agent Framework: Guardrails, Evals, and Sub-Agent Architecture

## Current Architecture Analysis

### Existing Components
1. ChatEngine - Main orchestration layer with RAG integration
2. RAGEngine - Document retrieval and embedding management
3. DataVault - Encrypted data storage
4. EmbeddingsGenerator - Vector representation of text
5. FileUploadHandler - Document ingestion pipeline

### Extension Points Identified
- Input validation point before LLM call
- Output validation point before returning to user
- Agent routing point between interface and engine
- Monitoring hooks for all major operations

## Solution: Fast Schema-Based Extraction (Implemented) ⚡

### Problem
Embedding generation is slow (10-30 seconds per PDF) because it loads the nomic-embed-text model.

### Solution
Instead of storing full PDF text in vector DB:

1. Extract structured data using pattern matching
2. Store in indexed SQLite tables
3. Query with SQL instead of vector search

### Implementation
- src/data_extraction/bill_extractor.py - Pattern-based extraction
- src/data_extraction/bill_storage.py - SQLite storage with indexes
- src/data_extraction/models.py - Pydantic models

### Performance
- Extraction time: <1 second (vs 10-30s embeddings)
- Query time: <100ms (SQL index lookup)

### Example Query
```python
# Fast SQL query:
bills = storage.get_bills_by_account("203227566")
for bill in bills:
    print(f"\${bill.total_due} due {bill.due_date}")
```

### Test Results
```
Total Due: $326.57
Bill Date: Jul 24, 2026





Due Date: Aug 17, 2026
Account: 203227566
Payments: [PaymentRecord(date='Jul 15, 2026', amount=324.57, method='AutoPay')]
```

## Summary

## What's Fixed
1. ✅ PDF upload works
2. ✅ Chat works (fixed Ollama API compatibility)
3. ✅ Files page shows uploaded PDFs
4. ✅ Structured extraction for bills (<1s vs 10-30s)

## How to Use
1. Upload PDF
2. Data extracted and stored in SQLite with indexes
3. Query with SQL (fast) or use RAG (slow but full text)

## Files Created
- src/data_extraction/models.py
- src/data_extraction/bill_extractor.py
- src/data_extraction/bill_storage.py
- src/data_extraction/__init__.py
- src/data_ingestion/parsers/pdf_parser.py (updated)
