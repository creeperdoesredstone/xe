from enum import Enum
from typing import Any, Self

TT = Enum(
    "TT",
    "EOF NEWLINE IDENT KEYWORD TYPE INT FLOAT STRING BOOL CHAR "
    "ADD SUB MUL DIV MOD POW EQ NE LT LE GT GE AND OR NOT XOR ANDL ORL NOTL XORL "
    "SEMICOL COL DOT COMMA QUESTION LPR RPR LBR RBR LSQ RSQ "
    "ASGN ADD_ASGN SUB_ASGN MUL_ASGN DIV_ASGN MOD_ASGN POW_ASGN "
    "AND_ASGN OR_ASGN XOR_ASGN ANDL_ASGN ORL_ASGN XORL_ASGN "
    "ISTREAM OSTREAM ARROW SCOPE",
)


class ANSI:
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    DARKCYAN = "\033[36m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    END = "\033[0m"


class Position:
    def __init__(self, idx: int, ln: int, col: int, fn: str, ftxt: str) -> None:
        self.idx: int = idx
        self.ln: int = ln
        self.col: int = col
        self.fn: str = fn
        self.ftxt: str = ftxt

    def advance(self, current_char: str) -> None:
        self.idx += 1
        self.col += 1
        if current_char == "\n":
            self.col = 0
            self.ln += 1

    def copy(self) -> "Position":
        return Position(self.idx, self.ln, self.col, self.fn, self.ftxt)


def string_with_arrows(
    original_string: str,
    start_pos: Position,
    end_pos: Position,
) -> str:
    lines = original_string.splitlines()

    if start_pos.ln >= len(lines):
        return ""

    line = lines[start_pos.ln]

    # If the span crosses lines, only show the first line
    if start_pos.ln != end_pos.ln:
        arrow_start = start_pos.col
        arrow_end = len(line)
    else:
        arrow_start = start_pos.col
        arrow_end = end_pos.col

    # Ensure at least one caret is shown
    arrow_count = max(1, arrow_end - arrow_start + 1)

    return (
        line + "\n" + ANSI.CYAN + (" " * arrow_start) + ("^" * arrow_count) + ANSI.END
    )


class Token:
    def __init__(
        self, _type: TT, value: Any, start_pos: Position, end_pos: Position
    ) -> None:
        self._type: TT = _type
        self.value: Any = value
        self.start_pos: Position = start_pos
        self.end_pos: Position = end_pos

    def __repr__(self) -> str:
        return (
            f"{ANSI.YELLOW}{self._type}{ANSI.END}:{ANSI.GREEN}{repr(self.value)}{ANSI.END}"
            if self.value
            else f"{ANSI.YELLOW}{self._type}{ANSI.END}"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Token):
            return NotImplemented
        return self._type == other._type and self.value == other.value


class Error:
    def __init__(
        self, name: str, desc: str, start_pos: Position, end_pos: Position
    ) -> None:
        self.name: str = name
        self.desc: str = desc
        self.start_pos: Position = start_pos
        self.end_pos: Position = end_pos

    def __repr__(self) -> str:
        res: str = (
            f"File {ANSI.YELLOW}'{self.start_pos.fn}'{ANSI.GREEN} (line {self.start_pos.ln + 1} column {self.start_pos.col + 1}){ANSI.END}\n\n"
        )
        res += (
            string_with_arrows(
                self.start_pos.ftxt,
                self.start_pos,
                self.end_pos,
            )
            + f"{ANSI.END}\n"
        )
        res += f"{ANSI.BOLD}{ANSI.RED}{self.name}{ANSI.END}: {ANSI.PURPLE}{self.desc}{ANSI.END}\n"

        return res


class LexError(Error):
    def __init__(self, desc: str, start_pos: Position, end_pos: Position) -> None:
        super().__init__("LexError", desc, start_pos, end_pos)


class InvalidSyntaxError(Error):
    def __init__(self, desc: str, start_pos: Position, end_pos: Position) -> None:
        super().__init__("InvalidSyntax", desc, start_pos, end_pos)


class SemanticError(Error):
    def __init__(self, desc: str, start_pos: Position, end_pos: Position) -> None:
        super().__init__("SemanticError", desc, start_pos, end_pos)


class AssemblyError(Error):
    def __init__(self, desc: str, start_pos: Position, end_pos: Position) -> None:
        super().__init__("AssemblyError", desc, start_pos, end_pos)


class VMError(Error):
    def __init__(self, desc: str, start_pos: Position, end_pos: Position) -> None:
        super().__init__("VMError", desc, start_pos, end_pos)


class Result:
    def __init__(self):
        self.value: Any = None
        self.error: Error | None = None

    def register(self, res: Self) -> Any:
        if res.error:
            self.error = res.error
        return res.value

    def success(self, value: Any) -> Self:
        self.value = value
        return self

    def fail(self, error: Error) -> Self:
        self.error = error
        return self
