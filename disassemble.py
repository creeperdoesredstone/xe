from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Iterable

from xe_lang.assembler import INSTRUCTION_MAP, MAGIC, VERSION


XBN_WORD_RE = re.compile(r"^(?:0[xX])?[0-9a-fA-F]+$")
MAX_INSTRUCTION_WORD = 0xFFFFFFFFF
MAX_DATA_WORD = 0xFFFFFFFF


class DisassemblyError(ValueError):
	"""Raised when an input file is not a well-formed XBN executable."""


@dataclass(frozen=True)
class ExecutableImage:
	magic: int
	version: int
	text: tuple[int, ...]
	data: tuple[int, ...]

	@property
	def text_size(self) -> int:
		return len(self.text)

	@property
	def data_size(self) -> int:
		return len(self.data)


REVERSE_MAP = {}

for mnemonic, (opcode, default_mod, default_arg, argc, wide) in INSTRUCTION_MAP.items():
	REVERSE_MAP[(opcode, default_mod, default_arg)] = (
		mnemonic,
		argc,
		wide,
	)


def decode_instruction(value: int) -> str:
	opcode = (value >> 32) & 0xF
	modifier = (value >> 16) & 0xFFFF
	arg = value & 0xFFFF

	# try exact matches (all non-wide instructions)
	key = (opcode, modifier, arg)

	if key in REVERSE_MAP:
		mnemonic, argc, _ = REVERSE_MAP[key]
		if argc == 0:
			return mnemonic

	# try instructions with arguments
	for mnemonic, (op, default_mod, _default_arg, argc, wide) in INSTRUCTION_MAP.items():
		if op != opcode:
			continue

		if argc == 1:
			if wide:
				value32 = (modifier << 16) | arg
				return f"{mnemonic} {value32}"
			if modifier == default_mod:
				return f"{mnemonic} {arg}"

		elif argc == 2:
			return f"{mnemonic} {modifier} {arg}"

	return f".word 0x{value:09X}"


def parse_executable(words: Iterable[int]) -> ExecutableImage:
	program = tuple(words)
	if len(program) < 4:
		raise DisassemblyError(
			f"Executable header is truncated: expected 4 words, found {len(program)}"
		)

	for index, word in enumerate(program):
		if not isinstance(word, int):
			raise DisassemblyError(f"Word {index} is not an integer")
		if not 0 <= word <= MAX_INSTRUCTION_WORD:
			raise DisassemblyError(
				f"Word {index} exceeds the 36-bit XBN width: {word!r}"
			)

	magic, version, text_size, data_size = program[:4]
	if magic != MAGIC:
		raise DisassemblyError(
			f"Invalid executable magic 0x{magic:08X}; expected 0x{MAGIC:08X}"
		)
	if version != VERSION:
		raise DisassemblyError(
			f"Unsupported executable version {version}; expected {VERSION}"
		)
	if text_size > MAX_DATA_WORD or data_size > MAX_DATA_WORD:
		raise DisassemblyError("Executable section count exceeds the 32-bit XBN limit")

	expected_words = 4 + text_size + data_size
	actual_words = len(program)
	section_summary = (
		f"header declares {text_size} text and {data_size} data words "
		f"({expected_words} total)"
	)
	if actual_words < expected_words:
		raise DisassemblyError(
			f"Truncated executable: {section_summary}, found {actual_words}"
		)
	if actual_words > expected_words:
		raise DisassemblyError(
			f"Executable has {actual_words - expected_words} extra word(s): "
			f"{section_summary}, found {actual_words}"
		)

	text_end = 4 + text_size
	text = program[4:text_end]
	data = program[text_end:]
	for index, word in enumerate(data):
		if word > MAX_DATA_WORD:
			raise DisassemblyError(
				f"Data word {index} exceeds the 32-bit data width: 0x{word:09X}"
			)

	return ExecutableImage(magic, version, text, data)


def read_executable(path: Path | str) -> ExecutableImage:
	path_obj = Path(path)
	try:
		lines = path_obj.read_text(encoding="utf-8").splitlines()
	except (OSError, UnicodeError) as error:
		raise DisassemblyError(f"Could not read executable '{path_obj}': {error}") from error

	words: list[int] = []
	for line_number, original in enumerate(lines, 1):
		token = original.strip()
		if not token:
			continue
		if not XBN_WORD_RE.fullmatch(token):
			raise DisassemblyError(
				f"Line {line_number}: invalid hexadecimal word {token!r}"
			)
		word = int(token, 16)
		if word > MAX_INSTRUCTION_WORD:
			raise DisassemblyError(
				f"Line {line_number}: word exceeds the 36-bit XBN width: {token!r}"
			)
		words.append(word)

	return parse_executable(words)


def format_disassembly(executable: ExecutableImage) -> str:
	lines = [
		"[header]",
		f"magic: 0x{executable.magic:08X}",
		f"version: {executable.version}",
		f"text_size: {executable.text_size}",
		f"data_size: {executable.data_size}",
		"[text]",
	]
	lines.extend(
		f"{address:04X}: {decode_instruction(word)}"
		for address, word in enumerate(executable.text)
	)
	lines.append("[data]")
	lines.extend(
		f"{address:04X}: 0x{word:08X}"
		for address, word in enumerate(executable.data)
	)
	return "\n".join(lines)


def disassemble(path: Path | str) -> str:
	rendered = format_disassembly(read_executable(path))
	print(rendered)
	return rendered


def main(argv: list[str] | None = None) -> int:
	args = sys.argv[1:] if argv is None else argv
	if len(args) != 1:
		print("usage:", file=sys.stderr)
		print("    python disassemble.py program.xbn", file=sys.stderr)
		return 2

	try:
		disassemble(Path(args[0]))
	except DisassemblyError as error:
		print(f"error: {error}", file=sys.stderr)
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
