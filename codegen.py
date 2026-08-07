from xe_lang.helper import TT, Result, AssemblyError
from xe_lang.nodes import *
from xe_lang.rules import BINARY_OPCODE_MAP
from math import sin, cos, tan, asin, acos, atan, sqrt, ceil
from xe_lang.stdlib import (
	BUILTIN_SYSCALLS,
	METHOD_SYSCALLS,
	PROPERTY_GETTER_SYSCALLS,
	PROPERTY_SETTER_SYSCALLS,
	BuiltInID,
)
from xe_lang.syscall_abi import SyscallID

from pathlib import Path
import struct

Instruction = tuple

TRUE = 0xFFFFFFFF
FALSE = 0

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

def emit_argument(arg: Node) -> Result:
	res = Result()
	struct_sym = getattr(arg, "struct_symbol", None)

	if struct_sym is None:
		return emit(arg)

	if not isinstance(arg, (Identifier, MemberAccess)):
		return res.fail(
			AssemblyError(
				"Only plain variables or struct fields can be passed by value as struct arguments.",
				arg.start_pos,
				arg.end_pos,
			)
		)

	base_address, is_local = resolve_struct_base_address(arg)
	load_opcode = "LOADSP" if is_local else "LOAD"

	instructions = [
		(arg.start_pos, arg.end_pos, load_opcode, base_address + slot)
		for slot in range(struct_sym.size)
	]

	return res.success(instructions)


def is_pointer_base(node: Node) -> bool:
	t = getattr(node, "type", None)
	return t is not None and getattr(t, "pointer_layers", 0) > 0


def is_implicit_float_cast(arg_type, expected_type) -> bool:
	return (
		expected_type.base == "float"
		and expected_type.pointer_layers == 0
		and arg_type.base == "int"
		and arg_type.pointer_layers == 0
	)


def emit_pointer_field_address(node: Node) -> Result:
	res = Result()

	field_address = node.field_address if hasattr(node, "field_address") else 0

	if isinstance(node, MemberAccess):
		base_instructions = res.register(emit(node.parent))
	elif isinstance(node, MemberAssign):
		base_instructions = res.register(emit(node.obj))
	else:
		base_instructions = res.register(emit(node))

	if res.error:
		return res

	instructions = list(base_instructions)
	if field_address:
		instructions.append((node.start_pos, node.end_pos, "PUSH", field_address))
		instructions.append((node.start_pos, node.end_pos, "ADDI"))

	return res.success(instructions)


def resolve_struct_base_address(node: Node) -> tuple[int, bool]:
	if isinstance(node, Identifier):
		return (node.address, node.is_local)

	if isinstance(node, MemberAccess):
		base_address, is_local = resolve_struct_base_address(node.parent)
		return (base_address + node.field_address, is_local)

	raise AssemblyError(
		f"Cannot resolve a compile-time address for struct member access on '{type(node).__name__}'. "
		"Only plain variables and nested struct fields are supported currently.",
		node.start_pos,
		node.end_pos,
	)


def init_labels():
	global for_labels, while_labels, if_labels, switch_labels, repeat_labels, string_labels, array_labels, nodes_to_lookup
	for_labels = 0
	while_labels = 0
	repeat_labels = 0
	if_labels = 0
	switch_labels = 0
	string_labels = 0
	array_labels = 0
	nodes_to_lookup = []


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


def emit_string_literal_init(node: StringLiteral) -> Result:
	global nodes_to_lookup, string_labels

	instructions = [
		# allocate descriptor
		(node.start_pos, node.end_pos, "PUSH", 3),
		(node.start_pos, node.end_pos, "SYS", SyscallID.OS_MALLOC),
		(node.start_pos, node.end_pos, "DUP", 0),
		(node.start_pos, node.end_pos, "DUP", 0),
		# allocate character buffer
		(node.start_pos, node.end_pos, "PUSH", len(node.value) + 1),
		(node.start_pos, node.end_pos, "SYS", SyscallID.OS_MALLOC),
		# store pointer to buffer[0] at descriptor[0]
		(node.start_pos, node.end_pos, "STREIND"),
		(node.start_pos, node.end_pos, "INCI"),
		(node.start_pos, node.end_pos, "DUP", 0),
		# store length including '\0' at descriptor[1]
		(node.start_pos, node.end_pos, "PUSH", len(node.value) + 1),
		(node.start_pos, node.end_pos, "STREIND"),
		(node.start_pos, node.end_pos, "INCI"),
		(node.start_pos, node.end_pos, "DUP", 0),
		# store capacity at descriptor[2]
		(node.start_pos, node.end_pos, "PUSH", len(node.value) + 1),
		(node.start_pos, node.end_pos, "STREIND"),
		# roll back to descriptor[0]
		(node.start_pos, node.end_pos, "DECI"),
		(node.start_pos, node.end_pos, "DECI"),
		(node.start_pos, node.end_pos, "LOADIND"),  # buffer[0]
		(node.start_pos, node.end_pos, "PUSH", f"STR_LIT_{string_labels}"),
		(node.start_pos, node.end_pos, "LOOKUP", len(node.value) + 1),
		# store the finished pointer into this literal's dedicated slot
		(node.start_pos, node.end_pos, "STORE", node.address),
	]
	node.label = f"STR_LIT_{string_labels}"
	string_labels += 1

	nodes_to_lookup.append(node)
	return Result().success(instructions)


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

	string_init_instructions = []
	for lit_node in getattr(ast, "string_literals", []):
		lit_instructions = res.register(emit_string_literal_init(lit_node))
		if res.error:
			return res
		string_init_instructions.extend(lit_instructions)
	instructions.extend(string_init_instructions)

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
	return Result().success(
		[(node.start_pos, node.end_pos, "LOAD", node.address)]
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

	opcode_template = BINARY_OPCODE_MAP.get((node.op._type, node.type.base))

	if opcode_template is None:
		return res.fail(
			AssemblyError(
				f"Unsupported binary operator '{node.op._type.name}' for '{node.left.type}' and '{node.right.type}'",
				node.op.start_pos,
				node.op.end_pos,
			)
		)

	opcode = list(opcode_template)
	comparison_ops = {TT.EQ, TT.NE, TT.LT, TT.LE, TT.GT, TT.GE}
	if node.op._type in comparison_ops:
		if (node.left.type.base == "float" and node.left.type.pointer_layers == 0) or (
			node.right.type.base == "float" and node.right.type.pointer_layers == 0
		):
			opcode[0] = f"F{opcode[0]}"
		else:
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
	store_opcode = "STORESP" if node.is_local else "STORE"
	load_opcode = "LOADSP" if node.is_local else "LOAD"

	if node.operator._type == TT.ASGN:
		if node.type.is_array and isinstance(node.value, ArrayInitializer):
			value_ins = res.register(emit_ArrayInitializer(node.value, node.address))
			instructions.extend(value_ins)
			return res.success(instructions)
		
		struct_sym = getattr(node, "struct_symbol", None)
		is_by_value_struct = struct_sym is not None and node.type.pointer_layers == 0

		if is_by_value_struct:
			if struct_sym is not None:
				if isinstance(node.value, (Identifier, MemberAccess)):
					# copying an existing struct value by name/field
					src_base, src_is_local = resolve_struct_base_address(node.value)
					src_load_opcode = "LOADSP" if src_is_local else "LOAD"
					for slot in range(struct_sym.size):
						instructions.append(
							(node.start_pos, node.end_pos, src_load_opcode, src_base + slot)
						)
				else:
					# A one-word built-in resource (for example os::File) may
					# be returned by a library call. Larger aggregate returns
					# still use the normal function return convention.
					if not isinstance(node.value, FunctionCall) and not (
						struct_sym.size == 1 and isinstance(node.value, LibraryCall)
					):
						return res.fail(
							AssemblyError(
								f"Cannot assign a value of this form to a by-value struct/class variable.",
								node.value.start_pos,
								node.value.end_pos,
							)
						)
					value_ins = res.register(emit(node.value))
					if res.error:
						return res
					instructions.extend(value_ins)

				for slot in reversed(range(struct_sym.size)):
					instructions.append(
						(node.start_pos, node.end_pos, store_opcode, node.address + slot)
					)

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
				store_opcode,
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
				load_opcode,
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
				SyscallID.STRING_CONCAT,
			)
		)
		instructions.append(
			(
				node.end_pos,
				node.end_pos,
				store_opcode,
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
			load_opcode,
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
			store_opcode,
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
	label = for_labels
	for_labels += 1
	res = Result()

	instructions = []

	instructions.extend(res.register(emit(node.init_expr)))
	if res.error:
		return res

	instructions.append(
		(node.init_expr.end_pos, node.init_expr.end_pos, f":beginfor({label})")
	)

	instructions.extend(res.register(emit(node.condition_expr)))
	if res.error:
		return res
	instructions.append(
		(
			node.condition_expr.end_pos,
			node.condition_expr.end_pos,
			"BRZ",
			f"endfor({label})",
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
			f"beginfor({label})",
		)
	)
	instructions.append(
		(node.step_expr.end_pos, node.step_expr.end_pos, f":endfor({label})")
	)

	return res.success(instructions)


def emit_WhileLoop(node: WhileLoop) -> Result:
	global while_labels
	label = while_labels
	while_labels += 1
	res = Result()

	instructions = []

	instructions.append(
		(node.start_pos, node.start_pos, f":beginwhile({label})")
	)

	instructions.extend(res.register(emit(node.condition_expr)))
	if res.error:
		return res
	instructions.append(
		(
			node.condition_expr.end_pos,
			node.condition_expr.end_pos,
			"BRZ",
			f"endwhile({label})",
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
			f"beginwhile({label})",
		)
	)
	instructions.append(
		(node.body.end_pos, node.body.end_pos, f":endwhile({label})")
	)

	return res.success(instructions)


def emit_RepeatLoop(node: RepeatLoop) -> Result:
	global repeat_labels
	label = repeat_labels
	repeat_labels += 1
	res = Result()

	instructions = []

	instructions.append(
		(node.start_pos, node.start_pos, f":beginrepeat({label})")
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
			f"beginrepeat({label})",
		)
	)

	instructions.append(
		(
			node.condition_expr.end_pos,
			node.condition_expr.end_pos,
			f":endrepeat({label})",
		)
	)

	return res.success(instructions)


def emit_IfConditional(node: IfConditional) -> Result:
	global if_labels
	label: int = if_labels
	if_labels += 1
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


def emit_FunctionDefinition(node: FunctionDefinition|ProcedureDefinition) -> Result:
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
	return_width = getattr(node, "return_width", 0 if is_proc else 1)

	params = getattr(node, "parameters", None)
	if params is None:
		params = getattr(node, "args", [])
	params_count = getattr(node, "param_count", len(params))

	# prologue
	instructions.append((node.start_pos, node.start_pos, f":{node.name}"))
	instructions.append((node.start_pos, node.start_pos, "SETFP"))

	for _ in range(locals_count):
		instructions.append((node.start_pos, node.start_pos, "PUSH", 0))

	# body
	body = res.register(emit(node.body))
	if res.error:
		func_name = func_stack.pop() if func_stack else None
		return res

	instructions.extend(body)

	# epilogue
	instructions.append((node.end_pos, node.end_pos, f":cleanup({node.name})"))

	if not is_proc:
		result_base = max(params_count, return_width)
		for slot in reversed(range(return_width)):
			instructions.append(
				(node.end_pos, node.end_pos, "STORESP", result_base - slot)
			)

	instructions.append(
		(
			node.end_pos,
			node.end_pos,
			"POP",
			locals_count,
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

	# Remove the saved FP plus argument slots not reused by the return value.
	cleanup_count = max(params_count, return_width) - return_width + 1
	if cleanup_count:
		instructions.append(
			(
				node.end_pos,
				node.end_pos,
				"POP",
				cleanup_count,
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
		struct_sym = getattr(node.value, "struct_symbol", None)

		if struct_sym is not None:
			if not isinstance(node.value, (Identifier, MemberAccess)):
				return res.fail(
					AssemblyError(
						"Only a plain variable or struct field can be returned by value.",
						node.value.start_pos,
						node.value.end_pos,
					)
				)

			base_address, _ = resolve_struct_base_address(node.value)
			load_opcode = "LOADSP"

			if isinstance(node.value, Identifier):
				# returns a struct
				for slot in range(struct_sym.size):
					instructions.append(
						(
							node.start_pos,
							node.end_pos,
							load_opcode,
							base_address - slot,
						)
					)
			else:
				# member access
				instructions.extend([
					(
						node.start_pos,
						node.end_pos,
						load_opcode,
						base_address - node.value.field_address, # $self
					),
					(
						node.start_pos,
						node.end_pos,
						"PUSH",
						node.value.field_address
					),
					(
						node.start_pos,
						node.end_pos,
						"ADDI",
					),
					(
						node.start_pos,
						node.end_pos,
						"LOADIND",
					),
				])
		else:
			value_instructions = res.register(emit(node.value))
			if res.error:
				return res
			instructions.extend(value_instructions)

	instructions.append(
		(node.start_pos, node.end_pos, "JUMP", f"cleanup({func_name})")
	)

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
					(expr.end_pos, expr.end_pos, "SYS", SyscallID.OS_MALLOC),
					(expr.end_pos, expr.end_pos, "DUP", 0),
					(expr.end_pos, expr.end_pos, "DUP", 0),
				]
				+ expr_instructions
				+ [
					(expr.end_pos, expr.end_pos, "SYS", SyscallID.INT_TO_HEX),
					(expr.end_pos, expr.end_pos, "SYS", SyscallID.OUTPUT_CHARS),
					(expr.end_pos, expr.end_pos, "SYS", SyscallID.OS_FREE),
				]
			)
		else:
			match expr.type.base:
				case "float":
					instructions += (
						[
							(expr.end_pos, expr.end_pos, "PUSH", 16),
							(expr.end_pos, expr.end_pos, "SYS", SyscallID.OS_MALLOC),
							(expr.end_pos, expr.end_pos, "DUP", 0),
							(expr.end_pos, expr.end_pos, "DUP", 0),
						]
						+ expr_instructions
						+ [
							(expr.end_pos, expr.end_pos, "SYS", SyscallID.FLOAT_TO_CHARS),
							(expr.end_pos, expr.end_pos, "SYS", SyscallID.OUTPUT_CHARS),
							(expr.end_pos, expr.end_pos, "SYS", SyscallID.OS_FREE),
						]
					)
				case "char":
					instructions += expr_instructions + [
						(expr.end_pos, expr.end_pos, "SYS", SyscallID.PUT_CHAR)
					]
				case "string":
					instructions += expr_instructions + [
						(expr.end_pos, expr.end_pos, "LOADIND"),
						(expr.end_pos, expr.end_pos, "SYS", SyscallID.OUTPUT_CHARS),
					]
				case _:
					instructions += (
						[
							(expr.end_pos, expr.end_pos, "PUSH", 16),
							(expr.end_pos, expr.end_pos, "SYS", SyscallID.OS_MALLOC),
							(expr.end_pos, expr.end_pos, "DUP", 0),
							(expr.end_pos, expr.end_pos, "DUP", 0),
						]
						+ expr_instructions
						+ [
							(expr.end_pos, expr.end_pos, "SYS", SyscallID.INT_TO_CHARS),
							(expr.end_pos, expr.end_pos, "SYS", SyscallID.OUTPUT_CHARS),
							(expr.end_pos, expr.end_pos, "SYS", SyscallID.OS_FREE),
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
			(node.start_pos, node.end_pos, "SYS", SyscallID.OS_MALLOC),
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

	if node.array.type.base == "string":
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
	return res.success(instructions)


def emit_InputStatement(node: InputStatement) -> Result:
	if not isinstance(node.var, Identifier):
		return Result().fail(
			AssemblyError(
				"Input currently requires a plain string variable.",
				node.start_pos,
				node.end_pos,
			)
		)
	opcode = "STORESP" if node.var.is_local else "STORE"
	return Result().success(
		[
			(node.start_pos, node.end_pos, "SYS", SyscallID.READ_STR),
			(node.start_pos, node.end_pos, opcode, node.var.address),
		]
	)


def emit_ArrayAssign(node: ArrayAssign) -> Result:
	res = Result()
	instructions = []

	if node.operator._type == TT.ASGN:
		array_inst = res.register(emit(node.array))
		if res.error:
			return res
		instructions.extend(array_inst)
		if node.array.type.base == "string":
			instructions.append((node.start_pos, node.end_pos, "LOADIND"))

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

	arg = SyscallID.STRING_CONCAT if node.op._type == TT.ADD else SyscallID.STRING_COMPARE

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

	if node.caller.return_width > node.caller.param_width:
		for _ in range(node.caller.return_width - node.caller.param_width):
			instructions.append((node.start_pos, node.start_pos, "PUSH", 0))

	for arg, expected_type in zip(node.arguments, node.arg_types):
		arg_instr = res.register(emit_argument(arg))
		if res.error:
			return res
		instructions.extend(arg_instr)
		if is_implicit_float_cast(arg.type, expected_type):
			instructions.append((arg.end_pos, arg.end_pos, "I2F"))

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
		caller_instr = res.register(emit_argument(node.caller))
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


def emit_ProcedureCall(node: ProcedureCall) -> Result:
	res = Result()
	instructions = []

	for arg, expected_type in zip(node.arguments, node.arg_types):
		arg_instr = res.register(emit_argument(arg))
		if res.error:
			return res
		instructions.extend(arg_instr)
		if is_implicit_float_cast(arg.type, expected_type):
			instructions.append((arg.end_pos, arg.end_pos, "I2F"))


	instructions.append((node.start_pos, node.end_pos, "PUSHFP"))

	instructions.append(
		(
			node.start_pos,
			node.end_pos,
			"CALL",
			node.name,
		)
	)

	return res.success(instructions)


def emit_StructDefinition(node: StructDefinition) -> Result:
	return Result().success([])


def emit_MemberAccess(node: MemberAccess) -> Result:
	res = Result()

	if is_pointer_base(node.parent):
		addr_instructions = res.register(emit_pointer_field_address(node))
		if res.error:
			return res
		instructions = list(addr_instructions)
		instructions.append((node.start_pos, node.end_pos, "LOADIND"))
		return res.success(instructions)

	try:
		base_address, is_local = resolve_struct_base_address(node.parent)
	except AssemblyError as err:
		return res.fail(err)

	address = base_address - node.field_address if is_local else base_address + node.field_address
	opcode = "LOADSP" if is_local else "LOAD"

	return res.success(
		[
			(
				node.start_pos,
				node.end_pos,
				opcode,
				address,
			)
		]
	)


def emit_MemberAssign(node: MemberAssign) -> Result:
	res = Result()
	instructions = []

	if is_pointer_base(node.obj):
		addr_instructions = res.register(emit_pointer_field_address(node))
		if res.error:
			return res
		instructions = list(addr_instructions)

		if node.operator._type == TT.ASGN:
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

			instructions.append((node.start_pos, node.end_pos, "STREIND"))
			return res.success(instructions)

		# compound ops on a pointer-based field: duplicate the address,
		# load current value, combine, store back.
		compound_map = {
			TT.ADD_ASGN: "ADD", TT.SUB_ASGN: "SUB", TT.MUL_ASGN: "MUL",
			TT.DIV_ASGN: "DIV", TT.MOD_ASGN: "MOD", TT.POW_ASGN: "POW",
		}
		opcode = compound_map.get(node.operator._type)
		if opcode is None:
			return res.fail(
				AssemblyError(
					f"Unsupported assignment operator '{node.operator._type.name}'",
					node.start_pos, node.end_pos,
				)
			)
		opcode += "F" if (node.type.base == "float" and node.type.pointer_layers == 0) else "I"

		instructions.append((node.start_pos, node.end_pos, "DUP", 0))
		instructions.append((node.start_pos, node.end_pos, "LOADIND"))

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

		instructions.append((node.start_pos, node.end_pos, opcode))
		instructions.append((node.start_pos, node.end_pos, "STREIND"))
		return res.success(instructions)

	try:
		base_address, is_local = resolve_struct_base_address(node.obj)
	except AssemblyError as err:
		return res.fail(err)

	address = base_address - node.field_address if is_local else base_address + node.field_address
	store_opcode = "STORESP" if is_local else "STORE"
	load_opcode = "LOADSP" if is_local else "LOAD"

	if node.operator._type == TT.ASGN:
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
				store_opcode,
				address,
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
			load_opcode,
			address,
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
			store_opcode,
			address,
		)
	)

	return res.success(instructions)


def emit_method(node: FunctionDefinition | ProcedureDefinition) -> Result:
	original_name = node.name
	node.name = node.mangled_name  # temporarily swap for label emission
	try:
		return emit_FunctionDefinition(node)
	finally:
		node.name = original_name


def emit_builtin_method_call(node: MethodCall, instructions: list, builtin_id) -> Result:
	res = Result()
	syscall = METHOD_SYSCALLS.get(builtin_id)
	if syscall is not None:
		instructions.append((node.start_pos, node.end_pos, "SYS", syscall))
		return res.success(instructions)

	return res.fail(
		AssemblyError(
			f"No codegen implemented for built-in method '{node.method_name}'.",
			node.start_pos,
			node.end_pos,
		)
	)


def emit_ClassDefinition(node: ClassDefinition) -> Result:
	res = Result()
	instructions = []

	for member in node.members:
		if isinstance(member, (FunctionDefinition, ProcedureDefinition)):
			method_instructions = res.register(emit_method(member))
			if res.error:
				return res
			instructions.extend(method_instructions)

	return res.success(instructions)


def emit_MethodCall(node: MethodCall) -> Result:
	res = Result()
	instructions = []

	if node.obj_is_pointer:
		obj_instructions = res.register(emit(node.obj))
		if res.error:
			return res
		instructions.extend(obj_instructions)
	else:
		if not isinstance(node.obj, Identifier):
			return res.fail(
				AssemblyError(
					"Only a plain variable can currently be used as the receiver of a by-value method call.",
					node.obj.start_pos,
					node.obj.end_pos,
				)
			)
		instructions.append((node.start_pos, node.end_pos, "PUSH", node.obj.address))

	builtin_id = getattr(node, "builtin_id", None)

	if builtin_id is not None:
		return emit_builtin_method_call(node, instructions, builtin_id)

	for arg, expected_type in zip(node.arguments, node.arg_types):
		arg_instr = res.register(emit_argument(arg))
		if res.error:
			return res
		if is_implicit_float_cast(arg.type, expected_type):
			instructions.append((arg.end_pos, arg.end_pos, "I2F"))
		instructions.extend(arg_instr)

	instructions.append((node.start_pos, node.end_pos, "PUSHFP"))
	instructions.append((node.start_pos, node.end_pos, "CALL", node.mangled_name))

	return res.success(instructions)


def emit_NewArrayExpression(node: NewArrayExpression) -> Result:
	res = Result()
	instructions = []

	size_instructions = res.register(emit(node.size_expr))
	if res.error:
		return res
	instructions.extend(size_instructions)

	element_width = getattr(node, "element_width", 1)

	if element_width != 1:
		instructions.append((node.start_pos, node.end_pos, "PUSH", element_width))
		instructions.append((node.start_pos, node.end_pos, "MULI"))

	instructions.append((node.start_pos, node.end_pos, "SYS", SyscallID.OS_MALLOC))

	return res.success(instructions)


def emit_NewObjectExpression(node: NewObjectExpression) -> Result:
	res = Result()
	instructions = []

	size = node.struct_symbol.size

	instructions.append((node.start_pos, node.end_pos, "PUSH", size))
	instructions.append((node.start_pos, node.end_pos, "SYS", SyscallID.OS_MALLOC))

	init_method = getattr(node, "init_method", None)

	if init_method is not None:
		instructions.append((node.start_pos, node.end_pos, "DUP", 0))

		for arg, expected_type in zip(node.args, init_method.parameters):
			arg_instructions = res.register(emit_argument(arg))
			if res.error:
				return res
			
			if is_implicit_float_cast(arg.type, expected_type):
				instructions.append((node.start_pos, node.end_pos, "I2F"))
			instructions.extend(arg_instructions)

		instructions.append((node.start_pos, node.end_pos, "PUSHFP"))
		instructions.append((node.start_pos, node.end_pos, "CALL", init_method.name))

		return res.success(instructions)

	for i, arg in enumerate(node.args):
		field = node.field_list[i]

		instructions.append((node.start_pos, node.end_pos, "DUP", 0))

		if field.address:
			instructions.append((node.start_pos, node.end_pos, "PUSH", field.address))
			instructions.append((node.start_pos, node.end_pos, "ADDI"))

		arg_instructions = res.register(emit(arg))
		if res.error:
			return res
		instructions.extend(arg_instructions)

		if is_implicit_float_cast(arg.type, field.type):
			instructions.append((arg.end_pos, arg.end_pos, "I2F"))

		instructions.append((node.end_pos, node.end_pos, "STREIND"))

	return res.success(instructions)


def emit_LibraryAccess(node: LibraryAccess) -> Result:
	res = Result()

	if node.const_value is not None:
		return emit(node.const_value)

	syscall = PROPERTY_GETTER_SYSCALLS.get(getattr(node, "builtin_getter", None))
	if syscall is None:
		return res.fail(
			AssemblyError(
				f"Library member '{node.library_name}::{node.member_name}' has no constant value to emit.",
				node.start_pos,
				node.end_pos,
			)
		)

	return res.success([(node.start_pos, node.end_pos, "SYS", syscall)])


def emit_LibraryAssign(node: LibraryAssign) -> Result:
	res = Result()
	instructions = res.register(emit(node.value))
	if res.error:
		return res
	syscall = PROPERTY_SETTER_SYSCALLS.get(node.builtin_setter)
	if syscall is None:
		return res.fail(
			AssemblyError(
				f"Library property '{node.library_name}::{node.member_name}' has no setter.",
				node.start_pos,
				node.end_pos,
			)
		)
	instructions.append((node.start_pos, node.end_pos, "SYS", syscall))
	return res.success(instructions)


MATH_BUILTIN_OPCODES = {
	BuiltInID.MATH_SIN: "SINF",
	BuiltInID.MATH_COS: "COSF",
	BuiltInID.MATH_TAN: "TANF",
	BuiltInID.MATH_ASIN: "ASINF",
	BuiltInID.MATH_ACOS: "ACOSF",
	BuiltInID.MATH_ATAN: "ATANF",
}


def emit_LibraryCall(node: LibraryCall) -> Result:
	res = Result()
	instructions = []

	reference_parameters = getattr(node, "reference_parameters", ())
	for index, (arg, expected_type) in enumerate(zip(node.arguments, node.arg_types)):
		if index in reference_parameters:
			arg_instructions = [
				(arg.start_pos, arg.end_pos, "PUSH", arg.address)
			]
		else:
			arg_instructions = res.register(emit_argument(arg))
		if res.error:
			return res
		instructions.extend(arg_instructions)

		if is_implicit_float_cast(arg.type, expected_type):
			instructions.append((arg.start_pos, arg.end_pos, "I2F"))

	if node.builtin_id in MATH_BUILTIN_OPCODES:
		opcode = MATH_BUILTIN_OPCODES[node.builtin_id]
		instructions.append((node.start_pos, node.end_pos, opcode))
		return res.success(instructions)

	if node.builtin_id == BuiltInID.MATH_SQRT:
		instructions.append((node.start_pos, node.end_pos, "SQRTF"))
		return res.success(instructions)

	if node.builtin_id == BuiltInID.MATH_POW:
		instructions.append((node.start_pos, node.end_pos, "POWF"))
		return res.success(instructions)

	if node.builtin_id == BuiltInID.MATH_LERP:
		instructions.append((node.start_pos, node.end_pos, "LERPF"))
		return res.success(instructions)

	if node.builtin_id == BuiltInID.STRING_CONCAT:
		instructions.append((node.start_pos, node.end_pos, "SYS", SyscallID.STRING_CONCAT))
		return res.success(instructions)

	if node.builtin_id == BuiltInID.STRING_GET_BUFFER_PTR:
		instructions.append((node.start_pos, node.end_pos, "LOADIND"))
		return res.success(instructions)

	if node.builtin_id == BuiltInID.STRING_STRLEN:
		instructions.append((node.start_pos, node.end_pos, "INCI"))
		instructions.append((node.start_pos, node.end_pos, "LOADIND"))
		instructions.append((node.start_pos, node.end_pos, "DECI"))
		return res.success(instructions)

	if node.builtin_id == BuiltInID.STRING_UPDATE_LENGTH:
		instructions.append((node.start_pos, node.end_pos, "SYS", SyscallID.STRING_UPDATE_LENGTH))
		instructions.append((node.start_pos, node.end_pos, "POP", 1))
		return res.success(instructions)

	if node.builtin_id == BuiltInID.STRING_APPEND:
		instructions.append((node.start_pos, node.end_pos, "SYS", SyscallID.APP_STRING_APPEND))
		return res.success(instructions)

	if node.builtin_id == BuiltInID.STRING_APPEND_CHAR:
		instructions.append((node.start_pos, node.end_pos, "SYS", SyscallID.APP_STRING_APPEND_CHAR))
		return res.success(instructions)

	syscall = BUILTIN_SYSCALLS.get(node.builtin_id)
	if syscall is not None:
		instructions.append((node.start_pos, node.end_pos, "SYS", syscall))
		return res.success(instructions)

	return res.fail(
		AssemblyError(
			f"No codegen implemented for builtin '{node.library_name}::{node.member_name}'.",
			node.start_pos,
			node.end_pos,
		)
	)
