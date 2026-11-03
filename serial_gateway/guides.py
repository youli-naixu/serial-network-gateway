"""In-app help texts. Kept in sync with docs/wiring.zh-CN.md and examples/README.zh-CN.md."""
from __future__ import annotations

import sys
from pathlib import Path

WIRING_ZH = """## 需要接 3 组线（两块板都用 3.3V 电平，直接连）
1. 下载口（烧录用，必接）
    ESP32 GPIO9  ──→  ASR PB6（下载口 RX）
    ESP32 GPIO10 ←──  ASR PB5（下载口 TX）
2. 业务串口（ESP32 用它通知 ASR 重启进入下载模式）
    ESP32 GPIO2  ──→  ASR PA6（串口 RX）
    ESP32 GPIO1  ←──  ASR PA5（串口 TX）
3. 共地
    ESP32 GND  ───  ASR GND
## 烧录前
- 拔掉 ASR 板上的 USB 线。板载 USB 芯片的发送脚也接在 PB6 上，插着会和 ESP32 抢这根线，烧录会失败。
- ASR 程序里要有接收“asr_isp”重启命令的代码（见“示例代码”）。没有的话，可以在烧录软件等待时手动给 ASR 重新上电。
## 线接错时会看到什么
- 准备情况里“ASR 没回应重启命令”：业务串口两根线接反、没共地，或 ASR 程序里没有 asr_isp。
- “ASR 收到了重启命令，但下载口没响应”：GPIO9/10 与 PB6/PB5 接反或没接，或 ASR 的 USB 没拔。
- 一直“ESP32 连不上”：ESP32 没开机、不在同一个网络，或地址填错。
## 引脚可以换吗
可以。示例代码里 TX/RX 引脚都是常量，改成你实际接的引脚即可；本程序不关心 ESP32 用哪几个引脚。
"""

DRIVER_ZH = """## com0com 是什么
Windows 上的开源虚拟串口驱动。它造出一对互相连通的串口：一头（例如 COM8）给天问Block 用，另一头由本程序接走，再经 Wi-Fi 送给 ESP32。
## 怎么安装
1. 点“打开下载页”，下载 com0com-3.0.0.0-i386-and-x64-signed.zip（名字里带 signed 的签名版；Windows 10/11 只能装签名版）。
2. 解压后以管理员身份运行和你的系统匹配的安装程序（64 位 Windows 选带 x64 的那个），按默认选项装完即可，不需要手动建端口。
3. 回到本程序点“装好了，重新检查”，再按提示点“准备虚拟串口”。本程序会把它改名为“ASR 无线烧录”。
## 为什么不用别的虚拟串口软件
烧录过程中官方工具会把波特率从 115200 切到 750000、再切到 1500000，还会用到 DTR/RTS 控制线。com0com 能把这些都转发过来；一些免费的虚拟串口软件做不到。
"""

EXAMPLES_ZH = """## ESP32 端
arduino/examples/AsrWirelessFlash：最小可用的 ESP32 程序（Arduino），只做三件事：连 Wi-Fi、提供“进入下载模式”接口、在 3333 端口转发串口。改好 Wi-Fi 名称密码和引脚就能用。
如果想把它加进你自己的 ESP32 程序，见 arduino/README.zh-CN.md 里“嵌入现有工程”一节。
## ASR 端
examples/asrpro/asr_isp_snippet.cpp：只有十几行，贴进你的天问工程，收到 <asr_isp> 就回复并重启进入下载模式。
examples/asrpro/asr_isp_minimal.hd：可以直接用天问Block 打开编译的最简工程，带语音提示“收到烧录命令，正在重启进入下载模式”，说完再重启。
## 第一次怎么把带 asr_isp 的程序烧进 ASR
第一次只能用 USB 线按普通方式烧录（因为旧程序还不认识重启命令）。之后就都可以无线烧录了。
"""


WIRING_EN = """## Three groups of wires (both boards use 3.3 V logic; connect directly)
1. Download port (for flashing, required)
    ESP32 GPIO9  ──→  ASR PB6 (download RX)
    ESP32 GPIO10 ←──  ASR PB5 (download TX)
2. Application UART (the ESP32 uses it to tell the ASR to restart into download mode)
    ESP32 GPIO2  ──→  ASR PA6 (UART RX)
    ESP32 GPIO1  ←──  ASR PA5 (UART TX)
3. Common ground
    ESP32 GND  ───  ASR GND
## Before flashing
- Unplug the USB cable from the ASR board. Its USB chip also drives PB6 and would fight the ESP32 on that wire.
- The ASR firmware must handle the "asr_isp" restart command (see Example code). Without it, power-cycle the ASR by hand while the flashing tool waits.
## What wrong wiring looks like
- "The ASR did not answer the restart command": application UART wires swapped, no common ground, or no asr_isp in the ASR firmware.
- "Received the restart command but the download port stayed silent": GPIO9/10 and PB6/PB5 swapped or missing, or the ASR's USB still plugged in.
- "Cannot reach ESP32": the ESP32 is off, on another network, or the address is wrong.
## Can I use other pins?
Yes. The example sketch defines the TX/RX pins as constants; change them to match your wiring. This program does not care which ESP32 pins you use.
"""

DRIVER_EN = """## What com0com is
An open-source virtual serial port driver for Windows. It creates a pair of connected ports: one end (for example COM8) is used by TianwenBlock, the other end is read by this program and sent to the ESP32 over Wi-Fi.
## How to install
1. Click "Open download page" and download com0com-3.0.0.0-i386-and-x64-signed.zip (the signed build; Windows 10/11 only accept signed drivers).
2. Unzip it and run the installer that matches your system (x64 for 64-bit Windows) as administrator. Default options are fine; no ports need to be created by hand.
3. Back here, click "Installed, check again", then "Prepare virtual COM". This program renames the port to "ASR 无线烧录".
## Why not another virtual COM product
During a download the official tool switches from 115200 to 750000 and then 1500000 baud and uses the DTR/RTS lines. com0com forwards all of that; some free virtual COM products do not.
"""

EXAMPLES_EN = """## ESP32 side
arduino/examples/AsrWirelessFlash: a minimal ESP32 (Arduino) sketch that does three things: joins Wi-Fi, offers an "enter download mode" endpoint, and relays the serial port on TCP port 3333. Set your Wi-Fi name, password and pins and it is ready.
To add it to your own ESP32 project, see "Embedding" in arduino/README.md.
## ASR side
examples/asrpro/asr_isp_snippet.cpp: about a dozen lines to paste into your TianwenBlock project; on <asr_isp> it replies and restarts into download mode.
examples/asrpro/asr_isp_minimal.hd: a minimal project that TianwenBlock opens and builds directly. It says "Flashing command received, restarting into download mode" and then restarts.
## The first time
The first firmware with asr_isp has to go in over USB the normal way (the old firmware does not know the command). After that every download can be wireless.
"""


def guide(name: str, language: str) -> str:
    table = {"wiring": (WIRING_ZH, WIRING_EN), "driver": (DRIVER_ZH, DRIVER_EN), "examples": (EXAMPLES_ZH, EXAMPLES_EN)}
    zh, en = table[name]
    return en if language == "en" else zh

def examples_dir() -> Path | None:
    """The bundled examples folder (next to the exe when packaged, the repo root from source)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    folder = base / "examples"
    return folder if folder.is_dir() else None
