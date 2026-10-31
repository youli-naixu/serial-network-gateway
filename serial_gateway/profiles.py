from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import GatewayProfile

SCHEMA_VERSION = 1


def default_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "SerialGateway" / "profiles.json"


class ProfileStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_config_path()

    def load(self) -> tuple[list[GatewayProfile], str]:
        if not self.path.exists():
            profile = GatewayProfile()
            return [profile], profile.name
        if self.path.stat().st_size > 1024 * 1024:
            raise ValueError("配置文件超过 1 MiB，请检查文件。")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("配置文件不是有效的 UTF-8 JSON；原文件未修改。") from exc
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("不支持该配置文件版本；原文件未修改。")
        raw_profiles = data.get("profiles")
        if not isinstance(raw_profiles, list) or not 1 <= len(raw_profiles) <= 100:
            raise ValueError("配置文件应包含 1–100 个配置档。")
        profiles = [GatewayProfile.from_dict(item) for item in raw_profiles]
        names = [p.name for p in profiles]
        if len(set(names)) != len(names):
            raise ValueError("配置文件含有重名配置档。")
        selected = data.get("selected", names[0])
        if selected not in names:
            raise ValueError("已选择的配置档不存在。")
        return profiles, selected

    def save(self, profiles: list[GatewayProfile], selected: str) -> None:
        if not 1 <= len(profiles) <= 100:
            raise ValueError("需要保留 1–100 个配置档。")
        for profile in profiles:
            profile.validate()
        names = [p.name for p in profiles]
        if len(set(names)) != len(names) or selected not in names:
            raise ValueError("配置档名称重复，或所选配置档不存在。")
        data = {"schema_version": SCHEMA_VERSION, "selected": selected, "profiles": [p.to_dict() for p in profiles]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix=".profiles-", suffix=".tmp", delete=False) as handle:
                temp_path = Path(handle.name)
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def get(self, name: str | None = None) -> GatewayProfile:
        profiles, selected = self.load()
        wanted = name if name is not None else selected
        for profile in profiles:
            if profile.name == wanted:
                return profile
        raise ValueError(f"找不到配置档：{wanted}")
