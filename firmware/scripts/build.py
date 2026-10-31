#!/usr/bin/env python3
"""Compile only; never opens a serial port. 仅编译，不打开串口。"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import shutil
import sys

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--check-enabled', action='store_true', help='Compile and link enabled paths with a permanent idle gate')
a = p.parse_args()
name = 'esp32s3_compilecheck' if a.check_enabled else 'esp32s3'
if a.check_enabled:
    text = (root / 'sdkconfig.defaults').read_text()
    for key in ('GW_WIFI_ENABLE', 'GW_UART_PINS_CONFIRMED'):
        text = text.replace(f'# CONFIG_{key} is not set', f'CONFIG_{key}=y')
    for role, pin in [('TX', 4), ('RX', 5), ('DTR', 6), ('RTS', 7)]:
        text = text.replace(f'CONFIG_GW_{role}_PIN=-1', f'CONFIG_GW_{role}_PIN={pin}')
    # Synthetic values retain real linkage; compile-only latch prevents all I/O.
    ssid = 'compile-fixture-' + 'not-a-network'
    secret = 'compile-fixture-' + 'not-a-secret'
    key_prefix = 'CONFIG_GW_WIFI_'
    text += '\nCONFIG_GW_COMPILE_CHECK_ONLY=y\n'
    text += f'{key_prefix}SSID="{ssid}"\n'
    text += f'{key_prefix}PASSWORD="{secret}"\n'
    (root / 'sdkconfig.compilecheck').write_text(text)
env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(root))
result = subprocess.run([sys.executable, '-m', 'platformio', 'run', '-d', str(root), '-e', name], env=env)
if not result.returncode:
    for filename in ('firmware.bin', 'firmware.elf', 'bootloader.bin', 'partitions.bin'):
        path = root / '.pio' / 'build' / name / filename
        if path.exists():
            saved = root / 'artifacts' / name / filename
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
            print('SHA256', hashlib.sha256(saved.read_bytes()).hexdigest(), saved, saved.stat().st_size)
sys.exit(result.returncode)
