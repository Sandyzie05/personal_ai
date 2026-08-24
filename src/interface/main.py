#!/usr/bin/env python3
"""
Personal AI System - Web Interface

Privacy-first personal assistant with encrypted data storage.
All processing happens locally using open-source AI models.
"""

import streamlit as st
import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional, List

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.ai_engine.chat_engine import ChatEngineError
from src.data_vault import DataVault, DataVaultError, create_vault
from src.interface.chat import (
    ASSISTANT_AVATAR,
    USER_AVATAR,
    ChatHistory,
    render_chat_history,
    render_sources,
)
from src.security.auth import AuthManager, AuthenticationError
from src.config import DEFAULT_OLLAMA_HOST, DEFAULT_CHAT_MODEL, DEFAULT_EMBED_MODEL


@st.dialog("Clear chat history?")
def _clear_chat_dialog(chat_history: ChatHistory) -> None:
    """Confirm before permanently deleting chat history.

    Replaces a prior call to `st.confirm(...)`, which is not a real
    Streamlit API and would have raised AttributeError the first time
    anyone clicked Clear.
    """
    st.write(
        "This permanently deletes all messages in this chat. This cannot be undone."
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Clear", type="primary", use_container_width=True):
            chat_history.clear()
            st.rerun()
    with cancel_col:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


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

        return ChatEngine(vault_path=_key_path, encryption_key=_key)

    def _init_vault(self) -> None:
        """Initialize encrypted data vault using the DEK unlocked at login.

        The DEK is never read from a plaintext key file here - it lives only
        in st.session_state for the duration of the authenticated session,
        having been unwrapped from the password-protected auth file by
        AuthManager during _render_login_gate().
        """
        if self._initialized and self.vault is not None:
            return

        self.encryption_key = st.session_state.get("vault_key")
        if not self.encryption_key:
            # Should never happen: run() gates all rendering behind login.
            st.error("No unlocked vault key in this session. Please log in again.")
            return

        try:
            vault_path = os.path.expanduser("~/.personal_ai_vault")
            self.vault = self._get_vault_instance(vault_path, self.encryption_key)
        except Exception as e:
            st.error(f"Failed to initialize vault: {str(e)}")

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
            self.chat_engine = self._get_chat_engine_instance(
                vault_path, self.encryption_key
            )
            self.chat_history = ChatHistory(self.vault)
        except Exception as e:
            st.warning(f"Could not initialize RAG: {str(e)}")

    def _show_rag_not_ready(self) -> bool:
        """Check if RAG is not ready due to missing model."""
        if not self.encryption_key:
            st.warning("🔒 Encryption key not loaded. Please reload the app.")
            return True
        if self.chat_engine is None:
            st.warning(
                "🤖 AI model not loaded. This will be initialized on first chat."
            )
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
                encryption_key=self.encryption_key,
            )
        except Exception as e:
            st.warning(f"Could not initialize RAG: {str(e)}")

    def _respond(self, user_query: str) -> tuple[str, list[str]]:
        """Render the assistant's answer inside the caller's st.chat_message block.

        Streams token-by-token through the RAG chat engine when available
        (so the reply appears incrementally instead of after a multi-second
        blocking wait); falls back to a single non-streaming Ollama call
        with no vault-grounded retrieval if the chat engine couldn't be
        initialized (e.g. Ollama was unreachable at startup).
        """
        if self.chat_engine:
            try:
                stream = self.chat_engine.query_vault_stream(user_query, use_rag=True)
                response_text = st.write_stream(stream)
                return response_text, stream.sources
            except ChatEngineError as e:
                error_text = f"⚠️ {e}"
                st.error(error_text)
                return error_text, []

        with st.spinner("Thinking..."):
            response_text, sources = self._get_fallback_response(user_query)
        st.markdown(response_text)
        return response_text, sources

    def _get_fallback_response(self, user_query: str) -> tuple[str, list[str]]:
        """Non-streaming fallback used only when the RAG chat engine is unavailable."""
        if not self.ollama_config:
            self._load_ollama_config()

        try:
            import ollama
        except ImportError:
            return (
                "⚠️ Ollama is not installed. Please install it to enable AI chat.\n\n"
                "Install: https://ollama.com/download\n\n"
                "Or run: pip install ollama",
                [],
            )

        try:
            host = self.ollama_config.get("ollama_host", DEFAULT_OLLAMA_HOST)
            model = self.ollama_config.get("ollama_model", DEFAULT_CHAT_MODEL)

            client = ollama.Client(host=host)

            messages = [
                {
                    "role": "system",
                    "content": "You are a helpful personal assistant with access to your local data. Be concise and cite your sources.",
                }
            ]

            if self.chat_history:
                for msg in self.chat_history.get_recent_messages(5):
                    messages.append({"role": msg.role, "content": msg.content})

            messages.append({"role": "user", "content": user_query})

            response = client.chat(model=model, messages=messages, stream=False)

            content = response["message"]["content"]

            sources = self._generate_sources(user_query)

            return content, sources

        except Exception as e:
            return (
                f"Error getting response: {str(e)}. Make sure Ollama is running at {self.ollama_config.get('ollama_host')}",
                [],
            )

    def _generate_sources(self, query: str) -> list[str]:
        """Generate source citations based on query."""
        sources = []

        vault_keys = self.vault.list_keys() if self.vault else []

        if vault_keys:
            sources.append(f"📁 Querying {len(vault_keys)} local data files")
        else:
            sources.append(
                "ℹ️ No local data files found. Upload documents to enable data-driven answers."
            )

        return sources

    def _load_ollama_config(self) -> None:
        """Load Ollama configuration from vault or use defaults.

        Falls back to defaults on DataVaultError (e.g. this entry was
        encrypted under a different vault key than the one unlocked now)
        instead of crashing the whole page.
        """
        config_data = None
        if self.vault:
            try:
                config_data = self.vault.retrieve_data("ollama_config")
            except DataVaultError as e:
                st.warning(f"⚠️ Could not load saved settings ({e}). Using defaults.")

        if config_data:
            self.ollama_config = config_data
        else:
            self.ollama_config = {
                "ollama_host": DEFAULT_OLLAMA_HOST,
                "ollama_model": DEFAULT_CHAT_MODEL,
                "ollama_embeddings": DEFAULT_EMBED_MODEL,
                "temperature": 0.7,
                "max_tokens": 2048,
                "stream": True,
            }

    def render_sidebar(self) -> None:
        """Render sidebar with navigation and settings."""
        with st.sidebar:
            st.title("Private AI")

            st.divider()

            page = st.radio(
                "Navigation",
                ["💬 Chat", "📂 Upload", "📄 Files", "⚙️ Settings"],
                label_visibility="collapsed",
            )

            st.divider()

            if st.button("🔓 Lock vault"):
                st.session_state.pop("vault_key", None)
                self.vault = None
                self.chat_engine = None
                self._initialized = False
                st.rerun()

            st.divider()

            st.caption("🔒 Privacy Features")
            st.progress(
                1.0, text="All processing is local - no data leaves your machine"
            )

            st.divider()

            # Show uploaded files count
            if self.vault:
                keys = self.vault.list_keys()
                # Filter out internal keys
                file_keys = [
                    k
                    for k in keys
                    if not k.startswith("vault_")
                    and k != "chat_history"
                    and k != "ollama_config"
                ]
                st.caption("📁 Uploaded Files")
                st.text(f"Total: {len(file_keys)} documents")

            if st.button("🔑 Encryption Key Status"):
                if self.encryption_key:
                    st.success("✅ Vault key unlocked for this session")
                else:
                    st.warning("⚠️ No encryption key unlocked")

            if st.button("📊 Vault Status"):
                if self.vault:
                    keys = self.vault.list_keys()
                    st.info(f"Vault: {len(keys)} encrypted files")
                    if keys:
                        st.write("### Uploaded Files")
                        for key in keys:
                            try:
                                data = self.vault.retrieve_data(key)
                            except DataVaultError as e:
                                st.caption(f"⚠️ {key}: could not decrypt ({e})")
                                continue
                            if data and isinstance(data, dict):
                                metadata = data.get("metadata", {})
                                if metadata:
                                    with st.expander(
                                        f"📄 {metadata.get('original_filename', key)}"
                                    ):
                                        st.json(
                                            {
                                                "File Type": metadata.get("file_type"),
                                                "Size": metadata.get("file_size"),
                                                "Uploaded": metadata.get(
                                                    "upload_timestamp"
                                                ),
                                            }
                                        )

            st.divider()

            st.caption("🤖 AI Status")
            if st.button("🔄 Check Ollama"):
                try:
                    try:
                        import ollama

                        client = ollama.Client(
                            host=self.ollama_config.get("ollama_host")
                        )
                        models = client.list()
                        st.success(
                            f"✅ Ollama connected! {len(models.get('models', []))} models available"
                        )
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
        self._ensure_vault_and_rag()

        if not self.chat_history:
            self.chat_history = ChatHistory(self.vault)

        title_col, clear_col = st.columns([5, 1])
        with title_col:
            st.header("💬 Chat with Your Data")
            st.caption("All interactions are encrypted and stored locally")
        with clear_col:
            has_messages = bool(self.chat_history.get_messages())
            if st.button(
                "🗑️ Clear", use_container_width=True, disabled=not has_messages
            ):
                _clear_chat_dialog(self.chat_history)

        with st.expander("⚙️ Advanced"):
            if st.button("🔄 Rebuild RAG Index"):
                with st.spinner("Rebuilding RAG index from vault data..."):
                    self._init_vault()
                    self._initialize_rag()
                st.success("✅ RAG index rebuilt!")
                st.rerun()

        render_chat_history(self.chat_history)

        user_input = st.chat_input("Ask a question about your data...")
        if user_input:
            with st.chat_message("user", avatar=USER_AVATAR):
                st.markdown(user_input)

            with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
                response_text, sources = self._respond(user_input)
                render_sources(sources)

            self.chat_history.add_exchange(user_input, response_text, sources)
            st.rerun()

    def render_upload_page(self) -> None:
        """Render file upload page for data ingestion."""
        st.header("📂 Upload Data")
        st.caption("Encrypted file upload - data will be processed locally")

        uploaded_file = st.file_uploader(
            "Choose a file",
            type=["txt", "pdf", "csv", "json", "md"],
            label_visibility="collapsed",
        )

        if uploaded_file is not None:
            import tempfile
            import os

            file_info = {
                "filename": uploaded_file.name,
                "size": uploaded_file.size,
                "type": uploaded_file.type,
            }

            st.success(
                f"📄 {uploaded_file.name} ({uploaded_file.size} bytes) ready for upload"
            )
            st.info("Data will be encrypted and stored in your local vault")

            if st.button("🔒 Upload & Encrypt"):
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=f"_{uploaded_file.name}"
                ) as tmp_file:
                    tmp_file.write(uploaded_file.getvalue())
                    tmp_path = tmp_file.name

                try:
                    from src.data_ingestion.handlers import FileUploadHandler

                    handler = FileUploadHandler(self.vault, self.encryption_key)
                    result = handler.handle_upload(tmp_path)

                    if result["success"]:
                        st.success("✅ File uploaded successfully!")
                        st.json(
                            {
                                "Storage Key": result["storage_key"],
                                "File Type": result["file_type"],
                                "Text Length": result["text_length"],
                            }
                        )

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
        file_keys = [
            k
            for k in keys
            if not k.startswith("vault_")
            and k != "chat_history"
            and k != "ollama_config"
        ]

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
                        st.metric(
                            "Uploaded",
                            upload_timestamp.split("T")[0]
                            if "T" in upload_timestamp
                            else upload_timestamp,
                        )

                    st.divider()

                    if data.get("text_content"):
                        text_length = len(data["text_content"])
                        st.info(f"📄 Text content: {text_length} characters")

                        # Show preview if not too long
                        if text_length <= 2000:
                            with st.expander("👁️ View Text Preview"):
                                st.text(data["text_content"][:2000])
                                if text_length > 2000:
                                    st.text(
                                        f"... ({text_length - 2000} more characters)"
                                    )

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
            initial_sidebar_state="expanded",
        )

        hide_streamlit_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            </style>
        """
        st.markdown(hide_streamlit_style, unsafe_allow_html=True)

    def _render_login_gate(self) -> None:
        """Render registration/login screen; unlocks the vault key on success.

        Nothing else in the app renders until st.session_state['vault_key']
        is populated by a successful register() or authenticate() call.
        """
        st.set_page_config(
            page_title="Private AI Assistant - Sign In", layout="centered"
        )
        st.title("🔒 Private AI Assistant")

        auth = AuthManager()

        if not auth.is_registered():
            st.subheader("Create your vault password")
            st.caption(
                "This password protects your encrypted vault. It is never sent "
                "anywhere and cannot be recovered if lost - choose something you "
                "will remember."
            )
            with st.form("register_form"):
                password = st.text_input("New password", type="password")
                confirm = st.text_input("Confirm password", type="password")
                submitted = st.form_submit_button("Create vault")
            if submitted:
                if password != confirm:
                    st.error("Passwords do not match.")
                else:
                    try:
                        dek = auth.register(password)
                        st.session_state["vault_key"] = dek
                        st.rerun()
                    except AuthenticationError as e:
                        st.error(str(e))
        else:
            st.subheader("Enter your vault password")
            with st.form("login_form"):
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Unlock")
            if submitted:
                try:
                    dek = auth.authenticate(password)
                    st.session_state["vault_key"] = dek
                    st.rerun()
                except AuthenticationError as e:
                    st.error(str(e))

    def run(self) -> None:
        """Run the main interface."""
        if "vault_key" not in st.session_state:
            self._render_login_gate()
            return

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


def get_app() -> PersonalAIInterface:
    """Get (or create) the PersonalAIInterface for this browser session.

    Streamlit reruns this whole module on every interaction. Without
    caching the instance in session_state, a brand-new PersonalAIInterface
    was constructed on every rerun - re-initializing the vault and chat
    engine, and re-decrypting the full chat history from disk, on every
    single button press or chat message instead of once per session.
    """
    if "app" not in st.session_state:
        st.session_state["app"] = PersonalAIInterface()
    return st.session_state["app"]


if __name__ == "__main__":
    get_app().run()
