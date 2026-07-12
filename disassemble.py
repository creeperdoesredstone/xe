from __future__ import annotations

import sys
from pathlib import Path

from xe_lang.assembler import INSTRUCTION_MAP


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
	for mnemonic, (op, default_mod, default_arg, argc, wide) in INSTRUCTION_MAP.items():
		if op != opcode:
			continue

		if argc == 1:
			if wide:
				value32 = (modifier << 16) | arg
				return f"{mnemonic} {value32}"
			elif modifier == default_mod:
				return f"{mnemonic} {arg}"

		elif argc == 2:
			return f"{mnemonic} {modifier} {arg}"

	return f".word 0x{value:09X}"


def disassemble(path: Path):
	with open(path, "r", encoding="utf8") as f:
		for address, line in enumerate(f):
			line = line.strip()

			if not line:
				continue

			value = int(line, 16)

			print(f"{address:04X}: {decode_instruction(value)}")


if __name__ == "__main__":
	if len(sys.argv) != 2:
		print("usage:")
		print("    python disassemble.py program.xbn")
		raise SystemExit(1)

	disassemble(Path(sys.argv[1]))
