"""Quality summary of a hull set: count, triangles, width distribution, strips."""
import sys, numpy as np
H = np.load(sys.argv[1]); n = len(H.files)//2
W = []; L = []; T = 0
for i in range(n):
    v, f = H["v%d"%i], H["f%d"%i]; T += len(f)
    nrm = np.cross(v[f[:,1]]-v[f[:,0]], v[f[:,2]]-v[f[:,0]]); ar = np.linalg.norm(nrm,axis=1)
    nn = nrm[ar>1e-6]/ar[ar>1e-6,None]
    W.append(min(np.ptp(v@x) for x in nn) if len(nn) else 0); L.append(np.ptp(v, axis=0).max())
W = np.array(W); L = np.array(L)
print("hulls %d tris %d | width<5cm %d <10cm %d <20cm %d <50cm %d | strips (>10 m long, <20 cm) %d" % (
    n, T, (W<0.05).sum(), (W<0.1).sum(), (W<0.2).sum(), (W<0.5).sum(), ((L>10)&(W<0.2)).sum()))
