"""Offline presentation translations and independent per-user language preferences.

Importing this module performs no file writes, device inspection or network I/O.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from string import Formatter
from typing import Any

from .locales import EN

DEFAULT_LANGUAGE = "zh-CN"
LANGUAGES = {"zh-CN": "简体中文", "en": "English"}


def normalize_language(language: str | None) -> str:
    value = str(language or "").strip().lower().replace("_", "-")
    return "en" if value == "english" or value == "en" or value.startswith("en-") else DEFAULT_LANGUAGE


def preferences_path() -> Path:
    from .profiles import default_config_path
    return default_config_path().with_name("preferences.json")


def load_language(path: str | Path | None = None) -> str:
    target = Path(path) if path is not None else preferences_path()
    try:
        if target.stat().st_size > 16384:
            return DEFAULT_LANGUAGE
        data = json.loads(target.read_text(encoding="utf-8-sig"))
        return normalize_language(data.get("language")) if isinstance(data, dict) else DEFAULT_LANGUAGE
    except (OSError, ValueError, UnicodeError):
        return DEFAULT_LANGUAGE


def resolve_language(language: str | None = None) -> str:
    return normalize_language(language) if language is not None else load_language()


def save_language(language: str, path: str | Path | None = None) -> None:
    target = Path(path) if path is not None else preferences_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    try:
        if target.stat().st_size <= 16384:
            previous = json.loads(target.read_text(encoding="utf-8-sig"))
            if isinstance(previous, dict):
                data = previous
    except (OSError, ValueError, UnicodeError):
        pass
    data["language"] = normalize_language(language)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=".preferences-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def translate(text: Any, language: str = DEFAULT_LANGUAGE, **values: Any) -> str:
    source = str(text)
    template = EN.get(source, source) if normalize_language(language) == "en" else source
    return template.format(**values) if values else template


# Match already formatted legacy messages without translating arbitrary profile names.
_PATTERNS = []
for _source, _target in sorted(EN.items(), key=lambda item: len(re.sub(r"\{[^}]*\}", "", item[0])), reverse=True):
    if "{" not in _source:
        continue
    _parts = []
    for _literal, _field, _spec, _conversion in Formatter().parse(_source):
        _parts.append(re.escape(_literal))
        if _field is not None:
            _parts.append(f"(?P<{_field}>.*?)")
    _PATTERNS.append((re.compile("^" + "".join(_parts) + "$", re.DOTALL), _target))


def translate_event(message: Any, language: str = DEFAULT_LANGUAGE) -> str:
    from ._gui_runtime import clean_text
    source = clean_text(message, 4000)
    if normalize_language(language) != "en":
        return source
    if source in EN:
        return EN[source]
    if source in EN.values():
        return source
    for pattern, target in _PATTERNS:
        match = pattern.fullmatch(source)
        if match:
            values = {key: (value if key in ("name", "names", "filename", "path") else EN.get(value, value)) for key, value in match.groupdict().items()}
            for key in ("error", "task", "action", "heading", "prefix", "mode", "kind", "title", "status", "verdict", "check", "scope", "message", "warning", "protocol_help"):
                if key in values:
                    values[key] = translate_event(values[key], "en")
            return target.format(**values)
    prefix = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):\s*(.*)", source, re.DOTALL)
    if prefix:
        return prefix[1] + ": " + translate_event(prefix[2], "en")
    if "\n\n" in source:
        return "\n\n".join(translate_event(part, language) for part in source.split("\n\n"))
    if not re.search(r"[\u3400-\u9fff]", source):
        return source
    # Unknown external errors must not leak untranslated text or arbitrary payloads.
    codes = re.findall(r"\b(?:[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)|E[A-Z_]{2,}|0x[0-9A-Fa-f]+)\b|(?:WinError|errno|错误码)\s*[:=]?\s*(-?\d+)", source)
    details = re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)|\bE[A-Z_]{2,}\b|\b0x[0-9A-Fa-f]+\b", source)
    details.extend("code " + code for code in codes if code)
    return "Unrecognized event" + (" (" + ", ".join(dict.fromkeys(details)) + ")" if details else "")


def translate_error(error: Any, language: str = DEFAULT_LANGUAGE) -> str:
    if isinstance(error, BaseException):
        name = type(error).__name__
        code = getattr(error, "winerror", None) or getattr(error, "errno", None)
        rendered = translate_event(str(error), language)
        if rendered.startswith("Unrecognized event"):
            rendered = "Operation failed"
        suffix = f" (code {code})" if code is not None else ""
        return f"{name}: {rendered}{suffix}"
    return translate_event(error, language)


class Translator:
    def __init__(self, language: str | None = None) -> None:
        self.language = resolve_language(language)

    def __call__(self, text: Any, **values: Any) -> str:
        return translate(text, self.language, **values)

    def event(self, message: Any) -> str:
        return translate_event(message, self.language)

    def error(self, error: Any) -> str:
        return translate_error(error, self.language)
