from xe_lang.helper import Result, SemanticError, TT
from xe_lang.nodes import *
from xe_lang.lexer import DATA_TYPES
from xe_lang.rules import BINARY_RULES, UNARY_RULES
from xe_lang.symbols import *


class SemanticAnalyzer:
	def __init__(self):
		self.scope = None
		self.next_address = 0x0000
		self.functions = {}
		self.structs = {}
		self.classes = {}

		self.current_function: SubroutineSymbol | None = None

	def push_scope(self):
		self.scope = Scope(self.scope)

	def pop_scope(self):
		self.scope = self.scope.parent

	def sizeof(self, _type: Type) -> int:
		if _type.pointer_layers > 0:
			return 1

		match _type.base:
			case "int" | "float" | "bool" | "char" | "string":
				return 1

		sym = self.scope.lookup(_type.base)

		if isinstance(sym, StructSymbol):
			return sym.size

		if isinstance(sym, ClassSymbol):
			return 1

		raise Exception(f"Unknown type {_type}")

	def analyze(self, node: Node) -> Result:
		method = getattr(
			self,
			f"visit_{type(node).__name__}",
			None,
		)

		if method is None:
			return Result().fail(
				SemanticError(
					f"No semantic handler for {type(node).__name__}",
					node.start_pos,
					node.end_pos,
				)
			)

		return method(node)

	def visit_Program(self, node: Program) -> Result:
		res = Result()
		self.push_scope()

		for defn in node.sub_defs:
			res.register(self.analyze(defn))
			if res.error:
				return res

		for stmt in node.statements:
			res.register(self.analyze(stmt))
			if res.error:
				return res

		self.pop_scope()
		return res.success(None)

	def visit_IntLiteral(self, node: IntLiteral) -> Result:
		node.type = Type("int")
		return Result().success(Type("int"))

	def visit_FloatLiteral(self, node: FloatLiteral) -> Result:
		node.type = Type("float")
		return Result().success(Type("float"))

	def visit_StringLiteral(self, node: StringLiteral) -> Result:
		node.address = self.next_address
		self.next_address += 3
		node.type = Type("string")
		return Result().success(Type("string"))

	def visit_BoolLiteral(self, node: BoolLiteral) -> Result:
		node.type = Type("bool")
		return Result().success(Type("bool"))

	def visit_CharLiteral(self, node: CharLiteral) -> Result:
		node.type = Type("char")
		return Result().success(Type("char"))

	def visit_Identifier(self, node: Identifier) -> Result:
		symbol: BaseSymbol | None = self.scope.lookup(node.value)

		if symbol is None:
			return Result().fail(
				SemanticError(
					f"Undefined variable '{node.value}'",
					node.start_pos,
					node.end_pos,
				)
			)

		node.address = symbol.address
		node.type = symbol.type
		symbol.type.parameters = symbol.parameters
		symbol.type.return_type = symbol.return_type
		node.type.parameters = symbol.parameters
		node.arr_size = symbol.arr_length
		node.is_library = symbol.is_library

		if (
			self.current_function is not None
			and node.value in self.current_function.param_names
		):
			node.is_local = True

		return Result().success(symbol.type)

	def visit_UnaryOperation(self, node: UnaryOperation) -> Result:
		res = Result()
		value_type: Type = res.register(self.analyze(node.value))
		if res.error:
			return res

		if node.op._type == TT.MUL:
			if value_type.pointer_layers == 0:
				return res.fail(
					SemanticError(
						"Cannot dereference a non-pointer.",
						node.start_pos,
						node.end_pos,
					)
				)

			result_type = Type(
				value_type.base,
				value_type.pointer_layers - 1,
			)

			node.type = result_type
			return res.success(result_type)

		if node.op._type == TT.AND:
			if not isinstance(node.value, Identifier):
				return res.fail(
					SemanticError(
						"Can only take the address of an lvalue.",
						node.start_pos,
						node.end_pos,
					)
				)

			if node.value.is_local:
				return res.fail(
					SemanticError(
						"Cannot take the address of a local variable or parameter.",
						node.start_pos,
						node.end_pos,
					)
				)

			result_type = Type(
				value_type.base,
				value_type.pointer_layers + 1,
			)

			node.type = result_type
			return res.success(result_type)

		base_type = UNARY_RULES.get((node.op._type, value_type.base))

		if base_type is None:
			return res.fail(
				SemanticError(
					(
						f"Operator '{node.op._type.name}' "
						f"is not defined for "
						f"'{value_type}'."
					),
					node.start_pos,
					node.end_pos,
				)
			)

		result_type = Type(base_type)
		node.type = result_type
		return res.success(result_type)

	def visit_BinaryOperation(self, node: BinaryOperation) -> Result:
		res = Result()

		left_type: Type = res.register(self.analyze(node.left))
		if res.error:
			return res

		right_type: Type = res.register(self.analyze(node.right))
		if res.error:
			return res

		if node.op._type in (TT.ADD, TT.SUB):
			if left_type.pointer_layers > 0 and right_type == Type("int"):
				node.type = left_type
				return res.success(left_type)

			if right_type.pointer_layers > 0 and left_type == Type("int"):
				node.type = right_type
				return res.success(right_type)

			if left_type.pointer_layers > 0 and left_type == right_type:
				result = Type("int")
				node.type = result
				return res.success(result)

		base_type = BINARY_RULES.get((node.op._type, left_type.base, right_type.base))

		if base_type is None:
			return res.fail(
				SemanticError(
					(
						f"Operator '{node.op._type.name}' "
						f"is not defined for "
						f"'{left_type}' and '{right_type}'."
					),
					node.start_pos,
					node.end_pos,
				)
			)

		result_type = Type(base_type)
		node.type = result_type

		if left_type == Type("string") and right_type == Type("string"):
			node.__class__ = StringOperation
			if node.op._type not in (TT.ADD, TT.EQ, TT.NE):
				return res.fail(
					SemanticError(
						(
							f"Operator '{node.op._type.name}' "
							f"is not defined for "
							f"'{left_type}' and '{right_type}'."
						),
						node.start_pos,
						node.end_pos,
					)
				)

		return res.success(result_type)

	def visit_VariableDeclaration(self, node: VariableDeclaration) -> Result:
		res = Result()

		if node.name in self.scope.symbols:
			return res.fail(
				SemanticError(
					f"Variable '{node.name}' already declared.",
					node.start_pos,
					node.end_pos,
				)
			)

		struct_or_class_sym = None

		if node.type not in DATA_TYPES:
			struct_or_class_sym = self.scope.lookup(node.type)
			if not isinstance(struct_or_class_sym, (StructSymbol, ClassSymbol)):
				return res.fail(
					SemanticError(
						f"Unknown type '{node.type}'.",
						node.start_pos,
						node.end_pos,
					)
				)

		symbol_type = Type(
			node.type,
			node.pointer_layers,
		)


		size = self.sizeof(symbol_type) if node.pointer_layers == 0 else 1

		if self.current_function is None:
			address = self.next_address
			self.next_address += size
			is_local = False
		else:
			address = self.current_function.next_local_offset
			self.current_function.next_local_offset -= size
			is_local = True

		var_symbol = VariableSymbol(
			node.name,
			symbol_type,
			address,
			is_local,
		)

		if isinstance(struct_or_class_sym, StructSymbol):
			var_symbol.struct_symbol = struct_or_class_sym

		self.scope.symbols[node.name] = var_symbol

		return res.success(None)

	def visit_VariableAssign(self, node: VariableAssign) -> Result:
		res = Result()

		symbol = self.scope.lookup(node.name)

		if symbol is None:
			return res.fail(
				SemanticError(
					f"Undefined variable '{node.name}'.",
					node.start_pos,
					node.end_pos,
				)
			)
		node.address = symbol.address
		node.is_local = symbol.is_local
		node.type = symbol.type

		value_type = res.register(self.analyze(node.value))

		if res.error:
			return res

		if node.operator._type == TT.ASGN:
			if value_type != symbol.type:
				# ensure both symbol and value are scalar types (pointer_layers == 0)
				# otherwise an int could be directly assigned to a float* pointer type.
				is_implicit_float_cast = (
					value_type.pointer_layers == 0
					and symbol.type.pointer_layers == 0
					and symbol.type.base == "float"
					and value_type.base == "int"
				)
				if not is_implicit_float_cast:
					return res.fail(
						SemanticError(
							f"Cannot assign '{value_type}' to '{symbol.type}'.",
							node.value.start_pos,
							node.value.end_pos,
						)
					)

			return res.success(None)

		compound_ops = {
			TT.ADD_ASGN,
			TT.SUB_ASGN,
			TT.MUL_ASGN,
			TT.DIV_ASGN,
			TT.MOD_ASGN,
			TT.POW_ASGN,
		}

		if node.operator._type in compound_ops:
			if (
				node.operator._type == TT.ADD_ASGN
				and symbol.type == Type("string")
				and value_type == Type("string")
			):
				pass
			else:
				if symbol.type.pointer_layers != 0 or symbol.type.base not in (
					"int",
					"float",
				):
					return res.fail(
						SemanticError(
							f"Operator '{node.operator._type.name}' requires numeric operands",
							node.start_pos,
							node.end_pos,
						)
					)

				if value_type.pointer_layers != 0 or value_type.base not in (
					"int",
					"float",
				):
					return res.fail(
						SemanticError(
							f"Operator '{node.operator._type.name}' requires numeric operands",
							node.start_pos,
							node.end_pos,
						)
					)

			if symbol.type == Type("string") and value_type != Type("string"):
				return res.fail(
					SemanticError(
						f"Cannot perform '{node.operator._type.name}' between '{symbol.type}' and '{value_type}'.",
						node.start_pos,
						node.end_pos,
					)
				)

			elif symbol.type == Type("int") and value_type != Type("int"):
				return res.fail(
					SemanticError(
						f"Cannot perform '{node.operator._type.name}' between '{symbol.type}' and '{value_type}'.",
						node.start_pos,
						node.end_pos,
					)
				)

			return res.success(None)

		return res.fail(
			SemanticError(
				f"Unsupported assignment operator '{node.operator._type.name}'",
				node.start_pos,
				node.end_pos,
			)
		)

	def visit_PointerAssign(self, node: PointerAssign) -> Result:
		res = Result()

		target_type = res.register(self.analyze(node.target))
		if res.error:
			return res

		value_type = res.register(self.analyze(node.value))
		if res.error:
			return res

		node.type = target_type

		if node.operator._type == TT.ASGN:
			if value_type != target_type:
				# ensure both types are scalar types (pointer_layers == 0)
				is_implicit_float_cast = (
					value_type.pointer_layers == 0
					and target_type.pointer_layers == 0
					and target_type.base == "float"
					and value_type.base == "int"
				)
				if not is_implicit_float_cast:
					return res.fail(
						SemanticError(
							f"Cannot assign '{value_type}' to a pointer target of type '{target_type}'.",
							node.value.start_pos,
							node.value.end_pos,
						)
					)
			return res.success(None)

		# compound assignments (+=, -=, etc.)
		compound_ops = {
			TT.ADD_ASGN,
			TT.SUB_ASGN,
			TT.MUL_ASGN,
			TT.DIV_ASGN,
			TT.MOD_ASGN,
			TT.POW_ASGN,
		}

		if node.operator._type in compound_ops:
			if target_type.pointer_layers != 0 or target_type.base not in (
				"int",
				"float",
			):
				return res.fail(
					SemanticError(
						f"Operator '{node.operator._type.name}' requires numeric operands.",
						node.start_pos,
						node.end_pos,
					)
				)

			if value_type.pointer_layers != 0 or value_type.base not in (
				"int",
				"float",
			):
				return res.fail(
					SemanticError(
						f"Operator '{node.operator._type.name}' requires numeric operands.",
						node.start_pos,
						node.end_pos,
					)
				)

			return res.success(None)

		return res.fail(
			SemanticError(
				f"Unsupported assignment operator '{node.operator._type.name}'",
				node.start_pos,
				node.end_pos,
			)
		)

	def visit_ForLoop(self, node: ForLoop) -> Result:
		res = Result()

		res.register(self.analyze(node.init_expr))
		if res.error:
			return res

		condition_type: Type = res.register(self.analyze(node.condition_expr))
		if res.error:
			return res

		if condition_type != Type("bool"):
			return res.fail(
				SemanticError(
					"For-loop condition must be bool.",
					node.condition_expr.start_pos,
					node.condition_expr.end_pos,
				)
			)

		res.register(self.analyze(node.step_expr))
		if res.error:
			return res

		self.push_scope()
		res.register(self.analyze(node.body))
		if res.error:
			self.pop_scope()
			return res

		self.pop_scope()
		return res.success(None)

	def visit_WhileLoop(self, node: WhileLoop) -> Result:
		res = Result()

		condition_type: Type = res.register(self.analyze(node.condition_expr))
		if res.error:
			return res

		if condition_type != Type("bool"):
			return res.fail(
				SemanticError(
					"While-loop condition must be a boolean.",
					node.condition_expr.start_pos,
					node.condition_expr.end_pos,
				)
			)

		self.push_scope()
		res.register(self.analyze(node.body))
		if res.error:
			self.pop_scope()
			return res

		self.pop_scope()
		return res.success(None)

	def visit_RepeatLoop(self, node: RepeatLoop) -> Result:
		res = Result()

		condition_type: Type = res.register(self.analyze(node.condition_expr))
		if res.error:
			return res

		if condition_type != Type("bool"):
			return res.fail(
				SemanticError(
					"Repeat-loop condition must be a boolean.",
					node.condition_expr.start_pos,
					node.condition_expr.end_pos,
				)
			)

		self.push_scope()
		res.register(self.analyze(node.body))
		if res.error:
			self.pop_scope()
			return res

		self.pop_scope()
		return res.success(None)

	def visit_IfConditional(self, node: IfConditional) -> Result:
		res = Result()

		for condition, body in node.cases:
			condition_type: Type = res.register(self.analyze(condition))
			if res.error:
				return res

			if condition_type != Type("bool"):
				return res.fail(
					SemanticError(
						"If-condition must be a boolean.",
						condition.start_pos,
						condition.end_pos,
					)
				)

			self.push_scope()
			res.register(self.analyze(body))
			if res.error:
				self.pop_scope()
				return res
			self.pop_scope()

		if node.else_case:
			self.push_scope()
			res.register(self.analyze(node.else_case))
			if res.error:
				self.pop_scope()
				return res
			self.pop_scope()

		return res.success(None)

	def visit_SwitchStatement(self, node: SwitchStatement) -> Result:
		res = Result()

		match_type: Type = res.register(self.analyze(node.match_expr))
		if res.error:
			return res

		for case_expr, body in node.cases:
			case_type: Type = res.register(self.analyze(case_expr))
			if res.error:
				return res

			if case_type != match_type:
				return res.fail(
					SemanticError(
						f"Case expression type '{case_type}' does not match switch expression type '{match_type}'.",
						case_expr.start_pos,
						case_expr.end_pos,
					)
				)

			self.push_scope()
			res.register(self.analyze(body))
			if res.error:
				self.pop_scope()
				return res
			self.pop_scope()

		if node.default_case:
			self.push_scope()
			res.register(self.analyze(node.default_case))
			if res.error:
				self.pop_scope()
				return res
			self.pop_scope()

		return res.success(None)

	def _declare_subroutine(
		self, node: FunctionDefinition | ProcedureDefinition, is_proc: bool
	) -> Result:
		res = Result()

		if node.name in self.scope.symbols:
			return res.fail(
				SemanticError(
					f"'{node.name}' is already declared.",
					node.start_pos,
					node.end_pos,
				)
			)

		return_type = None
		if not is_proc:
			if node.return_type not in DATA_TYPES:
				return res.fail(
					SemanticError(
						f"Unknown return type '{node.return_type}'.",
						node.start_pos,
						node.end_pos,
					)
				)
			return_type = Type(node.return_type, node.pointer_layers)

		param_list: list[Type] = [
			Type(p.type, p.pointer_layers) for p in node.parameters
		]
		param_names: list[str] = [p.name for p in node.parameters]
		symbol = SubroutineSymbol(
			name=node.name,
			type=Type("procedure" if is_proc else "function"),
			return_type=return_type,
			parameters=param_list,
			param_names=param_names,
			is_proc=is_proc,
		)

		self.scope.symbols[node.name] = symbol

		return res.success(None)

	def _analyze_subroutine_body(
		self, node: FunctionDefinition | ProcedureDefinition, is_proc: bool
	) -> Result:
		res = Result()

		res.register(self._declare_subroutine(node, is_proc))
		if res.error:
			return res

		self.push_scope()

		prev_function = self.current_function

		self.current_function = SubroutineSymbol(
			name=node.name,
			type=Type("procedure" if is_proc else "function"),
			return_type=None if is_proc else self.scope.parent.symbols[node.name].return_type,
			parameters=[Type(p.type, p.pointer_layers) for p in node.parameters],
			param_names=[p.name for p in node.parameters],
			is_proc=is_proc,
		)

		def bail(error: SemanticError) -> Result:
			self.pop_scope()
			self.current_function = prev_function
			return res.fail(error)

		param_count = len(node.parameters)
		for i, param in enumerate(node.parameters):
			if param.name in self.scope.symbols:
				return bail(
					SemanticError(
						f"Parameter '{param.name}' already declared.",
						param.start_pos,
						param.end_pos,
					)
				)

			if param.type not in DATA_TYPES:
				return bail(
					SemanticError(
						f"Unknown type '{param.type}'.",
						param.start_pos,
						param.end_pos,
					)
				)

			# First parameter gets the highest (furthest-from-FP) offset.
			offset = param_count - i

			self.scope.symbols[param.name] = VariableSymbol(
				name=param.name,
				type=Type(param.type, param.pointer_layers),
				address=offset,
				is_local=True,
			)

		res.register(self.analyze(node.body))
		if res.error:
			self.pop_scope()
			self.current_function = prev_function
			return res

		# next_local_offset started at -1 and was decremented once per local
		node.locals_count = -1 - self.current_function.next_local_offset
		node.param_count = param_count

		self.pop_scope()
		self.current_function = prev_function

		return res.success(None)

	def visit_FunctionDefinition(self, node: FunctionDefinition) -> Result:
		return self._analyze_subroutine_body(node, is_proc=False)

	def visit_ProcedureDefinition(self, node: ProcedureDefinition) -> Result:
		return self._analyze_subroutine_body(node, is_proc=True)

	def visit_ReturnStatement(self, node: ReturnStatement) -> Result:
		res = Result()

		if self.current_function is None:
			return res.fail(
				SemanticError(
					"'return' can only be used inside a function or procedure.",
					node.start_pos,
					node.end_pos,
				)
			)

		node.func_name = self.current_function.name
		node.is_proc = self.current_function.is_proc
		node.param_count = len(self.current_function.parameters)

		if self.current_function.is_proc:
			if node.value is not None:
				return res.fail(
					SemanticError(
						"Procedures cannot return a value.",
						node.start_pos,
						node.end_pos,
					)
				)
			return res.success(None)

		if node.value is None:
			return res.fail(
				SemanticError(
					f"Function '{node.func_name}' must return a value.",
					node.start_pos,
					node.end_pos,
				)
			)

		value_type: Type = res.register(self.analyze(node.value))
		if res.error:
			return res

		expected: Type = self.current_function.return_type

		if value_type != expected:
			is_implicit_float_cast = (
				value_type.pointer_layers == 0
				and expected.pointer_layers == 0
				and expected.base == "float"
				and value_type.base == "int"
			)
			if not is_implicit_float_cast:
				return res.fail(
					SemanticError(
						f"Cannot return '{value_type}' from a function returning '{expected}'.",
						node.value.start_pos,
						node.value.end_pos,
					)
				)

		node.type = expected
		return res.success(None)

	def _analyze_call(
		self, node: FunctionCall | ProcedureCall, expect_proc: bool
	) -> Result:
		res = Result()

		# 1. Resolve the callable type uniformly from the scope/expression
		if isinstance(node, FunctionCall):
			# Evaluates identifiers, nested calls like foo()(), etc.
			caller_type: Type = res.register(self.analyze(node.caller))
			if res.error:
				return res

			callable_name = getattr(node.caller, "value", "<anonymous_callable>")
		else:
			# ProcedureCall provides a direct string name
			caller_type = self.scope.lookup(node.name)
			if caller_type is None:
				return res.fail(
					SemanticError(
						f"Undefined procedure '{node.name}'.",
						node.start_pos,
						node.end_pos,
					)
				)
			callable_name = node.name

		# 2. Validate that the type is actually a callable function/procedure
		if not caller_type.is_callable:
			return res.fail(
				SemanticError(
					f"Expression '{callable_name}' of type '{caller_type}' is not callable.",
					node.start_pos,
					node.end_pos,
				)
			)

		# 3. Enforce function vs procedure enforcement rules
		is_proc = caller_type.is_proc
		if is_proc != expect_proc:
			kind, other = (
				("procedure", "function") if is_proc else ("function", "procedure")
			)
			return res.fail(
				SemanticError(
					f"'{callable_name}' is a {kind}; it cannot be called like a {other}.",
					node.start_pos,
					node.end_pos,
				)
			)

		# 4. Validate Argument Count
		expected_params = caller_type.parameters
		if len(node.arguments) != len(expected_params):
			return res.fail(
				SemanticError(
					f"'{callable_name}' expects {len(expected_params)} argument(s), got {len(node.arguments)}.",
					node.start_pos,
					node.end_pos,
				)
			)

		# 5. Type Check Arguments
		for i, (arg, expected_type) in enumerate(zip(node.arguments, expected_params)):
			arg_type: Type = res.register(self.analyze(arg))
			if res.error:
				return res

			if arg_type != expected_type:
				is_implicit_float_cast = (
					arg_type.pointer_layers == 0
					and expected_type.pointer_layers == 0
					and expected_type.base == "float"
					and arg_type.base == "int"
				)
				if not is_implicit_float_cast:
					return res.fail(
						SemanticError(
							f"Argument {i + 1} to '{callable_name}': cannot pass '{arg_type}' as '{expected_type}'.",
							arg.start_pos,
							arg.end_pos,
						)
					)

		node.arg_types = expected_params
		node.type = (
			caller_type.return_type
			if caller_type.return_type is not None
			else Type("none")
		)

		return res.success(node.type)

	def visit_FunctionCall(self, node: FunctionCall) -> Result:
		return self._analyze_call(node, expect_proc=False)

	def visit_ProcedureCall(self, node: ProcedureCall) -> Result:
		return self._analyze_call(node, expect_proc=True)

	def visit_OutputStatement(self, node: OutputStatement) -> Result:
		res = Result()

		for expr in node.values:
			res.register(self.analyze(expr))
			if res.error:
				return res

		return res.success(None)

	def visit_InputStatement(self, node: InputStatement) -> Result:
		res = Result()

		if isinstance(node.var, Identifier):
			symbol = self.scope.lookup(node.var.value)
			if symbol is None:
				return res.fail(
					SemanticError(
						f"Undefined variable '{node.var.value}'.",
						node.var.start_pos,
						node.var.end_pos,
					)
				)
			node.var.address = symbol.address
			node.var.is_local = symbol.is_local
			node.var.type = symbol.type
		else:
			res.register(self.analyze(node.var))
			if res.error:
				return res

		return res.success(None)

	def visit_TypeCast(self, node: TypeCast) -> Result:
		res = Result()

		expr_type: Type = res.register(self.analyze(node.value))
		if res.error:
			return res

		if node.type_to_cast not in DATA_TYPES:
			return res.fail(
				SemanticError(
					f"Unknown target type '{node.type_to_cast}'.",
					node.start_pos,
					node.end_pos,
				)
			)

		target_type = Type(node.type_to_cast, node.pointer_layers)

		if expr_type.pointer_layers != 0 or target_type.pointer_layers != 0:
			return res.fail(
				SemanticError(
					f"Cannot cast pointer types.",
					node.start_pos,
					node.end_pos,
				)
			)

		type_map = (
			("int", "float"),
			("float", "int"),
			("int", "char"),
			("char", "int"),
		)

		if (
			expr_type.base,
			target_type.base,
		) in type_map or expr_type.base == target_type.base:
			node.type = target_type
			return res.success(target_type)

		return res.fail(
			SemanticError(
				f"Cannot cast '{expr_type}' to '{target_type}'.",
				node.start_pos,
				node.end_pos,
			)
		)

	def visit_ArrayDeclaration(self, node: ArrayDeclaration) -> Result:
		res = Result()

		if node.name in self.scope.symbols:
			return res.fail(
				SemanticError(
					f"Array '{node.name}' already declared.",
					node.start_pos,
					node.end_pos,
				)
			)

		if node.element_type not in DATA_TYPES:
			return res.fail(
				SemanticError(
					f"Unknown array element type '{node.element_type}'.",
					node.start_pos,
					node.end_pos,
				)
			)

		size_type = res.register(self.analyze(node.size))
		if res.error:
			return res

		if size_type != Type("int"):
			return res.fail(
				SemanticError(
					f"Array size must be an integer, got '{size_type}'.",
					node.size.start_pos,
					node.size.end_pos,
				)
			)

		symbol_type = Type(node.element_type, node.pointer_layers + 1, True)

		if self.current_function is None:
			address = self.next_address
			self.next_address += 1
			is_local = False
		else:
			address = self.current_function.next_local_offset
			self.current_function.next_local_offset -= 1
			is_local = True

		node.address = address
		self.scope.symbols[node.name] = VariableSymbol(
			node.name, symbol_type, address, is_local, node.size.value
		)

		return res.success(None)

	def visit_ArrayInitializer(self, node):
		res = Result()

		element_type = None

		for elem in node.elements:
			t = res.register(self.analyze(elem))
			if res.error:
				return res

			if element_type is None:
				element_type = t
			elif t != element_type:
				return res.failure(
					SemanticError("Array elements must have the same type")
				)

		node.type = Type(
			element_type.base, element_type.pointer_layers + 1, is_array=True
		)

		return res.success(node.type)

	def visit_ArrayIndex(self, node: ArrayIndex) -> Result:
		res = Result()

		array_type = res.register(self.analyze(node.array))
		if res.error:
			return res

		index_type = res.register(self.analyze(node.index))
		if res.error:
			return res

		if index_type != Type("int"):
			return res.fail(
				SemanticError(
					f"Array index must be an integer, got '{index_type}'.",
					node.index.start_pos,
					node.index.end_pos,
				)
			)

		if array_type.pointer_layers == 0 and array_type.base != "string":
			return res.fail(
				SemanticError(
					f"Cannot index a non-array type '{array_type}'.",
					node.array.start_pos,
					node.array.end_pos,
				)
			)
		elif array_type.base == "string":
			result_type = Type("string")
		else:
			result_type = Type(array_type.base, min(array_type.pointer_layers - 1, 0))
		node.type = result_type
		return res.success(result_type)

	def visit_ArrayAssign(self, node: ArrayAssign) -> Result:
		res = Result()

		array_type = res.register(self.analyze(node.array))
		if res.error:
			return res

		if array_type.pointer_layers == 0:
			return res.fail(
				SemanticError(
					f"Cannot index a non-array type '{array_type}'.",
					node.array.start_pos,
					node.array.end_pos,
				)
			)

		index_type = res.register(self.analyze(node.index))
		if res.error:
			return res

		if index_type != Type("int"):
			return res.fail(
				SemanticError(
					f"Array index must be an integer, got '{index_type}'.",
					node.index.start_pos,
					node.index.end_pos,
				)
			)

		value_type = res.register(self.analyze(node.value))
		if res.error:
			return res

		# Element type
		element_type = Type(array_type.base, array_type.pointer_layers - 1)
		node.type = element_type

		if node.operator._type == TT.ASGN:
			if value_type != element_type:
				# Allow implicit int-to-float cast
				is_implicit_float_cast = (
					value_type.pointer_layers == 0
					and element_type.pointer_layers == 0
					and element_type.base == "float"
					and value_type.base == "int"
				)
				if not is_implicit_float_cast:
					return res.fail(
						SemanticError(
							f"Cannot assign '{value_type}' to array element of type '{element_type}'.",
							node.value.start_pos,
							node.value.end_pos,
						)
					)
			return res.success(None)

		compound_ops = {
			TT.ADD_ASGN,
			TT.SUB_ASGN,
			TT.MUL_ASGN,
			TT.DIV_ASGN,
			TT.MOD_ASGN,
			TT.POW_ASGN,
		}

		if node.operator._type in compound_ops:
			if element_type.pointer_layers != 0 or element_type.base not in (
				"int",
				"float",
			):
				return res.fail(
					SemanticError(
						f"Operator '{node.operator._type.name}' requires numeric operands.",
						node.start_pos,
						node.end_pos,
					)
				)

			if value_type.pointer_layers != 0 or value_type.base not in (
				"int",
				"float",
			):
				return res.fail(
					SemanticError(
						f"Operator '{node.operator._type.name}' requires numeric operands.",
						node.start_pos,
						node.end_pos,
					)
				)

			return res.success(None)

		return res.fail(
			SemanticError(
				f"Unsupported assignment operator '{node.operator._type.name}'",
				node.start_pos,
				node.end_pos,
			)
		)

	def visit_StructDefinition(self, node: StructDefinition) -> Result:
		res = Result()

		name = node.var.value

		if name in self.scope.symbols:
			return res.fail(
				SemanticError(
					f"Redefinition of '{name}'",
					node.start_pos,
					node.end_pos,
				)
			)

		struct = StructSymbol(
			name=name,
			type=Type(name),
		)

		self.scope.symbols[name] = struct

		offset = 0

		for field in node.fields:
			if field.field_name in struct.fields:
				return res.fail(
					SemanticError(
						f"Duplicate field '{field.field_name}' in struct '{name}'",
						field.start_pos,
						field.end_pos,
					)
				)

			field_type = Type(
				field.field_type,
				field.field_pointer_layers,
			)

			if field_type.pointer_layers == 0:
				sym = self.scope.lookup(field_type.base)

				if (
					sym is None
					and field_type.base not in {
						"int",
						"float",
						"bool",
						"char",
						"string",
					}
				):
					return res.fail(
						SemanticError(
							f"Unknown type '{field_type.base}'",
							field.start_pos,
							field.end_pos,
						)
					)

			struct.fields[field.field_name] = VariableSymbol(
				name=field.field_name,
				type=field_type,
				address=offset,
			)

			offset += self.sizeof(field_type)

		struct.size = offset

		node.symbol = struct

		return res.success(None)

	def visit_MemberAccess(self, node: MemberAccess) -> Result:
		res = Result()

		parent_type: Type = res.register(self.analyze(node.parent))
		if res.error:
			return res

		if parent_type.pointer_layers > 0:
			return res.fail(
				SemanticError(
					f"Cannot access member of a pointer type '{parent_type}'. Dereference it first.",
					node.start_pos,
					node.end_pos,
				)
			)

		struct_sym = self.scope.lookup(parent_type.base)

		if isinstance(struct_sym, StructSymbol):
			member_name = node.member.value
			field: VariableSymbol | None = struct_sym.fields.get(member_name)

			if field is None:
				return res.fail(
					SemanticError(
						f"Struct '{parent_type.base}' has no field named '{member_name}'.",
						node.member.start_pos,
						node.member.end_pos,
					)
				)

			node.type = field.type
			node.field_address = field.address
			node.struct_symbol = struct_sym
			return res.success(field.type)

		if parent_type.base in self.classes:
			member_name = node.member.value
			current_class = parent_type.base

			while current_class is not None:
				class_info = self.classes.get(current_class)
				if class_info and member_name in class_info["members"]:
					result_type = class_info["members"][member_name]
					node.type = result_type
					return res.success(result_type)
				current_class = class_info["parent"] if class_info else None

			return res.fail(
				SemanticError(
					f"Class '{parent_type.base}' has no member named '{member_name}'.",
					node.member.start_pos,
					node.member.end_pos,
				)
			)

		return res.fail(
			SemanticError(
				f"Type '{parent_type.base}' does not support member access.",
				node.start_pos,
				node.end_pos,
			)
		)
	
	def visit_MemberAssign(self, node: MemberAssign) -> Result:
		res = Result()

		parent_type: Type = res.register(self.analyze(node.obj))
		if res.error:
			return res

		if parent_type.pointer_layers > 0:
			return res.fail(
				SemanticError(
					f"Cannot access member of a pointer type '{parent_type}'. Dereference it first.",
					node.start_pos,
					node.end_pos,
				)
			)

		struct_sym = self.scope.lookup(parent_type.base)
		member_name = node.member.value
		field_type: Type | None = None

		if isinstance(struct_sym, StructSymbol):
			field: VariableSymbol | None = struct_sym.fields.get(member_name)
			if field is None:
				return res.fail(
					SemanticError(
						f"Struct '{parent_type.base}' has no field named '{member_name}'.",
						node.member.start_pos,
						node.member.end_pos,
					)
				)
			field_type = field.type
			node.field_address = field.address
			node.struct_symbol = struct_sym

		elif parent_type.base in self.classes:
			current_class = parent_type.base
			while current_class is not None:
				class_info = self.classes.get(current_class)
				if class_info and member_name in class_info["members"]:
					field_type = class_info["members"][member_name]
					break
				current_class = class_info["parent"] if class_info else None

			if field_type is None:
				return res.fail(
					SemanticError(
						f"Class '{parent_type.base}' has no member named '{member_name}'.",
						node.member.start_pos,
						node.member.end_pos,
					)
				)
		else:
			return res.fail(
				SemanticError(
					f"Type '{parent_type.base}' does not support member access.",
					node.start_pos,
					node.end_pos,
				)
			)

		node.type = field_type

		value_type: Type = res.register(self.analyze(node.value))
		if res.error:
			return res

		if node.operator._type == TT.ASGN:
			if value_type != field_type:
				is_implicit_float_cast = (
					value_type.pointer_layers == 0
					and field_type.pointer_layers == 0
					and field_type.base == "float"
					and value_type.base == "int"
				)
				if not is_implicit_float_cast:
					return res.fail(
						SemanticError(
							f"Cannot assign '{value_type}' to member '{member_name}' of type '{field_type}'.",
							node.value.start_pos,
							node.value.end_pos,
						)
					)
			return res.success(None)

		compound_ops = {
			TT.ADD_ASGN,
			TT.SUB_ASGN,
			TT.MUL_ASGN,
			TT.DIV_ASGN,
			TT.MOD_ASGN,
			TT.POW_ASGN,
		}

		if node.operator._type in compound_ops:
			if field_type.pointer_layers != 0 or field_type.base not in ("int", "float"):
				return res.fail(
					SemanticError(
						f"Operator '{node.operator._type.name}' requires numeric operands.",
						node.start_pos,
						node.end_pos,
					)
				)

			if value_type.pointer_layers != 0 or value_type.base not in ("int", "float"):
				return res.fail(
					SemanticError(
						f"Operator '{node.operator._type.name}' requires numeric operands.",
						node.start_pos,
						node.end_pos,
					)
				)

			return res.success(None)

		return res.fail(
			SemanticError(
				f"Unsupported assignment operator '{node.operator._type.name}'",
				node.start_pos,
				node.end_pos,
			)
		)
	
	def visit_ClassDefinition(self, node: ClassDefinition) -> Result:
		res = Result()

		if not hasattr(self, "classes"):
			self.classes = {}

		if node.name in self.classes:
			return res.fail(
				SemanticError(
					f"Class '{node.name}' already defined.",
					node.start_pos,
					node.end_pos,
				)
			)

		if node.parent_class and node.parent_class not in self.classes:
			return res.fail(
				SemanticError(
					f"Base class '{node.parent_class}' is undefined.",
					node.start_pos,
					node.end_pos,
				)
			)

		class_members = {}
		self.classes[node.name] = {
			"parent": node.parent_class,
			"members": class_members,
		}

		self.push_scope()

		for member in node.members:
			res.register(self.analyze(member))
			if res.error:
				self.pop_scope()
				return res

			if isinstance(member, VariableDeclaration):
				class_members[member.name] = Type(member.type, member.pointer_layers)
			elif isinstance(member, ArrayDeclaration):
				class_members[member.name] = Type(
					member.element_type, member.pointer_layers + 1, True
				)
			elif isinstance(member, FunctionDefinition):
				class_members[member.name] = Type(
					member.return_type, member.pointer_layers
				)
			elif isinstance(member, ProcedureDefinition):
				class_members[member.name] = Type("none")

		self.pop_scope()
		return res.success(None)

	def visit_NewArrayExpression(self, node: NewArrayExpression) -> Result:
		res = Result()

		if (
			node.type_name not in DATA_TYPES
			and (not hasattr(self, "structs") or node.type_name not in self.structs)
			and (not hasattr(self, "classes") or node.type_name not in self.classes)
		):
			return res.fail(
				SemanticError(
					f"Unknown base allocation type '{node.type_name}'.",
					node.start_pos,
					node.end_pos,
				)
			)

		size_type = res.register(self.analyze(node.size_expr))
		if res.error:
			return res

		if size_type != Type("int"):
			return res.fail(
				SemanticError(
					f"Allocation bounds array size must be an integer, got '{size_type}'.",
					node.size_expr.start_pos,
					node.size_expr.end_pos,
				)
			)

		allocated_type = Type(node.type_name, node.pointer_layers + 1)
		node.type = allocated_type
		return res.success(allocated_type)

	def visit_NewObjectExpression(self, node: NewObjectExpression) -> Result:
		res = Result()

		if not hasattr(self, "classes") or node.type_name not in self.classes:
			return res.fail(
				SemanticError(
					f"Unknown type template or class target '{node.type_name}'.",
					node.start_pos,
					node.end_pos,
				)
			)

		for arg in node.args:
			res.register(self.analyze(arg))
			if res.error:
				return res

		allocated_type = Type(node.type_name, 1)
		node.type = allocated_type
		return res.success(allocated_type)

	def visit_FreePointer(self, node: FreePointer) -> Result:
		res = Result()

		target_type = res.register(self.analyze(node.target))
		if res.error:
			return res

		if target_type.pointer_layers == 0:
			return res.fail(
				SemanticError(
					f"Cannot free non-pointer expression of type '{target_type}'.",
					node.target.start_pos,
					node.target.end_pos,
				)
			)

		return res.success(None)


def analyze(ast: Node) -> Result:
	analyzer = SemanticAnalyzer()
	return analyzer.analyze(ast)
