from pathlib import Path
from PIL import Image

path = Path(r"d:\VfSimulator\results\cce_IPC\_smoke.png")
img = Image.new("RGB", (1400, 700), "white")
img.save(path)
print(path)
