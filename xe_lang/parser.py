from xe_lang.helper import TT, Token, InvalidSyntaxError, Result, Position
from xe_lang.nodes import *


def parse(tokens: list[Token]) -> Result:
	# initialize
	tok_idx = -1
	current_tok: Token = None

	def advance():
		nonlocal tok_idx, current_tok
		tok_idx += 1
		if tok_idx < len(tokens):
			current_tok = tokens[tok_idx]

	def peek(offset: int = 1) -> Token | None:
		idx = tok_idx + offset
		if idx < len(tokens):
			return tokens[idx]
		return None

	advance()

	# helper subroutines
	def make_binary_op(token_types: list[TT], left_func, right_func=None) -> Result:
		if right_func == None:
			right_func = left_func

		res: Result = Result()
		left: Node = res.register(left_func())
		if res.error:
			return res

		while current_tok._type in token_types:
			op: Token = current_tok
			advance()

			right: Node = res.register(right_func())
			if res.error:
				return res

			left = BinaryOperation(left.start_pos, right.end_pos, left, op, right)

		return res.success(left)

	def lookahead_for_token(token: Token) -> bool:
		# this skips every newline and semicolon token in the lookahead search
		# then checks if the current token is the same as the target token
		nonlocal tok_idx
		lookahead_idx = tok_idx
		while lookahead_idx < len(tokens):
			lookahead_tok = tokens[lookahead_idx]
			if lookahead_tok._type in (TT.NEWLINE, TT.SEMICOL):
				lookahead_idx += 1
				continue
			return lookahead_tok == token

	# parse subroutines
	def program(terminate: TT | None = None) -> Result:
		res = Result()
		statements: list[Node] = []
		definitions: list[Node] = []

		DEFINITION_TYPES = (
			FunctionDefinition,
			ProcedureDefinition,
			StructDefinition,
			ClassDefinition
		)

		while current_tok._type in (TT.NEWLINE, TT.SEMICOL):
			advance()

		if current_tok._type == TT.EOF:
			return res.success(Program(current_tok.start_pos, current_tok.end_pos, [], []))

		while current_tok._type != TT.EOF:

			if terminate and current_tok._type == terminate:
				break

			start_idx = tok_idx

			stmt = res.register(statement())
			if res.error:
				return res

			if tok_idx == start_idx:
				return res.fail(
					InvalidSyntaxError(
						f"Parser stalled: infinite loop detected. Stuck on token '{current_tok.value or current_tok._type.name}'.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)

			if isinstance(stmt, DEFINITION_TYPES):
				definitions.append(stmt)
			else:
				statements.append(stmt)

			if (
				current_tok._type not in (TT.NEWLINE, TT.SEMICOL, TT.EOF)
				and current_tok._type != terminate
			):
				return res.fail(
					InvalidSyntaxError(
						f"Expected a newline or ';' to separate statements, but found unexpected trailing token '{current_tok.value or current_tok._type.name}'.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)

			while current_tok._type in (TT.NEWLINE, TT.SEMICOL):
				advance()

		if not statements and not definitions:
			return res.success(Program(current_tok.start_pos, current_tok.end_pos, [], []))

		return res.success(
			Program(
				statements[0].start_pos,
				statements[-1].end_pos,
				statements,
				definitions
			)
		)

	def statement() -> Result:
		if current_tok._type == TT.KEYWORD:
			match current_tok.value:
				case "var":
					return var_declaration()
				case "array":
					return array_declaration()
				case "for":
					return for_loop()
				case "while":
					return while_loop()
				case "repeat":
					return repeat_loop()
				case "if":
					return if_conditional()
				case "switch":
					return switch_statement()
				case "out":
					return output_statement()
				case "in":
					return input_statement()
				case "proc":
					return procedure_definition()
				case "fn":
					return function_definition()
				case "struct":
					return struct_definition()
				case "class":
					return class_definition()
				case "return":
					return return_statement()
				case "call":
					return procedure_call()

		return expr()

	def var_declaration() -> Result:
		start_pos: Position = current_tok.start_pos.copy()
		res: Result = Result()

		advance()
		if current_tok._type != TT.IDENT:
			return res.fail(
				InvalidSyntaxError(
					f"Expected variable name identifier after 'var' keyword, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		iden_name: str = current_tok.value
		advance()

		if current_tok._type != TT.COL:
			return res.fail(
				InvalidSyntaxError(
					f"Expected standard type assignment symbol ':' after variable name '{iden_name}', but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		# allow either a builtin TYPE token (int, float, ...) or an IDENT
		# referring to a user-defined struct/class name (e.g. Vec2)
		if current_tok._type not in (TT.TYPE, TT.IDENT):
			return res.fail(
				InvalidSyntaxError(
					f"Expected a valid data type (e.g., int, float) or a struct/class name after variable name '{iden_name}', but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		type_name: str = current_tok.value
		is_variable: bool = current_tok._type == TT.IDENT
		end_pos: Position = current_tok.end_pos.copy()
		advance()

		pointer_layers: int = 0

		while current_tok._type in (TT.MUL, TT.POW):
			if current_tok._type == TT.MUL:
				pointer_layers += 1
			else:
				pointer_layers += 2
			end_pos = current_tok.end_pos.copy()
			advance()

		if current_tok._type not in (TT.EOF, TT.NEWLINE, TT.SEMICOL, TT.RBR):
			return res.fail(
				InvalidSyntaxError(
					f"Expected end of line (newline or ';') after variable declaration block, but found unexpected trailing token '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		return res.success(
			VariableDeclaration(
				start_pos, end_pos, iden_name, type_name, pointer_layers, is_variable
			)
    )

	def array_declaration() -> Result:
		start_pos: Position = current_tok.start_pos.copy()
		res: Result = Result()

		advance()  # consume 'array'

		if current_tok._type != TT.IDENT:
			return res.fail(
				InvalidSyntaxError(
					f"Expected array name after 'array' keyword, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		arr_name: str = current_tok.value
		advance()

		if current_tok._type != TT.COL:
			return res.fail(
				InvalidSyntaxError(
					f"Expected ':' after array name '{arr_name}', but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		if current_tok._type != TT.TYPE:
			return res.fail(
				InvalidSyntaxError(
					f"Expected type after ':', but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		element_type: str = current_tok.value
		advance()

		pointer_layers: int = 0
		while current_tok._type in (TT.MUL, TT.POW):
			if current_tok._type == TT.MUL:
				pointer_layers += 1
			else:
				pointer_layers += 2
			advance()

		if current_tok._type != TT.LSQ:
			return res.fail(
				InvalidSyntaxError(
					f"Expected '[' for array size, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		size_expr = res.register(literal())
		if res.error:
			return res

		if current_tok._type != TT.RSQ:
			return res.fail(
				InvalidSyntaxError(
					f"Expected ']' after array size, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		end_pos: Position = current_tok.end_pos.copy()
		advance()

		if current_tok._type not in (TT.EOF, TT.NEWLINE, TT.SEMICOL, TT.RBR):
			return res.fail(
				InvalidSyntaxError(
					f"Expected end of line (newline or ';') after variable declaration block, but found unexpected trailing token '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		return res.success(
			ArrayDeclaration(
				start_pos, end_pos, arr_name, element_type, size_expr, pointer_layers
			)
		)

	def for_loop() -> Result:
		start_pos: Position = current_tok.start_pos.copy()
		res: Result = Result()

		advance()

		if current_tok._type != TT.LPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected opening parenthesis '(' after 'for' loop declaration, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		init_expr: Node = res.register(expr())
		if res.error:
			return res

		if current_tok._type != TT.SEMICOL:
			return res.fail(
				InvalidSyntaxError(
					f"Expected a statement separator ';' following for loop initialization, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		condition_expr: Node = res.register(expr())
		if res.error:
			return res

		if current_tok._type != TT.SEMICOL:
			return res.fail(
				InvalidSyntaxError(
					f"Expected a statement separator ';' following for loop condition, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		step_expr: Node = res.register(expr())
		if res.error:
			return res

		if current_tok._type != TT.RPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected a closing parenthesis ')' after for loop configuration, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()
		if current_tok._type != TT.LBR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected an opening curly brace '{{' before for loop body, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		body: Program = res.register(program(TT.RBR))
		if res.error:
			return res

		if current_tok._type != TT.RBR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected a closing curly brace '}}' after for loop body, but reached end of scope on '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		end_pos: Position = current_tok.end_pos.copy()
		advance()
		return res.success(
			ForLoop(start_pos, end_pos, init_expr, condition_expr, step_expr, body)
		)

	def while_loop() -> Result:
		start_pos: Position = current_tok.start_pos.copy()
		res: Result = Result()

		advance()

		if current_tok._type != TT.LPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected opening parenthesis '(' after while loop declaration, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		condition_expr: Node = res.register(expr())
		if res.error:
			return res

		if current_tok._type != TT.RPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected a closing parenthesis ')' after while loop condition, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()
		if current_tok._type != TT.LBR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected an opening curly brace '{{' before while loop body, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()
		body: Program = res.register(program(TT.RBR))
		if res.error:
			return res
		if current_tok._type != TT.RBR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected a closing curly brace '}}' after while loop body, but reached end of scope on '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		end_pos: Position = current_tok.end_pos.copy()
		advance()
		return res.success(WhileLoop(start_pos, end_pos, condition_expr, body))

	def repeat_loop() -> Result:
		start_pos: Position = current_tok.start_pos.copy()
		res: Result = Result()

		advance()

		if current_tok._type != TT.LBR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected opening curly brace '{{' after repeat loop declaration, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()
		body: Program = res.register(program(TT.RBR))
		if res.error:
			return res
		if current_tok._type != TT.RBR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected a closing curly brace '}}' after repeat loop body, but reached end of scope on '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		if not lookahead_for_token(Token(TT.KEYWORD, "until", None, None)):
			return res.fail(
				InvalidSyntaxError(
					f"Expected 'until' keyword after repeat loop body, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		while current_tok._type in (TT.NEWLINE, TT.SEMICOL):
			advance()
		advance()  # another advance to skip the 'until' keyword

		if current_tok._type != TT.LPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected opening parenthesis '(' after 'until', but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		condition_expr: Node = res.register(expr())
		if res.error:
			return res

		if current_tok._type != TT.RPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected a closing parenthesis ')' after repeat loop condition, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		end_pos: Position = condition_expr.end_pos.copy()
		advance()

		return res.success(RepeatLoop(start_pos, end_pos, condition_expr, body))

	def if_conditional() -> Result:
		start_pos: Position = current_tok.start_pos.copy()
		res: Result = Result()
		cases: list[tuple[Node, Program]] = []
		else_case: Program | None = None

		advance()

		if current_tok._type != TT.LPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected opening parenthesis '(' before if conditional, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		condition_expr: Node = res.register(expr())
		if res.error:
			return res
		if current_tok._type != TT.RPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected a closing parenthesis ')' after if conditional, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()
		if current_tok._type != TT.LBR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected an opening curly brace '{{' to define the start of if body, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()
		body: Program = res.register(program(TT.RBR))
		if res.error:
			return res
		if current_tok._type != TT.RBR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected a closing curly brace '}}' after if body, but reached end of scope on '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		end_pos: Position = current_tok.end_pos.copy()
		advance()
		cases.append((condition_expr, body))

		while lookahead_for_token(Token(TT.KEYWORD, "elseif", None, None)):
			while current_tok._type in (TT.NEWLINE, TT.SEMICOL):
				advance()
			advance()  # another advance to skip the 'elseif' keyword
			if current_tok._type != TT.LPR:
				return res.fail(
					InvalidSyntaxError(
						f"Expected opening parenthesis '(' before elseif conditional, but found '{current_tok.value or current_tok._type.name}'.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)
			advance()

			condition_expr: Node = res.register(expr())
			if res.error:
				return res
			if current_tok._type != TT.RPR:
				return res.fail(
					InvalidSyntaxError(
						f"Expected a closing parenthesis ')' after elseif conditional, but found '{current_tok.value or current_tok._type.name}'.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)
			advance()
			if current_tok._type != TT.LBR:
				return res.fail(
					InvalidSyntaxError(
						f"Expected an opening curly brace '{{' to define the start of elseif body, but found '{current_tok.value or current_tok._type.name}'.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)
			advance()
			body: Program = res.register(program(TT.RBR))
			if res.error:
				return res
			if current_tok._type != TT.RBR:
				return res.fail(
					InvalidSyntaxError(
						f"Expected a closing curly brace '}}' after elseif body, but reached end of scope on '{current_tok.value or current_tok._type.name}'.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)
			end_pos: Position = current_tok.end_pos.copy()
			advance()
			cases.append((condition_expr, body))

		if lookahead_for_token(Token(TT.KEYWORD, "else", None, None)):
			while current_tok._type in (TT.NEWLINE, TT.SEMICOL):
				advance()
			advance()  # another advance to skip the 'else' keyword
			if current_tok._type != TT.LBR:
				return res.fail(
					InvalidSyntaxError(
						f"Expected an opening curly brace '{{' to define the start of else body, but found '{current_tok.value or current_tok._type.name}'.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)
			advance()
			else_case: Program = res.register(program(TT.RBR))
			if res.error:
				return res
			if current_tok._type != TT.RBR:
				return res.fail(
					InvalidSyntaxError(
						f"Expected a closing curly brace '}}' after else body, but reached end of scope on '{current_tok.value or current_tok._type.name}'.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)
			end_pos: Position = current_tok.end_pos.copy()
			advance()

		return res.success(IfConditional(start_pos, end_pos, cases, else_case))

	def switch_statement() -> Result:
		start_pos = current_tok.start_pos.copy()
		res = Result()

		cases: list[tuple[Node, Program]] = []
		default_case: Program | None = None
		seen_cases = set()

		advance()

		if current_tok._type != TT.LPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected '(' after 'switch', but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		match_expr = res.register(expr())
		if res.error:
			return res

		if current_tok._type != TT.RPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected ')' after switch expression, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		if current_tok._type != TT.LBR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected '{{' to begin switch body, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		while True:
			while current_tok._type in (TT.NEWLINE, TT.SEMICOL):
				advance()

			if current_tok._type == TT.RBR:
				break

			if current_tok == Token(TT.KEYWORD, "case", None, None):
				if default_case is not None:
					return res.fail(
						InvalidSyntaxError(
							"No 'case' labels may appear after 'default'.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)

				advance()

				case_expr = res.register(expr())
				if res.error:
					return res

				if not isinstance(
					case_expr,
					(
						IntLiteral,
						FloatLiteral,
						StringLiteral,
						BoolLiteral,
					),
				):
					return res.fail(
						InvalidSyntaxError(
							"Switch case labels must be constant literals.",
							case_expr.start_pos,
							case_expr.end_pos,
						)
					)

				if case_expr.value in seen_cases:
					return res.fail(
						InvalidSyntaxError(
							f"Duplicate switch case '{case_expr.value}'.",
							case_expr.start_pos,
							case_expr.end_pos,
						)
					)

				seen_cases.add(case_expr.value)

				if current_tok._type != TT.LBR:
					return res.fail(
						InvalidSyntaxError(
							f"Expected '{{' after case label, but found '{current_tok.value or current_tok._type.name}'.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)
				advance()

				body = res.register(program(TT.RBR))
				if res.error:
					return res

				if current_tok._type != TT.RBR:
					return res.fail(
						InvalidSyntaxError(
							"Expected '}' after case body.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)

				advance()

				cases.append((case_expr, body))
				continue

			if current_tok == Token(TT.KEYWORD, "default", None, None):
				if default_case is not None:
					return res.fail(
						InvalidSyntaxError(
							"Switch statement may contain only one default case.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)

				advance()

				if current_tok._type != TT.LBR:
					return res.fail(
						InvalidSyntaxError(
							f"Expected '{{' after 'default', but found '{current_tok.value or current_tok._type.name}'.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)
				advance()

				default_case = res.register(program(TT.RBR))
				if res.error:
					return res

				if current_tok._type != TT.RBR:
					return res.fail(
						InvalidSyntaxError(
							"Expected '}' after default body.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)

				advance()
				continue

			return res.fail(
				InvalidSyntaxError(
					"Expected 'case', 'default', or '}' inside switch statement.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		end_pos = current_tok.end_pos.copy()
		advance()

		return res.success(
			SwitchStatement(
				start_pos,
				end_pos,
				match_expr,
				cases,
				default_case,
			)
		)

	def output_statement() -> Result:
		start_pos = current_tok.start_pos.copy()
		res = Result()
		values: list[Node] = []

		advance()  # consume 'out'

		while lookahead_for_token(Token(TT.OSTREAM, None, None, None)):
			while current_tok._type in (TT.NEWLINE, TT.SEMICOL):
				advance()
			advance()

			value: Node = res.register(logical_or())
			if res.error:
				return res

			values.append(value)
			end_pos: Position = current_tok.end_pos.copy()

		if current_tok._type not in (TT.NEWLINE, TT.SEMICOL, TT.EOF):
			return res.fail(
				InvalidSyntaxError(
					f"Expected EOL or '<<', found {current_tok.value or current_tok._type.name} instead.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		return res.success(OutputStatement(start_pos, end_pos, values))

	def input_statement() -> Result:
		start_pos = current_tok.start_pos.copy()
		res = Result()

		advance()  # consume 'in'

		if not lookahead_for_token(Token(TT.ISTREAM, None, None, None)):
			return res.fail(
				InvalidSyntaxError(
					f"Expected '>>' after 'in', found {current_tok.value or current_tok._type.name} instead.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		while current_tok._type in (TT.NEWLINE, TT.SEMICOL):
			advance()
		advance()

		var: Node = res.register(expr())
		if not isinstance(var, (Identifier, UnaryOperation)):
			return res.fail(
				InvalidSyntaxError(
					f"Expected a variable or dereference after 'in', found {type(var).__name__} instead.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		if isinstance(var, UnaryOperation) and var.op._type != TT.MUL:
			return res.fail(
				InvalidSyntaxError(
					f"Expected a variable or dereference after 'in', found {type(var).__name__} instead.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		if current_tok._type not in (TT.NEWLINE, TT.SEMICOL, TT.EOF):
			return res.fail(
				InvalidSyntaxError(
					f"Expected EOL, found {current_tok.value or current_tok._type.name} instead.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		return res.success(InputStatement(start_pos, var.end_pos.copy(), var))

	def procedure_definition() -> Result:
		start_pos = current_tok.start_pos.copy()
		res = Result()

		advance()  # consume 'proc'

		# procedure name
		if current_tok._type != TT.IDENT:
			return res.fail(
				InvalidSyntaxError(
					"Expected procedure name after 'proc'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		name = current_tok.value
		advance()

		if current_tok._type != TT.LPR:
			return res.fail(
				InvalidSyntaxError(
					"Expected '(' after procedure name.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		params = []

		if current_tok._type != TT.RPR:
			while True:
				# parameter name
				if current_tok._type != TT.IDENT:
					return res.fail(
						InvalidSyntaxError(
							"Expected parameter name.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)

				param_name = current_tok.value
				param_start = current_tok.start_pos.copy()
				advance()

				# :
				if current_tok._type != TT.COL:
					return res.fail(
						InvalidSyntaxError(
							f"Expected ':' after parameter '{param_name}'.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)
				advance()

				# type
				if current_tok._type not in (TT.TYPE, TT.IDENT):
					return res.fail(
						InvalidSyntaxError(
							f"Expected type for parameter '{param_name}'.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)

				type_name = current_tok.value
				param_end = current_tok.end_pos.copy()
				advance()

				pointer_layers = 0
				while current_tok._type in (TT.MUL, TT.POW):
					pointer_layers += 1 if current_tok._type == TT.MUL else 2
					param_end = current_tok.end_pos.copy()
					advance()

				params.append(
					Parameter(
						param_start,
						param_end,
						param_name,
						type_name,
						pointer_layers,
					)
				)

				if current_tok._type == TT.COMMA:
					advance()
					continue

				break

		if current_tok._type != TT.RPR:
			return res.fail(
				InvalidSyntaxError(
					"Expected ')' after parameter list.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		if current_tok._type != TT.LBR:
			return res.fail(
				InvalidSyntaxError(
					"Expected '{' to begin procedure body.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		body = res.register(program(TT.RBR))
		if res.error:
			return res

		if current_tok._type != TT.RBR:
			return res.fail(
				InvalidSyntaxError(
					"Expected '}' after procedure body.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		end_pos = current_tok.end_pos.copy()
		advance()

		return res.success(
			ProcedureDefinition(
				start_pos,
				end_pos,
				name,
				params,
				body,
			)
		)

	def function_definition() -> Result:
		start_pos = current_tok.start_pos.copy()
		res = Result()

		advance()  # consume 'fn'

		# function name
		if current_tok._type != TT.IDENT:
			return res.fail(
				InvalidSyntaxError(
					"Expected function name after 'fn'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		name = current_tok.value
		advance()

		if current_tok._type != TT.LPR:
			return res.fail(
				InvalidSyntaxError(
					"Expected '(' after function name.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		params = []

		if current_tok._type != TT.RPR:
			while True:
				# parameter name
				if current_tok._type != TT.IDENT:
					return res.fail(
						InvalidSyntaxError(
							"Expected parameter name.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)

				param_name = current_tok.value
				param_start = current_tok.start_pos.copy()
				advance()

				# :
				if current_tok._type != TT.COL:
					return res.fail(
						InvalidSyntaxError(
							f"Expected ':' after parameter '{param_name}'.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)
				advance()

				# type
				if current_tok._type not in (TT.TYPE, TT.IDENT):
					return res.fail(
						InvalidSyntaxError(
							f"Expected type for parameter '{param_name}'.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)

				type_name = current_tok.value
				param_end = current_tok.end_pos.copy()
				advance()

				pointer_layers = 0
				while current_tok._type in (TT.MUL, TT.POW):
					pointer_layers += 1 if current_tok._type == TT.MUL else 2
					param_end = current_tok.end_pos.copy()
					advance()

				params.append(
					Parameter(
						param_start,
						param_end,
						param_name,
						type_name,
						pointer_layers,
					)
				)

				if current_tok._type == TT.COMMA:
					advance()
					continue

				break

		if current_tok._type != TT.RPR:
			return res.fail(
				InvalidSyntaxError(
					"Expected ')' after parameter list.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		# return type
		if current_tok._type not in (TT.TYPE, TT.IDENT):
			return res.fail(
				InvalidSyntaxError(
					"Expected return type after parameter list.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		return_type = current_tok.value
		advance()

		pointer_layers = 0
		while current_tok._type in (TT.MUL, TT.POW):
			pointer_layers += 1 if current_tok._type == TT.MUL else 2
			advance()

		if current_tok._type != TT.LBR:
			return res.fail(
				InvalidSyntaxError(
					"Expected '{' to begin function body.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		body = res.register(program(TT.RBR))
		if res.error:
			return res

		if current_tok._type != TT.RBR:
			return res.fail(
				InvalidSyntaxError(
					"Expected '}' after function body.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		end_pos = current_tok.end_pos.copy()
		advance()

		# ensure a return exists
		if not any(isinstance(stmt, ReturnStatement) for stmt in body.statements):
			return res.fail(
				InvalidSyntaxError(
					f"Function '{name}' must contain a return statement.",
					body.start_pos,
					body.end_pos,
				)
			)

		return res.success(
			FunctionDefinition(
				start_pos,
				end_pos,
				name,
				params,
				return_type,
				pointer_layers,
				body,
			)
		)

	def return_statement() -> Result:
		start_pos = current_tok.start_pos.copy()
		res = Result()

		advance()  # consume 'return'

		if current_tok._type in (TT.NEWLINE, TT.SEMICOL, TT.EOF):
			end_pos = current_tok.end_pos.copy()
			return res.success(ReturnStatement(start_pos, end_pos, None))

		value = res.register(expr())
		if res.error:
			return res

		end_pos = value.end_pos.copy()
		return res.success(ReturnStatement(start_pos, end_pos, value))

	def struct_definition() -> Result:
		start_pos = current_tok.start_pos.copy()
		res = Result()

		advance()  # consume 'struct'

		if current_tok._type != TT.IDENT:
			return res.fail(
				InvalidSyntaxError(
					"Expected struct name.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		name = res.register(literal())

		if current_tok._type != TT.LBR:
			return res.fail(
				InvalidSyntaxError(
					"Expected '{' after struct name.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		advance()

		fields = []

		while current_tok._type != TT.RBR:
			while current_tok._type in (TT.NEWLINE, TT.SEMICOL):
				advance()

			if current_tok._type != TT.IDENT:
				return res.fail(
					InvalidSyntaxError(
						"Expected field name.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)

			field_name: str = current_tok.value
			field_start = current_tok.start_pos.copy()
			advance()

			if current_tok._type != TT.COL:
				return res.fail(
					InvalidSyntaxError(
						"Expected ':' after field name.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)

			advance()

			if current_tok._type not in (TT.TYPE, TT.IDENT):
				return res.fail(InvalidSyntaxError(
					f"Expected data type after ':', found {current_tok.value or current_tok._type} instead.",
					current_tok.start_pos,
					current_tok.end_pos
				))
			end_pos: Position = current_tok.end_pos.copy()
			field_type: str = current_tok.value
			field_pointer_layers: int = 0

			advance()

			while current_tok._type in (TT.MUL, TT.POW):
				field_pointer_layers += 1 + int(current_tok._type == TT.POW)
				end_pos = current_tok.end_pos.copy()
				advance()

			fields.append(
				StructField(
					field_start,
					end_pos,
					field_name,
					field_type,
					field_pointer_layers
				)
			)

			if current_tok._type not in (TT.SEMICOL, TT.NEWLINE, TT.RBR):
				return res.fail(InvalidSyntaxError(
					f"Expected ';' or EOL after field, found {current_tok.value or current_tok._type} instead.",
					current_tok.start_pos,
					current_tok.end_pos
				))
			while current_tok._type in (TT.SEMICOL, TT.NEWLINE): advance()

		end_pos = current_tok.end_pos.copy()
		advance()

		return res.success(
			StructDefinition(
				start_pos,
				end_pos,
				name,
				fields,
			)
		)

	def class_definition() -> Result:
		start_pos = current_tok.start_pos.copy()
		res = Result()

		advance()  # consume 'class'

		if current_tok._type != TT.IDENT:
			return res.fail(
				InvalidSyntaxError(
					"Expected class name after 'class' keyword.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		name = current_tok.value
		advance()

		# Optional inheritance pattern: class MyClass : ParentClass
		parent_class = None
		if current_tok._type == TT.COL:
			advance()
			if current_tok._type != TT.IDENT:
				return res.fail(
					InvalidSyntaxError(
						"Expected base class identifier after ':'.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)
			parent_class = current_tok.value
			advance()

		if current_tok._type != TT.LBR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected '{{' to begin class body, but found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		members: list[Node] = []

		while current_tok._type != TT.RBR:
			while current_tok._type in (TT.NEWLINE, TT.SEMICOL):
				advance()

			if current_tok._type == TT.RBR:
				break

			if current_tok._type == TT.KEYWORD:
				match current_tok.value:
					case "var":
						member = res.register(var_declaration())
					case "array":
						member = res.register(array_declaration())
					case "proc":
						member = res.register(procedure_definition())
					case "fn":
						member = res.register(function_definition())
					case _:
						return res.fail(
							InvalidSyntaxError(
								f"Unexpected keyword '{current_tok.value}' inside class declaration.",
								current_tok.start_pos,
								current_tok.end_pos,
							)
						)
			else:
				return res.fail(
					InvalidSyntaxError(
						f"Expected 'var', 'array', 'proc', 'fn', or '}}' inside class body, found '{current_tok.value or current_tok._type.name}'.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)

			if res.error:
				return res
			members.append(member)

			while current_tok._type in (TT.NEWLINE, TT.SEMICOL):
				advance()

		end_pos = current_tok.end_pos.copy()
		advance()  # consume '}'

		return res.success(
			ClassDefinition(
				start_pos,
				end_pos,
				name,
				parent_class,
				members,
			)
		)

	def new_expr() -> Result:
		start_pos = current_tok.start_pos.copy()
		res = Result()

		advance()  # consume 'new'

		if current_tok._type not in (TT.TYPE, TT.IDENT):
			return res.fail(
				InvalidSyntaxError(
					f"Expected a type name or class identifier after 'new', found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)

		type_name: str = current_tok.value
		advance()

		pointer_layers: int = 0
		while current_tok._type in (TT.MUL, TT.POW):
			pointer_layers += 1 + int(current_tok._type == TT.POW)
			advance()

		if current_tok._type == TT.LSQ:
			advance()
			size_expr = res.register(expr())
			if res.error:
				return res

			if current_tok._type != TT.RSQ:
				return res.fail(
					InvalidSyntaxError(
						f"Expected ']' after allocation size, found '{current_tok.value or current_tok._type.name}'.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)
			end_pos = current_tok.end_pos.copy()
			advance()
			return res.success(NewArrayExpression(start_pos, end_pos, type_name, pointer_layers, size_expr))

		if current_tok._type != TT.LPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected '(' or '[' after instantiation type, found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		args = []
		if current_tok._type != TT.RPR:
			while True:
				arg = res.register(expr())
				if res.error:
					return res
				args.append(arg)

				if current_tok._type == TT.COMMA:
					advance()
					continue
				break

		if current_tok._type != TT.RPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected ')' after instantiation arguments, found '{current_tok.value or current_tok._type.name}'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		
		end_pos = current_tok.end_pos.copy()
		advance()

		return res.success(
			NewObjectExpression(
				start_pos,
				end_pos,
				type_name,
				args,
			)
		)

	def procedure_call() -> Result:
		start_pos = current_tok.start_pos.copy()
		res = Result()

		advance()  #  consume 'call'

		if current_tok._type != TT.IDENT:
			return res.fail(
				InvalidSyntaxError(
					"Expected procedure name after 'call'.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		proc_name = current_tok.value
		advance()

		if current_tok._type != TT.LPR:
			return res.fail(
				InvalidSyntaxError(
					"Expected '(' after procedure name.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		args = []

		if current_tok._type != TT.RPR:
			while True:
				arg = res.register(expr())
				if res.error:
					return res
				args.append(arg)

				if current_tok._type == TT.COMMA:
					advance()
					continue

				break

		if current_tok._type != TT.RPR:
			return res.fail(
				InvalidSyntaxError(
					"Expected ')' after procedure arguments.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		end_pos = current_tok.end_pos.copy()
		advance()

		if current_tok._type not in (TT.NEWLINE, TT.SEMICOL, TT.EOF):
			return res.fail(
				InvalidSyntaxError(
					"Expected end of line after procedure call.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)		

		return res.success(ProcedureCall(start_pos, end_pos, proc_name, args))

	# other parse subroutines
	def expr() -> Result:
		res = Result()

		if current_tok == Token(TT.KEYWORD, "new", None, None):
			return new_expr()

		lhs = res.register(logical_or())
		if res.error:
			return res

		supported_assignment_ops = {
			TT.ASGN,
			TT.ADD_ASGN,
			TT.SUB_ASGN,
			TT.MUL_ASGN,
			TT.DIV_ASGN,
			TT.MOD_ASGN,
			TT.POW_ASGN,
		}

		if current_tok._type in supported_assignment_ops:
			op_tok = current_tok
			advance()

			rhs = res.register(expr())
			if res.error:
				return res

			# standard variable assignment (x = value)
			if isinstance(lhs, Identifier):
				return res.success(
					VariableAssign(lhs.start_pos, rhs.end_pos, lhs.value, rhs, op_tok)
				)

			# pointer assignment via dereference (*ptr = value or *(ptr + 1) = value)
			elif isinstance(lhs, UnaryOperation) and lhs.op._type == TT.MUL:
				return res.success(
					PointerAssign(lhs.start_pos, rhs.end_pos, lhs, rhs, op_tok)
				)

			# array indexing/assignment
			elif isinstance(lhs, ArrayIndex):
				return res.success(
					ArrayAssign(
						lhs.start_pos, rhs.end_pos, lhs.array, lhs.index, rhs, op_tok
					)
				)
			
			elif isinstance(lhs, MemberAccess):
				return res.success(
					MemberAssign(
						lhs.start_pos, rhs.end_pos, lhs.parent, lhs.member, rhs, op_tok
					)
				)

			# invalid lhs (5 = value or (x + 1) = value)
			else:
				return res.fail(
					InvalidSyntaxError(
						f"Invalid assignment target. Cannot assign a value to an expression of type '{type(lhs).__name__}'.",
						lhs.start_pos,
						lhs.end_pos,
					)
				)

		return res.success(lhs)

	def logical_or() -> Result:
		return make_binary_op([TT.ORL], logical_xor)

	def logical_xor() -> Result:
		return make_binary_op([TT.XORL], logical_and)

	def logical_and() -> Result:
		return make_binary_op([TT.ANDL], comparison_eq)

	def comparison_eq() -> Result:
		return make_binary_op([TT.EQ, TT.NE], comparison_lg)

	def comparison_lg() -> Result:
		return make_binary_op([TT.LT, TT.LE, TT.GT, TT.GE], bitwise_or)

	def bitwise_or() -> Result:
		return make_binary_op([TT.OR], bitwise_xor)

	def bitwise_xor() -> Result:
		return make_binary_op([TT.XOR], bitwise_and)

	def bitwise_and() -> Result:
		return make_binary_op([TT.AND], additive)

	def additive() -> Result:
		return make_binary_op([TT.ADD, TT.SUB], multiplicative)

	def multiplicative() -> Result:
		return make_binary_op([TT.MUL, TT.DIV, TT.MOD], power)

	def power():
		return make_binary_op([TT.POW], unary, power)

	def unary() -> Result:
		res: Result = Result()
		if current_tok._type in [TT.ADD, TT.SUB, TT.NOT, TT.NOTL, TT.AND, TT.MUL]:
			op: Token = current_tok
			advance()

			value: Node = res.register(unary())
			if res.error:
				return res

			return res.success(UnaryOperation(op.start_pos, value.end_pos, op, value))
		return postfix()
	
	def postfix() -> Result:
		res: Result = Result()
		result: Node = res.register(literal())
		if res.error: return res

		while True:
			if current_tok._type == TT.LSQ:  # array index
				advance()
				index = res.register(expr())
				if res.error:
					return res
				if current_tok._type != TT.RSQ:
					return res.fail(
						InvalidSyntaxError(
							f"Expected ']' after array index, but found '{current_tok.value or current_tok._type.name}'.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)
				result = ArrayIndex(
					result.start_pos, current_tok.end_pos, result, index
				)
				advance()

			elif current_tok._type == TT.LPR:  # function call
				advance()
				args = []

				if current_tok._type != TT.RPR:
					while True:
						arg = res.register(expr())
						if res.error:
							return res

						args.append(arg)

						if current_tok._type == TT.COMMA:
							advance()
							continue

						if current_tok._type != TT.RPR:
							return res.fail(
								InvalidSyntaxError(
									"Expected ',' or ')' after function argument.",
									current_tok.start_pos,
									current_tok.end_pos,
								)
							)

						break

				result = FunctionCall(
					result.start_pos, current_tok.end_pos.copy(), result, args
				)
				advance()

			elif current_tok._type in (TT.DOT, TT.ARROW):  # member expression
				is_arrow: bool = current_tok._type == TT.ARROW
				advance()

				if current_tok._type != TT.IDENT:
					return res.fail(
						InvalidSyntaxError(
							f"Expected member name after '.', found '{current_tok.value or current_tok._type.name}'.",
							current_tok.start_pos,
							current_tok.end_pos,
						)
					)

				member_name = current_tok.value
				member_start = current_tok.start_pos
				member_end = current_tok.end_pos
				advance()

				if current_tok._type == TT.LPR:
					advance()
					args: list[Node] = []

					if current_tok._type != TT.RPR:
						while True:
							arg = res.register(expr())
							if res.error:
								return res
							args.append(arg)

							if current_tok._type == TT.COMMA:
								advance()
								continue

							if current_tok._type != TT.RPR:
								return res.fail(
									InvalidSyntaxError(
										"Expected ',' or ')' after method call argument.",
										current_tok.start_pos,
										current_tok.end_pos,
									)
								)
							break

					member_end = current_tok.end_pos.copy()
					advance()
					result = MethodCall(result.start_pos, member_end, result, member_name, args, is_arrow)
				else:
					member = Identifier(member_start, member_end, member_name)
					result = MemberAccess(result.start_pos, member_start, result, member, is_arrow)

			else:
				break
		
		return res.success(result)

	def literal() -> Result:
		res: Result = Result()
		tok: Token = current_tok
		if tok._type == TT.EOF:
			return res.fail(
				InvalidSyntaxError(
					"Unexpectedly reached End-Of-File (EOF) while expecting an expression or literal value.",
					tok.start_pos,
					tok.end_pos,
				)
			)

		advance()

		if tok._type == TT.INT:
			return res.success(IntLiteral(tok.start_pos, tok.end_pos, tok.value))

		if tok._type == TT.FLOAT:
			return res.success(FloatLiteral(tok.start_pos, tok.end_pos, tok.value))

		if tok._type == TT.STRING:
			return res.success(StringLiteral(tok.start_pos, tok.end_pos, tok.value))

		if tok._type == TT.BOOL:
			return res.success(BoolLiteral(tok.start_pos, tok.end_pos, tok.value))

		if tok._type == TT.CHAR:
			return res.success(CharLiteral(tok.start_pos, tok.end_pos, tok.value))

		if tok._type == TT.IDENT:
			iden_name: str = tok.value
			return res.success(Identifier(tok.start_pos, tok.end_pos, iden_name))

		if tok._type == TT.LPR:
			if current_tok._type == TT.TYPE:
				return type_cast(tok.start_pos)

			value = res.register(expr())
			if res.error:
				return res

			if current_tok._type != TT.RPR:
				return res.fail(
					InvalidSyntaxError(
						f"Expected matching closing parenthesis ')', but found '{current_tok.value or current_tok._type.name}' instead.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)
			advance()
			return res.success(value)

		if tok._type == TT.LSQ:
			elements: list[Node] = []
			if current_tok._type != TT.RSQ:
				while True:
					elem = res.register(expr())
					if res.error:
						return res
					elements.append(elem)
					if current_tok._type != TT.COMMA:
						break
					advance()
			if current_tok._type != TT.RSQ:
				return res.fail(
					InvalidSyntaxError(
						f"Expected ']', but found '{current_tok.value or current_tok._type.name}' instead.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)
			end_pos = current_tok.end_pos.copy()
			advance()
			return res.success(ArrayInitializer(tok.start_pos, end_pos, elements))

		if tok._type == TT.LBR:
			block_content: Program = res.register(program(TT.RBR))
			if res.error:
				return res

			if current_tok._type != TT.RBR:
				return res.fail(
					InvalidSyntaxError(
						f"Expected '}}' to terminate a block, found {current_tok.value or current_tok._type} instead.",
						current_tok.start_pos,
						current_tok.end_pos,
					)
				)

			return res.success(block_content)

		return res.fail(
			InvalidSyntaxError(
				f"Expected a literal value, identifier, or grouping '(' but encountered an invalid starting token '{tok.value or tok._type.name}'.",
				tok.start_pos,
				tok.end_pos,
			)
		)

	def type_cast(start_pos: Position) -> Result:
		res = Result()
		type_to_cast: str = current_tok.value
		pointer_layers: int = 0
		advance()

		while current_tok._type in (TT.MUL, TT.POW):
			pointer_layers += 1 if current_tok._type == TT.MUL else 2
			advance()

		if current_tok._type != TT.RPR:
			return res.fail(
				InvalidSyntaxError(
					f"Expected ')' after type, found {current_tok.value or current_tok.type} instead.",
					current_tok.start_pos,
					current_tok.end_pos,
				)
			)
		advance()

		value = res.register(literal())
		if res.error:
			return res

		return res.success(
			TypeCast(start_pos, value.end_pos, value, type_to_cast, pointer_layers)
		)

	# parse
	res = program()
	if res.error:
		return res

	if current_tok._type != TT.EOF:
		return res.fail(
			InvalidSyntaxError(
				f"Syntax Error: Expected absolute End of File (EOF) after successful program breakdown, but found rogue trailing '{current_tok._type.name}' ('{current_tok.value}').",
				current_tok.start_pos,
				current_tok.end_pos,
			)
		)

	return res
