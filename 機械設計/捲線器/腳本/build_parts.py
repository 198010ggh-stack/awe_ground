"""Build the 3D-printed winch parts in SolidWorks (parts only, no assembly/drawings).

Part frame: Front Plane = plate/disc face, +Y up, solid grows toward -Z
(SolidWorks extrude_boss(direction=True) on Front Plane goes to -Z).
"""
import math
import os
import sys

sys.path.insert(0, r"C:\Users\123\.claude\skills\solidworks-automation\scripts")
from sw_connect import mm, get_com_member  # noqa: E402
from sw_part import (sketch as _sw_sketch, sketch_circle, sketch_corner_rectangle, sketch_arc,  # noqa: E402
                     sketch_line, extrude_boss, extrude_cut, _ensure_sketch_selected)
from sw_session import SolidWorksSession  # noqa: E402
from contextlib import contextmanager  # noqa: E402


@contextmanager
def sketch(model, plane):
    """草圖期間關閉自動吸附（AddToDB），避免孔心被吸到其他圖元的對齊位置上。"""
    with _sw_sketch(model, plane) as sk:
        model.SketchManager.AddToDB = True
        try:
            yield sk
        finally:
            model.SketchManager.AddToDB = False

OUT = r"D:\123\Desktop\aweground\機械設計\捲線器"

# ---- parameters (mm), from the design spec ----
H = 85.0
PLATE_W, PLATE_H, PLATE_T = 120.0, 125.0, 10.0
FOOT_L, FOOT_T = 35.0, 8.0
RAIL_Y = 40.0                 # rail centre offset from plate centre (along plate width)
SLOT_FROM_END = 15.0          # slot centre distance from foot free end
SLOT_TRAVEL = 6.0             # ±3 mm adjustment
GUSSET_X, GUSSET_Z, GUSSET_T, GUSSET_POS = 30.0, 55.0, 8.0, 28.0
MOUNT_PCD, MOUNT_HOLE, BOSS_D = 56.0, 5.5, 40.0   # Ø40：讓過定子面 4 顆內六角螺絲頭（約 Ø28 孔距，外包絡約 Ø35）和中心軸承
KFL_SPACING, KFL_HOLE, KFL_BOSS_D = 53.0, 6.5, 36.0
DISC_D, DISC_T, DISC_BORE = 140.0, 8.0, 15.2   # 從動側軸孔：公差片上 Ø15.2 已驗證能順滑通過光軸
ROD_PCD, ROD_HOLE = 70.0, 5.5
# 驅動側擋板：配標準抱緊式法蘭聯軸器（Ø15 孔，法蘭盤 Ø56.8×6，輪轂 Ø35×20.4），反裝：
# 法蘭盤在鋁管內貼擋板內側，輪轂穿過擋板中心孔朝外；用法蘭上 Ø47.5 那組 3×Ø5.2 孔
DRIVE_BORE = 35.4                        # 讓 Ø35 輪轂穿過並定心
FLANGE_PCD, FLANGE_HOLE, FLANGE_N, FLANGE_START = 47.5, 5.5, 3, 90.0   # 3×M5
GROOVE_ID, GROOVE_OD, GROOVE_DEPTH = 83.6, 90.4, 4.0   # 鋁管實測長 68：每端插入 4 mm，淨捲線寬仍 60
LINE_HOLE_R, LINE_HOLE_D = 48.0, 3.0


# ---------------- helpers ----------------
def body_box(model):
    """Bounding box of all solid bodies in mm: (xmin, ymin, zmin, xmax, ymax, zmax)."""
    boxes = [b.GetBodyBox() for b in (model.GetBodies2(0, True) or [])]
    if not boxes:
        return None
    lo = [min(b[i] for b in boxes) * 1000 for i in range(3)]
    hi = [max(b[i + 3] for b in boxes) * 1000 for i in range(3)]
    return tuple(round(v, 2) for v in lo + hi)


def volume_cm3(model):
    model.ForceRebuild3(False)
    mp = get_com_member(model.Extension, "CreateMassProperty")
    return round(get_com_member(mp, "Volume") * 1e6, 3) if mp else None


def check(cond, msg):
    if not cond:
        raise RuntimeError(msg)


def holes_on_pcd(model, pcd, d, n, start_deg, cx=0.0, cy=0.0):
    for i in range(n):
        a = math.radians(start_deg + i * 360.0 / n)
        sketch_circle(model, mm(cx + math.cos(a) * pcd / 2), mm(cy + math.sin(a) * pcd / 2), mm(d / 2))


def feature_checked(model, make, accept, label):
    """Run make(direction) for direction True/False; keep the first result that accept(box, vol) approves."""
    for direction in (True, False):
        feat = make(direction)
        if feat is None:
            continue
        model.ForceRebuild3(False)
        box, vol = body_box(model), volume_cm3(model)
        if accept(box, vol):
            print(f"  {label}: ok (direction={direction}) box={box} vol={vol}")
            return feat
        print(f"  {label}: direction={direction} rejected box={box} vol={vol}; undo")
        model.EditUndo2(1)
        model.ForceRebuild3(False)
    raise RuntimeError(f"{label}: no direction produced the expected geometry")


def cut_midplane(model, sketch_ref, total_depth):
    _ensure_sketch_selected(model, sketch_ref)
    return model.FeatureManager.FeatureCut4(
        True, False, False, 6, 0, total_depth, 0,
        False, False, False, False, 0.0, 0.0,
        False, False, False, False, False,
        True, True, False, False,
        False, 0, 0, False, False)


def extrude_mid(model, sketch_ref, total_depth):
    _ensure_sketch_selected(model, sketch_ref)
    return model.FeatureManager.FeatureExtrusion3(
        True, False, True, 6, 0, total_depth, 0.0,
        False, False, False, False, 0.0, 0.0,
        False, False, False, False, True, False, True, 0, 0.0, False)


def near(a, b, tol=0.05):
    return abs(a - b) <= tol


# ---------------- parts ----------------
def build_upright(session, kind):
    """kind 'A' = motor plate, 'BC' = KFL002 bearing plate (print twice)."""
    model = session.new_part()
    w = PLATE_W / 2
    print(f"[立板 {kind}]")

    # 1) plate with all holes, Front Plane, thickness toward -Z
    with sketch(model, "Front Plane") as sk:
        sketch_corner_rectangle(model, mm(-w), 0, mm(w), mm(PLATE_H))
        if kind == "A":
            sketch_circle(model, 0, mm(H), mm(BOSS_D / 2))
            holes_on_pcd(model, MOUNT_PCD, MOUNT_HOLE, 4, 45, cy=H)
        else:
            sketch_circle(model, 0, mm(H), mm(KFL_BOSS_D / 2))
            sketch_circle(model, 0, mm(H - KFL_SPACING / 2), mm(KFL_HOLE / 2))
            sketch_circle(model, 0, mm(H + KFL_SPACING / 2), mm(KFL_HOLE / 2))
    feature_checked(model, lambda d: extrude_boss(model, sk, mm(PLATE_T), direction=d),
                    lambda b, v: b and near(b[2], -PLATE_T) and near(b[5], 0), "plate")

    # 2) foot on Top Plane: x ±60, z from -PLATE_T to -(PLATE_T+FOOT_L), thickness +Y (0..FOOT_T)
    #    Top Plane sketch y maps to model -Z or +Z depending on version; try both and keep the right one.
    def make_foot(sign):
        with sketch(model, "Top Plane") as fsk:
            z0, z1 = PLATE_T * sign, (PLATE_T + FOOT_L) * sign
            sketch_corner_rectangle(model, mm(-w), mm(z0), mm(w), mm(z1))
            zc = (PLATE_T + FOOT_L - SLOT_FROM_END) * sign
            t = SLOT_TRAVEL / 2
            for x in (-RAIL_Y, RAIL_Y):
                # straight slot, centre-to-centre length = travel, full width = M5 clearance (±3 mm adjustment)
                slot = model.SketchManager.CreateSketchSlot(
                    0, 0, mm(MOUNT_HOLE),
                    mm(x), mm(zc - t), 0, mm(x), mm(zc + t), 0, 0, 0, 0, 1, False)
                check(slot is not None, "slot creation failed")
        return fsk

    v_plate = volume_cm3(model)
    foot_vol = (PLATE_W * FOOT_L * FOOT_T - 2 * (math.pi * (MOUNT_HOLE / 2) ** 2 + MOUNT_HOLE * SLOT_TRAVEL) * FOOT_T) / 1000
    done = False
    for sign in (1, -1):
        fsk = make_foot(sign)
        try:
            feature_checked(
                model, lambda d: extrude_boss(model, fsk, mm(FOOT_T), direction=d),
                lambda b, v: b and near(b[1], 0) and near(b[2], -(PLATE_T + FOOT_L)) and near(b[5], 0)
                and abs((v - v_plate) - foot_vol) < 0.3, f"foot(sketch sign {sign})")
            done = True
            break
        except RuntimeError as e:
            print("  ", e)
            model.EditUndo2(1)   # remove the unused sketch
    check(done, "foot could not be placed")

    # 3) gussets: triangle in the YZ plane (Right Plane), extruded mid-plane 2*(POS+T/2), then middle cut away
    def make_tri(sign):
        with sketch(model, "Right Plane") as tsk:
            za, zb = PLATE_T * sign, (PLATE_T + GUSSET_X) * sign
            ya, yb = FOOT_T, FOOT_T + GUSSET_Z
            sketch_line(model, mm(za), mm(ya), mm(zb), mm(ya))
            sketch_line(model, mm(zb), mm(ya), mm(za), mm(yb))
            sketch_line(model, mm(za), mm(yb), mm(za), mm(ya))
        return tsk

    tri_area = 0.5 * GUSSET_X * GUSSET_Z
    outer = 2 * (GUSSET_POS + GUSSET_T / 2)
    inner = 2 * (GUSSET_POS - GUSSET_T / 2)
    v_before = volume_cm3(model)
    done = False
    for sign in (1, -1):
        tsk = make_tri(sign)
        feat = extrude_mid(model, tsk, mm(outer))
        if feat is None:
            model.EditUndo2(1)
            continue
        v = volume_cm3(model)
        box = body_box(model)
        if near(box[2], -(PLATE_T + FOOT_L)) and near(box[5], 0) and abs((v - v_before) - tri_area * outer / 1000) < 0.3:
            print(f"  gusset slab ok (sketch sign {sign}) vol {v_before}->{v}")
            done = True
            break
        print(f"  gusset slab sign {sign} rejected vol {v_before}->{v} box={box}")
        model.EditUndo2(1)
        model.EditUndo2(1)
        model.ForceRebuild3(False)
    check(done, "gusset slab failed")
    tsk2 = make_tri(sign)
    v_before = volume_cm3(model)
    feat = cut_midplane(model, tsk2, mm(inner))
    check(feat is not None, "gusset middle cut failed")
    v = volume_cm3(model)
    print(f"  gusset middle cut vol {v_before}->{v}")
    check(abs((v_before - v) - tri_area * inner / 1000) < 0.3, "gusset cut volume mismatch")
    return model


def build_disc(session, drive):
    model = session.new_part()
    print(f"[擋板 {'驅動側' if drive else '從動側'}]")
    with sketch(model, "Front Plane") as sk:
        sketch_circle(model, 0, 0, mm(DISC_D / 2))
        bore = DRIVE_BORE if drive else DISC_BORE
        sketch_circle(model, 0, 0, mm(bore / 2))
        holes_on_pcd(model, ROD_PCD, ROD_HOLE, 4, 45)
        if drive:
            holes_on_pcd(model, FLANGE_PCD, FLANGE_HOLE, FLANGE_N, FLANGE_START)
        sketch_circle(model, 0, mm(LINE_HOLE_R), mm(LINE_HOLE_D / 2))
    holes_area = (bore ** 2 + 4 * ROD_HOLE ** 2 + LINE_HOLE_D ** 2
                  + (FLANGE_N * FLANGE_HOLE ** 2 if drive else 0)) * math.pi / 4
    expected = (math.pi / 4 * DISC_D ** 2 - holes_area) * DISC_T / 1000
    feature_checked(model, lambda d: extrude_boss(model, sk, mm(DISC_T), direction=d),
                    lambda b, v: b and near(b[2], -DISC_T) and near(b[5], 0) and abs(v - expected) < 0.01, "disc")
    # annular groove on the Front Plane face (z = 0), GROOVE_DEPTH deep
    v0 = volume_cm3(model)
    groove = math.pi / 4 * (GROOVE_OD ** 2 - GROOVE_ID ** 2) * GROOVE_DEPTH / 1000
    with sketch(model, "Front Plane") as gsk:
        sketch_circle(model, 0, 0, mm(GROOVE_OD / 2))
        sketch_circle(model, 0, 0, mm(GROOVE_ID / 2))
    feature_checked(model, lambda d: extrude_cut(model, gsk, mm(GROOVE_DEPTH), direction=d),
                    lambda b, v: abs((v0 - v) - groove) < 0.2, "groove")
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
    jobs = sys.argv[1:] or ["A", "BC", "drive", "driven"]
    for job in jobs:
        if job in ("A", "BC"):
            m = build_upright(s, job)
            save(s, m, "立板A_電機座" if job == "A" else "立板BC_軸承座")
        else:
            m = build_disc(s, drive=(job == "drive"))
            save(s, m, "擋板_驅動側" if job == "drive" else "擋板_從動側")
    print("ALL DONE")
