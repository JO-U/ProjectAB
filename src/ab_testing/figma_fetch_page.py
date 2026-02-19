#recupera una pagina specifica da Figma API.

import os
import json
import re
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

FIGMA_TOKEN = os.getenv("FIGMA_TOKEN")
FIGMA_FILE_KEY = "LAO6f3Ng3utDPRYRPBq7P0"
PROTOTYPE_PAGE_A = os.getenv("PROTOTYPE_PAGE_A")
PROTOTYPE_PAGE_B = os.getenv("PROTOTYPE_PAGE_B")

OUTPUT_DIR = Path("data/test/figma_cache")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def extract_node_id_from_url(url: str):
    if not url:
        return None
    match = re.search(r"[?&]node-id=([^&]+)", url)
    if not match:
        return None
    return match.group(1).replace("-", ":")


def fetch_figma_page(node_id: str, output_subdir: str = ""):
    
    if not FIGMA_TOKEN:
        print("[ERROR] FIGMA_TOKEN non configurato nel .env")
        return None
    
    url = f"https://api.figma.com/v1/files/{FIGMA_FILE_KEY}/nodes"
    headers = {"X-Figma-Token": FIGMA_TOKEN}
    params = {"ids": node_id, "depth": 10}  
    
    print(f"[*] Recuperando pagina {node_id}...")
    print(f"[*] URL: {url}")
    print(f"[*] Params: {params}")
    
    try:
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code != 200:
            print(f"[ERROR] API error: {response.status_code}")
            print(f"[ERROR] Response: {response.text}")
            return None
        
        data = response.json()
        
        target_dir = OUTPUT_DIR / output_subdir if output_subdir else OUTPUT_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        output_file = target_dir / f"page_{node_id.replace(':', '_')}_full.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        
        print(f"[OK] Pagina salvata in: {output_file}")
        
        if "nodes" in data and node_id in data["nodes"]:
            node = data["nodes"][node_id]
            print(f"[INFO] Nome: {node.get('document', {}).get('name', 'N/A')}")
            print(f"[INFO] Tipo: {node.get('document', {}).get('type', 'N/A')}")

            def collect_frames(node, path=""):
                frames = []
                node_type = node.get("type")
                node_name = node.get("name", "")
                node_id = node.get("id", "")
                next_path = f"{path}/{node_name}" if path else node_name

                if node_type == "FRAME":
                    frames.append(
                        {
                            "id": node_id,
                            "name": node_name,
                            "type": node_type,
                            "path": next_path,
                            "absoluteBoundingBox": node.get("absoluteBoundingBox"),
                            "absoluteRenderBounds": node.get("absoluteRenderBounds"),
                        }
                    )

                for child in node.get("children", []) or []:
                    frames.extend(collect_frames(child, next_path))

                return frames
            
            def count_children(node):
                count = 0
                if "children" in node:
                    count = len(node["children"])
                    for child in node["children"]:
                        count += count_children(child)
                return count
            
            total_children = count_children(node.get("document", {}))
            print(f"[INFO] Elementi totali (ricorsivi): {total_children}")

            frames = collect_frames(node.get("document", {}))
            frames_file = target_dir / f"page_{node_id.replace(':', '_')}_frames.json"
            with open(frames_file, "w", encoding="utf-8") as f:
                json.dump(frames, f, indent=2)

            print(f"[OK] Frame trovati: {len(frames)}")
            print(f"[OK] Frame salvati in: {frames_file}")
        
        return data
        
    except Exception as e:
        print(f"[ERROR] Errore: {e}")
        return None


def main():
    print("[*] === Fetch Figma Page ===")
    print(f"[*] File Key: {FIGMA_FILE_KEY}")
    print()

    # Fetch prototipi 
    proto_a_id = extract_node_id_from_url(PROTOTYPE_PAGE_A) if PROTOTYPE_PAGE_A else None
    proto_b_id = extract_node_id_from_url(PROTOTYPE_PAGE_B) if PROTOTYPE_PAGE_B else None

    if proto_a_id:
        print()
        print(f"[*] Prototype A node-id: {proto_a_id}")
        fetch_figma_page(proto_a_id, output_subdir="prototype_a")
    else:
        print("[WARN] PROTOTYPE_PAGE_A non configurato o node-id non trovato")

    if proto_b_id:
        print()
        print(f"[*] Prototype B node-id: {proto_b_id}")
        fetch_figma_page(proto_b_id, output_subdir="prototype_b")
    else:
        print("[WARN] PROTOTYPE_PAGE_B non configurato o node-id non trovato")

    print()
    print("[SUCCESS] Recupero completato!")


if __name__ == "__main__":
    main()
