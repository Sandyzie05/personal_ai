# Personal AI System Architecture

## Overview

The Personal AI system is a single-user, local-first Streamlit assistant.
Its encrypted primary vault supports document-backed chat, configurable MCP
agents and a separate public-market forecasting workflow. It is not an
offline-only system or a multi-user service.

Current status and ordered build gates live in
[development_planning.md](development_planning.md). The proposed integrated
capstone is described in [agentic_system_design.md](agentic_system_design.md).

## Key Components

### Core Architecture Layers

1. **User Interface Layer** - Streamlit web interface with chat, upload, settings, and dashboard pages
2. **Security Layer** - AES-256-GCM encryption, password-based authentication, encrypted data vault
3. **AI Processing Layer** - Local LLM inference with Ollama/oMLX interfaces, RAG engine with ChromaDB
4. **Data Ingestion Layer** - File parsing for PDF, DOCX, CSV, and structured data extraction
5. **Agent Layer** - User-selected configurable agents with MCP tools; no automatic cross-agent coordinator
6. **Forecast Layer** - Fixed public-data adapters, bounded parallel specialists, explicit encrypted forecast/outcome saves

### Security Design

The actual boundaries and residual risks are documented in
[security_design.md](security_design.md):

- AuthManager generates a random DEK and wraps it with a password-derived
  KEK; only wrapped key material is persisted.
- Vault values, chat history, agent configuration and OAuth tokens are
  encrypted with AES-256-GCM. SQLite itself is not SQLCipher.
- Decrypted data exists in process memory. The Chroma index contains
  plaintext document text/metadata under the private vault directory.
- The UI must stay bound to localhost. Inference endpoints are configurable,
  so private inference must use an explicitly trusted local endpoint.
- Remote MCP servers receive selected tool names and arguments, which can
  contain private data. Public forecast adapters receive narrow validated
  public inputs; specialists receive only public evidence.
- Agent confirmation is configurable and tool classification is not a
  complete authorization policy. Generic agents are broader than the
  proposed research-only capstone.

## System Integration

### AI Engine
The system integrates with local LLM providers through:
- Ollama for local inference
- oMLX support for alternative local models
- Ollama-compatible tool calling
- ChromaDB for vector-based RAG (Retrieval-Augmented Generation)

### Multi-Agent Framework
The multi-agent system allows:
- User-defined agents with custom models, instructions, and tool access
- MCP (Model Context Protocol) server integration for external tool access
- Tool use with confirmation gates for sensitive actions
- Agent session binding for maintaining conversation context
- Built-in "General" agent for backward compatibility

The general agent runtime and forecasting specialists are separate execution
paths. Forecast parallelism is not evidence of dynamic agent delegation,
scheduled execution or approved long-term task memory.

### Data Management
- Structured record extraction and storage
- Encrypted file storage
- Document categorization and retrieval
- RAG index for enhanced querying

## Ownership and execution flow

```text
Browser -> localhost Streamlit login gate -> AuthManager -> session DEK
                                            |
                 encrypted DataVault <------+--> document/chat/agent state
                         |
               decrypted scoped context -> configured inference endpoint
                         |
                    local Chroma index (plaintext residual risk)

User-selected MCP agent -> configured tools -> remote HTTP or local process
Forecast page -> validated public ticker -> fixed public providers
              -> public-only specialists -> explicit preview/save -> vault
```

Domain modules remain independent of Streamlit. The interface owns consent
and orchestration; AuthManager owns password-to-DEK derivation; DataVault
owns encrypted records. Models must not authorize persistence or expand
network/tool permissions. No scheduler, account connector or separate
coordinator is implied by this diagram.