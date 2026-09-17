"""Hull facets vs source: grid samples pushed 5 mm inward along facet normal; exact distance."""
import sys, numpy as np
d = np.load(sys.argv[1]); sv, sf = d["v"], d["f"]
A = sv[sf[:,0]]; B = sv[sf[:,1]]; C = sv[sf[:,2]]
probe = float(sys.argv[3]) if len(sys.argv) > 3 else 5e-3
def winding(P):
    out=[]
    for s in range(0,len(P),300):
        q = P[s:s+300][:,None,:]; x, y, z = A[None]-q, B[None]-q, C[None]-q
        lx, ly, lz = [np.sqrt((u*u).sum(2)) for u in (x,y,z)]
        det = (x*np.cross(y,z)).sum(2); den = lx*ly*lz + (x*y).sum(2)*lz + (y*z).sum(2)*lx + (z*x).sum(2)*ly
        out.append(np.arctan2(det, den).sum(1)/(2*np.pi))
    return np.concatenate(out) if out else np.zeros(0)
def point_tri_dist(p):
    # exact distance from p to all source triangles (vectorized, Ericson)
    ab = B - A; ac = C - A; ap = p - A
    d1 = (ab*ap).sum(1); d2 = (ac*ap).sum(1)
    n = np.cross(ab, ac); nn = (n*n).sum(1)
    # barycentric projection
    t = np.linalg.solve if False else None
    d00 = (ab*ab).sum(1); d01 = (ab*ac).sum(1); d11 = (ac*ac).sum(1)
    denom = d00*d11 - d01*d01
    v = (d11*d1 - d01*d2)/np.maximum(denom,1e-18); w = (d00*d2 - d01*d1)/np.maximum(denom,1e-18); u = 1 - v - w
    inside = (u >= 0) & (v >= 0) & (w >= 0)
    proj = A + ab*v[:,None] + ac*w[:,None]
    dist = np.where(inside, np.linalg.norm(p - proj, axis=1), np.inf)
    def seg(P0, P1):
        e = P1 - P0; tt = np.clip(((p - P0)*e).sum(1)/np.maximum((e*e).sum(1),1e-18), 0, 1)
        return np.linalg.norm(p - (P0 + e*tt[:,None]), axis=1)
    return float(min(dist.min(), seg(A,B).min(), seg(B,C).min(), seg(C,A).min()))
H = np.load(sys.argv[2]); n = len(H.files)//2
S, O = [], []
widths = {}
for i in range(n):
    v, f = H["v%d"%i], H["f%d"%i]
    nrm = np.cross(v[f[:,1]]-v[f[:,0]], v[f[:,2]]-v[f[:,0]]); ar = np.linalg.norm(nrm,axis=1)
    longest = np.max([np.linalg.norm(v[f[:,1]]-v[f[:,0]],axis=1), np.linalg.norm(v[f[:,2]]-v[f[:,1]],axis=1), np.linalg.norm(v[f[:,0]]-v[f[:,2]],axis=1)], axis=0)
    ok = ar / np.maximum(longest, 1e-12) >= 0.01   # facet height >= 1 cm: reliable normal
    vol = np.einsum('ij,ij->i', v[f[:,0]], np.cross(v[f[:,1]], v[f[:,2]])).sum()/6
    nn = nrm[ok]/ar[ok,None] * (1.0 if vol > 0 else -1.0)
    widths[i] = min(np.ptp(v@x) for x in nn) if len(nn) else 0
    if widths[i] < 0.05: continue
    corners = v[f[ok]]
    g = []
    for a in range(5):
        for b in range(5-a):
            g.append(corners[:,0]*(1-(a+b)/4) + corners[:,1]*a/4 + corners[:,2]*b/4)
    pts = np.vstack(g) - np.tile(nn, (len(g),1))*probe
    # keep samples inside their own hull (all hull planes)
    hn = nrm[ar > 1e-9]/ar[ar > 1e-9,None] * (1.0 if vol > 0 else -1.0)
    ho = np.einsum('ij,ij->i', hn, v[f[ar > 1e-9, 0]])
    keep = np.all(pts @ hn.T - ho <= 1e-6, axis=1)
    pts = pts[keep]
    S.append(pts); O.append(np.full(len(pts), i))
P = np.vstack(S); Ow = np.concatenate(O)
w = winding(P); out = w < 0.5
print("hulls", n, "checked", len(set(Ow)), "samples", len(P), "outside", int(out.sum()), "hulls with outside", len(set(Ow[out])))
rows = []
for i in sorted(set(Ow[out])):
    pts = P[out & (Ow == i)]
    dist = max(point_tri_dist(p) for p in pts[:50])
    rows.append((dist, i, len(pts), pts[0]))
for dist, i, k, p0 in sorted(rows, reverse=True)[:15]:
    print("  hull %d width %.3f outside samples %d, max TRUE distance outside source %.4f m at %s" % (i, widths[i], k, dist, p0.round(2).tolist()))
