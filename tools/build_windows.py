"""Build a portable Windows folder after the GUI has been visually checked."""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="构建串口网络网关 Windows 便携版。")
    parser.add_argument("--preview-checked", action="store_true", help="已检查当前GUI预览后再构建")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("请在 Windows 上构建 Windows 程序。")
    if not args.preview_checked:
        parser.error("请先运行GUI并检查布局，再添加 --preview-checked。")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=root, check=True)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(root / "dist"), "--workpath", str(root / "build"),
        str(root / "SerialGateway.spec"),
    ], cwd=root, check=True)
    app_dir = root / "dist" / "SerialGateway"
    subprocess.run([str(app_dir / "SerialGateway-cli.exe"), "selftest", "--output", str(root / "artifacts" / "packaged-selftest.json")], check=True)
    from package_portable import package_portable
    archive = package_portable(root)
    print(archive)
    print("SHA256 " + hashlib.sha256(archive.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
