from xe_lang.helper import Token, Position, ANSI


class Node:
	def __init__(self, start_pos: Position, end_pos: Position) -> None:
		self.start_pos: Position = start_pos
		self.end_pos: Position = end_pos
		self.type: str = "none"

	def prettyprint(self, indent: int = 0, label: str = "") -> str:
		prefix = "  " * indent
		node_name = f"{ANSI.BOLD}{ANSI.BLUE}{self.__class__.__name__}{ANSI.END}"
		label_prefix = f"{ANSI.CYAN}{label}: {ANSI.END}" if label else ""

		lines = [f"{prefix}{label_prefix}{node_name}"]

		for key, value in self.__dict__.items():
			if key in (
				"start_pos",
				"end_pos",
				"left_type",
				"right_type",
				"arg_types",
				"is_local",
				"address",
			):
				continue

			if key == "type" and value == "none":
				continue

			child_indent = indent + 1
			child_prefix = "  " * child_indent

			if isinstance(value, Node):
				lines.append(value.prettyprint(child_indent, label=key))

			elif isinstance(value, list):
				if not value:
					lines.append(f"{child_prefix}{ANSI.YELLOW}{key}{ANSI.END}: []")
				else:
					lines.append(f"{child_prefix}{ANSI.YELLOW}{key}{ANSI.END}: [")
					for i, item in enumerate(value):
						if isinstance(item, Node):
							lines.append(
								item.prettyprint(child_indent + 1, label=f"[{i}]")
							)
						elif isinstance(item, tuple):
							lines.append(
								f"{child_prefix}  {ANSI.PURPLE}Case [{i}]{ANSI.END}:"
							)
							for part in item:
								if isinstance(part, Node):
									lines.append(part.prettyprint(child_indent + 2))
						else:
							lines.append(f"{child_prefix}    {item}")
					lines.append(f"{child_prefix}]")

			elif isinstance(value, tuple):
				lines.append(f"{child_prefix}{ANSI.YELLOW}{key}{ANSI.END}: {value}")

			elif value is not None:
				val_str = f"{ANSI.GREEN}{repr(value)}{ANSI.END}"
				lines.append(f"{child_prefix}{ANSI.YELLOW}{key}{ANSI.END}: {val_str}")

		return "\n".join(lines)

	def __str__(self) -> str:
		return self.prettyprint()


class Program(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		statements: list[Node],
		sub_defs: list[Node],
	):
		super().__init__(start_pos, end_pos)
		self.statements: list[Node] = statements
		self.sub_defs: list[Node] = sub_defs

	def __repr__(self):
		return f"Program({self.statements})"


class IntLiteral(Node):
	def __init__(self, start_pos: Position, end_pos: Position, value: int) -> None:
		super().__init__(start_pos, end_pos)
		self.value: int = value
		self.type: str = "int"

	def __repr__(self):
		return f"INT:{self.value}"


class FloatLiteral(Node):
	def __init__(self, start_pos: Position, end_pos: Position, value: float) -> None:
		super().__init__(start_pos, end_pos)
		self.value: float = value
		self.type: str = "float"

	def __repr__(self):
		return f"FLOAT:{self.value}"


class StringLiteral(Node):
	def __init__(self, start_pos: Position, end_pos: Position, value: str) -> None:
		super().__init__(start_pos, end_pos)
		self.value: str = value
		self.type: str = "str"

	def __repr__(self):
		return f"STR:{self.value}"


class BoolLiteral(Node):
	def __init__(self, start_pos: Position, end_pos: Position, value: bool) -> None:
		super().__init__(start_pos, end_pos)
		self.value: bool = value
		self.type: str = "bool"

	def __repr__(self):
		return f"BOOL:{self.value}"


class CharLiteral(Node):
	def __init__(self, start_pos: Position, end_pos: Position, value: str) -> None:
		super().__init__(start_pos, end_pos)
		self.value: str = value
		self.type: str = "char"

	def __repr__(self):
		return f"CHAR:{self.value}"


class Identifier(Node):
	def __init__(self, start_pos: Position, end_pos: Position, value: str) -> None:
		super().__init__(start_pos, end_pos)
		self.value: str = value
		self.type: str = "none"
		self.address: int = -1
		self.pointer_layers: int = 0
		self.is_local: bool = False
		self.const_value: Node | None = None

	def __repr__(self):
		return f"IDEN:{self.value}"


class BinaryOperation(Node):
	def __init__(
		self, start_pos: Position, end_pos: Position, left: Node, op: Token, right: Node
	) -> None:
		super().__init__(start_pos, end_pos)
		self.left: Node = left
		self.op: Token = op
		self.right: Node = right
		self.type = None
		self.left_type: str = left.type
		self.right_type: str = right.type

	def __repr__(self):
		return f"[{self.left}, {self.op}, {self.right}]"


class UnaryOperation(Node):
	def __init__(
		self, start_pos: Position, end_pos: Position, op: Token, value: Node
	) -> None:
		super().__init__(start_pos, end_pos)
		self.op: Token = op
		self.value: Node = value
		self.type: str = "none"

	def __repr__(self):
		return f"[{self.op}, {self.value}]"


class VariableDeclaration(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		name: str,
		type: str,
		pointer_layers: int,
		is_variable: bool,
	):
		super().__init__(start_pos, end_pos)
		self.name: str = name
		self.type: str = type
		self.pointer_layers: int = pointer_layers
		self.is_variable: bool = is_variable

	def __repr__(self):
		return f"DECLARE ({self.name}: {self.type})"


class VariableAssign(Node):
	def __init__(
		self, start_pos: Position, end_pos: Position, name: str, value: Node, op: Token
	):
		super().__init__(start_pos, end_pos)
		self.name: str = name
		self.operator: Token = op
		self.value: Node = value
		self.address: int = -1
		self.is_local: bool = False

	def __repr__(self):
		return f"ASSIGN ({self.name}, {self.operator}, {self.value})"


class PointerAssign(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		target: Node,
		value: Node,
		op: Token,
	):
		super().__init__(start_pos, end_pos)
		self.target: Node = target
		self.operator: Token = op
		self.value: Node = value

	def __repr__(self):
		return f"PTR_ASSIGN ({self.target}, {self.operator}, {self.value})"


class ForLoop(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		init_expr: Node,
		condition_expr: Node,
		step_expr: Node,
		body: Program,
	):
		super().__init__(start_pos, end_pos)
		self.init_expr: Node = init_expr
		self.condition_expr: Node = condition_expr
		self.step_expr: Node = step_expr
		self.body: Program = body


class WhileLoop(Node):
	def __init__(
		self, start_pos: Position, end_pos: Position, condition: Node, body: Program
	):
		super().__init__(start_pos, end_pos)
		self.condition_expr: Node = condition
		self.body: Program = body


class RepeatLoop(Node):
	def __init__(
		self, start_pos: Position, end_pos: Position, condition: Node, body: Program
	):
		super().__init__(start_pos, end_pos)
		self.condition_expr: Node = condition
		self.body: Program = body


class IfConditional(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		cases: list[tuple[Node, Program]],
		else_case: Program | None,
	):
		super().__init__(start_pos, end_pos)
		self.cases: list[tuple[Node, Program]] = cases
		self.else_case: Program | None = else_case


class SwitchStatement(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		match_expr: Node,
		cases: list[tuple[Node, Program]],
		default_case: Program | None,
	):
		super().__init__(start_pos, end_pos)
		self.match_expr: Node = match_expr
		self.cases: list[tuple[Node, Program]] = cases
		self.default_case: Program | None = default_case


class OutputStatement(Node):
	def __init__(self, start_pos: Position, end_pos: Position, values: list[Node]):
		super().__init__(start_pos, end_pos)
		self.values: list[Node] = values


class InputStatement(Node):
	def __init__(
		self, start_pos: Position, end_pos: Position, var: Identifier | UnaryOperation
	):
		super().__init__(start_pos, end_pos)
		self.var: Identifier | UnaryOperation = var


class Parameter(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		name: str,
		type: str,
		pointer_layers: int,
	):
		super().__init__(start_pos, end_pos)
		self.name: str = name
		self.type: str = type
		self.pointer_layers: int = pointer_layers


class FunctionDefinition(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		name: str,
		parameters: list[Parameter],
		return_type: str | None,
		pointer_layers: int,
		body: Program,
	):
		super().__init__(start_pos, end_pos)
		self.name: str = name
		self.parameters: list[Parameter] = parameters
		self.return_type: str | None = return_type
		self.pointer_layers: int = pointer_layers
		self.body: Program = body

		self.return_width: int = 0


class ProcedureDefinition(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		name: str,
		parameters: list[Parameter],
		body: Program,
	):
		super().__init__(start_pos, end_pos)
		self.name: str = name
		self.parameters: list[Parameter] = parameters
		self.body: Program = body

		self.return_width: int = 0


class FunctionCall(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		caller: Node,
		arguments: list[Node],
	):
		super().__init__(start_pos, end_pos)
		self.caller: Node = caller
		self.arguments: list[Node] = arguments
		self.arg_types: list = []

	def __repr__(self):
		return f"CALL_FN {self.name}({self.arguments})"


class ProcedureCall(Node):
	def __init__(
		self, start_pos: Position, end_pos: Position, name: str, arguments: list[Node]
	):
		super().__init__(start_pos, end_pos)
		self.name: str = name
		self.arguments: list[Node] = arguments
		self.arg_types: list = []

	def __repr__(self):
		return f"CALL_PROC {self.name}({self.arguments})"


class ReturnStatement(Node):
	def __init__(self, start_pos: Position, end_pos: Position, value: Node | None):
		super().__init__(start_pos, end_pos)
		self.value: Node | None = value
		self.func_name: str | None = None
		self.is_proc: bool = False
		self.param_count: int = 0


class StructDefinition(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		var: Identifier,
		fields: list[StructField],
	):
		super().__init__(start_pos, end_pos)
		self.var = var
		self.fields: list[StructField] = fields


class ClassDefinition(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		name: str,
		parent_class: str|None,
		members: list[Node],
	):
		super().__init__(start_pos, end_pos)
		self.name = name
		self.parent_class = parent_class
		self.members: list[Node] = members


class NewArrayExpression(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		type_name: str,
		pointer_layers: int,
		size_expr: Node,
	):
		super().__init__(start_pos, end_pos)
		self.type_name: str = type_name
		self.pointer_layers: int = pointer_layers
		self.size_expr: Node = size_expr


class NewObjectExpression(Node):
	def __init__(
		self, start_pos: Position, end_pos: Position, type_name: str, args: list[Node]
	):
		super().__init__(start_pos, end_pos)
		self.type_name: str = type_name
		self.args: list[Node] = args


class FreePointer(Node):
	pass


class TypeCast(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		value: Node,
		type_to_cast: str,
		pointer_layers: int,
	):
		super().__init__(start_pos, end_pos)
		self.value: Node = value
		self.type_to_cast: str = type_to_cast
		self.pointer_layers: int = pointer_layers


class ArrayDeclaration(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		name: str,
		element_type: str,
		size: Node,
		pointer_layers: int = 0,
	):
		super().__init__(start_pos, end_pos)
		self.name: str = name
		self.element_type: str = element_type
		self.size: Node = size
		self.pointer_layers: int = pointer_layers
		self.address: int = -1

	def __repr__(self):
		return f"DECLARE_ARR ({self.name}: {self.element_type}[{self.size}])"


class ArrayInitializer(Node):
	def __init__(self, start_pos: Position, end_pos: Position, elements: list[Node]):
		super().__init__(start_pos, end_pos)
		self.elements: list[Node] = elements

	def __repr__(self):
		return f"ARRAY_INIT({self.elements})"


class ArrayIndex(Node):
	def __init__(
		self, start_pos: Position, end_pos: Position, array: Node, index: Node
	):
		super().__init__(start_pos, end_pos)
		self.array: Node = array
		self.index: Node = index

	def __repr__(self):
		return f"ARR_INDEX({self.array}[{self.index}])"


class ArrayAssign(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		array: Node,
		index: Node,
		value: Node,
		op: Token,
	):
		super().__init__(start_pos, end_pos)
		self.array: Node = array
		self.index: Node = index
		self.value: Node = value
		self.operator: Token = op

	def __repr__(self):
		return f"ARR_ASSIGN({self.array}[{self.index}] {self.operator} {self.value})"


class MemberAccess(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		parent: Node,
		member: Node,
		is_arrow: bool = False
	):
		super().__init__(start_pos, end_pos)
		self.parent: Node = parent
		self.member: Node = member
		self.is_arrow: bool = is_arrow


class MemberAssign(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		obj: Node,
		member: Identifier,
		value: Node,
		op: Token,
		is_arrow: bool = False
	):
		super().__init__(start_pos, end_pos)
		self.obj: Node = obj
		self.member: Identifier = member
		self.value: Node = value
		self.operator: Token = op
		self.is_arrow: bool = is_arrow


class StructField(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		field_name: str,
		field_type: str,
		field_pointer_layers: int,
	):
		super().__init__(start_pos, end_pos)
		self.field_name: str = field_name
		self.field_type: str = field_type
		self.field_pointer_layers: int = field_pointer_layers


class StringOperation(BinaryOperation):
	def __init__(
		self, start_pos: Position, end_pos: Position, left: Node, op: Node, right: Node
	):
		super().__init__(start_pos, end_pos, left, op, right)


class MethodCall(Node):
	def __init__(
		self,
		start_pos: Position,
		end_pos: Position,
		obj: Node,
		method_name: str,
		arguments: list[Node],
		is_arrow: bool = False
	):
		super().__init__(start_pos, end_pos)
		self.obj: Node = obj
		self.method_name: str = method_name
		self.arguments: list[Node] = arguments
		self.arg_types: list = []
		self.is_arrow: bool = is_arrow

	def __repr__(self):
		return f"METHOD_CALL({self.obj}.{self.method_name}({self.arguments}))"
