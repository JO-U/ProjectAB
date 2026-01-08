import yaml
import pandas as pd
import json
import time
import argparse
import numpy as np

import sys
import os
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # A root
from config.env import config as env_config

import requests
from dotenv import load_dotenv
from faker import Faker
from pydantic import BaseModel, Field, model_validator
from typing import Dict, Any, List

load_dotenv()
fake = Faker('it_IT')
API_KEY = os.getenv('OPENROUTER_API_KEY')

# PYDANTIC MODELS
TARGET_CONFIGS = {} 

class Demographics(BaseModel):
    eta: int = Field(ge=0, le=100)
    genere: str = Field(pattern="^(M|F|O)$")
    paese: str = Field(default="Italia", pattern="^Italia$")

class Bio(BaseModel):
    occupation: str = Field(min_length=1)
    family_status: str = Field(min_length=1)
    allergies: List[str] = Field(default_factory=list)
    tech_comfort: str = Field(pattern="^(high|medium|low)$")

class Persona(BaseModel):
    behavior: Dict[str, Any]
    demographics: Demographics
    bio: Bio
    goal: str = Field(min_length=5, max_length=200)
    literacy: str = Field(pattern="^(high|medium|low)$")
    target_group: str

    @model_validator(mode='after')
    def check_tech_comfort(self):
        if self.bio.tech_comfort != self.literacy:
            raise ValueError(f"tech_comfort '{self.bio.tech_comfort}' != literacy '{self.literacy}'")
        return self

    @model_validator(mode='after')
    def check_eta_range(self):
        target_group = self.target_group
        if target_group and target_group in TARGET_CONFIGS:
            config = TARGET_CONFIGS[target_group]['demographics']
            age_min, age_max = config['age_min'], config['age_max']
            if not (age_min <= self.demographics.eta <= age_max):
                raise ValueError(f"Eta {self.demographics.eta} fuori [{age_min}-{age_max}] per {target_group}")
        return self

    @model_validator(mode='after')
    def check_behavior_ranges(self):
        target_group = self.target_group
        if target_group and target_group in TARGET_CONFIGS:
            ranges = TARGET_CONFIGS[target_group]['behavior_ranges']
            for key, (min_val, max_val) in ranges.items():
                if key in self.behavior and not (min_val <= self.behavior[key] <= max_val):
                    raise ValueError(f"{key} {self.behavior[key]} fuori [{min_val}-{max_val}] per {target_group}")
        return self

#LLM CALL
def llm_call(literacy, behaviors_sample, demographics_range, max_retries=3):
    for attempt in range(max_retries):
        try:
            payload = {
                "model": "tngtech/deepseek-r1t2-chimera:free",
                "messages": [{
                    "role": "user",
                    "content": f"""TASK: Genera {len(behaviors_sample)} profili realistici
                    
                    Context:
                    - Literacy: {literacy} (high=tech-savvy, medium=average, low=basic)
                    - Età: tra {demographics_range['age_min']} e {demographics_range['age_max']} anni
                    - Genere: M/F/O (usa tutti in modo bilanciato)| Paese: Italia
                    - Behaviors Kaggle: {json.dumps(behaviors_sample, indent=2)}
                    
                    OUTPUT SOLO JSON array:
                    [{{"demographics":{{"eta":int, "genere":"str","paese":"str"}},"bio":{{"occupation":"str","family_status":"str","allergies":[],"tech_comfort":"{literacy}"}},"goal":"1 frase obiettivo sito ricette"}}]""" }],
                "temperature": 0.6,  
                "max_tokens": 2500
            }
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions",
                               headers={"Authorization": f"Bearer {API_KEY}"},
                               json=payload, timeout=180)
            resp.raise_for_status()
            
            content = resp.json()['choices'][0]['message']['content']
            
            if isinstance(content, list):
                parsed = content
            else:
                raw_content = content.strip() if isinstance(content, str) else str(content)
                if '```json' in raw_content:
                    parts = raw_content.split('```json')
                    if len(parts)>1:
                        raw_content=parts[1].split('```')[0].strip()
                start = raw_content.find('[')
                end = raw_content.rfind(']') + 1
                if start != -1 and end > start:
                    raw_content = raw_content[start:end]
                parsed = json.loads(raw_content)
            
            if isinstance(parsed, list) and len(parsed) == len(behaviors_sample):
                return parsed
            else:
                raise ValueError(f"Parsed len {len(parsed)} != batch {len(behaviors_sample)}")
                
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {str(e)[:80]}")
            if attempt < max_retries-1:
                time.sleep(5*(2**attempt))
            else:
                raise

#MAIN
if __name__ == "__main__":
    print("Loading Kaggle traffic...")
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_PATH = env_config.PERSONAS_PATH.parent / 'K_dataset.json'
    traffic = pd.read_json(DATA_PATH)
    traffic = traffic.rename(columns={
        'Page Views': 'pageviews',
        'Session Duration': 'session_duration_sec',
        'Bounce Rate': 'bounce_rate'
    })

    print("Loading config...")
    CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'target.yaml')
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    
    TARGET_CONFIGS = config['targets']

    parser = argparse.ArgumentParser()
    parser.add_argument('--target')
    args = parser.parse_args()
    
    if args.target:
        targets_to_process = [args.target]
    else:
        targets_to_process = list(TARGET_CONFIGS.keys())
        print(f"Processing all targets: {targets_to_process}")
    
    for target_name in targets_to_process:
        print(f"\n--- Processing {target_name} ---")

        if target_name not in TARGET_CONFIGS: 
            print(f"Target '{target_name}' non presente in target.yaml")
            continue
        target_conf = TARGET_CONFIGS[target_name]
        ranges= target_conf['behavior_ranges']
        age_range= target_conf['demographics']

        TARGET_CONFIGS[target_name] = target_conf  # Cache
        
        #filter for target
        ranges = target_conf['behavior_ranges']
        mask = pd.Series([True] * len(traffic))
        if 'bounce_rate' in ranges:
            mask &= traffic['bounce_rate'].between(*ranges['bounce_rate'])
        if 'pageviews' in ranges:
            mask &= traffic['pageviews'].between(*ranges['pageviews'])
        if 'session_duration_sec' in ranges:
            mask &= traffic['session_duration_sec'].between(*ranges['session_duration_sec'])
        
        df_filt = traffic[mask]
        print(f"Filtrato: {len(df_filt)} / {len(traffic)} righe")
        
        if len(df_filt) == 0:
            os.makedirs('data/personas_data', exist_ok=True)
            json.dump([], open(f"data/personas_data/{target_name}_personas.json", 'w'), indent=2)
            print(f"0 personas salvate: data/personas_data/{target_name}_personas.json")
            continue
        
        num_personas = env_config.NUM_PROFILES
        behaviors = df_filt.sample(min(num_personas, len(df_filt))).to_dict('records')
        
        probs = target_conf['literacy_distribution']
        literacies = np.random.choice(['high','medium','low'], size=len(behaviors), 
                             p=[probs['high'], probs['medium'], probs['low']])

        
        print("LLM batch enrich...")
        enriched_bio = []
        age_range = target_conf['demographics']
        for i in range(0, len(behaviors), 1):
            batch = behaviors[i:i+1]
            lit = literacies[i]
            try:
                batch_enrich = llm_call(lit, batch, age_range)
                enriched_bio.extend(batch_enrich)
                print(f"   ✓ {len(enriched_bio)}/{len(behaviors)}")
            except Exception as e:
                print(f"   ✗ Error in batch {i}: {e}")
                enriched_bio.extend([{}] * len(batch))
        
        # VALIDAZIONE PYDANTIC
        personas = []
        numeric_keys = ['pageviews', 'bounce_rate', 'session_duration_sec', 'Time on Page', 'Previous Visits', 'Conversion Rate']
        for i, (bh, lit) in enumerate(zip(behaviors, literacies)):
            bio_goal = enriched_bio[i]
            if not bio_goal or not isinstance(bio_goal, dict) or 'demographics' not in bio_goal:
                print(f"Skipping invalid bio_goal for persona {i}")
                continue
            
            try:
                behavior = {k: float(v) if k in numeric_keys and isinstance(v, (int, float, str)) else v 
                           for k, v in bh.items()}
                persona_dict = {
                    'behavior': behavior,
                    'demographics': bio_goal['demographics'],
                    'bio': bio_goal['bio'],
                    'goal': bio_goal['goal'],
                    'literacy': lit,
                    'target_group': target_name
                }
                persona = Persona(**persona_dict)  
                personas.append(persona.model_dump())
                print(f" Persona {i} ok")
            except Exception as e:
                print(f"  Persona {i} scartata: {e}")
                continue
        
        os.makedirs(env_config.PERSONAS_PATH, exist_ok=True)
        outfile_path = env_config.PERSONAS_PATH / f"{target_name}_personas.json"

if outfile_path.exists():
    with open(outfile_path, 'r') as f:
        all_personas = json.load(f)
    print(f"Caricati {len(all_personas)} esistenti")
else:
    all_personas = []

all_personas.extend(personas)
with open(outfile_path, 'w') as f:
    json.dump(all_personas, f, indent=2, default=str)
print(f"{len(all_personas)} totali: {outfile_path}")
