import os
import re
import random
from pathlib import Path
from typing import Dict, List, Any

KB_DIR = Path(__file__).resolve().parent.parent / "ref" / "kb"

# Categories mapping to filenames
CATEGORY_FILES = {
    "artist": "kb1_styles_artists.md",
    "character": "kb2_characters.md",
    "clothing": "kb3_races_clothing.md",
    "scene": "kb4_scenes_actions.md",
    # kb5 is excluded per spec
}

KB_INDEX: Dict[str, List[Dict[str, Any]]] = {}

def build_kb_index():
    global KB_INDEX
    KB_INDEX.clear()
    
    if not KB_DIR.exists():
        return
        
    for cat, filename in CATEGORY_FILES.items():
        KB_INDEX[cat] = []
        filepath = KB_DIR / filename
        if not filepath.exists():
            continue
            
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            
        current_entry = None
        current_lines = []
        
        for i, line in enumerate(lines):
            # Parse headings
            match = re.match(r"^##*\s+(.+)$", line.strip())
            if match:
                # Save previous entry
                if current_entry and current_lines:
                    current_entry["content_preview"] = "\n".join([l.strip() for l in current_lines[:5] if l.strip() and not l.strip().startswith("<")])
                    KB_INDEX[cat].append(current_entry)
                
                title = match.group(1).strip()
                current_entry = {
                    "line": i + 1,
                    "title": title,
                }
                current_lines = []
            elif current_entry is not None:
                if line.strip() and not line.startswith("#"):
                    current_lines.append(line.strip())
        
        # Save last entry
        if current_entry and current_lines:
            current_entry["content_preview"] = "\n".join([l.strip() for l in current_lines[:5] if l.strip() and not l.strip().startswith("<")])
            KB_INDEX[cat].append(current_entry)

def draw_random(categories: List[str], count: int = 1) -> Dict[str, List[Dict[str, Any]]]:
    if not KB_INDEX:
        build_kb_index()
        
    result = {}
    if "all" in categories:
        target_cats = list(CATEGORY_FILES.keys())
    else:
        target_cats = [c for c in categories if c in CATEGORY_FILES]
        
    for cat in target_cats:
        items = KB_INDEX.get(cat, [])
        if not items:
            continue
        try:
            sampled = random.sample(items, min(count, len(items)))
        except ValueError:
            sampled = items
        result[cat] = sampled
        
    return result
