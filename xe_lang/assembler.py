from __future__ import annotations

import re
from pathlib import Path

from xe_lang.helper import Result, AssemblyError, Position

Instruction = int

HEX_INT_RE = re.compile(r"^[+-]?0[xX][0-9a-fA-F]+$")
DEC_INT_RE = re.compile(r"^[+-]?\d+$")

MAGIC = 0x58424E31  # "XBN1"
VERSION = 1

# opcode -> (op_code, default_modifier, default_arg, arg_count, wide_single_arg)
INSTRUCTION_MAP = {
	"PUSH": (0x0, 0x0000, 0x0000, 1, True),
	"LOAD": (0x1, 0x0000, 0x0000, 1, False),
	"STORE": (0x1, 0x0001, 0x0000, 1, False),
	"POP": (0x1, 0x0002, 0x0000, 0, False),
	"DUP": (0x1, 0x0003, 0x0000, 0, False),
	"SWAP": (0x1, 0x0004, 0x0000, 0, False),
	"OVER": (0x1, 0x0005, 0x0000, 0, False),
	"ROT": (0x1, 0x0006, 0x0000, 0, False),
	"LOADIND": (0x1, 0x0007, 0x0000, 0, False),
	"STREIND": (0x1, 0x0008, 0x0000, 0, False),
	"PUSHFP": (0x1, 0x0009, 0x0000, 0, False),
	"POPFP": (0x1, 0x000A, 0x0000, 0, False),
	"SETFP": (0x1, 0x000B, 0x0000, 0, False),
	"LOADFP": (0x1, 0x000C, 0x0000, 1, False),
	"STOREFP": (0x1, 0x000D, 0x0000, 1, False),
	"I2F": (0x2, 0x0000, 0x0001, 0, False),
	"F2I": (0x2, 0x0001, 0x0000, 0, False),
	"I2B": (0x2, 0x0000, 0x0002, 0, False),
	"F2B": (0x2, 0x0001, 0x0002, 0, False),
	"B2F": (0x2, 0x0002, 0x0001, 0, False),
	"ADDI": (0x3, 0x0000, 0x0000, 0, False),
	"SUBI": (0x3, 0x0000, 0x0001, 0, False),
	"MULI": (0x3, 0x0000, 0x0002, 0, False),
	"DIVI": (0x3, 0x0000, 0x0003, 0, False),
	"MODI": (0x3, 0x0000, 0x0004, 0, False),
	"POWI": (0x3, 0x0000, 0x0005, 0, False),
	"AND": (0x3, 0x0000, 0x0006, 0, False),
	"OR": (0x3, 0x0000, 0x0007, 0, False),
	"XOR": (0x3, 0x0000, 0x0008, 0, False),
	"ILT": (0x3, 0x0000, 0x0011, 0, False),
	"IEQ": (0x3, 0x0000, 0x0012, 0, False),
	"ILE": (0x3, 0x0000, 0x0013, 0, False),
	"IGT": (0x3, 0x0000, 0x0014, 0, False),
	"INE": (0x3, 0x0000, 0x0015, 0, False),
	"IGE": (0x3, 0x0000, 0x0016, 0, False),
	"ICR": (0x3, 0x0000, 0x0017, 0, False),
	"ADDF": (0x3, 0x0001, 0x0000, 0, False),
	"SUBF": (0x3, 0x0001, 0x0001, 0, False),
	"MULF": (0x3, 0x0001, 0x0002, 0, False),
	"DIVF": (0x3, 0x0001, 0x0003, 0, False),
	"MODF": (0x3, 0x0001, 0x0004, 0, False),
	"POWF": (0x3, 0x0001, 0x0005, 0, False),
	"LERPF": (0x3, 0x0001, 0x0006, 0, False),
	"FLT": (0x3, 0x0001, 0x0011, 0, False),
	"FEQ": (0x3, 0x0001, 0x0012, 0, False),
	"FLE": (0x3, 0x0001, 0x0013, 0, False),
	"FGT": (0x3, 0x0001, 0x0014, 0, False),
	"FNE": (0x3, 0x0001, 0x0015, 0, False),
	"FGE": (0x3, 0x0001, 0x0016, 0, False),
	"INCI": (0x3, 0x0002, 0x0000, 0, False),
	"DECI": (0x3, 0x0002, 0x0001, 0, False),
	"NEGI": (0x3, 0x0002, 0x0002, 0, False),
	"NOT": (0x3, 0x0002, 0x0003, 0, False),
	"SQRTI": (0x3, 0x0002, 0x000A, 0, False),
	"INCF": (0x3, 0x0003, 0x0000, 0, False),
	"DECF": (0x3, 0x0003, 0x0001, 0, False),
	"NEGF": (0x3, 0x0003, 0x0002, 0, False),
	"SINF": (0x3, 0x0003, 0x0004, 0, False),
	"COSF": (0x3, 0x0003, 0x0005, 0, False),
	"TANF": (0x3, 0x0003, 0x0006, 0, False),
	"ASINF": (0x3, 0x0003, 0x0007, 0, False),
	"ACOSF": (0x3, 0x0003, 0x0008, 0, False),
	"ATANF": (0x3, 0x0003, 0x0009, 0, False),
	"SQRTF": (0x3, 0x0003, 0x000A, 0, False),
	"JUMP": (0x4, 0x0000, 0x0000, 1, False),
	"BRZ": (0x4, 0x0001, 0x0000, 1, False),
	"BRNZ": (0x4, 0x0002, 0x0000, 1, False),
	"CALL": (0x4, 0x0003, 0x0000, 1, False),
	"CALZ": (0x4, 0x0004, 0x0000, 1, False),
	"CALN": (0x4, 0x0005, 0x0000, 1, False),
	"RET": (0x4, 0x0006, 0x0000, 1, False),
	"RETZ": (0x4, 0x0007, 0x0000, 1, False),
	"RETN": (0x4, 0x0008, 0x0000, 1, False),
	"HALT": (0x5, 0x0000, 0x0000, 0, False),
	"WAIT": (0x5, 0x0001, 0x0000, 0, False),
	"PUSHIM": (0x5, 0x0002, 0x0000, 0, False),
	"POPIM": (0x5, 0x0003, 0x0000, 0, False),
	"IRT": (0x5, 0x0004, 0x0000, 0, False),
	"IRTZ": (0x5, 0x0005, 0x0000, 0, False),
	"IRTN": (0x5, 0x0006, 0x0000, 0, False),
	"SYS": (0x5, 0x0007, 0x0000, 1, False),
	"INT": (0x6, 0x0000, 0x0000, 2, False),
	"SETIM": (0x7, 0x0000, 0x0000, 1, True),
	"LOOKUP": (0x8, 0x0000, 0x0000, 1, False),
	"WRITE": (0x8, 0x0001, 0x0000, 1, False),
	"MEMCPY": (0x8, 0x0002, 0x0000, 1, False),
	"MEMSET": (0x8, 0x0003, 0x0000, 1, False),
}


def _encode_instruction(opcode: str, args: list[int]) -> int:
	op, default_modifier, default_arg, arg_count, wide = INSTRUCTION_MAP[opcode]

	if len(args) != arg_count:
		raise ValueError(f"{opcode} expects {arg_count} argument(s), got {len(args)}")

	if arg_count == 0:
		modifier = default_modifier
		arg = default_arg & 0xFFFF
	elif arg_count == 1:
		arg_value = args[0]
		if wide:
			modifier = (arg_value >> 16) & 0xFFFF
			arg = arg_value & 0xFFFF
		else:
			modifier = default_modifier
			arg = arg_value & 0xFFFF
	else:
		modifier = args[0] & 0xFFFF
		arg = args[1] & 0xFFFF

	return ((op & 0xF) << 32) | ((modifier & 0xFFFF) << 16) | (arg & 0xFFFF)


def _parse_int(token: str) -> int:
	if HEX_INT_RE.match(token):
		return int(token, 16)
	if DEC_INT_RE.match(token):
		return int(token, 10)
	raise ValueError(f"Invalid integer literal: {token}")


def _parse_arg(token: str, labels: dict[str, int]) -> int:
	token = token.strip()

	if not token:
		raise ValueError("Empty argument")

	if token in labels:
		return labels[token]

	try:
		return _parse_int(token)
	except ValueError:
		raise ValueError(f"Undefined label '{token}'")


def _make_position(fn: str, ftxt: str, line: int, col: int) -> Position:
	return Position(0, line, col, fn, ftxt)


def assemble(fn: str, ftxt: str) -> Result:
	res = Result()
	lines = ftxt.splitlines()

	MAGIC = 0x58424E31  # "XBN1"
	VERSION = 1

	labels: dict[str, int] = {}

	# (start, end, opcode, raw_args)
	instruction_records: list[tuple[Position, Position, str, list[str]]] = []

	# (position, token)
	data_records: list[tuple[Position, str]] = []

	section = "text"

	# 1st pass
	address = 0

	for line_idx, original_line in enumerate(lines):
		stripped = original_line.split(";", 1)[0]

		if not stripped.strip():
			continue

		tokens = stripped.strip().split()
		if not tokens:
			continue

		token_index = 0

		#
		# labels / section markers
		#
		while token_index < len(tokens) and tokens[token_index].startswith(":"):
			label = tokens[token_index][1:]

			pos = _make_position(
				fn,
				ftxt,
				line_idx,
				original_line.find(tokens[token_index]),
			)

			if not label:
				return res.fail(
					AssemblyError(
						"Invalid label definition",
						pos,
						pos,
					)
				)

			if label in labels:
				return res.fail(
					AssemblyError(
						f"Duplicate label '{label}'",
						pos,
						pos,
					)
				)

			labels[label] = address

			if label.startswith("SECTION_TEXT"):
				section = "text"

			elif label.startswith("SECTION_DATA"):
				section = "data"

			token_index += 1

		if token_index >= len(tokens):
			continue

		#
		# TEXT SECTION
		#
		if section == "text":
			opcode = tokens[token_index].upper()

			if opcode not in INSTRUCTION_MAP:
				start = _make_position(
					fn,
					ftxt,
					line_idx,
					original_line.find(tokens[token_index]),
				)
				end = _make_position(
					fn,
					ftxt,
					line_idx,
					len(stripped),
				)

				return res.fail(
					AssemblyError(
						f"Unknown opcode '{opcode}'",
						start,
						end,
					)
				)

			_, _, _, argc, _ = INSTRUCTION_MAP[opcode]

			raw_args = tokens[token_index + 1 : token_index + 1 + argc]
			extra = tokens[token_index + 1 + argc :]

			if len(raw_args) < argc:
				start = _make_position(
					fn,
					ftxt,
					line_idx,
					original_line.find(tokens[token_index]),
				)
				end = _make_position(fn, ftxt, line_idx, len(stripped))

				return res.fail(
					AssemblyError(
						f"Missing argument(s) for {opcode}",
						start,
						end,
					)
				)

			if extra:
				start = _make_position(
					fn,
					ftxt,
					line_idx,
					original_line.find(extra[0]),
				)
				end = _make_position(fn, ftxt, line_idx, len(stripped))

				return res.fail(
					AssemblyError(
						f"Too many arguments for {opcode}",
						start,
						end,
					)
				)

			start = _make_position(
				fn,
				ftxt,
				line_idx,
				original_line.find(tokens[token_index]),
			)

			end = _make_position(
				fn,
				ftxt,
				line_idx,
				len(stripped),
			)

			instruction_records.append(
				(
					start,
					end,
					opcode,
					raw_args,
				)
			)

			address += 1

		#
		# DATA SECTION
		#
		else:
			if len(tokens[token_index:]) != 1:
				start = _make_position(
					fn,
					ftxt,
					line_idx,
					original_line.find(tokens[token_index]),
				)

				return res.fail(
					AssemblyError(
						"Expected one data value",
						start,
						start,
					)
				)

			pos = _make_position(
				fn,
				ftxt,
				line_idx,
				original_line.find(tokens[token_index]),
			)

			data_records.append(
				(
					pos,
					tokens[token_index],
				)
			)

			address += 1

	# 2nd pass
	instructions: list[int] = []

	for start, end, opcode, raw_args in instruction_records:
		try:
			args = [_parse_arg(arg, labels) for arg in raw_args]

			instructions.append(
				_encode_instruction(
					opcode,
					args,
				)
			)

		except ValueError as exc:
			return res.fail(
				AssemblyError(
					str(exc),
					start,
					end,
				)
			)

	data: list[int] = []

	for pos, token in data_records:
		try:
			data.append(_parse_int(token) & 0xFFFFFFFF)
		except ValueError as exc:
			return res.fail(
				AssemblyError(
					str(exc),
					pos,
					pos,
				)
			)

	#
	# Build final program
	#
	program = [
		MAGIC,
		VERSION,
		len(instructions),
		len(data),
	]

	program.extend(instructions)
	program.extend(data)

	#
	# Write .xbn
	#
	stem = Path(fn).stem

	if not stem.startswith("<"):
		outdir = Path("exe")
		outdir.mkdir(exist_ok=True)

		with open(
			outdir / f"{stem}.xbn",
			"w",
			encoding="utf-8",
		) as f:
			f.write("\n".join(f"0x{word:09X}" for word in program))

	print(labels)
	return res.success(program)


def assemble_file(path: Path | str) -> Result:
	path_obj = Path(path)
	res = Result()

	if not path_obj.exists():
		pos = Position(0, 0, 0, str(path_obj), "")
		return res.fail(AssemblyError(f"Assembly file not found: {path}", pos, pos))

	text = path_obj.read_text(encoding="utf-8")
	res.register(assemble(str(path_obj), text))
	return res
