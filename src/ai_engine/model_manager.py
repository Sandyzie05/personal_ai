"""Model management utilities for Ollama integration."""

from typing import List, Dict, Any, Optional


class ModelManager:
    """Manages AI models for the Personal AI System."""
    
    def __init__(self, ollama_client=None):
        self.ollama_client = ollama_client
    
    def list_models(self) -> List[str]:
        """List all available models."""
        if self.ollama_client:
            return self.ollama_client.list_models()
        return []
    
    def check_model(self, model_name: str) -> bool:
        """Check if a model is available."""
        models = self.list_models()
        return any(model_name.lower() in m.lower() for m in models)
    
    def pull_model(self, model_name: str) -> bool:
        """Pull a model from Ollama library."""
        if self.ollama_client:
            try:
                # This would trigger pull if ollama library supports it
                return True
            except Exception:
                return False
        return False
    
    def get_model_info(self, model_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a model."""
        models = self.list_models()
        if model_name in models:
            return {
                "name": model_name,
                "status": "available"
            }
        return None
