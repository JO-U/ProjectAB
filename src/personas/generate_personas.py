import yaml
import pandas as pd
import json
import time
import argparse
import numpy as np
import sys
import os
import re
from pathlib import Path
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config.env import config as env_config

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from typing import List, Literal, Optional

load_dotenv()
API_KEY = os.getenv('OPENROUTER_API_KEY')

#PYDANTIC MODELS 
TARGET_CONFIGS = {}


class Bio(BaseModel):
    """Informazioni biografiche base"""
    nome: str = Field(min_length=1, max_length=50)
    eta: int = Field(ge=18, le=100)
    lavoro: str = Field(min_length=1, max_length=100)
    stato_familiare: str = Field(min_length=1, max_length=50)


class Navigation(BaseModel):
    """Comportamento di navigazione"""
    decision_style: Literal["cautious", "speedrunner", "balanced"] = "balanced"
    patience_level: float = Field(ge=0.0, le=1.0, default=0.5)
    tech_comfort: Literal["low", "medium", "high"] = "medium"
    task_oriented: bool = Field(default=True)  # True=focus on task, False=explores
    attention_to_detail: Literal["high", "medium", "low"] = "medium"


class GustiCulinari(BaseModel):
    gusti: List[str] = Field(max_length=5)  # es. ["mediterraneo", "piccante", "vegano"]
    piatti_preferiti: List[str] = Field(max_length=5)
    allergie: List[str] = Field(default_factory=list, max_length=3)


class PersonaSimple(BaseModel):
    persona_id: str = Field(min_length=5, max_length=50)
    bio: Bio
    navigation: Navigation
    gusti_culinari: GustiCulinari
    brief: str = Field(min_length=20, max_length=200, description="Riassunto compatto (~54 token)")
    target_group: str

    @model_validator(mode='after')
    def check_eta_range(self):
        if self.target_group in TARGET_CONFIGS:
            config = TARGET_CONFIGS[self.target_group]
            age_min = config['demographics']['age_min']
            age_max = config['demographics']['age_max']
            if not (age_min <= self.bio.eta <= age_max):
                raise ValueError(
                    f"Eta {self.bio.eta} fuori range [{age_min}-{age_max}] "
                    f"per target_group '{self.target_group}'"
                )
        return self


# LLM CALL
def llm_call_simple_with_behaviors(demographics_range: dict, target_group: str, 
                                   behaviors_sample: List[dict], max_retries: int = 3) -> List[dict]:
#Genera personas semplificate usando behavioral data da Kaggle
#Returns: List[dict] con bio, navigation, gusti_culinari    
    prompt = f"""TASK: Genera {len(behaviors_sample)} profili persona CONCISI (max 100 token/persona). Devono avere nomi, lavori e stili di navigazione realistici per il target specificato e diversi tra loro. NON RIPETERE.
CONTESTO:
- Età: [{demographics_range['age_min']}-{demographics_range['age_max']}] anni
- Target: {target_group}
- Paese: Italia
- Behavioral data reali da Kaggle (usa questi per inferire navigation behavior):
{json.dumps(behaviors_sample, indent=2)}

OUTPUT: SOLO JSON array (no markdown, no testo extra).

STRUTTURA:
[{{
  "bio": {{
    "nome": "Nome Cognome italiano",
    "eta": int,
    "lavoro": "Professione",
    "stato_familiare": "Single|Married|Parent|Divorced"
  }},
  "navigation": {{
    "decision_style": "cautious|speedrunner|balanced",
    "patience_level": float [0-1],
    "tech_comfort": "low|medium|high",
    "task_oriented": true|false,
    "attention_to_detail": "high|medium|low"
  }},
  "gusti_culinari": {{
    "gusti": ["stile1", "stile2"],
    "piatti_preferiti": ["piatto1", "piatto2"],
    "allergie": ["allergia1"] or []
  }},
  "brief": "Riassunto compatto di tutte le info sopra in ~54 token"
}}]

LINEE GUIDA:
1. Nome: Italiano realistico
2. Lavoro: Coerente con età
3. decision_style: cautious=pensa prima di cliccare, speedrunner=veloce, balanced=mix
4. patience_level: 0.0-0.3=bassa, 0.4-0.6=media, 0.7-1.0=alta
5. tech_comfort: Indipendente dall'età 
6. task_oriented: true=va dritto al compito, false=esplora per curiosità
7. attention_to_detail: high=nota tutti gli elementi, low=ignora dettagli meno prominenti
8. gusti: 2-3 stili culinari 
9. piatti_preferiti: 2-3 piatti concreti italiani
10. allergie: 0-2 allergie comuni (glutine, lattosio, frutta secca, ecc.)
11. brief: Riassunto compatto (~50 token) che integra bio + navigation + gusti in forma narrativa

ESEMPIO:
{{"bio":{{"nome":"Laura Bianchi","eta":35,"lavoro":"Insegnante","stato_familiare":"Parent"}},"navigation":{{"decision_style":"balanced","patience_level":0.7,"tech_comfort":"medium","task_oriented":true,"attention_to_detail":"high"}},"gusti_culinari":{{"gusti":["mediterraneo","salutare"],"piatti_preferiti":["pasta al pomodoro","insalata di farro"],"allergie":["frutta secca"]}},"brief":"Laura, 35 anni, insegnante con famiglia. Navigazione bilanciata, paziente, attenta ai dettagli. Preferisce cucina mediterranea salutare, ama pasta e farro. Allergia frutta secca."}}
]"""

    def repair_json_text(text: str) -> str:
        text = re.sub(r",\s*([}\]])", r"\1", text)
        text = re.sub(r"[\x00-\x1F]", "", text)
        return text

    for attempt in range(max_retries):
        try:
            payload = {
                "model": "tngtech/deepseek-r1t2-chimera:free",
                "messages": [{
                    "role": "user",
                    "content": prompt
                }],
                "temperature": 0.3,
                "max_tokens": 2000
            }
            
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json=payload,
                timeout=120
            )
            resp.raise_for_status()
            
            content = resp.json()['choices'][0]['message']['content']
            
            # Parse JSON
            if isinstance(content, list):
                parsed = content
            else:
                raw_content = content.strip() if isinstance(content, str) else str(content)
                
                # Remove markdown
                if '```json' in raw_content:
                    parts = raw_content.split('```json')
                    if len(parts) > 1:
                        raw_content = parts[1].split('```')[0].strip()
                elif '```' in raw_content:
                    parts = raw_content.split('```')
                    if len(parts) >= 2:
                        raw_content = parts[1].strip()
                
                # Find JSON array
                start = raw_content.find('[')
                end = raw_content.rfind(']') + 1
                if start != -1 and end > start:
                    raw_content = raw_content[start:end]

                try:
                    parsed = json.loads(raw_content)
                except json.JSONDecodeError:
                    parsed = json.loads(repair_json_text(raw_content))
            
            # Validate
            if isinstance(parsed, list) and len(parsed) >= len(behaviors_sample):
                return parsed[:len(behaviors_sample)]
            else:
                raise ValueError(
                    f"Parsed len {len(parsed) if isinstance(parsed, list) else 0} "
                    f"< expected {len(behaviors_sample)}"
                )
        
        except Exception as e:
            print(f"  ✗ Attempt {attempt + 1} failed: {str(e)[:100]}")
            if attempt < max_retries - 1:
                time.sleep(3 * (2 ** attempt))
            else:
                raise
    
    return []

# MAIN
if __name__ == "__main__":
    print("=" * 70)
    print("ProjectAB Simple Persona Generator")
    print("=" * 70)
    
    #Kaggle traffic data
    print("\n[1/4] Loading Kaggle traffic data...")
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_PATH = env_config.PERSONAS_PATH.parent / 'K_dataset.json'
    
    if not DATA_PATH.exists():
        print(f"✗ Kaggle dataset not found at {DATA_PATH}")
        sys.exit(1)
    
    traffic = pd.read_json(DATA_PATH)
    traffic = traffic.rename(columns={
        'Page Views': 'pageviews',
        'Session Duration': 'session_duration_sec',
        'Bounce Rate': 'bounce_rate',
        'Time on Page': 'time_on_page',
        'Previous Visits': 'previous_visits',
        'Conversion Rate': 'conversion_rate'
    })
    print(f" Loaded {len(traffic)} traffic records")
    
    #Target
    print("\n[2/4] Loading target configurations...")
    CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'target.yaml')
    
    if not os.path.exists(CONFIG_PATH):
        print(f"✗ target.yaml not found at {CONFIG_PATH}")
        sys.exit(1)
    
    with open(CONFIG_PATH, 'r') as f:
        config = yaml.safe_load(f)
    
    TARGET_CONFIGS = config.get('targets', {})
    print(f" Loaded {len(TARGET_CONFIGS)} target groups: {list(TARGET_CONFIGS.keys())}")
    
    parser = argparse.ArgumentParser(
        description="Generate simple personas (bio + navigation + gusti culinari)"
    )
    parser.add_argument(
        '--target',
        type=str,
        help="Specific target group to process (optional; if not specified, process all)"
    )
    parser.add_argument(
        '--num-personas',
        type=int,
        default=10,
        help="Number of personas per target (default: 10)"
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=5,
        help="LLM batch size (default: 5)"
    )
    parser.add_argument(
        '--output-format',
        choices=['json', 'yaml'],
        default='json',
        help="Output format (default: json)"
    )
    args = parser.parse_args()
    
    if args.target:
        targets_to_process = [args.target]
        print(f"\n[3/4] Processing single target: {args.target}")
    else:
        targets_to_process = list(TARGET_CONFIGS.keys())
        print(f"\n[3/4] Processing all {len(targets_to_process)} targets")
    
    # Main processing loop
    all_personas_generated = {}
    
    for target_name in targets_to_process:
        print(f"\n   {target_name}")
        
        if target_name not in TARGET_CONFIGS:
            print(f"     Target '{target_name}' not found in target.yaml")
            continue
        
        target_conf = TARGET_CONFIGS[target_name]
        ranges = target_conf.get('behavior_ranges', {})
        age_range = target_conf.get('demographics', {})
        
        #filter traffic data by behavior_ranges
        mask = pd.Series([True] * len(traffic))
        
        for metric, (min_val, max_val) in ranges.items():
            if metric in traffic.columns:
                mask &= traffic[metric].between(min_val, max_val)
        
        df_filt = traffic[mask]
        print(f"     Filtered: {len(df_filt)} / {len(traffic)} rows")
        
        if len(df_filt) == 0:
            print(f"     No matching records. Skipping.")
            all_personas_generated[target_name] = []
            continue
        
        #sample behaviors from Kaggle data
        num_personas = args.num_personas
        behaviors = df_filt.sample(min(num_personas, len(df_filt))).to_dict('records')
        print(f"     Sampling {len(behaviors)} behavioral patterns from Kaggle")
        
        # LLM generation (in batches)
        enriched = []
        llm_failures = 0
        
        for i in range(0, len(behaviors), args.batch_size):
            batch_size = min(args.batch_size, len(behaviors) - i)
            batch_behaviors = behaviors[i:i+batch_size]
            
            try:
                batch_result = llm_call_simple_with_behaviors(age_range, target_name, batch_behaviors)
                enriched.extend(batch_result)
                print(f"       {len(enriched)}/{len(behaviors)}")
            except Exception as e:
                print(f"       Batch {i//args.batch_size + 1}: {str(e)[:60]}...")
                llm_failures += batch_size
                # Fallback 
                for single_behavior in batch_behaviors:
                    try:
                        single_result = llm_call_simple_with_behaviors(age_range, target_name, [single_behavior])
                        if single_result:
                            enriched.extend(single_result)
                            print(f"       {len(enriched)}/{len(behaviors)} (single retry)")
                        else:
                            llm_failures += 1
                    except Exception as single_e:
                        print(f"       Single retry failed: {str(single_e)[:60]}...")
                        llm_failures += 1
            
            time.sleep(0.5)  #Rate limiting
        
        # Validate with Pydantic
        print(f"    • Validating with Pydantic...")
        personas = []
        validation_failures = 0
        
        for i, enr in enumerate(enriched):
            if not enr or not isinstance(enr, dict):
                print(f"      ✗ Persona {i}: Invalid data from LLM")
                validation_failures += 1
                continue
            
            try:
                persona_dict = {
                    'persona_id': f"PS_{target_name}_{uuid.uuid4().hex[:8]}",
                    'bio': enr.get('bio', {}),
                    'navigation': enr.get('navigation', {}),
                    'gusti_culinari': enr.get('gusti_culinari', {}),
                    'brief': enr.get('brief', ''),
                    'target_group': target_name
                }
                
                # Validate
                persona = PersonaSimple(**persona_dict)
                personas.append(persona.model_dump())
                
            except Exception as e:
                print(f"      ✗ Persona {i}: {str(e)[:80]}...")
                validation_failures += 1
                continue
        
        print(f"    ✓ {len(personas)} personas generated and validated")
        if llm_failures > 0:
            print(f"    ⚠ LLM failures: {llm_failures}")
        if validation_failures > 0:
            print(f"    ⚠ Validation failures: {validation_failures}")
        
        all_personas_generated[target_name] = personas
    
    # Save outputs
    print(f"\n[4/4] Saving outputs...")
    output_dir = env_config.PERSONAS_PATH / "simple"
    os.makedirs(output_dir, exist_ok=True)
    
    for target_name, personas in all_personas_generated.items():
        if not personas:
            print(f"  • {target_name}: 0 personas (skipped)")
            continue
        
        outfile_path = output_dir / f"{target_name}_simple_personas.{args.output_format}"
        
        if args.output_format == 'json':
            # Load existing personas if file exists
            existing_personas = []
            if outfile_path.exists():
                try:
                    with open(outfile_path, 'r', encoding='utf-8') as f:
                        existing_personas = json.load(f)
                    print(f"  • {target_name}: Loaded {len(existing_personas)} existing personas")
                except:
                    pass
            
            #avoiding duplicates by persona_id
            existing_ids = {p.get('persona_id') for p in existing_personas}
            new_personas = [p for p in personas if p.get('persona_id') not in existing_ids]
            all_personas = existing_personas + new_personas
            
            #Save
            with open(outfile_path, 'w', encoding='utf-8') as f:
                json.dump(all_personas, f, indent=2, default=str, ensure_ascii=False)
            
            print(f"   {target_name}: {len(new_personas)} new + {len(existing_personas)} existing = {len(all_personas)} total → {outfile_path}")
        
        elif args.output_format == 'yaml':
            #YAML logic
            existing_personas = []
            if outfile_path.exists():
                try:
                    with open(outfile_path, 'r', encoding='utf-8') as f:
                        existing_personas = yaml.safe_load(f) or []
                    print(f"  • {target_name}: Loaded {len(existing_personas)} existing personas")
                except:
                    pass
            
            existing_ids = {p.get('persona_id') for p in existing_personas}
            new_personas = [p for p in personas if p.get('persona_id') not in existing_ids]
            all_personas = existing_personas + new_personas
            
            with open(outfile_path, 'w', encoding='utf-8') as f:
                yaml.dump(all_personas, f, default_flow_style=False, allow_unicode=True)
            
            print(f"   {target_name}: {len(new_personas)} new + {len(existing_personas)} existing = {len(all_personas)} total  {outfile_path}")
    
    print("\n" + "=" * 70)
    print(" Generation complete!")
    print("=" * 70)
