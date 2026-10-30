"""Prefix chooser with per-row hover help and a live complete-name preview."""
import tkinter as tk
from tkinter import ttk

from naming import NO_PREFIX, compose_name, split_name
from workbench_ui import BORDER, Tooltip


class PrefixedName(ttk.Frame):
    def __init__(self, parent, variable, prefixes):
        super().__init__(parent, style="Dialog.TFrame")
        self.variable = variable
        self.entries = [NO_PREFIX, *prefixes]
        prefix, suffix = split_name(variable.get(), prefixes)
        self.prefix = tk.StringVar(self, value=prefix)
        self.suffix = tk.StringVar(self, value=suffix)
        self.meaning = tk.StringVar(self)
        self.preview = tk.StringVar(self)
        self.popup = None
        self.previous_grab = None
        row = ttk.Frame(self, style="Dialog.TFrame")
        row.pack(fill="x")
        self.button = ttk.Button(row, text="", command=self.open_popup, width=14)
        self.button.pack(side="left", padx=(0, 8))
        self.entry = ttk.Entry(row, textvariable=self.suffix, width=30)
        self.entry.pack(side="left", fill="x", expand=True)
        self.button.bind("<Down>", lambda _: self.open_popup())
        ttk.Label(self, textvariable=self.meaning, style="Dialog.TLabel", foreground="#9d9d9d",
                  wraplength=430).pack(anchor="w", pady=(6, 0))
        ttk.Label(self, textvariable=self.preview, style="Dialog.TLabel", foreground="#4ec9b0",
                  wraplength=430).pack(anchor="w", pady=(5, 0))
        ttk.Label(self, text="名称建议每个英文单词首字母大写，例如 LoginMessage。\n前缀只用于命名；实际发送方向由接口和业务代码决定。",
                  style="Dialog.TLabel", foreground="#9d9d9d", wraplength=430).pack(anchor="w", pady=(5, 0))
        self.prefix.trace_add("write", self.update_name)
        self.suffix.trace_add("write", self.update_name)
        self.bind("<Destroy>", self.on_destroy, add="+")
        self.update_name()

    def update_name(self, *_):
        selected = next(item for item in self.entries if item.prefix == self.prefix.get())
        self.button.configure(text=(selected.prefix or "无前缀") + "  ▾")
        self.meaning.set(selected.meaning)
        self.variable.set(compose_name(selected.prefix, self.suffix.get()))
        self.preview.set("完整名称：" + (self.variable.get() or "（请输入名称）"))

    def open_popup(self):
        if self.popup is not None:
            self.close_popup()
            return
        self.previous_grab = self.grab_current()
        self.popup = popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(bg=BORDER, padx=1, pady=1)
        self.tree = ttk.Treeview(popup, columns=("meaning",), show="tree headings", selectmode="browse",
                                 height=min(7, len(self.entries)))
        self.tree.heading("#0", text="前缀", anchor="w")
        self.tree.column("#0", width=115, minwidth=90, stretch=False)
        self.tree.heading("meaning", text="含义（悬停查看详细说明）", anchor="w")
        self.tree.column("meaning", width=310, minwidth=200)
        scroll = ttk.Scrollbar(popup, command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(fill="both", expand=True)
        for index, item in enumerate(self.entries):
            self.tree.insert("", "end", iid=str(index), text=item.prefix or "无前缀", values=(item.meaning,))
            if item.prefix == self.prefix.get():
                self.tree.selection_set(str(index))
                self.tree.focus(str(index))
                self.tree.see(str(index))
        self.hint = Tooltip(self.tree, "")
        self.hovered_row = None
        self.tree.bind("<Motion>", self.hover_row, add="+")
        self.tree.bind("<Leave>", lambda _: setattr(self, "hovered_row", None), add="+")
        self.tree.bind("<ButtonRelease-1>", self.choose_clicked)
        self.tree.bind("<Return>", self.choose_keyboard)
        popup.bind("<Escape>", self.close_popup)
        popup.bind("<ButtonPress-1>", self.outside_click, add="+")
        popup.update_idletasks()
        x = min(self.winfo_rootx(), self.winfo_screenwidth() - popup.winfo_reqwidth() - 8)
        y = self.button.winfo_rooty() + self.button.winfo_height() + 3
        if y + popup.winfo_reqheight() > self.winfo_screenheight():
            y = self.button.winfo_rooty() - popup.winfo_reqheight() - 3
        popup.geometry(f"+{max(0, x)}+{max(0, y)}")
        popup.deiconify()
        popup.grab_set()
        self.tree.focus_set()

    def hover_row(self, event):
        row = self.tree.identify_row(event.y)
        if row == self.hovered_row:
            return
        self.hovered_row = row
        self.hint.hide()
        if row:
            item = self.entries[int(row)]
            self.hint.text = f"{item.prefix or '无前缀'}：{item.meaning}\n{item.description or '暂无详细说明。'}"
            self.hint.schedule()

    def choose_clicked(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.choose(row)

    def choose_keyboard(self, _event):
        selection = self.tree.selection()
        if selection:
            self.choose(selection[0])
        return "break"

    def choose(self, row):
        self.prefix.set(self.entries[int(row)].prefix)
        self.close_popup()
        self.entry.focus_set()

    def outside_click(self, event):
        popup = self.popup
        if popup and not (popup.winfo_rootx() <= event.x_root < popup.winfo_rootx() + popup.winfo_width()
                          and popup.winfo_rooty() <= event.y_root < popup.winfo_rooty() + popup.winfo_height()):
            self.close_popup()
            return "break"

    def close_popup(self, _event=None):
        if self.popup is not None:
            self.hint.hide()
            popup, self.popup = self.popup, None
            if popup.winfo_exists():
                popup.grab_release()
                popup.destroy()
            if self.previous_grab is not None and self.previous_grab.winfo_exists():
                self.previous_grab.grab_set()
            self.previous_grab = None
        return "break"

    def on_destroy(self, event):
        if event.widget is self:
            self.close_popup()
