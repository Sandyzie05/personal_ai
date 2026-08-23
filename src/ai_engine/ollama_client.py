"""Ollama API client for local LLM interactions."""

import ollama
from typing import List, Dict, Any, Optional
from datetime import datetime


class OllamaClientError(Exception):
    """Custom exception for Ollama client errors."""
    pass


class OllamaClient:
    """Client for interacting with Ollama local LLMs."""
    
    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "llama3.2:latest",
        temperature: float = 0.7,
        max_tokens: int = 2048
    ):
        self.host = host
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = ollama.Client(host=host)
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False
    ) -> Dict[str, Any]:
        """Send a chat message to Ollama."""
        response = self.client.chat(
            model=self.model,
            messages=messages,
            stream=stream,
            options={
                "temperature": self.temperature,
                "num_predict": self.max_tokens
            }
        )
        return response
    
    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for a text string."""
        response = self.client.embeddings(
            model="nomic-embed-text",
            prompt=text
        )
        return response["embedding"]
    
    def list_models(self) -> List[str]:
        """List available models in Ollama."""
        try:
            response = self.client.list()
            # Handle Model objects from ollama library
            if hasattr(response, 'models'):
                return [model.model for model in response.models if hasattr(model, 'model')]
            elif isinstance(response, dict):
                return [model.get("name", "") for model in response.get("models", [])]
            return []
        except Exception as e:
            print(f"Error listing models: {e}")
            return []
    
    def check_connection(self) -> bool:
        """Check if Ollama is running and accessible."""
        try:
            self.client.list()
            return True
        except Exception:
            return False
    
    def model_exists(self, model_name: str) -> bool:
        """Check if a model exists in Ollama."""
        try:
            models = self.list_models()
            return model_name in models
        except Exception:
            return False
    
    def pull_model(self, model_name: str) -> None:
        """Pull a model from Ollama."""
        try:
            self.client.pull(model_name)
        except Exception as e:
            raise OllamaClientError(f"Failed to pull model {model_name}: {str(e)}")
