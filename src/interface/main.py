#!/usr/bin/env python3
"""
Personal AI System - Web Interface

Privacy-first personal assistant with encrypted data storage.
All processing happens locally using open-source AI models.
"""

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx
import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional, List

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.data_vault import DataVault, create_vault
from src.security.encryption import AEADEncryption
from src.interface.chat import ChatHistory, display_message, display_chat_history


class PersonalAIInterface:
    """Main interface for the Personal AI System."""
    
    def __init__(self):
        self.vault: Optional[DataVault] = None
        self.encryption_key: Optional[bytes] = None
        self.chat_history: Optional[ChatHistory] = None
        self.ollama_config: Dict[str, Any] = {}
        self.ai_client: Optional[Any] = None
        self.uploaded_files: List[str] = []
        self.chat_engine: Optional[Any] = None
        self._initialized: bool = False
        # Don't initialize heavy components on startup
        # They'll be initialized lazily when needed
    
    def _get_vault_instance(self, _key_path: str, _key: bytes) -> DataVault:
        """Vault instance to prevent multiple instances during initialization."""
        return create_vault(vault_path=_key_path, encryption_key=_key)
    
    def _get_chat_engine_instance(self, _key_path: str, _key: bytes) -> Any:
        """ChatEngine instance to prevent multiple instances during initialization."""
        from src.ai_engine.chat_engine import ChatEngine
        return ChatEngine(
            vault_path=_key_path,
            encryption_key=_key
        )
    
    def _init_vault(self) -> None:
        """Initialize encrypted data vault."""
        if self._initialized and self.vault is not None:
            return
        
        try:
            encryption_key_path = os.path.expanduser("~/.personal_ai_key")
            vault_path = os.path.expanduser("~/.personal_ai_vault")
            
            if os.path.exists(encryption_key_path):
                with open(encryption_key_path, "rb") as f:
                    self.encryption_key = f.read()
            
            if self.encryption_key:
                self.vault = self._get_vault_instance(vault_path, self.encryption_key)
            else:
                # Create unencrypted vault when no key
                self.vault = self._get_vault_instance(vault_path, None)
            
        except Exception as e:
            st.error(f"Failed to initialize vault: {str(e)}")
            st.info("Encryption key will be created on first use")
        
        self._initialized = True
    
    def _ensure_vault_and_rag(self) -> None:
        """Ensure vault and RAG are initialized (lazy initialization)."""
        if not self._initialized:
            self._init_vault()
        
        # Skip if no encryption key or if already initialized
        if self.encryption_key is None or self.chat_engine is not None:
            return
        
        try:
            vault_path = os.path.expanduser("~/.personal_ai_vault")
            self.chat_engine = self._get_chat_engine_instance(vault_path, self.encryption_key)
            self.chat_history = ChatHistory(self.vault)
        except Exception as e:
            st.warning(f"Could not initialize RAG: {str(e)}")
    
    def _show_rag_not_ready(self) -> bool:
        """Check if RAG is not ready due to missing model."""
        if not self.encryption_key:
            st.warning("🔒 Encryption key not loaded. Please reload the app.")
            return True
        if self.chat_engine is None:
            st.warning("🤖 AI model not loaded. This will be initialized on first chat.")
            return True
        return False
    
    def _initialize_rag(self) -> None:
        """Initialize chat engine with RAG for querying vault data."""
        if self.encryption_key is None:
            return
        
        try:
            from src.ai_engine.chat_engine import ChatEngine
            
            self.chat_engine = ChatEngine(
                vault_path=os.path.expanduser("~/.personal_ai_vault"),
                encryption_key=self.encryption_key
            )
        except Exception as e:
            st.warning(f"Could not initialize RAG: {str(e)}")
    
    def _get_ai_response(self, user_query: str) -> tuple[str, list[str]]:
        """Get AI response from Ollama with source citations."""
        if not self.ollama_config:
            self._load_ollama_config()
        
        try:
            if self.chat_engine:
                result = self.chat_engine.query_vault(user_query, use_rag=True)
                return result["response"], result.get("context_used", [])
            
            try:
                import ollama
                ollama_available = True
            except ImportError:
                ollama_available = False
            
            if not ollama_available:
                return (
                    "⚠️ Ollama is not installed. Please install it to enable AI chat.\n\n"
                    "Install: https://ollama.com/download\n\n"
                    "Or run: pip install ollama",
                    []
                )
            
            host = self.ollama_config.get("ollama_host", "http://localhost:11434")
            model = self.ollama_config.get("ollama_model", "llama3.2:latest")
            
            client = ollama.Client(host=host)
            
            messages = [
                {"role": "system", "content": "You are a helpful personal assistant with access to your local data. Be concise and cite your sources."}
            ]
            
            if self.chat_history:
                for msg in self.chat_history.get_recent_messages(5):
                    messages.append({"role": msg.role, "content": msg.content})
            
            messages.append({"role": "user", "content": user_query})
            
            response = client.chat(
                model=model,
                messages=messages,
                stream=False
            )
            
            content = response["message"]["content"]
            
            sources = self._generate_sources(user_query)
            
            return content, sources
            
        except Exception as e:
            return f"Error getting response: {str(e)}. Make sure Ollama is running at {self.ollama_config.get('ollama_host')}", []
    
    def _generate_sources(self, query: str) -> list[str]:
        """Generate source citations based on query."""
        sources = []
        
        vault_keys = self.vault.list_keys() if self.vault else []
        
        if vault_keys:
            sources.append(f"📁 Querying {len(vault_keys)} local data files")
        else:
            sources.append("ℹ️ No local data files found. Upload documents to enable data-driven answers.")
        
        return sources
    
    def _load_ollama_config(self) -> None:
        """Load Ollama configuration from vault or use defaults."""
        config_data = self.vault.retrieve_data("ollama_config") if self.vault else None
        
        if config_data:
            self.ollama_config = config_data
        else:
            self.ollama_config = {
                "ollama_host": "http://localhost:11434",
                "ollama_model": "llama3.2:latest",
                "ollama_embeddings": "nomic-embed-text",
                "temperature": 0.7,
                "max_tokens": 2048,
                "stream": True
            }
    
    def render_sidebar(self) -> None:
        """Render sidebar with navigation and settings."""
        with st.sidebar:
            st.title("Private AI")
            
            st.divider()
            
            page = st.radio(
                "Navigation",
                ["💬 Chat", "📂 Upload", "📄 Files", "⚙️ Settings"],
                label_visibility="collapsed"
            )
            
            st.divider()
            
            st.caption("🔒 Privacy Features")
            st.progress(1.0, text="All processing is local - no data leaves your machine")
            
            st.divider()
            
            # Show uploaded files count
            if self.vault:
                keys = self.vault.list_keys()
                # Filter out internal keys
                file_keys = [k for k in keys if not k.startswith("vault_") and k != "chat_history" and k != "ollama_config"]
                st.caption("📁 Uploaded Files")
                st.text(f"Total: {len(file_keys)} documents")
            
            if st.button("🔑 Encryption Key Status"):
                if self.encryption_key:
                    st.success("✅ Encryption key loaded from ~/.personal_ai_key")
                else:
                    st.warning("⚠️ No encryption key found")
            
                if st.button("📊 Vault Status"):
                    if self.vault:
                        keys = self.vault.list_keys()
                        st.info(f"Vault: {len(keys)} encrypted files")
                        if keys:
                            st.write("### Uploaded Files")
                            for key in keys:
                                data = self.vault.retrieve_data(key)
                                if data and isinstance(data, dict):
                                    metadata = data.get("metadata", {})
                                    if metadata:
                                        with st.expander(f"📄 {metadata.get('original_filename', key)}"):
                                            st.json({
                                                "File Type": metadata.get("file_type"),
                                                "Size": metadata.get("file_size"),
                                                "Uploaded": metadata.get("upload_timestamp")
                                            })
            
            st.divider()
            
            st.caption("🤖 AI Status")
            if st.button("🔄 Check Ollama"):
                try:
                    try:
                        import ollama
                        client = ollama.Client(host=self.ollama_config.get("ollama_host"))
                        models = client.list()
                        st.success(f"✅ Ollama connected! {len(models.get('models', []))} models available")
                        for model in models.get("models", [])[:5]:
                            st.text(f"  • {model.get('model', 'unknown')}")
                    except ImportError:
                        st.error("❌ Ollama package not installed")
                        st.info("Install: pip install ollama")
                except Exception as e:
                    st.error(f"❌ Cannot connect to Ollama: {str(e)}")
                    st.info("Make sure Ollama is running: https://ollama.com/download")
            
            return page
    
    def render_chat_page(self) -> None:
        """Render chat interface."""
        st.header("💬 Chat with Your Data")
        st.caption("All interactions are encrypted and stored locally")
        
        self._ensure_vault_and_rag()
        
        if not self.chat_history:
            self.chat_history = ChatHistory(self.vault)
        
        display_chat_history(self.chat_history)
        
        st.divider()
        
        if st.button("🔄 Rebuild RAG Index"):
            with st.spinner("Rebuilding RAG index from vault data..."):
                self._init_vault()
                self._initialize_rag()
            st.success("✅ RAG index rebuilt!")
            st.rerun()
        
        user_input = st.text_input(
            "Ask a question...",
            placeholder="What would you like to know?",
            label_visibility="collapsed"
        )
        
        col1, col2 = st.columns([9, 1])
        
        with col1:
            if st.button("🚀 Send", type="primary", use_container_width=True):
                if user_input.strip():
                    with st.spinner("Thinking..."):
                        response, sources = self._get_ai_response(user_input)
                        
                        self.chat_history.add_message("user", user_input)
                        self.chat_history.add_message("assistant", response, sources)
                        
                        st.rerun()
        
        with col2:
            if st.button("🗑️ Clear", use_container_width=True):
                if st.confirm("Clear all chat history?"):
                    self.chat_history.clear()
                    st.rerun()
    
    def render_upload_page(self) -> None:
        """Render file upload page for data ingestion."""
        st.header("📂 Upload Data")
        st.caption("Encrypted file upload - data will be processed locally")
        
        uploaded_file = st.file_uploader(
            "Choose a file",
            type=["txt", "pdf", "csv", "json", "md"],
            label_visibility="collapsed"
        )
        
        if uploaded_file is not None:
            import tempfile
            import os
            
            file_info = {
                "filename": uploaded_file.name,
                "size": uploaded_file.size,
                "type": uploaded_file.type
            }
            
            st.success(f"📄 {uploaded_file.name} ({uploaded_file.size} bytes) ready for upload")
            st.info("Data will be encrypted and stored in your local vault")
            
            if st.button("🔒 Upload & Encrypt"):
                with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{uploaded_file.name}") as tmp_file:
                    tmp_file.write(uploaded_file.getvalue())
                    tmp_path = tmp_file.name
                
                try:
                    from src.data_ingestion.handlers import FileUploadHandler
                    
                    handler = FileUploadHandler(self.vault, self.encryption_key)
                    result = handler.handle_upload(tmp_path)
                    
                    if result["success"]:
                        st.success(f"✅ File uploaded successfully!")
                        st.json({
                            "Storage Key": result["storage_key"],
                            "File Type": result["file_type"],
                            "Text Length": result["text_length"]
                        })
                        
                        self.uploaded_files = handler.list_uploaded_files()
                        
                        if self.chat_engine:
                            self.chat_engine.initialize_rag()
                            st.success("✅ RAG index updated with new file!")
                    else:
                        st.error("❌ Upload failed")
                except Exception as e:
                    st.error(f"❌ Upload failed: {str(e)}")
                finally:
                    os.unlink(tmp_path)
    
    def render_config_page(self) -> None:
        """Render configuration page."""
        st.header("⚙️ Settings")
        st.divider()
        
        from src.interface.config import render_config_page
        
        self._init_vault()  # Initialize vault but not RAG
        
        self.ollama_config = render_config_page()
        
        if st.button("💾 Save to Vault"):
            if self.vault:
                self.vault.store_data("ollama_config", self.ollama_config)
                st.success("✅ Configuration saved to encrypted vault")
    
    def render_files_page(self) -> None:
        """Render a page showing all uploaded files from the vault."""
        st.header("📄 Your Uploaded Files")
        st.caption("All files are encrypted and stored locally in your vault")
        
        self._init_vault()  # Initialize vault but not RAG
        
        if not self.vault:
            st.warning("Vault not initialized. Please reload the page.")
            return
        
        keys = self.vault.list_keys()
        
        # Filter out internal keys
        file_keys = [k for k in keys if not k.startswith("vault_") and k != "chat_history" and k != "ollama_config"]
        
        if not file_keys:
            st.info("No files uploaded yet. Go to the Upload page to add documents.")
            return
        
        st.success(f"Found {len(file_keys)} uploaded file(s)")
        
        # Display each file
        for key in file_keys:
            try:
                data = self.vault.retrieve_data(key)
                
                if not data or not isinstance(data, dict):
                    continue
                
                metadata = data.get("metadata", {})
                original_filename = metadata.get("original_filename", key)
                file_type = metadata.get("file_type", "unknown")
                file_size = metadata.get("file_size", 0)
                upload_timestamp = metadata.get("upload_timestamp", "unknown")
                
                # Calculate human-readable file size
                if file_size > 1024 * 1024:
                    size_str = f"{file_size / (1024 * 1024):.2f} MB"
                elif file_size > 1024:
                    size_str = f"{file_size / 1024:.2f} KB"
                else:
                    size_str = f"{file_size} bytes"
                
                with st.expander(f"📄 {original_filename}", expanded=False):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Type", file_type.upper())
                    with col2:
                        st.metric("Size", size_str)
                    with col3:
                        st.metric("Uploaded", upload_timestamp.split("T")[0] if "T" in upload_timestamp else upload_timestamp)
                    
                    st.divider()
                    
                    if data.get("text_content"):
                        text_length = len(data["text_content"])
                        st.info(f"📄 Text content: {text_length} characters")
                        
                        # Show preview if not too long
                        if text_length <= 2000:
                            with st.expander("👁️ View Text Preview"):
                                st.text(data["text_content"][:2000])
                                if text_length > 2000:
                                    st.text(f"... ({text_length - 2000} more characters)")
                    
                    if st.button("🗑️ Delete", key=f"delete_{key}"):
                        self.vault.delete_data(key)
                        st.rerun()
                        
            except Exception as e:
                st.error(f"Error loading file {key}: {str(e)}")
    
    def _setup_page(self) -> None:
        """Configure page settings (must be called first)."""
        # Initialize vault first
        try:
            self._init_vault()
        except Exception as e:
            st.error(f"Vault initialization failed: {str(e)}")
        
        st.set_page_config(
            page_title="Private AI Assistant",
            page_icon=None,
            layout="wide",
            initial_sidebar_state="expanded"
        )
        
        hide_streamlit_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            </style>
        """
        st.markdown(hide_streamlit_style, unsafe_allow_html=True)
    
    def run(self) -> None:
        """Run the main interface."""
        self._setup_page()
        
        page = self.render_sidebar()
        
        if page == "💬 Chat":
            self.render_chat_page()
        elif page == "📂 Upload":
            self.render_upload_page()
        elif page == "📄 Files":
            self.render_files_page()
        elif page == "⚙️ Settings":
            self.render_config_page()


app = PersonalAIInterface()


if __name__ == "__main__":
    app.run()
