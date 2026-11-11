"""Tile a test case's 8 reference views into one contact sheet (for visual comparison)."""
import sys
from pathlib import Path
from PIL import Image
case, out = sys.argv[1], sys.argv[2]
size = int(sys.argv[3]) if len(sys.argv) > 3 else 640
d = Path(__file__).resolve().parents[1] / "artifacts/inputs" / case / "images"
files = sorted(d.glob("view_*.png"))
sheet = Image.new("RGB", (size * 4, size * 2), "white")
for i, f in enumerate(files):
    sheet.paste(Image.open(f).convert("RGB").resize((size, size), Image.LANCZOS), ((i % 4) * size, (i // 4) * size))
sheet.save(out)
