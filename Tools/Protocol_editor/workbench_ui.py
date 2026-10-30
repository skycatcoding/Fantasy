"""VS Code inspired presentation layer; protocol editing remains in the model."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox, ttk

BG = "#1f1f1f"
SIDE = "#181818"
BAR = "#181818"
BORDER = "#2b2b2b"
TEXT = "#cccccc"
MUTED = "#858585"
BLUE = "#0078d4"
INPUT = "#313131"
FONT = ("Microsoft YaHei UI", 10)
SMALL = ("Microsoft YaHei UI", 9)
MONO = ("Consolas", 11)
SIDEBAR_WIDTH = 510


class Tooltip:
    """A delayed hint that does not take focus away from the editor."""
    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self.pending = None
        self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        for event in ("<Leave>", "<ButtonPress>", "<Escape>", "<Unmap>", "<Destroy>"):
            widget.bind(event, self.hide, add="+")

    def schedule(self, _event=None):
        self.hide()
        self.pending = self.widget.after(self.delay, self.show)

    def show(self):
        self.pending = None
        if not self.text or not self.widget.winfo_viewable():
            return
        self.window = tip = tk.Toplevel(self.widget)
        tip.withdraw()
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=self.text, justify="left", wraplength=360,
                 bg="#252526", fg=TEXT, font=FONT, padx=12, pady=9,
                 relief="solid", borderwidth=1).pack()
        tip.update_idletasks()
        x = min(self.widget.winfo_rootx(), self.widget.winfo_screenwidth() - tip.winfo_reqwidth() - 8)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        if y + tip.winfo_reqheight() > self.widget.winfo_screenheight():
            y = self.widget.winfo_rooty() - tip.winfo_reqheight() - 6
        tip.geometry(f"+{max(0, x)}+{max(0, y)}")
        tip.deiconify()

    def hide(self, _event=None):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            window = self.window
            self.window = None
            if window.winfo_exists():
                window.destroy()


def dark_caption(window):
    """Request a dark native caption for this application's own Windows window."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        handle = user32.GetParent(window.winfo_id())
        enabled = ctypes.c_int(1)
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute(handle, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
        for attribute, color in ((34, 0x2b2b2b), (35, 0x181818), (36, 0xcccccc)):
            value = ctypes.c_uint(color)
            dwm.DwmSetWindowAttribute(handle, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


class WorkbenchTabs(tk.Frame):
    """Flat editor tabs without the platform theme's raised notebook borders."""
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.header = tk.Frame(self, bg=BAR, height=38)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        self.pages = []
        self.buttons = []
        self.selected = None

    def add(self, page, *, text):
        index = len(self.pages)
        self.pages.append(page)
        group = tk.Frame(self.header, bg=BAR)
        group.pack(side="left", fill="y")
        button = tk.Label(group, text=text, bg=BAR, fg=MUTED, padx=19, pady=8,
                          font=SMALL, cursor="hand2")
        button.pack(fill="both", expand=True)
        underline = tk.Frame(group, bg=BAR, height=2)
        underline.pack(fill="x")
        button.bind("<Button-1>", lambda _: self.select(index))
        self.buttons.append((button, underline))
        if self.selected is None:
            self.select(0)

    def select(self, index=None):
        if index is None:
            return str(self.pages[self.selected]) if self.selected is not None else ""
        if not isinstance(index, int):
            index = next(i for i, page in enumerate(self.pages) if str(page) == str(index))
        for position, page in enumerate(self.pages):
            page.pack_forget()
            button, underline = self.buttons[position]
            button.configure(bg=BG if position == index else BAR, fg=TEXT if position == index else MUTED)
            underline.configure(bg=BLUE if position == index else BAR)
        self.pages[index].pack(fill="both", expand=True)
        self.selected = index


class WorkbenchMixin:
    def _configure_style(self):
        self.root.configure(background=BG)
        self.root.option_add("*Font", "{Microsoft YaHei UI} 10")
        self.root.option_add("*TCombobox*Listbox.background", INPUT)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#04395e")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=FONT, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, troughcolor=SIDE, focuscolor=BLUE)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Heading.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("TButton", background="#333333", foreground=TEXT, padding=(12, 5), borderwidth=0, width=0)
        style.map("TButton", background=[("active", "#454545"), ("pressed", "#505050")],
                  foreground=[("disabled", "#666666"), ("active", "white")])
        style.configure("Flat.TButton", background=BG, padding=(10, 5))
        style.map("Flat.TButton", background=[("active", "#333333"), ("pressed", "#454545")])
        style.configure("Sidebar.TButton", background=SIDE, padding=(9, 5))
        style.map("Sidebar.TButton", background=[("active", "#2a2d2e")])
        style.configure("Primary.TButton", background=BLUE, foreground="white", padding=(14, 5))
        style.map("Primary.TButton", background=[("active", "#1686d9"), ("pressed", "#0062a3")])
        style.configure("Danger.TButton", foreground="#f48771", background=BG, padding=(10, 5))
        style.map("Danger.TButton", background=[("active", "#3a2424")])
        style.configure("TEntry", fieldbackground=INPUT, foreground=TEXT, insertcolor="white", padding=(8, 6),
                        borderwidth=1, bordercolor="#454545", lightcolor=INPUT, darkcolor=INPUT)
        style.map("TEntry", bordercolor=[("focus", BLUE)])
        style.configure("TCombobox", fieldbackground=INPUT, foreground=TEXT, background=INPUT,
                        arrowcolor=TEXT, insertcolor="white", padding=(7, 6), borderwidth=1)
        style.map("TCombobox", fieldbackground=[("readonly", INPUT)], foreground=[("readonly", TEXT)],
                  selectbackground=[("readonly", "#264f78")], selectforeground=[("readonly", "white")])
        style.configure("TPanedwindow", background=BORDER, sashwidth=3)
        style.configure("Treeview", background=BG, fieldbackground=BG, foreground=TEXT,
                        rowheight=30, borderwidth=0, font=MONO)
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.map("Treeview", background=[("selected", "#264f78")], foreground=[("selected", "#ffffff")])
        style.configure("Explorer.Treeview", background=SIDE, fieldbackground=SIDE, font=("Segoe UI", 10), rowheight=29)
        style.configure("Treeview.Heading", background=BG, foreground="#9d9d9d", relief="flat",
                        font=SMALL, padding=(8, 8), borderwidth=0)
        style.map("Treeview.Heading", background=[("active", "#292929")])
        style.configure("TScrollbar", background="#424242", troughcolor=BG, borderwidth=0,
                        arrowcolor="#9b9b9b", arrowsize=11, relief="flat")
        style.map("TScrollbar", background=[("active", "#626262")])
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0)
        style.configure("TNotebook.Tab", background=BAR, foreground=MUTED, padding=(18, 9), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", BG), ("active", "#282828")],
                  foreground=[("selected", "#ffffff"), ("active", TEXT)])
        style.layout("TNotebook.Tab", [("Notebook.tab", {"sticky": "nswe", "children": [
            ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                ("Notebook.label", {"side": "top", "sticky": ""})]})]})])
        style.configure("Dialog.TFrame", background="#252526")
        style.configure("Dialog.TLabel", background="#252526", foreground=TEXT, font=SMALL)

    def _build(self):
        self.tab_title = tk.StringVar(value="未打开文件")
        self.breadcrumb = tk.StringVar(value="Fantasy Protocol")
        self.message_count = tk.StringVar(value="0 条消息")
        self.schema_summary = tk.StringVar(value="")
        self.file_info = tk.StringVar(value="UTF-8   ·   Fantasy Protocol")
        self.dirty_indicator = tk.StringVar(value="")
        self.sidebar_visible = True
        # Compact application menu and a real quick-search entry, like a workbench title bar.
        top = tk.Frame(self, bg=BAR, height=38)
        top.pack(fill="x")
        top.pack_propagate(False)
        menu_definitions = [
            ("文件", [("打开文件…", self.open_file, "Ctrl+O"), ("打开示例", self.open_default, ""),
                       ("保存", self.save, "Ctrl+S"), ("保存并导出", lambda: self.save(export=True), ""),
                       ("仅保存", lambda: self.save(export=False), ""),
                       ("另存为…", lambda: self.save(as_new=True), "Ctrl+Shift+S")]),
            ("编辑", [("撤销", self.undo, "Ctrl+Z"), ("重做", self.redo, "Ctrl+Y"),
                       ("新增消息…", self.add_message, ""), ("新增 RPC…", self.add_rpc, ""),
                       ("命名前缀设置…", self.edit_prefixes, "")]),
            ("视图", [("显示 / 隐藏侧栏", self.toggle_sidebar, "Ctrl+B"), ("搜索消息", self.focus_search, "Ctrl+P"),
                       ("检查协议", self.check, "")]),
            ("帮助", [("快捷操作", self.show_help, "")]),
        ]
        for name, commands in menu_definitions:
            button = tk.Menubutton(top, text=name, bg=BAR, fg=TEXT, activebackground="#303030",
                                   activeforeground="white", relief="flat", padx=10, cursor="hand2")
            menu = tk.Menu(button, tearoff=False, bg="#252526", fg=TEXT, activebackground="#04395e",
                           activeforeground="white", borderwidth=1)
            for label, command, accelerator in commands:
                menu.add_command(label=label, command=command, accelerator=accelerator)
            if name == "文件":
                menu.add_separator()
                menu.add_checkbutton(label="保存后自动导出", variable=self.auto_export, command=self.persist_state)
            button.configure(menu=menu)
            button.pack(side="left")
        quick = tk.Frame(top, bg=INPUT, highlightthickness=1, highlightbackground="#454545")
        quick.pack(side="left", fill="x", expand=True, padx=(55, 80), pady=5)
        tk.Label(quick, text="⌕", bg=INPUT, fg=MUTED, font=("Segoe UI", 13), padx=8).pack(side="left")
        self.quick_search = tk.Entry(quick, textvariable=self.search, bg=INPUT, fg=TEXT, insertbackground="white",
                                    relief="flat", highlightthickness=0, font=SMALL)
        self.quick_search.pack(side="left", fill="x", expand=True, pady=3)
        tk.Label(quick, text="搜索消息   Ctrl+P", bg=INPUT, fg=MUTED, font=("Segoe UI", 9), padx=8).pack(side="right")
        self.export_button = ttk.Button(top, text="保存并导出", command=lambda: self.save(export=True), style="Primary.TButton")
        self.export_button.pack(side="right", padx=10, pady=4)
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        status_bar = tk.Frame(self, bg="#007acc", height=24)
        status_bar.pack(side="bottom", fill="x")
        status_bar.pack_propagate(False)
        tk.Label(status_bar, textvariable=self.file_info, bg="#007acc", fg="white", font=("Segoe UI", 9), padx=14).pack(side="right")
        tk.Label(status_bar, textvariable=self.status, anchor="w", bg="#007acc", fg="white", font=SMALL,
                 padx=10).pack(side="left", fill="x", expand=True)
        work = tk.Frame(self, bg=BG)
        work.pack(fill="both", expand=True)
        self.panes = ttk.Panedwindow(work, orient="horizontal")
        self.panes.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(self.panes, bg=SIDE, width=SIDEBAR_WIDTH)
        self.editor_area = tk.Frame(self.panes, bg=BG)
        self.panes.add(self.sidebar, weight=0)
        self.panes.add(self.editor_area, weight=1)
        heading = tk.Frame(self.sidebar, bg=SIDE, padx=17, pady=13)
        heading.pack(fill="x")
        tk.Label(heading, text="消息定义", bg=SIDE, fg=TEXT, font=SMALL).pack(side="left")
        tk.Label(heading, text="PROTOCOLS", bg=SIDE, fg="#626262", font=("Segoe UI", 8)).pack(side="right")
        self.explorer_file = tk.Label(self.sidebar, textvariable=self.tab_title, bg=SIDE, fg="#dcb67a",
                                     anchor="w", font=("Segoe UI", 10), padx=20, pady=10)
        self.explorer_file.pack(fill="x")
        sidebar_tools = tk.Frame(self.sidebar, bg=SIDE, padx=6, pady=7)
        sidebar_tools.pack(fill="x")
        self.add_message_button = ttk.Button(sidebar_tools, text="＋ 消息", command=self.add_message, style="Sidebar.TButton")
        self.add_message_button.pack(side="left")
        self.add_message_button.tooltip = Tooltip(self.add_message_button,
            "新增一条独立消息\n用于单向通知、服务器推送或普通数据结构。\n例如：G2C_PushMessage（服务器向客户端推送消息）。\n只创建一条定义；需要请求和回复时可用右侧的「＋ RPC」。")
        self.add_rpc_button = ttk.Button(sidebar_tools, text="＋ RPC", command=self.add_rpc, style="Sidebar.TButton")
        self.add_rpc_button.pack(side="left", padx=3)
        self.add_rpc_button.tooltip = Tooltip(self.add_rpc_button,
            "成对新增请求和回复\n用于发送请求后，需要等待对方返回结果的操作。\n例如：登录请求 C2G_LoginRequest 和登录回复 G2C_LoginResponse。\n自动创建两条定义，并设置 IRequest / IResponse 及回复关联；具体处理逻辑需在代码中编写。")
        tree_frame = tk.Frame(self.sidebar, bg=SIDE)
        tree_frame.pack(fill="both", expand=True)
        self.message_tree = ttk.Treeview(tree_frame, columns=("description",), show="tree headings", selectmode="browse", style="Explorer.Treeview")
        self.message_tree.heading("#0", text="协议名称", anchor="w")
        self.message_tree.column("#0", width=275, minwidth=180, stretch=False)
        self.message_tree.heading("description", text="中文注释 / 说明", anchor="w")
        self.message_tree.column("description", width=215, minwidth=140, anchor="w")
        list_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.message_tree.yview)
        list_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.message_tree.xview)
        self.message_tree.configure(yscrollcommand=list_scroll.set, xscrollcommand=list_x.set)
        list_x.pack(side="bottom", fill="x")
        self.message_tree.pack(side="left", fill="both", expand=True)
        list_scroll.pack(side="right", fill="y")
        self.message_tree.bind("<<TreeviewSelect>>", self.select_message)
        self.message_tree.bind("<Double-1>", lambda _: self.edit_message())
        self.message_tree.bind("<Delete>", lambda _: self.delete_message())
        tk.Label(self.sidebar, textvariable=self.message_count, bg=SIDE, fg=MUTED, anchor="w",
                 font=SMALL, padx=16, pady=9).pack(fill="x")

        tab_bar = tk.Frame(self.editor_area, bg=BAR, height=37)
        tab_bar.pack(fill="x")
        tab_bar.pack_propagate(False)
        file_tab = tk.Frame(tab_bar, bg=BG)
        file_tab.pack(side="left", fill="y")
        tk.Frame(file_tab, bg=BLUE, height=1).pack(fill="x")
        tab_content = tk.Frame(file_tab, bg=BG, padx=15)
        tab_content.pack(fill="both", expand=True)
        tk.Label(tab_content, text="{ }", bg=BG, fg="#dcb67a", font=("Consolas", 11)).pack(side="left", padx=(0, 9))
        tk.Label(tab_content, textvariable=self.tab_title, bg=BG, fg=TEXT, font=("Segoe UI", 10)).pack(side="left")
        tk.Label(tab_content, textvariable=self.dirty_indicator, bg=BG, fg=TEXT, font=("Segoe UI", 10), width=2).pack(side="left", padx=(7, 0))
        ttk.Button(tab_bar, text="检查协议", command=self.check, style="Flat.TButton").pack(side="right", padx=9, pady=2)
        tk.Label(self.editor_area, textvariable=self.breadcrumb, bg=BG, fg=MUTED, anchor="w",
                 font=("Segoe UI", 9), padx=20, pady=9).pack(fill="x")
        detail = tk.Frame(self.editor_area, bg=BG, padx=22, pady=11)
        detail.pack(fill="x")
        tk.Label(detail, textvariable=self.selection_title, bg=BG, fg="#e7e7e7", anchor="w",
                 font=("Segoe UI", 17)).pack(anchor="w")
        tk.Label(detail, textvariable=self.selection_meta, bg=BG, fg=MUTED, anchor="w", justify="left",
                 font=SMALL, wraplength=760).pack(anchor="w", pady=(6, 0))
        actions = tk.Frame(self.editor_area, bg=BG, padx=13, pady=8)
        actions.pack(fill="x")
        ttk.Button(actions, text="编辑消息属性", command=self.edit_message, style="Flat.TButton").pack(side="left")
        ttk.Button(actions, text="删除消息", command=self.delete_message, style="Danger.TButton").pack(side="left", padx=5)
        tk.Label(actions, textvariable=self.schema_summary, bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="right", padx=10)
        tk.Frame(self.editor_area, bg=BORDER, height=1).pack(fill="x")
        self.tabs = WorkbenchTabs(self.editor_area)
        self.tabs.pack(fill="both", expand=True)
        field_tab = ttk.Frame(self.tabs, padding=(17, 13, 17, 0))
        source_tab = ttk.Frame(self.tabs)
        export_tab = ttk.Frame(self.tabs, padding=(17, 12))
        self.tabs.add(field_tab, text="字段结构")
        self.tabs.add(source_tab, text="原文预览")
        self.tabs.add(export_tab, text="导出日志")
        export_actions = ttk.Frame(export_tab)
        export_actions.pack(fill="x", pady=(0, 10))
        ttk.Label(export_actions, text="ProtocolExportTool", style="Muted.TLabel").pack(side="left")
        ttk.Button(export_actions, text="清空日志", command=self.clear_export_log, style="Flat.TButton").pack(side="right")
        export_frame = ttk.Frame(export_tab)
        export_frame.pack(fill="both", expand=True)
        self.export_output = tk.Text(export_frame, wrap="word", font=MONO, bg=BG, fg=TEXT,
                                     padx=10, pady=10, borderwidth=0, highlightthickness=0, state="disabled")
        export_scroll = ttk.Scrollbar(export_frame, orient="vertical", command=self.export_output.yview)
        self.export_output.configure(yscrollcommand=export_scroll.set)
        export_scroll.pack(side="right", fill="y")
        self.export_output.pack(fill="both", expand=True)
        self.export_output.tag_configure("success", foreground="#4ec9b0")
        self.export_output.tag_configure("error", foreground="#f48771")
        self.export_output.tag_configure("info", foreground="#569cd6")
        field_tools = ttk.Frame(field_tab)
        field_tools.pack(fill="x", pady=(0, 10))
        ttk.Button(field_tools, text="＋ 添加字段", command=self.add_field, style="Primary.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(field_tools, text="编辑", command=self.edit_field, style="Flat.TButton").pack(side="left")
        ttk.Button(field_tools, text="删除", command=self.delete_field, style="Flat.TButton").pack(side="left", padx=3)
        field_frame = tk.Frame(field_tab, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        field_frame.pack(fill="both", expand=True)
        columns = ("number", "label", "type", "name", "description")
        self.field_tree = ttk.Treeview(field_frame, columns=columns, show="headings", selectmode="browse")
        for key, title, width in [("number", "#", 52), ("label", "修饰符", 110), ("type", "类型", 145),
                                  ("name", "字段名称", 155), ("description", "说明", 210)]:
            self.field_tree.heading(key, text=title, anchor="w")
            self.field_tree.column(key, width=width, minwidth=45, anchor="w", stretch=key in ("type", "name", "description"))
        self.field_tree.tag_configure("even", background=BG)
        self.field_tree.tag_configure("odd", background="#232323")
        field_scroll = ttk.Scrollbar(field_frame, orient="vertical", command=self.field_tree.yview)
        horizontal = ttk.Scrollbar(field_tab, orient="horizontal", command=self.field_tree.xview)
        self.field_tree.configure(yscrollcommand=field_scroll.set, xscrollcommand=horizontal.set)
        self.field_tree.pack(side="left", fill="both", expand=True)
        field_scroll.pack(side="right", fill="y")
        horizontal.pack(fill="x")
        self.field_tree.bind("<Double-1>", lambda _: self.edit_field())
        self.field_tree.bind("<Delete>", lambda _: self.delete_field())
        self.field_empty = tk.Label(field_frame, text="{ }\n\n暂无字段\n点击「添加字段」开始定义消息内容", bg=BG,
                                    fg=MUTED, font=("Microsoft YaHei UI", 11), justify="center")
        tk.Label(field_tab, text="双击字段编辑   ·   Delete 删除   ·   Ctrl+S 保存", bg=BG, fg="#707070",
                 anchor="w", font=SMALL, pady=11).pack(fill="x")
        self.line_numbers = tk.Text(source_tab, width=5, wrap="none", bg=BG, fg="#6e7681", relief="flat",
                                    font=MONO, padx=5, pady=14, borderwidth=0, state="disabled", takefocus=False)
        self.line_numbers.pack(side="left", fill="y")
        source_frame = ttk.Frame(source_tab)
        source_frame.pack(fill="both", expand=True)
        self.preview = tk.Text(source_frame, wrap="none", font=MONO, bg=BG, fg="#d4d4d4", padx=10, pady=14,
                               borderwidth=0, highlightthickness=0, selectbackground="#264f78", state="disabled")
        self.preview_scroll = ttk.Scrollbar(source_frame, orient="vertical", command=self.scroll_preview)
        preview_x = ttk.Scrollbar(source_frame, orient="horizontal", command=self.preview.xview)
        self.preview.configure(yscrollcommand=self.preview_scrolled, xscrollcommand=preview_x.set)
        preview_x.pack(side="bottom", fill="x")
        self.preview_scroll.pack(side="right", fill="y")
        self.preview.pack(fill="both", expand=True)
        self.preview.tag_configure("comment", foreground="#6a9955")
        self.preview.tag_configure("keyword", foreground="#569cd6")
        self.root.bind("<Control-b>", lambda _: self.toggle_sidebar())
        self.root.bind("<Control-p>", lambda _: self.focus_search())
        self.root.bind("<Map>", lambda event: dark_caption(self.root) if event.widget is self.root else None, add="+")
        self.root.after_idle(lambda: self.panes.sashpos(0, SIDEBAR_WIDTH) if self.sidebar_visible else None)

    def scroll_preview(self, *args):
        self.preview.yview(*args)
        self.line_numbers.yview_moveto(self.preview.yview()[0])

    def preview_scrolled(self, start, end):
        self.preview_scroll.set(start, end)
        self.line_numbers.yview_moveto(start)

    def toggle_sidebar(self):
        if self.sidebar_visible:
            self.panes.forget(self.sidebar)
        else:
            self.panes.insert(0, self.sidebar, weight=0)
            self.panes.sashpos(0, SIDEBAR_WIDTH)
        self.sidebar_visible = not self.sidebar_visible

    def focus_search(self):
        if not self.sidebar_visible:
            self.toggle_sidebar()
        self.quick_search.focus_set()
        self.quick_search.selection_range(0, "end")

    def show_help(self):
        messagebox.showinfo("快捷操作", "Ctrl+O  打开协议\nCtrl+S  保存\nCtrl+Z / Ctrl+Y  撤销 / 重做\n"
                            "Ctrl+P  搜索消息\nCtrl+B  显示 / 隐藏侧栏\n\n双击消息编辑属性，双击字段编辑内容。", parent=self.root)

    def update_workbench(self):
        if not self.document:
            return
        name = self.document.path.name if self.document.path else "未命名.proto"
        parent = self.document.path.parent.name if self.document.path else "Protocols"
        self.tab_title.set(name)
        self.dirty_indicator.set("●" if self.document.dirty else "")
        self.breadcrumb.set(f"{parent}   ›   {name}" + (f"   ›   {self.selected_name}" if self.selected_name else ""))
        newline = "CRLF" if self.document.newline == "\r\n" else "LF"
        encoding = "UTF-8 BOM" if self.document.bom else "UTF-8"
        self.file_info.set(f"{encoding}    {newline}    Fantasy Protocol")
        self.message_count.set(f"{len(self.message_tree.get_children())} / {len(self.document.messages)} 条消息")
        message = self.document.get(self.selected_name) if self.selected_name else None
        self.schema_summary.set(f"{len(message.fields)} fields" if message else "")
        if message and not message.fields:
            self.field_empty.place(relx=.5, rely=.4, anchor="center")
        else:
            self.field_empty.place_forget()
