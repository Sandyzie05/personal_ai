# 🛡️ Secure Personal AI System

A personal AI assistant that processes your sensitive data (health, finances, emails) **entirely locally** using open-source models, with strong encryption for data at rest and in transit.

---

## 🎯 Vision

Build a personal AI assistant that helps you with your data and answers questions about your personal information, health data, financial data, etc. - all while maintaining maximum security and privacy.

**Core Principle**: Your sensitive data never leaves your machine. All AI processing happens locally on your device.

---

## 📋 Current Status

**Status**: 🟢 Working prototype - security-reviewed 2026-08-23

Core pieces are implemented and working end-to-end: password-gated encrypted
vault, local RAG over Ollama, and a Streamlit chat UI. See
`docs/security_design.md` for the real (as-built) security architecture and
`AGENTS.md` before making further changes - both were written after a
security pass that fixed several gaps between this document's original plan
and what the code actually did (most notably: the app had no login screen at
all before that pass).

---

## 🏗️ Architecture Overview

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

---

## 📁 Current State

**Working Directory**: `/Users/sandeepgupta/code/personal_ai`

**Status**: Empty workspace - ready for implementation

---

## 🔧 Technology Stack

| Layer | Technologies | Rationale |
|-------|-------------|-----------|
| **AI/ML** | Ollama, llama3.2, Phi-3, nomic-embed-text, LlamaIndex, ChromaDB | Open-source, local-first, macOS optimized |
| **Backend** | Python (FastAPI/Streamlit), SQLAlchemy | Easy development, excellent encryption libraries |
| **Frontend** | Streamlit (web), Rich (CLI) | Quick UI development, good for prototyping |
| **Encryption** | Python `cryptography`, GPG | Well-maintained, FIPS-compliant |
| **Database** | SQLite + SQLCipher | Encrypted, serverless, perfect for local use |
| **Data Parsing** | Pandas, PyPDF2, python-docx, email-parser | Industry standard Python libraries |

---

## 🛡️ Security Model

### Data Protection Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. DATA INGESTION (Unencrypted → Encrypted)                │
│    User Input/Import  →  Validation  →  ENCRYPTION  → DB  │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│ 2. STORAGE (Always Encrypted)                              │
│    AES-256-GCM Encrypted Database                         │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│ 3. PROCESSING (In-Memory Only)                             │
│    Data Decrypted → AI Processing → Results Encrypted      │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│ 4. OUTPUT (Encrypted/Anonymized)                           │
│    Responses scrubbed, sensitive data redacted             │
└─────────────────────────────────────────────────────────────┘
```

### Security Best Practices

- ✅ **No data leaves the device** - All AI processing local
- ✅ **AES-256-GCM encryption** for data at rest, keyed by a password-derived
  key (PBKDF2-HMAC-SHA256, 600k iterations) - see `docs/security_design.md`
- ✅ **Login gate** in the Streamlit app; the encryption key is never written
  to disk in plaintext
- ✅ **Persistent lockout** after 5 failed password attempts
- ⚠️ **Known residual risk**: the RAG search index (ChromaDB) stores
  document text in plaintext at `~/.personal_ai_vault/.chroma` (0700
  permissions) for local semantic search - not yet encrypted at rest. See
  `docs/security_design.md` for why and what mitigates it today.
- ❌ Key rotation, audit logging, and air-gapped backup below are aspirational
  and not yet implemented - don't assume they exist.

---

## 📅 Implementation Plan

### Phase 1: Security Foundation (Week 1)
**Goal**: Build the encrypted data vault that will store all your sensitive data.

**Deliverables**:
- AES-256-GCM encryption utilities
- Master key stored in macOS Keychain
- Password + biometric (Touch ID) authentication
- Directory structure for organized data

**Files**:
```
src/
├── security/
│   ├── encryption.py      # AES-256 encryption utilities
│   ├── key_manager.py     # Key generation/storage
│   └── auth.py            # Authentication layer
└── data_vault/
    ├── vault.py           # Main vault interface
    └── file_handler.py    # File operations
```

---

### Phase 2: Local AI Engine Setup (Week 2)
**Goal**: Get Ollama running with the right models and set up RAG.

**Steps**:
1. Install Ollama on macOS
2. Pull models: `  ollama pull llama3.2:latest`, `ollama pull nomic-embed-text`
3. Set up Python API integration
4. Build local RAG with LlamaIndex + ChromaDB

**Files**:
```
src/
└── ai_engine/
    ├── ollama_client.py   # Ollama API client
    ├── embeddings.py      # Embedding generation
    ├── rag_engine.py      # RAG orchestration
    └── model_manager.py   # Model loading/management
```

---

### Phase 3: Web Interface (Week 3-4)
**Goal**: Create the Streamlit chat interface.

**Files**:
```
src/
└── interface/
    ├── streamlit_app.py      # Main Streamlit app
    ├── chat_engine.py        # Chat logic with RAG
    └── ui_components.py      # Reusable UI elements
```

**Features**:
- Chat interface with conversation history
- Source citation (which files data came from)
- Clear privacy indicators

---

### Phase 4: Data Connectors (Week 5+)
**Goal**: Import data from various sources.

**Importers**:
- CSV/Excel parser (financial data)
- Apple Health XML parser
- PDF/TXT/DOCX parser
- Email import (IMAP/Gmail export)

**Files**:
```
src/
└── data_ingestion/
    ├── health_importer.py    # Apple Health data
    ├── finance_importer.py   # Financial data
    ├── email_importer.py     # Email data
    └── document_parser.py    # PDF/DOCX/TXT
```

---

## 🔐 Security Architecture Details

### Encryption Strategy

**AES-256-GCM** encryption for:
- All stored data (database, files)
- Data in memory during processing
- Backup files

**Key Management**:
- Master key stored in macOS Keychain
- Fallback to password-based key derivation
- Key rotation support
- Shamir Secret Sharing for backup

### Authentication

- Local password authentication
- Touch ID integration (macOS)
- Session tokens with expiration
- Brute-force protection

---

## 📊 Feature Roadmap

### Phase 1 MVP (End of Week 1)
- ✅ Encrypted data storage
- ✅ Command-line interface
- ✅ Basic file import/export
- ✅ Local encryption/decryption

### Phase 2 (End of Week 2)
- ✅ Local LLM integration (Ollama)
- ✅ Vector embeddings
- ✅ Basic RAG functionality

### Phase 3 (End of Week 4)
- ✅ Web interface (Streamlit)
- ✅ Chat interface
- ✅ Conversation history
- ✅ Source citation

### Phase 4 (End of Week 6+)
- ✅ Health data import
- ✅ Financial data import
- ✅ Email processing
- ✅ Document parsing

### Future Enhancements
- Voice interface (Whisper.cpp)
- Automation triggers
- Analytics dashboard
- Encrypted cross-device sync

---

## 🚀 Getting Started (Future Implementation)

### Prerequisites
- macOS (or Linux/Windows)
- Python 3.10+
- Ollama installed
- Touch ID (optional, for biometric auth)

### Installation Steps

```bash
# 1. Clone the repository
git clone <repository-url>
cd personal_ai

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate

# 3. Install dependencies
make install   # or: pip install -r requirements.txt

# 4. Pull the required Ollama models (chat model is configurable, see below)
ollama pull qwen3:8b
ollama pull nomic-embed-text

# 5. Start the application - first run will ask you to set a vault password
make run       # or: PYTHONPATH=. streamlit run src/interface/main.py
```

Override the default chat/embedding models or Ollama host via environment
variables instead of editing code - see `src/config.py`:

```bash
export PERSONAL_AI_CHAT_MODEL=qwen3:8b
export PERSONAL_AI_EMBED_MODEL=nomic-embed-text
```

Before shipping a change to the chat/RAG path, run `make check-context` to
confirm the app's context-window assumptions still match the model you're
using.

---

## 📝 Implementation Checklist

- [ ] Phase 1: Security Foundation
  - [ ] AES-256 encryption utilities
  - [ ] Key management system
  - [ ] Authentication layer
  - [ ] Encrypted data vault
  - [ ] File handler utilities

- [ ] Phase 2: Local AI Engine
  - [ ] Ollama installation and setup
  - [ ] Model pulling and management
  - [ ] Embedding generation
  - [ ] RAG engine integration
  - [ ] Local API server

- [ ] Phase 3: Web Interface
  - [ ] Streamlit setup
  - [ ] Chat interface
  - [ ] Conversation management
  - [ ] Source citation
  - [ ] Privacy indicators

- [ ] Phase 4: Data Connectors
  - [ ] File importers (CSV, PDF, DOCX)
  - [ ] Health data parser
  - [ ] Financial data parser
  - [ ] Email parser
  - [ ] Data validation

---

## 🔍 Risk Assessment & Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Data breach** | High | Low | AES-256 encryption, keys stored separately |
| **Model hallucination** | Medium | Medium | Context constraints, source citation |
| **Hardware loss** | High | Medium | Encrypted offsite backups |
| **API compromise** | Low | Low | No external APIs needed |
| **Key loss** | High | Low | Multi-device key backup with SSS |

---

## 📚 References & Resources

### Tools & Technologies
- [Ollama Documentation](https://ollama.com/docs)
- [LlamaIndex Documentation](https://docs.llamaindex.ai/)
- [ChromaDB Documentation](https://docs.trychroma.com/)
- [Python Cryptography Library](https://cryptography.io/)

### Security Standards
- AES-256 encryption
- GCM mode for authenticated encryption
- Key rotation best practices

---

## 👥 Contributing & Maintenance

This is a personal project, but contributions and suggestions are welcome. Always prioritize security over features.

---

## 📄 License

This project is proprietary. All code and data are private and confidential.

---

## 🎯 Next Steps

1. **Review this plan** - Ensure all requirements are covered
2. **Phase 1 Implementation** - Build security infrastructure
3. **Test encryption** - Verify data protection works
4. **Iterate** - Add features based on need

---

**Last Updated**: August 19, 2026  
**Status**: Planning Complete, Ready for Implementation  
**Next Action**: Start Phase 1 implementation
