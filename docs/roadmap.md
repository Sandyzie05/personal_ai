# Original Plan & Feature Roadmap

This is the original planning document written before implementation
started. It's kept for historical context (why certain files/modules exist,
what was originally envisioned) but **does not reflect current state** -
some of it was built differently than planned, some wasn't built at all, and
some was actively superseded (e.g. LlamaIndex and SQLCipher were planned but
never used; the app uses a hand-rolled RAG pipeline and application-level
AES-256-GCM over plain SQLite instead - see `docs/security_design.md` for
what's actually implemented).

For current state, use:
- **`README.md`** - what the project is and how to run it today
- **`docs/security_design.md`** - actual (as-built) security architecture
- **`AGENTS.md`** - guidelines for making further changes

---

## Original Vision

Build a personal AI assistant that helps you with your data and answers
questions about your personal information, health data, financial data,
etc. - all while maintaining maximum security and privacy.

**Core Principle**: Your sensitive data never leaves your machine. All AI
processing happens locally on your device.

## Original Architecture Sketch (aspirational, not fully built as drawn)

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER LAYER                               │
│  [Web UI / CLI / API]                                           │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                     SECURITY LAYER                              │
│  ├── Encrypted Data Vault (AES-256)                             │
│  ├── Local Authentication (biometrics/password)                 │
│  └── Zero External Data Leave Policy                            │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                   AI PROCESSING LAYER                           │
│  ├── Ollama Local LLMs                                          │
│  │   ├── Base Model (llama3.2/Phi-3)                          │
│  │   ├── Embeddings Model (nomic-embed-text)                   │
│  │   └── Specialized Models (medical/finance)                   │
│  └── Local RAG Engine (LlamaIndex/ChromaDB)                     │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                    DATA INGESTION LAYER                         │
│  ├── File Watcher / Importer                                    │
│  ├── Email Parser (Gmail/IMAP)                                  │
│  ├── Health Data (Apple Health/CSV)                             │
│  └── Financial Data (CSV/YNAB/Plaid API alternative)           │
└─────────────────────────────────────────────────────────────────┘
```

Notably not built: biometric/Touch ID auth, "specialized" medical/finance
models, a file watcher, email import, FastAPI/SQLAlchemy backend, GPG,
SQLCipher, Whisper voice interface, cross-device sync. Password-based auth
(not biometric) was added instead - see `docs/security_design.md`.

## Original Phased Plan

### Phase 1: Security Foundation
AES-256-GCM encryption utilities, master key in macOS Keychain (not built -
key is password-wrapped instead), password + biometric auth (biometric not
built), encrypted data vault.

### Phase 2: Local AI Engine Setup
Ollama integration, embeddings, RAG engine via LlamaIndex + ChromaDB (built
with a hand-rolled RAG engine + ChromaDB, not LlamaIndex).

### Phase 3: Web Interface
Streamlit chat interface, conversation history, source citation, privacy
indicators. Built, plus a login gate that wasn't in the original plan but
turned out to be necessary (see `docs/security_design.md`).

### Phase 4: Data Connectors
CSV/Excel, Apple Health XML, PDF/DOCX/TXT, email import. Built: CSV, health
XML, PDF, DOCX parsers, plus a T-Mobile bill extractor
(`src/data_extraction/`, see `BILL_EXTRACTION_README.md`) that wasn't in the
original plan. Not built: email import.

## Original Encryption/Auth Design Notes

- Master key stored in macOS Keychain, fallback to password-based
  derivation, key rotation support, Shamir Secret Sharing for backup - only
  the password-based derivation was built; no Keychain integration, key
  rotation, or SSS backup exist yet.
- Local password authentication, Touch ID, session tokens with expiration,
  brute-force protection - password auth and brute-force lockout exist
  (`src/security/auth.py`); Touch ID and session-token expiry do not.

## Future Enhancements (still aspirational)

- Voice interface (Whisper.cpp)
- Automation triggers
- Analytics dashboard
- Encrypted cross-device sync
- Key rotation, audit logging, air-gapped backups (see README's Security
  section for current status of these)

## Original Risk Assessment

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Data breach** | High | Low | AES-256 encryption, password-derived key (not stored on disk) |
| **Model hallucination** | Medium | Medium | Context constraints, source citation |
| **Hardware loss** | High | Medium | Encrypted offsite backups (not yet implemented) |
| **API compromise** | Low | Low | No external APIs needed |
| **Key loss** | High | Low | No backup/recovery mechanism yet if the vault password is forgotten - this is a real current gap, not just historical |
