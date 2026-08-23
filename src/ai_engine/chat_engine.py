from typing import Optional, List, Dict, Any
from pathlib import Path

from .ollama_client import OllamaClient, OllamaClientError
from .embeddings import EmbeddingsGenerator, EmbeddingsError
from .rag_engine import RAGEngine, RAGEngineError

from src.data_vault import DataVault, DataVaultError
from src.security.encryption import AEADEncryption


class ChatEngineError(Exception):
    """Custom exception for chat engine operations."""
    pass


class ChatEngine:
    """Chat engine that queries encrypted data vault using RAG and Llama models."""
    
    DEFAULT_MODEL = "llama3.2:latest"
    
    def __init__(
        self,
        ollama_client: Optional[OllamaClient] = None,
        vault_path: Optional[str] = None,
        encryption_key: Optional[bytes] = None
    ):
        self.ollama_client = ollama_client or OllamaClient()
        
        self.vault = DataVault(vault_path=vault_path, encryption_key=encryption_key)
        self.rag_engine: Optional[RAGEngine] = None
        self._encryption_key = encryption_key
        self._model_loaded = False
    
    def _ensure_model(self, lazy: bool = False) -> None:
        """Ensure the chat model is available."""
        if lazy and self._model_loaded:
            return
        if not self.ollama_client.model_exists(self.DEFAULT_MODEL):
            if lazy:
                # Don't pull in lazy mode, just warn
                self._model_loaded = True
                return
            try:
                self.ollama_client.pull_model(self.DEFAULT_MODEL)
            except OllamaClientError as e:
                if not lazy:
                    raise ChatEngineError(
                        f"Cannot chat without {self.DEFAULT_MODEL} model. "
                        f"Please pull it manually: ollama pull {self.DEFAULT_MODEL}"
                    ) from e
        self._model_loaded = True
    
    def _load_vault_data_for_rag(self) -> List[str]:
        """Load and prepare all vault data for RAG indexing."""
        texts = []
        for key in self.vault.list_keys():
            try:
                data = self.vault.retrieve_data(key)
                if data:
                    text = self._data_to_text(data)
                    texts.append(text)
            except Exception:
                continue
        return texts
    
    def _data_to_text(self, data: Dict[str, Any]) -> str:
        """Convert data dictionary to text for RAG."""
        text_parts = []
        for key, value in data.items():
            if key == "text_content" and isinstance(value, str):
                text_parts.append(value)
            elif key == "metadata" and isinstance(value, dict):
                for meta_key, meta_value in value.items():
                    text_parts.append(f"{meta_key}: {meta_value}")
            elif isinstance(value, dict):
                text_parts.append(self._data_to_text(value))
            elif isinstance(value, list):
                text_parts.append(f"{key}: {', '.join(str(x) for x in value)}")
            else:
                text_parts.append(f"{key}: {value}")
        return " ".join(filter(None, text_parts))
    
    def initialize_rag(self) -> None:
        """Initialize RAG engine with vault data."""
        if self.rag_engine is None:
            # Use lazy RAGEngine initialization
            self.rag_engine = RAGEngine()
            self.rag_engine._initialized = True  # Skip lazy init checks
            texts = self._load_vault_data_for_rag()
            if texts:
                self.rag_engine.add_documents(texts)
    
    def query_vault(self, query: str, use_rag: bool = True) -> Dict[str, Any]:
        """Query the encrypted vault using chat model."""
        if not self.ollama_client.model_exists(self.DEFAULT_MODEL):
            raise ChatEngineError(
                f"Model '{self.DEFAULT_MODEL}' not available."
            )
        
        if use_rag:
            if self.rag_engine is None:
                self.initialize_rag()
            
            context = self.rag_engine.get_context_for_query(query)
            
            if context:
                prompt = (
                    f"Context from your data:\n{context}\n\n"
                    f"Question: {query}\n\n"
                    f"Answer based on the context above. "
                    f"If the answer is not in the context, say 'I cannot find this information in your vault.'"
                )
            else:
                prompt = (
                    f"No data found in vault matching your query.\n\n"
                    f"Question: {query}\n\n"
                    f"Please provide a general response or ask for data to be added to your vault."
                )
        else:
            prompt = query
        
        try:
            response = self.ollama_client.chat(
                messages=[
                    {"role": "system", "content": "You are a helpful personal assistant with access to your local encrypted data. Answer based on the provided context."},
                    {"role": "user", "content": prompt}
                ]
            )
            
            return {
                "query": query,
                "response": response.get("message", {}).get("content", ""),
                "context_used": context if use_rag else None
            }
        except OllamaClientError as e:
            raise ChatEngineError(f"Chat generation failed: {str(e)}")
    
    def add_data_to_vault(self, key: str, data: Dict[str, Any]) -> bool:
        """Add data to encrypted vault."""
        try:
            self.vault.store_data(key, data)
            
            if self.rag_engine:
                text = self._data_to_text(data)
                self.rag_engine.add_document(text)
            
            return True
        except DataVaultError as e:
            raise ChatEngineError(f"Failed to store data: {str(e)}")
    
    def clear_vault(self) -> bool:
        """Clear all data from vault and RAG index."""
        try:
            self.vault.clear()
            if self.rag_engine:
                self.rag_engine.clear()
            return True
        except Exception as e:
            raise ChatEngineError(f"Failed to clear vault: {str(e)}")
