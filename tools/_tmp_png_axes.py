from pathlib import Path
from PIL import Image, ImageDraw

path = Path(r"d:\VfSimulator\results\cce_IPC\_axes.png")
img = Image.new("RGB", (1400, 700), "white")
draw = ImageDraw.Draw(img)
draw.line((60, 640, 1340, 640), fill="#111827", width=2)
draw.line((60, 60, 60, 640), fill="#111827", width=2)
img.save(path)
print(path)
