"""HKVL-56 六維力傳感器讀取工具（依據用戶手冊 Ver 1.1）

用法：
  python hkvl56.py --selftest                 # 不用接硬件，用手冊裏的例子自檢
  python -m serial.tools.list_ports -v        # 列出電腦上的串口，找 485 模塊是 COM 幾
  python hkvl56.py COM5 --raw                 # 像串口助手一樣看十六進制原始字節
  python hkvl56.py COM5 --scan                # 自動試 115200/460800/921600/1M 和 Modbus
  python hkvl56.py COM5                       # RS485 主動發送模式，115200，上電先去皮 200 幀，按 56C 顯示負載率
  python hkvl56.py COM5 --csv log.csv         # 同時記錄到 CSV
  python hkvl56.py COM5 --modbus --id 1       # Modbus-RTU 主從模式輪詢
  python hkvl56.py COM5 --hw-zero             # 先發硬件清零命令（斷電丟失）

我們的 56C 是 Modbus 版：Windows 上 `python hkvl56.py COM7 --modbus`，
Jetson NX 上 `python3 hkvl56.py /dev/ttyUSB0 --modbus`（串口名見 `ls /dev/ttyUSB*`）。
在自己的程序裏讀數：見 stream() 的說明。

讀串口需要 pyserial：pip install pyserial（Jetson 上 sudo apt install python3-serial）
支持 Python 3.6 及以上。
"""
import argparse
import struct
import sys
import time

AXES = ("Fx", "Fy", "Fz", "Mx", "My", "Mz")
HEADER = b"\x53\x54"  # 'ST'
FRAME_LEN = 28        # 2 幀頭 + 6×float32 小端 + 2 CRC（小端）

# CAN 2.0 每 LSB 對應的物理量：(力 N/LSB, 力矩 Nm/LSB)，手冊 6.2.2 表
CAN_SCALE = {
    "56AS": (0.025, 0.001),
    "56A": (0.050031, 0.0025),
    "56B": (0.0981, 0.00345),
    "56C": (0.14715, 0.005),
    "56D": (0.1962, 0.006),
}
# 量程 (Fx/Fy/Fz N, Mx/My/Mz Nm)，手冊 3.1 表
RANGE = {"56AS": (50, 2), "56A": (100, 5), "56B": (200, 7), "56C": (300, 10), "56D": (400, 12)}


# ---------------------------------------------------------------- 校驗
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(body: bytes) -> bytes:
    return body + struct.pack("<H", crc16_modbus(body))


def _hex(data: bytes) -> str:  # bytes.hex(" ") 要 Python 3.8，老 Jetson 是 3.6
    return " ".join("%02X" % b for b in data)


# ---------------------------------------------------------------- RS485 主動發送
class ActiveParser:
    """把串口收到的字節流切成 28 字節幀；幀頭錯或 CRC 錯就丟 1 個字節重新對齊。"""

    def __init__(self):
        self.buf = bytearray()
        self.bad = 0

    def feed(self, data: bytes):
        self.buf += data
        out = []
        while len(self.buf) >= FRAME_LEN:
            i = self.buf.find(HEADER)
            if i < 0:
                del self.buf[:-1]  # 留最後一個字節，可能是 0x53
                break
            if i:
                del self.buf[:i]
                continue
            if len(self.buf) < FRAME_LEN:
                break
            frame = bytes(self.buf[:FRAME_LEN])
            if crc16_modbus(frame[:26]) == struct.unpack("<H", frame[26:])[0]:
                out.append(struct.unpack("<6f", frame[2:26]))
                del self.buf[:FRAME_LEN]
            else:
                self.bad += 1
                del self.buf[:1]
        return out


# ---------------------------------------------------------------- Modbus-RTU
def mb_read_all(dev_id=1) -> bytes:
    """03 讀 0x0003 起 12 個寄存器 = Fx..Mz 六個 float。"""
    return with_crc(struct.pack(">BBHH", dev_id, 0x03, 0x0003, 12))


def mb_write(dev_id, reg, value) -> bytes:
    return with_crc(struct.pack(">BBHH", dev_id, 0x06, reg, value))


def mb_zero(dev_id=1):          # 寫 0x000E = 0：當前值作為零點，斷電丟失
    return mb_write(dev_id, 0x000E, 0)


def mb_set_id(new_id, dev_id=1):  # dev_id=0 為廣播；立即生效
    return mb_write(dev_id, 0x0000, new_id)


MB_BAUD = {115200: 2, 460800: 3, 921600: 4, 1000000: 5}


def mb_set_baud(baud, dev_id=1):
    return mb_write(dev_id, 0x000A, MB_BAUD[baud])


def mb_parse_read(resp: bytes):
    if len(resp) < 5 or crc16_modbus(resp[:-2]) != struct.unpack("<H", resp[-2:])[0]:
        raise ValueError("Modbus 回包 CRC 錯：" + _hex(resp))
    if resp[1] != 0x03:
        raise ValueError("Modbus 異常回包：" + _hex(resp))
    n = resp[2]
    return struct.unpack("<%df" % (n // 4), resp[3:3 + n])  # 手冊：float 按小端字節序


# ---------------------------------------------------------------- CAN
def _s12(v):
    return v - 0x1000 if v & 0x800 else v


def _three12(b5: bytes):
    v = int.from_bytes(b5, "big") >> 4  # 4.5 字節 = 3×12 bit，最後半字節是保留 0
    return _s12((v >> 24) & 0xFFF), _s12((v >> 12) & 0xFFF), _s12(v & 0xFFF)


def can20_decode(head: bytes, data: bytes, model="56AS"):
    """CAN 2.0 兩幀（7 字節）：頭幀 [序][AA][Fx Fy Fz 4.5B]，數據幀 [序][Mx My Mz 4.5B][FF]。"""
    if head[1] != 0xAA or data[6] != 0xFF or head[0] != data[0]:
        raise ValueError("幀不配對：%s / %s" % (_hex(head), _hex(data)))
    kf, km = CAN_SCALE[model]
    fx, fy, fz = _three12(head[2:7])
    mx, my, mz = _three12(data[1:6])
    return fx * kf, fy * kf, fz * kf, mx * km, my * km, mz * km


def canfd_decode(data: bytes):
    """CAN-FD 32 字節：6×float32 小端 + 4 保留 + UINT32 幀序。"""
    vals = struct.unpack("<6f", data[:24])
    seq = struct.unpack("<I", data[28:32])[0]
    return vals, seq


# ---------------------------------------------------------------- 複合載荷判據
def load_ratio(vals, model):
    """手冊 3.3：Σ(分量/量程)² ≤ 1 才安全。"""
    f_fs, m_fs = RANGE[model]
    fs = (f_fs,) * 3 + (m_fs,) * 3
    return sum((v / s) ** 2 for v, s in zip(vals, fs))


# ---------------------------------------------------------------- 自檢
def selftest():
    hx = bytes.fromhex
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = all(abs(a - b) < 1e-3 for a, b in zip(got, want)) if isinstance(want, tuple) else got == want
        ok &= good
        print(("通過 " if good else "失敗 ") + name, got if not good else "")

    # Modbus 命令的 CRC（手冊第 5 章各例子）
    check("讀 Fx", with_crc(hx("010300030002")), hx("010300030002340B"))
    check("讀 Fz+Mx", with_crc(hx("010300070004")), hx("010300070004F5C8"))
    check("讀六維", mb_read_all(1), hx("01030003000CB5CF"))
    check("寫 ID", mb_set_id(2, 1), hx("010600000002080B"))
    check("廣播寫 ID", mb_set_id(2, 0), hx("00060000000209DA"))
    check("寫波特率 1M", mb_set_baud(1000000), hx("0106000A000569CB"))
    check("清零", mb_zero(1), hx("0106000E0000E809"))
    check("全零回包", with_crc(hx("010318") + bytes(24))[-2:], hx("6CF4"))
    check("Fx 回包解析", mb_parse_read(hx("0103046ADE54423920")), (53.2172,))
    check("float 解析", struct.unpack("<2f", hx("DE4B3D4291824142")), (47.3241, 48.3775))
    # 主動發送幀：用手冊的 12.5 / 13.5 拼一幀，再故意插垃圾字節和壞幀
    vals = (12.5, 13.5, -1.0, 0.25, 0.0, 0.5)
    good = with_crc(HEADER + struct.pack("<6f", *vals))
    bad = bytearray(good)
    bad[10] ^= 0xFF
    p = ActiveParser()
    got = p.feed(b"\x00\x53" + bytes(bad) + good[:7]) + p.feed(good[7:] + good)
    check("主動幀解析", got[0] if got else (), vals)
    check("主動幀數量/壞幀", (len(got), p.bad >= 1), (2, True))
    # CAN 2.0（手冊 6.2.2 例子，56AS）
    check("CAN2.0 解析", can20_decode(hx("4CAA0220060520"), hx("4C005FFD00E0FF")),
          (0.85, 0.15, 2.05, 0.005, -0.003, 0.014))
    # CAN-FD（手冊 6.2.1 例子：CD CC 44 41 = 12.3，9A 99 B9 41 = 23.2）
    check("CAN-FD 解析", struct.unpack("<2f", hx("CDCC44419A99B941")), (12.3, 23.2))
    # 複合載荷判據（手冊示例 1：200N/8Nm 量程，Fx=-80N、My=8Nm → 1.16）
    RANGE["_ex"] = (200, 8)
    check("複合載荷示例1", (round(load_ratio((-80, 0, 0, 0, 8, 0), "_ex"), 2),), (1.16,))
    print("全部通過" if ok else "有失敗項")
    return ok


# ---------------------------------------------------------------- 串口
def open_port(port, baud):
    try:
        import serial
    except ImportError:
        sys.exit("沒有 pyserial：pip install pyserial（Jetson 上 sudo apt install python3-serial）")
    try:
        return serial.Serial(port, baud, timeout=0.05)
    except (OSError, getattr(serial, "SerialException", OSError)) as e:
        msg = "打不開串口 %s：%s\n" % (port, e)
        if "ermission" in str(e):
            msg += "Linux 上沒有串口權限：sudo usermod -aG dialout $USER，然後退出重新登錄"
        else:
            msg += ("看一下串口名：Windows 用 python -m serial.tools.list_ports -v，"
                    "Linux 用 ls /dev/ttyUSB* /dev/serial/by-id/；也可能被別的程序佔着")
        sys.exit(msg)


def raw_dump(ser, seconds=0.0):
    """像串口助手那樣按十六進制顯示收到的字節；遇到 53 54 換行，方便看出 28 字節一幀。"""
    t0 = time.time()
    total = 0
    line = []

    def flush():
        if line:
            print(" ".join(line))
            line.clear()

    try:
        while not seconds or time.time() - t0 < seconds:
            data = ser.read(ser.in_waiting or 1)
            total += len(data)
            for b in data:
                if b == 0x54 and line and line[-1] == "53":
                    line.pop()
                    flush()
                    line.append("53")
                line.append("%02X" % b)
                if len(line) >= 32:
                    flush()
    except KeyboardInterrupt:
        pass
    flush()
    dt = max(time.time() - t0, 1e-9)
    print("共收到 %d 字節，%.0f 字節/秒" % (total, total / dt))
    if total == 0:
        print("一個字節都沒有：檢查供電、A/B 有沒有接反、485 模塊 GND 有沒有接；也可能是 Modbus 版（要先發命令才回）")


def scan(ser, dev_id=1):
    """每個波特率聽 1 秒主動幀，再發一次 Modbus 讀命令，報告哪種組合能收到 CRC 正確的數據。"""
    found = False
    for baud in (115200, 460800, 921600, 1000000):
        ser.baudrate = baud
        time.sleep(0.05)
        ser.reset_input_buffer()
        p = ActiveParser()
        good = []
        t0 = time.time()
        while time.time() - t0 < 1.0:
            good += p.feed(ser.read(ser.in_waiting or 1))
        mb = None
        ser.reset_input_buffer()
        ser.write(mb_read_all(dev_id))
        try:
            mb = mb_parse_read(ser.read(29))
        except ValueError:
            pass
        msg = "%7d bps：主動幀 %4d 個（壞 %d）" % (baud, len(good), p.bad)
        msg += "，Modbus(ID %d) %s" % (dev_id, "有回應" if mb else "無回應")
        print(msg)
        if good or mb:
            found = True
            v = good[-1] if good else mb
            print("          例：" + " ".join("%s=%+.3f" % kv for kv in zip(AXES, v)))
    if not found:
        print("哪個波特率都沒收到正確數據：先用 --raw 看有沒有字節進來")


def stream(ser, modbus=False, dev_id=1, period=0.0, on_error=print):
    """不停產出 (Fx, Fy, Fz, Mx, My, Mz)，單位 N / N·m。

    自己的程序裏可以直接用：
        import serial, hkvl56
        ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=0.05)
        it = hkvl56.stream(ser, modbus=True)
        zero = hkvl56.tare(it, 200)
        for raw in it:
            fx, fy, fz, mx, my, mz = (a - b for a, b in zip(raw, zero))
    """
    if modbus:
        while True:
            ser.reset_input_buffer()
            ser.write(mb_read_all(dev_id))
            try:
                yield mb_parse_read(ser.read(29))
            except ValueError as e:
                on_error(e)
            if period:
                time.sleep(period)
    else:
        p = ActiveParser()
        while True:
            for v in p.feed(ser.read(ser.in_waiting or 1)):
                yield v


def tare(it, n=200):
    """從 stream() 取 n 幀求平均，作為軟件零點；這時傳感器上不要加載。"""
    acc = [0.0] * 6
    for _ in range(n):
        acc = [a + b for a, b in zip(acc, next(it))]
    return tuple(a / n for a in acc)


def run(args):
    ser = open_port(args.port, args.baud)
    if args.raw:
        return raw_dump(ser, args.seconds)
    if args.scan:
        return scan(ser, args.id)
    if args.hw_zero:
        ser.write(mb_zero(args.id))
        time.sleep(0.1)
        ser.reset_input_buffer()
        print("已發硬件清零命令（斷電丟失；主動發送模式下是否支持手冊沒寫，以讀數為準）")

    it = stream(ser, args.modbus, args.id, args.period)
    offset = (0.0,) * 6
    if args.tare:
        print("去皮中，傳感器上先不要加載……")
        offset = tare(it, args.tare)
        print("零點：", " ".join("%s=%+.3f" % kv for kv in zip(AXES, offset)))

    csv = open(args.csv, "w", encoding="utf-8") if args.csv else None
    if csv:
        csv.write("t," + ",".join(AXES) + "\n")
    t0 = time.time()
    n = 0
    last = 0.0
    try:
        for raw in it:
            v = tuple(a - b for a, b in zip(raw, offset))
            t = time.time() - t0
            n += 1
            if csv:
                csv.write("%.4f," % t + ",".join("%.4f" % x for x in v) + "\n")
            if args.seconds and t >= args.seconds:
                break
            if t - last >= 0.1:
                last = t
                line = " ".join("%s=%+8.3f" % kv for kv in zip(AXES, v))
                if args.model:
                    line += "  負載率=%.2f" % load_ratio(raw, args.model)
                print("%7.2fs %5.0fHz  %s" % (t, n / t if t else 0, line))
    except KeyboardInterrupt:
        pass
    finally:
        if csv:
            csv.close()
        ser.close()


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="HKVL-56 六維力傳感器讀取")
    ap.add_argument("port", nargs="?", help="串口，例如 COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--modbus", action="store_true", help="Modbus-RTU 主從模式（默認是主動發送模式）")
    ap.add_argument("--id", type=int, default=1, help="Modbus 從機地址")
    ap.add_argument("--period", type=float, default=0.0, help="Modbus 輪詢間隔 s")
    ap.add_argument("--tare", type=int, default=200, help="開始時平均多少幀作為軟件零點，0 = 不去皮")
    ap.add_argument("--hw-zero", action="store_true", help="先發硬件清零命令")
    ap.add_argument("--model", choices=sorted(RANGE), default="56C", help="型號，用來顯示複合負載率（默認 56C）")
    ap.add_argument("--csv", help="記錄到 CSV 文件")
    ap.add_argument("--raw", action="store_true", help="只按十六進制顯示收到的字節（代替串口助手）")
    ap.add_argument("--scan", action="store_true", help="自動試 4 種波特率和 Modbus，看哪個能讀到數據")
    ap.add_argument("--seconds", type=float, default=0.0, help="--raw 或讀數運行多少秒後自動停止，0 = 按 Ctrl+C 才停")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    if not args.port:
        ap.error("要給串口號，或者用 --selftest")
    run(args)


if __name__ == "__main__":
    main()
