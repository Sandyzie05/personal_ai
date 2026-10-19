# Personal AI System Architecture

## Overview

The Personal AI system is a privacy-first, local-only AI assistant designed to process sensitive personal data entirely on your device. It uses open-source models and implements robust security measures to protect user data.

## Key Components

### Core Architecture Layers

1. **User Interface Layer** - Streamlit web interface with chat, upload, settings, and dashboard pages
2. **Security Layer** - AES-256-GCM encryption, password-based authentication, encrypted data vault
3. **AI Processing Layer** - Local LLM inference with Ollama/oMLX interfaces, RAG engine with ChromaDB
4. **Data Ingestion Layer** - File parsing for PDF, DOCX, CSV, and structured data extraction
5. **Multi-Agent Layer** - Configurable agent system with MCP integration for external tools

### Security Design

The security architecture is documented in detail in `docs/security_design.md`. Key principles include:
- All data encryption using AES-256-GCM
- Master key derivation from user password with secure storage
- Encrypted vault for all sensitive data at rest
- Local-only processing with no data leaving the device
- Password-based authentication with brute-force protection

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

### Data Management
- Structured record extraction and storage
- Encrypted file storage
- Document categorization and retrieval
- RAG index for enhanced querying