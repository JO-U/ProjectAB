# src/llm_client.py
import os
import requests
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

class LLMClient:
    def __init__(self, provider: str = "openrouter"):
        self.provider = provider
        self.api_key = os.getenv("OPENROUTER_API_KEY")  # O Ollama locale
        self.base_url = "https://openrouter.ai/api/v1/chat/completions" if provider == "openrouter" else "http://localhost:11434/api/generate"

    def generate(self, messages: List[Dict[str, str]], model: str = "meta-llama/llama-3.1-8b-instruct:free") -> str:
        if self.provider == "openrouter":
            return self._openrouter_generate(messages, model)
        elif self.provider == "ollama":
            return self._ollama_generate(messages[0]["content"])  # Semplificato per Ollama
        raise ValueError("Provider non supportato")

    def _openrouter_generate(self, messages: List[Dict[str, str]], model: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model,
            "messages": messages,
            "max_tokens": 500
        }
        resp = requests.post(self.base_url, headers=headers, json=data)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _ollama_generate(self, prompt: str) -> str:
        data = {"model": "llama3.1", "prompt": prompt, "stream": False}
        resp = requests.post(self.base_url, json=data)
        resp.raise_for_status()
        return resp.json()["response"]
