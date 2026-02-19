import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter + Gemini
api_key = os.getenv('OPENROUTER_API_KEY')
if not api_key:
    raise ValueError("OPENROUTER_API_KEY mancante in .env")

LLM_CONFIG = {
    'provider': 'openrouter',
    'api_key': api_key,
    'base_url': 'https://openrouter.ai/api/v1/chat/completions',
    'model': os.getenv('LLM_MODEL', 'allenai/molmo-2-8b:free')  
}

FIGMA_CONFIG = {'figma_token': os.getenv('FIGMA_TOKEN')}
config = LLM_CONFIG
figma_config = FIGMA_CONFIG
