from xe_lang.helper import TT, Result, AssemblyError
from xe_lang.nodes import *
from xe_lang.rules import BINARY_OPCODE_MAP
from math import ceil

from pathlib import Path
import struct

Instruction = tuple

TRUE = 0xFFFFFFFF
FALSE = 0

SAVED_FP_OFFSET = 0
RETURN_SLOT_OFFSET = 1
FIRST_ARG_OFFSET = 2
FIRST_LOCAL_OFFSET = -1

for_labels = 0
while_labels = 0
repeat_labels = 0
if_labels = 0
switch_labels = 0
string_labels = 0
array_labels = 0

func_stack: list[str] = []
func_name: str | None = None

nodes_to_lookup: list[Node] = []

# TODO:
# - implement structs
# - implement classes (no OOP needed, basically structs with methods)
# - implement graphics via syscalls (dw about implementation we'll do that in r1)
# - fix & test subroutines (functions & procedures)


def init_labels():
	global for_labels, while_labels, if_labels, switch_labels, repeat_labels, string_labels, array_labels
	for_labels = 0
	while_labels = 0
	repeat_labels = 0
	if_labels = 0
	switch_labels = 0
	string_labels = 0


def generate_lookup_data():
	global nodes_to_lookup
	instructions = []

	for node in nodes_to_lookup:
		instructions.append((None, None, ":" + node.label))

		if isinstance(node, StringLiteral):
			for char in node.value:
				instructions.append((None, None, ord(char)))
			instructions.append((None, None, 0))

	return instructions


def float_to_u32(value: float) -> int:
	return struct.unpack(">I", struct.pack(">f", value))[0]


def format_instructions(instructions: list[Instruction]) -> str:
	lines = []

	for instruction in instructions:
		opcode = instruction[2]
		args = instruction[3:]

		if args:
			lines.append(f"{opcode} {' '.join(map(str, args))}")
		else:
			lines.append(f"{opcode}")

	return "\n".join(lines)


def compile_ast(ast: Program, fn: str) -> Result:
	global func_name, func_stack
	func_stack = []
	func_name = None

	init_labels()

	res = Result()
	name = fn.split("\\")[-1].removesuffix(".xe")
	nodes_to_lookup.clear()

	instructions = [(None, None, f":SECTION_TEXT_{name}")]

	main_prgm_instructions = []
	for stmt in ast.statements:
		stmt_instructions = res.register(emit(stmt))
		if res.error:
			return res
		main_prgm_instructions.extend(stmt_instructions)

	main_prgm_instructions.append((None, None, "HALT"))

	for sub in ast.sub_defs:
		sub_instructions = res.register(emit(sub))
		if res.error:
			return res
		main_prgm_instructions.extend(sub_instructions)
	main_prgm_instructions.append((None, None, "HALT"))

	data_lookup = generate_lookup_data()

	instructions.extend(main_prgm_instructions)
	instructions.append((None, None, f":SECTION_DATA_{name}"))

	instructions.extend(data_lookup)

	fn = Path(ast.start_pos.fn).stem

	if not fn.startswith("<"):
		asm_dir = Path("asm")
		asm_dir.mkdir(exist_ok=True)

		with open(
			asm_dir / f"{fn}.xas",
			"w",
			encoding="utf-8",
		) as file:
			file.write(format_instructions(instructions))

	return res.success(instructions)


def emit(node: Node) -> Result:
	fn = globals().get(f"emit_{type(node).__name__}")

	if fn is None:
		return Result().fail(
			AssemblyError(
				f"Unsupported AST node '{type(node).__name__}'",
				node.start_pos,
				node.end_pos,
			)
		)

	return fn(node)


def emit_Program(node: Program) -> Result:
	res = Result()

	instructions = []

	for stmt in node.statements:
		stmt_instructions = res.register(emit(stmt))
		if res.error:
			return res

		instructions.extend(stmt_instructions)

	for defn in node.sub_defs:
		sub_def_instructions = res.register(emit(defn))
		if res.error:
			return res

		instructions.extend(sub_def_instructions)

	return res.success(instructions)


def emit_IntLiteral(node: IntLiteral) -> Result:
	return Result().success(
		[
			(
				node.start_pos,
				node.end_pos,
				"PUSH",
				node.value,
			)
		]
	)


def emit_FloatLiteral(node: FloatLiteral) -> Result:
	return Result().success(
		[
			(
				node.start_pos,
				node.end_pos,
				"PUSH",
				float_to_u32(node.value),
			)
		]
	)


def emit_StringLiteral(node: StringLiteral) -> Result:
	global nodes_to_lookup, string_labels
	instructions = [
		# get description vector pointer
		(node.start_pos, node.end_pos, "PUSH", 3),
		(node.start_pos, node.end_pos, "SYS", 21),
		(node.start_pos, node.end_pos, "DUP", 0),
		(node.start_pos, node.end_pos, "DUP", 0),
		(node.start_pos, node.end_pos, "PUSH", 16 * ceil((len(node.value) + 1) / 16)),
		(node.start_pos, node.end_pos, "SYS", 21),
		(
			node.start_pos,
			node.end_pos,
			"STREIND",  # &str[0]
		),
		(
			node.start_pos,
			node.end_pos,
			"INCI",
		),
		(node.start_pos, node.end_pos, "DUP", 0),
		(node.start_pos, node.end_pos, "PUSH", len(node.value) + 1),
		(
			node.start_pos,
			node.end_pos,
			"STREIND",  # str.length
		),
		(
			node.start_pos,
			node.end_pos,
			"INCI",
		),
		(node.start_pos, node.end_pos, "DUP", 0),
		(node.start_pos, node.end_pos, "PUSH", 16 * ceil((len(node.value) + 1) / 16)),
		(
			node.start_pos,
			node.end_pos,
			"STREIND",  # str.capacity
		),
		(
			node.start_pos,
			node.end_pos,
			"DECI",
		),
		(
			node.start_pos,
			node.end_pos,
			"DECI",
		),
		(
			node.start_pos,
			node.end_pos,
			"LOADIND",
		),
		(node.start_pos, node.end_pos, "PUSH", f"STR_LIT_{string_labels}"),
		(node.start_pos, node.end_pos, "LOOKUP", len(node.value) + 1),
	]
	node.label = f"STR_LIT_{string_labels}"
	string_labels += 1

	nodes_to_lookup.append(node)
	return Result().success(instructions)


def emit_BoolLiteral(node: BoolLiteral) -> Result:
	return Result().success(
		[
			(
				node.start_pos,
				node.end_pos,
				"PUSH",
				TRUE if node.value else FALSE,
			)
		]
	)


def emit_CharLiteral(node: CharLiteral) -> Result:
	return Result().success(
		[
			(
				node.start_pos,
				node.end_pos,
				"PUSH",
				ord(node.value),
			)
		]
	)


def emit_Identifier(node: Identifier) -> Result:
	opcode = "LOADSP" if node.is_local else "LOAD"
	instructions = [
		(
			node.start_pos,
			node.end_pos,
			opcode,
			node.address,
		)
	]
	return Result().success(instructions)


def emit_UnaryOperation(node: UnaryOperation) -> Result:
	res = Result()

	if node.op._type != TT.AND:
		instructions = res.register(emit(node.value))
	else:
		instructions = []

	if res.error:
		return res

	match node.op._type:
		case TT.ADD:
			pass

		case TT.SUB:
			is_float = node.type.base == "float" and node.type.pointer_layers == 0
			instructions.append(
				(
					node.start_pos,
					node.end_pos,
					"NEGF" if is_float else "NEGI",
				)
			)

		case TT.NOT:
			instructions.append(
				(
					node.start_pos,
					node.end_pos,
					"NOT",
				)
			)

		case TT.NOTL:
			instructions.append(
				(
					node.start_pos,
					node.end_pos,
					"NOT",
				)
			)

		case TT.MUL:
			instructions.append(
				(
					node.start_pos,
					node.end_pos,
					"LOADIND",
				)
			)

		case TT.AND:
			instructions.append(
				(node.start_pos, node.end_pos, "PUSH", node.value.address)
			)

		case _:
			return res.fail(
				AssemblyError(
					f"Unsupported unary operator '{node.op._type.name}' for '{node.value.type}'",
					node.start_pos,
					node.end_pos,
				)
			)

	return res.success(instructions)


def emit_BinaryOperation(node: BinaryOperation) -> Result:
	res = Result()

	left = res.register(emit(node.left))
	if res.error:
		return res

	right = res.register(emit(node.right))
	if res.error:
		return res

	if node.type.base == "float" and node.type.pointer_layers == 0:
		if node.left.type.base == "int" and node.left.type.pointer_layers == 0:
			left.append(
				(
					node.start_pos,
					node.end_pos,
					"I2F",
				)
			)

		if node.right.type.base == "int" and node.right.type.pointer_layers == 0:
			right.append(
				(
					node.start_pos,
					node.end_pos,
					"I2F",
				)
			)

	instructions = left + right

	opcode = BINARY_OPCODE_MAP.get((node.op._type, node.type.base))

	if opcode is None:
		return res.fail(
			AssemblyError(
				f"Unsupported binary operator '{node.op._type.name}' for '{node.left.type}' and '{node.right.type}'",
				node.op.start_pos,
				node.op.end_pos,
			)
		)

	if len(opcode[0]) == 2:
		if (node.left.type.base == "float" and node.left.type.pointer_layers == 0) or (
			node.right.type.base == "float" and node.right.type.pointer_layers == 0
		):
			opcode[0] = f"F{opcode[0]}"
		elif len(opcode) == 1:
			opcode[0] = f"I{opcode[0]}"

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			*opcode,
		)
	)

	return res.success(instructions)


def emit_VariableDeclaration(node: VariableDeclaration) -> Result:
	return Result().success([])


def emit_VariableAssign(node: VariableAssign) -> Result:
	res = Result()

	instructions = []

	if node.operator._type == TT.ASGN:
		if node.type.is_array and isinstance(node.value, ArrayInitializer):
			value_ins = res.register(emit_ArrayInitializer(node.value, node.address))
			instructions.extend(value_ins)
			return res.success(instructions)
		value_ins = res.register(emit(node.value))
		if res.error:
			return res
		instructions.extend(value_ins)

		if (
			node.type.base == "float"
			and node.type.pointer_layers == 0
			and node.value.type.base == "int"
			and node.value.type.pointer_layers == 0
		):
			instructions.append((node.start_pos, node.end_pos, "I2F"))

		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"STORE",
				node.address,
			)
		)

		return res.success(instructions)

	if (
		node.type.base == "float"
		and node.type.pointer_layers == 0
		and node.value.type.base == "int"
		and node.value.type.pointer_layers == 0
	):
		pass

	if node.type.base == "string":
		# only concatenation is supported
		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"LOAD",
				node.address,
			)
		)

		value_ins = res.register(emit(node.value))
		if res.error:
			return res
		instructions.extend(value_ins)

		instructions.append(
			(
				node.end_pos,
				node.end_pos,
				"SYS",
				10,
			)
		)
		instructions.append(
			(
				node.end_pos,
				node.end_pos,
				"STORE",
				node.address,
			)
		)
		return res.success(instructions)

	compound_map = {
		TT.ADD_ASGN: "ADD",
		TT.SUB_ASGN: "SUB",
		TT.MUL_ASGN: "MUL",
		TT.DIV_ASGN: "DIV",
		TT.MOD_ASGN: "MOD",
		TT.POW_ASGN: "POW",
	}

	opcode = compound_map.get(node.operator._type)

	if opcode is None:
		return res.fail(
			AssemblyError(
				f"Unsupported assignment operator '{node.operator._type.name}'",
				node.start_pos,
				node.end_pos,
			)
		)

	opcode += (
		"F" if (node.type.base == "float" and node.type.pointer_layers == 0) else "I"
	)

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"LOAD",
			node.address,
		)
	)

	value_ins = res.register(emit(node.value))
	if res.error:
		return res
	instructions.extend(value_ins)

	if (
		node.type.base == "float"
		and node.type.pointer_layers == 0
		and node.value.type.base == "int"
		and node.value.type.pointer_layers == 0
	):
		instructions.append((node.start_pos, node.end_pos, "I2F"))

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			opcode,
		)
	)

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"STORE",
			node.address,
		)
	)

	return res.success(instructions)


def emit_PointerAssign(node: PointerAssign) -> Result:
	res = Result()
	instructions = []

	address_instructions = res.register(emit(node.target.value))
	if res.error:
		return res

	if node.operator._type == TT.ASGN:
		instructions.extend(address_instructions)

		value_instructions = res.register(emit(node.value))
		if res.error:
			return res
		instructions.extend(value_instructions)

		if (
			node.type.base == "float"
			and node.type.pointer_layers == 0
			and node.value.type.base == "int"
			and node.value.type.pointer_layers == 0
		):
			instructions.append((node.start_pos, node.end_pos, "I2F"))

		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"STREIND",
			)
		)
		return res.success(instructions)

	compound_map = {
		TT.ADD_ASGN: "ADD",
		TT.SUB_ASGN: "SUB",
		TT.MUL_ASGN: "MUL",
		TT.DIV_ASGN: "DIV",
		TT.MOD_ASGN: "MOD",
		TT.POW_ASGN: "POW",
	}

	opcode = compound_map.get(node.operator._type)
	if opcode is None:
		return res.fail(
			AssemblyError(
				f"Unsupported assignment operator '{node.operator._type.name}'",
				node.start_pos,
				node.end_pos,
			)
		)

	opcode += (
		"F" if (node.type.base == "float" and node.type.pointer_layers == 0) else "I"
	)

	instructions.extend(address_instructions)
	current_val_instructions = res.register(emit(node.target))
	if res.error:
		return res
	instructions.extend(current_val_instructions)

	value_instructions = res.register(emit(node.value))
	if res.error:
		return res
	instructions.extend(value_instructions)

	if (
		node.type.base == "float"
		and node.type.pointer_layers == 0
		and node.value.type.base == "int"
		and node.value.type.pointer_layers == 0
	):
		instructions.append((node.start_pos, node.end_pos, "I2F"))

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			opcode,
		)
	)

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"STREIND",
		)
	)

	return res.success(instructions)


def emit_ForLoop(node: ForLoop) -> Result:
	global for_labels
	res = Result()

	instructions = []

	instructions.extend(res.register(emit(node.init_expr)))
	if res.error:
		return res

	instructions.append(
		(node.init_expr.end_pos, node.init_expr.end_pos, f":beginfor({for_labels})")
	)

	instructions.extend(res.register(emit(node.condition_expr)))
	if res.error:
		return res
	instructions.append(
		(
			node.condition_expr.end_pos,
			node.condition_expr.end_pos,
			"BRZ",
			f"endfor({for_labels})",
		)
	)

	instructions.extend(res.register(emit(node.body)))
	if res.error:
		return res
	instructions.extend(res.register(emit(node.step_expr)))
	if res.error:
		return res

	instructions.append(
		(
			node.step_expr.end_pos,
			node.step_expr.end_pos,
			"JUMP",
			f"beginfor({for_labels})",
		)
	)
	instructions.append(
		(node.step_expr.end_pos, node.step_expr.end_pos, f":endfor({for_labels})")
	)

	for_labels += 1
	return res.success(instructions)


def emit_WhileLoop(node: WhileLoop) -> Result:
	global while_labels
	res = Result()

	instructions = []

	instructions.append(
		(node.start_pos, node.start_pos, f":beginwhile({while_labels})")
	)

	instructions.extend(res.register(emit(node.condition_expr)))
	if res.error:
		return res
	instructions.append(
		(
			node.condition_expr.end_pos,
			node.condition_expr.end_pos,
			"BRZ",
			f"endwhile({while_labels})",
		)
	)

	instructions.extend(res.register(emit(node.body)))
	if res.error:
		return res

	instructions.append(
		(
			node.body.end_pos,
			node.body.end_pos,
			"JUMP",
			f"beginwhile({while_labels})",
		)
	)
	instructions.append(
		(node.body.end_pos, node.body.end_pos, f":endwhile({while_labels})")
	)

	while_labels += 1
	return res.success(instructions)


def emit_RepeatLoop(node: RepeatLoop) -> Result:
	global repeat_labels
	res = Result()

	instructions = []

	instructions.append(
		(node.start_pos, node.start_pos, f":beginrepeat({repeat_labels})")
	)

	instructions.extend(res.register(emit(node.body)))
	if res.error:
		return res

	instructions.extend(res.register(emit(node.condition_expr)))
	if res.error:
		return res
	instructions.append(
		(
			node.condition_expr.end_pos,
			node.condition_expr.end_pos,
			"BRZ",
			f"beginrepeat({repeat_labels})",
		)
	)

	instructions.append(
		(
			node.condition_expr.end_pos,
			node.condition_expr.end_pos,
			f":endrepeat({repeat_labels})",
		)
	)

	repeat_labels += 1
	return res.success(instructions)


def emit_IfConditional(node: IfConditional) -> Result:
	global if_labels
	label: int = if_labels
	res = Result()

	instructions = []

	for i, (condition, body) in enumerate(node.cases):
		next_label = (
			f"branch({label}_{i})" if i != len(node.cases) - 1 else f"else({label})"
		)

		instructions.extend(res.register(emit(condition)))
		instructions.append((condition.end_pos, condition.end_pos, "BRZ", next_label))

		instructions.extend(res.register(emit(body)))
		instructions.append((body.end_pos, body.end_pos, "JUMP", f"endif({label})"))

		instructions.append((condition.end_pos, condition.end_pos, f":{next_label}"))

	if node.else_case:
		instructions.extend(res.register(emit(node.else_case)))

	instructions.append((node.end_pos, node.end_pos, f":endif({label})"))

	if_labels += 1
	return res.success(instructions)


def emit_SwitchStatement(node: SwitchStatement) -> Result:
	global switch_labels

	label = switch_labels
	switch_labels += 1

	res = Result()
	instructions = []

	instructions.extend(res.register(emit(node.match_expr)))
	if res.error:
		return res

	for i, (case_expr, body) in enumerate(node.cases):

		fail_label = (
			f"case({label}_{i + 1})"
			if i < len(node.cases) - 1
			else (f"default({label})" if node.default_case else f"endswitch({label})")
		)

		if i != 0:
			instructions.append(
				(
					case_expr.start_pos,
					case_expr.start_pos,
					f":case({label}_{i})",
				)
			)

		instructions.append((case_expr.start_pos, case_expr.start_pos, "DUP", 0))

		instructions.extend(res.register(emit(case_expr)))
		if res.error:
			return res

		instructions.append(
			(
				case_expr.end_pos,
				case_expr.end_pos,
				(
					"FEQ"
					if node.match_expr.type.base == "float"
					and node.match_expr.type.pointer_layers == 0
					else "IEQ"
				),
			)
		)

		instructions.append(
			(
				case_expr.end_pos,
				case_expr.end_pos,
				"BRZ",
				fail_label,
			)
		)

		instructions.append((case_expr.end_pos, case_expr.end_pos, "POP", 1))

		instructions.extend(res.register(emit(body)))
		if res.error:
			return res

		instructions.append(
			(
				body.end_pos,
				body.end_pos,
				"JUMP",
				f"endswitch({label})",
			)
		)

	# default
	if node.default_case:
		instructions.append(
			(
				node.default_case.start_pos,
				node.default_case.start_pos,
				f":default({label})",
			)
		)

		# discard switch value
		instructions.append(
			(node.default_case.start_pos, node.default_case.start_pos, "POP", 1)
		)

		instructions.extend(res.register(emit(node.default_case)))
		if res.error:
			return res

	else:
		instructions.append((node.end_pos, node.end_pos, "POP", 1))

	instructions.append(
		(
			node.end_pos,
			node.end_pos,
			f":endswitch({label})",
		)
	)

	return res.success(instructions)


def emit_FunctionDefinition(node: FunctionDefinition) -> Result:
	global func_stack, func_name

	res = Result()
	instructions = []

	if func_name is not None:
		func_stack.append(func_name)
	func_name = node.name

	is_proc = (
		getattr(node, "is_proc", False) or getattr(node, "return_type", None) is None
	)

	locals_count = getattr(node, "locals_count", 0)

	params = getattr(node, "parameters", None)
	if params is None:
		params = getattr(node, "args", [])
	params_count = len(params)

	# ------------------------------------------------------------
	# Prologue
	# ------------------------------------------------------------

	instructions.append((node.start_pos, node.start_pos, f":{node.name}"))

	# Caller already pushed FP.
	instructions.append((node.start_pos, node.start_pos, "SETFP"))

	# Reserve return slot.
	if not is_proc:
		instructions.append((node.start_pos, node.start_pos, "PUSH", 0))

	# Allocate locals.
	for _ in range(locals_count):
		instructions.append((node.start_pos, node.start_pos, "PUSH", 0))

	# ------------------------------------------------------------
	# Body
	# ------------------------------------------------------------

	body = res.register(emit(node.body))
	if res.error:
		func_name = func_stack.pop() if func_stack else None
		return res

	instructions.extend(body)

	# ------------------------------------------------------------
	# Epilogue
	# ------------------------------------------------------------

	instructions.append((node.end_pos, node.end_pos, f":cleanup({node.name})"))

	if not is_proc:
		# save return value into the reserved return slot
		instructions.append(
			(
				node.end_pos,
				node.end_pos,
				"STORESP",
				params_count,
			)
		)

	# remove locals while preserving the return slot
	instructions.append(
		(
			node.end_pos,
			node.end_pos,
			"LEAVE",
		)
	)

	# restore caller FP
	instructions.append(
		(
			node.end_pos,
			node.end_pos,
			"POPFP",
		)
	)

	# remove arguments (callee cleans)
	if params_count:
		instructions.append(
			(
				node.end_pos,
				node.end_pos,
				"POP",
				params_count + 1,
			)
		)

	instructions.append(
		(
			node.end_pos,
			node.end_pos,
			"RET",
		)
	)

	func_name = func_stack.pop() if func_stack else None

	return res.success(instructions)


def emit_ProcedureDefinition(node: ProcedureDefinition) -> Result:
	return emit_FunctionDefinition(node)


def emit_ReturnStatement(node: ReturnStatement) -> Result:
	global func_name
	res = Result()
	instructions = []

	if node.value is not None:
		value_instructions = res.register(emit(node.value))
		if res.error:
			return res
		instructions.extend(value_instructions)

	instructions.append((node.start_pos, node.end_pos, "JUMP", f"cleanup({func_name})"))

	return res.success(instructions)


def emit_OutputStatement(node: OutputStatement) -> Result:
	res = Result()
	instructions = []

	for expr in node.values:
		expr_instructions = res.register(emit(expr))
		if res.error:
			return res

		if expr.type.pointer_layers > 0:
			instructions += (
				[
					(expr.end_pos, expr.end_pos, "PUSH", 10),
					(expr.end_pos, expr.end_pos, "SYS", 21),  # malloc
					(expr.end_pos, expr.end_pos, "DUP", 0),
					(expr.end_pos, expr.end_pos, "DUP", 0),
				]
				+ expr_instructions
				+ [
					(expr.end_pos, expr.end_pos, "SYS", 8),  # int2hex
					(expr.end_pos, expr.end_pos, "SYS", 1),  # outchars
					(expr.end_pos, expr.end_pos, "SYS", 22),  # freeblock
				]
			)
		else:
			match expr.type.base:
				case "float":
					instructions += (
						[
							(expr.end_pos, expr.end_pos, "PUSH", 16),
							(expr.end_pos, expr.end_pos, "SYS", 21),  # malloc
							(expr.end_pos, expr.end_pos, "DUP", 0),
							(expr.end_pos, expr.end_pos, "DUP", 0),
						]
						+ expr_instructions
						+ [
							(expr.end_pos, expr.end_pos, "SYS", 6),  # float2chars
							(expr.end_pos, expr.end_pos, "SYS", 1),  # outchars
							(expr.end_pos, expr.end_pos, "SYS", 22),  # freeblock
						]
					)
				case "char":
					instructions += expr_instructions + [
						(expr.end_pos, expr.end_pos, "SYS", 9)  # putchar
					]
				case "string":
					instructions += expr_instructions + [
						(expr.end_pos, expr.end_pos, "LOADIND"),
						(expr.end_pos, expr.end_pos, "SYS", 1),  # outchars
					]
				case _:
					instructions += (
						[
							(expr.end_pos, expr.end_pos, "PUSH", 16),
							(expr.end_pos, expr.end_pos, "SYS", 21),  # malloc
							(expr.end_pos, expr.end_pos, "DUP", 0),
							(expr.end_pos, expr.end_pos, "DUP", 0),
						]
						+ expr_instructions
						+ [
							(expr.end_pos, expr.end_pos, "SYS", 5),  # int2chars
							(expr.end_pos, expr.end_pos, "SYS", 1),  # outchars
							(expr.end_pos, expr.end_pos, "SYS", 22),  # freeblock
						]
					)

	return res.success(instructions)


def emit_TypeCast(node: TypeCast) -> Result:
	res = Result()
	instructions = res.register(emit(node.value))
	if res.error:
		return res

	if (
		node.type.base == "float"
		and node.type.pointer_layers == 0
		and node.value.type.base == "int"
		and node.value.type.pointer_layers == 0
	):
		instructions.append((node.start_pos, node.end_pos, "I2F"))

	if (
		node.type.base == "int"
		and node.type.pointer_layers == 0
		and node.value.type.base == "float"
		and node.value.type.pointer_layers == 0
	):
		instructions.append((node.start_pos, node.end_pos, "F2I"))

	return res.success(instructions)


def emit_ArrayDeclaration(node: ArrayDeclaration) -> Result:
	return Result().success(
		[
			(node.start_pos, node.end_pos, "PUSH", node.size.value),
			(node.start_pos, node.end_pos, "SYS", 21),
			(node.start_pos, node.end_pos, "STORE", node.address),
		]
	)


def emit_ArrayInitializer(node: ArrayInitializer, init_address: int = -1) -> Result:
	res = Result()
	instructions = []

	if init_address > -1:
		instructions.append((node.start_pos, node.start_pos, "LOAD", init_address))

	element_instructions = []
	for elem in node.elements:
		elem_inst = res.register(emit(elem))
		if res.error:
			return res
		element_instructions.append(elem_inst)

	for inst_list in element_instructions:
		if init_address > -1:
			instructions.append((elem.end_pos, elem.end_pos, "DUP", 0))

		instructions.extend(inst_list)

		if init_address > -1:
			instructions.append((elem.end_pos, elem.end_pos, "STREIND"))
			instructions.append((elem.end_pos, elem.end_pos, "INCI"))

	if init_address > -1:
		instructions.append((node.end_pos, node.end_pos, "POP", 1))
	return res.success(instructions)


def emit_ArrayIndex(node: ArrayIndex) -> Result:
	res = Result()
	instructions = []

	index_inst = res.register(emit(node.index))
	if res.error:
		return res
	instructions.extend(index_inst)

	array_inst = res.register(emit(node.array))
	if res.error:
		return res
	instructions.extend(array_inst)

	if node.type.base == "string":
		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"LOADIND",
			)
		)

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"ADDI",
		)
	)

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"LOADIND",
		)
	)
	node.type.base = "char"

	return res.success(instructions)


def emit_ArrayAssign(node: ArrayAssign) -> Result:
	res = Result()
	instructions = []

	if node.operator._type == TT.ASGN:
		array_inst = res.register(emit(node.array))
		if res.error:
			return res
		instructions.extend(array_inst)

		index_inst = res.register(emit(node.index))
		if res.error:
			return res
		instructions.extend(index_inst)

		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"ADDI",
			)
		)

		value_inst = res.register(emit(node.value))
		if res.error:
			return res
		instructions.extend(value_inst)

		if (
			node.type.base == "float"
			and node.type.pointer_layers == 0
			and node.value.type.base == "int"
			and node.value.type.pointer_layers == 0
		):
			instructions.append((node.start_pos, node.end_pos, "I2F"))

		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"STREIND",
			)
		)

		return res.success(instructions)

	compound_map = {
		TT.ADD_ASGN: "ADD",
		TT.SUB_ASGN: "SUB",
		TT.MUL_ASGN: "MUL",
		TT.DIV_ASGN: "DIV",
		TT.MOD_ASGN: "MOD",
		TT.POW_ASGN: "POW",
	}

	opcode = compound_map.get(node.operator._type)
	if opcode is None:
		return res.fail(
			AssemblyError(
				f"Unsupported assignment operator '{node.operator._type.name}'",
				node.start_pos,
				node.end_pos,
			)
		)

	opcode += (
		"F" if (node.type.base == "float" and node.type.pointer_layers == 0) else "I"
	)

	array_inst = res.register(emit(node.array))
	if res.error:
		return res
	instructions.extend(array_inst)

	index_inst = res.register(emit(node.index))
	if res.error:
		return res
	instructions.extend(index_inst)

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"ADDI",
		)
	)

	instructions.append((node.start_pos, node.end_pos, "DUP", 0))

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"LOADIND",
		)
	)

	value_inst = res.register(emit(node.value))
	if res.error:
		return res
	instructions.extend(value_inst)

	if (
		node.type.base == "float"
		and node.type.pointer_layers == 0
		and node.value.type.base == "int"
		and node.value.type.pointer_layers == 0
	):
		instructions.append((node.start_pos, node.end_pos, "I2F"))

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			opcode,
		)
	)

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"STREIND",
		)
	)

	return res.success(instructions)


def emit_StringOperation(node: StringOperation) -> Result:
	res = Result()

	left = res.register(emit(node.left))
	if res.error:
		return res

	right = res.register(emit(node.right))
	if res.error:
		return res

	instructions = left + right

	arg = 10 if node.op._type == TT.ADD else 11

	instructions.append((node.start_pos, node.end_pos, "SYS", arg))

	if node.op._type == TT.NE:
		instructions.append(
			(
				node.end_pos,
				node.end_pos,
				"NOT",
			)
		)

	return res.success(instructions)


def emit_FunctionCall(node: FunctionCall) -> Result:
	res = Result()
	instructions = []

	# Evaluate arguments left-to-right.
	for arg in node.arguments:
		arg_instr = res.register(emit(arg))
		if res.error:
			return res
		instructions.extend(arg_instr)

	# Save caller's frame pointer.
	instructions.append((node.start_pos, node.end_pos, "PUSHFP"))

	if isinstance(node.caller, Identifier):
		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"CALL",
				node.caller.value,
			)
		)
	else:
		caller_instr = res.register(emit(node.caller))
		if res.error:
			return res

		instructions.extend(caller_instr)

		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"CALLIND",
			)
		)

	return res.success(instructions)
