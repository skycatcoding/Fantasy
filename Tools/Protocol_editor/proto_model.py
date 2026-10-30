"""Lossless, span-based editing of Fantasy's line-oriented protocol dialect."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import tempfile


class ProtocolError(ValueError):
    pass


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
TYPE_NAME = r"\.?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
FIELD_TYPE = rf"(?:map\s*<\s*{TYPE_NAME}\s*,\s*{TYPE_NAME}\s*>|{TYPE_NAME})"
FIELD_PATTERN = re.compile(
    rf"(?m)^(?P<indent>[ \t]*)(?:(?P<label>repeatedArray|repeatedList|repeated|optional|required)\s+)?"
    rf"(?P<type>{FIELD_TYPE})[ \t]+(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<number>\d+)"
    rf"(?P<options>\s*\[[^\n]*?\])?\s*;[ \t]*(?=\n|\r|$)"
)
INTERFACES = (
    "", "IMessage", "IRequest", "IResponse", "IAddressMessage", "IAddressRequest", "IAddressResponse",
    "IAddressableMessage", "IAddressableRequest", "IAddressableResponse",
    "ICustomRouteMessage", "ICustomRouteRequest", "ICustomRouteResponse",
    "IRoamingMessage", "IRoamingRequest", "IRoamingResponse",
)
RESPONSE_TYPES = {
    "IRequest": "IResponse", "IAddressRequest": "IAddressResponse",
    "IAddressableRequest": "IAddressableResponse", "ICustomRouteRequest": "ICustomRouteResponse",
    "IRoamingRequest": "IRoamingResponse",
}
ROUTED_TYPES = {"ICustomRouteMessage", "ICustomRouteRequest", "IRoamingMessage", "IRoamingRequest"}
SCALARS = ("string", "bool", "int32", "int64", "uint32", "uint64", "sint32", "sint64",
           "fixed32", "fixed64", "sfixed32", "sfixed64", "float", "double", "bytes", "byte")


def mask_source(source: str) -> str:
    """Blank comments/strings while retaining offsets, newlines, and real braces."""
    chars = list(source)
    index = 0
    while index < len(source):
        if source.startswith("//", index):
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
        elif source.startswith("/*", index):
            closing = source.find("*/", index + 2)
            if closing < 0:
                raise ProtocolError("存在未闭合的 /* 注释。")
            end = closing + 2
        elif source[index] in "\"'":
            quote = source[index]
            end = index + 1
            while end < len(source):
                if source[end] == "\\":
                    end += 2
                elif source[end] == quote:
                    end += 1
                    break
                else:
                    end += 1
            else:
                raise ProtocolError("存在未闭合的字符串。")
        elif source[index] == "#" and not source[source.rfind("\n", 0, index) + 1:index].strip():
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
        else:
            index += 1
            continue
        for offset in range(index, min(end, len(chars))):
            if chars[offset] not in "\r\n":
                chars[offset] = " "
        index = end
    return "".join(chars)


def line_end(source: str, offset: int) -> int:
    end = source.find("\n", offset)
    return len(source) if end < 0 else end + 1


def attached_start(source: str, offset: int, *, message: bool = False) -> int:
    start = offset
    while start > 0:
        previous_end = start - 1
        previous_start = source.rfind("\n", 0, previous_end) + 1
        previous = source[previous_start:start].strip()
        allowed = previous.startswith("///") and not previous.startswith("////")
        if message:
            allowed = allowed or previous.startswith(("// Protocol ", "// using ", "////"))
        else:
            allowed = allowed or previous.startswith("#")
        if not allowed:
            break
        start = previous_start
    return start


def description(prefix: str, inline: str = "") -> str:
    lines = [line.strip()[3:].strip() for line in prefix.splitlines()
             if line.strip().startswith("///") and not line.strip().startswith("////")]
    if inline.lstrip().startswith("//"):
        lines.append(inline.lstrip().lstrip("/").strip())
    return "\n".join(lines)


def replace_spans(source: str, edits: list[tuple[int, int, str]]) -> str:
    previous = len(source) + 1
    for start, end, replacement in sorted(edits, reverse=True):
        if end > previous or start < 0 or end < start:
            raise ProtocolError("编辑范围发生重叠。")
        source = source[:start] + replacement + source[end:]
        previous = start
    return source


@dataclass
class ProtoField:
    name: str
    type_name: str
    number: int
    label: str
    description: str
    start: int
    end: int
    code_start: int
    name_span: tuple[int, int]
    type_span: tuple[int, int]
    number_span: tuple[int, int]
    options: str = ""
    indent: str = "    "


@dataclass
class Message:
    name: str
    annotation: str
    description: str
    serialization: str
    start: int
    declaration_start: int
    end: int
    open_brace: int
    close_brace: int
    name_span: tuple[int, int]
    annotation_span: tuple[int, int] | None
    fields: list[ProtoField] = field(default_factory=list)
    readonly_reason: str = ""

    @property
    def parts(self) -> list[str]:
        return [part.strip() for part in self.annotation.split(",") if part.strip()]

    @property
    def interface(self) -> str:
        return self.parts[0] if self.parts else ""


class ProtocolDocument:
    def __init__(self, text: str, path: Path | None = None, *, bom: bool = False):
        self.text = text
        self.path = Path(path).resolve() if path else None
        self.bom = bom
        self.disk_bytes: bytes | None = None
        self.saved_text = text
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.messages: list[Message] = []
        self.enums: set[str] = set()
        self.related_errors: list[str] = []
        self._parse()

    @classmethod
    def load(cls, path: Path | str) -> ProtocolDocument:
        path = Path(path).resolve()
        data = path.read_bytes()
        try:
            document = cls(data.decode("utf-8-sig"), path, bom=data.startswith(b"\xef\xbb\xbf"))
        except UnicodeDecodeError as error:
            raise ProtocolError("文件必须使用 UTF-8 编码。请先在文本编辑器中转换编码。") from error
        document.disk_bytes = data
        return document

    @property
    def dirty(self) -> bool:
        return self.text != self.saved_text

    def _parse(self) -> None:
        masked = mask_source(self.text)
        depth = [0] * (len(masked) + 1)
        stack: list[int] = []
        braces: dict[int, int] = {}
        for index, char in enumerate(masked):
            depth[index] = len(stack)
            if char == "{":
                stack.append(index)
            elif char == "}":
                if not stack:
                    raise ProtocolError(f"第 {self.text.count(chr(10), 0, index) + 1} 行存在多余的右大括号。")
                braces[stack.pop()] = index
        if stack:
            raise ProtocolError("存在未闭合的大括号。")
        self.messages = []
        self.enums = set()
        for match in re.finditer(r"(?m)^[ \t]*(message|enum)[ \t]+([A-Za-z_]\w*)\b", masked):
            if depth[match.start()] != 0:
                continue
            if match[1] == "enum":
                self.enums.add(match[2])
                continue
            opening = masked.find("{", match.end())
            if opening < 0 or masked[match.end():opening].strip():
                raise ProtocolError(f"消息 {match[2]} 的声明缺少有效的大括号。")
            closing = braces[opening]
            header_end = self.text.find("\n", match.end())
            header_end = len(self.text) if header_end < 0 else header_end
            header_limit = min(header_end, opening) if opening < header_end else header_end
            annotation_match = re.search(r"//([^\r\n]*)", self.text[match.end():header_limit])
            annotation_span = None
            annotation = ""
            if annotation_match:
                annotation_span = (match.end() + annotation_match.start(), match.end() + annotation_match.end())
                annotation = annotation_match[1].strip()
            start = attached_start(self.text, match.start(), message=True)
            prefix = self.text[start:match.start()]
            serialization = re.search(r"(?m)^[ \t]*// Protocol[ \t]+(\w+)", prefix)
            closing_line_end = line_end(self.text, closing)
            message_end = closing + 1 if masked[closing + 1:closing_line_end].strip() else closing_line_end
            message = Message(match[2], annotation, description(prefix),
                              serialization[1] if serialization else "", start, match.start(),
                              message_end, opening, closing, match.span(2), annotation_span)
            body = masked[opening + 1:closing]
            covered: list[tuple[int, int]] = []
            for field_match in FIELD_PATTERN.finditer(body):
                absolute = opening + 1 + field_match.start()
                if depth[absolute] != 1:
                    continue
                code_start = self.text.rfind("\n", 0, absolute) + 1
                # Compact one-line declarations are preserved read-only.
                if code_start <= opening:
                    continue
                origin = opening + 1
                end = line_end(self.text, origin + field_match.end() - 1)
                field_start = attached_start(self.text, code_start)
                semicolon = masked.rfind(";", origin + field_match.start(), origin + field_match.end())
                inline = self.text[semicolon + 1:end].strip()
                prefix = self.text[field_start:code_start]
                type_start = field_match.start("label") if field_match["label"] else field_match.start("type")
                span = lambda key: (origin + field_match.start(key), origin + field_match.end(key))
                message.fields.append(ProtoField(
                    field_match["name"], re.sub(r"\s+", "", field_match["type"]) if field_match["type"].startswith("map") else field_match["type"],
                    int(field_match["number"]), field_match["label"] or "", description(prefix, inline),
                    field_start, end, code_start, span("name"),
                    (origin + type_start, origin + field_match.end("type")), span("number"),
                    self.text[slice(*span("options"))] if field_match["options"] else "", field_match["indent"],
                ))
                covered.append((field_match.start(), field_match.end()))
            remainder = body
            for begin, end in sorted(covered, reverse=True):
                remainder = remainder[:begin] + " " * (end - begin) + remainder[end:]
            if remainder.strip() or "\n" not in self.text[opening:closing]:
                message.readonly_reason = "包含嵌套声明、单行声明或暂不支持的语法；原文已保留，本消息只读。"
            self.messages.append(message)

    def restore(self, text: str) -> None:
        previous = self.text
        self.text = text
        try:
            self._parse()
        except Exception:
            self.text = previous
            self._parse()
            raise

    def get(self, name: str) -> Message:
        for message in self.messages:
            if message.name == name:
                return message
        raise ProtocolError(f"没有找到消息：{name}")

    def related(self) -> list[ProtocolDocument]:
        self.related_errors = []
        if not self.path:
            return []
        documents = []
        for path in sorted(self.path.parent.rglob("*.proto")):
            if path.resolve() != self.path:
                try:
                    documents.append(self.load(path))
                except (OSError, ProtocolError) as error:
                    self.related_errors.append(f"无法检查相关文件 {path.name}：{error}")
        return documents

    def _ensure_unique(self, name: str, excluding: str = "") -> None:
        if not IDENTIFIER.fullmatch(name):
            raise ProtocolError("名称只能由英文字母、数字和下划线组成，且不能以数字开头。")
        for document in [self, *self.related()]:
            if name in document.enums or any(m.name == name and not (document is self and m.name == excluding)
                                             for m in document.messages):
                raise ProtocolError(f"名称已经存在：{name}")

    @staticmethod
    def _references(message: Message, name: str) -> bool:
        return name in message.parts[1:] or any(
            any(token.rsplit(".", 1)[-1] == name for token in re.findall(TYPE_NAME, item.type_name))
            for item in message.fields)

    def references(self, name: str, *, external_only: bool = False) -> list[str]:
        documents = self.related() if external_only else [self, *self.related()]
        return [f"{document.path.name if document.path else '当前文件'} / {message.name}"
                for document in documents for message in document.messages
                if (message.name != name or document is not self) and self._references(message, name)]

    def add_message(self, name: str, annotation: str = "IMessage", doc: str = "", serialization: str = "") -> None:
        self._ensure_unique(name)
        nl = self.newline
        prefix = "".join(f"/// {line}{nl}" for line in doc.splitlines())
        if serialization:
            prefix += f"// Protocol {serialization}{nl}"
        declaration = f"message {name}" + (f" // {annotation.strip()}" if annotation.strip() else "")
        separator = "" if not self.text else (nl if self.text.endswith(("\n", "\r")) else nl * 2)
        self.restore(self.text + separator + prefix + declaration + nl + "{" + nl + "}" + nl)

    def add_rpc(self, request: str, response: str, doc: str = "") -> None:
        if request == response:
            raise ProtocolError("请求和回复必须使用不同的名称。")
        self._ensure_unique(request)
        self._ensure_unique(response)
        before = self.text
        try:
            self.add_message(request, f"IRequest,{response}", doc)
            self.add_message(response, "IResponse", "服务器回复")
        except Exception:
            self.restore(before)
            raise

    def edit_message(self, old: str, name: str, annotation: str, doc: str, serialization: str) -> None:
        message = self.get(old)
        if message.readonly_reason:
            raise ProtocolError(message.readonly_reason)
        self._ensure_unique(name, excluding=old)
        if old != name:
            external = self.references(old, external_only=True)
            if external:
                raise ProtocolError("其他文件正在引用这个名称，请先协调引用：\n" + "\n".join(external))
        edits: list[tuple[int, int, str]] = []
        if name != old:
            edits.append((*message.name_span, name))
            for other in self.messages:
                if other is not message and old in other.parts[1:] and other.annotation_span:
                    updated = ",".join(name if part == old else part for part in other.parts)
                    edits.append((*other.annotation_span, "// " + updated))
                for item in other.fields:
                    updated = re.sub(rf"\b{re.escape(old)}\b", name, item.type_name)
                    if updated != item.type_name:
                        edits.append((*item.type_span, (item.label + " " if item.label else "") + updated))
        if annotation != message.annotation:
            if message.annotation_span:
                edits.append((*message.annotation_span, "// " + annotation if annotation else ""))
            elif annotation:
                declaration_end = self.text.find("\n", message.name_span[1])
                if message.open_brace < declaration_end:
                    raise ProtocolError("请先将左大括号放到单独一行，再添加接口声明。")
                insertion = declaration_end - 1 if self.text[declaration_end - 1:declaration_end] == "\r" else declaration_end
                edits.append((insertion, insertion, " // " + annotation))
        if doc != message.description or serialization != message.serialization:
            prefix = self.text[message.start:message.declaration_start]
            lines = prefix.splitlines(keepends=True)
            kept = [line for line in lines if not (
                (line.strip().startswith("///") and not line.strip().startswith("////")) or
                line.strip().startswith("// Protocol "))]
            new_prefix = "".join(kept) + "".join(f"/// {line}{self.newline}" for line in doc.splitlines())
            if serialization:
                new_prefix += f"// Protocol {serialization}{self.newline}"
            edits.append((message.start, message.declaration_start, new_prefix))
        self.restore(replace_spans(self.text, edits))

    def delete_message(self, name: str) -> None:
        refs = self.references(name)
        if refs:
            raise ProtocolError("消息仍被引用，请先修改或删除引用方：\n" + "\n".join(refs))
        message = self.get(name)
        # Namespace imports affect the entire exported file, not only this message.
        prefix = self.text[message.start:message.declaration_start]
        imports = "".join(line for line in prefix.splitlines(keepends=True)
                          if line.strip().startswith("// using "))
        self.restore(self.text[:message.start] + imports + self.text[message.end:])

    @staticmethod
    def check_field(name: str, type_name: str, number: int, label: str) -> None:
        if not IDENTIFIER.fullmatch(name):
            raise ProtocolError("字段名称无效。请使用英文字母、数字和下划线，且不能以数字开头。")
        if not re.fullmatch(FIELD_TYPE, type_name):
            raise ProtocolError("字段类型无效，例如 string、int32、Position、map<int32,string>。")
        if not 1 <= number <= 536870911 or 19000 <= number <= 19999:
            raise ProtocolError("字段编号须为 1～536870911，且不能使用保留范围 19000～19999。")
        if label not in ("", "repeated", "repeatedList", "repeatedArray", "optional", "required"):
            raise ProtocolError("不支持的集合/修饰符。")
        if re.match(r"map\s*<", type_name):
            if label:
                raise ProtocolError("map 字段不能再添加集合/修饰符。")
            key = re.search(r"<\s*([^,]+)", type_name)[1].strip()
            if key in SCALARS and key in ("float", "double", "bytes", "byte"):
                raise ProtocolError("map 的键不能使用 float、double、bytes 或 byte。")

    def edit_field(self, message_name: str, index: int | None, *, name: str, type_name: str,
                   number: int, label: str = "", doc: str = "") -> None:
        message = self.get(message_name)
        if message.readonly_reason:
            raise ProtocolError(message.readonly_reason)
        self.check_field(name, type_name, number, label)
        for offset, other in enumerate(message.fields):
            if offset != index and (other.name == name or other.number == number):
                raise ProtocolError("同一消息中的字段名称和编号不能重复。")
        kind = (label + " " if label else "") + type_name
        if index is None:
            indent = message.fields[0].indent if message.fields else "    "
            new_field = "".join(f"{indent}/// {line}{self.newline}" for line in doc.splitlines())
            new_field += f"{indent}{kind} {name} = {number};{self.newline}"
            insertion = self.text.rfind("\n", 0, message.close_brace) + 1
            self.restore(self.text[:insertion] + new_field + self.text[insertion:])
            return
        item = message.fields[index]
        if doc == item.description:
            self.restore(replace_spans(self.text, [(*item.name_span, name), (*item.type_span, kind),
                                                   (*item.number_span, str(number))]))
        else:
            prefix = self.text[item.start:item.code_start]
            preserved = "".join(line for line in prefix.splitlines(keepends=True)
                                if not line.strip().startswith("///"))
            text = preserved + "".join(f"{item.indent}/// {line}{self.newline}" for line in doc.splitlines())
            text += f"{item.indent}{kind} {name} = {number}{item.options};{self.newline}"
            self.restore(self.text[:item.start] + text + self.text[item.end:])

    def delete_field(self, message_name: str, index: int) -> None:
        message = self.get(message_name)
        if message.readonly_reason:
            raise ProtocolError(message.readonly_reason)
        item = message.fields[index]
        self.restore(self.text[:item.start] + self.text[item.end:])

    def validate(self) -> list[str]:
        errors: list[str] = []
        documents = [self, *self.related()]
        errors.extend(self.related_errors)
        names: dict[str, Message] = {}
        declared: set[str] = set()
        for document in documents:
            for enum_name in document.enums:
                if enum_name in declared:
                    errors.append(f"类型名称重复：{enum_name}")
                declared.add(enum_name)
            for message in document.messages:
                if message.name in declared:
                    errors.append(f"类型名称重复：{message.name}")
                declared.add(message.name)
                names[message.name] = message
        for message in self.messages:
            seen_names: set[str] = set()
            seen_numbers: set[int] = set()
            for item in message.fields:
                try:
                    self.check_field(item.name, item.type_name, item.number, item.label)
                except ProtocolError as error:
                    errors.append(f"{message.name}.{item.name}：{error}")
                if item.name in seen_names or item.number in seen_numbers:
                    errors.append(f"{message.name}：字段名称或编号重复（{item.name} = {item.number}）。")
                seen_names.add(item.name)
                seen_numbers.add(item.number)
            kind = message.interface
            if self.path and self.path.parent.name.lower() == "outer" and kind in (
                    "IAddressMessage", "IAddressRequest", "IAddressResponse"):
                errors.append(f"{message.name}：IAddress 消息应定义在 Inner 目录中。")
            if message.serialization not in ("", "ProtoBuf", "MemoryPack"):
                errors.append(f"{message.name}：未知序列化类型 {message.serialization}。")
            if kind and kind not in INTERFACES:
                errors.append(f"{message.name}：未知消息接口 {kind}。")
            if kind in RESPONSE_TYPES:
                if len(message.parts) < 2:
                    errors.append(f"{message.name}：请求缺少回复消息名称。")
                elif message.parts[1] not in names:
                    errors.append(f"{message.name}：回复消息不存在：{message.parts[1]}。")
                elif names[message.parts[1]].interface != RESPONSE_TYPES[kind]:
                    errors.append(f"{message.name}：回复须使用 {RESPONSE_TYPES[kind]} 接口。")
            if kind in ROUTED_TYPES:
                route_index = 2 if kind in RESPONSE_TYPES else 1
                if len(message.parts) <= route_index:
                    errors.append(f"{message.name}：缺少路由类型，例如 ChatRoute 或 MapRoamingType。")
                elif not IDENTIFIER.fullmatch(message.parts[route_index]):
                    errors.append(f"{message.name}：路由类型名称无效。")
        return list(dict.fromkeys(errors))

    def save(self, path: Path | str | None = None, *, backup_dir: Path | None = None) -> Path | None:
        target = Path(path).resolve() if path else self.path
        if target is None:
            raise ProtocolError("请先选择保存路径。")
        errors = self.validate()
        if errors:
            raise ProtocolError("保存前检查未通过：\n" + "\n".join(errors))
        existing = target.read_bytes() if target.exists() else None
        if target == self.path and self.disk_bytes is not None and existing != self.disk_bytes:
            raise ProtocolError("文件已被 Visual Studio 或其他程序修改。请先另存为，或重新打开文件后再编辑。")
        data = (b"\xef\xbb\xbf" if self.bom else b"") + self.text.encode("utf-8")
        backup = None
        if existing != data:
            if existing is not None:
                directory = backup_dir or Path(__file__).resolve().parent / ".backups"
                directory.mkdir(parents=True, exist_ok=True)
                key = hashlib.sha256(str(target).encode()).hexdigest()[:10]
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                backup = directory / f"{target.stem}-{key}-{stamp}.proto.bak"
                backup.write_bytes(existing)
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix=".protocol-", suffix=".tmp", dir=target.parent)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Recheck after preparing the replacement to catch concurrent edits.
                latest = target.read_bytes() if target.exists() else None
                if latest != existing:
                    raise ProtocolError("保存期间文件被其他程序修改，请重新打开或另存为。")
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        self.path = target
        self.disk_bytes = data
        self.saved_text = self.text
        return backup
