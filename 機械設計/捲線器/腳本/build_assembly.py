"""Simplified purchased parts + full winch assembly in SolidWorks.

World frame (assembly): shaft axis along Z at height Y = 85 mm, Y up, rail top at Y = 0.
Web-model axial coordinate x maps to Z = -x; line leaves toward -X.
"""
import math
import os
import sys

import pythoncom
from win32com.client import VARIANT

sys.path.insert(0, r"C:\Users\123\.claude\skills\solidworks-automation\scripts")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sw_connect import mm, get_com_member, create_empty_dispatch_variant  # noqa: E402
from sw_part import sketch as _sw_sketch, sketch_circle, sketch_corner_rectangle, sketch_line, sketch_polygon, extrude_boss, extrude_cut, _ensure_sketch_selected  # noqa: E402
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
from sw_assembly import add_component  # noqa: E402
from sw_appearance import set_document_appearance  # noqa: E402
from build_parts import body_box, volume_cm3, check, near, feature_checked, holes_on_pcd  # noqa: E402

OUT = r"D:\123\Desktop\aweground\機械設計\捲線器"
STD = os.path.join(OUT, "外購件示意")
H = 85.0
PLATE_B = -110.0   # 立板 B 朝捲筒一面的 Z（KFL002 內圈 A0=18.5，需離聯軸器 1.5 mm）
PLATE_C = -226.5   # 立板 C 朝捲筒一面的 Z（給旋轉的拉桿螺母和 M6 螺絲頭留間隙）

COLORS = {
    "pla": "#EDEDE4", "disc": "#F2B04A", "alu": "#C9CED4", "steel": "#D5D9DE",
    "dark": "#2A2D31", "zinc": "#8E959C", "spider": "#D2352C", "line": "#E8D23A", "rail": "#AEB4BB",
}


def revolve_profile(model, contours):
    """contours: list of closed polylines [(z, r), ...] in mm; revolved 360° about the part Z axis.
    Right Plane sketch: sketch x = -Z, sketch y = Y (radius)."""
    with sketch(model, "Right Plane") as sk:
        model.SketchManager.AddToDB = True   # 關閉吸附，小尺寸輪廓才不會被吸偏
        zs = [p[0] for c in contours for p in c]
        model.SketchManager.CreateCenterLine(mm(-max(zs) - 5), 0, 0, mm(-min(zs) + 5), 0, 0)
        for pts in contours:
            for (z1, r1), (z2, r2) in zip(pts, pts[1:] + pts[:1]):
                sketch_line(model, mm(-z1), mm(r1), mm(-z2), mm(r2))
        model.SketchManager.AddToDB = False
    _ensure_sketch_selected(model, sk)
    feat = model.FeatureManager.FeatureRevolve2(
        True, True, False, False, False, False, 0, 0, 2 * math.pi, 0,
        False, False, 0.0, 0.0, 0, 0, 0, True, True, True)
    check(feat is not None, "revolve failed")
    model.ForceRebuild3(False)
    return feat


def expected_revolve_volume(contours):
    """Pappus volume (cm3) of polygon contours revolved about the axis."""
    total = 0.0
    for pts in contours:
        a = cz = 0.0
        for (z1, r1), (z2, r2) in zip(pts, pts[1:] + pts[:1]):
            c = z1 * r2 - z2 * r1
            a += c
            cz += (r1 + r2) * c
        a *= 0.5
        r_centroid = cz / (6 * a)
        total += abs(2 * math.pi * r_centroid * a)
    return total / 1000


def revolved_part(session, name, contours, color):
    model = session.new_part()
    revolve_profile(model, contours)
    v, ve = volume_cm3(model), expected_revolve_volume(contours)
    zs = [p[0] for c in contours for p in c]
    box = body_box(model)
    print(f"[{name}] vol {v} (expected {ve:.3f}) box {box}")
    check(abs(v - ve) < 0.05 * max(ve, 0.2) + 0.02, f"{name}: volume mismatch")
    check(near(box[2], min(zs), 0.1) and near(box[5], max(zs), 0.1), f"{name}: axial extent wrong")
    return model


def finish(session, model, name, color, folder=STD):
    set_document_appearance(model, COLORS[color])
    path = os.path.join(folder, name + ".SLDPRT")
    model.ForceRebuild3(False)
    check(session.save(model, path) and os.path.exists(path), f"save failed {path}")
    return path


def through_cut(model, draw, expected_cm3, label):
    with sketch(model, "Front Plane") as sk:
        draw()
    v0 = volume_cm3(model)
    feature_checked(model, lambda d: extrude_cut(model, sk, 0, direction=d),
                    lambda b, v: abs((v0 - v) - expected_cm3) < 0.02 + 0.02 * expected_cm3, label)


def cut_through_both(model, sketch_ref):
    _ensure_sketch_selected(model, sketch_ref)
    return model.FeatureManager.FeatureCut4(
        False, False, False, 1, 1, 0.01, 0.01,
        False, False, False, False, 0.0, 0.0,
        False, False, False, False, False,
        True, True, False, False,
        False, 0, 0, False, False)


def cut_from_offset(model, sketch_ref, start_mm, depth_mm, expected_cm3, label):
    """從草圖平面偏移 start_mm 開始、沿軸向切 depth_mm；方向用體積判斷。"""
    v0 = volume_cm3(model)
    for direction in (True, False):
        for flip in (False, True):
            _ensure_sketch_selected(model, sketch_ref)
            feat = model.FeatureManager.FeatureCut4(
                True, False, direction, 0, 0, mm(depth_mm), 0,
                False, False, False, False, 0.0, 0.0,
                False, False, False, False, False,
                True, True, False, False,
                False, 3, mm(start_mm), flip, False)
            if feat is None:
                continue
            v = volume_cm3(model)
            if abs((v0 - v) - expected_cm3) < 0.25 * expected_cm3:
                print(f"  {label}: ok removed {v0 - v:.3f}")
                return
            model.EditUndo2(1)
            model.ForceRebuild3(False)
    raise RuntimeError(f"{label} failed")


def build_clamp_flange(s):
    """標準件「大扭矩法蘭聯軸器－抱緊式」Ø15 孔的示意模型（按賣家尺寸表）：
    法蘭盤 Ø56.8×6，輪轂 Ø35，總長 26.4；3×Ø5.2 在 Ø45、3×Ø5.2 在 Ø47.5；鍵槽 5×2.3；M4 抱緊螺絲。
    抱緊切縫與螺絲位置按商品圖估計，以實物為準。"""
    m = s.new_part()
    print("[抱緊式法蘭聯軸器_D15]")
    prof = [[(0, 7.5), (0, 28.4), (6, 28.4), (6, 17.5), (26.4, 17.5), (26.4, 7.5)]]
    revolve_profile(m, prof)
    check(abs(volume_cm3(m) - expected_revolve_volume(prof)) < 0.01, "flange body")
    v0 = volume_cm3(m)
    with sketch(m, "Front Plane") as sk:
        holes_on_pcd(m, 47.5, 5.2, 3, 90)
        holes_on_pcd(m, 45.0, 5.2, 3, 30)
    holes = 6 * math.pi * 2.6 ** 2 * 6 / 1000
    feature_checked(m, lambda d: extrude_cut(m, sk, 0, direction=d),
                    lambda b, v: abs((v0 - v) - holes) < 0.005, "6 x Ø5.2")
    v0 = volume_cm3(m)
    with sketch(m, "Front Plane") as sk:
        sketch_corner_rectangle(m, mm(-2.5), mm(-9.8), mm(2.5), mm(-6.5))
    feature_checked(m, lambda d: extrude_cut(m, sk, 0, direction=d),
                    lambda b, v: 0.2 < (v0 - v) < 0.45, "keyway 5x2.3")
    with sketch(m, "Front Plane") as sk:
        sketch_corner_rectangle(m, mm(-0.5), mm(7.0), mm(0.5), mm(18.0))
    cut_from_offset(m, sk, 12.0, 14.4, 0.144, "clamp slit")
    v0 = volume_cm3(m)
    with sketch(m, "Right Plane") as sk:           # 草圖 x = -Z
        sketch_circle(m, mm(-20.2), mm(12.5), mm(2.25))
    check(cut_through_both(m, sk) is not None, "clamp screw hole")
    removed = v0 - volume_cm3(m)
    print(f"  clamp screw hole removed {removed:.3f}")
    check(0.25 < removed < 0.45, "clamp screw hole volume")
    print(f"  flange box {body_box(m)} vol {volume_cm3(m)}")
    return m



def build_purchased(s):
    paths = {}
    rev = [
        ("捲筒軸_D15x200", [[(-256, 0), (-256, 7.5), (-56, 7.5), (-56, 0)]], "steel"),
        ("電機_80100_Kv50", [[(-40, 0), (-40, 6), (0, 6), (0, 36.5), (3, 36.5), (3, 39.5), (100, 39.5), (100, 0)]], "dark"),
        ("聯軸器_爪", [[(-14, 6), (-14, 20), (-39, 20), (-39, 6)], [(-55, 7.5), (-55, 20), (-80, 20), (-80, 7.5)]], "alu"),
        ("聯軸器_梅花墊", [[(-39, 6), (-39, 17), (-55, 17), (-55, 6)]], "spider"),
        ("捲筒芯_6061鋁管_D90x3x68", [[(-205, 42), (-205, 45), (-137, 45), (-137, 42)]], "alu"),
        ("線_Dyneema_50m捲繞", [[(-201, 45), (-201, 48), (-141, 48), (-141, 45)]], "line"),
    ]
    def done(name):
        path = os.path.join(STD, name + ".SLDPRT")
        if os.path.exists(path):
            paths[name] = path
            print(f"[{name}] already built, skip")
            return True
        return False

    for name, contours, color in rev:
        if done(name):
            continue
        m = revolved_part(s, name, contours, color)
        if name == "捲筒軸_D15x200":
            add_spool_shaft_flat(m)
        elif name == "電機_80100_Kv50":
            add_motor_shaft_flat(m)
        paths[name] = finish(s, m, name, color)

    name = "線_出線段"
    if not done(name):
        m = s.new_part()
        with sketch(m, "Front Plane") as sk:
            # 實際線徑 1 mm；SolidWorks 在原點畫 r=1 的圓會被吸附失敗，示意用 Ø3
            check(sketch_circle(m, 0, 0, mm(1.5)) is not None, "line circle failed")
        feature_checked(m, lambda d: extrude_boss(m, sk, mm(400), direction=d),
                        lambda b, v: b and near(b[2], -400) and near(b[5], 0), "line out")
        paths[name] = finish(s, m, name, "line")

    # KFL002 (local frame: mounting face z=0, housing toward +Z)
    name = "KFL002"
    if not done(name):
        m = s.new_part()
        print(f"[{name}]")
        with sketch(m, "Front Plane") as sk:
            sketch_circle(m, 0, 0, mm(21))
        feature_checked(m, lambda d: extrude_boss(m, sk, mm(6.5), direction=d),
                        lambda b, v: b and near(b[2], 0) and near(b[5], 6.5), "flange round")
        with sketch(m, "Front Plane") as sk2:
            slot = m.SketchManager.CreateSketchSlot(0, 0, mm(14), 0, mm(-26.5), 0, 0, mm(26.5), 0, 0, 0, 0, 1, False)
            check(slot is not None, "kfl slot failed")
        feature_checked(m, lambda d: extrude_boss(m, sk2, mm(6.5), direction=d),
                        lambda b, v: b and near(b[2], 0) and near(b[5], 6.5) and near(b[4], 33.5), "flange lobes")
        v_before = volume_cm3(m)
        through_cut(m, lambda: (sketch_circle(m, 0, 0, mm(9.5)),
                                sketch_circle(m, 0, mm(-26.5), mm(3.5)), sketch_circle(m, 0, mm(26.5), mm(3.5))),
                    (math.pi * 9.5 ** 2 + 2 * math.pi * 3.5 ** 2) * 6.5 / 1000, "kfl holes")
        revolve_profile(m, [[(0, 9.5), (0, 17), (13, 17), (13, 11), (18.5, 11), (18.5, 7.5), (13, 7.5), (13, 9.5)]])   # 賣家表 A=13、A0=18.5
        print(f"  kfl box {body_box(m)} vol {volume_cm3(m)}")
        check(near(body_box(m)[5], 18.5), "kfl axial extent")
        paths[name] = finish(s, m, name, "zinc")

    # 4 x M5 全牙螺桿 L95（local z 0..-95）
    name = "M5拉桿x4_L95"
    if not done(name):
        m = s.new_part()
        with sketch(m, "Front Plane") as sk:
            holes_on_pcd(m, 70, 5, 4, 45)
        feature_checked(m, lambda d: extrude_boss(m, sk, mm(95), direction=d),
                        lambda b, v: b and near(b[2], -95) and near(b[5], 0), "rods")
        paths[name] = finish(s, m, name, "steel")

    # 六角螺母（local z -t..0）：AddToDB 關閉草圖吸附，避免頂點被吸偏
    for name, af, t, bore in (("M5尼龍防鬆螺母", 8.0, 5.0, 2.5), ("M6螺母", 10.0, 5.2, 3.0)):
        if done(name):
            continue
        m = s.new_part()
        with sketch(m, "Front Plane") as sk:
            m.SketchManager.AddToDB = True
            rc = af / 2 / math.cos(math.radians(30))
            pts = [(rc * math.cos(math.radians(60 * k)), rc * math.sin(math.radians(60 * k))) for k in range(6)]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                sketch_line(m, mm(x1), mm(y1), mm(x2), mm(y2))
            check(sketch_circle(m, 0, 0, mm(bore)) is not None, f"{name} bore failed")
            m.SketchManager.AddToDB = False
        expected = (1.5 * math.sqrt(3) * rc ** 2 - math.pi * bore ** 2) * t / 1000
        feature_checked(m, lambda d: extrude_boss(m, sk, mm(t), direction=d),
                        lambda b, v: b and near(b[2], -t) and near(b[5], 0) and abs(v - expected) < 0.005, name)
        paths[name] = finish(s, m, name, "steel")

    # 回轉體標準件（local z：墊片 -1.2..0；螺絲頭 0..6、螺桿 -25..0）
    for name, contours in (("M5加大墊片", [[(-1.2, 2.65), (-1.2, 7.5), (0, 7.5), (0, 2.65)]]),
                           ("M5平墊片", [[(-1, 2.65), (-1, 5), (0, 5), (0, 2.65)]]),
                           ("M5內六角螺絲x25", [[(-25, 0), (-25, 2.5), (0, 2.5), (0, 4.25), (5, 4.25), (5, 0)]]),
                           ("M6內六角螺絲x25", [[(-25, 0), (-25, 3), (0, 3), (0, 5), (6, 5), (6, 0)]])):
        if done(name):
            continue
        m = revolved_part(s, name, contours, "steel")
        paths[name] = finish(s, m, name, "steel")

    name = "抱緊式法蘭聯軸器_D15"
    if not done(name):
        m = build_clamp_flange(s)
        paths[name] = finish(s, m, name, "alu")

    # 2020 型材：兩根長 400，中心距 80（local z 0..-400）
    name = "2020型材x400_x2"
    if not done(name):
        m = s.new_part()
        with sketch(m, "Front Plane") as sk:
            sketch_corner_rectangle(m, mm(30), mm(-20), mm(50), 0)
            sketch_corner_rectangle(m, mm(-50), mm(-20), mm(-30), 0)
        feature_checked(m, lambda d: extrude_boss(m, sk, mm(400), direction=d),
                        lambda b, v: b and near(b[2], -400) and near(b[5], 0) and near(v, 320.0, 0.5), "rails 2020")
        paths[name] = finish(s, m, name, "rail")

    # 2020 短橫樑：長 60（X 方向），夾在兩根型材之間（local z -20..0）
    name = "2020型材x60_橫樑"
    if not done(name):
        m = s.new_part()
        with sketch(m, "Front Plane") as sk:
            sketch_corner_rectangle(m, mm(-30), mm(-20), mm(30), 0)
        feature_checked(m, lambda d: extrude_boss(m, sk, mm(20), direction=d),
                        lambda b, v: b and near(b[2], -20) and near(b[5], 0), "crossbar 2020")
        paths[name] = finish(s, m, name, "rail")
    return paths


ROT_Y180 = (-1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0)
ROT_Y90 = (0.0, 0.0, 1.0, 0.0, 1.0, 0.0, -1.0, 0.0, 0.0)
IDENT = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def place(asm, sw, path, x, y, z, rot=IDENT, expect_z=None, expect_x=None, label=""):
    comp = add_component(asm, path, mm(x), mm(y), mm(z), sw=sw)
    check(comp is not None, f"add failed {path}")
    asm.ClearSelection2(True)
    comp.Select4(False, create_empty_dispatch_variant(), False)
    asm.UnfixComponent()          # 第一個零件會被自動固定在原點，先解除才能擺位
    t = comp.Transform2
    # pywin32 需用帶型別的 VARIANT 陣列，否則 ArrayData 賦值不生效
    t.ArrayData = VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                          list(rot) + [mm(x), mm(y), mm(z), 1.0, 0.0, 0.0, 0.0])
    check(comp.SetTransformAndSolve2(t), f"transform failed {path}")
    asm.ClearSelection2(True)
    comp.Select4(False, create_empty_dispatch_variant(), False)
    asm.FixComponent()            # 示意總裝不加配合，全部固定，避免被拖動
    asm.ClearSelection2(True)
    box = [v * 1000 for v in comp.GetBox(False, False)]
    tag = label or os.path.basename(path)
    print(f"  {tag}: X[{box[0]:.1f},{box[3]:.1f}] Y[{box[1]:.1f},{box[4]:.1f}] Z[{box[2]:.1f},{box[5]:.1f}]")
    if expect_z:
        check(near(box[2], expect_z[0], 1.0) and near(box[5], expect_z[1], 1.0), f"{tag}: Z range {box[2]:.1f}..{box[5]:.1f} != {expect_z}")
    if expect_x:
        check(near(box[0], expect_x[0], 1.5) and near(box[3], expect_x[1], 1.5), f"{tag}: X range wrong")
    return comp


def build_assembly(s, p):
    printed = {
        "A": os.path.join(OUT, "立板A_電機座.SLDPRT"),
        "BC": os.path.join(OUT, "立板BC_軸承座.SLDPRT"),
        "drive": os.path.join(OUT, "擋板_驅動側.SLDPRT"),
        "driven": os.path.join(OUT, "擋板_從動側.SLDPRT"),
    }
    for key, path in printed.items():
        doc = s.open(path)
        set_document_appearance(doc, COLORS["disc" if key in ("drive", "driven") else "pla"])
        check(s.save(doc), f"save {path}")

    # 舊版總裝若已開着（已保存），先關閉，才能用同一文件名保存新版
    target = os.path.normcase(os.path.join(OUT, "捲線器_總裝.SLDASM"))
    for doc in list(get_com_member(s.sw, "GetDocuments") or []):
        if os.path.normcase(get_com_member(doc, "GetPathName") or "") == target:
            check(not get_com_member(doc, "GetSaveFlag"), "舊版總裝有未保存的修改，請先處理")
            s.sw.CloseDoc(get_com_member(doc, "GetTitle"))
            print("closed old assembly (was saved)")

    asm = s.new_assembly()
    sw = s.sw
    print("[總裝]")
    place(asm, sw, p["2020型材x400_x2"], 0, 0, 110, expect_z=(-290, 110))
    place(asm, sw, p["2020型材x60_橫樑"], 0, 0, 110, expect_z=(90, 110), label="橫樑1")
    place(asm, sw, p["2020型材x60_橫樑"], 0, 0, -270, expect_z=(-290, -270), label="橫樑2")
    place(asm, sw, printed["A"], 0, 0, 0, expect_z=(-45, 0), label="立板A")
    place(asm, sw, printed["BC"], 0, 0, PLATE_B, ROT_Y180, expect_z=(PLATE_B, PLATE_B + 45), label="立板B")
    place(asm, sw, printed["BC"], 0, 0, PLATE_C, expect_z=(PLATE_C - 45, PLATE_C), label="立板C")
    place(asm, sw, p["電機_80100_Kv50"], 0, H, 0, expect_z=(-40, 100))
    place(asm, sw, p["聯軸器_爪"], 0, H, 0, expect_z=(-80, -14))
    place(asm, sw, p["聯軸器_梅花墊"], 0, H, 0, expect_z=(-55, -39))
    place(asm, sw, p["捲筒軸_D15x200"], 0, H, 0, expect_z=(-256, -56))
    place(asm, sw, p["KFL002"], 0, H, PLATE_B + 10, expect_z=(PLATE_B + 10, PLATE_B + 28.5), label="KFL002 (B)")
    kc = PLATE_C - 10
    place(asm, sw, p["KFL002"], 0, H, kc, ROT_Y180, expect_z=(kc - 18.5, kc), label="KFL002 (C)")
    # M6 × 25 螺絲：頭在朝捲筒一面，螺母擰在 KFL002 法蘭外側
    for dy in (-26.5, 26.5):
        place(asm, sw, p["M6內六角螺絲x25"], 0, H + dy, PLATE_B, ROT_Y180, expect_z=(PLATE_B - 6, PLATE_B + 25), label="M6螺絲 (B)")
        place(asm, sw, p["M6螺母"], 0, H + dy, PLATE_B + 16.5, ROT_Y180, expect_z=(PLATE_B + 16.5, PLATE_B + 21.7), label="M6螺母 (B)")
        place(asm, sw, p["M6內六角螺絲x25"], 0, H + dy, PLATE_C, expect_z=(PLATE_C - 25, PLATE_C + 6), label="M6螺絲 (C)")
        place(asm, sw, p["M6螺母"], 0, H + dy, kc - 6.5, expect_z=(kc - 11.7, kc - 6.5), label="M6螺母 (C)")
    # 標準抱緊式法蘭（零件座標：法蘭盤 z 0..6，輪轂 z 6..26.4）反裝：
    # 法蘭盤在鋁管內（Z -147..-141）貼驅動側擋板內側，輪轂穿過擋板中心孔朝立板 B（到 Z -120.6）
    place(asm, sw, p["抱緊式法蘭聯軸器_D15"], 0, H, -147, expect_z=(-147, -120.6), label="抱緊式法蘭（反裝）")
    # 3 × M5×25：頭在鋁管內（Z -152..-147），穿過法蘭盤和擋板；擋板外側 M5 平墊片 + 尼龍防鬆螺母
    for k in range(3):
        a = math.radians(90 + 120 * k)
        x, y = 23.75 * math.cos(a), H + 23.75 * math.sin(a)
        place(asm, sw, p["M5內六角螺絲x25"], x, y, -147, ROT_Y180, expect_z=(-152, -122), label="M5×25")
        place(asm, sw, p["M5平墊片"], x, y, -132, expect_z=(-133, -132), label="M5平墊片")
        place(asm, sw, p["M5尼龍防鬆螺母"], x, y, -127, expect_z=(-132, -127), label="M5螺母（法蘭）")
    place(asm, sw, printed["drive"], 0, H, -141, ROT_Y180, expect_z=(-141, -133), label="擋板 驅動側")
    place(asm, sw, p["捲筒芯_6061鋁管_D90x3x68"], 0, H, 0, expect_z=(-205, -137))
    place(asm, sw, printed["driven"], 0, H, -201, expect_z=(-209, -201), label="擋板 從動側")
    # 拉桿 L95 置中於兩擋板外側面（-133 / -209）之間；每端：加大墊片 1.2 + 尼龍防鬆螺母 5，伸出約 3.3
    place(asm, sw, p["M5拉桿x4_L95"], 0, H, -123.5, expect_z=(-218.5, -123.5))
    r = 35 * math.cos(math.radians(45))
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * r, H + sy * r
            place(asm, sw, p["M5加大墊片"], x, y, -131.8, expect_z=(-133, -131.8), label="墊片 驅動側")
            place(asm, sw, p["M5尼龍防鬆螺母"], x, y, -126.8, expect_z=(-131.8, -126.8), label="螺母 驅動側")
            place(asm, sw, p["M5加大墊片"], x, y, -209, expect_z=(-210.2, -209), label="墊片 從動側")
            place(asm, sw, p["M5尼龍防鬆螺母"], x, y, -210.2, expect_z=(-215.2, -210.2), label="螺母 從動側")
    place(asm, sw, p["線_Dyneema_50m捲繞"], 0, H, 0, expect_z=(-201, -141))
    place(asm, sw, p["線_出線段"], 0, H + 48, -171, ROT_Y90, expect_x=(0, 400), label="出線段（朝 +X）")

    asm.ForceRebuild3(False)
    asm.ShowNamedView2("*Isometric", 7)
    asm.ViewZoomtofit2()
    path = os.path.join(OUT, "捲線器_總裝.SLDASM")
    check(s.save(asm, path) and os.path.exists(path), "assembly save failed")
    print("saved", path, os.path.getsize(path))
    return asm


# ---------------- D 型削平 ----------------
def segment_area(r, h):
    """圓被削去深 h 的弓形面積 (mm²)。"""
    return r * r * math.acos((r - h) / r) - (r - h) * math.sqrt(2 * r * h - h * h)


def add_spool_shaft_flat(model):
    """捲筒軸全長單邊削平 1 mm（削在 +Y 側），Ø15 → 剩 14。"""
    expected = segment_area(7.5, 1.0) * 200 / 1000
    v0 = volume_cm3(model)
    with sketch(model, "Front Plane") as sk:
        sketch_corner_rectangle(model, mm(-10), mm(6.5), mm(10), mm(12))
    feature_checked(model, lambda d: extrude_cut(model, sk, 0, direction=d),
                    lambda b, v: abs((v0 - v) - expected) < 0.01, "spool shaft flat")


def add_motor_shaft_flat(model):
    """電機 Ø12 D 型軸：從軸端起 29 mm 長的削平，削平後 11 mm（圖紙 11-0.1）。軸在 Z -40..0。"""
    expected = segment_area(6.0, 1.0) * 29 / 1000
    v0 = volume_cm3(model)
    with sketch(model, "Front Plane") as sk:
        sketch_corner_rectangle(model, mm(-8), mm(5.0), mm(8), mm(10))
    for direction in (True, False):
        for flip_start in (False, True):
            _ensure_sketch_selected(model, sk)
            feat = model.FeatureManager.FeatureCut4(
                True, False, direction, 0, 0, mm(29), 0,
                False, False, False, False, 0.0, 0.0,
                False, False, False, False, False,
                True, True, False, False,
                False, 3, mm(11), flip_start, False)   # T0=3：從草圖平面偏移 11 mm 開始
            if feat is None:
                continue
            v = volume_cm3(model)
            if abs((v0 - v) - expected) < 0.01:
                print(f"  motor shaft flat ok (dir={direction}, flip_start={flip_start}) removed {v0 - v:.4f} cm3")
                return
            print(f"  motor shaft flat rejected dir={direction} flip={flip_start} removed {v0 - v:.4f}")
            model.EditUndo2(1)
            model.ForceRebuild3(False)
    raise RuntimeError("motor shaft flat failed")


if __name__ == "__main__":
    os.makedirs(STD, exist_ok=True)
    s = SolidWorksSession(version=2024, max_documents=60)
    paths = build_purchased(s)
    build_assembly(s, paths)
    print("ALL DONE")
