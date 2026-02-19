"""
Script per normalizzare le coordinate di ogni frame a (0,0) locale.
Trasforma absoluteBoundingBox in coordinate locali relative al frame.
"""

import json
from pathlib import Path
from typing import Dict, Any

# Path dei JSON originali
OUTPUT_DIR = Path("data/test/figma_cache/frames_local")
OUTPUT_DIR_A = Path("data/test/figma_cache/frames_local_a")
OUTPUT_DIR_B = Path("data/test/figma_cache/frames_local_b")

PROTOTYPE_DIRS = {
    "prototype_a": Path("data/test/figma_cache/prototype_a"),
    "prototype_b": Path("data/test/figma_cache/prototype_b"),
}

# Frame IDs per prototipo A
FRAME_IDS_A = {
    "home": "1120:2350",
    "all": "1155:4205",
    "antipasti": "1194:3563",
    "primi": "194:4190",
    "secondi": "1194:4372",
    "dolci": "1194:4593",
    "ricetta": "1194:4817",
    "portate": "1194:4705",
}

# Frame IDs per prototipo B
FRAME_IDS_B = {
    "home": "1384:11891",
    "all": "1384:11908",
    "antipasti": "1384:12263",
    "primi": "1384:12352",
    "secondi": "1384:12461",
    "dolci": "1384:12571",
    "ricetta": "1384:12705",
    "portate": "1384:12670",
}


def find_element_by_id(node: Dict[str, Any], target_id: str) -> Dict[str, Any] | None:
    if node.get("id") == target_id:
        return node
    
    for child in node.get("children", []):
        result = find_element_by_id(child, target_id)
        if result:
            return result
    return None


def normalize_coordinates_recursive(node: Dict[str, Any], offset_x: float, offset_y: float) -> None:
    bbox = node.get("absoluteBoundingBox")
    if bbox and isinstance(bbox, dict):
        # Sottrai offset per ottenere coordinate locali
        bbox["x"] = bbox.get("x", 0) - offset_x
        bbox["y"] = bbox.get("y", 0) - offset_y
    
    # Normalizza anche absoluteRenderBounds se presente
    render_bounds = node.get("absoluteRenderBounds")
    if render_bounds and isinstance(render_bounds, dict):
        render_bounds["x"] = render_bounds.get("x", 0) - offset_x
        render_bounds["y"] = render_bounds.get("y", 0) - offset_y
    
    # Ricorsione sui children
    for child in node.get("children", []):
        normalize_coordinates_recursive(child, offset_x, offset_y)


def normalize_frame(figma_doc: Dict[str, Any], frame_id: str, frame_name: str) -> Dict[str, Any] | None:
    # Trova il frame
    frame = find_element_by_id(figma_doc, frame_id)
    if not frame:
        print(f"[WARN] Frame '{frame_name}' ({frame_id}) non trovato")
        return None
    
    # Ottieni offset del frame
    frame_bbox = frame.get("absoluteBoundingBox")
    if not frame_bbox:
        print(f"[WARN] Frame '{frame_name}' non ha bounding box")
        return None
    
    frame_x = frame_bbox.get("x", 0)
    frame_y = frame_bbox.get("y", 0)
    
    print(f"[INFO] Frame '{frame_name}': offset ({frame_x}, {frame_y})")
    
    # Crea copia del frame per non modificare l'originale
    import copy
    frame_local = copy.deepcopy(frame)
    
    # Normalizza il frame stesso e tutti i children
    normalize_coordinates_recursive(frame_local, frame_x, frame_y)
    
    return frame_local


def main():
    print("[*] === Normalize Frame Coordinates ===\n")
    
    # Crea directory output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def get_first_node_key(data_obj: Dict[str, Any]) -> str | None:
        nodes = data_obj.get("nodes", {})
        return next(iter(nodes.keys()), None) if isinstance(nodes, dict) else None

    def process_json_file(json_path: Path, prototype_name: str, output_dir: Path):
        if not json_path.exists():
            print(f"[WARN] File JSON non trovato: {json_path}")
            return

        # Scegli gli ID corretti in base al prototipo
        frame_ids = FRAME_IDS_A if prototype_name == "prototype_a" else FRAME_IDS_B

        print(f"[*] Caricamento JSON da: {json_path}")
        print(f"[*] Usando frame IDs per: {prototype_name}")
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        node_key = get_first_node_key(data)
        if not node_key:
            print(f"[WARN] Nessun nodo trovato in: {json_path}")
            return

        # Estrai documento
        node_entry = data.get("nodes", {}).get(node_key)
        if not node_entry or "document" not in node_entry:
            print(f"[WARN] Nodo '{node_key}' non valido o senza document in: {json_path}")
            return
        figma_doc = node_entry["document"]

        # Output directory specifica per prototipo
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] Salvando in: {output_dir}")

        # Normalizza ogni frame
        for frame_name, frame_id in frame_ids.items():
            print(f"\n[*] Normalizzo frame: {frame_name} ({frame_id})")

            frame_local = normalize_frame(figma_doc, frame_id, frame_name)

            if frame_local:
                output_path = output_dir / f"{frame_name}_local.json"
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(frame_local, f, indent=2)

                print(f"[OK] Salvato: {output_path}")

                #esmpio coordinate
                if frame_local.get("children"):
                    first_child = frame_local["children"][0]
                    child_bbox = first_child.get("absoluteBoundingBox", {})
                    print(f"     Esempio: '{first_child.get('name')}' → ({child_bbox.get('x')}, {child_bbox.get('y')})")

        print(f"\n[SUCCESS] Frame normalizzati in: {output_dir}")

    # output directories separate
    for proto_label, proto_dir in PROTOTYPE_DIRS.items():
        output_dir = OUTPUT_DIR_A if proto_label == "prototype_a" else OUTPUT_DIR_B
        full_files = sorted(proto_dir.glob("page_*_full.json"))
        if not full_files:
            print(f"[WARN] Nessun file full trovato in: {proto_dir}")
            continue
        for full_file in full_files:
            print(f"\n[*] === {proto_label.upper()} ===")
            process_json_file(full_file, proto_label, output_dir)


if __name__ == "__main__":
    main()
