"""Accuracy of a UCX set = union of conv(vertices) of each hull, vs the closed source.
Source inside-test: generalized winding number. Hull inside-test: qhull Delaunay."""
import sys, time, argparse
import numpy as np
from scipy.spatial import Delaunay, QhullError
from collections import Counter
ap = argparse.ArgumentParser()
ap.add_argument("mesh"); ap.add_argument("hulls"); ap.add_argument("--n", type=int, default=400000)
ap.add_argument("--save", default=None)
a = ap.parse_args()
d = np.load(a.mesh); sv, sf = d["v"], d["f"]
H = np.load(a.hulls); hulls = [(H["v%d"%i], H["f%d"%i]) for i in range(len(H.files)//2)]
lo, hi = sv.min(0)-0.5, sv.max(0)+0.5
box = np.prod(hi-lo)
P = np.random.default_rng(7).uniform(lo, hi, size=(a.n,3))
t=time.time()
A = sv[sf[:,0]].astype(np.float64); B = sv[sf[:,1]]; C = sv[sf[:,2]]
w = np.zeros(len(P))
for s0 in range(0, len(P), 1500):
    q = P[s0:s0+1500][:,None,:]
    x, y, z = A[None]-q, B[None]-q, C[None]-q
    lx, ly, lz = [np.sqrt((u*u).sum(2)) for u in (x,y,z)]
    det = (x * np.cross(y, z)).sum(2)
    den = lx*ly*lz + (x*y).sum(2)*lz + (y*z).sum(2)*lx + (z*x).sum(2)*ly
    w[s0:s0+1500] = np.arctan2(det, den).sum(1)/(2*np.pi)
inS = np.abs(w) > 0.5
cnt = np.zeros(len(P), np.int32)
for v, f in hulls:
    m = np.all((P >= v.min(0)-1e-9) & (P <= v.max(0)+1e-9), axis=1)
    idx = np.where(m)[0]
    if len(idx) == 0 or len(v) < 4: continue
    try:
        tri = Delaunay(v)
    except QhullError:
        tri = Delaunay(v, qhull_options="QJ")
    cnt[idx[tri.find_simplex(P[idx]) >= 0]] += 1
inH = cnt > 0
def vol(v,f):
    X,Y,Z = v[f[:,0]],v[f[:,1]],v[f[:,2]]
    return abs(np.einsum('ij,ij->i', X, np.cross(Y,Z)).sum()/6)
cell = box/a.n
print("hulls %d tris %d | source vol %.1f, sum hull mesh vol %.1f | sample cell %.3f m3 | %.0fs" % (
    len(hulls), sum(len(f) for _,f in hulls), vol(sv,sf), sum(vol(*h) for h in hulls), cell, time.time()-t))
exc = inH & ~inS; dfc = inS & ~inH
print("EXCESS (collision where source is empty): %.2f m3 (%.3f%%)" % (exc.sum()*cell, 100*exc.sum()/max(inS.sum(),1)))
print("DEFICIT (source not covered):             %.2f m3 (%.3f%%)" % (dfc.sum()*cell, 100*dfc.sum()/max(inS.sum(),1)))
print("OVERLAP (inside >=2 hulls):               %.2f m3" % ((cnt>1).sum()*cell))
for lab, m in (("EXCESS", exc), ("DEFICIT", dfc)):
    c = Counter(map(tuple, np.floor(P[m]/2.0).astype(int)*2))
    print(lab, "top 2m cells [x,y,z]:", [(tuple(int(u) for u in k), round(n*cell,2)) for k,n in c.most_common(6)])
if a.save: np.savez(a.save, excess=P[exc], deficit=P[dfc])
