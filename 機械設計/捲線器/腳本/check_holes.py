import sys, os, math
sys.path.insert(0, r"C:\Users\123\.claude\skills\solidworks-automation\scripts"); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_assembly import *


def planar_circles(model, plane_axis=2, at=0.0, rmax=25):
    out = set()
    for body in model.GetBodies2(0, True):
        for e in get_com_member(body, "GetEdges"):
            c = get_com_member(e, "GetCurve")
            if c and get_com_member(c, "IsCircle"):
                p = get_com_member(c, "CircleParams")
                v = [round(p[i] * 1000, 3) for i in range(3)] + [round(p[6] * 1000, 3)]
                if abs(v[plane_axis] - at) < 1e-3 and v[3] < rmax:
                    out.add(tuple(v))
    return sorted(out)


def verify(path, expected, plane_axis=2, at=0.0, tol=0.01):
    """expected: list of (x, y, r) on the face; returns list of problems."""
    s = SolidWorksSession(version=2024, max_documents=60)
    m = s.open(path)
    found = planar_circles(m, plane_axis, at)
    probs = []
    for ex, ey, er in expected:
        if not any(abs(f[0] - ex) < tol and abs(f[1] - ey) < tol and abs(f[3] - er) < tol for f in found):
            probs.append((ex, ey, er))
    name = os.path.basename(path)
    print(f"{name}: {len(expected) - len(probs)}/{len(expected)} holes at expected positions")
    for p in probs:
        print("   MISSING/SHIFTED expected", p)
    if probs:
        print("   found:", [f for f in found])
    return probs


def pcd(d, n, start, cy=0.0, r=0.0):
    return [(round(d / 2 * math.cos(math.radians(start + 360 * i / n)), 3),
             round(cy + d / 2 * math.sin(math.radians(start + 360 * i / n)), 3), r) for i in range(n)]
