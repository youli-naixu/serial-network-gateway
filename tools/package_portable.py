"""Refresh portable documents and package an already validated Windows build."""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path


def package_portable(root: Path) -> Path:
    app_dir = root / "dist" / "SerialGateway"
    if not (app_dir / "SerialGateway.exe").is_file() or not (app_dir / "SerialGateway-cli.exe").is_file():
        raise FileNotFoundError("请先构建GUI和CLI。")
    for relative in ("README.zh-CN.md", "docs", "examples"):
        source = root / relative
        target = app_dir / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
            shutil.copytree(source, app_dir / "_internal" / relative, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
            shutil.copy2(source, app_dir / "_internal" / relative)
    manifest = app_dir / "SHA256SUMS.json"
    checksums = {
        str(path.relative_to(app_dir)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(app_dir.rglob("*")) if path.is_file() and path != manifest
    }
    manifest.write_text(json.dumps(checksums, indent=2) + "\n", encoding="utf-8")
    version = (root / "serial_gateway" / "__init__.py").read_text(encoding="utf-8").split('__version__ = "', 1)[1].split('"', 1)[0]
    archive = root / "dist" / f"ASR-Wireless-Flash-Assistant-{version}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(app_dir.parent))
    return archive


if __name__ == "__main__":
    archive = package_portable(Path(__file__).resolve().parents[1])
    print(archive)
    print("SHA256 " + hashlib.sha256(archive.read_bytes()).hexdigest())
