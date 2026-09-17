"""Plan view: source section and hull sections at a given z, saved as PNG."""
import sys, numpy as np
from PIL import Image, ImageDraw
mesh, hulls_file, z0, cx, cy, half, out = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]), float(sys.argv[6]), sys.argv[7]
S = 1000
img = Image.new("RGB", (S, S), (20, 20, 24)); d = ImageDraw.Draw(img)
def to_px(p):
    return ((p[0] - (cx - half)) / (2*half) * S, S - (p[1] - (cy - half)) / (2*half) * S)
def section(v, f, colour, width=1):
    segs = []
    for t in f:
        p = v[t]; dz = p[:, 2] - z0
        pts = [p[i] + (p[(i+1)%3]-p[i]) * (dz[i]/(dz[i]-dz[(i+1)%3])) for i in range(3) if dz[i]*dz[(i+1)%3] < 0]
        if len(pts) == 2: segs.append(pts)
    for a, b in segs:
        d.line([to_px(a), to_px(b)], fill=colour, width=width)
    return len(segs)
m = np.load(mesh); section(m["v"], m["f"], (255, 255, 255), 3)
H = np.load(hulls_file)
palette = [(255,90,90),(90,255,120),(120,160,255),(255,220,80),(255,120,255),(120,255,255),(255,160,60),(180,120,255)]
count = 0
for i in range(len(H.files)//2):
    v = H["v%d"%i]
    if v[:,2].max() < z0 or v[:,2].min() > z0: continue
    if np.any(v[:, :2].min(0) > [cx+half, cy+half]) or np.any(v[:, :2].max(0) < [cx-half, cy-half]): continue
    if section(v, H["f%d"%i], palette[count % len(palette)], 1): count += 1
print("hull sections drawn:", count)
img.save(out)
