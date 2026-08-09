from __future__ import annotations

import struct

Instruction = tuple


def _float_to_u32(value: float) -> int:
	return struct.unpack(">I", struct.pack(">f", value))[0]


def _u32_to_float(bits: int) -> float:
	return struct.unpack(">f", struct.pack(">I", bits))[0]


def _truncate_toward_zero(value: float) -> int:
	return int(value)


def _pow_i32(base: int, exponent: int) -> int:
	if exponent < 0:
		raise ValueError("integer exponent must be non-negative")
	return pow(base & 0xFFFFFFFF, exponent, 1 << 32)


BINARY_FOLDABLE = {
	"ADDI": lambda a, b: a + b,
	"SUBI": lambda a, b: a - b,
	"MULI": lambda a, b: a * b,
	"DIVI": lambda a, b: int(a / b),
	"MODI": lambda a, b: a % b,
	"POWI": _pow_i32,
	"AND": lambda a, b: a & b,
	"OR": lambda a, b: a | b,
	"XOR": lambda a, b: a ^ b,
}

NOOP_OPCODES = {"NOP"}


def _is_label(instr: Instruction) -> bool:
	return instr[0] is None and instr[1] is None and isinstance(instr[2], str) and instr[2].startswith(":")


def _is_section(instr: Instruction) -> bool:
	return _is_label(instr) and instr[2].startswith(":SECTION_")


def _is_data_word(instr: Instruction) -> bool:
	return instr[0] is None and instr[1] is None and isinstance(instr[2], int)


def _opcode(instr: Instruction) -> str:
	return instr[2] if isinstance(instr[2], str) else ""


def _referenced_labels(instructions: list[Instruction]) -> set[str]:
	refs: set[str] = set()
	for instr in instructions:
		if len(instr) <= 3:
			continue

		for operand in instr[3:]:
			if isinstance(operand, str):
				refs.add(operand if operand.startswith(":") else ":" + operand)

	return refs


def remove_nops(instructions: list[Instruction]) -> list[Instruction]:
	instr = []
	for i in instructions:
		if _opcode(i) in NOOP_OPCODES or (_opcode(i) == "POP" and i[-1] == 0):
			continue
		instr.append(i)
	return instr


def fold_constants(instructions: list[Instruction]) -> list[Instruction]:
	out: list[Instruction] = []

	for instr in instructions:
		if (
			_opcode(instr) in BINARY_FOLDABLE
			and len(out) >= 2
			and _opcode(out[-1]) == "PUSH"
			and _opcode(out[-2]) == "PUSH"
			and isinstance(out[-1][3], int)
			and isinstance(out[-2][3], int)
		):
			b = out[-1]
			a = out[-2]
			try:
				folded = BINARY_FOLDABLE[_opcode(instr)](a[3], b[3])
			except (ArithmeticError, OverflowError, ValueError):
				out.append(instr)
				continue
			if not isinstance(folded, int):
				out.append(instr)
				continue
			out.pop()
			out.pop()
			out.append((a[0], instr[1], "PUSH", folded))
		else:
			out.append(instr)

	return out


def fold_literal_casts(instructions: list[Instruction]) -> list[Instruction]:
	out: list[Instruction] = []

	for instr in instructions:
		opcode = _opcode(instr)

		if (
			opcode == "I2F"
			and out
			and _opcode(out[-1]) == "PUSH"
			and isinstance(out[-1][3], int)
		):
			push = out.pop()
			out.append((push[0], instr[1], "PUSH", _float_to_u32(float(push[3]))))

		elif (
			opcode == "F2I"
			and out
			and _opcode(out[-1]) == "PUSH"
			and isinstance(out[-1][3], int)
		):
			push = out.pop()
			truncated = _truncate_toward_zero(_u32_to_float(push[3]))
			out.append((push[0], instr[1], "PUSH", truncated))

		else:
			out.append(instr)

	return out


def fold_inc_dec(instructions: list[Instruction]) -> list[Instruction]:
	out: list[Instruction] = []
	i = 0
	while i < len(instructions):
		cur = instructions[i]
		nxt = instructions[i + 1] if i + 1 < len(instructions) else None

		if nxt is not None:
			if cur[2:] == ("PUSH", 1):
				if _opcode(nxt) == "ADDI":
					out.append((cur[0], cur[1], "INCI"))
					i += 2
					continue
				if _opcode(nxt) == "SUBI":
					out.append((cur[0], cur[1], "DECI"))
					i += 2
					continue
			if cur[2:] == ("PUSH", 1065353216):
				if _opcode(nxt) == "ADDF":
					out.append((cur[0], cur[1], "INCF"))
					i += 2
					continue
				if _opcode(nxt) == "SUBF":
					out.append((cur[0], cur[1], "DECF"))
					i += 2
					continue

		out.append(cur)
		i += 1

	return out


def remove_redundant_loads(instructions: list[Instruction]) -> list[Instruction]:
	out: list[Instruction] = []
	i = 0
	while i < len(instructions):
		cur = instructions[i]
		nxt = instructions[i + 1] if i + 1 < len(instructions) else None

		if (
			nxt is not None
			and _opcode(cur) in ("LOAD", "LOADSP")
			and _opcode(nxt) in ("STORE", "STORESP")
			and cur[2][-2:] == nxt[2][-2:]  # both plain or both SP variant matches by construction
			and len(cur) > 3
			and len(nxt) > 3
			and cur[3] == nxt[3]
		):
			# LOAD n; STORE n with nothing in between is a no-op round trip
			i += 2
			continue

		out.append(cur)
		i += 1

	return out


def remove_unreachable_after_halt(instructions: list[Instruction]) -> list[Instruction]:
	out: list[Instruction] = []
	dead = False

	for instr in instructions:
		if _is_label(instr) or _is_section(instr) or _is_data_word(instr):
			dead = False
			out.append(instr)
			continue

		if dead:
			continue

		out.append(instr)

		if _opcode(instr) in ("HALT", "JUMP"):
			dead = True

	return out


def remove_unused_labels(instructions: list[Instruction]) -> list[Instruction]:
	while True:
		refs = _referenced_labels(instructions)
		pruned = [
			instr
			for instr in instructions
			if not (_is_label(instr) and not _is_section(instr) and instr[2] not in refs)
		]

		swept = remove_unreachable_after_halt(pruned)

		if swept == instructions:
			return swept

		instructions = swept


DEFAULT_PASSES = [
	remove_nops,
	fold_constants,
	fold_literal_casts,
	fold_inc_dec,
	remove_redundant_loads,
]


def optimize(instructions: list[Instruction], passes: list | None = None) -> list[Instruction]:
	passes = DEFAULT_PASSES if passes is None else passes
	for p in passes:
		instructions = p(instructions)
	return instructions


if __name__ == "__main__":
	sample = [
		(None, None, ":SECTION_TEXT_demo"),
		(0, 0, "PUSH", 2),
		(0, 0, "PUSH", 3),
		(0, 0, "ADDI"),
		(0, 0, "NOP"),
		(0, 0, "STORE", 0),
		(0, 0, "LOAD", 0),
		(0, 0, "HALT"),
		(0, 0, "PUSH", 999),            # unreachable, should be dropped
		(None, None, ":UNUSED_LABEL"),  # unreferenced, should be dropped
		(None, None, ":SECTION_DATA_demo"),
		(None, None, 65),
		(None, None, 0),
	]
	print("--- basic passes ---")
	for instr in optimize(sample):
		print(instr)

	float_add_sample = [
		(None, None, ":SECTION_TEXT_demo2"),
		(0, 0, "LOAD", 0),
		(0, 0, "PUSH", 4),
		(0, 0, "I2F"),
		(0, 0, "ADDF"),
		(0, 0, "HALT"),
	]
	print("--- float literal folding (a + 4) ---")
	for instr in optimize(float_add_sample):
		print(instr)

	int_cast_sample = [
		(None, None, ":SECTION_TEXT_demo3"),
		(0, 0, "PUSH", _float_to_u32(3.14)),
		(0, 0, "F2I"),
		(0, 0, "HALT"),
	]
	print("--- float literal cast folding ((int)3.14) ---")
	for instr in optimize(int_cast_sample):
		print(instr)
