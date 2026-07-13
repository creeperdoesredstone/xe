from xe_lang.helper import TT, Token, Position, LexError
from string import ascii_letters

DIGITS = "0123456789"
LETTERS = ascii_letters
VALID_IDEN = LETTERS + DIGITS + "_"

KEYWORDS = [
	"var",
	"array",
	"out",
	"in",
	"for",
	"while",
	"repeat",
	"until",
	"if",
	"elseif",
	"else",
	"switch",
	"case",
	"default",
	"break",
	"continue",
	"proc",
	"call",
	"fn",
	"return",
	"class",
	"new",
	"free",
	"this",
	"struct",
]
DATA_TYPES = ["int", "float", "string", "bool", "char"]


def lex(fn: str, ftxt: str) -> tuple[list[Token], None] | tuple[None, LexError]:
	# initialize
	pos: Position = Position(-1, 0, -1, fn, ftxt)
	current_char: str | None = ""
	tokens: list[Token] = []
	start_pos: Position = pos.copy()

	def advance():
		nonlocal current_char
		pos.advance(current_char)
		current_char = ftxt[pos.idx] if pos.idx < len(ftxt) else None

	advance()

	# helper subroutines
	def make_operator_tok(_type: TT, start_pos: Position) -> None:
		tokens.append(Token(_type, None, start_pos, pos.copy()))
		advance()

	def make_compound_tok(
		no_char_type: TT, with_char_type: TT|tuple[TT], char_to_check: str
	) -> None:
		nonlocal current_char, pos
		operator_end_pos: Position = pos.copy()
		advance()

		if isinstance(with_char_type, tuple):
			for i in range(len(with_char_type)):
				if current_char == char_to_check[i]:
					tokens.append(Token(with_char_type[i], None, start_pos, pos.copy()))
					advance()
					return
			
			tokens.append(Token(no_char_type, None, start_pos, operator_end_pos))
			return
		
		if current_char == char_to_check:
			tokens.append(Token(with_char_type, None, start_pos, pos.copy()))
			advance()
		else:
			tokens.append(Token(no_char_type, None, start_pos, operator_end_pos))

	def make_comment() -> None:
		while current_char != None and current_char != "\n":
			advance()

	def make_number() -> None:
		res: str = ""
		dot_count: int = 0
		end_pos: Position = pos.copy()

		while current_char != None and current_char in (DIGITS + "."):
			if current_char == ".":
				dot_count += 1
				if dot_count > 1:
					break

			res += current_char
			end_pos = pos.copy()
			advance()

		if dot_count == 0:
			tokens.append(Token(TT.INT, int(res), start_pos, end_pos))
		else:
			tokens.append(Token(TT.FLOAT, float(res), start_pos, end_pos))

	def make_iden_or_keyword() -> None:
		res: str = ""
		end_pos: Position = pos.copy()

		while current_char != None and current_char in VALID_IDEN:
			res += current_char
			end_pos = pos.copy()
			advance()

		if res in KEYWORDS:
			tokens.append(Token(TT.KEYWORD, res, start_pos, end_pos))
		elif res in DATA_TYPES:
			tokens.append(Token(TT.TYPE, res, start_pos, end_pos))
		elif res in ("true", "false"):
			tokens.append(Token(TT.BOOL, res == "true", start_pos, end_pos))
		else:
			tokens.append(Token(TT.IDENT, res, start_pos, end_pos))

	def turn_to_asgn_tok() -> None:
		if current_char == "=":
			current_tok: Token = tokens[-1]
			new_type: TT = getattr(TT, f"{current_tok._type.name}_ASGN")

			tokens[-1]._type = new_type
			tokens[-1].end_pos = pos.copy()

			advance()
	
	escape_map: dict[str, str] = {
		"0": "\0",
		"n": "\n",
		"t": "\t",
		"\n": "",
	}

	# lex
	while current_char != None:
		start_pos = pos.copy()

		if current_char in " \t":
			advance()
		elif current_char == "\n":
			make_operator_tok(TT.NEWLINE, start_pos)
		elif current_char == "#":
			make_comment()
		elif current_char == "+":
			make_operator_tok(TT.ADD, start_pos)
			turn_to_asgn_tok()
		elif current_char == "-":
			make_operator_tok(TT.SUB, start_pos)
			turn_to_asgn_tok()
		elif current_char == "*":
			make_compound_tok(TT.MUL, TT.POW, "*")
			turn_to_asgn_tok()
		elif current_char == "/":
			make_operator_tok(TT.DIV, start_pos)
			turn_to_asgn_tok()
		elif current_char == "%":
			make_operator_tok(TT.MOD, start_pos)
			turn_to_asgn_tok()
		elif current_char == "&":
			make_compound_tok(TT.AND, TT.ANDL, "&")
			turn_to_asgn_tok()
		elif current_char == "|":
			make_compound_tok(TT.OR, TT.ORL, "|")
			turn_to_asgn_tok()
		elif current_char == "~":
			make_operator_tok(TT.NOT, start_pos)
			turn_to_asgn_tok()
		elif current_char == "!":
			make_compound_tok(TT.NOTL, TT.NE, "=")
		elif current_char == "^":
			make_compound_tok(TT.XOR, TT.XORL, "^")
			turn_to_asgn_tok()
		elif current_char == "(":
			make_operator_tok(TT.LPR, start_pos)
		elif current_char == ")":
			make_operator_tok(TT.RPR, start_pos)
		elif current_char == "[":
			make_operator_tok(TT.LSQ, start_pos)
		elif current_char == "]":
			make_operator_tok(TT.RSQ, start_pos)
		elif current_char == "{":
			make_operator_tok(TT.LBR, start_pos)
		elif current_char == "}":
			make_operator_tok(TT.RBR, start_pos)
		elif current_char == ":":
			make_operator_tok(TT.COL, start_pos)
		elif current_char == ";":
			make_operator_tok(TT.SEMICOL, start_pos)
		elif current_char == ",":
			make_operator_tok(TT.COMMA, start_pos)
		elif current_char == ".":
			make_operator_tok(TT.DOT, start_pos)
		elif current_char == "=":
			make_compound_tok(TT.ASGN, TT.EQ, "=")
		elif current_char == "<":
			make_compound_tok(TT.LT, (TT.LE, TT.OSTREAM), "=<")
		elif current_char == ">":
			make_compound_tok(TT.GT, (TT.GE, TT.ISTREAM), "=>")
		elif current_char == "'":
			advance()
			char: str = "\0"

			if current_char != "'":
				if current_char == "\\":
					advance()
					char = escape_map.get(current_char, current_char)
				else:
					char = current_char
				advance()
				
				if current_char != "'":
					return tokens, LexError(
						f"Expected ' to terminate character, found '{current_char}' instead.",
						pos.copy(),
						pos.copy()
					)
			tokens.append(Token(TT.CHAR, char, start_pos, pos.copy()))
			advance()
		elif current_char == '"':
			advance()
			string: str = ""
			escape: bool = False

			while current_char not in (None, "\n") and current_char != '"' or escape:
				if escape:
					string += escape_map.get(current_char, current_char)
					escape = False
				else:
					if current_char == "\\":
						escape = True
					else:
						string += current_char
				advance()
			
			if current_char != '"':
				return tokens, LexError(
					f"Expected '\"' to terminate string, found '{current_char}' instead.",
					pos.copy(),
					pos.copy()
				)
			end_pos: Position = pos.copy()
			tokens.append(Token(TT.STRING, string, start_pos, end_pos))
			advance()
		elif current_char in DIGITS:
			make_number()
		elif current_char in VALID_IDEN:
			make_iden_or_keyword()
		else:
			return tokens, LexError(
				f"Encountered illegal character '{current_char}' during lexing.",
				start_pos,
				pos.copy(),
			)

	make_operator_tok(TT.EOF, start_pos)
	return tokens, None
