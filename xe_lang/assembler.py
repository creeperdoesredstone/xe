from __future__ import annotations

import re
from pathlib import Path

from xe_lang.helper import Result, AssemblyError, Position

Instruction = int

HEX_INT_RE = re.compile(r"^[+-]?0[xX][0-9a-fA-F]+$")
DEC_INT_RE = re.compile(r"^[+-]?\d+$")

# opcode -> (op_code, default_modifier, default_arg, arg_count, wide_single_arg)
INSTRUCTION_MAP = {
	"PUSH":      (0x0, 0x0000, 0x0000, 1, True),

	"LOAD":      (0x1, 0x0000, 0x0000, 1, False),
	"STORE":     (0x1, 0x0001, 0x0000, 1, False),
	"POP":       (0x1, 0x0002, 0x0000, 0, False),
	"DUP":       (0x1, 0x0003, 0x0000, 0, False),
	"SWAP":      (0x1, 0x0004, 0x0000, 0, False),
	"OVER":      (0x1, 0x0005, 0x0000, 0, False),
	"ROT":       (0x1, 0x0006, 0x0000, 0, False),
	"LOADIND":   (0x1, 0x0007, 0x0000, 0, False),
	"STREIND":   (0x1, 0x0008, 0x0000, 0, False),
	"PUSHFP":    (0x1, 0x0009, 0x0000, 0, False),
	"POPFP":     (0x1, 0x000A, 0x0000, 0, False),
	"SETFP":     (0x1, 0x000B, 0x0000, 0, False),
	"LOADSP":    (0x1, 0x000C, 0x0000, 1, False),
	"STORESP":   (0x1, 0x000D, 0x0000, 1, False),

	"I2F":       (0x2, 0x0000, 0x0001, 0, False),
	"F2I":       (0x2, 0x0001, 0x0000, 0, False),
	"I2B":       (0x2, 0x0000, 0x0002, 0, False),
	"F2B":       (0x2, 0x0001, 0x0002, 0, False),
	"B2F":       (0x2, 0x0002, 0x0001, 0, False),

	"ADDI":      (0x3, 0x0000, 0x0000, 0, False),
	"SUBI":      (0x3, 0x0000, 0x0001, 0, False),
	"MULI":      (0x3, 0x0000, 0x0002, 0, False),
	"DIVI":      (0x3, 0x0000, 0x0003, 0, False),
	"MODI":      (0x3, 0x0000, 0x0004, 0, False),
	"POWI":      (0x3, 0x0000, 0x0005, 0, False),
	"AND":       (0x3, 0x0000, 0x0006, 0, False),
	"OR":        (0x3, 0x0000, 0x0007, 0, False),
	"XOR":       (0x3, 0x0000, 0x0008, 0, False),

	"ILT":       (0x3, 0x0000, 0x0011, 0, False),
	"IEQ":       (0x3, 0x0000, 0x0012, 0, False),
	"ILE":       (0x3, 0x0000, 0x0013, 0, False),
	"IGT":       (0x3, 0x0000, 0x0014, 0, False),
	"INE":       (0x3, 0x0000, 0x0015, 0, False),
	"IGE":       (0x3, 0x0000, 0x0016, 0, False),
	"ICR":       (0x3, 0x0000, 0x0017, 0, False),

	"ADDF":      (0x4, 0x0001, 0x0000, 0, False),
	"SUBF":      (0x4, 0x0001, 0x0001, 0, False),
	"MULF":      (0x4, 0x0001, 0x0002, 0, False),
	"DIVF":      (0x4, 0x0001, 0x0003, 0, False),
	"MODF":      (0x4, 0x0001, 0x0004, 0, False),
	"POWF":      (0x4, 0x0001, 0x0005, 0, False),

	"SINF":      (0x4, 0x0001, 0x0006, 0, False),
	"COSF":      (0x4, 0x0001, 0x0007, 0, False),
	"TANF":      (0x4, 0x0001, 0x0008, 0, False),
	"ASINF":     (0x4, 0x0001, 0x0009, 0, False),
	"ACOSF":     (0x4, 0x0001, 0x000A, 0, False),
	"ATANF":     (0x4, 0x0001, 0x000B, 0, False),
	"LERPF":     (0x4, 0x0001, 0x000C, 0, False),

	"FLT":       (0x4, 0x0001, 0x0011, 0, False),
	"FEQ":       (0x4, 0x0001, 0x0012, 0, False),
	"FLE":       (0x4, 0x0001, 0x0013, 0, False),
	"FGT":       (0x4, 0x0001, 0x0014, 0, False),
	"FNE":       (0x4, 0x0001, 0x0015, 0, False),
	"FGE":       (0x4, 0x0001, 0x0016, 0, False),

	"INCI":      (0x4, 0x0002, 0x0000, 0, False),
	"DECI":      (0x4, 0x0002, 0x0001, 0, False),
	"NEGI":      (0x4, 0x0002, 0x0002, 0, False),
	"NOT":       (0x4, 0x0002, 0x0003, 0, False),

	"INCF":      (0x4, 0x0003, 0x0000, 0, False),
	"DECF":      (0x4, 0x0003, 0x0001, 0, False),
	"NEGF":      (0x4, 0x0003, 0x0002, 0, False),

	"JUMP":      (0x5, 0x0000, 0x0000, 1, True),
	"BRZ":       (0x5, 0x0001, 0x0000, 1, True),
	"BRNZ":      (0x5, 0x0002, 0x0000, 1, True),
	"CALL":      (0x5, 0x0003, 0x0000, 1, True),
	"CALZ":      (0x5, 0x0004, 0x0000, 1, True),
	"CALN":      (0x5, 0x0005, 0x0000, 1, True),
	"RET":       (0x5, 0x0006, 0x0000, 1, False),
	"RETZ":      (0x5, 0x0007, 0x0000, 1, False),
	"RETN":      (0x5, 0x0008, 0x0000, 1, False),

	"HALT":      (0x6, 0x0000, 0x0000, 0, False),
	"WAIT":      (0x6, 0x0000, 0x0001, 0, False),
	"PUSHIM":    (0x6, 0x0000, 0x0002, 0, False),
	"POPIM":     (0x6, 0x0000, 0x0003, 0, False),
	"IRT":       (0x6, 0x0000, 0x0004, 0, False),
	"IRTZ":      (0x6, 0x0000, 0x0005, 0, False),
	"IRTN":      (0x6, 0x0000, 0x0006, 0, False),
	"SYS":       (0x6, 0x0000, 0x0007, 1, False),

	"INT":       (0x7, 0x0000, 0x0000, 2, False),
	"SETIM":     (0x8, 0x0000, 0x0000, 1, True),

	"LOOKUP":    (0x9, 0x0000, 0x0000, 1, False),
	"WRITE":     (0x9, 0x0001, 0x0000, 1, False),
	"OFFSET":    (0x9, 0x0002, 0x0000, 1, False),
	"LOADOFF":   (0x9, 0x0003, 0x0000, 1, False),
	"LOADINOFF": (0x9, 0x0004, 0x0000, 0, False),
	"PUSHOFF":   (0x9, 0x0005, 0x0000, 0, False),
	"POPOFF":    (0x9, 0x0006, 0x0000, 0, False),
	"ENABOFF":   (0x9, 0x0007, 0x0000, 0, False),
	"DISABOFF":  (0x9, 0x0008, 0x0000, 0, False),
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


def _parse_arg(token: str):
	token = token.strip()
	if not token:
		raise ValueError("Empty token")
	if token.startswith(":"):
		return token[1:]
	return _parse_int(token)


def _make_position(fn: str, ftxt: str, line: int, col: int) -> Position:
	return Position(0, line, col, fn, ftxt)


def assemble(fn: str, ftxt: str) -> Result:
	res = Result()
	lines = ftxt.splitlines()

	labels: dict[str, int] = {}
	records: list[tuple[Position, Position, str, list[int | str]]] = []
	instruction_index = 0

	for line_idx, original_line in enumerate(lines):
		stripped_line = original_line.split(";", 1)[0]
		if not stripped_line or not stripped_line.strip():
			continue

		tokens = stripped_line.strip().split()
		if not tokens:
			continue

		token_index = 0
		while token_index < len(tokens) and tokens[token_index].startswith(":"):
			label_token = tokens[token_index]
			label_name = label_token[1:]

			if not label_name:
				start_pos = _make_position(fn, ftxt, line_idx, original_line.find(label_token))
				return res.fail(AssemblyError("Invalid label definition", start_pos, start_pos))

			if label_name in labels:
				start_pos = _make_position(fn, ftxt, line_idx, original_line.find(label_token))
				return res.fail(AssemblyError(f"Duplicate label '{label_name}'", start_pos, start_pos))

			labels[label_name] = instruction_index
			token_index += 1

		if token_index >= len(tokens):
			continue

		opcode = tokens[token_index].upper()
		if opcode not in INSTRUCTION_MAP:
			start_col = original_line.find(tokens[token_index])
			start_pos = _make_position(fn, ftxt, line_idx, start_col)
			end_pos = _make_position(fn, ftxt, line_idx, len(stripped_line))
			return res.fail(AssemblyError(f"Unknown opcode '{opcode}'", start_pos, end_pos))

		_, _, _, arg_count, _ = INSTRUCTION_MAP[opcode]
		raw_args = tokens[token_index + 1 : token_index + 1 + arg_count]
		extra = tokens[token_index + 1 + arg_count :]
		if len(raw_args) < arg_count:
			start_col = original_line.find(tokens[token_index])
			start_pos = _make_position(fn, ftxt, line_idx, start_col)
			end_pos = _make_position(fn, ftxt, line_idx, len(stripped_line))
			return res.fail(AssemblyError(f"Missing argument(s) for {opcode}", start_pos, end_pos))
		if extra:
			start_col = original_line.find(extra[0])
			start_pos = _make_position(fn, ftxt, line_idx, start_col)
			end_pos = _make_position(fn, ftxt, line_idx, len(stripped_line))
			return res.fail(AssemblyError(f"Too many arguments for {opcode}", start_pos, end_pos))

		args: list[int | str] = []
		try:
			for raw in raw_args:
				args.append(_parse_arg(raw))
		except ValueError as exc:
			start_col = original_line.find(raw_args[len(args)])
			start_pos = _make_position(fn, ftxt, line_idx, start_col)
			end_pos = _make_position(fn, ftxt, line_idx, len(stripped_line))
			return res.fail(AssemblyError(str(exc), start_pos, end_pos))

		start_col = original_line.find(tokens[token_index])
		start_pos = _make_position(fn, ftxt, line_idx, start_col)
		end_pos = _make_position(fn, ftxt, line_idx, len(stripped_line))
		records.append((start_pos, end_pos, opcode, args))
		instruction_index += 1

	instructions: list[Instruction] = []
	for start_pos, end_pos, opcode, args in records:
		final_args: list[int] = []
		for arg in args:
			if isinstance(arg, str):
				if arg not in labels:
					return res.fail(AssemblyError(f"Undefined label '{arg}'", start_pos, end_pos))
				final_args.append(labels[arg])
			else:
				final_args.append(arg)

		try:
			instructions.append(_encode_instruction(opcode, final_args))
		except ValueError as exc:
			return res.fail(AssemblyError(str(exc), start_pos, end_pos))

	return res.success(instructions)


def assemble_file(path: Path | str) -> Result:
	path_obj = Path(path)
	res = Result()

	if not path_obj.exists():
		pos = Position(0, 0, 0, str(path_obj), "")
		return res.fail(AssemblyError(f"Assembly file not found: {path}", pos, pos))

	text = path_obj.read_text(encoding="utf-8")
	return assemble(str(path_obj), text)