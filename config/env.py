import os
from pathlib import Path
from config.llm_providers import LLM_CONFIG, FIGMA_CONFIG 

class Config: #legge .env + definisce costanti globali
    PERSONAS_PATH = Path("data/personas_data/")
    PERSONA_GROUPS = os.getenv("PERSONA_GROUPS", "adult_18_30")
    NUM_PROFILES = int(os.getenv("NUM_PROFILES", "1"))
    VARIANT_KEYS = os.getenv("VARIANT_KEYS", "variant_a_grid,variant_b_list").split(",")
    NUM_SIMULATIONS = int(os.getenv("NUM_SIMULATIONS", "10"))
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", "800"))
    DEBUG = os.getenv("DEBUG", "true").lower() == "true"
    VARIANT_URLS = eval(os.getenv("VARIANT_URLS", "{'A': 'https://jo-u.github.io/design?layout=list', 'B': 'https://jo-u.github.io/design?layout=grid'}"))  
    FIGMA_FILE_KEY = os.getenv("FIGMA_FILE_KEY")  
    if not FIGMA_FILE_KEY:
        raise ValueError("FIGMA_FILE_KEY mancante in .env")

config = Config()
