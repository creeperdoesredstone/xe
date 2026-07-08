from xe_lang.helper import TT, Result, AssemblyError
from xe_lang.nodes import *
from xe_lang.rules import BINARY_OPCODE_MAP

from pathlib import Path
import struct

Instruction = tuple

TRUE = 0xFFFFFFFF
FALSE = 0

for_labels: int = 0
while_labels: int = 0
repeat_labels: int = 0
if_labels: int = 0
switch_labels: int = 0


def float_to_u32(value: float) -> int:
	return struct.unpack(">I", struct.pack(">f", value))[0]


def format_instructions(instructions: list[Instruction]) -> str:
	lines = []

	for instruction in instructions:
		start = instruction[0]
		opcode = instruction[2]
		args = instruction[3:]

		location = "" if start is None else f" ; {start.ln + 1}:{start.col + 1}"

		if args:
			lines.append(f"{opcode} {' '.join(map(str, args))}{location}")
		else:
			lines.append(f"{opcode}{location}")

	return "\n".join(lines)


def compile_ast(ast: Node, fn: str) -> Result:
	res = Result()
	name = fn.split("\\")[-1].removesuffix(".xe")

	instructions = [(None, None, f":SECTION_TEXT_{name}")]

	prgm_instructions = res.register(emit(ast))

	if res.error:
		return res

	instructions.extend(prgm_instructions)
	instructions.append((None, None, "HALT"))
	instructions.append((None, None, f":SECTION_DATA_{name}"))

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
	instructions = [
		(
			node.start_pos,
			node.end_pos,
			"LOAD",
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
			# FIX: Checked base and verified it's a primitive scalar
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

	if len(opcode) == 2:
		if (node.left.type.base == "float" and node.left.type.pointer_layers == 0) or \
		   (node.right.type.base == "float" and node.right.type.pointer_layers == 0):
			opcode = f"F{opcode}"
		else:
			opcode = f"I{opcode}"

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			opcode,
		)
	)

	return res.success(instructions)


def emit_VariableDeclaration(node: VariableDeclaration) -> Result:
	return Result().success([])


def emit_VariableAssign(node: VariableAssign) -> Result:
	res = Result()

	instructions = []

	if node.operator._type == TT.ASGN:
		value_ins = res.register(emit(node.value))
		if res.error:
			return res
		instructions.extend(value_ins)
		
		# FIX: Adhering to the Type structure properties
		if node.type.base == "float" and node.type.pointer_layers == 0 and \
		   node.value.type.base == "int" and node.value.type.pointer_layers == 0:
			instructions.append(
				(
					node.start_pos,
					node.end_pos,
					"I2F"
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
	
	# FIX: Adhering to the Type structure properties
	if node.type.base == "float" and node.type.pointer_layers == 0 and \
	   node.value.type.base == "int" and node.value.type.pointer_layers == 0:
		# Note: This logic follows your original draft sequence for casting before compound ops
		pass

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
		
	# FIX: Safe extraction of type properties
	opcode += "F" if (node.type.base == "float" and node.type.pointer_layers == 0) else "I"

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

	# FIX: Cast integer literal if assignment destination expression is float
	if node.type.base == "float" and node.type.pointer_layers == 0 and \
	   node.value.type.base == "int" and node.value.type.pointer_layers == 0:
		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"I2F"
			)
		)

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

	# FIX: Target is a Dereference node (*expr), so target.value is the pointer expression evaluation
	address_instructions = res.register(emit(node.target.value))
	if res.error:
		return res

	if node.operator._type == TT.ASGN:
		instructions.extend(address_instructions)

		value_instructions = res.register(emit(node.value))
		if res.error:
			return res
		instructions.extend(value_instructions)

		# FIX: Implicit conversion logic compliance
		if node.type.base == "float" and node.type.pointer_layers == 0 and \
		   node.value.type.base == "int" and node.value.type.pointer_layers == 0:
			instructions.append(
				(
					node.start_pos,
					node.end_pos,
					"I2F"
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
		
	# FIX: Safe resolution of numeric types
	opcode += "F" if (node.type.base == "float" and node.type.pointer_layers == 0) else "I"

	instructions.extend(address_instructions)
	current_val_instructions = res.register(emit(node.target))
	if res.error:
		return res
	instructions.extend(current_val_instructions)

	value_instructions = res.register(emit(node.value))
	if res.error:
		return res
	instructions.extend(value_instructions)

	if node.type.base == "float" and node.type.pointer_layers == 0 and \
	   node.value.type.base == "int" and node.value.type.pointer_layers == 0:
		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"I2F"
			)
		)

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
		(node.init_expr.end_pos, node.init_expr.end_pos, f":beginfor_{for_labels}")
	)

	instructions.extend(res.register(emit(node.condition_expr)))
	if res.error:
		return res
	instructions.append(
		(
			node.condition_expr.end_pos,
			node.condition_expr.end_pos,
			"BRZ",
			f"endfor_{for_labels}",
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
			f"beginfor_{for_labels}",
		)
	)
	instructions.append(
		(node.step_expr.end_pos, node.step_expr.end_pos, f":endfor_{for_labels}")
	)

	for_labels += 1
	return res.success(instructions)


def emit_WhileLoop(node: WhileLoop) -> Result:
	global while_labels
	res = Result()

	instructions = []

	instructions.append(
		(node.start_pos, node.start_pos, f":beginwhile_{while_labels}")
	)

	instructions.extend(res.register(emit(node.condition_expr)))
	if res.error:
		return res
	instructions.append(
		(
			node.condition_expr.end_pos,
			node.condition_expr.end_pos,
			"BRZ",
			f"endwhile_{while_labels}",
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
			f"beginwhile_{while_labels}",
		)
	)
	instructions.append(
		(node.body.end_pos, node.body.end_pos, f":endwhile_{while_labels}")
	)

	while_labels += 1
	return res.success(instructions)


def emit_RepeatLoop(node: RepeatLoop) -> Result:
	global repeat_labels
	res = Result()

	instructions = []

	instructions.append(
		(node.start_pos, node.start_pos, f":beginrepeat_{repeat_labels}")
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
			f"beginrepeat_{repeat_labels}",
		)
	)

	instructions.append(
		(node.condition_expr.end_pos, node.condition_expr.end_pos, f":endrepeat_{repeat_labels}")
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
			f"branch_{label}_{i}"
			if i != len(node.cases) - 1
			else f"else_{label}"
		)

		instructions.extend(res.register(emit(condition)))
		instructions.append(
			(condition.end_pos, condition.end_pos, "BRZ", next_label)
		)

		instructions.extend(res.register(emit(body)))
		instructions.append(
			(body.end_pos, body.end_pos, "JUMP", f"endif_{label}")
		)

		instructions.append(
			(condition.end_pos, condition.end_pos, f":{next_label}")
		)

	if node.else_case:
		instructions.extend(res.register(emit(node.else_case)))

	instructions.append(
		(node.end_pos, node.end_pos, f":endif_{label}")
	)

	if_labels += 1
	return res.success(instructions)


def emit_SwitchStatement(node: SwitchStatement) -> Result:
	global switch_labels

	label = switch_labels
	switch_labels += 1

	res = Result()
	instructions = []

	# evaluate switch expression once
	instructions.extend(res.register(emit(node.match_expr)))
	if res.error:
		return res

	for i, (case_expr, body) in enumerate(node.cases):

		fail_label = (
			f"case_{label}_{i + 1}"
			if i < len(node.cases) - 1
			else (
				f"default_{label}"
				if node.default_case
				else f"endswitch_{label}"
			)
		)

		# entry label (except first case, which falls through)
		if i != 0:
			instructions.append(
				(
					case_expr.start_pos,
					case_expr.start_pos,
					f":case_{label}_{i}",
				)
			)

		# duplicate switch value
		instructions.append(
			(
				case_expr.start_pos,
				case_expr.start_pos,
				"DUP",
			)
		)

		# emit literal
		instructions.extend(res.register(emit(case_expr)))
		if res.error:
			return res

		# compare
		instructions.append(
			(
				case_expr.end_pos,
				case_expr.end_pos,
				"FEQ"
				if node.match_expr.type.base == "float"
				and node.match_expr.type.pointer_layers == 0
				else "IEQ",
			)
		)

		# if comparison failed
		instructions.append(
			(
				case_expr.end_pos,
				case_expr.end_pos,
				"BRZ",
				fail_label,
			)
		)

		# comparison succeeded -> remove duplicated switch value
		instructions.append(
			(
				case_expr.end_pos,
				case_expr.end_pos,
				"POP",
			)
		)

		# emit case body
		instructions.extend(res.register(emit(body)))
		if res.error:
			return res

		instructions.append(
			(
				body.end_pos,
				body.end_pos,
				"JUMP",
				f"endswitch_{label}",
			)
		)

	# default
	if node.default_case:
		instructions.append(
			(
				node.default_case.start_pos,
				node.default_case.start_pos,
				f":default_{label}",
			)
		)

		# discard switch value
		instructions.append(
			(
				node.default_case.start_pos,
				node.default_case.start_pos,
				"POP",
			)
		)

		instructions.extend(res.register(emit(node.default_case)))
		if res.error:
			return res

	else:
		# no case matched
		instructions.append(
			(
				node.end_pos,
				node.end_pos,
				"POP",
			)
		)

	instructions.append(
		(
			node.end_pos,
			node.end_pos,
			f":endswitch_{label}",
		)
	)

	return res.success(instructions)


def emit_FunctionDefinition(node: FunctionDefinition) -> Result:
	res = Result()
	instructions = []

	instructions.append(
		(node.start_pos, node.start_pos, f":func_{node.name}")
	)

	instructions.append((node.start_pos, node.start_pos, "PUSHFP"))
	instructions.append((node.start_pos, node.start_pos, "SETFP"))

	locals_count = getattr(node, "locals_count", 0)
	for _ in range(locals_count):
		instructions.append((node.start_pos, node.start_pos, "PUSH", 0))

	body_instructions = res.register(emit(node.body))
	if res.error:
		return res
	instructions.extend(body_instructions)

	instructions.append(
		(node.end_pos, node.end_pos, f":cleanup_{node.name}")
	)

	for _ in range(locals_count):
		instructions.append((node.end_pos, node.end_pos, "POP"))

	is_proc = getattr(node, "is_proc", False) or getattr(node, "return_type", None) is None
	params_count = len(getattr(node, "parameters", [])) or len(getattr(node, "args", []))

	if is_proc:
		instructions.append((node.end_pos, node.end_pos, "POPFP"))
		if params_count > 0:
			instructions.append((node.end_pos, node.end_pos, "RET", params_count))
		else:
			instructions.append((node.end_pos, node.end_pos, "RET"))
	else:
		if params_count > 0:
			instructions.append((node.end_pos, node.end_pos, "STORESP", params_count))
		instructions.append((node.end_pos, node.end_pos, "POPFP"))
		instructions.append((node.end_pos, node.end_pos, "RET"))

	return res.success(instructions)

def emit_OutputStatement(node: OutputStatement) -> Result:
	res = Result()
	instructions = []

	for expr in node.values:
		expr_instructions = res.register(emit(expr))
		if res.error:
			return res
		
		if expr.type.pointer_layers > 0:
			...
		else:
			match expr.type.base:
				case "float":
					instructions += [
						(expr.end_pos, expr.end_pos, "PUSH", 16),
						(expr.end_pos, expr.end_pos, "SYS", 21), # malloc
						(expr.end_pos, expr.end_pos, "DUP"),
						(expr.end_pos, expr.end_pos, "DUP"),
					] + expr_instructions + [
						(expr.end_pos, expr.end_pos, "SYS", 6),  # float2chars
						(expr.end_pos, expr.end_pos, "SYS", 1),  # outchars
						(expr.end_pos, expr.end_pos, "SYS", 22), # freeblock
					]
				case "char":
					instructions += expr_instructions + [
						(expr.end_pos, expr.end_pos, "SYS", 9)   # putchar
					]
				case _:
					instructions += [
						(expr.end_pos, expr.end_pos, "PUSH", 16),
						(expr.end_pos, expr.end_pos, "SYS", 21), # malloc
						(expr.end_pos, expr.end_pos, "DUP"),
						(expr.end_pos, expr.end_pos, "DUP"),
					] + expr_instructions + [
						(expr.end_pos, expr.end_pos, "SYS", 5),  # int2chars
						(expr.end_pos, expr.end_pos, "SYS", 1),  # outchars
						(expr.end_pos, expr.end_pos, "SYS", 22), # freeblock
					]
	
	return res.success(instructions)


def emit_TypeCast(node: TypeCast) -> Result:
	res = Result()
	instructions = res.register(emit(node.value))
	if res.error:
		return res

	if node.type.base == "float" and node.type.pointer_layers == 0 and \
	   node.value.type.base == "int" and node.value.type.pointer_layers == 0:
		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"I2F"
			)
		)
	
	if node.type.base == "int" and node.type.pointer_layers == 0 and \
	   node.value.type.base == "float" and node.value.type.pointer_layers == 0:
		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"F2I"
			)
		)
	
	return res.success(instructions)


def emit_ArrayDeclaration(node: ArrayDeclaration) -> Result:
	"""Arrays are just pointers - no code generation needed"""
	return Result().success([])


def emit_ArrayInitializer(node: ArrayInitializer) -> Result:
	res = Result()
	instructions = []

	# Emit each element
	element_instructions = []
	for elem in node.elements:
		elem_inst = res.register(emit(elem))
		if res.error:
			return res
		element_instructions.append(elem_inst)

	# Combine all element instructions
	for inst_list in element_instructions:
		instructions.extend(inst_list)

	return res.success(instructions)


def emit_ArrayIndex(node: ArrayIndex) -> Result:
	res = Result()
	instructions = []

	# Load array pointer
	array_inst = res.register(emit(node.array))
	if res.error:
		return res
	instructions.extend(array_inst)

	# Load index
	index_inst = res.register(emit(node.index))
	if res.error:
		return res
	instructions.extend(index_inst)

	# Add pointer + index
	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"ADDI",
		)
	)

	# Dereference to get value
	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"LOADIND",
		)
	)

	return res.success(instructions)


def emit_ArrayAssign(node: ArrayAssign) -> Result:
	res = Result()
	instructions = []

	if node.operator._type == TT.ASGN:
		# Emit: address = array + index
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

		# Value to store
		value_inst = res.register(emit(node.value))
		if res.error:
			return res
		instructions.extend(value_inst)

		# Implicit int-to-float cast if needed
		if node.type.base == "float" and node.type.pointer_layers == 0 and \
		   node.value.type.base == "int" and node.value.type.pointer_layers == 0:
			instructions.append(
				(
					node.start_pos,
					node.end_pos,
					"I2F"
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

	# Compound assignment (+=, -=, etc.)
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

	# Suffix with I or F based on element type
	opcode += "F" if (node.type.base == "float" and node.type.pointer_layers == 0) else "I"

	# Load current value
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

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"LOADIND",
		)
	)

	# Load value to add/subtract/etc
	value_inst = res.register(emit(node.value))
	if res.error:
		return res
	instructions.extend(value_inst)

	# Cast if needed
	if node.type.base == "float" and node.type.pointer_layers == 0 and \
	   node.value.type.base == "int" and node.value.type.pointer_layers == 0:
		instructions.append(
			(
				node.start_pos,
				node.end_pos,
				"I2F"
			)
		)

	# Perform operation
	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			opcode,
		)
	)

	# Store back
	# Reload address (array + index again)
	array_inst2 = res.register(emit(node.array))
	if res.error:
		return res
	
	# Build STREIND by duplicating earlier pattern
	# We need: address on stack, then value
	# Currently: value is on top, need address below it
	instructions.append((node.start_pos, node.end_pos, "SWAP"))
	
	array_inst2 = res.register(emit(node.array))
	if res.error:
		return res
	instructions.extend(array_inst2)

	index_inst2 = res.register(emit(node.index))
	if res.error:
		return res
	instructions.extend(index_inst2)

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
			"SWAP",
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
