from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import patch

from proto_model import ProtocolDocument, ProtocolError
from protocol_editor import DEFAULT_FILE, PROJECT_ROOT, FormDialog, ProtocolEditor, TOOL_DIR
from export_runner import ExportCommand, ExportError, ExportResult, prepare_export, run_export


FIXTURE = '''syntax = "proto3";
package Demo;

// using Demo.Types
////[Serializable]
/// 请求说明
// Protocol MemoryPack
message C2G_TestRequest // IRequest,G2C_TestResponse
{
    /// 名称
    string Name = 1; // 保留说明; 不要丢失
    # [SomeAttribute]
    repeatedList byte Data = 2;
    map<int32, Item> Items = 3;
}
// Protocol MemoryPack
message G2C_TestResponse // IResponse
{
    string Text = 1;
}
enum State { Off = 0; On = 1; }
message Item
{
    int64 Id = 1;
}
'''


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.doc = ProtocolDocument(FIXTURE)

    def test_repository_load_is_lossless_and_valid(self):
        data = DEFAULT_FILE.read_bytes()
        doc = ProtocolDocument.load(DEFAULT_FILE)
        self.assertEqual(doc.text.encode("utf-8"), data)
        self.assertGreater(len(doc.messages), 50)
        self.assertEqual(doc.validate(), [])
        self.assertFalse(any(m.readonly_reason for m in doc.messages))

    def test_real_other_protocols_parse_without_losing_source(self):
        for path in DEFAULT_FILE.parent.parent.rglob("*.proto"):
            with self.subTest(path=path.name):
                doc = ProtocolDocument.load(path)
                self.assertEqual(doc.text.encode("utf-8"), path.read_bytes())

    def test_parse_fantasy_metadata_and_fields(self):
        message = self.doc.get("C2G_TestRequest")
        self.assertEqual(message.annotation, "IRequest,G2C_TestResponse")
        self.assertEqual(message.serialization, "MemoryPack")
        self.assertEqual(message.description, "请求说明")
        self.assertEqual(message.fields[1].label, "repeatedList")
        self.assertEqual(message.fields[2].type_name, "map<int32,Item>")
        self.assertIn("保留说明; 不要丢失", message.fields[0].description)
        self.assertEqual(self.doc.validate(), [])

    def test_edit_field_keeps_comments_options_and_unrelated_text(self):
        message = self.doc.get("C2G_TestRequest")
        item = message.fields[0]
        self.doc.edit_field(message.name, 0, name="AccountName", type_name="string", number=7, doc=item.description)
        expected = FIXTURE.replace("string Name = 1;", "string AccountName = 7;")
        self.assertEqual(self.doc.text, expected)

    def test_field_doc_edit_preserves_code_injection(self):
        self.doc.edit_field("C2G_TestRequest", 1, name="Data", type_name="byte", number=2,
                            label="repeatedList", doc="二进制内容")
        self.assertIn("# [SomeAttribute]", self.doc.text)
        self.assertIn("/// 二进制内容", self.doc.text)

    def test_field_options_survive_edits(self):
        doc = ProtocolDocument('message Item\n{\n  string Name = 1 [json_name = "x;y"];// note\n}\n')
        item = doc.get("Item").fields[0]
        doc.edit_field("Item", 0, name="Title", type_name="string", number=1, doc=item.description)
        self.assertIn('[json_name = "x;y"];// note', doc.text)

    def test_add_and_remove_field_does_not_renumber_others(self):
        self.doc.edit_field("Item", None, name="Level", type_name="int32", number=8, doc="等级")
        self.assertEqual([f.number for f in self.doc.get("Item").fields], [1, 8])
        self.doc.delete_field("Item", 0)
        self.assertEqual(self.doc.get("Item").fields[0].number, 8)
        self.assertNotIn("int64 Id", self.doc.text)

    def test_duplicate_field_rejected_without_mutation(self):
        for name, number in [("Id", 9), ("Other", 1)]:
            with self.subTest(name=name), self.assertRaises(ProtocolError):
                self.doc.edit_field("Item", None, name=name, type_name="int32", number=number)
        self.assertEqual(self.doc.text, FIXTURE)

    def test_invalid_numbers_and_names_rejected(self):
        for number in (0, -1, 19000, 19999, 536870912):
            with self.subTest(number=number), self.assertRaises(ProtocolError):
                self.doc.check_field("Value", "int32", number, "")
        with self.assertRaises(ProtocolError):
            self.doc.check_field("1Bad", "int32", 1, "")

    def test_map_validation(self):
        self.doc.check_field("Items", "map<int32,string>", 1, "")
        for key, label in [("float", ""), ("string", "repeated")]:
            with self.subTest(key=key), self.assertRaises(ProtocolError):
                self.doc.check_field("Items", f"map<{key},string>", 1, label)
        self.doc.check_field("Items", "mapNode", 1, "repeated")

    def test_rpc_pair_creation_and_metadata(self):
        self.doc.add_rpc("C2G_HelloRequest", "G2C_HelloResponse", "问候")
        self.assertEqual(self.doc.get("C2G_HelloRequest").parts, ["IRequest", "G2C_HelloResponse"])
        self.assertEqual(self.doc.validate(), [])

    def test_rename_response_updates_requests_and_field_types(self):
        self.doc.edit_message("Item", "InventoryItem", "", "", "")
        self.assertIn("map<int32,InventoryItem> Items", self.doc.text)
        response = self.doc.get("G2C_TestResponse")
        self.doc.edit_message(response.name, "G2C_Reply", response.annotation, response.description, response.serialization)
        self.assertIn("IRequest,G2C_Reply", self.doc.text)
        self.assertEqual(self.doc.validate(), [])

    def test_edit_message_preserves_raw_injection_and_using(self):
        self.doc.edit_message("C2G_TestRequest", "C2G_TestRequest", "IRequest,G2C_TestResponse", "新的说明", "ProtoBuf")
        self.assertIn("// using Demo.Types", self.doc.text)
        self.assertIn("////[Serializable]", self.doc.text)
        self.assertIn("// Protocol ProtoBuf", self.doc.text)
        self.assertEqual(self.doc.get("C2G_TestRequest").description, "新的说明")

    def test_noop_message_edit_is_exact(self):
        message = self.doc.get("C2G_TestRequest")
        self.doc.edit_message(message.name, message.name, message.annotation, message.description, message.serialization)
        self.assertEqual(self.doc.text, FIXTURE)

    def test_delete_response_with_reference_is_blocked(self):
        with self.assertRaises(ProtocolError):
            self.doc.delete_message("G2C_TestResponse")
        self.assertEqual(self.doc.text, FIXTURE)

    def test_delete_request_then_response(self):
        self.doc.delete_message("C2G_TestRequest")
        self.doc.delete_message("G2C_TestResponse")
        self.assertEqual([m.name for m in self.doc.messages], ["Item"])
        self.assertIn("enum State", self.doc.text)
        self.assertIn("// using Demo.Types", self.doc.text)

    def test_validate_missing_response_and_duplicate_number(self):
        doc = ProtocolDocument('message Q // IRequest,Missing\n{\n int32 A = 1;\n int32 B = 1;\n}\n')
        errors = "\n".join(doc.validate())
        self.assertIn("回复消息不存在", errors)
        self.assertIn("重复", errors)

    def test_comments_and_strings_do_not_affect_braces(self):
        doc = ProtocolDocument('/* { } */\nmessage X\n{\n string A = 1 [json_name="}"]; // {\n}\n')
        self.assertEqual(len(doc.get("X").fields), 1)

    def test_unbalanced_source_does_not_replace_document(self):
        with self.assertRaises(ProtocolError):
            self.doc.restore("message Bad {\n")
        self.assertEqual(self.doc.text, FIXTURE)

    def test_advanced_syntax_preserved_readonly(self):
        source = 'message X\n{\n oneof choice {\n string Name = 1;\n int32 Id = 2;\n }\n}\n'
        doc = ProtocolDocument(source)
        self.assertTrue(doc.get("X").readonly_reason)
        with self.assertRaises(ProtocolError):
            doc.edit_field("X", None, name="Value", type_name="int32", number=3)
        self.assertEqual(doc.text, source)

    def test_compact_messages_preserve_adjacent_declarations_on_delete(self):
        doc = ProtocolDocument("message First {} message Second {}\n")
        self.assertTrue(doc.get("First").readonly_reason)
        doc.delete_message("First")
        self.assertIn("message Second {}", doc.text)
        self.assertEqual(doc.get("Second").name, "Second")

    def test_unedited_advanced_construct_survives_another_message_edit(self):
        source = 'message Advanced\n{\n reserved 2;\n oneof data {\n string Value = 1;\n }\n}\n'
        doc = ProtocolDocument(source + FIXTURE)
        doc.edit_field("Item", None, name="Level", type_name="int32", number=2)
        self.assertTrue(doc.text.startswith(source))

    def test_rpc_pair_duplicate_does_not_leave_partial_request(self):
        with self.assertRaises(ProtocolError):
            self.doc.add_rpc("NewRequest", "Item")
        self.assertEqual(self.doc.text, FIXTURE)

    def test_add_and_remove_interface_annotation(self):
        doc = ProtocolDocument("message Example\n{\n}\n")
        doc.edit_message("Example", "Example", "IMessage", "", "")
        self.assertEqual(doc.get("Example").interface, "IMessage")
        doc.edit_message("Example", "Example", "", "", "")
        self.assertEqual(doc.get("Example").interface, "")

    def test_enum_and_message_name_collision_is_reported(self):
        doc = ProtocolDocument("enum Item { Zero = 0; }\nmessage Item\n{\n}\n")
        self.assertTrue(any("类型名称重复" in error for error in doc.validate()))

    def test_external_reference_blocks_rename_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "A.proto").write_text('message Item\n{\n int32 Id = 1;\n}\n', encoding="utf-8")
            (root / "B.proto").write_text('message Other\n{\n .Demo.Item Data = 1;\n}\n', encoding="utf-8")
            doc = ProtocolDocument.load(root / "A.proto")
            with self.assertRaises(ProtocolError):
                doc.edit_message("Item", "NewItem", "", "", "")
            with self.assertRaises(ProtocolError):
                doc.delete_message("Item")

    def test_sibling_response_is_recognized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "A.proto").write_text('message Q // IRequest,R\n{\n}\n', encoding="utf-8")
            (root / "B.proto").write_text('message R // IResponse\n{\n}\n', encoding="utf-8")
            self.assertEqual(ProtocolDocument.load(root / "A.proto").validate(), [])

    def test_malformed_sibling_is_reported_before_save(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "A.proto").write_text(FIXTURE, encoding="utf-8")
            (root / "Bad.proto").write_text("message Bad {", encoding="utf-8")
            doc = ProtocolDocument.load(root / "A.proto")
            self.assertTrue(any("Bad.proto" in error for error in doc.validate()))
            with self.assertRaises(ProtocolError):
                doc.save()

    def test_atomic_save_backup_bom_and_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "A.proto"
            original = b"\xef\xbb\xbf" + FIXTURE.replace("\n", "\r\n").encode()
            path.write_bytes(original)
            doc = ProtocolDocument.load(path)
            doc.edit_field("Item", None, name="Level", type_name="int32", number=2)
            backup = doc.save(backup_dir=root / "backups")
            self.assertEqual(backup.read_bytes(), original)
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\n", path.read_bytes().replace(b"\r\n", b""))
            self.assertFalse(doc.dirty)
            self.assertFalse(list(root.glob(".protocol-*.tmp")))

    def test_noop_save_does_not_write_or_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "A.proto"
            path.write_bytes(FIXTURE.encode())
            timestamp = path.stat().st_mtime_ns
            doc = ProtocolDocument.load(path)
            self.assertIsNone(doc.save(backup_dir=path.parent / "backups"))
            self.assertEqual(path.stat().st_mtime_ns, timestamp)
            self.assertFalse((path.parent / "backups").exists())

    def test_conflicting_external_save_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "A.proto"
            path.write_bytes(FIXTURE.encode())
            doc = ProtocolDocument.load(path)
            doc.add_message("Added")
            external = FIXTURE + "// external edit\n"
            path.write_bytes(external.encode())
            with self.assertRaises(ProtocolError):
                doc.save()
            self.assertEqual(path.read_bytes(), external.encode())


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.editor = ProtocolEditor(self.root)
        self.editor.document = ProtocolDocument(FIXTURE)
        self.editor.selected_name = "C2G_TestRequest"
        self.editor.refresh()
        self.root.update()

    def tearDown(self):
        for callback in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", callback)
        self.root.destroy()

    def test_tree_search_and_preview(self):
        self.assertEqual(len(self.editor.message_tree.get_children()), 3)
        self.assertEqual(self.editor.message_tree.item("0", "values"), ("请求说明",))
        self.assertEqual(self.editor.message_tree.item("1", "values"), ("—",))
        self.assertEqual(len(self.editor.field_tree.get_children()), 3)
        self.assertIn("repeatedList byte Data", self.editor.preview.get("1.0", "end"))
        self.editor.search.set("IResponse")
        self.assertEqual(len(self.editor.message_tree.get_children()), 1)
        self.editor.search.set("请求说明")
        self.assertEqual(self.editor.message_tree.get_children(), ("0",))
        self.assertEqual(self.editor.message_tree.item("0", "text"), "C2G_TestRequest")

    def test_actual_default_file_and_window_layout(self):
        self.assertTrue(self.editor.load_path(DEFAULT_FILE))
        self.root.deiconify()
        self.root.update()
        self.assertGreater(self.editor.message_tree.winfo_width(), 200)
        self.assertGreater(self.editor.field_tree.winfo_width(), 350)
        self.assertGreater(self.editor.field_tree.winfo_height(), 150)
        self.assertEqual(len(self.editor.message_tree.get_children()), len(self.editor.document.messages))

    def test_workbench_sidebar_can_hide_and_search_restores_it(self):
        self.editor.toggle_sidebar()
        self.assertFalse(self.editor.sidebar_visible)
        self.assertEqual(len(self.editor.panes.panes()), 1)
        self.editor.focus_search()
        self.assertTrue(self.editor.sidebar_visible)
        self.assertEqual(len(self.editor.panes.panes()), 2)

    def test_preview_tabs_line_numbers_and_dirty_marker(self):
        self.editor.tabs.select(1)
        self.assertEqual(self.editor.tabs.selected, 1)
        self.assertEqual(len(self.editor.line_numbers.get("1.0", "end-1c").splitlines()),
                         len(self.editor.preview.get("1.0", "end-1c").splitlines()))
        self.editor.perform(lambda: self.editor.document.add_message("NewMessage"))
        self.assertEqual(self.editor.dirty_indicator.get(), "●")
        self.editor.undo()
        self.assertEqual(self.editor.dirty_indicator.get(), "")

    def test_add_rpc_and_undo_redo_via_ui_actions(self):
        with patch("protocol_editor.FormDialog.show", return_value={
            "request": "C2G_HelloRequest", "response": "G2C_HelloResponse", "doc": "问候"}):
            self.editor.add_rpc()
        self.assertEqual(self.editor.selected_name, "C2G_HelloRequest")
        self.assertEqual(len(self.editor.document.messages), 5)
        self.editor.undo()
        self.assertEqual(self.editor.document.text, FIXTURE)
        self.editor.redo()
        self.assertEqual(len(self.editor.document.messages), 5)

    def test_add_edit_delete_field_via_ui_actions(self):
        values = {"name": "Age", "type": "int32", "number": "9", "label": "", "doc": "年龄"}
        with patch("protocol_editor.FormDialog.show", return_value=values):
            self.editor.add_field()
        self.assertEqual(self.editor.document.get("C2G_TestRequest").fields[-1].name, "Age")
        self.editor.field_tree.selection_set("3")
        with patch("protocol_editor.FormDialog.show", return_value={**values, "name": "Level"}):
            self.editor.edit_field()
        self.assertEqual(self.editor.document.get("C2G_TestRequest").fields[-1].name, "Level")
        self.editor.field_tree.selection_set("3")
        with patch("protocol_editor.messagebox.askyesno", return_value=True):
            self.editor.delete_field()
        self.assertEqual(len(self.editor.document.get("C2G_TestRequest").fields), 3)

    def test_modal_validation_keeps_invalid_form_open(self):
        def validate(values):
            if values["name"] == "Bad":
                raise ProtocolError("无效名称")
        dialog = FormDialog(self.root, "Test", [("name", "Name", "Bad", None)], validate=validate)
        dialog.accept()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(dialog.error.get(), "无效名称")
        dialog.values["name"].set("Good")
        dialog.accept()
        self.assertEqual(dialog.result["name"], "Good")

    def test_unsaved_cancel_keeps_document(self):
        self.editor.document.add_message("Added")
        with patch("protocol_editor.messagebox.askyesnocancel", return_value=None):
            self.assertFalse(self.editor.confirm_discard())

    def test_message_dialog_action_updates_metadata_and_undo(self):
        with patch("protocol_editor.FormDialog.show", return_value={
            "name": "C2G_RenamedRequest", "kind": "IRequest", "response": "G2C_TestResponse",
            "route": "", "serialization": "MemoryPack", "doc": "新说明"}):
            self.editor.edit_message()
        self.assertEqual(self.editor.selected_name, "C2G_RenamedRequest")
        self.assertEqual(self.editor.current().description, "新说明")
        self.assertEqual(self.editor.message_tree.item("0", "values"), ("新说明",))
        self.editor.undo()
        self.assertEqual(self.editor.document.text, FIXTURE)
        self.assertEqual(self.editor.message_tree.item("0", "values"), ("请求说明",))

    def test_save_as_from_ui_writes_model(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Saved.proto"
            with patch("protocol_editor.filedialog.asksaveasfilename", return_value=str(target)), \
                 patch.object(self.editor, "persist_state"):
                self.assertTrue(self.editor.save(as_new=True, export=False))
            self.assertEqual(target.read_text(encoding="utf-8"), FIXTURE)
            self.assertFalse(self.editor.document.dirty)

    def test_save_finishes_before_auto_export_and_only_save_skips_it(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Saved.proto"
            self.editor.document.path = target
            def export():
                self.assertEqual(target.read_text(encoding="utf-8"), self.editor.document.text)
                self.assertFalse(self.editor.document.dirty)
                return True
            with patch.object(self.editor, "persist_state"), patch.object(self.editor, "start_export", side_effect=export) as start:
                self.assertTrue(self.editor.save())
                start.assert_called_once()
                start.reset_mock()
                self.assertTrue(self.editor.save(export=False))
                start.assert_not_called()
                self.editor.auto_export.set(False)
                self.assertTrue(self.editor.save())
                start.assert_not_called()
                self.assertTrue(self.editor.save(export=True))
                start.assert_called_once()

    def test_cancel_or_failed_save_does_not_export(self):
        with patch.object(self.editor, "start_export") as start, \
             patch("protocol_editor.filedialog.asksaveasfilename", return_value=""):
            self.assertFalse(self.editor.save())
            self.editor.document.path = DEFAULT_FILE
            with patch.object(self.editor.document, "save", side_effect=ProtocolError("保存冲突")), \
                 patch("protocol_editor.messagebox.showerror"):
                self.assertFalse(self.editor.save())
            start.assert_not_called()

    def test_export_preparation_failure_stays_visible_when_closing(self):
        self.editor.document.add_message("Added")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Saved.proto"
            with patch("protocol_editor.filedialog.asksaveasfilename", return_value=str(target)), \
                 patch("protocol_editor.messagebox.askyesnocancel", return_value=True), \
                 patch("protocol_editor.prepare_export", side_effect=ExportError("找不到 dotnet")), \
                 patch.object(self.editor, "persist_state"):
                self.editor.close()
            self.assertTrue(self.root.winfo_exists())
            self.assertIn("找不到 dotnet", self.editor.export_output.get("1.0", "end"))
            self.assertTrue(target.exists())
            self.assertFalse(self.editor.document.dirty)

    def test_background_export_keeps_ui_responsive_and_prevents_duplicate_runs(self):
        self.editor.document.path = DEFAULT_FILE
        entered, release = threading.Event(), threading.Event()
        def worker(command):
            entered.set()
            release.wait(3)
            return ExportResult(7, "测试导出错误")
        with patch("protocol_editor.prepare_export", return_value=ExportCommand(("fake",), TOOL_DIR)), \
             patch("protocol_editor.run_export", side_effect=worker) as run, \
             patch.object(self.root, "after"):
            try:
                self.assertTrue(self.editor.start_export())
                self.assertTrue(entered.wait(2))
                self.root.update()
                self.assertFalse(self.editor.start_export())
                self.assertFalse(self.editor.save())
                self.editor.close()
                self.assertTrue(self.root.winfo_exists())
                self.assertTrue(self.editor.export_running)
            finally:
                release.set()
            result = self.editor.export_results.get(timeout=3)
            self.editor.export_results.put(result)
            self.editor.poll_export()
            self.assertFalse(self.editor.export_running)
            self.assertIn("导出失败", self.editor.status.get())
            self.assertIn("测试导出错误", self.editor.export_output.get("1.0", "end"))
            run.assert_called_once()
        self.editor.export_results.put(ExportResult(0, "生成完成"))
        self.editor.poll_export()
        self.assertIn("导出成功", self.editor.status.get())

    def test_close_after_saving_waits_for_successful_export(self):
        self.editor.document.add_message("Added")
        def start():
            self.editor.export_running = True
            return True
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Saved.proto"
            with patch("protocol_editor.filedialog.asksaveasfilename", return_value=str(target)), \
                 patch("protocol_editor.messagebox.askyesnocancel", return_value=True), \
                 patch.object(self.editor, "persist_state"), \
                 patch.object(self.editor, "start_export", side_effect=start), \
                 patch.object(self.root, "destroy") as destroy:
                self.editor.close()
                destroy.assert_not_called()
                self.assertTrue(self.editor._close_after_export)
                self.editor.export_results.put(ExportResult(1, "失败"))
                self.editor.poll_export()
                destroy.assert_not_called()
                self.assertFalse(self.editor._close_after_export)
                self.editor._close_after_export = True
                self.editor.export_results.put(ExportResult(0, "成功"))
                self.editor.poll_export()
                destroy.assert_called_once()


class ExportRunnerTests(unittest.TestCase):
    def test_preparation_uses_existing_project_settings_and_rejects_other_files(self):
        with patch("export_runner.shutil.which", return_value="dotnet"):
            command = prepare_export(DEFAULT_FILE, PROJECT_ROOT)
            self.assertEqual(command.cwd, PROJECT_ROOT / "examples/Tools/ProtocolExportTool")
            self.assertEqual(command.args[2:5], ("export", "--silent", "--config"))
            with self.assertRaises(ExportError):
                prepare_export(TOOL_DIR / "Other.proto", PROJECT_ROOT)
        with patch("export_runner.shutil.which", return_value=None), self.assertRaisesRegex(ExportError, "dotnet"):
            prepare_export(DEFAULT_FILE, PROJECT_ROOT)
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ExportError, "找不到协议导出工具"):
            prepare_export(DEFAULT_FILE, Path(directory))

    def test_runner_reports_timeout_and_start_failure(self):
        command = ExportCommand(("dotnet", "fake.dll"), TOOL_DIR)
        with patch("export_runner.subprocess.run", side_effect=subprocess.TimeoutExpired(command.args, 1, output=b"partial")):
            result = run_export(command, timeout=1)
            self.assertFalse(result.success)
            self.assertIn("partial", result.output)
            self.assertIn("超时", result.output)
        with patch("export_runner.subprocess.run", side_effect=OSError("cannot start")):
            self.assertIn("无法启动", run_export(command).output)


class ExporterIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "dotnet is not installed")
    def test_export_edited_copy_with_existing_tool(self):
        original = DEFAULT_FILE.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protocol = root / "Protocol"
            shutil.copytree(DEFAULT_FILE.parent.parent, protocol)
            doc = ProtocolDocument.load(protocol / "Outer/OuterMessage.proto")
            doc.add_rpc("C2G_EditorSmokeRequest", "G2C_EditorSmokeResponse", "编辑器测试")
            doc.edit_field("C2G_EditorSmokeRequest", None, name="Name", type_name="string", number=1)
            doc.edit_field("G2C_EditorSmokeResponse", None, name="Greeting", type_name="string", number=1)
            doc.save(backup_dir=root / "backups")
            config = {"Export": {key: {"Value": str(value)} for key, value in {
                "NetworkProtocolDirectory": protocol, "NetworkProtocolServerDirectory": root / "Server",
                "NetworkProtocolClientDirectory": root / "Client", "SharedOpCodeCacheFile": protocol / "OpCode.Cache",
            }.items()}}
            import json
            config_path = root / "ExporterSettings.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            command = prepare_export(doc.path, PROJECT_ROOT, settings_path=config_path)
            result = run_export(command, timeout=45)
            self.assertEqual(result.returncode, 0, result.output)
            server = (root / "Server/OuterMessage.cs").read_text(encoding="utf-8-sig")
            helper = (root / "Client/NetworkProtocolHelper.cs").read_text(encoding="utf-8-sig")
            self.assertIn("class C2G_EditorSmokeRequest", server)
            self.assertIn("class G2C_EditorSmokeResponse", server)
            self.assertIn("public string Greeting", server)
            self.assertIn("C2G_EditorSmokeRequest(this Session", helper)
        self.assertEqual(DEFAULT_FILE.read_bytes(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
