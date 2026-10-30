"""User-defined naming hints; they never change protocol routing or interfaces."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import tempfile


@dataclass(frozen=True)
class NamingPrefix:
    prefix: str
    meaning: str
    description: str = ""


NO_PREFIX = NamingPrefix("", "无前缀 · 普通数据结构", "例如 PlayerInfo、ItemInfo，也可直接输入完整的自定义名称。")
DEFAULT_PREFIXES = (
    NamingPrefix("C2G_", "客户端 → Gate", "客户端发送给 Gate 服务器，例如登录、账号操作等协议。"),
    NamingPrefix("G2C_", "Gate → 客户端", "Gate 服务器发送给客户端，例如操作结果或服务器推送。"),
    NamingPrefix("C2M_", "客户端 → Map", "客户端发送给 Map 服务器，例如场景中的移动或战斗请求。"),
    NamingPrefix("M2C_", "Map → 客户端", "Map 服务器发送给客户端，例如场景状态或战斗结果。"),
)


def validate_prefixes(entries):
    seen = set()
    for entry in entries:
        if not isinstance(entry, NamingPrefix) or not all(isinstance(value, str) for value in
                (entry.prefix, entry.meaning, entry.description)):
            raise ValueError("前缀、含义和详细说明必须是文本。")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*_", entry.prefix):
            raise ValueError("前缀必须以英文字母开头、以下划线结尾，只能包含英文字母、数字和下划线，例如 C2Chat_。")
        if entry.prefix in seen:
            raise ValueError(f"前缀重复：{entry.prefix}")
        if not entry.meaning.strip() or "\n" in entry.meaning or "\r" in entry.meaning:
            raise ValueError("请填写一行简短含义，例如 客户端 → 聊天服务器。")
        seen.add(entry.prefix)


def load_prefixes(path: Path):
    if not path.exists():
        return list(DEFAULT_PREFIXES)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        entries = [NamingPrefix(**item) for item in data["prefixes"]]
        validate_prefixes(entries)
        return entries
    except (TypeError, KeyError, ValueError) as error:
        raise ValueError(f"命名前缀配置无效：{error}") from error


def save_prefixes(path: Path, entries):
    validate_prefixes(entries)
    data = json.dumps({"prefixes": [asdict(entry) for entry in entries]}, ensure_ascii=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".prefixes-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def split_name(name, entries):
    for entry in sorted(entries, key=lambda item: len(item.prefix), reverse=True):
        if name.startswith(entry.prefix):
            return entry.prefix, name[len(entry.prefix):]
    return "", name


def compose_name(prefix, name):
    name = name.strip()
    # Pasting a full name with the selected prefix must not duplicate it.
    return name if prefix and name.startswith(prefix) else prefix + name
