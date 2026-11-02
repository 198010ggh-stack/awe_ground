"""Build the 3D-printed bench-rig parts (pulley sheave + pulley stand) in SolidWorks.

Design: 機械設計/臺架/文檔/臺架設計文檔_2026-10-30.md
Part frames are chosen so the exported STL already lies in its print orientation (model +Z up):
  sheave  - axis along Z, faces at z = 0 and 15 (or -15..0)
  stand   - (v2) X along the line from the outer face of the winch's near 2020 rail (table edge at x = 35),
            Y along the table edge, Z up from the table top.
Run as build_rig.py [sheave|stand].
"""
import math
import os
import sys

sys.path.insert(0, r"C:\Users\123\.claude\skills\solidworks-automation\scripts")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "捲線器", "腳本"))
from sw_connect import mm  # noqa: E402
from sw_part import (sketch_circle, sketch_corner_rectangle, sketch_line, extrude_boss,  # noqa: E402
                     _ensure_sketch_selected, _select_by_id)
from sw_session import SolidWorksSession  # noqa: E402
from build_parts import sketch, feature_checked, body_box, volume_cm3, check, near, extrude_mid, cut_midplane  # noqa: E402

OUT = r"D:\123\Desktop\aweground\機械設計\臺架"

# ---- parameters (mm), from the design spec ----
# sheave
SHEAVE_D, SHEAVE_W = 52.0, 15.0
GROOVE_ROOT_D, GROOVE_RIM = 40.0, 1.5         # 90° V groove, depth (52-40)/2 = 6, rim flats 1.5 each side
BEARING_D, BEARING_W = 22.0, 7.0              # 608ZZ seats from both faces
LIP_D = 19.0                                  # 1 mm shoulder between the bearings, bears on the outer ring only
# stand v2: bolted to the winch's near rail, sits on the table (z from the table top)
FLANGE_T, FLANGE_Y, FLANGE_H = 8.0, 50.0, 20.0        # upright flange bearing on the rail's outer face, y ±50, as tall as the rail
RAIL_HOLE_Y, RAIL_HOLE_Z, RAIL_HOLE_D = 40.0, 10.0, 5.5   # 2 x M5 x 16 into hammer T-nuts in the rail's side slot
PAD_X1, PAD_Y0, PAD_Y1, PAD_T = 35.0, 16.3, 31.0, 8.0     # feet beside the cheeks; front edge flush with the table edge;
                                                          # stop at y 31 so the Ø15 washer under each M5 head clears them
CHEEK_T, CHEEK_GAP = 8.0, 16.6                # gap = sheave 15 + 2 x boss 0.8; open underneath so the sheave goes in from below
AXLE_X, AXLE_Z, AXLE_D = 30.0, 39.0, 8.4      # line at table z 59.7 = 39 + 20.7 (line centre in the V root); drum line 59.5
BOSS_D, BOSS_H = 11.0, 0.8                    # bears on the 608 inner ring only
KEEPER_R, KEEPER_ANGLE, KEEPER_D = 29.3, 45.0, 5.0   # printed Ø5 bar between the cheeks, surface 0.8 mm outside the sheave rim
CHEEK_PROFILE = [(0.0, 0.0), (58.0, 0.0), (58.0, 60.0), (50.0, 69.0), (22.0, 69.0), (0.0, 36.0)]


def polygon_area_centroid(pts):
    """Shoelace area and centroid (cx, cy) of a closed polygon."""
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    a /= 2
    return abs(a), cx / (6 * a), cy / (6 * a)


def sketch_polygon(model, pts):
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        sketch_line(model, mm(x0), mm(y0), mm(x1), mm(y1))


def delete_orphan_sketches(model):
    """Delete sketches left behind by rejected trial features (sketches that no feature consumes)."""
    orphans, feat = [], model.FirstFeature
    while feat is not None:
        if feat.GetTypeName2 == "ProfileFeature" and not (feat.GetChildren or ()):
            orphans.append(feat.Name)
        feat = feat.GetNextFeature
    for name in orphans:
        check(_select_by_id(model.Extension, name, "SKETCH"), f"cannot select {name}")
        model.EditDelete()
        print(f"  deleted unused {name}")


def revolve(model, sketch_ref):
    """Full 360° revolve boss about the single centerline in the sketch."""
    _ensure_sketch_selected(model, sketch_ref)
    return model.FeatureManager.FeatureRevolve2(
        True, True, False, False, False, False, 0, 0, 2 * math.pi, 0,
        False, False, 0.0, 0.0, 0, 0, 0, True, True, True)


# ---------------- parts ----------------
def build_sheave(session):
    model = session.new_part()
    print("[滑輪]")
    r_rim, r_root, r_bore, r_lip = SHEAVE_D / 2, GROOVE_ROOT_D / 2, BEARING_D / 2, LIP_D / 2
    mid = SHEAVE_W / 2
    # half cross-section (r, axial); the V flanks are 45°, so depth = r_rim - r_root = mid - GROOVE_RIM
    check(near(r_rim - r_root, mid - GROOVE_RIM), "groove is not 90°")
    profile = [(r_bore, 0.0), (r_rim, 0.0), (r_rim, GROOVE_RIM), (r_root, mid), (r_rim, SHEAVE_W - GROOVE_RIM),
               (r_rim, SHEAVE_W), (r_bore, SHEAVE_W), (r_bore, BEARING_W + 1.0), (r_lip, BEARING_W + 1.0),
               (r_lip, BEARING_W), (r_bore, BEARING_W)]
    area, r_c, _ = polygon_area_centroid(profile)
    expected = 2 * math.pi * r_c * area / 1000     # Pappus
    # Top Plane: sketch y maps to model ±Z, so the sheave axis is model Z either way
    with sketch(model, "Top Plane") as sk:
        sketch_polygon(model, profile)
        model.SketchManager.CreateCenterLine(0, mm(-5), 0, 0, mm(5), 0)
    check(revolve(model, sk) is not None, "sheave revolve failed")
    box, vol = body_box(model), volume_cm3(model)
    print(f"  revolve: box={box} vol={vol} expected={expected:.3f}")
    check(near(box[0], -r_rim) and near(box[3], r_rim) and near(box[5] - box[2], SHEAVE_W)
          and abs(vol - expected) < 0.01, "sheave geometry mismatch")
    return model


def build_stand(session):
    model = session.new_part()
    print("[滑輪座 v2]")

    # 1) flange on Front Plane (XY), x 0..FLANGE_T, height +Z 0..FLANGE_H
    with sketch(model, "Front Plane") as sk:
        sketch_corner_rectangle(model, 0, mm(-FLANGE_Y), mm(FLANGE_T), mm(FLANGE_Y))
    v_flange = FLANGE_T * 2 * FLANGE_Y * FLANGE_H / 1000
    feature_checked(model, lambda d: extrude_boss(model, sk, mm(FLANGE_H), direction=d),
                    lambda b, v: b and near(b[2], 0) and near(b[5], FLANGE_H) and abs(v - v_flange) < 0.01, "flange")

    # 2) two feet on the table beside the cheeks (nothing between the cheeks, so the sheave fits in from below)
    with sketch(model, "Front Plane") as psk:
        for s in (-1, 1):
            sketch_corner_rectangle(model, mm(FLANGE_T), mm(s * PAD_Y0), mm(PAD_X1), mm(s * PAD_Y1))
    v_pads = 2 * (PAD_X1 - FLANGE_T) * (PAD_Y1 - PAD_Y0) * PAD_T / 1000
    feature_checked(model, lambda d: extrude_boss(model, psk, mm(PAD_T), direction=d),
                    lambda b, v: b and near(b[2], 0) and near(b[5], FLANGE_H) and abs((v - v_flange) - v_pads) < 0.01,
                    "feet")

    # 3) cheeks: side profile on Top Plane (XZ), mid-plane slab of both cheeks, then cut the gap between them
    #    (the gap cut skips the flange so the flange stays one piece across the gap)
    outer = CHEEK_GAP + 2 * CHEEK_T
    a_p, _, _ = polygon_area_centroid(CHEEK_PROFILE)
    gap_profile = ([(FLANGE_T, 0.0)] + CHEEK_PROFILE[1:] + [(0.0, FLANGE_H), (FLANGE_T, FLANGE_H)])
    a_q, _, _ = polygon_area_centroid(gap_profile)
    z_top = max(z for _, z in CHEEK_PROFILE)
    v0 = volume_cm3(model)
    slab_add = (a_p * outer - FLANGE_T * FLANGE_H * outer) / 1000   # the flange block inside the slab already exists
    zsign = None
    for sign in (1, -1):
        with sketch(model, "Top Plane") as tsk:
            sketch_polygon(model, [(x, sign * z) for x, z in CHEEK_PROFILE])
        feat = extrude_mid(model, tsk, mm(outer))
        if feat is not None:
            box, v = body_box(model), volume_cm3(model)
            # x check guards against a mirrored Top Plane sketch x axis (cheeks must reach out to +58)
            if (near(box[0], 0) and near(box[3], CHEEK_PROFILE[1][0]) and near(box[2], 0)
                    and near(box[5], z_top) and abs((v - v0) - slab_add) < 0.05):
                print(f"  cheek slab ok (sketch sign {sign}) box={box} vol {v0}->{v}")
                zsign = sign
                break
            print(f"  cheek slab sign {sign} rejected box={box} vol {v0}->{v}")
            model.EditUndo2(1)
        model.EditUndo2(1)
        model.ForceRebuild3(False)
    check(zsign is not None, "cheek slab failed")

    def p(x, z):
        return mm(x), mm(zsign * z)

    def side_sketch(draw):
        with sketch(model, "Top Plane") as s2:
            draw()
        return s2

    def cut_step(sk2, depth, removed, label):
        before = volume_cm3(model)
        check(cut_midplane(model, sk2, mm(depth)) is not None, f"{label} failed")
        after = volume_cm3(model)
        print(f"  {label}: vol {before}->{after} (expected -{removed:.3f})")
        check(abs((before - after) - removed) < 0.05, f"{label} volume mismatch")

    gap = side_sketch(lambda: sketch_polygon(model, [(x, zsign * z) for x, z in gap_profile]))
    cut_step(gap, CHEEK_GAP, a_q * CHEEK_GAP / 1000, "cheek gap cut")

    def add_step(sk2, depth, added, label):
        before = volume_cm3(model)
        check(extrude_mid(model, sk2, mm(depth)) is not None, f"{label} failed")
        after = volume_cm3(model)
        print(f"  {label}: vol {before}->{after} (expected +{added:.3f})")
        check(abs((after - before) - added) < 0.05, f"{label} volume mismatch")

    # 4) Ø11 x 0.8 bosses on the cheek inner faces: fill the gap with a Ø11 disc, then cut all but 0.8 per side
    boss_area = math.pi * (BOSS_D / 2) ** 2
    bsk = side_sketch(lambda: sketch_circle(model, *p(AXLE_X, AXLE_Z), mm(BOSS_D / 2)))
    add_step(bsk, CHEEK_GAP, boss_area * CHEEK_GAP / 1000, "boss slab")
    bcut = side_sketch(lambda: sketch_circle(model, *p(AXLE_X, AXLE_Z), mm(BOSS_D / 2)))
    cut_step(bcut, SHEAVE_W, boss_area * SHEAVE_W / 1000, "boss middle cut")

    # 5) printed keeper bar across the gap at 45° above the outgoing side (replaces the v1 M4 pin)
    kx = AXLE_X + KEEPER_R * math.cos(math.radians(KEEPER_ANGLE))
    kz = AXLE_Z + KEEPER_R * math.sin(math.radians(KEEPER_ANGLE))
    ksk = side_sketch(lambda: sketch_circle(model, *p(kx, kz), mm(KEEPER_D / 2)))
    add_step(ksk, CHEEK_GAP, math.pi * (KEEPER_D / 2) ** 2 * CHEEK_GAP / 1000, "keeper bar")

    # 6) axle hole through cheeks + bosses
    hsk = side_sketch(lambda: sketch_circle(model, *p(AXLE_X, AXLE_Z), mm(AXLE_D / 2)))
    cut_step(hsk, outer + 10, math.pi * (AXLE_D / 2) ** 2 * (2 * CHEEK_T + 2 * BOSS_H) / 1000, "axle hole")

    # 7) two horizontal M5 holes through the flange (Right Plane, YZ). Which sketch axis is model Z depends
    #    on the version, so try the candidate layouts and keep the one that removes exactly the flange holes.
    removed = 2 * math.pi * (RAIL_HOLE_D / 2) ** 2 * FLANGE_T / 1000
    layouts = [[(s * RAIL_HOLE_Z, y) for y in (-RAIL_HOLE_Y, RAIL_HOLE_Y)] for s in (1, -1)]
    layouts += [[(y, s * RAIL_HOLE_Z) for y in (-RAIL_HOLE_Y, RAIL_HOLE_Y)] for s in (1, -1)]
    for pts in layouts:
        with sketch(model, "Right Plane") as rsk:
            for u, w in pts:
                sketch_circle(model, mm(u), mm(w), mm(RAIL_HOLE_D / 2))
        before = volume_cm3(model)
        feat = cut_midplane(model, rsk, mm(2 * FLANGE_T + 10))
        after = volume_cm3(model)
        if feat is not None and abs((before - after) - removed) < 0.05:
            print(f"  rail holes ok (layout {pts}): vol {before}->{after}")
            break
        print(f"  rail holes layout {pts} rejected: vol {before}->{after}")
        if feat is not None:
            model.EditUndo2(1)
        model.ForceRebuild3(False)
    else:
        check(False, "rail holes failed")

    delete_orphan_sketches(model)
    box = body_box(model)
    print(f"  final box={box} vol={volume_cm3(model)} bodies={len(model.GetBodies2(0, True) or [])}")
    check(near(box[0], 0) and near(box[3], CHEEK_PROFILE[1][0]) and near(box[1], -FLANGE_Y) and near(box[5], z_top),
          "stand bounding box mismatch")
    check(len(model.GetBodies2(0, True) or []) == 1, "stand is not a single body")
    return model


def save(session, model, name):
    path = os.path.join(OUT, name + ".SLDPRT")
    model.ForceRebuild3(False)
    ok = session.save(model, path)
    check(ok and os.path.exists(path), f"save failed: {path}")
    model.ShowNamedView2("*Isometric", 7)
    model.ViewZoomtofit2()
    print(f"  saved {path} ({os.path.getsize(path)} bytes)")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    s = SolidWorksSession(version=2024)
    jobs = sys.argv[1:] or ["sheave", "stand"]
    for job in jobs:
        if job == "sheave":
            save(s, build_sheave(s), "滑輪")
        else:
            save(s, build_stand(s), "滑輪座")
    print("ALL DONE")
