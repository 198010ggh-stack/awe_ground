#!/usr/bin/env bash
# 在 Jetson 上編譯安裝 CH340/CH341 串口驅動（ch341.ko）。
# JetPack 6（L4T R36，內核 5.15.148-tegra）的內核沒帶這個驅動，USB 轉 485 模塊插上後
# lsusb 能看到 1a86:7522，但沒有 /dev/ttyUSB0。跑一次這個腳本就好，以後插上自動加載。
#
# 用法（普通用戶運行，中途會要 sudo 密碼）：  bash nx_install_ch341.sh
# NX 沒網時：在電腦上下載下面 SRC 地址裏的 ch341.c（必要時還有 usb-serial.c generic.c bus.c），
#            放進 ~/ch341-build/ 再運行，腳本會跳過下載。
# 以後 apt 升級了 L4T 內核（uname -r 變了），要重新跑一次。
set -e   # 不用 pipefail：grep -q 提前退出會讓 dmesg 收到 SIGPIPE，誤判成沒找到

KVER=$(uname -r)            # 例如 5.15.148-tegra
BASE=${KVER%%-*}            # 例如 5.15.148，用同版本的官方源碼
BUILD=${CH341_KDIR:-/lib/modules/$KVER/build}   # 環境變量只用來測試，平時不用管
WORK=${CH341_WORK:-$HOME/ch341-build}
SRC=https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/drivers/usb/serial

say() { printf '\n== %s\n' "$*"; }

if [ "$(id -u)" = 0 ]; then
    echo "不要用 sudo 運行：直接 bash nx_install_ch341.sh，需要時腳本自己會要密碼"
    exit 1
fi
ME=$(id -un)

say "內核 $KVER"

if lsmod | grep -q '^ch341 '; then
    say "ch341 驅動已經加載，不用裝"
elif modinfo ch341 >/dev/null 2>&1; then
    say "系統裏有 ch341，只是沒加載，直接加載"
    sudo modprobe ch341
else
    # 1. 內核頭文件和編譯工具
    if [ ! -f "$BUILD/Makefile" ] || ! command -v make >/dev/null || ! command -v gcc >/dev/null; then
        say "安裝內核頭文件和編譯工具"
        sudo apt-get update
        sudo apt-get install -y nvidia-l4t-kernel-headers build-essential
    fi
    if [ ! -f "$BUILD/Makefile" ]; then
        echo "找不到 $BUILD/Makefile：內核頭文件沒裝好，把上面的輸出發給 Claude"
        exit 1
    fi

    # 2. usb-serial 核心在不在（內置或模塊都算）；不在就一起編
    NEED_CORE=0
    if [ ! -d /sys/bus/usb-serial ] && ! modinfo usbserial >/dev/null 2>&1; then
        NEED_CORE=1
        say "內核也沒有 usbserial 核心，一起編譯"
    fi

    # 3. 下載和內核同版本的源碼
    mkdir -p "$WORK"
    cd "$WORK"
    FILES="ch341.c"
    if [ "$NEED_CORE" = 1 ]; then FILES="$FILES usb-serial.c generic.c bus.c"; fi
    for f in $FILES; do
        if [ -s "$f" ]; then
            say "已有 $f，跳過下載"
        else
            say "下載 $f（Linux v$BASE）"
            curl -fsSL --retry 3 -o "$f" "$SRC/$f?h=v$BASE" || rm -f "$f"
            if [ ! -s "$f" ]; then
                echo "下載失敗：檢查 NX 能不能上網，或者按文件頭的說明手動放進 $WORK/"
                exit 1
            fi
        fi
    done
    if ! grep -q '0x7522' ch341.c; then
        echo "注意：這版 ch341.c 的設備表裏沒有 CH340K(1a86:7522)，裝好後要手動加 ID"
    fi

    # 4. 編譯（失敗時先準備一下頭文件裏的編譯工具再試一次）
    {
        echo "obj-m += ch341.o"
        if [ "$NEED_CORE" = 1 ]; then
            echo "obj-m += usbserial.o"
            echo "usbserial-y := usb-serial.o generic.o bus.o"
        fi
    } > Makefile
    say "編譯"
    if ! make -C "$BUILD" M="$WORK" modules; then
        say "第一次編譯失敗，準備頭文件裏的編譯工具後重試"
        sudo make -C "$BUILD" modules_prepare
        make -C "$BUILD" M="$WORK" modules
    fi

    # 5. 安裝到 extra/，depmod 之後插上模塊會自動加載
    say "安裝"
    sudo mkdir -p "/lib/modules/$KVER/extra"
    sudo cp ch341.ko "/lib/modules/$KVER/extra/"
    if [ "$NEED_CORE" = 1 ]; then sudo cp usbserial.ko "/lib/modules/$KVER/extra/"; fi
    sudo depmod -a
    if ! sudo modprobe ch341; then
        echo "加載失敗，內核日誌："
        sudo dmesg | tail -15
        exit 1
    fi
fi

# 6. 串口權限和 pyserial
if ! id -nG "$ME" | grep -qw dialout; then
    sudo usermod -aG dialout "$ME"
    DIALOUT_NEW=1
fi
if ! python3 -c "import serial" 2>/dev/null; then
    say "安裝 pyserial"
    sudo apt-get install -y python3-serial
fi

# 7. 檢查結果
sleep 2
say "結果"
if ls /dev/ttyUSB* >/dev/null 2>&1; then
    ls -l /dev/ttyUSB* /dev/serial/by-id/ 2>/dev/null || true
    echo "成功：485 模塊已經是串口了。"
else
    echo "還沒看到 /dev/ttyUSB*：把 485 模塊拔下來再插上，然後 ls /dev/ttyUSB*"
fi
if sudo dmesg | grep -qi brltty; then
    echo "內核日誌裏有 brltty：它會搶串口，運行 sudo apt remove brltty 後重插模塊"
fi
if [ "${DIALOUT_NEW:-0}" = 1 ]; then
    echo "已把 $ME 加入 dialout 組：退出重新登錄（或重啟）後才有串口權限"
fi
