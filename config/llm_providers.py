import os
from dotenv import load_dotenv

load_dotenv()

LLM_CONFIG = {}
provider = os.getenv('LLMPROVIDER','openrouter')  #from .env

if provider == 'openrouter':
    api_key = os.getenv('OPENROUTER_API_KEY')
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY mancante in .env")
    LLM_CONFIG = {
        'provider': 'openrouter',
        'api_key': api_key,
        'base_url': 'https://openrouter.ai/api/v1/chat/completions',
        'model': os.getenv('LLM_MODEL', 'deepseek-r1t2-chimera:free')  
    }
elif provider == 'ollama':  #locale
    LLM_CONFIG = {
        'provider': 'ollama',
        'base_url': os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434') + '/api/generate',
        'model': os.getenv('OLLAMA_MODEL', 'llama3.2'),
        'api_key': None  
    }
elif provider == 'gemini':  #extra
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY mancante")
    LLM_CONFIG = {
        'provider': 'gemini',
        'base_url': f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}',
        'model': 'gemini-1.5-flash'
    }
else:
    raise ValueError(f"LLMPROVIDER={provider} non supportato. Usa: openrouter|ollama|gemini")

#print(f"LLM_CONFIG loaded: {provider} - {list(LLM_CONFIG.keys())}")  

FIGMA_CONFIG = {'figma_token': os.getenv('FIGMA_TOKEN')}
config = LLM_CONFIG
figma_config = FIGMA_CONFIG
