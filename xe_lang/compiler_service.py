from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Iterable, Mapping

from xe_lang.assembler import assemble
from xe_lang.codegen import compile_ast, format_instructions
from xe_lang.executable import decode_static_layout
from xe_lang.ir_optimize import DEFAULT_PASSES, optimize
from xe_lang.lexer import lex
from xe_lang.nodes import (
	ArrayDeclaration,
	ConstantDeclaration,
	EnumDeclaration,
	LibraryCall,
	Node,
	Program,
	StringLiteral,
	VariableDeclaration,
)
from xe_lang.optimizer import Optimizer
from xe_lang.parser import parse
from xe_lang.semantic import SemanticAnalyzer
from xe_lang.syscall_abi import SyscallID


HEAP_START = 0x2000
MAX_ADDRESS_COUNT = 200_000


_DECLARATION_STATEMENTS = (
	ArrayDeclaration,
	ConstantDeclaration,
	EnumDeclaration,
	VariableDeclaration,
)
_SYS_RE = re.compile(r"^\s*SYS\s+(0[xX][0-9a-fA-F]+|\d+)\s*(?:;.*)?$")


@dataclass(frozen=True)
class SourceUnit:
	path: str
	source: str

	def normalized(self) -> "SourceUnit":
		return SourceUnit(normalize_workspace_path(self.path), self.source.replace("\r\n", "\n").replace("\r", "\n"))


@dataclass(frozen=True)
class Diagnostic:
	severity: str
	message: str
	path: str
	line: int
	column: int
	code: str = ""

	def __str__(self) -> str:
		return f"{self.path}:{self.line}:{self.column}: {self.message}"


@dataclass(frozen=True)
class MemorySummary:
	text_words: int = 0
	data_words: int = 0
	static_words: int = 0
	heap_start: int = HEAP_START
	address_limit: int = MAX_ADDRESS_COUNT


@dataclass(frozen=True)
class CompileArtifact:
	success: bool
	units: tuple[SourceUnit, ...]
	entry_path: str
	assembly: str = ""
	program: tuple[int, ...] = ()
	diagnostics: tuple[Diagnostic, ...] = ()
	required_syscalls: tuple[int, ...] = ()
	required_capabilities: tuple[str, ...] = ()
	assets: tuple[str, ...] = ()
	dynamic_assets: tuple[str, ...] = ()
	memory: MemorySummary = field(default_factory=MemorySummary)
	source_hash: str = ""
	artifact_hash: str = ""
	compiler_profile: str = "xe-xbn1"

	@property
	def bytecode_size(self) -> int:
		return len(self.program)

	def compatibility_manifest(self) -> dict[str, object]:
		return {
			"artifact_hash": self.artifact_hash,
			"assets": list(self.assets),
			"dynamic_assets": list(self.dynamic_assets),
			"compiler_profile": self.compiler_profile,
			"entry": self.entry_path,
			"memory": {
				"address_limit": self.memory.address_limit,
				"data_words": self.memory.data_words,
				"heap_start": self.memory.heap_start,
				"static_words": self.memory.static_words,
				"text_words": self.memory.text_words,
			},
			"required_capabilities": list(self.required_capabilities),
			"required_syscalls": list(self.required_syscalls),
			"source_hash": self.source_hash,
			"success": self.success,
		}


def normalize_workspace_path(path: str) -> str:
	text = str(path).replace("\\", "/").strip()
	if not text:
		raise ValueError("Workspace source path cannot be empty")
	if text.startswith("//") or re.match(r"^[A-Za-z]:/", text):
		raise ValueError(f"Workspace source path is not portable: {path!r}")
	value = PurePosixPath(text)
	if value.is_absolute() or ".." in value.parts:
		raise ValueError(f"Workspace source path is not portable: {path!r}")
	normalized = str(value)
	if normalized in {"", "."}:
		raise ValueError("Workspace source path cannot refer to the workspace root")
	return normalized


def _canonical_source_hash(units: Iterable[SourceUnit], entry_path: str) -> str:
	payload = {
		"entry": entry_path,
		"units": [{"path": item.path, "source": item.source} for item in units],
	}
	encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
	return hashlib.sha256(encoded).hexdigest()


def _artifact_hash(program: Iterable[int], source_hash: str) -> str:
	digest = hashlib.sha256()
	digest.update(source_hash.encode("ascii"))
	for word in program:
		digest.update((int(word) & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big"))
	return digest.hexdigest()


def _diagnostic(error: object, fallback_path: str, code: str = "XE0001") -> Diagnostic:
	position = getattr(error, "start_pos", None)
	path = normalize_workspace_path(getattr(position, "fn", fallback_path) or fallback_path)
	return Diagnostic(
		"error",
		str(getattr(error, "desc", error)),
		path,
		max(1, int(getattr(position, "ln", 0)) + 1),
		max(1, int(getattr(position, "col", 0)) + 1),
		code,
	)


def _parse_unit(unit: SourceUnit) -> tuple[Program | None, Diagnostic | None]:
	tokens, error = lex(unit.path, unit.source)
	if error is not None:
		return None, _diagnostic(error, unit.path, "XE1001")
	parsed = parse(tokens)
	if parsed.error is not None:
		return None, _diagnostic(parsed.error, unit.path, "XE1002")
	return parsed.value, None


def _required_syscalls(assembly: str) -> tuple[int, ...]:
	values: set[int] = set()
	for line in assembly.splitlines():
		match = _SYS_RE.match(line)
		if match:
			values.add(int(match.group(1), 0))
	return tuple(sorted(values))


def _walk_nodes(value: object, seen: set[int] | None = None) -> Iterable[Node]:
	seen = seen if seen is not None else set()
	if isinstance(value, Node):
		if id(value) in seen:
			return
		seen.add(id(value))
		yield value
		for child in vars(value).values():
			if isinstance(child, (Node, list, tuple, dict)):
				yield from _walk_nodes(child, seen)
	elif isinstance(value, dict):
		for child in value.values():
			yield from _walk_nodes(child, seen)
	elif isinstance(value, (list, tuple)):
		for child in value:
			yield from _walk_nodes(child, seen)


def _required_assets(program: Program) -> tuple[tuple[str, ...], tuple[str, ...]]:
	assets: set[str] = set()
	dynamic: set[str] = set()
	loaders = {("graphics", "load_image"), ("audio", "load")}
	for node in _walk_nodes(program):
		if not isinstance(node, LibraryCall) or (node.library_name, node.member_name) not in loaders:
			continue
		position = getattr(node, "start_pos", None)
		path = str(getattr(position, "fn", "workspace.xe") or "workspace.xe").replace("\\", "/")
		line = max(1, int(getattr(position, "ln", 0)) + 1)
		label = f"{node.library_name}::{node.member_name}@{path}:{line}"
		if len(node.arguments) != 1 or not isinstance(node.arguments[0], StringLiteral):
			dynamic.add(label)
			continue
		try:
			asset_path = normalize_workspace_path(node.arguments[0].value)
		except ValueError:
			dynamic.add(label)
			continue
		assets.add(asset_path)
	return tuple(sorted(assets, key=lambda value: (value.casefold(), value))), tuple(sorted(dynamic))


def capability_for_syscall(syscall: int) -> str:
	if 1 <= syscall <= 12:
		return "core.io"
	if 20 <= syscall <= 29:
		return "core.os"
	if 30 <= syscall <= 58:
		return "core.graphics"
	if 60 <= syscall <= 64:
		return "core.input"
	if syscall == 80:
		return "core.request"
	if (
		100 <= syscall <= 129
		or 142 <= syscall <= 146
		or syscall in {208, 209, 246, 247, 248, 249, 253, 254}
		or 270 <= syscall <= 276
	):
		return "app.graphics"
	if 130 <= syscall <= 141 or 180 <= syscall <= 196 or 250 <= syscall <= 252:
		return "app.os"
	if 150 <= syscall <= 152:
		return "app.window"
	if 160 <= syscall <= 164 or 210 <= syscall <= 217 or 260 <= syscall <= 266:
		return "app.filesystem"
	if 170 <= syscall <= 171:
		return "app.string"
	if 200 <= syscall <= 207:
		return "app.currency"
	if 220 <= syscall <= 245 or syscall == 255 or 290 <= syscall <= 291:
		return "app.compiler"
	if 280 <= syscall <= 289:
		return "app.audio"
	return "unknown"


def _finish(units: tuple[SourceUnit, ...], entry_path: str, program_ast: Program) -> CompileArtifact:
	source_hash = _canonical_source_hash(units, entry_path)
	assets, dynamic_assets = _required_assets(program_ast)
	semantic = SemanticAnalyzer().analyze(program_ast)
	if semantic.error is not None:
		return CompileArtifact(False, units, entry_path, diagnostics=(_diagnostic(semantic.error, entry_path, "XE1003"),), source_hash=source_hash)
	optimized_ast = Optimizer().optimize(program_ast)
	assembly = compile_ast(optimized_ast, entry_path)
	if assembly.error is not None:
		return CompileArtifact(False, units, entry_path, diagnostics=(_diagnostic(assembly.error, entry_path, "XE1004"),), source_hash=source_hash)
	formatted = format_instructions(optimize(assembly.value, DEFAULT_PASSES))
	bytecode = assemble(entry_path, formatted, emit_file=False)
	if bytecode.error is not None:
		return CompileArtifact(False, units, entry_path, assembly=formatted, diagnostics=(_diagnostic(bytecode.error, entry_path, "XE1005"),), source_hash=source_hash)
	program = tuple(int(word) for word in bytecode.value)
	text_words = program[2]
	data_words = program[3]
	_, static_words = decode_static_layout(list(program[4 + text_words:]))
	syscalls = _required_syscalls(formatted)
	capabilities = tuple(sorted({capability_for_syscall(value) for value in syscalls}))
	memory = MemorySummary(text_words, data_words, static_words, max(HEAP_START, static_words))
	return CompileArtifact(
		success=True,
		units=units,
		entry_path=entry_path,
		assembly=formatted,
		program=program,
		required_syscalls=syscalls,
		required_capabilities=capabilities,
		assets=assets,
		dynamic_assets=dynamic_assets,
		memory=memory,
		source_hash=source_hash,
		artifact_hash=_artifact_hash(program, source_hash),
	)


def compile_source(source: str, filename: str = "workspace.xe") -> CompileArtifact:
	try:
		unit = SourceUnit(filename, source).normalized()
	except ValueError:
		portable = str(filename).replace("\\", "/")
		logical_name = PurePosixPath(portable).name or "workspace.xe"
		unit = SourceUnit(logical_name, source.replace("\r\n", "\n").replace("\r", "\n"))
	program, diagnostic = _parse_unit(unit)
	if diagnostic is not None or program is None:
		return CompileArtifact(False, (unit,), unit.path, diagnostics=(diagnostic,) if diagnostic else ())
	return _finish((unit,), unit.path, program)


def compile_workspace(
	sources: Mapping[str, str] | Iterable[SourceUnit],
	entry_path: str = "workspace.xe",
) -> CompileArtifact:
	entry = normalize_workspace_path(entry_path)
	if isinstance(sources, Mapping):
		items = (SourceUnit(path, source) for path, source in sources.items())
	else:
		items = iter(sources)
	by_path: dict[str, SourceUnit] = {}
	for raw in items:
		unit = raw.normalized()
		if unit.path in by_path:
			raise ValueError(f"Duplicate workspace path: {unit.path}")
		by_path[unit.path] = unit
	units = tuple(by_path[path] for path in sorted(by_path, key=lambda value: (value.casefold(), value)))
	if entry not in by_path:
		diagnostic = Diagnostic("error", f"Workspace entry file not found: {entry}", entry, 1, 1, "XE2001")
		return CompileArtifact(False, units, entry, diagnostics=(diagnostic,), source_hash=_canonical_source_hash(units, entry))

	parsed: dict[str, Program] = {}
	diagnostics: list[Diagnostic] = []
	for unit in units:
		program, diagnostic = _parse_unit(unit)
		if diagnostic is not None:
			diagnostics.append(diagnostic)
		elif program is not None:
			parsed[unit.path] = program
	if diagnostics:
		return CompileArtifact(False, units, entry, diagnostics=tuple(diagnostics), source_hash=_canonical_source_hash(units, entry))

	statements = []
	sub_defs = []
	for unit in units:
		program = parsed[unit.path]
		if unit.path != entry:
			for statement in program.statements:
				if not isinstance(statement, _DECLARATION_STATEMENTS):
					diagnostics.append(Diagnostic(
						"error",
						"Library units may contain declarations only; move executable statements to workspace.xe",
						unit.path,
						statement.start_pos.ln + 1,
						statement.start_pos.col + 1,
						"XE2002",
					))
			statements.extend(statement for statement in program.statements if isinstance(statement, _DECLARATION_STATEMENTS))
		else:
			statements.extend(program.statements)
		sub_defs.extend(program.sub_defs)
	if diagnostics:
		return CompileArtifact(False, units, entry, diagnostics=tuple(diagnostics), source_hash=_canonical_source_hash(units, entry))

	entry_program = parsed[entry]
	combined = Program(entry_program.start_pos, entry_program.end_pos, statements, sub_defs)
	return _finish(units, entry, combined)


def syscall_name(value: int) -> str:
	try:
		return SyscallID(value).name
	except ValueError:
		return f"SYS_{value}"
