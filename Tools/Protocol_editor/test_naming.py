from pathlib import Path
from types import SimpleNamespace
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from naming import DEFAULT_PREFIXES, NamingPrefix, compose_name, load_prefixes, save_prefixes, split_name, validate_prefixes
from protocol_editor import FormDialog, PrefixSettings, ProtocolEditor
from proto_model import ProtocolDocument


class NamingTests(unittest.TestCase):
    def test_missing_file_uses_defaults_and_empty_saved_list_stays_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefixes.json"
            self.assertEqual(load_prefixes(path), list(DEFAULT_PREFIXES))
            save_prefixes(path, [])
            self.assertEqual(load_prefixes(path), [])

    def test_custom_prefix_meaning_and_multiline_description_survive_restart(self):
        entries = [NamingPrefix("C2Chat_", "客户端 → 聊天服务器", "发送聊天内容。\n也用于加入频道。")]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefixes.json"
            save_prefixes(path, entries)
            self.assertEqual(load_prefixes(path), entries)
            self.assertFalse(list(path.parent.glob(".prefixes-*.tmp")))

    def test_invalid_or_duplicate_entries_are_rejected_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefixes.json"
            save_prefixes(path, DEFAULT_PREFIXES)
            original = path.read_bytes()
            for entries in ([NamingPrefix("C2G", "缺少下划线")], [NamingPrefix("2G_", "数字开头")],
                            [NamingPrefix("C2G_", "")], [DEFAULT_PREFIXES[0], DEFAULT_PREFIXES[0]],
                            [NamingPrefix("C2G_", "两行\n含义")]):
                with self.subTest(entries=entries), self.assertRaises(ValueError):
                    save_prefixes(path, entries)
                self.assertEqual(path.read_bytes(), original)
            with patch("naming.os.replace", side_effect=OSError("locked")), self.assertRaises(OSError):
                save_prefixes(path, [])
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(path.parent.glob(".prefixes-*.tmp")))

    def test_invalid_config_is_reported_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefixes.json"
            for text in ('{"prefixes":', '{"prefixes": [{"prefix": 7, "meaning": "x"}]}'):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_prefixes(path)
                self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_names_keep_unknown_prefixes_and_avoid_duplicate_pasted_prefix(self):
        self.assertEqual(compose_name("C2G_", " LoginMessage "), "C2G_LoginMessage")
        self.assertEqual(compose_name("C2G_", "C2G_LoginMessage"), "C2G_LoginMessage")
        self.assertEqual(compose_name("", "PlayerInfo"), "PlayerInfo")
        self.assertEqual(split_name("C2G_LoginMessage", DEFAULT_PREFIXES), ("C2G_", "LoginMessage"))
        self.assertEqual(split_name("Custom_Data", DEFAULT_PREFIXES), ("", "Custom_Data"))


class NamingGuiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.editor = ProtocolEditor(self.root)
        self.root.update()

    def tearDown(self):
        for callback in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", callback)
        self.root.destroy()
        self.assertFalse(self.errors, self.errors)

    def form(self, prefixes=DEFAULT_PREFIXES):
        return FormDialog(self.root, "新增消息", [("name", "消息名称", "C2G_NewMessage", None)],
                          name_prefixes=prefixes)

    def test_live_preview_no_prefix_and_full_name_result(self):
        dialog = self.form()
        name = dialog.name_inputs["name"]
        self.assertEqual(name.prefix.get(), "C2G_")
        self.assertEqual(name.suffix.get(), "NewMessage")
        name.suffix.set("LoginMessage")
        self.assertEqual(dialog.values["name"].get(), "C2G_LoginMessage")
        self.assertIn("C2G_LoginMessage", name.preview.get())
        name.prefix.set("")
        name.suffix.set("PlayerInfo")
        dialog.accept()
        self.assertEqual(dialog.result["name"], "PlayerInfo")

    def test_blank_body_cannot_create_prefix_only_message(self):
        dialog = self.form()
        dialog.name_inputs["name"].suffix.set(" ")
        dialog.accept()
        self.assertIsNone(dialog.result)
        self.assertIn("不能只填写前缀", dialog.error.get())

    def test_new_message_keeps_optional_response_field_and_rpc_has_two_name_inputs(self):
        self.editor.document = ProtocolDocument("")
        captured = []
        def accept(dialog):
            captured.append(dialog)
            for control in dialog.name_inputs.values():
                control.suffix.set(control.suffix.get().replace("New", "Smoke"))
            dialog.accept()
            return dialog.result
        with patch.object(FormDialog, "show", accept):
            self.editor.add_message()
            self.assertEqual(set(captured[-1].name_inputs), {"name"})
            self.assertEqual(self.editor.document.get("C2G_SmokeMessage").interface, "IMessage")
            self.editor.add_rpc()
            self.assertEqual(set(captured[-1].name_inputs), {"request", "response"})
            self.assertEqual(self.editor.document.get("C2G_SmokeRequest").parts, ["IRequest", "G2C_SmokeResponse"])

    def test_popup_hover_details_selection_and_modal_grab_restoration(self):
        self.root.deiconify()
        custom = NamingPrefix("C2Chat_", "客户端 → 聊天服务器", "发送聊天内容及加入频道。")
        dialog = self.form([*DEFAULT_PREFIXES, custom])
        dialog.grab_set()
        self.root.update()
        name = dialog.name_inputs["name"]
        name.open_popup()
        self.root.update()
        row = str(len(name.entries) - 1)
        x, y, width, height = name.tree.bbox(row)
        name.hover_row(SimpleNamespace(y=y + height // 2))
        name.hint.show()
        self.assertIn(custom.description, name.hint.text)
        self.assertTrue(name.hint.window.winfo_viewable())
        name.choose(row)
        self.assertEqual(name.prefix.get(), "C2Chat_")
        self.assertEqual(name.variable.get(), "C2Chat_NewMessage")
        self.assertIsNone(name.popup)
        self.assertEqual(dialog.grab_current(), dialog)
        name.open_popup()
        self.root.update()
        dialog.destroy()
        self.root.update()

    def test_settings_add_edit_delete_cancel_and_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefixes.json"
            received = []
            manager = PrefixSettings(self.root, DEFAULT_PREFIXES, path, received.append)
            custom = {"prefix": "C2Chat_", "meaning": "客户端 → 聊天服务器", "doc": "聊天说明"}
            with patch("protocol_editor.FormDialog.show", return_value=custom):
                manager.edit_entry(new=True)
            self.assertEqual(manager.entries[-1].prefix, "C2Chat_")
            with patch("protocol_editor.FormDialog.show", return_value={**custom, "meaning": "新的含义"}):
                manager.edit_entry()
            self.assertEqual(manager.entries[-1].meaning, "新的含义")
            manager.delete_entry()
            self.assertEqual(manager.entries, list(DEFAULT_PREFIXES))
            manager.destroy()
            self.assertFalse(path.exists())
            self.assertFalse(received)
            manager = PrefixSettings(self.root, [NamingPrefix(custom["prefix"], custom["meaning"], custom["doc"])], path, received.append)
            manager.accept()
            self.assertEqual(load_prefixes(path), received[0])
            self.assertEqual(received[0][0].prefix, "C2Chat_")

    def test_settings_save_error_keeps_dialog_and_previous_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefixes.json"
            received = []
            manager = PrefixSettings(self.root, DEFAULT_PREFIXES, path, received.append)
            with patch("protocol_editor.save_prefixes", side_effect=OSError("只读")):
                manager.accept()
            self.assertTrue(manager.winfo_exists())
            self.assertFalse(received)
            self.assertIn("只读", manager.error.get())


if __name__ == "__main__":
    unittest.main()
