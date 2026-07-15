from xe_lang.nodes import *
from xe_lang.helper import TT


LITERALS = (
    IntLiteral,
    FloatLiteral,
    BoolLiteral,
    StringLiteral,
)


class Optimizer:
	def optimize(self, node: Node) -> Node:
		method = getattr(self, f"visit_{type(node).__name__}", None)
		if method is None:
			return node
		return method(node)

	def visit_Program(self, node: Program) -> Program:
		node.statements = [self.optimize(s) for s in node.statements]
		return node

	def visit_IntLiteral(self, node: IntLiteral):
		return node

	def visit_FloatLiteral(self, node: FloatLiteral):
		return node

	def visit_BoolLiteral(self, node: BoolLiteral):
		return node

	def visit_StringLiteral(self, node: StringLiteral):
		return node

	def visit_UnaryOperation(self, node: UnaryOperation):
		node.value = self.optimize(node.value)

		# constant folding
		if isinstance(node.value, LITERALS):
			return self.fold_unary(node)

		return node

	def fold_unary(self, node: UnaryOperation):
		op = node.op._type
		value = node.value

		# don't attempt to fold pointer operators
		if op in (TT.AND, TT.MUL):
			return node

		v = value.value

		if op == TT.ADD:
			return value

		if op == TT.SUB:
			if isinstance(value, FloatLiteral):
				return FloatLiteral(node.start_pos, node.end_pos, -v)
			return IntLiteral(node.start_pos, node.end_pos, -v)
		
		if op == TT.NOT:
			return IntLiteral(node.start_pos, node.end_pos, ~v)

		if op == TT.NOTL:
			return BoolLiteral(node.start_pos, node.end_pos, not bool(v))

		return node

	def visit_BinaryOperation(self, node: BinaryOperation):
		node.left = self.optimize(node.left)
		node.right = self.optimize(node.right)

		left = node.left
		right = node.right

		# constant folding
		if isinstance(left, LITERALS) and isinstance(
			right, LITERALS
		):
			return self.fold_binary(node)

		# identity simplification
		return self.simplify(node)

	def fold_binary(self, node: BinaryOperation):
		left = node.left
		right = node.right

		a = left.value
		b = right.value
		op = node.op._type

		# string operations

		if isinstance(left, StringLiteral) and isinstance(right, StringLiteral):
			if op == TT.ADD:
				return StringLiteral(node.start_pos, node.end_pos, a + b)

			if op == TT.EQ:
				return BoolLiteral(node.start_pos, node.end_pos, a == b)

			if op == TT.NE:
				return BoolLiteral(node.start_pos, node.end_pos, a != b)

			return node

		is_float = (
			isinstance(left, FloatLiteral)
			or isinstance(right, FloatLiteral)
		)

		Literal = FloatLiteral if is_float else IntLiteral

		# arithmetic

		if op == TT.ADD:
			return Literal(node.start_pos, node.end_pos, a + b)

		if op == TT.SUB:
			return Literal(node.start_pos, node.end_pos, a - b)

		if op == TT.MUL:
			return Literal(node.start_pos, node.end_pos, a * b)

		if op == TT.DIV:
			if b != 0:
				return Literal(node.start_pos, node.end_pos, a / b if is_float else int(a / b))
			return node

		if op == TT.MOD:
			if b != 0:
				return Literal(node.start_pos, node.end_pos, a % b)
			return node

		if op == TT.POW:
			return Literal(node.start_pos, node.end_pos, a ** b)

		# comparisons

		if op == TT.LT:
			return BoolLiteral(node.start_pos, node.end_pos, a < b)

		if op == TT.LE:
			return BoolLiteral(node.start_pos, node.end_pos, a <= b)

		if op == TT.GT:
			return BoolLiteral(node.start_pos, node.end_pos, a > b)

		if op == TT.GE:
			return BoolLiteral(node.start_pos, node.end_pos, a >= b)

		if op == TT.EQ:
			return BoolLiteral(node.start_pos, node.end_pos, a == b)

		if op == TT.NE:
			return BoolLiteral(node.start_pos, node.end_pos, a != b)

		# logical

		if op == TT.ANDL:
			return BoolLiteral(node.start_pos, node.end_pos, bool(a) and bool(b))

		if op == TT.ORL:
			return BoolLiteral(node.start_pos, node.end_pos, bool(a) or bool(b))

		if op == TT.XORL:
			return BoolLiteral(node.start_pos, node.end_pos, bool(a) != bool(b))

		# bitwise (ints only)

		if (
			isinstance(left, IntLiteral)
			and isinstance(right, IntLiteral)
		):
			if op == TT.AND:
				return IntLiteral(node.start_pos, node.end_pos, a & b)

			if op == TT.OR:
				return IntLiteral(node.start_pos, node.end_pos, a | b)

			if op == TT.XOR:
				return IntLiteral(node.start_pos, node.end_pos, a ^ b)

		return node

	def simplify(self, node: BinaryOperation):
		op = node.op._type

		# x + 0
		if op == TT.ADD:
			if isinstance(node.right, IntLiteral) and node.right.value == 0:
				return node.left
			if isinstance(node.left, IntLiteral) and node.left.value == 0:
				return node.right

		# x * 1 / 0
		if op == TT.MUL:
			if isinstance(node.right, IntLiteral):
				if node.right.value == 1:
					return node.left
				if node.right.value == 0:
					return IntLiteral(node.start_pos, node.end_pos, 0)

			if isinstance(node.left, IntLiteral):
				if node.left.value == 1:
					return node.right
				if node.left.value == 0:
					return IntLiteral(node.start_pos, node.end_pos, 0)

		return node

	def visit_VariableAssign(self, node: VariableAssign):
		node.value = self.optimize(node.value)
		return node

	def visit_ForLoop(self, node: ForLoop):
		node.init_expr = self.optimize(node.init_expr)
		node.condition_expr = self.optimize(node.condition_expr)
		node.body = self.optimize(node.body)

		return node
	
	def visit_WhileLoop(self, node: WhileLoop):
		node.condition_expr = self.optimize(node.condition_expr)
		node.body = self.optimize(node.body)

		return node
	
	def visit_RepeatLoop(self, node: RepeatLoop):
		node.condition_expr = self.optimize(node.condition_expr)
		node.body = self.optimize(node.body)

		return node
	
	def visit_IfConditional(self, node: IfConditional):
		node.cases = [
			(self.optimize(condition), self.optimize(body))
			for condition, body in node.cases
		]
		if node.else_case:
			node.else_case = self.optimize(node.else_case)

		return node
	
	def visit_SwitchStatement(self, node: SwitchStatement):
		node.match_expr = self.optimize(node.match_expr)
		node.cases = [
			(self.optimize(condition), self.optimize(body))
			for condition, body in node.cases
		]
		if node.default_case:
			node.default_case = self.optimize(node.default_case)

		return node
	
	def visit_FunctionDefinition(self, node: FunctionDefinition):
		node.body = self.optimize(node.body)
		return node
	
	def visit_ProcedureDefinition(self, node: ProcedureDefinition):
		node.body = self.optimize(node.body)
		return node

	def visit_ArrayInitializer(self, node: ArrayInitializer):
		for i, elem in enumerate(node.elements):
			node.elements[i] = self.optimize(elem)
		return node
	
	def visit_StringOperation(self, node: StringOperation):
		node.left = self.optimize(node.left)
		node.right = self.optimize(node.right)

		left = node.left
		right = node.right

		if isinstance(left, LITERALS) and isinstance(
			right, LITERALS
		):
			return self.fold_binary(node)

		return node
