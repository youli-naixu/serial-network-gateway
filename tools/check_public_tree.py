"""Check publishable Git paths without printing potentially sensitive matches."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


PATTERNS = {
    "absolute-windows-path": re.compile(r"(?i)\b[A-Z]:[\\/](?:Users|CODEX|Tools)[\\/]"),
    "private-ip": re.compile(r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"),
    "hardware-address": re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-secret": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "local-owner": re.compile(r"(?i)tomori[ _-]?nao|homelab-ops|0vo\.top"),
}
# Placeholder ranges used in examples and tests; real deployments must not appear.
ALLOWED_EXAMPLE_IPS = re.compile(r"\b(?:192\.168\.50\.\d{1,3}|10\.0\.0\.0/16)\b")
BANNED_PARTS = {".local-tools", ".local", ".codex", ".pio", ".pio-core", "__pycache__", "artifacts", "dist", "build"}
BANNED_SUFFIXES = {".exe", ".dll", ".sys", ".cat", ".pfx", ".p12", ".pem", ".key", ".zip", ".bin"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".toml", ".ini", ".yml", ".yaml", ".c", ".h", ".csv", ".spec"}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    command = ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    paths = sorted(set(subprocess.check_output(command).decode("utf-8").split("\0")) - {""})
    errors = []
    for relative in paths:
        path = root / relative
        if not path.is_file():
            continue
        if any(part in BANNED_PARTS for part in path.relative_to(root).parts) or path.suffix.lower() in BANNED_SUFFIXES:
            errors.append((relative, 0, "non-source artifact"))
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            errors.append((relative, 0, "oversized file"))
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in ("LICENSE", "NOTICE", "Kconfig.projbuild", "CMakeLists.txt", ".gitignore", ".gitattributes", ".editorconfig"):
            continue
        text = path.read_text(encoding="utf-8-sig")
        if path.resolve() == Path(__file__).resolve():
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for kind, pattern in PATTERNS.items():
                checked = ALLOWED_EXAMPLE_IPS.sub("", line) if kind == "private-ip" else line
                if pattern.search(checked):
                    errors.append((relative, number, kind))
            if re.search(r"CONFIG_\w*(?:PASSWORD|SSID)\w*\s*=\s*\"[^\"]+\"", line):
                errors.append((relative, number, "nonempty wireless credential"))
        if path.suffix == ".md":
            prose = re.sub(r"```.*?```", "", text, flags=re.S)
            prose = re.sub(r"`[^`\n]*`", "", prose)
            for target in re.findall(r"\]\(([^)]+)\)", prose):
                target = target.strip("<>").split("#", 1)[0]
                if not target or "://" in target or target.startswith(("mailto:", "#")):
                    continue
                if not (path.parent / target).exists():
                    errors.append((relative, 0, "broken relative documentation link"))
    for path, line, reason in errors:
        print(f"{path}:{line}: {reason}")
    print(f"Scanned {len(paths)} publishable paths; {len(errors)} issue(s).")
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
