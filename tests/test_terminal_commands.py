from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "apps" / "xenon_terminal.xe"


class XenonTerminalCommandTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls) -> None:
		cls.source = SOURCE_PATH.read_text(encoding="utf-8")

	def run_probe(self, probe: str, *, filesystem_root: str | None = None) -> str:
		anchor = "animation_step = 8\n\nwhile (terminal_window.state != graphics::WINDOW_CLOSED)"
		self.assertIn(anchor, self.source)
		source = self.source.replace(anchor, f"animation_step = 8\n{probe}\nwhile (false)", 1)
		parts: list[str] = []
		context = RuntimeContext(filesystem_root=filesystem_root)
		context.output_handler = parts.append
		with redirect_stdout(StringIO()):
			_, error, _ = run("terminal-command-probe.xe", source, context)
		self.assertIsNone(error, str(error))
		return "".join(parts)

	def test_source_compiles_and_exposes_scroll_contract(self) -> None:
		artifact = compile_source(self.source, str(SOURCE_PATH))
		self.assertTrue(artifact.success, "\n".join(map(str, artifact.diagnostics)))
		for marker in (
			"tab_scroll_rows",
			"tab_follow_tail",
			"tab_new_output",
			"tab_horizontal_scroll",
			"graphics::scroll_delta()",
			"call terminal_follow_latest()",
		):
			self.assertIn(marker, self.source)

	def test_quoted_tokenizer_preserves_spaces_and_escapes(self) -> None:
		output = self.run_probe(
			'''terminal_command = "echo \\"hello two\\" \\"quote\\\\\\\"ok\\""
call terminal_parse_command()
out << terminal_arg_0
out << "|"
out << terminal_arg_1
out << "|"
out << terminal_arg_2
out << "|"
out << terminal_arg_count'''
		)
		self.assertEqual("echo|hello two|quote\"ok|3", output)

	def test_only_clear_discards_previous_output(self) -> None:
		output = self.run_probe(
			'''terminal_command = "echo first"
call execute_terminal_command()
terminal_command = "about"
call execute_terminal_command()
out << tab_1_output
out << "|CLEAR|"
terminal_command = "clear"
call execute_terminal_command()
terminal_command = "echo last"
call execute_terminal_command()
out << tab_1_output'''
		)
		before, after = output.split("|CLEAR|", 1)
		self.assertIn("> echo first\nfirst", before)
		self.assertIn("> about\nPlaceholder", before)
		self.assertEqual("> echo last\nlast", after)

	def test_virtual_file_commands_are_sandboxed_and_quoted(self) -> None:
		with tempfile.TemporaryDirectory() as drive:
			output = self.run_probe(
				'''terminal_command = "mkdir \\"My Folder\\""
call execute_terminal_command()
terminal_command = "write \\"My Folder/notes one.txt\\" \\"hello world\\""
call execute_terminal_command()
terminal_command = "append \\"My Folder/notes one.txt\\" \\"!\\""
call execute_terminal_command()
terminal_command = "cat \\"My Folder/notes one.txt\\""
call execute_terminal_command()
out << tab_1_output''',
				filesystem_root=drive,
			)
			self.assertIn("Directory created", output)
			self.assertIn("Written", output)
			self.assertIn("Appended", output)
			self.assertIn("hello world!", output)
			self.assertEqual("hello world!", Path(drive, "My Folder", "notes one.txt").read_text(encoding="utf-8"))

	def test_planned_deterministic_command_set_is_present(self) -> None:
		for command in (
			"help", "clear", "echo", "date", "time", "uptime", "pwd", "ls", "dir", "cd",
			"tree", "cat", "type", "head", "tail", "touch", "mkdir", "rm", "del", "ren",
			"mv", "cp", "write", "append", "find", "wc", "history", "saved", "save",
			"xe-check", "xe-run", "about", "version", "whoami", "split", "monitor", "text",
			"theme", "exit",
		):
			self.assertIn(f'terminal_arg_0 == "{command}"', self.source)
		self.assertNotIn("Deterministic Xe shell", self.source)
		self.assertNotIn("palette", self.source.lower())

	def test_legacy_terminal_about_is_also_placeholder(self) -> None:
		legacy_source = (ROOT / "apps" / "terminal.xe").read_text(encoding="utf-8")
		self.assertNotIn("deterministic Xe shell", legacy_source)
		self.assertIn('set_active_output("Placeholder")', legacy_source)

	def test_legacy_terminal_ctrl_clipboard_has_host_and_local_fallback(self) -> None:
		legacy_source = (ROOT / "apps" / "terminal.xe").read_text(encoding="utf-8")
		anchor = "terminal_animation_step = 8\n\nwhile (terminal_window.state != graphics::WINDOW_CLOSED)"
		self.assertIn(anchor, legacy_source)
		probe = '''call set_terminal_text(terminal_command, "copy me")
call terminal_select_all()
call terminal_copy_selection()
call terminal_cut_selection()
out << terminal_command
out << "|"
call terminal_paste_clipboard()
out << terminal_command
out << "|"
call terminal_select_all()
call append_terminal_character(33)
out << terminal_command'''
		modified = legacy_source.replace(anchor, f"terminal_animation_step = 8\n{probe}\nwhile (false)", 1)
		parts: list[str] = []
		context = RuntimeContext()
		context.output_handler = parts.append
		with redirect_stdout(StringIO()):
			_, error, _ = run("legacy-terminal-clipboard.xe", modified, context)
		self.assertIsNone(error, str(error))
		self.assertEqual("|copy me|!", "".join(parts))
		self.assertIn("graphics::MOD_CTRL", legacy_source)
		self.assertIn("os::clipboard_read()", legacy_source)
		self.assertIn("os::clipboard_write(terminal_clipboard)", legacy_source)

	def test_scrolled_tab_tracks_new_output_until_following_latest(self) -> None:
		output = self.run_probe(
			'''call terminal_scroll_output(5)
terminal_command = "echo arrived"
call execute_terminal_command()
out << tab_follow_tail[0]
out << ","
out << tab_new_output[0]
out << ","
out << tab_scroll_rows[0]
out << "|"
call terminal_follow_latest()
out << tab_follow_tail[0]
out << ","
out << tab_new_output[0]
out << ","
out << tab_scroll_rows[0]'''
		)
		self.assertEqual("0,1,5|1,0,0", output)

	def test_copy_move_find_and_delete_commands_use_virtual_drive(self) -> None:
		with tempfile.TemporaryDirectory() as drive:
			output = self.run_probe(
				'''terminal_command = "mkdir work"
call execute_terminal_command()
terminal_command = "write work/source.txt data"
call execute_terminal_command()
terminal_command = "cp work/source.txt work/copy.txt"
call execute_terminal_command()
terminal_command = "mv work/copy.txt work/moved.txt"
call execute_terminal_command()
terminal_command = "find moved work"
call execute_terminal_command()
terminal_command = "rm work/moved.txt"
call execute_terminal_command()
out << tab_1_output''',
				filesystem_root=drive,
			)
			self.assertIn("Copied", output)
			self.assertIn("Moved", output)
			self.assertIn("work/moved.txt", output)
			self.assertIn("Moved to virtual trash", output)
			self.assertFalse(Path(drive, "work", "moved.txt").exists())


if __name__ == "__main__":
	unittest.main()
