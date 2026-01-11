import os
import json
import requests
import hashlib
import re
import sys
from pathlib import Path
from typing import Dict, Any, List, TypedDict
from datetime import datetime, timedelta

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

provider: str = os.getenv("LLMPROVIDER", "openrouter")
if provider != "openrouter":
    raise ValueError("Solo OpenRouter")

api_key: str = os.getenv("OPENROUTER_API_KEY") or ""
if not api_key:
    raise ValueError("OPENROUTER_API_KEY (.env)")

LLM_CONFIG: Dict[str, Any] = {
    "provider": "openrouter",
    "api_key": api_key,
    "base_url": "https://openrouter.ai/api/v1/chat/completions",
    "model": os.getenv("LLM_MODEL", "xiaomi/mimo-v2-flash:free"),
}

print(f" {LLM_CONFIG['model']}")

#peths
TEST_DIR = Path("data/test")
CACHE_DIR = TEST_DIR / "cache"
PERSONAS_DIR = Path("data/personas_data")

for d in (TEST_DIR, CACHE_DIR):
    d.mkdir(exist_ok=True, parents=True)


#figma

FIGMA_TOKEN: str = os.getenv("FIGMA_TOKEN") or ""
FIGMA_FILE_KEY: str = os.getenv("FIGMA_FILE_KEY") or ""
FIGMA_NODE_IDS: List[str] = (
    os.getenv("FIGMA_NODE_IDS", "").split(",")
    if os.getenv("FIGMA_NODE_IDS")
    else []
)

if not FIGMA_TOKEN or not FIGMA_FILE_KEY:
    sys.exit("FIGMA_TOKEN + FIGMA_FILE_KEY richiesti")

class Persona(TypedDict):
    name: str
    target_group: str
    goal: str
    bio: Dict[str, Any]
    demographics: Dict[str, Any]
    behavior: Dict[str, Any]
    literacy: str

#LLM generate
def generate_completion(config: Dict[str, Any], prompt: str, temperature: float = 0.3, max_tokens: int = 800) -> str:
    resp = requests.post(
        config["base_url"],
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
            "X-Title": "FigmaAB",
        },
        json={
            "model": config["model"],
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

def extract_json_llm(content: str) -> Dict[str, Any]:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            #mandatory
            required = ["completion", "timetogoal", "overallscore", "nps"]
            if all(k in result for k in required):
                #optional fields
                result.setdefault("actions", "")
                result.setdefault("predictednext", "")
                return result
        except json.JSONDecodeError:
            pass
    
    #retry with fix prompt
    fix_prompt = (
        "Estrai SOLO il JSON della risposta precedente.\n"
        "Schema richiesto:\n"
        "{'completion':true/false,'timetogoal':5.0,'overallscore':8,'nps':7,'predictednext':'descrizione azione'}\n"
        "Rispondi SOLO con JSON, niente testo."
    )
    fixed = generate_completion(LLM_CONFIG, fix_prompt, temperature=0.1, max_tokens=200)
    match_fixed = re.search(r"\{.*\}", fixed, re.DOTALL)
    if not match_fixed:
        raise ValueError("Impossibile estrarre JSON dalla risposta LLM")
    return json.loads(match_fixed.group())

#FIGMA fetch
class FigmaAB:
    BASE_URL = "https://api.figma.com/v1"

    def __init__(self, token: str) -> None:
        self.headers = {"X-Figma-Token": token}

    def fetch_ab(self, file_key: str, node_ids: List[str]) -> Dict[str, Any]:
        h = hashlib.md5(f"{file_key}_{'_'.join(node_ids)}".encode()).hexdigest()
        cache_file = CACHE_DIR / f"figma_ab_{h}.json"

        if cache_file.exists():
            age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
            if age < timedelta(hours=1):
                print(f"Cache: {cache_file.name}")
                return json.loads(cache_file.read_text())

        params = {"ids": ",".join(node_ids)} if node_ids else None
        resp = requests.get(
            f"{self.BASE_URL}/files/{file_key}",
            headers=self.headers,
            params=params,
        )
        resp.raise_for_status()

        data = resp.json()
        cache_file.write_text(json.dumps(data, indent=2))
        print("cached")
        return data

#prsonas 
def load_personas(limit: int = 100) -> List[Persona]:
    personas: List[Persona] = []
    for path in PERSONAS_DIR.glob("*.json"):
        raw = json.loads(path.read_text())
        for item in raw[:limit]:
            personas.append({
                "name": item.get("bio", {}).get("occupation", "Anon"),
                "target_group": item.get("target_group", "unknown"),
                "goal": item.get("goal", ""),
                "bio": item.get("bio", {}),
                "demographics": item.get("demographics", {}),
                "behavior": item.get("behavior", {}),
                "literacy": item.get("literacy", "medium"),
            })
    print(f"Personas caricate: {len(personas)}")
    return personas

#A/B VARIANTS
def extract_ab_variants(figma_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    variants: Dict[str, Dict[str, Any]] = {}

    def scan_node(node: Dict[str, Any]) -> None:
        if node.get("type") == "CANVAS":
            name_lower = node.get("name", "").lower()
            if "variant a" in name_lower:
                label = "A"
            elif "variant b" in name_lower:
                label = "B"
            else:
                label = None

            if label:
                titles = []
                descriptions = []

                def extract_texts(n: Dict[str, Any]):
                    if n.get("type") == "TEXT":
                        text = n.get("characters", "")
                        if text.strip():
                            titles.append(text)
                    if n.get("description"):
                        descriptions.append(n.get("description"))
                    for c in n.get("children", []):
                        extract_texts(c)

                extract_texts(node)

                variants[label] = {
                    "name": node.get("name", ""),
                    "layout": "UNKNOWN",
                    "titles": titles[:15],
                    "descriptions": descriptions[:5]
                }

        #scan children
        for c in node.get("children", []):
            scan_node(c)

    scan_node(figma_data.get("document", {}))
    return variants

#SIMULATION

AB_PROMPT = """
SIMULAZIONE UTENTE SU INTERFACCIA A/B

Variante {var}
Elementi visibili: {titles}
{descriptions_text}

PROFILO UTENTE:
Nome: {name}
Occupazione: {occupation}
Età: {eta} anni
Allergie/Preferenze: {allergies}
Esperienza: {literacy} (bassa/media/alta)
Comportamento passato: {pageviews} pagine, {session_duration_sec}s di sessione, bounce rate {bounce_rate}%

GOAL PRIMARIO: {goal}

TASK:
1. Valuta se l'interfaccia permette di raggiungere il goal
2. Stima tempo per completare (secondi)
3. Valuta usabilità (1-10)
4. Calcola probabilità di raccomandazione (NPS: 0-10)
5. Descrivi azioni compiute
6. Predici azione successiva dell'utente

RISPOSTA (SOLO JSON):
{{"completion":true/false,"timetogoal":float,"overallscore":int,"nps":int, "actions": "string","predictednext":"azione descrittiva"}}
"""

def simulate_ab(persona: Persona, variant: Dict[str, Any], label: str) -> Dict[str, Any]:
    bio = persona["bio"]
    beh = persona["behavior"]
    dem = persona["demographics"]

    descriptions_text = ""
    if variant.get("descriptions"):
        descriptions_text = "Descrizioni: " + " | ".join(variant.get("descriptions", []))

    prompt = AB_PROMPT.format(
        var=label,
        titles=" | ".join(variant.get("titles", [])[:20]),
        descriptions_text=descriptions_text,
        name=persona["name"],
        occupation=bio.get("occupation", "?"),
        eta=dem.get("age", dem.get("eta", 30)),
        allergies=", ".join(bio.get("allergies", [])) or "Nessuna",
        literacy=persona["literacy"],
        goal=persona["goal"],
        pageviews=beh.get("pageviews", 0),
        session_duration_sec=beh.get("session_duration_sec", 0),
        bounce_rate=beh.get("bounce_rate", 0),
    )

    raw = generate_completion(LLM_CONFIG, prompt, temperature=0.3, max_tokens=600)
    try:
        metrics = extract_json_llm(raw)
    except Exception as e:
        print(f"Errore estrazione JSON per {persona['name']}: {e}")
        metrics = {
            "completion": False,
            "timetogoal": 999,
            "overallscore": 0,
            "nps": 0,
            "actions": "ERRORE",
            "predictednext": "ERRORE"
        }
    
    metrics.update({
        "persona": persona["name"],
        "group": persona["target_group"],
        "variant": label,
        "var_name": variant.get("name", ""),
        "llm_raw": raw[:80]
    })
    return metrics

# MAIN
def main() -> None:
    fetcher = FigmaAB(FIGMA_TOKEN)
    figma_raw = fetcher.fetch_ab(FIGMA_FILE_KEY, FIGMA_NODE_IDS)
    ab_variants = extract_ab_variants(figma_raw)

    if "A" not in ab_variants or "B" not in ab_variants:
        print(" Non sono state trovate entrambe le varianti A e B")
        return

    personas = load_personas()
    if not personas:
        print(" Nessuna persona caricata")
        return

    mid = len(personas) // 2
    group_a_personas = personas[:mid]
    group_b_personas = personas[mid:]

    all_results: List[Dict[str, Any]] = []

    # Variant A Grid
    for persona in group_a_personas:
        metrics = simulate_ab(persona, ab_variants["A"], "A")
        all_results.append(metrics)

    # Variant B List
    for persona in group_b_personas:
        metrics = simulate_ab(persona, ab_variants["B"], "B")
        all_results.append(metrics)

    df = pd.DataFrame(all_results)
    if df.empty:
        print("Nessun risultato A/B generato")
        return

    print(df.groupby(["group", "variant"])[["nps", "overallscore"]].mean().round(2))

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    df.to_csv(TEST_DIR / f"ab_test_{ts}.csv", index=False)
    df.to_json(TEST_DIR / f"ab_test_{ts}.json", indent=2)
    print(f" Output salvati in {TEST_DIR}")

if __name__ == "__main__":
    main()
