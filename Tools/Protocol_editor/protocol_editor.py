"""Fantasy protocol desktop editor. Python standard library only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import re
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from workbench_ui import WorkbenchMixin, dark_caption, BG, INPUT, TEXT
from export_runner import ExportError, ExportResult, prepare_export, run_export
from naming import DEFAULT_PREFIXES, NamingPrefix, load_prefixes, save_prefixes, validate_prefixes
from naming_ui import PrefixedName

from proto_model import INTERFACES, RESPONSE_TYPES, ROUTED_TYPES, SCALARS, ProtocolDocument, ProtocolError

TOOL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_DIR.parents[1]
DEFAULT_FILE = PROJECT_ROOT / "examples/Config/NetworkProtocol/Outer/OuterMessage.proto"
STATE_FILE = TOOL_DIR / ".editor-state.json"
PREFIX_FILE = TOOL_DIR / ".naming-prefixes.json"


class FormDialog(tk.Toplevel):
    """A small modal form; data is committed only after pressing OK."""
    def __init__(self, parent, title, fields, description="", validate=None, *, name_prefixes=None,
                 name_fields=("name",), description_label="说明（可选）"):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.configure(background="#252526")
        self.result = None
        self.values = {}
        self.validate_values = validate
        self.error = tk.StringVar()
        body = ttk.Frame(self, padding=24, style="Dialog.TFrame")
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=title, style="Dialog.TLabel", font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 18))
        self.widgets = {}
        self.name_inputs = {}
        for row, (key, label, value, choices) in enumerate(fields, start=1):
            ttk.Label(body, text=label, style="Dialog.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 16), pady=6)
            variable = tk.StringVar(value=value)
            self.values[key] = variable
            if name_prefixes is not None and key in name_fields:
                control = PrefixedName(body, variable, name_prefixes)
                control.grid(row=row, column=1, sticky="ew", pady=6)
                self.name_inputs[key] = control
                self.widgets[key] = control.entry
                continue
            elif choices is None:
                widget = ttk.Entry(body, textvariable=variable, width=46)
            else:
                widget = ttk.Combobox(body, textvariable=variable, values=choices, width=44)
            widget.grid(row=row, column=1, sticky="ew", pady=6)
            self.widgets[key] = widget
        row = len(fields) + 1
        ttk.Label(body, text=description_label, style="Dialog.TLabel").grid(row=row, column=0, sticky="nw", pady=(10, 0))
        self.description = tk.Text(body, width=46, height=4, font=("Microsoft YaHei UI", 10),
                                   relief="flat", borderwidth=0, wrap="word", bg=INPUT, fg=TEXT, insertbackground="white",
                                   padx=9, pady=7, highlightthickness=1, highlightbackground="#454545", highlightcolor="#0078d4", selectbackground="#264f78")
        self.description.grid(row=row, column=1, pady=(10, 0), sticky="ew")
        self.description.insert("1.0", description)
        ttk.Label(body, textvariable=self.error, foreground="#f48771", style="Dialog.TLabel", wraplength=440).grid(
            row=row + 1, column=0, columnspan=2, sticky="w", pady=(12, 4))
        actions = ttk.Frame(body, style="Dialog.TFrame")
        actions.grid(row=row + 2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="left", padx=5)
        ttk.Button(actions, text="确定", style="Primary.TButton", command=self.accept).pack(side="left")
        self.bind("<Escape>", lambda _: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        owner = parent.winfo_toplevel()
        x = max(0, owner.winfo_rootx() + (owner.winfo_width() - self.winfo_reqwidth()) // 2)
        y = max(0, owner.winfo_rooty() + (owner.winfo_height() - self.winfo_reqheight()) // 2)
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.after_idle(lambda: dark_caption(self))
        if self.widgets:
            first = next(iter(self.widgets.values()))
            first.focus_set()
            first.selection_range(0, "end")

    def accept(self):
        for control in self.name_inputs.values():
            if not control.suffix.get().strip():
                self.error.set("请填写消息名称，例如 LoginMessage；不能只填写前缀。")
                return
        values = {key: value.get().strip() for key, value in self.values.items()}
        values["doc"] = self.description.get("1.0", "end-1c").strip()
        try:
            if self.validate_values:
                self.validate_values(values)
        except (ProtocolError, ValueError) as error:
            self.error.set(str(error))
            return
        self.result = values
        self.destroy()

    def show(self):
        self.grab_set()
        self.wait_window()
        return self.result


class PrefixSettings(tk.Toplevel):
    def __init__(self, parent, entries, path, on_saved):
        super().__init__(parent)
        self.withdraw()
        self.title("命名前缀设置")
        self.transient(parent.winfo_toplevel())
        self.configure(bg="#252526")
        self.entries = list(entries)
        self.path, self.on_saved = path, on_saved
        self.error = tk.StringVar(self)
        body = ttk.Frame(self, padding=20, style="Dialog.TFrame")
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="命名前缀设置", style="Dialog.TLabel", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        ttk.Label(body, text="设置前缀、简短含义和详细说明。只影响新建消息的命名选项，不会重命名已有协议。",
                  style="Dialog.TLabel", wraplength=700).pack(anchor="w", pady=(8, 14))
        table = ttk.Frame(body, style="Dialog.TFrame")
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("prefix", "meaning", "description"), show="headings", height=8)
        for key, title, width in (("prefix", "前缀", 110), ("meaning", "简短含义", 200), ("description", "详细说明", 350)):
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, width=width, minwidth=80)
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _: self.edit_entry())
        tools = ttk.Frame(body, style="Dialog.TFrame")
        tools.pack(fill="x", pady=10)
        for label, command in (("新增前缀", lambda: self.edit_entry(new=True)), ("编辑", self.edit_entry), ("删除", self.delete_entry)):
            ttk.Button(tools, text=label, command=command).pack(side="left", padx=(0, 8))
        ttk.Label(body, text="“无前缀”始终可选。前缀需以下划线结尾，例如 C2Chat_。",
                  style="Dialog.TLabel", foreground="#9d9d9d").pack(anchor="w")
        ttk.Label(body, textvariable=self.error, style="Dialog.TLabel", foreground="#f48771", wraplength=700).pack(anchor="w", pady=8)
        actions = ttk.Frame(body, style="Dialog.TFrame")
        actions.pack(fill="x")
        ttk.Button(actions, text="保存设置", command=self.accept, style="Primary.TButton").pack(side="right")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right", padx=8)
        self.bind("<Escape>", lambda _: self.destroy())
        self.refresh()
        self.update_idletasks()
        owner = parent.winfo_toplevel()
        self.geometry(f"+{max(0, owner.winfo_rootx() + (owner.winfo_width() - self.winfo_reqwidth()) // 2)}"
                      f"+{max(0, owner.winfo_rooty() + (owner.winfo_height() - self.winfo_reqheight()) // 2)}")
        self.deiconify()
        self.bind("<Map>", lambda event: dark_caption(self) if event.widget is self else None, add="+")
        dark_caption(self)

    def refresh(self, selected=None):
        self.tree.delete(*self.tree.get_children())
        for index, entry in enumerate(self.entries):
            self.tree.insert("", "end", iid=str(index), values=(entry.prefix, entry.meaning, " ".join(entry.description.split())))
        if selected is not None and selected < len(self.entries):
            self.tree.selection_set(str(selected))
            self.tree.see(str(selected))

    def edit_entry(self, new=False):
        selection = self.tree.selection()
        if not new and not selection:
            self.error.set("请先选择要编辑的前缀。")
            return
        index = len(self.entries) if new else int(selection[0])
        entry = NamingPrefix("", "") if new else self.entries[index]
        def validate(values):
            candidate = NamingPrefix(values["prefix"], values["meaning"], values["doc"])
            validate_prefixes([item for offset, item in enumerate(self.entries) if offset != index] + [candidate])
        result = FormDialog(self, "新增前缀" if new else "编辑前缀", [
            ("prefix", "前缀", entry.prefix, None), ("meaning", "简短含义", entry.meaning, None),
        ], entry.description, validate, description_label="详细说明（可选）").show()
        self.grab_set()
        if result:
            item = NamingPrefix(result["prefix"], result["meaning"], result["doc"])
            if new:
                self.entries.append(item)
            else:
                self.entries[index] = item
            self.error.set("")
            self.refresh(index)

    def delete_entry(self):
        selection = self.tree.selection()
        if selection:
            index = int(selection[0])
            del self.entries[index]
            self.error.set("")
            self.refresh(min(index, len(self.entries) - 1) if self.entries else None)
        else:
            self.error.set("请先选择要删除的前缀。")

    def accept(self):
        try:
            save_prefixes(self.path, self.entries)
        except (OSError, ValueError) as error:
            self.error.set(f"未能保存设置：{error}")
            return
        self.on_saved(list(self.entries))
        self.destroy()

    def show(self):
        self.grab_set()
        self.wait_window()


class ProtocolEditor(WorkbenchMixin, ttk.Frame):
    def __init__(self, root: tk.Tk, path: Path | None = None):
        super().__init__(root)
        self.root = root
        self.document: ProtocolDocument | None = None
        self.selected_name = ""
        self.undo_stack: list[tuple[str, str]] = []
        self.redo_stack: list[tuple[str, str]] = []
        self.search = tk.StringVar()
        self.status = tk.StringVar(value="打开一个 .proto 文件开始编辑")
        self.file_label = tk.StringVar(value="尚未打开文件")
        self.selection_title = tk.StringVar(value="选择一条消息")
        self.selection_meta = tk.StringVar(value="左侧选择消息，右侧查看字段和原文")
        self.auto_export = tk.BooleanVar(value=True)
        self.export_running = False
        self.export_results = queue.Queue()
        self._close_after_export = False
        self.prefix_error = ""
        try:
            self.prefixes = load_prefixes(PREFIX_FILE)
        except (OSError, ValueError) as error:
            self.prefixes = list(DEFAULT_PREFIXES)
            self.prefix_error = str(error)
        self._configure_style()
        self._build()
        self.pack(fill="both", expand=True)
        self.root.title("Fantasy 协议编辑器")
        self.root.geometry("1280x820")
        self.root.minsize(1040, 680)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Control-o>", lambda _: self.open_file())
        self.root.bind("<Control-s>", lambda _: self.save())
        self.root.bind("<Control-Shift-S>", lambda _: self.save(as_new=True))
        self.root.bind("<Control-z>", lambda _: self.undo())
        self.root.bind("<Control-y>", lambda _: self.redo())
        self.search.trace_add("write", lambda *_: self.refresh_list())
        if path:
            self.load_path(path)
        if self.prefix_error:
            self.status.set("命名前缀配置读取失败，暂用默认选项。请在 编辑 → 命名前缀设置 中检查。")

    def edit_prefixes(self):
        def saved(entries):
            self.prefixes = entries
            self.prefix_error = ""
            self.status.set("命名前缀设置已保存，新建消息时即可使用。")
        dialog = PrefixSettings(self.root, self.prefixes, PREFIX_FILE, saved)
        if self.prefix_error:
            dialog.error.set(self.prefix_error + "；原配置文件尚未被覆盖。")
        dialog.show()

    def load_path(self, path):
        try:
            document = ProtocolDocument.load(path)
        except (OSError, ProtocolError) as error:
            messagebox.showerror("无法打开", str(error), parent=self.root)
            return False
        self.document = document
        self.selected_name = document.messages[0].name if document.messages else ""
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.search.set("")
        self.refresh()
        self.status.set(f"已打开 {len(document.messages)} 条消息；枚举及其他原文保留。")
        return True

    def confirm_discard(self):
        if self.document and self.document.dirty:
            answer = messagebox.askyesnocancel("存在未保存的修改", "是否先保存当前修改？", parent=self.root)
            if answer is None:
                return False
            if answer:
                return self.save()
        return True

    def open_file(self):
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(parent=self.root, title="打开 Fantasy 协议文件",
                                         filetypes=[("Protocol 文件", "*.proto"), ("所有文件", "*.*")],
                                         initialdir=str(self.document.path.parent if self.document and self.document.path else DEFAULT_FILE.parent))
        if path:
            self.load_path(path)

    def open_default(self):
        if self.confirm_discard():
            self.load_path(DEFAULT_FILE)

    def refresh_list(self):
        self.message_tree.delete(*self.message_tree.get_children())
        if not self.document:
            return
        query = self.search.get().strip().lower()
        for index, message in enumerate(self.document.messages):
            if query and query not in (message.name + " " + message.annotation + " " + message.description).lower():
                continue
            item = str(index)
            summary = " ".join(message.description.split())
            if len(summary) > 80:
                summary = summary[:79] + "…"
            self.message_tree.insert("", "end", iid=item, text=message.name, values=(summary or "—",))
            if message.name == self.selected_name:
                self.message_tree.selection_set(item)
                self.message_tree.see(item)

        self.message_count.set(f"{len(self.message_tree.get_children())} / {len(self.document.messages)} 条消息")

    def select_message(self, _event=None):
        selection = self.message_tree.selection()
        if not self.document or not selection:
            return
        self.selected_name = self.document.messages[int(selection[0])].name
        self.show_selection()

    def current(self):
        if not self.document or not self.selected_name:
            raise ProtocolError("请先打开文件并选择一条消息。")
        return self.document.get(self.selected_name)

    def show_selection(self):
        self.field_tree.delete(*self.field_tree.get_children())
        text = ""
        if self.document and self.selected_name:
            message = self.document.get(self.selected_name)
            self.selection_title.set(message.name)
            meta = (message.annotation or "数据结构（无消息接口）") + "  ·  " + (message.serialization or "ProtoBuf（默认）")
            if message.readonly_reason:
                meta += "\n" + message.readonly_reason
            elif message.description:
                meta += "\n" + message.description.replace("\n", " ")
            self.selection_meta.set(meta)
            for index, item in enumerate(message.fields):
                self.field_tree.insert("", "end", iid=str(index), values=(
                    item.number, item.label or "—", item.type_name, item.name, item.description.replace("\n", " ")),
                    tags=("odd" if index % 2 else "even",))
            text = self.document.text[message.start:message.end]
        else:
            self.selection_title.set("选择一条消息")
            self.selection_meta.set("左侧选择消息，或点击「新增消息 / 新增 RPC」")
            if self.document:
                text = self.document.text
        self.line_numbers.configure(state="normal")
        self.line_numbers.delete("1.0", "end")
        self.line_numbers.insert("1.0", "\n".join(f"{index:>3}" for index in range(1, len(text.splitlines()) + 1)))
        self.line_numbers.configure(state="disabled")
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        for number, line in enumerate(text.splitlines(), 1):
            comment = line.find("//")
            if comment >= 0:
                self.preview.tag_add("comment", f"{number}.{comment}", f"{number}.end")
            for match in re.finditer(r"\b(message|enum|string|int32|int64|float|bool|bytes|repeatedList|repeatedArray|repeated|map)\b", line[:comment] if comment >= 0 else line):
                self.preview.tag_add("keyword", f"{number}.{match.start()}", f"{number}.{match.end()}")
        self.preview.configure(state="disabled")
        self.update_workbench()

    def refresh(self):
        if self.document:
            names = {message.name for message in self.document.messages}
            if self.selected_name not in names:
                self.selected_name = self.document.messages[0].name if self.document.messages else ""
            path = str(self.document.path) if self.document.path else "未命名"
            self.file_label.set(path)
            self.root.title(("* " if self.document.dirty else "") + "Fantasy 协议编辑器 — " + Path(path).name)
        self.refresh_list()
        self.show_selection()

    def perform(self, operation, *, select=None):
        if not self.document:
            messagebox.showinfo("打开文件", "请先打开一个 .proto 文件。", parent=self.root)
            return False
        before = self.document.text
        previous_selection = self.selected_name
        try:
            operation()
        except (ProtocolError, ValueError, OSError) as error:
            messagebox.showerror("无法完成操作", str(error), parent=self.root)
            return False
        if self.document.text != before:
            self.undo_stack.append((before, previous_selection))
            self.undo_stack = self.undo_stack[-100:]
            self.redo_stack.clear()
        if select is not None:
            self.selected_name = select
        self.refresh()
        self.status.set("修改已应用，尚未保存。点击「保存」或按 Ctrl+S。" if self.document.dirty else "内容未改变。")
        return True

    def undo(self):
        if self.document and self.undo_stack:
            self.redo_stack.append((self.document.text, self.selected_name))
            text, self.selected_name = self.undo_stack.pop()
            self.document.restore(text)
            self.refresh()
            self.status.set("已撤销。")

    def redo(self):
        if self.document and self.redo_stack:
            self.undo_stack.append((self.document.text, self.selected_name))
            text, self.selected_name = self.redo_stack.pop()
            self.document.restore(text)
            self.refresh()
            self.status.set("已重做。")

    def message_form(self, message=None):
        if not self.document:
            messagebox.showinfo("打开文件", "请先打开一个 .proto 文件。", parent=self.root)
            return None
        if message and message.readonly_reason:
            messagebox.showinfo("只读消息", message.readonly_reason, parent=self.root)
            return None
        parts = message.parts if message else []
        kind = message.interface if message else "IMessage"
        response = parts[1] if kind in RESPONSE_TYPES and len(parts) > 1 else ""
        route_index = 2 if kind in RESPONSE_TYPES else 1
        route = parts[route_index] if kind in ROUTED_TYPES and len(parts) > route_index else ""
        fields = [
            ("name", "消息名称", message.name if message else "C2G_NewMessage", None),
            ("kind", "消息接口", kind, INTERFACES),
            ("response", "回复消息（请求用）", response, [m.name for m in self.document.messages]),
            ("route", "路由类型（路由消息用）", route, None),
            ("serialization", "序列化", message.serialization if message and message.serialization else "默认", ("默认", "ProtoBuf", "MemoryPack")),
        ]
        def validate(values):
            self.document._ensure_unique(values["name"], excluding=message.name if message else "")
            if values["kind"] not in INTERFACES:
                raise ProtocolError("请选择受支持的消息接口；数据结构可将接口留空。")
            if values["kind"] in RESPONSE_TYPES and not values["response"]:
                raise ProtocolError("请求需要填写回复消息名称。也可以使用「新增 RPC」成对创建。")
            if values["kind"] in ROUTED_TYPES and not values["route"]:
                raise ProtocolError("请填写路由类型，例如 ChatRoute 或 MapRoamingType。")
            if values["serialization"] not in ("默认", "ProtoBuf", "MemoryPack"):
                raise ProtocolError("序列化请选择 默认、ProtoBuf 或 MemoryPack。")
        return FormDialog(self.root, "编辑消息" if message else "新增消息", fields,
                          message.description if message else "", validate,
                          name_prefixes=self.prefixes if message is None else None).show()

    @staticmethod
    def annotation(values):
        parts = [values["kind"]] if values["kind"] else []
        if values["kind"] in RESPONSE_TYPES:
            parts.append(values["response"])
        if values["kind"] in ROUTED_TYPES:
            parts.append(values["route"])
        return ",".join(parts)

    def add_message(self):
        values = self.message_form()
        if values:
            self.perform(lambda: self.document.add_message(values["name"], self.annotation(values), values["doc"],
                         "" if values["serialization"] == "默认" else values["serialization"]), select=values["name"])

    def edit_message(self):
        try:
            message = self.current()
        except ProtocolError:
            return
        values = self.message_form(message)
        if values:
            self.perform(lambda: self.document.edit_message(message.name, values["name"], self.annotation(values),
                         values["doc"], "" if values["serialization"] == "默认" else values["serialization"]), select=values["name"])

    def add_rpc(self):
        if not self.document:
            return
        def validate(values):
            self.document._ensure_unique(values["request"])
            self.document._ensure_unique(values["response"])
            if values["request"] == values["response"]:
                raise ProtocolError("请求和回复名称不能相同。")
        values = FormDialog(self.root, "成对新增 RPC", [
            ("request", "请求消息名称", "C2G_NewRequest", None),
            ("response", "回复消息名称", "G2C_NewResponse", None),
        ], "客户端请求", validate, name_prefixes=self.prefixes, name_fields=("request", "response")).show()
        if values:
            self.perform(lambda: self.document.add_rpc(values["request"], values["response"], values["doc"]), select=values["request"])

    def delete_message(self):
        try:
            message = self.current()
        except ProtocolError:
            return
        if messagebox.askyesno("删除消息", f"删除 {message.name} 及其所有字段？\n此修改可以撤销。", parent=self.root):
            self.perform(lambda: self.document.delete_message(message.name))

    def field_form(self, index=None):
        try:
            message = self.current()
        except ProtocolError:
            return
        if message.readonly_reason:
            messagebox.showinfo("只读消息", message.readonly_reason, parent=self.root)
            return
        item = message.fields[index] if index is not None else None
        used_numbers = {f.number for f in message.fields}
        number = max(used_numbers, default=0) + 1
        if 19000 <= number <= 19999:
            number = 20000
        types = sorted(set(SCALARS) | {m.name for m in self.document.messages} | self.document.enums)
        def validate(values):
            try:
                value = int(values["number"])
            except ValueError:
                raise ProtocolError("字段编号必须是整数。") from None
            self.document.check_field(values["name"], values["type"], value, values["label"])
            for offset, other in enumerate(message.fields):
                if offset != index and (other.name == values["name"] or other.number == value):
                    raise ProtocolError("字段名称或编号已存在。")
        values = FormDialog(self.root, "编辑字段" if item else "添加字段", [
            ("name", "字段名称", item.name if item else "NewField", None),
            ("type", "字段类型（可直接输入）", item.type_name if item else "string", types),
            ("label", "集合 / 修饰符", item.label if item else "", ("", "repeated", "repeatedList", "repeatedArray")),
            ("number", "字段编号", str(item.number if item else number), None),
        ], item.description if item else "", validate).show()
        if values:
            self.perform(lambda: self.document.edit_field(message.name, index, name=values["name"],
                         type_name=values["type"], number=int(values["number"]), label=values["label"], doc=values["doc"]))

    def add_field(self):
        self.field_form()

    def edit_field(self):
        selection = self.field_tree.selection()
        if selection:
            self.field_form(int(selection[0]))

    def delete_field(self):
        selection = self.field_tree.selection()
        if not selection:
            return
        message = self.current()
        index = int(selection[0])
        if messagebox.askyesno("删除字段", f"删除字段 {message.fields[index].name}？\n原有字段编号不会自动重排。", parent=self.root):
            self.perform(lambda: self.document.delete_field(message.name, index))

    def check(self):
        if not self.document:
            return
        errors = self.document.validate()
        if errors:
            messagebox.showerror("检查未通过", "\n".join(errors), parent=self.root)
        else:
            readonly = sum(bool(m.readonly_reason) for m in self.document.messages)
            messagebox.showinfo("检查通过", f"已检查 {len(self.document.messages)} 条消息。\n名称、字段编号及请求回复关联检查通过。"
                                + (f"\n其中 {readonly} 条复杂消息原样保留，只读。" if readonly else ""), parent=self.root)

    def save(self, as_new=False, *, export=None):
        if self.export_running:
            self.status.set("正在导出，请等待完成后再保存。")
            return False
        if not self.document:
            return False
        path = self.document.path
        if as_new or not path:
            selected = filedialog.asksaveasfilename(parent=self.root, title="保存协议文件", defaultextension=".proto",
                filetypes=[("Protocol 文件", "*.proto")], initialdir=str(path.parent if path else DEFAULT_FILE.parent),
                initialfile=path.name if path else "OuterMessage.proto")
            if not selected:
                return False
            path = Path(selected)
        try:
            backup = self.document.save(path, backup_dir=TOOL_DIR / ".backups")
        except (OSError, ProtocolError) as error:
            messagebox.showerror("未能保存", str(error), parent=self.root)
            return False
        self.refresh()
        self.status.set("已保存。旧文件备份：" + backup.name if backup else "已保存。")
        self.persist_state()
        should_export = self.auto_export.get() if export is None else export
        if should_export:
            return self.start_export()
        return True

    def append_export_log(self, text, tag=""):
        self.export_output.configure(state="normal")
        if self.export_output.index("end-1c") != "1.0":
            self.export_output.insert("end", "\n")
        self.export_output.insert("end", text, tag)
        self.export_output.see("end")
        self.export_output.configure(state="disabled")

    def clear_export_log(self):
        self.export_output.configure(state="normal")
        self.export_output.delete("1.0", "end")
        self.export_output.configure(state="disabled")

    def start_export(self):
        if self.export_running or not self.document or not self.document.path:
            return False
        self.tabs.select(2)
        try:
            command = prepare_export(self.document.path, PROJECT_ROOT)
        except ExportError as error:
            self.append_export_log(str(error), "error")
            self.status.set("协议已保存，导出未启动。请查看导出日志。")
            return False
        self.export_running = True
        self.export_button.configure(state="disabled")
        self.append_export_log(f"开始导出：{self.document.path.name}", "info")
        self.status.set("协议已保存，正在导出 C#……")
        def worker():
            try:
                result = run_export(command)
            except Exception as error:
                result = ExportResult(-1, f"导出失败：{error}")
            self.export_results.put(result)
        threading.Thread(target=worker, name="Fantasy protocol exporter", daemon=True).start()
        self.root.after(75, self.poll_export)
        return True

    def poll_export(self):
        try:
            result = self.export_results.get_nowait()
        except queue.Empty:
            self.root.after(75, self.poll_export)
            return
        self.export_running = False
        self.export_button.configure(state="normal")
        if result.output:
            self.append_export_log(result.output)
        if result.success:
            self.append_export_log("导出成功。可以回到 Visual Studio 重新生成两端项目。", "success")
            self.status.set("协议已保存，C# 导出成功。")
        else:
            self.append_export_log(f"导出失败（退出码 {result.returncode}）。协议文件已保存，请修正日志中的问题后重试。", "error")
            self.status.set("协议已保存，但导出失败。请查看导出日志。")
        if self._close_after_export:
            self._close_after_export = False
            if result.success and not self.document.dirty:
                self.close()

    def persist_state(self):
        if self.document and self.document.path:
            try:
                STATE_FILE.write_text(json.dumps({"last_file": str(self.document.path), "auto_export": self.auto_export.get()}, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass

    def close(self):
        if self.export_running:
            self.status.set("正在导出，请等待完成后再关闭窗口。")
            return
        if self.confirm_discard():
            if self.export_running:
                self._close_after_export = True
                return
            self.persist_state()
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Fantasy .proto 协议桌面编辑器")
    parser.add_argument("file", nargs="?", help="打开指定协议文件")
    parser.add_argument("--check", action="store_true", help="仅检查文件，不打开界面")
    options = parser.parse_args()
    path = Path(options.file).resolve() if options.file else DEFAULT_FILE
    if options.check:
        try:
            document = ProtocolDocument.load(path)
            errors = document.validate()
        except (OSError, ProtocolError) as error:
            print(error, file=sys.stderr)
            return 1
        print(f"{path}: {len(document.messages)} messages, {sum(len(m.fields) for m in document.messages)} fields")
        for error in errors:
            print(error)
        return 1 if errors else 0
    if not options.file and STATE_FILE.exists():
        try:
            previous = Path(json.loads(STATE_FILE.read_text(encoding="utf-8"))["last_file"])
            if previous.is_file():
                path = previous
        except (OSError, ValueError, KeyError):
            pass
    root = tk.Tk()
    def report_error(error_type, error, trace):
        log = TOOL_DIR / "editor-error.log"
        try:
            log.write_text("".join(traceback.format_exception(error_type, error, trace)), encoding="utf-8")
        except OSError:
            pass
        messagebox.showerror("操作未完成", f"{error}\n\n错误详情：{log}", parent=root)
    root.report_callback_exception = report_error
    editor = ProtocolEditor(root, path)
    if STATE_FILE.exists():
        try:
            preferences = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            editor.auto_export.set(bool(preferences.get("auto_export", True)))
        except (OSError, ValueError):
            pass
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
