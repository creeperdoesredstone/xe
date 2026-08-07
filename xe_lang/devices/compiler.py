from __future__ import annotations

from dataclasses import dataclass
import re

from xe_lang.assembler import assemble
from xe_lang.codegen import compile_ast, format_instructions
from xe_lang.ir_optimize import DEFAULT_PASSES, optimize
from xe_lang.lexer import lex
from xe_lang.optimizer import Optimizer
from xe_lang.parser import parse
from xe_lang.semantic import SemanticAnalyzer


@dataclass(frozen=True)
class CompileSnapshot:
	success: bool
	error: str = ""
	line: int = 0
	column: int = 0
	assembly: str = ""
	bytecode_size: int = 0


@dataclass
class VisualAtom:
	line: int
	kind: int
	text: str
	enabled: bool = True


@dataclass(frozen=True)
class VisualScript:
	"""A top-level Xe script represented as one electron in visual mode."""

	name: str
	line: int
	end_line: int
	shell: int
	enabled: bool = True


@dataclass(frozen=True)
class VisualDocument:
	name: str
	source: str
	scripts: tuple[VisualScript, ...]


ATOM_EVENT = 0
ATOM_INSTRUCTION = 1
ATOM_VALUE = 2
ATOM_CONDITION = 3
ATOM_DECLARATION = 4


class CompilerDevice:
	"""In-process compiler service shared by graphical Xe authoring tools."""

	def __init__(self) -> None:
		self.snapshot = CompileSnapshot(False)
		self.bytecode: tuple[int, ...] = ()
		self.visual_source = ""
		self.atoms: list[VisualAtom] = []
		self.scripts: list[VisualScript] = []
		self.documents: list[VisualDocument] = [VisualDocument("", "", ()) for _ in range(16)]

	def compile(self, source: str, filename: str = "workspace.xe") -> bool:
		self.bytecode = ()
		try:
			return self._compile(source, filename)
		except Exception as error:
			self.snapshot = CompileSnapshot(
				False,
				f"Compiler error: {error}",
				1,
				1,
			)
			return False

	def _compile(self, source: str, filename: str) -> bool:
		tokens, error = lex(filename, source)
		if error is not None:
			self._set_error(error)
			return False
		ast = parse(tokens)
		if ast.error is not None:
			self._set_error(ast.error)
			return False
		semantic = SemanticAnalyzer().analyze(ast.value)
		if semantic.error is not None:
			self._set_error(semantic.error)
			return False
		optimized_ast = Optimizer().optimize(ast.value)
		assembly = compile_ast(optimized_ast, filename)
		if assembly.error is not None:
			self._set_error(assembly.error)
			return False
		formatted = format_instructions(optimize(assembly.value, DEFAULT_PASSES))
		bytecode = assemble(filename, formatted, emit_file=False)
		if bytecode.error is not None:
			self._set_error(bytecode.error)
			return False
		self.bytecode = tuple(bytecode.value)
		self.snapshot = CompileSnapshot(True, assembly=formatted, bytecode_size=len(self.bytecode))
		return True

	def set_runtime_error(self, message: str) -> None:
		self.snapshot = CompileSnapshot(
			False,
			message,
			1,
			1,
			self.snapshot.assembly,
			self.snapshot.bytecode_size,
		)

	def _set_error(self, error) -> None:
		start = getattr(error, "start_pos", None)
		self.snapshot = CompileSnapshot(
			False,
			getattr(error, "desc", str(error)),
			getattr(start, "ln", -1) + 1,
			getattr(start, "col", -1) + 1,
		)

	def load_visual(self, source: str) -> int:
		self.visual_source = source
		self.atoms = []
		for line_number, original in enumerate(source.splitlines(), 1):
			text = original.strip()
			if not text:
				continue
			enabled = not text.startswith("# disabled:")
			if not enabled:
				text = text[len("# disabled:"):].lstrip()
			self.atoms.append(VisualAtom(line_number, self._atom_kind(text), text, enabled))
		self.scripts = self._build_script_graph(source)
		return len(self.atoms)

	def atom_count(self) -> int:
		return len(self.atoms)

	def atom_text(self, index: int) -> str:
		return self.atoms[index].text if 0 <= index < len(self.atoms) else ""

	def atom_kind(self, index: int) -> int:
		return self.atoms[index].kind if 0 <= index < len(self.atoms) else ATOM_INSTRUCTION

	def atom_line(self, index: int) -> int:
		return self.atoms[index].line if 0 <= index < len(self.atoms) else 0

	def atom_enabled(self, index: int) -> bool:
		return self.atoms[index].enabled if 0 <= index < len(self.atoms) else False

	def set_atom_enabled(self, index: int, enabled: bool) -> bool:
		if not 0 <= index < len(self.atoms):
			return False
		self.atoms[index].enabled = bool(enabled)
		lines = self.visual_source.splitlines()
		line_index = self.atoms[index].line - 1
		if not 0 <= line_index < len(lines):
			return False
		content = self.atoms[index].text
		indent = lines[line_index][:-len(lines[line_index].lstrip())]
		lines[line_index] = indent + (content if enabled else "# disabled: " + content)
		self.visual_source = "\n".join(lines)
		self.scripts = self._build_script_graph(self.visual_source)
		return True

	def script_count(self) -> int:
		return len(self.scripts)

	def script_name(self, index: int) -> str:
		return self.scripts[index].name if 0 <= index < len(self.scripts) else ""

	def script_shell(self, index: int) -> int:
		return self.scripts[index].shell if 0 <= index < len(self.scripts) else -1

	def script_line(self, index: int) -> int:
		return self.scripts[index].line if 0 <= index < len(self.scripts) else 0

	def script_enabled(self, index: int) -> bool:
		return self.scripts[index].enabled if 0 <= index < len(self.scripts) else False

	def load_document(self, slot: int, name: str, source: str) -> int:
		if not 0 <= slot < len(self.documents):
			return 0
		scripts = tuple(self._build_script_graph(source))
		self.documents[slot] = VisualDocument(name, source, scripts)
		return len(scripts)

	def document_script_count(self, slot: int) -> int:
		if not 0 <= slot < len(self.documents):
			return 0
		return len(self.documents[slot].scripts)

	def document_script_name(self, slot: int, index: int) -> str:
		if not 0 <= slot < len(self.documents):
			return ""
		scripts = self.documents[slot].scripts
		return scripts[index].name if 0 <= index < len(scripts) else ""

	def document_script_shell(self, slot: int, index: int) -> int:
		if not 0 <= slot < len(self.documents):
			return -1
		scripts = self.documents[slot].scripts
		return scripts[index].shell if 0 <= index < len(scripts) else -1

	def document_script_line(self, slot: int, index: int) -> int:
		if not 0 <= slot < len(self.documents):
			return 0
		scripts = self.documents[slot].scripts
		return scripts[index].line if 0 <= index < len(scripts) else 0

	def document_script_enabled(self, slot: int, index: int) -> bool:
		if not 0 <= slot < len(self.documents):
			return False
		scripts = self.documents[slot].scripts
		return scripts[index].enabled if 0 <= index < len(scripts) else False

	def document_source(self, slot: int) -> str:
		if not 0 <= slot < len(self.documents):
			return ""
		return self.documents[slot].source

	@staticmethod
	def _code_text(line: str) -> tuple[str, bool]:
		text = line.strip()
		enabled = not text.startswith("# disabled:")
		if not enabled:
			text = text[len("# disabled:"):].lstrip()
		result: list[str] = []
		in_string = False
		quote = ""
		escaped = False
		for char in text:
			if in_string:
				if escaped:
					escaped = False
				elif char == "\\":
					escaped = True
				elif char == quote:
					in_string = False
				result.append(" ")
				continue
			if char in {'"', "'"}:
				in_string = True
				quote = char
				result.append(" ")
			elif char == "#":
				break
			else:
				result.append(char)
		return "".join(result).strip(), enabled

	@classmethod
	def _build_script_graph(cls, source: str) -> list[VisualScript]:
		"""Group top-level scripts by call connectivity without changing Xe syntax."""

		lines = source.splitlines()
		declaration = re.compile(r"^(fn|proc|class|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)")
		sections: list[dict[str, object]] = []
		main_lines: list[tuple[int, str, bool]] = []
		current: dict[str, object] | None = None
		depth = 0
		saw_open = False

		for line_number, original in enumerate(lines, 1):
			code, enabled = cls._code_text(original)
			if current is None:
				match = declaration.match(code)
				if match:
					current = {
						"name": match.group(2),
						"line": line_number,
						"end_line": line_number,
						"lines": [(line_number, code, enabled)],
						"enabled": enabled,
					}
					depth = 0
					saw_open = False
				else:
					if code:
						main_lines.append((line_number, code, enabled))
					continue
			else:
				current["lines"].append((line_number, code, enabled))
				current["end_line"] = line_number

			opens = code.count("{")
			closes = code.count("}")
			if opens:
				saw_open = True
			depth += opens - closes
			if current is not None and saw_open and depth <= 0:
				sections.append(current)
				current = None
				depth = 0
				saw_open = False

		if current is not None:
			sections.append(current)
		if main_lines:
			sections.insert(0, {
				"name": "main",
				"line": main_lines[0][0],
				"end_line": main_lines[-1][0],
				"lines": main_lines,
				"enabled": any(item[2] for item in main_lines),
			})
		if not sections:
			return []

		names = [str(section["name"]) for section in sections]
		parents = list(range(len(sections)))

		def find(index: int) -> int:
			while parents[index] != index:
				parents[index] = parents[parents[index]]
				index = parents[index]
			return index

		def union(left: int, right: int) -> None:
			left_root = find(left)
			right_root = find(right)
			if left_root != right_root:
				parents[right_root] = left_root

		for source_index, section in enumerate(sections):
			body = "\n".join(item[1] for item in section["lines"])
			for target_index, name in enumerate(names):
				if source_index == target_index:
					continue
				if re.search(rf"\b(?:call\s+)?{re.escape(name)}\s*\(", body):
					union(source_index, target_index)

		shell_by_root: dict[int, int] = {}
		scripts: list[VisualScript] = []
		for index, section in enumerate(sections):
			root = find(index)
			if root not in shell_by_root:
				shell_by_root[root] = len(shell_by_root)
			scripts.append(VisualScript(
				str(section["name"]),
				int(section["line"]),
				int(section["end_line"]),
				shell_by_root[root],
				bool(section["enabled"]),
			))
		return scripts

	@staticmethod
	def _atom_kind(text: str) -> int:
		word = text.split(maxsplit=1)[0] if text else ""
		if word in {"fn", "proc", "class", "struct", "enum"}:
			return ATOM_EVENT
		if word in {"if", "elseif", "else", "while", "repeat", "switch", "case"}:
			return ATOM_CONDITION
		if word in {"var", "array", "const"}:
			return ATOM_DECLARATION
		if text[0:1] in {'"', "'"} or word in {"true", "false"} or word[:1].isdigit():
			return ATOM_VALUE
		return ATOM_INSTRUCTION
