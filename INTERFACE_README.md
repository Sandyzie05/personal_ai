# 🌐 Web Interface Module

Privacy-first Streamlit interface for the Personal AI System.

## 📁 Structure

```
src/interface/
├── __init__.py       # Module initialization
├── main.py           # Main Streamlit application
├── chat.py           # Chat interface and history management
└── config.py         # Configuration UI for Ollama settings
```

## ✨ Features

### 📱 Chat Interface
- Real-time chat with AI assistant
- Conversation history with encryption
- Source citations showing data sources used
- Reply generation with local LLMs

### 🔒 Privacy & Security
- All data encrypted using AES-256-GCM
- Local encryption key management
- Vault integration for chat history storage
- No data leaves your machine

### ⚙️ Configuration
- Ollama host configuration
- Model selection (LLM and embeddings)
- Temperature and token limits
- All settings persisted in encrypted vault

## 🚀 Quick Start

### Prerequisites

1. **Python 3.10+**
2. **Ollama** - Install from https://ollama.com/download
3. **Pull required models**:
   ```bash
   ollama pull llama3.2:latest
   ollama pull nomic-embed-text
   ```

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Ensure Ollama is running
ollama serve
```

### Running the Interface

```bash
# From project root
streamlit run src/interface/main.py

# Or directly
python -m src.interface.main
```

Open your browser at `http://localhost:8501`

## 📋 Usage

### Chat Page (💬 Chat)
1. Type your question in the input field
2. Press "Send" or hit Enter
3. View AI response with source citations
4. Clear history with the "🗑️ Clear" button

### Upload Page (📂 Upload)
1. Select files to upload (txt, pdf, csv, json, md)
2. File will be encrypted and stored locally
3. Future RAG integration will use these documents

### Settings Page (⚙️ Settings)
1. Configure Ollama connection
2. Select models for chat and embeddings
3. Adjust temperature and max tokens
4. Save configuration to encrypted vault

## 🔧 Configuration

The interface stores settings in your encrypted vault:
- Ollama connection settings
- Chat preferences
- User configuration

All configuration is encrypted and stored locally at `~/.personal_ai_vault/`

## 🔐 Security

- All chat history is encrypted before storage
- Encryption keys stored securely in macOS Keychain (or file fallback)
- No external API calls
- Local processing only

## 🎨 UI Design

- **Privacy-focused**: Minimalist design with clear privacy indicators
- **Professional**: Clean interface suitable for sensitive data
- **Accessible**: High contrast and clear labeling
- **Streamlit-styled**: Uses native Streamlit components

## 🐛 Troubleshooting

### Ollama Connection Issues
```
❌ Cannot connect to Ollama: Connection refused
```
**Solution**: Ensure Ollama is running: `ollama serve`

### Ollama Package Not Found
```
ModuleNotFoundError: No module named 'ollama'
```
**Solution**: Install the package: `pip install ollama`

### Vault Initialization Error
```
Failed to initialize vault
```
**Solution**: Check permissions on `~/.personal_ai_vault/` directory

## 📝 Future Enhancements

- [ ] File upload with encryption
- [ ] Document parsing (PDF, DOCX, etc.)
- [ ] Health data import
- [ ] Financial data import
- [ ] Email processing
- [ ] Multi-file context selection
- [ ] Custom system prompts
- [ ] Export chat history

## 🤝 Integration with Other Modules

- **Data Vault**: Stores encrypted chat history and configuration
- **Security**: Uses AES-256-GCM encryption
- **AI Engine**: Connects to Ollama for LLM interactions
- **Embeddings**: Uses nomic-embed-text for vector representations

## 📄 License

Proprietary - Private and confidential
