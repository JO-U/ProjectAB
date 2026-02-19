import os
import sys
import asyncio
import base64
import io
import json
import random
import time
import hashlib
import re
import difflib
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from PIL import Image, ImageDraw
import requests

# Google Gemini
from google import genai
from google.genai import types

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    print("[WARN] pytesseract non installato - OCR verifiche disabilitato")

load_dotenv()

# API Configuration
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-1.5-flash")

if not GOOGLE_API_KEY:
    sys.exit("ERROR: GOOGLE_API_KEY mancante (.env)")

try:
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    print(f"[INFO] Using Google Gemini API (model: {LLM_MODEL})")
except Exception as e:
    sys.exit(f"[ERROR] Gemini initialization failed: {e}")

DISABLE_LLM = False
DISABLE_LLM_ACTION = os.getenv("DISABLE_LLM_ACTION", "read").strip().lower()
DISABLE_RECOVERY = os.getenv("DISABLE_RECOVERY", "0").strip().lower() in ("1", "true", "yes")
PROTOTYPE_URL_A = os.getenv("PROTOTYPE_URL_A")
PROTOTYPE_URL_B = os.getenv("PROTOTYPE_URL_B")
print(f"[INFO] LLM {'DISABILITATO' if DISABLE_LLM else 'ABILITATO'} (DISABLE_LLM={os.getenv('DISABLE_LLM', '')})")

TASK_1 = os.getenv("TASK_1", "")
TASK_2 = os.getenv("TASK_2", "")
TASK_3 = os.getenv("TASK_3", "")

PERSONAS_DIR = Path("data/personas_data")
FRAMES_LOCAL_DIR_A = Path("data/test/figma_cache/frames_local_a")
FRAMES_LOCAL_DIR_B = Path("data/test/figma_cache/frames_local_b")
FRAMES_LOCAL_DIR = Path("data/test/figma_cache/frames_local")  # Fallback default
CACHE_DIR = Path("data/test/cache")
LOGS_DIR = Path("data/test/logs")
CACHE_DIR.mkdir(exist_ok=True, parents=True)
LOGS_DIR.mkdir(exist_ok=True, parents=True)

MAX_ACTIONS = 25
MAX_TASK_RETRIES = 2  # Numero massimo di tentativi per task fallito

MIN_ELEMENT_SIZE = 10  
MIN_SCROLL_DISTANCE = 400
SCREENSHOT_SCALE = 0.1  

ACTION_CACHE = {}
OCR_CACHE = {}
LAST_LLM_CALL_TS = 0.0
LLM_MIN_INTERVAL_SEC = float(os.getenv("LLM_MIN_INTERVAL_SEC", "15"))
LAST_CACHE_KEY = None  # Track last used cache key for invalidation

def hash_screenshot(img_b64: str, task: str) -> str:
    """Crea hash per caching azione"""
    combined = f"{img_b64[:100]}{task}" 
    return hashlib.md5(combined.encode()).hexdigest()


def load_last_incomplete_session() -> Optional[Dict]:
    """Carica l'ultima sessione incompleta dai log"""
    if not LOGS_DIR.exists():
        return None
    log_files = [f for f in LOGS_DIR.glob("*.json") if not f.name.startswith("manual_session")]
    if not log_files:
        return None
    #ordina per data di modifica
    log_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    #cerca prima sessione incompleta
    for log_file in log_files:
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                session = json.load(f)
                if not session.get('all_tasks_completed', False):
                    persona_id = session.get('persona_id', 'X')
                    prototype = session.get('prototype', 'X')
                    custom_name = log_file.name.replace('.json','')
                    print(f"[INFO] Trovata sessione incompleta: {log_file.name} (Persona: {persona_id}, Variante: {custom_name})")
                    return session
        except Exception as e:
            print(f"[WARN] Errore leggendo {log_file.name}: {e}")
            continue
    return None


def load_last_completed_session_for_continuation() -> Optional[Dict]:
    print("[DEBUG] Controllo sessioni completate per continuation...")
    
    if not LOGS_DIR.exists():
        print("[DEBUG] Directory logs non esiste")
        return None
    
    log_files = list(LOGS_DIR.glob("session_*.json"))
    if not log_files:
        print("[DEBUG] Nessun file di log trovato")
        return None
    
    print(f"[DEBUG] Trovati {len(log_files)} file di log")
    
    log_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    
    #cerca l'ultima sessione completata
    for log_file in log_files:
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                session = json.load(f)
            completed = session.get('all_tasks_completed', False)
            prototype = session.get('prototype', 'X')
            persona_id = session.get('persona_id', 'X')
            variant = session.get('variant', '')
            session_number = session.get('session_number', '')
            custom_name = f"{session_number}{prototype}"
            print(f"[DEBUG] {log_file.name}: prototype={prototype}, persona_id={persona_id}, completed={completed}, variante={custom_name}")
            if completed:
                other_prototype = "B" if prototype == "A" else "A"
                print(f"[DEBUG] Sessione completata trovata: {prototype} -> cerco se esiste già {other_prototype}")
                found_other = False
                for other_log in log_files:
                    try:
                        with open(other_log, 'r', encoding='utf-8') as f2:
                            other_session = json.load(f2)
                            if (other_session.get('persona_id') == persona_id and 
                                other_session.get('prototype') == other_prototype):
                                print(f"[DEBUG] Già esiste sessione per {other_prototype} con questa persona - skip")
                                found_other = True
                                break
                    except:
                        continue
                if not found_other:
                    print(f"[INFO] Trovata sessione completata prototipo {prototype}, continuo con {other_prototype} (Variante: {custom_name})")
                    print(f"[INFO] Persona: {session.get('persona')} (ID: {persona_id})")
                    return session
        except Exception as e:
            print(f"[WARN] Errore leggendo {log_file.name}: {e}")
            continue
    print("[DEBUG] Nessuna sessione completata da continuare")
    return None


def load_last_manual_frame() -> Optional[str]:

    manual_files = sorted(LOGS_DIR.glob("manual_session_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    
    print(f"[DEBUG] load_last_manual_frame: trovati {len(manual_files)} file manuali")
    if manual_files:
        print(f"[DEBUG] File log più recente: {manual_files[0].name}")
    if not manual_files:
        print(f"[DEBUG] Nessun file manuale trovato")
        return None
    try:
        with open(manual_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        actions = data.get("actions", [])
        print(f"[DEBUG] Azioni nel log manuale: {len(actions)}")
        for a in reversed(actions):
            last_frame = a.get("frame_after") or a.get("frame")
            print(f"[DEBUG] Azione: {a.get('action_type')} - frame_after={a.get('frame_after')}, frame={a.get('frame')} -> last_frame={last_frame}")
            if last_frame:
                print(f"[DEBUG] Ritorno frame da log manuale: {last_frame}")
                return last_frame
    except Exception as e:
        print(f"[DEBUG] Errore in load_last_manual_frame: {e}")
        return None
    print(f"[DEBUG] Nessun frame trovato nel log manuale")
    return None


def get_actions_summary(actions: List[Dict], max_actions: int = 5) -> str:
    #Crea riassunto delle azioni già eseguite
    if not actions:
        return "Nessuna azione precedente."
    
    summary_lines = []
    recent_actions = actions[-max_actions:] if len(actions) > max_actions else actions
    
    for action in recent_actions:
        action_num = action.get('action_number', '?')
        action_type = action.get('action_type', 'unknown')
        target = action.get('target_text', 'N/A')
        success = "✓" if action.get('success', False) else "✗"
        frame = action.get('frame_after', action.get('frame', 'unknown'))
        
        summary_lines.append(f"{action_num}. {action_type.upper()} '{target}' [{success}] → frame:{frame}")
    
    if len(actions) > max_actions:
        summary_lines.insert(0, f"... ({len(actions) - max_actions} azioni precedenti omesse) ...")
    
    return "\n".join(summary_lines)


async def get_viewport_scroll_top(page) -> Optional[int]:
    try:
        scroll_top = await page.evaluate(
            """() => {
                const viewport = document.querySelector('[data-testid="viewport-container"]');
                if (!viewport) return null;
                return Math.floor(viewport.scrollTop || 0);
            }"""
        )
        return int(scroll_top) if scroll_top is not None else None
    except Exception:
        return None


def test_api_connection() -> bool:
    try:
        print("[INFO] Test connessione Gemini API...")
        # Simple test: list models
        models = list(gemini_client.models.list())
        print(f"[OK] Gemini API connessa - Models disponibili: {len(models)}")
        return True
    except Exception as e:
        print(f"[ERROR] Impossibile connettersi a Gemini: {e}")
        return False

# PERSONA
def get_personas_used_in_logs():
    used_personas = set()
    
    if not LOGS_DIR.exists():
        return used_personas
    
    for log_file in LOGS_DIR.glob("*.json"):
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                log_data = json.load(f)
                # Cerca persona_id a livello root
                if "persona_id" in log_data:
                    used_personas.add(log_data["persona_id"])
        except Exception as e:
            print(f"[WARN] Errore leggendo {log_file.name}: {e}")
    return used_personas

def load_next_persona():
    if not PERSONAS_DIR.exists():
        print(f"[WARN] {PERSONAS_DIR} non trovata")
        return None
    
    all_personas = []
    json_files = sorted(PERSONAS_DIR.glob("*.json"))  # Ordine alfabetico
    
    print(f"[DEBUG] File personas trovati: {[f.name for f in json_files]}")
    
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                personas = json.load(f)
                all_personas.extend(personas)
                print(f"[DEBUG] Caricate {len(personas)} personas da {json_file.name}")
        except Exception as e:
            print(f"[WARN] Errore caricando {json_file.name}: {e}")
    
    if not all_personas:
        print(f"[ERROR] Nessuna persona trovata in {PERSONAS_DIR}")
        return None
    
    persona_counts = {}
    if LOGS_DIR.exists():
        for log_file in LOGS_DIR.glob("*.json"):
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    log_data = json.load(f)
                    pid = log_data.get("persona_id")
                    proto = log_data.get("prototype")
                    if pid and proto:
                        persona_counts.setdefault(pid, {"A": 0, "B": 0})
                        if proto in ["A", "B"]:
                            persona_counts[pid][proto] += 1
            except Exception as e:
                print(f"[WARN] Errore leggendo {log_file.name}: {e}")
    # Filtra personas che NON sono già state usate almeno una volta per A e una volta per B
    available_personas = []
    for p in all_personas:
        pid = p.get('persona_id', '')
        counts = persona_counts.get(pid, {"A": 0, "B": 0})
        if counts["A"] < 1 or counts["B"] < 1:
            available_personas.append(p)
    if available_personas:
        persona = random.choice(available_personas)
        print(f"[OK] Selezionata persona CASUALE: {persona.get('persona_id', '')}")
        pid = persona.get('persona_id', '')
        counts = persona_counts.get(pid, {"A": 0, "B": 0})
        print(f"[DEBUG] Persona scelta: {pid} | Nome: {persona.get('bio', {}).get('nome', 'N/A')} | Sessioni A: {counts['A']} | Sessioni B: {counts['B']}")
    else:
        print(f"[WARN] Tutte le personas sono già state usate su entrambi i prototipi - seleziono casualmente da tutte")
        persona = random.choice(all_personas)
        pid = persona.get('persona_id', '')
        counts = persona_counts.get(pid, {"A": 0, "B": 0})
        print(f"[DEBUG] Persona scelta (tutte usate): {pid} | Nome: {persona.get('bio', {}).get('nome', 'N/A')} | Sessioni A: {counts['A']} | Sessioni B: {counts['B']}")
    print(f"[PERSONA] Selezionata: {persona.get('persona_id', 'N/A')}")
    print(f"          Nome: {persona.get('bio', {}).get('nome', 'N/A')}, {persona.get('bio', {}).get('eta', 'N/A')} anni")
    print(f"          Brief: {persona.get('brief', 'N/A')[:100]}...")
    print(f"[DEBUG] Totale personas disponibili: {len(all_personas)}")
    return persona


def format_persona_for_llm(persona) -> str:
    #Formatta persona per il prompt LLM usando solo brief
    if not persona:
        return ""
    
    return f"""
PROFILO UTENTE:
{persona.get('brief', 'Profilo non disponibile')}
"""


#JSON FRAME
def load_frame_json(frame_name: str) -> Optional[Dict[str, Any]]:
    frame_path = FRAMES_LOCAL_DIR / f"{frame_name}_local.json"
    if not frame_path.exists():
        print(f"[ERROR] Frame JSON non trovato: {frame_path}")
        return None
    
    with open(frame_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_portate_button_position() -> Optional[tuple]:
    # Cerca nel frame "all"
    all_frame_path = FRAMES_LOCAL_DIR / "all_local.json"
    if not all_frame_path.exists():
        print(f"[WARN] File {all_frame_path} non trovato")
        return None
    
    try:
        with open(all_frame_path, "r", encoding="utf-8") as f:
            frame_data = json.load(f)
        
        print(f"[DEBUG] get_portate_button_position() - Cercando 'portate'...")
        portate_elem = find_element_by_name(frame_data, "portate")
        if portate_elem:
            bbox = portate_elem.get("absoluteBoundingBox", {})
            x = bbox.get("x", 0)
            y = bbox.get("y", 0)
            h = bbox.get("height", 0)
            print(f"[Okay] Bottone 'Portate' trovato: id={portate_elem.get('id')}, name={portate_elem.get('name')}, chars={portate_elem.get('characters')}, pos=({x}, {y}), h={h}")
            return (x, y, h)
        else:
            print(f"[ERROR] Bottone 'Portate' NON trovato in frame 'all'!")
            # Debug: stampa gli elementi nel frame
            available = _collect_named_nodes(frame_data)
            available_names = [n.get("characters", n.get("name", "?")) for n in available[:10]]
            print(f"[DEBUG] Primi 10 elementi nel frame: {available_names}")
            return None
    except Exception as e:
        print(f"[ERROR] Errore leggendo {all_frame_path}: {e}")
        return None


def collect_node_ids(node: Dict[str, Any], ids: Optional[set] = None) -> set:
    #Raccoglie ID nodi nel frame JSON
    if ids is None:
        ids = set()
    node_id = node.get("id")
    if node_id:
        ids.add(node_id)
    for child in node.get("children", []):
        collect_node_ids(child, ids)
    return ids


def build_frame_node_index(frames_dir: Optional[Path] = None) -> Dict[str, str]:
    #Indicizza node-id -> frame_name scansionando i file frames_local
    if frames_dir is None:
        frames_dir = FRAMES_LOCAL_DIR
    
    index: Dict[str, str] = {}
    for frame_path in frames_dir.glob("*_local.json"):
        frame_name = frame_path.stem.replace("_local", "")
        try:
            with open(frame_path, "r", encoding="utf-8") as f:
                frame_data = json.load(f)
            node_ids = collect_node_ids(frame_data)
            for node_id in node_ids:
                if node_id not in index:
                    index[node_id] = frame_name
        except Exception:
            continue
    return index


FRAME_NODE_INDEX = {}  # Inizializzato in test_prototype in base al prototipo


def parse_node_id_from_url(url: str) -> Optional[str]:
    #Estrae il node-id dall'URL Figma e lo normalizza ("-" -> ":")
    if not url:
        return None
    match = re.search(r"[?&]node-id=([^&]+)", url)
    if not match:
        return None
    return match.group(1).replace("-", ":")


def get_node_id_for_frame(frame_name: str, frame_index: Dict[str, str]) -> Optional[str]:
    candidates = []
    for node_id, frame in frame_index.items():
        if frame == frame_name:
            candidates.append(node_id)
    
    if not candidates:
        return None
    
    # Preferisci node-id root (senza "I" iniziale e ";")
    root_nodes = [n for n in candidates if not n.startswith('I') and ';' not in n]
    if root_nodes:
        return root_nodes[0]
    
    # Fallback: ritorna il primo trovato
    return candidates[0]


def set_node_id_in_url(url: str, node_id: str) -> str:
    if not url or not node_id:
        return url
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    # Figma URL format: sostituisci TUTTI i caratteri speciali con "-"
    node_id_formatted = node_id.replace(":", "-").replace(";", "-")
    qs["node-id"] = [node_id_formatted]
    # NON modificare starting-point-node-id - mantieni quello originale del prototipo
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def get_frame_from_url(url: str, current_frame: str = "home") -> str:
    node_id = parse_node_id_from_url(url)
    if not node_id:
        print(f"[DEBUG] Nessun node-id trovato nell'URL, mantengo frame: {current_frame}")
        return current_frame
    
    # Cerca nel FRAME_NODE_INDEX
    if node_id in FRAME_NODE_INDEX:
        frame_name = FRAME_NODE_INDEX[node_id]
        print(f"[DEBUG] Node-id {node_id} → frame '{frame_name}'")
        return frame_name
    
    # Se non trovato, il node-id è uno dei nodi nested (es. ricetta dettagliata)
    # I nodi nested appartengono a frame di dettaglio (ricetta)
    print(f"[DEBUG] Node-id {node_id} non trovato in FRAME_NODE_INDEX, è un nodo nested → frame 'ricetta'")
    return "ricetta"


def find_element_by_id(node: Dict[str, Any], target_id: str) -> Optional[Dict[str, Any]]:
    #Ricerca ricorsiva di elemento per ID
    if node.get("id") == target_id:
        return node
    
    for child in node.get("children", []):
        result = find_element_by_id(child, target_id)
        if result:
            return result
    
    return None


def find_element_by_name(node: Dict[str, Any], target_name: str) -> Optional[Dict[str, Any]]:
    #Ricerca ricorsiva di un elemento per characters esatto (case-insensitive).
    #Se non trovato esattamente, prova il fuzzy matching progressivo.
    if not target_name:
        return None

    node_chars = node.get("characters")
    if isinstance(node_chars, str) and target_name.strip().lower() == node_chars.strip().lower():
        return node

    for child in node.get("children", []):
        result = find_element_by_name(child, target_name)
        if result:
            return result

    #Se non trovato, prova il fuzzy matching progressivo (esatto, difflib, parole chiave)
    return find_element_by_name_fuzzy(node, target_name)


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower()) if value else ""


def _collect_named_nodes(node: Dict[str, Any], nodes: Optional[list] = None) -> list:
    if nodes is None:
        nodes = []
    name = node.get("name")
    chars = node.get("characters")
    if isinstance(name, str) or isinstance(chars, str):
        nodes.append(node)
    for child in node.get("children", []):
        _collect_named_nodes(child, nodes)
    return nodes


def find_element_by_name_fuzzy(node: Dict[str, Any], target_name: str) -> Optional[Dict[str, Any]]:
    #Fallback fuzzy match quando il target_text non coincide esattamente.
    
    #Strategia di ricerca progressiva:
    #1. Ricerca esatta (case-insensitive)
    #2. Fuzzy match con difflib
    #3. Ricerca per parole chiave (substring matching parziale)
    if not target_name:
        return None
    
    target_lower = target_name.strip().lower()
    target_norm = _normalize_text(target_name)
    
    if not target_norm:
        return None

    candidates = _collect_named_nodes(node)
    if not candidates:
        return None

    # Passo 1: Ricerca esatta (case-insensitive, solo se almeno un match esatto)
    exact_matches = [candidate for candidate in candidates if isinstance(candidate.get("characters", ""), str) and candidate.get("characters", "").strip().lower() == target_lower]
    if exact_matches:
        print(f"[DEBUG] Match esatto trovato: '{target_name}'")
        return exact_matches[0]

    # Passo 2: Fuzzy match con difflib SOLO se nessun match esatto
    name_map = {}
    for n in candidates:
        name = n.get("name") or n.get("characters") or ""
        norm = _normalize_text(str(name))
        if norm and norm not in name_map:
            name_map[norm] = n

    best = difflib.get_close_matches(target_norm, list(name_map.keys()), n=1, cutoff=0.6)
    if best:
        print(f"[DEBUG] Fuzzy match trovato: '{target_name}' → '{name_map[best[0]].get('characters', name_map[best[0]].get('name', ''))}'")
        return name_map[best[0]]

    # Passo 3: Ricerca per parole chiave (estrai parole > 3 caratteri)
    keywords = [w for w in target_name.lower().split() if len(w) > 3]
    if keywords:
        print(f"[DEBUG] Ricerca per parole chiave: {keywords}")

        matches_by_score = []
        for candidate in candidates:
            chars = candidate.get("characters", "")
            if not isinstance(chars, str):
                continue

            chars_lower = chars.lower()
            # Conta quante parole chiave sono presenti
            matched_keywords = sum(1 for kw in keywords if kw in chars_lower)

            if matched_keywords > 0:
                # Priorità: più parole chiave corrispondono, meglio è
                matches_by_score.append((matched_keywords, chars, candidate))

        if matches_by_score:
            # Ordina per numero di corrispondenze (decrescente) e lunghezza del nome (crescente)
            matches_by_score.sort(key=lambda x: (-x[0], len(x[1])))
            best_match = matches_by_score[0]
            print(f"[DEBUG] Match per parole chiave trovato ({best_match[0]} corrispondenze): '{target_name}' → '{best_match[1]}'")
            return best_match[2]
        else:
            # Nessun match: stampa i primi 10 elementi disponibili per debug
            available = [(c.get("characters", c.get("name", "?"))[:40]) for c in candidates[:10]]
            print(f"[DEBUG] Nessun elemento con parole chiave {keywords}. Elementi disponibili (primi 10): {available}")

    print(f"[DEBUG] Nessun match trovato per: '{target_name}'")
    return None


def get_element_center(element: Dict[str, Any]) -> Optional[tuple]:
    #Estrae centro (x, y) dal bounding box dell'elemento
    bbox = element.get("absoluteBoundingBox")
    if not bbox:
        return None
    
    x = bbox.get("x", 0)
    y = bbox.get("y", 0)
    width = bbox.get("width", 0)
    height = bbox.get("height", 0)
    
    center_x = int(x + width / 2)
    center_y = int(y + height / 2)
    
    return (center_x, center_y)


def extract_clickable_elements(node: Dict[str, Any], elements: Optional[list] = None) -> list:
    #Estrae ricorsivamente solo elementi con testo visibile (characters)
    if elements is None:
        elements = []
    
    # Info essenziali
    node_id = node.get("id")
    node_name = node.get("name")
    node_characters = node.get("characters")
    bbox = node.get("absoluteBoundingBox")
    
    # Rendi cliccabile qualsiasi nodo di tipo TEXT con characters non vuoto, escludendo solo nomi tecnici generici
    technical_names = ["group", "frame", "type:card", "card/tags", "media", "diff", "arrow", "vector", "union", "rectangle", "time", "container/icon", "time/icon"]
    if (
        node.get("type") == "TEXT"
        and node_id and node_name and bbox
        and node_characters and isinstance(node_characters, str) and node_characters.strip()
        and node_name.lower() not in technical_names
        and node_characters.lower() not in technical_names
    ):
        x = bbox.get("x", 0)
        y = bbox.get("y", 0)
        w = bbox.get("width", 0)
        h = bbox.get("height", 0)
        #calcola centro
        cx = int(x + w / 2)
        cy = int(y + h / 2)
        element_info = {
            "id": node_id,
            "name": node_name,
            "characters": node_characters,
            "x": int(x),
            "y": int(y),
            "width": int(w),
            "height": int(h),
            "center_x": cx,
            "center_y": cy
        }
        elements.append(element_info)
    
    #ricorsione sui figli (NIENTE ORDINAMENTO)
    for child in node.get("children", []):
        extract_clickable_elements(child, elements)

    # Log di debug: stampa tutti i characters trovati solo al livello root della chiamata
    if node.get("type") == "FRAME" and node.get("name", "").lower().startswith("type:card"):
        if elements:
            print("[DEBUG] Elementi cliccabili trovati:", [e["characters"] for e in elements if e.get("characters")])

    return elements


def get_frame_element_names_for_llm(
    frame_name: str,
    viewport_width: int,
    viewport_height: int,
    limit: int = 30,
    scroll_offset_y: int = 0
) -> List[str]:
    """Ritorna lista MINIMALISTA di SOLO elementi cliccabili essenziali: titoli card e bottoni navigazione.
    scroll_offset_y: offset di scroll verticale per adattare le coordinate degli elementi.
    
    Elementi inclusi:
    - Titoli card ricette (name="Card/Title")
    - Bottoni navigazione: Prep.it, Back, Portate
    - Opzioni overlay: Primi, Secondi, Antipasti, Dolci
    
    Esclusi: descrizioni, tempi, difficoltà, allergeni, tag
    """
    frame_data = load_frame_json(frame_name)
    if not frame_data:
        return []
    elements = extract_clickable_elements(frame_data)
    visible = filter_elements_by_viewport(elements, viewport_width, viewport_height, scroll_offset_y=scroll_offset_y)
    
    # Ordina gli elementi visibili per coordinata y (dall'alto verso il basso nel viewport)
    visible_sorted = sorted(visible, key=lambda e: e.get("y", 0) - scroll_offset_y)
    
    # Identificatori per elementi ESSENZIALI (solo cliccabili principali)
    navigation_buttons = ["prep.it", "back", "portate", "primi", "secondi", "antipasti", "dolci", "le ricette", "scopri le ricette"]
    
    names = []
    seen = set()
    
    for elem in visible_sorted:
        text = str(elem.get("characters", "")).strip()
        name = elem.get("name", "").lower()
        
        if not text or text in seen or text.lower() == "none":
            continue
            
        # Esclude testi tecnici generici
        if text.lower() in ["group", "frame", "type:card", "card/tags", "media", "diff", "arrow", "vector", "union", "rectangle"]:
            continue
        
        # FILTRO STRETTO: Include SOLO:
        # 1. Titoli card (name contiene "card/title")
        # 2. Bottoni navigazione (prep.it, back, portate, primi, secondi, etc.)
        is_card_title = "card/title" in name
        is_nav_button = text.lower() in navigation_buttons
        
        if is_card_title or is_nav_button:
            seen.add(text)
            names.append(text)
        
        if len(names) >= limit:
            break

    # In ricetta: consentire solo Back e Prep.it come elementi cliccabili
    if frame_name == "ricetta":
        allowed = {"back", "prep.it"}
        names = [n for n in names if n.strip().lower() in allowed]

    return names




def filter_elements_by_task(elements: list, task: str) -> list:
    """
    STRATEGIA 1: Filtra elementi per PAROLE CHIAVE nel task
    
    Target principali: bottoni, cards, filtri, logo
    """
    if not elements:
        return []
    
    # Parole chiave per filtrare elementi rilevanti
    keywords = [
        "button", "btn", "cta",
        "card", "tile", "item",
        "filter", "filtro", "filtri", "category", "categoria",
        "logo", "brand"
    ]
    
    # Filtra per grandezza minima (almeno 20x20 pixel)
    MIN_SIZE = 20
    filtered = []
    
    for elem in elements:
        w = elem.get("width", 0)
        h = elem.get("height", 0)
        
        # Salta elementi troppo piccoli
        if w < MIN_SIZE or h < MIN_SIZE:
            continue
        
        # Salta elementi nascosti
        if w == 0 or h == 0:
            continue
        
        name_lower = elem.get("name", "").lower()
        
        # Se nome contiene keyword rilevante → includi
        if any(kw in name_lower for kw in keywords):
            filtered.append(elem)
    
    return filtered


def filter_elements_by_viewport(elements: list, viewport_width: int = 1280, viewport_height: int = 720, scroll_offset_y: int = 0) -> list:
    """
    STRATEGIA 2: Filtra elementi VISIBILI nel viewport
    (utile se stai scrollando - mostra solo elementi visibili ORA)
    scroll_offset_y: offset di scroll verticale (positivo = scrollato down, elementi con y più alto sono visibili)
    
    CRITERIO: Un elemento è visibile se il suo centro è dentro il viewport
    """
    visible = []
    
    for elem in elements:
        x = elem.get("x", 0)
        y = elem.get("y", 0)
        w = elem.get("width", 0)
        h = elem.get("height", 0)
        # Applica offset di scroll
        adjusted_y = y - scroll_offset_y
        # Elemento parzialmente visibile se QUALSIASI parte del suo bounding box è dentro il viewport
        in_viewport = (
            x + w > 0 and x < viewport_width and
            adjusted_y + h > 0 and adjusted_y < viewport_height
        )
        if in_viewport:
            visible.append(elem)
    
    return visible




def extract_first_json(text: str) -> Optional[Dict]:
    import re
    
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if not match:
        return None
    
    try:
        result = json.loads(match.group())
        # Normalizza il campo "completed" in booleano
        if "completed" in result:
            val = result["completed"]
            if isinstance(val, str):
                result["completed"] = val.lower() in ("true", "yes", "1", "done")
            else:
                result["completed"] = bool(val)
        return result
    except Exception:
        return None


def image_to_base64(img: Image.Image) -> str:
    img_resized = img.resize((512, 362), Image.Resampling.LANCZOS)
    
    buf = io.BytesIO()
    img_resized.save(buf, format="JPEG", quality=40)  # Quality ridotta a 40
    return base64.b64encode(buf.getvalue()).decode("utf-8")


async def call_llm_combined(img_b64: str, persona: Dict, current_task: str, frame_elements: Optional[list] = None, current_frame: str = "home", is_final: bool = False, all_actions: Optional[list] = None, task_actions: Optional[list] = None) -> Optional[Dict]:
    """
    CHIAMATA LLM - Google Gemini
    
    Richiede:
    1. Stato task (completato/non completato)
    2. Azione da fare (se non completato)
    3. Feedback UX (se is_final=True)
    """
    global LAST_LLM_CALL_TS
    ignore_preferences = bool(current_task) and ("PRIMO PIATTO" in current_task or "SECONDO PIATTO" in current_task)
    persona_text = format_persona_for_llm(persona) if persona else ""
    if ignore_preferences:
        # Per i task 1 e 2: non includere gusti/brief per evitare bias
        persona_text = "PROFILO UTENTE:\n(IGNORA GUSTI PERSONALI PER QUESTO TASK)"
    
    # Formatta elementi come lista numerata (più leggibile per LLM)
    if frame_elements:
        elements_list = "\n".join([f"{i+1}. {name}" for i, name in enumerate(frame_elements)])
    else:
        elements_list = "(nessun elemento disponibile)"
    
    elements_json = json.dumps(frame_elements, ensure_ascii=False) if frame_elements else "[]"
    task_actions_summary = json.dumps(task_actions, indent=2, ensure_ascii=False) if task_actions else "[]"
    
    # Costruisci prompt
    if is_final:
        if all_actions:
            actions_summary = json.dumps(all_actions, indent=2, ensure_ascii=False)
            prompt = f"""{persona_text}

IMMEDESIMAZIONE (OBBLIGATORIO):
- Ragiona e decidi in prima persona, come se fossi davvero questa persona.
- Coerenza con gusti, vincoli, personalità, pazienza e motivazioni.
- Non uscire dal ruolo.

Hai completato TUTTE le task! Ecco il riassunto delle azioni eseguite:

AZIONI ESEGUITE:
{actions_summary}

---
Valuta l'ESPERIENZA GLOBALE (scale 1-7):
1. Facilità d'uso del sito? (1=molto difficile, 7=molto facile)
2. Soddisfa le tue esigenze? (1=per niente, 7=perfettamente)
3. Eventuali note/suggerimenti? (testo breve)

RISPONDI ESCLUSIVAMENTE IN FORMATO JSON:
{{"ease_of_use": 1-7, "feature_satisfaction": 1-7, "notes": "feedback opzionale"}}
"""
        else:
            prompt = f"""{persona_text}

IMMEDESIMAZIONE (OBBLIGATORIO):
- Ragiona e decidi in prima persona, come se fossi davvero questa persona.
- Coerenza con gusti, vincoli, personalità, pazienza e motivazioni.
- Non uscire dal ruolo.

Hai appena completato: {current_task}

Valuta l'esperienza con il sito (scale 1-7):
1. Facilità d'uso? (1=difficile, 7=facile)
2. Soddisfa le tue esigenze? (1=no, 7=sì)

RISPONDI ESCLUSIVAMENTE IN FORMATO JSON:
{{"ease_of_use": 1-7, "feature_satisfaction": 1-7}}
"""
    else:
        actions_context = f"\nAZIONI RECENTI:\n{get_actions_summary(task_actions, max_actions=15)}\n" if task_actions else ""
        
        # Elenco piatti già visti (solo click su ricette, esclude UI/filtri)
        seen_dishes = []
        if task_actions:
            ui_exclusions = {
                "back",
                "prep.it",
                "portate",
                "primi",
                "secondi",
                "le ricette",
                "scopri le ricette",
                "home",
            }
            seen_set = set()
            for a in task_actions:
                if a.get("action_type") == "click":
                    t = str(a.get("target_text", "")).strip()
                    if t and t.lower() not in ui_exclusions:
                        if t not in seen_set:
                            seen_set.add(t)
                            seen_dishes.append(t)
        if task_actions:
            actions_context += "\n ANALIZZA LE AZIONI PRECEDENTI:\n- NON ripetere la stessa azione se non ha funzionato\n- Se sei bloccato prova un approccio COMPLETAMENTE DIVERSO\n- Usa il feedback delle azioni passate per decidere il prossimo passo\n"
            
            last_action = task_actions[-1]
            if last_action.get('action_type') in ['click', 'hover']:
                frame_before = last_action.get('frame')
                frame_after = last_action.get('frame_after', frame_before)
                
                if frame_before == frame_after:
                    # Nessun cambio di frame
                    actions_context += f"\n FEEDBACK CLICK: Hai cliccato '{last_action.get('target_text', '?')}' ma il frame è rimasto '{current_frame}' → ELEMENTO GIÀ SELEZIONATO! Prova un'azione DIVERSA.\n"
                elif frame_after != current_frame:
                    # Il click ha cambiato il frame, ma poi è ritornato indietro (overlay chiuso)
                    actions_context += f"\n FEEDBACK CLICK: Il click ha aperto '{frame_after}' ma sei ritornato a '{current_frame}' (overlay si è chiuso). Ora seleziona cosa guardare in '{current_frame}'!\n"
                else:
                    # Il click ha funzionato e il frame è  cambiato
                    actions_context += f"\n FEEDBACK CLICK: Il click ha funzionato! Frame cambiato da '{frame_before}' a '{frame_after}' - continua a cercare il piatto.\n"
        
        # Lista piatti visti
        seen_dishes_section = (
            "\nPIATTI GIÀ VISTI (evita di ripetere):\n- " + "\n- ".join(seen_dishes)
            if seen_dishes else ""
        )

        prompt = f"""Sei: {persona_text.strip()}

    IMMEDESIMAZIONE:
    - Ragiona e decidi in prima persona, come se fossi davvero questa persona.
    - Coerenza con gusti, vincoli, personalità, pazienza e motivazioni.

{"TASK PRIORITY - IGNORA PREFERENZE PERSONALI:" if ignore_preferences else "COERENZA PERSONALE:"}
{("PER QUESTO TASK (primo/secondo piatto):\n- IGNORA i tuoi gusti personali (allergie)\n- Valuta i criteri oggettivi del task (tempo di preparazione, difficoltà, tipo di piatto)" if ignore_preferences else "Scegli un piatto coerente con le tue preferenze e vincoli personali (allergie).")}


{actions_context}
TASK ATTUALE: {current_task}


EFFICIENZA: Hai già fatto {len(task_actions) if task_actions else 0} azioni per questo task.
{"ATTENZIONE: Stai facendo tante azioni! Dopo 8-10 azioni prova a  scegliere un piatto. NON continuare a cercare all'infinito." if task_actions and len(task_actions) >= 8 else "Cerca di completare il task in max 8-10 azioni. Quando vedi un piatto che soddisfa pienamente i criteri, SCEGLILO."}

IMPORTANTE: NON cercare il "piatto perfetto"

REGOLE DI COMPORTAMENTO:
- Analizza lo screenshot e decidi la prossima azione 
- Puoi scegliere una ricetta dichiarando la scelta, o cliccando 
- questi sono i piatti che hai già visto {seen_dishes_section}
- clicca sulle ricette per vedere dettagli (ingredienti, preparazione, allergeni)
- scroll per vedere altre ricette (della categoria selezionata)

ATTENZIONE AL CAMPO "action"
AZIONI VALIDE - Il campo "action" DEVE essere ESATTAMENTE uno di questi 5 valori (tutto minuscolo):
1. "click" → Per cliccare su un elemento (poi specifica target_text dalla lista)
2. "scroll" → Per scrollare la pagina (specifica direction: "up"/"down" e distance >= 400)
3. "hover" → Per passare il mouse su un elemento (specifica target_text dalla lista)
4. "read" → Per leggere il contenuto corrente
5. "back" → Per tornare alla schermata precedente da una schermata ricette

ERRORI FATALI DA EVITARE:
{{"action": "Prep.it"}} → NO! Usa {{"action": "click", "target_text": "Prep.it"}}
{{"action": "scopri Le Ricette"}} → NO! Usa {{"action": "click", "target_text": "scopri Le Ricette"}}
{{"action": "Back"}} → NO! Usa {{"action": "back"}} (tutto minuscolo)
{{"action": "Click"}} → NO! Usa {{"action": "click"}} (tutto minuscolo)
{{"action": "Foglie di vite ripiene"}} → NO! Usa {{"action": "click", "target_text": "Foglie di vite ripiene"}}

ESEMPI CORRETTI:
{{"action": "click", "target_text": "Prep.it"}}
{{"action": "click", "target_text": "scopri Le Ricette"}}
{{"action": "back", "target_text": "Back"}}
{{"action": "scroll", "direction": "down", "distance": 600}}

REGOLA: Il campo "action" contiene il TIPO di azione, NON il nome dell'elemento!
CORRETTO: {{"action": "click", "target_text": "scopri Le Ricette"}}

IMPORTANTE: Rispondi SOLO con un oggetto JSON valido in italiano.
NON scrivere spiegazioni fuori dal JSON.

FORMATO RISPOSTA (SOLO JSON):
Se task NON completato:
{{"action": "click", "target_text": "nome_elemento", "reason": "spiegazione", "completed": false}}

Quando hai trovato una ricetta per completare il task:
{{"action": "done", "reason": "motivo completamento", "selected_recipe": "nome_piatto_scelto", "selection_reason": "spiegazione dettagliata di perché hai scelto questo piatto (es: tempo di prep, difficoltà, ingredienti, preferenze personali che lo rendono adatto)", "completed": true}}

IMPORTANTE: Nel campo 'selection_reason' spiega ESATTAMENTE perché hai scelto questo piatto.
"""
    
    # Cache per ridurre chiamate duplicate
    global LAST_CACHE_KEY
    cache_key = None
    if not is_final and img_b64:
        cache_key = hash_screenshot(img_b64, f"{current_task}|{current_frame}|{elements_json}|{task_actions_summary}")
        if cache_key in ACTION_CACHE:
            print("[DEBUG] Cache hit LLM - riuso risposta precedente")
            LAST_CACHE_KEY = cache_key
            return ACTION_CACHE[cache_key]
    
    LAST_CACHE_KEY = cache_key
    
    print(f"[DEBUG] Chiamata LLM - is_final={is_final}, current_task={current_task[:50] if current_task else 'N/A'}...")
    
    if DISABLE_LLM:
        print("[DEBUG] LLM DISABILITATO - ritorno risposta fittizia")
        if is_final:
            return {"ease_of_use": 4, "feature_satisfaction": 4, "notes": "LLM disabilitato"}
        return {"action": DISABLE_LLM_ACTION or "read", "target_text": "", "reason": "LLM disabilitato"}
    
    # ============================================
    # Google Gemini API
    # ============================================
    try:
        # Throttle per rispettare rate limits Gemini (15 RPM free tier)
        elapsed = time.time() - LAST_LLM_CALL_TS
        if elapsed < 4.5:  # ~13 RPM per sicurezza
            wait_s = 4.5 - elapsed
            print(f"[DEBUG] Gemini throttle: attendo {wait_s:.1f}s")
            await asyncio.sleep(wait_s)
        
        print(f"[DEBUG] Chiamata Gemini API (model: {LLM_MODEL})...")
        
        # Prepara contenuti multimodali 
        contents: List[Any] = []
        contents.append(types.Part.from_text(text=prompt))
        
        if img_b64:
            try:
                image_part = types.Part.from_bytes(
                    data=base64.b64decode(img_b64),
                    mime_type="image/jpeg"
                )
                contents.append(image_part)
            except Exception as e:
                print(f"[WARN] Errore decodifica immagine: {e}. Procedo solo con testo.")
        
        # Chiamata Gemini
        response = gemini_client.models.generate_content(
            model=LLM_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.2,  # Più basso per precisione
                max_output_tokens=1000
            )
        )
        
        LAST_LLM_CALL_TS = time.time()
        
        # Gemini ritorna già JSON 
        response_text = (response.text or "{}").strip()
        if not response_text:
            response_text = "{}"
        
        print(f"[DEBUG] Response text length: {len(response_text)}, first 100 chars: {response_text[:100]}")
        
        # Estrai JSON dal blocco markdown se presente
        if response_text.startswith("```"):
            # Rimuovi ```json e ``` agli inizi/fine
            response_text = response_text.replace("```json", "").replace("```", "").strip()
        
        # Se il testo contiene sia ragionamento che JSON, cerca di estrarre solo il JSON
        if not response_text.startswith("{"):
            # Cerca un oggetto JSON nel testo
            import re
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_text)
            if json_match:
                response_text = json_match.group(0)
                print(f"[DEBUG] JSON estratto dal testo: {response_text[:100]}")
            else:
                print(f"[ERROR] Nessun JSON trovato nella risposta")
                print(f"[DEBUG] Risposta completa: {response_text}")
                return {}
        
        result = json.loads(response_text)
        print(f"[DEBUG] Gemini response keys: {list(result.keys())}")
        
        if cache_key:
            ACTION_CACHE[cache_key] = result
        
        return result
        
    except Exception as e:
        error_str = str(e)
        print(f"[ERROR] Gemini API error: {error_str}")
        
        # Gestione rate limit e errori temporanei Gemini
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "503" in error_str:
            error_type = "rate limit" if "429" in error_str else ("resource exhausted" if "RESOURCE_EXHAUSTED" in error_str else "service unavailable")
            print(f"[WARN] Gemini {error_type} - attendo 60s e riprovo...")
            await asyncio.sleep(60)
            # Secondo tentativo
            try:
                response = gemini_client.models.generate_content(
                    model=LLM_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=1000
                    )
                )
                LAST_LLM_CALL_TS = time.time()
                response_text = (response.text or "{}").strip()
                if not response_text:
                    response_text = "{}"
                if response_text.startswith("```"):
                    response_text = response_text.replace("```json", "").replace("```", "").strip()
                
                # Estrai JSON se mescolato con testo
                if not response_text.startswith("{"):
                    import re
                    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_text)
                    if json_match:
                        response_text = json_match.group(0)
                    else:
                        print(f"[ERROR] Nessun JSON nel retry")
                        return {}
                
                result = json.loads(response_text)
                if cache_key:
                    ACTION_CACHE[cache_key] = result
                return result
            except Exception as retry_e:
                print(f"[ERROR] Gemini retry fallito: {retry_e}")
                return {}
        else:
            print(f"[ERROR] Gemini error non recuperabile")
            return {}
    return {}




# ACTIONS

async def execute_action(page, frame, canvas_box, action: Dict, current_frame: str = "home", portate_button_pos: Optional[tuple] = None, scroll_offset_y: int = 0):
    #Esegue l'azione scelta dall'LLM, ritorna (success, updated_frame)    
    action_type = action.get("action")
    if action_type:
        print(f"[ACTION] {action_type.upper()}: {action.get('reason', '')}")
    else:
        print(f"[ACTION] UNKNOWN: {action.get('reason', '')}")
    
    if action_type == "click":
        success, updated_frame = await execute_click(page, frame, canvas_box, action, current_frame, portate_button_pos, scroll_offset_y)
        if updated_frame != current_frame:
            print(f"[FRAME] Aggiornato: {current_frame} -> {updated_frame}")
        return success, updated_frame
    
    elif action_type == "scroll":
        success = await execute_scroll(page, frame, action, canvas_box)
        return success, current_frame
    
    elif action_type == "hover":
        success = await execute_hover(page, frame, canvas_box, action, current_frame, portate_button_pos)
        return success, current_frame
    
    elif action_type == "back":
        print(f"[ACTION] Cerco bottone 'back' nel frame corrente '{current_frame}'...")
        
        if current_frame == "ricetta":
            back_action = {"target_text": "back"}
            success, updated_frame = await execute_click(page, frame, canvas_box, back_action, current_frame, portate_button_pos, scroll_offset_y)
            if success:
                print(f"[OK] Back eseguito - frame aggiornato: {updated_frame}")
            else:
                print(f"[ERROR] Back fallito nel frame 'ricetta' - frame rimasto '{updated_frame}'")
            return success, updated_frame
        else:
            print(f"[WARN] Back button non disponibile nel frame '{current_frame}' (solo in 'ricetta')")
            print(f"[HINT] Usa Portate per navigare tra le categorie")
            return False, current_frame
    
    elif action_type == "read":
        print(f"[ACTION] Lettura schermata in corso...")
        await page.wait_for_timeout(2000)
        return True, current_frame
    
    return False, current_frame


async def execute_click(page, frame, canvas_box, action: Dict, current_frame: str = "home", portate_button_pos: Optional[tuple] = None, scroll_offset_y: int = 0) -> tuple[bool, str]:
    """Esegui click su elemento - cerca per NOME nel frame JSON corrente
    Se node-id non combacia con nessun frame conosciuto, usa ricetta_local
    scroll_offset_y: offset scroll corrente per calcolare coordinate relative al viewport
    Ritorna: (success, updated_frame)
    """
    
    # Ottieni target_text dall'LLM (il nome dell'elemento che vuole cliccare)
    target_text = action.get("target_text")
    
    if not target_text:
        print(f"[ERROR] target_text non fornito dall'LLM")
        return False, current_frame
    
    print(f"[DEBUG] Cercando elemento: '{target_text}'")
    
    # CASO SPECIALE: Se clicca su "Prep.it", forza navigazione a home
    if target_text.strip().lower() == "prep.it":
        print(f"[SPECIAL] Click su 'Prep.it' rilevato - forzo navigazione a home")
        # Naviga direttamente all'URL home
        home_node_id = None
        for nid, fname in FRAME_NODE_INDEX.items():
            if fname == "home":
                home_node_id = nid
                break
        
        if home_node_id:
            # Costruisci URL home
            home_url = page.url
            # Sostituisci node-id nell'URL
            import re
            home_url = re.sub(r'node-id=[^&]+', f'node-id={home_node_id.replace(":", "-")}', home_url)
            print(f"[DEBUG] Navigando a home: {home_url}")
            await page.goto(home_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            print(f"[OK] Navigazione a home completata")
            return True, "home"
        else:
            print(f"[WARN] Node-id per 'home' non trovato - tento click normale")
    
    # Determina frame da usare: prova a identificare dall'URL
    frame_to_search = current_frame
    node_id = parse_node_id_from_url(page.url)
    
    # Se node-id è presente, prova a identificare il frame
    if node_id:
        detected_frame = get_frame_from_url(page.url, current_frame)
        if detected_frame != current_frame:
            print(f"[DEBUG] Frame cambiato dall'URL: {current_frame} → {detected_frame}")
            frame_to_search = detected_frame
            # IMPORTANTE: Se il frame è cambiato, il nuovo frame ha scroll=0!
            # Non usare lo scroll_offset del frame precedente
            original_scroll = scroll_offset_y
            scroll_offset_y = 0
            print(f"[DEBUG] Frame cambiato, resetto scroll_offset da {original_scroll} a 0 per questo click")
        else:
            print(f"[DEBUG] Frame rimane: {current_frame} (node-id non nel mapping)")
    else:
        print(f"[DEBUG] Nessun node-id nell'URL, uso frame: {current_frame}")
    
    # Carica frame JSON
    frame_data = load_frame_json(frame_to_search)
    if not frame_data:
        print(f"[ERROR] Frame '{frame_to_search}' non caricato")
        return False, current_frame
    
    # Cerca elemento per NOME nel frame JSON corrente
    element = find_element_by_name(frame_data, target_text)
    
    # Se non trovato e non siamo già in portate, cerca in portate (overlay che si apre sopra altri frame)
    if not element and frame_to_search != "portate":
        print(f"[DEBUG] Elemento '{target_text}' non trovato in '{frame_to_search}', cerco in portate (overlay)...")
        portate_data = load_frame_json("portate")
        if portate_data:
            element = find_element_by_name(portate_data, target_text)
            if element:
                print(f"[OK] Elemento '{target_text}' trovato in portate overlay!")
                print(f"[DEBUG] Nome elemento: {element.get('name')}, ID: {element.get('id')}")
                frame_to_search = "portate"
            else:
                print(f"[DEBUG] Elemento '{target_text}' non trovato neanche in portate")
    
    if not element:
        # Debug: stampa gli elementi disponibili nel frame
        frame_data = load_frame_json(frame_to_search)
        if frame_data:
            available = _collect_named_nodes(frame_data)
            available_names = [n.get("name", n.get("characters", "?")) for n in available[:15]]
            print(f"[DEBUG] Elementi disponibili in '{frame_to_search}': {available_names}")
        
        print(f"[ERROR] Elemento '{target_text}' non trovato nel frame '{frame_to_search}' né in portate overlay")
        print(f"[HINT] Frame corrente URL: {page.url}")
        print(f"[HINT] Verifica che l'elemento sia visibile e il nome sia corretto")
        return False, current_frame
    
    # Estrai coordinate dal JSON Figma
    coords = get_element_center(element)
    if not coords:
        print(f"[ERROR] Coordinate non disponibili per '{target_text}'")
        return False, current_frame
    
    figma_x, figma_y = coords
    print(f"[OK] Elemento trovato: {element.get('name')}")
    print(f"[OK] Coordinate Figma: ({figma_x}, {figma_y})")
    
    try:
        from PIL import Image, ImageDraw
        # Se siamo nel frame portate, calcola coordinate assolute usando bottone Portate
        if frame_to_search == "portate":
            if not portate_button_pos:
                print(f"[ERROR] Tentativo di cliccare in portate ma portate_button_pos è None!")
                return False, current_frame
            # Coordinate in portate_local.json sono relative al frame (0,0)
            # Aggiungiamo la posizione del bottone + offset gap (5px)
            screen_x = portate_button_pos[0] + figma_x
            screen_y = portate_button_pos[1] + portate_button_pos[2] + 5 + figma_y
            print(f"[DEBUG] Click in portate: locale=({figma_x}, {figma_y}), bottone_pos=({portate_button_pos[0]}, {portate_button_pos[1]}), h={portate_button_pos[2]}, assoluta=({screen_x}, {screen_y})")
        else:
            # Applica scroll offset: sottrai lo scroll dalle coordinate Y assolute del frame
            screen_x = figma_x
            screen_y = figma_y - scroll_offset_y
            if scroll_offset_y > 0:
                print(f"[DEBUG] Click con scroll: Figma_Y={figma_y}, scroll_offset={scroll_offset_y}, viewport_Y={screen_y}")
        
        # Verifica se elemento è dentro viewport
        canvas_w = canvas_box['width']
        canvas_h = canvas_box['height']
        TOLERANCE = 50
        if screen_y < -TOLERANCE or screen_y > canvas_h + TOLERANCE:
            print(f"[ERROR] Elemento '{target_text}' troppo fuori viewport! Y={screen_y}, canvas_height={canvas_h}")
            print(f"[HINT] Scrollo automaticamente per portare l'elemento in vista.")
            new_scroll_offset = figma_y - int(canvas_h / 2)
            print(f"[DEBUG] Nuovo scroll_offset_y: {new_scroll_offset}")
            return await execute_click(page, frame, canvas_box, action, current_frame, portate_button_pos, scroll_offset_y=new_scroll_offset)
        if screen_y < 0 or screen_y > canvas_h:
            print(f"[WARN] Elemento '{target_text}' parzialmente fuori viewport (Y={screen_y}). Clampo coordinate...")
            screen_y = max(0, min(screen_y, canvas_h - 1))
        
        # Le coordinate dal JSON sono già corrette per il viewport (NO SCALING)
        # Aggiungi solo offset canvas
        canvas_h = canvas_box['height']
        
        abs_x = canvas_box['x'] + screen_x
        abs_y = canvas_box['y'] + screen_y
        
        # Clamp dentro canvas
        abs_x = max(canvas_box['x'], min(abs_x, canvas_box['x'] + canvas_w - 1))
        abs_y = max(canvas_box['y'], min(abs_y, canvas_box['y'] + canvas_h - 1))
        
        print(f"[DEBUG] Canvas: x={canvas_box['x']:.0f}, y={canvas_box['y']:.0f}, w={canvas_w:.0f}, h={canvas_h:.0f}")
        print(f"[DEBUG] Click absolute: ({abs_x:.0f}, {abs_y:.0f})")
        
        # Screenshot PRIMA
        before_path = CACHE_DIR / "action_before.jpg"
        shot = await page.screenshot()
        img_before = Image.open(io.BytesIO(shot))
        draw = ImageDraw.Draw(img_before)
        r = 15
        draw.ellipse([(abs_x - r, abs_y - r), (abs_x + r, abs_y + r)], outline="red", width=3)
        draw.text((int(abs_x) + 20, int(abs_y) - 10), target_text, fill="red")
        img_before.save(before_path)
        print(f"[DEBUG] Before: {before_path}")
        
        # CLICK
        print(f"[CLICK] @ ({int(abs_x)}, {int(abs_y)})")
        await page.mouse.move(abs_x, abs_y, steps=15)
        await page.wait_for_timeout(500)
        await page.mouse.down(button="left")
        await page.wait_for_timeout(300)
        await page.mouse.up(button="left")
        print("[OK] Click inviato!")
        
        # Attendi transizioni
        await page.wait_for_timeout(5000)
        
        # Screenshot DOPO
        after_path = CACHE_DIR / "action_after.jpg"
        shot_after = await page.screenshot()
        img_after = Image.open(io.BytesIO(shot_after))
        draw_after = ImageDraw.Draw(img_after)
        draw_after.ellipse([(abs_x - r, abs_y - r), (abs_x + r, abs_y + r)], outline="green", width=3)
        img_after.save(after_path)
        print(f"[DEBUG] After: {after_path}")
        
        # Determina frame aggiornato dopo il click
        updated_frame = get_frame_from_url(page.url, current_frame)
        print(f"[DEBUG] Frame estratto da URL: {updated_frame}")
        
        # ECCEZIONE: Se clicco su "Portate", forza il frame (perchè è un overlay che non cambia URL)
        element_name = element.get("name", "")
        element_name_lower = element_name.lower()
        print(f"[DEBUG] Nome elemento trovato: '{element_name}' → lowercase: '{element_name_lower}'")
        
        if "portate" in element_name_lower or "filtro portate" in element_name_lower:
            print(f"[OVERLAY] Rilevato click su 'Portate' - forzo frame a 'portate' (overlay non cambia URL)")
            updated_frame = "portate"
        
        print(f"[DEBUG] Frame finale ritornato: {updated_frame}")
        return True, updated_frame
    except Exception as e:
        print(f"[ERROR] Click failed: {e}")
        return False, current_frame


async def execute_scroll(page, frame, action: Dict, canvas_box: Dict) -> bool:
    """Esegui scroll"""
    
    direction = action.get("direction", "down").lower()
    distance_raw = action.get("distance", action.get("scroll_amount", 300))
    
    # Parsa distance: può essere numero, "full", "page", ecc.
    if isinstance(distance_raw, str):
        distance_raw_lower = distance_raw.lower()
        if distance_raw_lower == "full" or distance_raw_lower == "page":
            distance = 1000  # Full page scroll
        else:
            try:
                distance = int(distance_raw)
            except ValueError:
                distance = 300  # Default
    else:
        try:
            distance = int(distance_raw)
        except (ValueError, TypeError):
            distance = 300  # Default
    # Impone una distanza minima
    if distance < MIN_SCROLL_DISTANCE:
        distance = MIN_SCROLL_DISTANCE
    
    if direction in ["down", "bottom"]:
        scroll_y = distance
    elif direction in ["up", "top"]:
        scroll_y = -distance
    elif direction in ["right"]:
        scroll_y = 0
    elif direction in ["left"]:
        scroll_y = 0
    else:
        scroll_y = distance
    
    try:
        # IMPORTANTE: posiziona il mouse sul canvas prima di scrollare
        # altrimenti il wheel scroll non ha effetto sul viewport Figma
        canvas_center_x = canvas_box['x'] + canvas_box['width'] / 2
        canvas_center_y = canvas_box['y'] + canvas_box['height'] / 2
        await page.mouse.move(canvas_center_x, canvas_center_y)
        await page.wait_for_timeout(100)
        
        await page.mouse.wheel(0, int(scroll_y))
        print(f"[OK] Scroll {direction} {distance}px")
        await page.wait_for_timeout(2000)
        return True
    except Exception as e:
        print(f"[ERROR] Scroll failed: {e}")
        return False


async def execute_hover(page, frame, canvas_box, action: Dict, current_frame: str = "home", portate_button_pos: Optional[tuple] = None) -> bool:
    """Esegui hover su elemento - cerca per NOME nel frame JSON corrente"""
    
    # Ottieni target_text dall'LLM (il nome dell'elemento dove hovare)
    target_text = action.get("target_text")
    
    if not target_text:
        print(f"[ERROR] target_text non fornito dall'LLM per hover")
        return False
    
    # Determina frame da usare: prova a identificare dall'URL
    frame_to_search = current_frame
    node_id = parse_node_id_from_url(page.url)
    
    # Se node-id è presente, prova a identificare il frame
    if node_id:
        detected_frame = get_frame_from_url(page.url, current_frame)
        if detected_frame != current_frame:
            print(f"[DEBUG] Frame cambiato dall'URL: {current_frame} → {detected_frame}")
            frame_to_search = detected_frame
        else:
            print(f"[DEBUG] Frame rimane: {current_frame} (node-id non nel mapping)")
    else:
        print(f"[DEBUG] Nessun node-id nell'URL, uso frame: {current_frame}")
    
    # Carica frame JSON
    frame_data = load_frame_json(frame_to_search)
    if not frame_data:
        print(f"[ERROR] Frame '{frame_to_search}' non caricato")
        return False
    
    # Cerca elemento per NOME nel frame JSON corrente
    element = find_element_by_name(frame_data, target_text)
    
    # Se non trovato e non siamo già in portate, cerca in portate (overlay)
    if not element and frame_to_search != "portate":
        print(f"[DEBUG] Elemento '{target_text}' non trovato in '{frame_to_search}', cerco in portate...")
        portate_data = load_frame_json("portate")
        if portate_data:
            element = find_element_by_name(portate_data, target_text)
            if element:
                frame_to_search = "portate"
    
    if not element:
        print(f"[ERROR] Elemento '{target_text}' non trovato per hover")
        return False
    
    # Estrai coordinate dal JSON Figma
    coords = get_element_center(element)
    if not coords:
        print(f"[ERROR] Coordinate non disponibili per hover su '{target_text}'")
        return False
    
    figma_x, figma_y = coords
    print(f"[OK] Elemento trovato per hover: {element.get('name')}")
    print(f"[OK] Coordinate Figma: ({figma_x}, {figma_y})")
    
    try:
        # Se siamo nel frame portate, calcola coordinate assolute usando bottone Portate
        if frame_to_search == "portate" and portate_button_pos:
            screen_x = portate_button_pos[0] + figma_x
            screen_y = portate_button_pos[1] + portate_button_pos[2] + 5 + figma_y
            print(f"[DEBUG] Hover elemento in portate: locale=({figma_x}, {figma_y}), assoluta=({screen_x}, {screen_y})")
        else:
            screen_x = figma_x
            screen_y = figma_y
        
        # Calcola coordinate assolute
        canvas_w = canvas_box['width']
        canvas_h = canvas_box['height']
        
        abs_x = canvas_box['x'] + screen_x
        abs_y = canvas_box['y'] + screen_y
        
        # Clamp dentro canvas
        abs_x = max(canvas_box['x'], min(abs_x, canvas_box['x'] + canvas_w - 1))
        abs_y = max(canvas_box['y'], min(abs_y, canvas_box['y'] + canvas_h - 1))
        
        print(f"[HOVER] @ ({int(abs_x)}, {int(abs_y)})")
        
        # Muovi mouse e aspetta
        await page.mouse.move(abs_x, abs_y, steps=10)
        await page.wait_for_timeout(3000)  # Tempo per effetti hover
        
        print("[OK] Hover eseguito!")
        return True
        
    except Exception as e:
        print(f"[ERROR] Hover failed: {e}")
        return False


# ============================================================================
# MAIN
# ============================================================================

async def main() -> None:
    # Controlla se c'è una sessione incompleta da riprendere
    incomplete_session = None if DISABLE_RECOVERY else load_last_incomplete_session()
    if DISABLE_RECOVERY:
        print("[INFO] Recovery disabilitato (DISABLE_RECOVERY=1)")
    
    # Se non c'è sessione incompleta, controlla se c'è una completata da continuare
    completed_session = None
    if not incomplete_session and not DISABLE_RECOVERY:
        completed_session = load_last_completed_session_for_continuation()
    
    if incomplete_session:
        print(f"\n{'='*70}")
        print("[RECOVERY MODE] SESSIONE INCOMPLETA TROVATA")
        print(f"{'='*70}")
        print(f"Session ID: {incomplete_session.get('session_id')}")
        print(f"Prototype: {incomplete_session.get('prototype')}")
        print(f"Persona: {incomplete_session.get('persona')}")
        print(f"Azioni già eseguite: {len(incomplete_session.get('actions', []))}")
        print(f"\nRiassunto azioni precedenti:")
        print(get_actions_summary(incomplete_session.get('actions', [])))
        print(f"{'='*70}\n")
        
        # Ripristina parametri dalla sessione
        persona_id = incomplete_session.get('persona_id')
        persona = {
            'persona_id': persona_id,
            'bio': {
                'nome': incomplete_session.get('persona'),
                'eta': incomplete_session.get('persona_age')
            },
            'brief': incomplete_session.get('persona_brief')
        }
        all_tasks = incomplete_session.get('tasks', [])
        prototype_name = incomplete_session.get('prototype')
        prototype_url = incomplete_session.get('prototype_url')
        
        # Verifica che abbiamo tutti i dati necessari
        if not prototype_name or not prototype_url:
            print("[ERROR] Sessione incompleta non contiene dati del prototipo validi")
            return
        
        # Test connessione API
        if not test_api_connection():
            print("[ERROR] Connessione API fallita - aborto")
            return
        
        # Riprendi test dal prototipo interrotto
        await test_prototype(
            persona,
            all_tasks,
            prototype_name,
            prototype_url,
            recovery_session=incomplete_session
        )

        # IMPORTANTE: Non continuare con l'altro prototipo!
        # Se una sessione è incompleta, deve riprendere dallo stesso task/prototipo
        # Non iniziare mai una nuova sessione con un altro prototipo finché la prima non è completata
        print(f"[RECOVERY] Sessione {'completata' if incomplete_session.get('all_tasks_completed') else 'incompleta'} - termino")
        return
    
    # Controlla se c'è una sessione completata da continuare
    if completed_session:
        print(f"\n{'='*70}")
        print("[CONTINUATION MODE] SESSIONE COMPLETATA TROVATA")
        print(f"{'='*70}")
        print(f"Prototype completato: {completed_session.get('prototype')}")
        print(f"Persona: {completed_session.get('persona')} (ID: {completed_session.get('persona_id')})")
        print(f"Task completati: {len([t for t in completed_session.get('tasks_completed', {}).values() if t.get('completed')])}")
        print(f"Azioni eseguite: {len(completed_session.get('actions', []))}")
        
        # Determina quale prototipo testare
        last_prototype = completed_session.get('prototype')
        next_prototype = "B" if last_prototype == "A" else "A"
        next_url = PROTOTYPE_URL_B if next_prototype == "B" else PROTOTYPE_URL_A
        
        if not next_url:
            print(f"[ERROR] URL per prototipo {next_prototype} non configurato")
            return
        
        print(f"\nContinuo con prototipo {next_prototype}")
        print(f"{'='*70}\n")
        
        # Ripristina la stessa persona
        persona = {
            'persona_id': completed_session.get('persona_id'),
            'bio': {
                'nome': completed_session.get('persona'),
                'eta': completed_session.get('persona_age')
            },
            'brief': completed_session.get('persona_brief')
        }
        all_tasks = completed_session.get('tasks', [])
        
        # Test connessione API
        if not test_api_connection():
            print("[ERROR] Connessione API fallita - aborto")
            return
        
        # Testa il prototipo successivo
        await test_prototype(persona, all_tasks, next_prototype, next_url)
        return
    
    # Modalità normale: nuova sessione
    print(f"\n{'='*70}")
    print("[NORMAL MODE] AVVIO NUOVA SESSIONE")
    print(f"{'='*70}\n")
    
    # Prepara task
    all_tasks = [TASK_1, TASK_2, TASK_3]
    all_tasks = [t for t in all_tasks if t]  # Filtra task vuoti
    
    # Carica persona
    persona = load_next_persona()
    if not persona:
        print("[ERROR] Persona non caricata")
        return
    
    # Verifica prototipi
    if not PROTOTYPE_URL_A or not PROTOTYPE_URL_B:
        print("[ERROR] PROTOTYPE_URL_A e PROTOTYPE_URL_B devono essere configurati in .env")
        return
    
    # Test connessione API
    if not test_api_connection():
        print("[ERROR] Connessione API fallita - aborto")
        return
    
    # Test entrambi i prototipi
    prototypes = [
        {"name": "A", "url": PROTOTYPE_URL_A},
        {"name": "B", "url": PROTOTYPE_URL_B}
    ]
    for prototype in prototypes:
        print(f"\n\n{'='*70}")
        print(f"TESTING PROTOTIPO {prototype['name']}")
        print(f"URL: {prototype['url']}")
        print(f"{'='*70}\n")
        # Inizializza sessione aggregata
        session_start = datetime.now()
        session_log = {
            "session_id": f"{session_start.strftime('%Y%m%d_%H%M%S')}_prototype_{prototype['name']}",
            "prototype": prototype['name'],
            "prototype_url": prototype['url'],
            "persona_id": persona.get('persona_id', 'N/A'),
            "persona": persona.get('bio', {}).get('nome', 'N/A'),
            "persona_age": persona.get('bio', {}).get('eta', 'N/A'),
            "persona_brief": persona.get('brief', 'N/A'),
            "tasks": all_tasks,
            "start_time": session_start.isoformat(),
            "end_time": None,
            "duration_seconds": 0,
            "successful_tasks": 0,
            "tasks_completed": {},
            "actions": [],
            "total_actions": 0,
            "successful_actions": 0,
            "failed_actions": 0,
            "action_types": {},
            "all_tasks_completed": False,
            "prototype_ux_surveys": {},
            "errors": []
        }
        # Esegui ogni task e aggiorna session_log
        for task_index, task in enumerate(all_tasks, 1):
            await test_prototype(persona, [task], prototype['name'], prototype['url'], session_log=session_log, task_index=task_index)
            await asyncio.sleep(2)
        # Salva sessione aggregata
        log_file = LOGS_DIR / f"session_{session_log['session_id']}_proto{prototype['name']}.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(session_log, f, ensure_ascii=False, indent=2)
        await asyncio.sleep(5)


async def test_prototype(
    persona: Dict,
    all_tasks: List[str],
    prototype_name: str,
    prototype_url: str,
    recovery_session: Optional[Dict] = None,
    session_log: Optional[Dict] = None,
    task_index: Optional[int] = None
) -> None:
    """Testa un singolo prototipo con tutti i task"""
    
    # Imposta FRAMES_LOCAL_DIR in base al prototipo
    global FRAMES_LOCAL_DIR, FRAME_NODE_INDEX
    if prototype_name == "A":
        FRAMES_LOCAL_DIR = FRAMES_LOCAL_DIR_A
    else:
        FRAMES_LOCAL_DIR = FRAMES_LOCAL_DIR_B
    
    print(f"[DEBUG] Caricando frame local da: {FRAMES_LOCAL_DIR}")
    
    # Ricostruisci l'indice per il nuovo prototipo
    FRAME_NODE_INDEX = build_frame_node_index()
    
    # Trova posizione del bottone Portate (uguale in tutti i frame)
    portate_button_pos = get_portate_button_position()
    if not portate_button_pos:
        print("[ERROR] Impossibile trovare la posizione del bottone Portate")
        return
    print(f"[INFO] Bottone Portate a: ({portate_button_pos[0]}, {portate_button_pos[1]}), h={portate_button_pos[2]})")
    
    # Inizializza o recupera log sessione
    session_start_ts = time.time()  # Timestamp per calcoli durata
    if recovery_session:
        print(f"[RECOVERY] Ripristino sessione esistente")
        session_log = recovery_session
        session_start = datetime.fromisoformat(session_log['start_time'])
        print(f"[RECOVERY] Azioni già eseguite: {len(session_log.get('actions', []))}")
    elif session_log:
        # Usa session_log aggregato passato dal main
        session_start = datetime.fromisoformat(session_log['start_time'])
    else:
        # Fallback: crea nuova sessione (solo se chiamata diretta)
        session_start = datetime.now()
        session_log = {
            "session_id": f"{session_start.strftime('%Y%m%d_%H%M%S')}_prototype_{prototype_name}",
            "prototype": prototype_name,
            "prototype_url": prototype_url,
            "persona_id": persona.get('persona_id', 'N/A'),
            "persona": persona.get('bio', {}).get('nome', 'N/A'),
            "persona_age": persona.get('bio', {}).get('eta', 'N/A'),
            "persona_brief": persona.get('brief', 'N/A'),
            "tasks": all_tasks,
            "start_time": session_start.isoformat(),
            "end_time": None,
            "duration_seconds": 0,
            "successful_tasks": 0,
            "tasks_completed": {},
            "actions": [],
            "total_actions": 0,
            "successful_actions": 0,
            "failed_actions": 0,
            "action_types": {},
            "all_tasks_completed": False,
            "prototype_ux_surveys": {},
            "errors": []
        }
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1440, "height": 1024})
        
        print(f"[*] Navigazione a {prototype_url}")
        await page.goto(prototype_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(10000)  # 10 secondi per caricamento iniziale Figma
        
        # Trova canvas
        try:
            canvas = page.locator("canvas").first
            await canvas.wait_for(state="visible", timeout=5000)
            canvas_box = await canvas.bounding_box()
        except:
            # Fallback: usa screenshot della pagina intera
            canvas_box = {
                'x': 0,
                'y': 0,
                'width': 1440,
                'height': 1024
            }
        
        if not canvas_box:
            print("[ERROR] Canvas non trovato")
            await browser.close()
            return
        
        print(f"[OK] Canvas: {canvas_box['width']:.0f}x{canvas_box['height']:.0f}")
        print(f"[OK] Canvas offset: x={canvas_box['x']:.0f}, y={canvas_box['y']:.0f}")
        
        # RECOVERY MODE: Ripristina stato browser ri-eseguendo i click
        if recovery_session:
            successful_clicks = [
                a for a in session_log.get('actions', [])
                if a.get('action_type') == 'click' and a.get('success', False)
            ]
            
            if successful_clicks:
                print(f"\n[RECOVERY] Ripristino stato browser - riesecuzione {len(successful_clicks)} click...")
                current_recovery_frame = "home"
                
                for idx, action in enumerate(successful_clicks, 1):
                    target = action.get('target_text', 'unknown')
                    frame_expected = action.get('frame_after', action.get('frame', 'unknown'))
                    print(f"[RECOVERY] Click {idx}/{len(successful_clicks)}: '{target}'")
                    
                    try:
                        # Determina frame corrente per cercare l'elemento
                        frame_to_search = action.get('frame_before') or action.get('frame') or current_recovery_frame
                        frame_data = load_frame_json(frame_to_search)
                        
                        element = None
                        if frame_data:
                            element = find_element_by_name(frame_data, target)
                        
                        # Se non trovato, prova in portate (overlay)
                        if not element and frame_to_search != 'portate':
                            portate_data = load_frame_json('portate')
                            if portate_data:
                                element = find_element_by_name(portate_data, target)
                                if element:
                                    frame_to_search = 'portate'
                        
                        if element:
                            coords = get_element_center(element)
                            if coords:
                                figma_x, figma_y = coords
                                
                                # Se in portate, calcola coordinate assolute
                                if frame_to_search == 'portate':
                                    screen_x = portate_button_pos[0] + figma_x
                                    screen_y = portate_button_pos[1] + portate_button_pos[2] + 5 + figma_y
                                else:
                                    screen_x = figma_x
                                    screen_y = figma_y
                                
                                # Coordinate assolute canvas
                                click_x = canvas_box['x'] + screen_x
                                click_y = canvas_box['y'] + screen_y
                                
                                await page.mouse.click(click_x, click_y)
                                await page.wait_for_timeout(3000)  # Attendi caricamento transizione
                                
                                # Determina frame REALE dopo il click leggendo l'URL
                                element_name_lower = element.get("name", "").lower()
                                if "portate" in element_name_lower or "filtro portate" in element_name_lower:
                                    actual_frame = "portate"
                                else:
                                    actual_frame = get_frame_from_url(page.url, current_recovery_frame)
                                
                                print(f"[RECOVERY] ✓ Click eseguito → {actual_frame}")
                                current_recovery_frame = actual_frame
                            else:
                                print(f"[RECOVERY] ⚠ Coordinate non disponibili")
                        else:
                            print(f"[RECOVERY] ⚠ Elemento '{target}' non trovato")
                    except Exception as e:
                        print(f"[RECOVERY] ⚠ Errore: {e}")
                
                # Attesa finale per assicurarsi che l'ultimo frame sia completamente caricato
                await page.wait_for_timeout(5000)
                print(f"[RECOVERY] Stato browser ripristinato!\n")
            else:
                print(f"[RECOVERY] Nessun click da ripristinare")
        
        # Raccolta globale di tutte le azioni (per survey finale)
        all_session_actions = []
        
        # Frame iniziale: se recovery ha navigato a un frame specifico, usalo
        initial_frame = "home"
        recovery_scroll_needed = 0  # Accumulatore scroll per ripristinare lo stato della pagina
        if recovery_session and session_log.get('actions'):
            for a in reversed(session_log.get('actions', [])):
                last_frame_from_log = a.get('frame_after') or a.get('frame')
                if last_frame_from_log:
                    initial_frame = last_frame_from_log
                    print(f"[DEBUG] Frame iniziale globale impostato da recovery: {initial_frame}")
                    break
            
            # Calcola scroll totale per ripristinare posizione pagina
            for a in session_log.get('actions', []):
                if a.get('action_type') == 'scroll' and a.get('success'):
                    direction = a.get('scroll_direction', 'down').lower()
                    amount = a.get('scroll_amount', 0)
                    if 'down' in direction or 'bottom' in direction:
                        recovery_scroll_needed += amount
                    elif 'up' in direction or 'top' in direction:
                        recovery_scroll_needed -= amount
            
            if recovery_scroll_needed != 0:
                print(f"[RECOVERY] Scroll cumulativo da ripristinare: {recovery_scroll_needed}px")
                # Esegue scroll di ripristino
                try:
                    await page.mouse.wheel(0, recovery_scroll_needed)
                    await page.wait_for_timeout(1000)
                    print(f"[RECOVERY] Scroll di ripristino eseguito: {recovery_scroll_needed}px")
                except Exception as e:
                    print(f"[WARN] Errore durante scroll di ripristino: {e}")
        
        # Determina da quale task iniziare
        start_task_index = 1
        if recovery_session:
            tasks_completed = session_log.get('tasks_completed', {})
            actions = session_log.get('actions', [])
            actions_by_task = {}
            for a in actions:
                idx = a.get('task_index')
                if idx is not None:
                    actions_by_task.setdefault(idx, []).append(a)
            
            for idx in range(1, len(all_tasks) + 1):
                task_key = f"task_{idx}"
                task_info = tasks_completed.get(task_key, {})
                completed_flag = task_info.get('completed', False)
                logged_actions_count = task_info.get('actions_count')
                actual_actions_count = len(actions_by_task.get(idx, []))
                
                # Incoerenza tra log e azioni -> considera NON completato
                if logged_actions_count is not None and logged_actions_count != actual_actions_count:
                    completed_flag = False
                
                if not completed_flag:
                    start_task_index = idx
                    print(f"[RECOVERY] Ripresa dal task {start_task_index}")
                    break
        
        # Loop per ogni task
        for task_index, current_task in enumerate(all_tasks, 1):
            # Salta task già completati in recovery mode
            if task_index < start_task_index:
                print(f"[RECOVERY] Skipping task {task_index} (già completato)")
                continue
            
            # Sistema di retry per task falliti
            task_retry_count = 0
            task_success = False
            
            while task_retry_count <= MAX_TASK_RETRIES and not task_success:
                if task_retry_count > 0:
                    print(f"\n{'='*60}")
                    print(f"[RETRY] TENTATIVO {task_retry_count + 1}/{MAX_TASK_RETRIES + 1} PER TASK {task_index}")
                    print(f"{'='*60}\n")
                    # Torna alla home prima di riprovare
                    current_frame = initial_frame
                else:
                    print(f"\n{'='*60}")
                    print(f"TASK {task_index}/{len(all_tasks)}: {current_task}")
                    print(f"{'='*60}\n")
            
                # Recupera azioni precedenti per questo task (se in recovery)
                previous_actions = []
                if recovery_session:
                    previous_actions = [
                        a for a in session_log.get('actions', []) 
                        if a.get('task_index') == task_index
                    ]
                    if previous_actions:
                        print(f"[RECOVERY] Azioni precedenti per questo task:")
                        print(get_actions_summary(previous_actions))
                
                task_actions = previous_actions.copy()
                action_count = len(previous_actions)
                task_completed = False
                action_attempts = {}  # Track: {"action_type|target": count}
                task_start_ts = time.time()
                task_errors: List[Dict[str, Any]] = []
                task_action_types: Dict[str, int] = {}
                task_selected_recipe = None  # Track ricetta scelta quando task completato
                task_selection_reason = None  # Track motivo della scelta
                
                # Variabili per rilevamento loop
                consecutive_scroll_attempts = 0
                last_screenshot_hash = None
                task_scroll_offset = 0  # Traccia scroll cumulativo per questo task
                last_elements_list = None  # Traccia ultima lista elementi per rilevare fondo

                # Se in recovery, ricostruisci offset di scroll dalle azioni precedenti
                if previous_actions:
                    for prev_action in previous_actions:
                        if prev_action.get("action_type") != "scroll":
                            continue
                        prev_dir = str(prev_action.get("scroll_direction", "down")).lower()
                        prev_amount_raw = prev_action.get("scroll_amount", MIN_SCROLL_DISTANCE)
                        try:
                            prev_amount = int(prev_amount_raw)
                        except (TypeError, ValueError):
                            prev_amount = MIN_SCROLL_DISTANCE
                        if "down" in prev_dir or "bottom" in prev_dir:
                            task_scroll_offset += prev_amount
                        elif "up" in prev_dir or "top" in prev_dir:
                            task_scroll_offset -= prev_amount
                    task_scroll_offset = max(0, task_scroll_offset)
                
                # Determina frame corrente dall'ultima azione
                if previous_actions:
                    current_frame = previous_actions[-1].get('frame_after') or previous_actions[-1].get('frame') or initial_frame
                else:
                    current_frame = initial_frame  # Use global initial frame (recovery-aware)
                
                print(f"[DEBUG] Frame iniziale per task {task_index}: {current_frame}")
                
                # Loop di azioni per questo task
                while action_count < MAX_ACTIONS and not task_completed:
                    action_start = time.time()
                    
                    print(f"\n--- AZIONE {action_count + 1}/{MAX_ACTIONS} (Task {task_index}) ---")
                    
                    # Screenshot corrente
                    shot = await page.screenshot()
                    img = Image.open(io.BytesIO(shot)).convert('RGB')
                    shot_path = CACHE_DIR / f"task{task_index}_action_{action_count + 1}.jpg"
                    img.save(shot_path)
                    img_b64 = image_to_base64(img)
                    
                    # Hash screenshot per rilevare se è uguale all'ultimo
                    current_screenshot_hash = hashlib.md5(img_b64.encode()).hexdigest()
                    
                    # Aggiorna frame corrente dall'URL del browser
                    # MA: Se l'ultima azione era un click/hover e ha cambiato il frame, 
                    # usa quello (perché l'overlay non cambia l'URL, solo l'UI)
                    frame_from_url = get_frame_from_url(page.url, current_frame)
                
                    if task_actions:
                        last_action = task_actions[-1]
                        if last_action.get('action_type') in ['click', 'hover']:
                            frame_after = last_action.get('frame_after')
                            if frame_after and frame_after != last_action.get('frame'):
                                # Il click ha cambiato il frame - mantieni quello
                                current_frame = frame_after
                                print(f"[DEBUG] Overlay rilevato: mantengo frame '{current_frame}' (URL rimane su '{frame_from_url}')")
                            else:
                                current_frame = frame_from_url
                                print(f"[DEBUG] Frame corrente (da URL): {current_frame}")
                        else:
                            current_frame = frame_from_url
                            print(f"[DEBUG] Frame corrente (da URL): {current_frame}")
                    else:
                        current_frame = frame_from_url
                        print(f"[DEBUG] Frame corrente (da URL): {current_frame}")
                    
                    print(f"[DEBUG] URL browser: {page.url}")

                    viewport_w = int(canvas_box.get("width", 1440))
                    viewport_h = int(canvas_box.get("height", 1024))
                    real_scroll_top = await get_viewport_scroll_top(page)
                    effective_scroll_offset = real_scroll_top if real_scroll_top is not None else task_scroll_offset
                    frame_elements = get_frame_element_names_for_llm(
                        current_frame,
                        viewport_w,
                        viewport_h,
                        scroll_offset_y=effective_scroll_offset
                    )
                    print(
                        f"[DEBUG] Elementi CLICCABILI nel frame '{current_frame}' "
                        f"(scroll_offset={effective_scroll_offset}, viewport={viewport_w}x{viewport_h}): {len(frame_elements)} elementi"
                    )
                    if frame_elements:
                        for i, elem in enumerate(frame_elements, 1):
                            print(f"       {i}. {elem}")
                    
                    # Rilevamento fondo pagina: se lista elementi è identica a quella precedente dopo scroll
                    elements_list_key = tuple(frame_elements)  # Converti in tupla per confronto
                    is_bottom_detected = False
                    if last_elements_list is not None and elements_list_key == last_elements_list:
                        print(f"[WARN] Lista elementi IDENTICA al precedente - possibile FONDO della pagina")
                        consecutive_scroll_attempts += 1
                        if consecutive_scroll_attempts >= 2:
                            is_bottom_detected = True
                            print(f"[INFO] FONDO PAGINA RILEVATO - aggiungi hint al LLM")
                    else:
                        consecutive_scroll_attempts = 0
                    last_elements_list = elements_list_key
                    
                    # Se fondo rilevato, aggiungi info agli elementi
                    if is_bottom_detected:
                        frame_elements_for_llm = list(frame_elements) + ["[FONDO PAGINA - SCEGLI TRA LE OPZIONI DISPONIBILI]"]
                    else:
                        frame_elements_for_llm = frame_elements
                    
                    # Chiedi azione + verifica completamento (UNA SOLA CHIAMATA)
                    print("[*] Chiedo al LLM: task completato? Se no, quale azione?...")
                    llm_response = await call_llm_combined(
                        img_b64,
                        persona,
                        current_task,
                        frame_elements=frame_elements_for_llm,
                        current_frame=current_frame,
                        is_final=False,
                        task_actions=task_actions
                    )
                    
                    if not llm_response:
                        error_msg = "LLM non ha fornito risposta valida"
                        print(f"[ERROR] {error_msg}")
                        # Non loggare errori Gemini nel JSON
                        session_log["failed_actions"] += 1
                        break
                    
                    # Verifica se task è completato
                    if llm_response.get("completed", False):
                        recipe_selected = llm_response.get("selected_recipe", "N/A")
                        task_selected_recipe = recipe_selected  # Salva per log task
                        task_selection_reason = llm_response.get("selection_reason", "N/A")  # Salva motivo
                        print(f"[VERIFY] TASK COMPLETATO: {llm_response.get('reason', '')}")
                        print(f"[RECIPE] Piatto scelto: {recipe_selected}")
                        print(f"[SELECTION_REASON] {task_selection_reason}")
                        # Estrai feedback UX se fornito inline (se non disponibile, skip survey finale)
                        if llm_response.get("ease_of_use"):
                            session_log["ux_surveys"][f"task_{task_index}"] = {
                                "ease_of_use": llm_response.get("ease_of_use"),
                                "feature_satisfaction": llm_response.get("feature_satisfaction"),
                                "source": "inline"
                            }
                        task_completed = True
                        session_log["successful_tasks"] = session_log.get("successful_tasks", 0) + 1
                        break
                    
                    # Se no, estrai l'azione proposta
                    llm_action = llm_response
                    action_type = llm_action.get("action", "unknown")
                    action_reason = llm_action.get("action_reason", llm_action.get("reason", ""))
                    
                    # Validazione: action_type deve essere uno dei valori validi
                    valid_actions = ["click", "scroll", "hover", "read", "back"]
                    if action_type not in valid_actions:
                        print(f"[ERROR] Azione non valida dall'LLM: '{action_type}' (valide: {valid_actions})")
                        print(f"[DEBUG] LLM response: {llm_response}")
                        
                        # Invalida cache per evitare loop infinito
                        global LAST_CACHE_KEY
                        if LAST_CACHE_KEY and LAST_CACHE_KEY in ACTION_CACHE:
                            print(f"[DEBUG] Invalidating cached response that caused validation failure")
                            del ACTION_CACHE[LAST_CACHE_KEY]
                            LAST_CACHE_KEY = None
                        
                        action_count += 1
                        session_log["errors"].append({
                            "action_number": action_count,
                            "task_index": task_index,
                            "error": "invalid_action_type",
                            "message": f"LLM ha ritornato action_type non valido: '{action_type}'",
                            "llm_response": llm_response
                        })
                        session_log["failed_actions"] += 1
                        
                        # Se troppi fallimenti di validazione consecutivi, forza scroll o back
                        if session_log["failed_actions"] >= 5:
                            print(f"[WARN] Troppi fallimenti validazione consecutivi - forzo azione 'back'")
                            llm_action = {"action": "back", "reason": "Troppi errori LLM - tentativo recupero", "completed": False}
                            action_type = "back"
                            action_reason = llm_action.get("reason", "")
                            # Reset counter
                            session_log["failed_actions"] = 0
                            # Non continuare il loop, procedi con l'azione forzata
                        else:
                            # Riprova con un nuovo tentativo
                            continue
                    
                    print(f"[LLM DECISION] {action_type.upper()}: {action_reason}")
                    
                    # Crea chiave univoca per l'azione
                    action_key = f"{action_type}|{llm_action.get('target_text', '')}"
                    action_attempts[action_key] = action_attempts.get(action_key, 0) + 1
                    
                    # ========== RILEVAMENTO LOOP SCROLL ==========
                    if action_type == "scroll":
                        # Se lo screenshot è identico al precedente = probabile loop
                        if current_screenshot_hash == last_screenshot_hash:
                            print(f"[WARN] Screenshot IDENTICO - scroll non ha cambiato nulla!")
                            consecutive_scroll_attempts += 1
                            if consecutive_scroll_attempts >= 4:
                                print(f"[ERROR] Loop scroll rilevato (4 tentativi) - FORZO task a 'completato'")
                                # Non loggare loop scroll nel JSON
                                task_completed = True
                                session_log["failed_actions"] += 1
                                break
                        else:
                            consecutive_scroll_attempts = 0
                    else:
                        consecutive_scroll_attempts = 0
                    
                    last_screenshot_hash = current_screenshot_hash

                    # Logica per prevenzione loop:
                    # - Click/hover stesso elemento: max 5 tentativi (previene loop su bottoni già aperti)
                    # - Scroll: illimitato
                    if action_type in ["click", "hover"]:
                        max_attempts = 5  # Evita loop infinito su click ripetuti
                    else:
                        max_attempts = 999  # Scroll e altre azioni illimitate
                    
                    if action_attempts[action_key] > max_attempts:
                        print(f"[WARN] Azione ripetuta {action_attempts[action_key]} volte - SKIP (max {max_attempts} per {action_type})")
                        task_errors.append({
                            "type": "repeated_action",
                            "action_number": action_count + 1,
                            "message": f"Azione ripetuta oltre il limite ({action_type})",
                            "timestamp": datetime.now().isoformat()
                        })
                        session_log["failed_actions"] += 1
                        action_count += 1
                        session_log["total_actions"] = action_count
                        continue
                    
                    if action_attempts[action_key] >= max_attempts:
                        print(f"[WARN] Tentativo {action_attempts[action_key]}/{max_attempts} per {action_type} (ULTIMA CHANCE)")
                    
                    # Leggi scroll REALE prima di eseguire l'azione
                    real_scroll_top = await get_viewport_scroll_top(page)
                    effective_scroll_offset = real_scroll_top if real_scroll_top is not None else task_scroll_offset
                    if real_scroll_top is not None and real_scroll_top != task_scroll_offset:
                        print(f"[DEBUG] Scroll reale dal browser: {real_scroll_top}px (task_scroll_offset era {task_scroll_offset}px)")
                        # Aggiorna task_scroll_offset con il valore reale
                        task_scroll_offset = real_scroll_top
                    
                    # Esegui azione e aggiorna frame se navigato
                    frame_before = current_frame
                    success, current_frame = await execute_action(page, page, canvas_box, llm_action, current_frame, portate_button_pos, effective_scroll_offset)
                    
                    # IMPORTANTE: Se il frame è cambiato (dopo click/back), resetta lo scroll offset
                    # Ogni frame ha il suo scroll indipendente
                    if current_frame != frame_before:
                        print(f"[DEBUG] Frame cambiato ({frame_before} → {current_frame}), resetto scroll_offset da {task_scroll_offset} a 0")
                        task_scroll_offset = 0
                    
                    action_duration = time.time() - action_start
                    
                    # Log azione
                    error_type = None
                    if not success:
                        if action_type == "click":
                            error_type = "click_failed"
                        elif action_type == "scroll":
                            error_type = "scroll_failed"
                        elif action_type == "read":
                            error_type = "read_failed"
                        elif action_type == "back":
                            error_type = "back_failed"
                        else:
                            error_type = "action_failed"

                    action_log = {
                        "action_number": action_count + 1,
                        "task_index": task_index,
                        "task": current_task,
                        "action_type": action_type,
                        "target_text": llm_action.get("target_text", "N/A"),
                        "frame": frame_before,
                        "frame_before": frame_before,
                        "frame_after": current_frame,
                        "success": success,
                        # Limita la durata massima di ogni azione
                        "duration_seconds": min(round(action_duration, 2), 30.0),
                        "reason": llm_action.get("reason", ""),
                        "timestamp": datetime.now().isoformat(),
                        "error_type": error_type
                    }
                    # Avvisa se la durata è stata limitata
                    if round(action_duration, 2) > 30.0:
                        print(f"[WARN] Durata azione eccessiva ({round(action_duration, 2)}s), limitata a 30s: {action_log.get('action_type', 'unknown')} - {action_log.get('target_text', '')}")
                    
                    # Aggiungi campi extra per scroll
                    if action_type == "scroll":
                        action_log["scroll_direction"] = llm_action.get("direction", "down")
                        raw_amount = llm_action.get("distance", llm_action.get("scroll_amount", MIN_SCROLL_DISTANCE))
                        try:
                            action_log["scroll_amount"] = int(raw_amount)
                        except (TypeError, ValueError):
                            action_log["scroll_amount"] = MIN_SCROLL_DISTANCE
                        
                        # Aggiorna offset di scroll per il task
                        scroll_direction = llm_action.get("direction", "down").lower()
                        scroll_amount = action_log["scroll_amount"]
                        if "down" in scroll_direction or "bottom" in scroll_direction:
                            task_scroll_offset += scroll_amount
                        elif "up" in scroll_direction or "top" in scroll_direction:
                            task_scroll_offset -= scroll_amount
                        task_scroll_offset = max(0, task_scroll_offset)  # Non può essere negativo
                    
                    task_actions.append(action_log)
                    all_session_actions.append(action_log)  # Traccia GLOBALE
                    session_log["actions"].append(action_log)

                    # Salva subito il log con l'azione appena fatta
                    log_file = LOGS_DIR / f"session_{session_log['session_id']}_proto{session_log['prototype']}.json"
                    with open(log_file, "w", encoding="utf-8") as f:
                        json.dump(session_log, f, indent=2, ensure_ascii=False)
                    
                    # Aggiorna contatori
                    if success:
                        session_log["successful_actions"] += 1
                        print(f"[SUCCESS] Azione completata! Frame attuale: {current_frame}")
                    else:
                        session_log["failed_actions"] += 1
                        print("[WARN] Azione fallita")
                        task_errors.append({
                            "type": error_type or "action_failed",
                            "action_number": action_count + 1,
                            "message": f"Azione fallita: {action_type}",
                            "timestamp": datetime.now().isoformat()
                        })
                    
                    # Conteggio per tipo
                    session_log["action_types"][action_type] = session_log["action_types"].get(action_type, 0) + 1
                    task_action_types[action_type] = task_action_types.get(action_type, 0) + 1
                    
                    action_count += 1
                    session_log["total_actions"] = action_count
                    
                    # Se l'azione era scroll, attendi extra per stabilizzazione viewport
                    if action_type == "scroll" and success:
                        await page.wait_for_timeout(1500)  # Extra wait per scroll stabilization
                    
                    # Pausa più lunga tra azioni per evitare rate limit
                    await page.wait_for_timeout(30000)  # 60 secondi tra ogni azione
                
                # Log task completato (SENZA survey per-task)
                task_end_ts = time.time()
                session_log["tasks_completed"][f"task_{task_index}"] = {
                    "task": current_task,
                    "completed": task_completed,
                    "actions_count": action_count,
                    "duration_seconds": round(task_end_ts - task_start_ts, 2),
                    "action_types": task_action_types,
                    "errors": task_errors,
                    "selected_recipe": task_selected_recipe,
                    "selection_reason": task_selection_reason if task_completed else None,
                    "retry_count": task_retry_count
                }
                
                # GESTIONE FALLIMENTO E RETRY
                if not task_completed:
                    print(f"\n{'='*60}")
                    print(f"[WARN] TASK {task_index} FALLITO (tentativo {task_retry_count + 1}/{MAX_TASK_RETRIES + 1})")
                    print(f"{'='*60}\n")
                    print(f"Task: {current_task}")
                    print(f"Azioni eseguite: {action_count}")
                    print(f"Errori: {len(task_errors)}")
                    if task_errors:
                        print(f"Ultimo errore: {task_errors[-1]}")
                    
                    task_retry_count += 1
                    
                    if task_retry_count > MAX_TASK_RETRIES:
                        # Esauriti tutti i tentativi - INTERRUZIONE
                        print(f"\n[ERROR] Esauriti {MAX_TASK_RETRIES + 1} tentativi per task {task_index} - INTERRUZIONE TEST")
                        
                        # Chiudi browser e salva log parziale
                        await browser.close()
                        
                        # Marca sessione come incompleta
                        session_log["all_tasks_completed"] = False
                        session_log["end_time"] = datetime.now().isoformat()
                        session_log["duration_seconds"] = round(time.time() - session_start_ts, 2)
                        
                        # Salva log parziale
                        log_file = LOGS_DIR / f"session_{session_log['session_id']}_proto{prototype_name}.json"
                        with open(log_file, "w", encoding="utf-8") as f:
                            json.dump(session_log, f, indent=2, ensure_ascii=False)
                        print(f"\n[INFO] Log parziale salvato: {session_log['session_id']}")
                        print(f"[INFO] Test interrotto al task {task_index}/{len(all_tasks)}")
                        return
                    else:
                        # Riprovare con il task
                        print(f"[RETRY] Riprovo task {task_index}...")
                        await page.wait_for_timeout(3000)  # Pausa prima del retry
                        continue  # Torna all'inizio del while loop
                else:
                    # Task completato con successo
                    task_success = True
                    print(f"[SUCCESS] Task {task_index} completato!")
                    break  # Esci dal while del retry
            
            # Breve pausa tra task
            await page.wait_for_timeout(2000)
        
        await browser.close()
        
        # SURVEY FINALE PER QUESTO PROTOTIPO con lista di tutte le azioni
      
        print(f"\n{'='*60}")
        print(f"RACCOLTA FEEDBACK PROTOTIPO {prototype_name}")
        print(f"{'='*60}\n")
        
        all_tasks_completed = all([v.get("completed") for v in session_log["tasks_completed"].values()])
        
        if all_tasks_completed:
            print(f"[*] Tutte le task completate! Chiedo valutazione per prototipo {prototype_name}...")
            
            # Chiama LLM con LISTA COMPLETA di azioni (SENZA screenshot)
            prototype_ux_survey = await call_llm_combined(
                "",  # Niente screenshot - solo azioni
                persona, 
                current_task="",
                frame_elements=[],
                current_frame="",
                is_final=True,
                all_actions=all_session_actions
            )
            
            session_log["prototype_ux_survey"] = prototype_ux_survey
            if prototype_ux_survey:
                print(f"\n[PROTOTYPE {prototype_name} UX FEEDBACK]")
                print(f"  Facilità d'uso: {prototype_ux_survey.get('ease_of_use', 'N/A')}/7")
                print(f"  Soddisfazione: {prototype_ux_survey.get('feature_satisfaction', 'N/A')}/7")
                print(f"  Note: {prototype_ux_survey.get('notes', 'N/A')}")
        else:
            print(f"[WARN] Non tutti i task completati - survey prototipo {prototype_name} skippato")
            session_log["prototype_ux_survey"] = {}
        
        # Finalizza log
        session_end = datetime.now()
        session_log["end_time"] = session_end.isoformat()
        session_log["duration_seconds"] = round((session_end - session_start).total_seconds(), 2)
        session_log["all_tasks_completed"] = all([v.get("completed") for v in session_log["tasks_completed"].values()])

        # Ricalcola i contatori basandosi sulle azioni salvate (fonte di verità)
        actions = session_log.get("actions", [])
        session_log["total_actions"] = len(actions)
        session_log["successful_actions"] = sum(1 for a in actions if a.get("success") is True)
        session_log["failed_actions"] = sum(1 for a in actions if a.get("success") is False)
        recomputed_action_types: Dict[str, int] = {}
        for a in actions:
            t = a.get("action_type", "unknown")
            recomputed_action_types[t] = recomputed_action_types.get(t, 0) + 1
        session_log["action_types"] = recomputed_action_types

        # Ricalcola task completati (sempre coerente)
        session_log["successful_tasks"] = sum(
            1 for v in session_log["tasks_completed"].values() if v.get("completed", False)
        )
        
        # Salva log
        # Calcola nome progressivo log: 1A, 1B, 2A, 2B...
        existing_logs = list(LOGS_DIR.glob("*.json"))
        max_number = 0
        pattern = re.compile(r"(\d+)([AB])\.json$", re.IGNORECASE)
        for log in existing_logs:
            m = pattern.search(log.name)
            if m:
                num = int(m.group(1))
                if num > max_number:
                    max_number = num
        # Alterna variante
        next_number = max_number + 1 if prototype_name == "A" else max_number
        variant = prototype_name.upper()
        # Se file esiste, incrementa
        while True:
            log_filename = f"{next_number}{variant}.json"
            log_file = LOGS_DIR / log_filename
            if not log_file.exists():
                break
            next_number += 1
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(session_log, f, indent=2, ensure_ascii=False)
        
        # Stampa riassunto
        print(f"\n{'='*60}")
        print("RIASSUNTO SESSIONE")
        print(f"{'='*60}")
        print(f"Prototype: {prototype_name}")
        print(f"Persona: {persona.get('bio', {}).get('nome', 'N/A')} ({persona.get('bio', {}).get('eta', 'N/A')} anni)")
        print(f"Durata totale: {session_log['duration_seconds']}s")
        print(f"Task completati: {session_log['successful_tasks']}/{len(all_tasks)}")
        print(f"Azioni totali: {session_log['total_actions']}")
        print(f"Azioni riuscite: {session_log['successful_actions']}")
        print(f"Azioni fallite: {session_log['failed_actions']}")
        print(f"Tipologie azioni: {session_log['action_types']}")
        print(f"\nDETTAGLI TASK:")
        for task_key, task_data in session_log["tasks_completed"].items():
            status = " COMPLETATO" if task_data["completed"] else " NON COMPLETATO"
            print(f"  {status}: {task_data['task'][:70]}... ({task_data['actions_count']} azioni)")
        print(f"\nTutti i task completati: {' SÌ' if session_log['all_tasks_completed'] else ' NO'}")
        print(f"\nLog salvato: {log_file}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
