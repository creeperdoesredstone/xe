from dataclasses import dataclass, field
from typing import Union
from enum import Enum, auto
from xe_lang.nodes import *


class BuiltInID(Enum):
	MATH_SIN = auto()
	MATH_COS = auto()
	MATH_TAN = auto()
	MATH_ASIN = auto()
	MATH_ACOS = auto()
	MATH_ATAN = auto()
	MATH_SQRT = auto()
	MATH_POW = auto()

	WINDOW_OPEN = auto()
	WINDOW_CLOSE = auto()
	WINDOW_PRESENT = auto()

	GRAPHICS_CLEAR = auto()
	GRAPHICS_PIXEL = auto()
	GRAPHICS_LINE = auto()

	OS_EXIT = auto()
	OS_SLEEP = auto()
	OS_CLOCK = auto()


class Scope:
	def __init__(self, parent=None):
		self.parent = parent
		self.symbols: dict[str, BaseSymbol] = {}

	def lookup(self, name) -> BaseSymbol | None:
		scope = self
		while scope is not None:
			if name in scope.symbols:
				return scope.symbols[name]
			scope = scope.parent
		return None


class Type:
	def __init__(self, base: str, pointer_layers: int = 0, is_array: bool = False):
		self.base: str = base
		self.pointer_layers: int = pointer_layers
		self.is_array: bool = is_array

		self.is_callable: bool = base == "function" and pointer_layers == 0
		self.is_proc: bool = base == "procedure" and pointer_layers == 0
		self.parameters: list = []
		self.return_type = None

	def __eq__(self, other):
		return (
			isinstance(other, Type)
			and self.base == other.base
			and self.pointer_layers == other.pointer_layers
		)

	def __ne__(self, other):
		return not self.__eq__(other)

	def __str__(self):
		return (self.base if self.base else "Unknown") + ("*" * self.pointer_layers)

	def __repr__(self):
		return self.__str__()


@dataclass
class BaseSymbol:
	name: str
	type: Type
	address: int = 0
	arr_length: int = 0
	parameters: list = field(default_factory=list)
	return_type: Union[Type, None] = None
	is_callable: bool = False
	is_library: bool = False
	const_value: Node|None = None


@dataclass
class VariableSymbol(BaseSymbol):
	is_local: bool = False

	def __repr__(self) -> str:
		loc_str = "local" if self.is_local else "global"
		arr_str = f"[{self.arr_length}]" if self.arr_length > 0 else ""
		return (
			f"<Symbol '{self.name}': {self.type}{arr_str} @ {loc_str}({self.address})>"
		)


@dataclass
class SubroutineSymbol(BaseSymbol):
	param_names: list[str] = field(default_factory=list)
	is_proc: bool = False
	is_callable: bool = True
	next_local_offset: int = -1

	def __post_init__(self):
		self.type = Type("procedure" if self.is_proc else "function")

	def __repr__(self) -> str:
		kind = "proc" if self.is_proc else "fn"
		param_str = ", ".join(str(p) for p in self.parameters)
		ret_str = f" {self.return_type}" if self.return_type else ""
		locals_count = abs(self.next_local_offset) - 1
		return (
			f"<{kind} {self.name}({param_str})"
			f"{ret_str} @ addr {self.address} "
			f"(locals: {locals_count})>"
		)


@dataclass
class BuiltInSubroutineSymbol(BaseSymbol):
	builtin_id: str = ""

	is_proc: bool = False
	is_callable: bool = True

	def __post_init__(self):
		self.type = Type("procedure" if self.is_proc else "function")

	def __repr__(self):
		kind = "proc" if self.is_proc else "fn"
		param_str = ", ".join(str(p) for p in self.parameters)
		ret_str = f" -> {self.return_type}" if self.return_type else ""
		return f"<builtin {kind} {self.name}" f"({param_str}){ret_str}>"


@dataclass
class LibrarySymbol(BaseSymbol):
	is_library = True
	members: dict[str, BaseSymbol] = field(default_factory=dict)

	def __post_init__(self):
		self.type = Type("library")

	def lookup(self, name: str):
		return self.members.get(name)

	def __repr__(self):
		return f"<library {self.name}" f" ({len(self.members)} members)>"


@dataclass
class StructSymbol(BaseSymbol):
	fields: dict[str, VariableSymbol] = field(default_factory=dict)
	size: int = 0

	def __post_init__(self):
		self.type = Type(self.name)

	def lookup(self, field: str):
		return self.fields.get(field)

	def __repr__(self):
		return f"<struct {self.name}" f" ({len(self.fields)} fields, size={self.size})>"


@dataclass
class ClassSymbol(BaseSymbol):
	fields: dict[str, VariableSymbol] = field(default_factory=dict)
	methods: dict[str, BaseSymbol] = field(default_factory=dict)
	base_class: Union["ClassSymbol", None] = None
	size: int = 0

	def __post_init__(self):
		self.type = Type(self.name)

	def lookup_field(self, name: str):
		if name in self.fields:
			return self.fields[name]
		if self.base_class:
			return self.base_class.lookup_field(name)
		return None

	def lookup_method(self, name: str):
		if name in self.methods:
			return self.methods[name]
		if self.base_class:
			return self.base_class.lookup_method(name)
		return None

	def __repr__(self):
		base = f" : {self.base_class.name}" if self.base_class else ""
		return (
			f"<class {self.name}{base}"
			f" ({len(self.fields)} fields, "
			f"{len(self.methods)} methods)>"
		)


def make_library(name: str, members: dict[str, BaseSymbol]) -> LibrarySymbol:
	return LibrarySymbol(name, Type("library"), members=members)


def init_libraries(scope: Scope):
	scope.symbols["math"] = make_library(
		"math",
		{
			"sin": BuiltInSubroutineSymbol(
				"sin",
				Type("function"),
				parameters=[Type("float")],
				return_type=Type("float"),
				builtin_id=BuiltInID.MATH_SIN,
			),
			"cos": BuiltInSubroutineSymbol(
				"cos",
				Type("function"),
				parameters=[Type("float")],
				return_type=Type("float"),
				builtin_id=BuiltInID.MATH_COS,
			),
			"tan": BuiltInSubroutineSymbol(
				"tan",
				Type("function"),
				parameters=[Type("float")],
				return_type=Type("float"),
				builtin_id=BuiltInID.MATH_TAN,
			),
			"asin": BuiltInSubroutineSymbol(
				"asin",
				Type("function"),
				parameters=[Type("float")],
				return_type=Type("float"),
				builtin_id=BuiltInID.MATH_ASIN,
			),
			"acos": BuiltInSubroutineSymbol(
				"acos",
				Type("function"),
				parameters=[Type("float")],
				return_type=Type("float"),
				builtin_id=BuiltInID.MATH_ACOS,
			),
			"atan": BuiltInSubroutineSymbol(
				"atan",
				Type("function"),
				parameters=[Type("float")],
				return_type=Type("float"),
				builtin_id=BuiltInID.MATH_ATAN,
			),
			"sqrt": BuiltInSubroutineSymbol(
				"sqrt",
				Type("function"),
				parameters=[Type("float")],
				return_type=Type("float"),
				builtin_id=BuiltInID.MATH_SQRT,
			),
			"pow": BuiltInSubroutineSymbol(
				"pow",
				Type("function"),
				parameters=[Type("float"), Type("float")],
				return_type=Type("float"),
				builtin_id=BuiltInID.MATH_POW,
			),
			"pi": VariableSymbol("pi", Type("float"), const_value=FloatLiteral(None, None, 3.141592653589793)),
			"e": VariableSymbol("e", Type("float"), const_value=FloatLiteral(None, None, 2.718281828459045)),
		},
	)

	scope.symbols["window"] = make_library(
		"window",
		members={
			"open": BuiltInSubroutineSymbol(
				"open",
				Type("procedure"),
				parameters=[
					Type("int"),
					Type("int"),
					Type("string"),
				],
				is_proc=True,
				builtin_id=BuiltInID.WINDOW_OPEN,
			),
			"close": BuiltInSubroutineSymbol(
				"close",
				Type("procedure"),
				is_proc=True,
				builtin_id=BuiltInID.WINDOW_CLOSE,
			),
			"present": BuiltInSubroutineSymbol(
				"present",
				Type("procedure"),
				is_proc=True,
				builtin_id=BuiltInID.WINDOW_PRESENT,
			),
			"width": VariableSymbol("width", Type("int")),
			"height": VariableSymbol("height", Type("int")),
		},
	)

	scope.symbols["graphics"] = make_library(
		"graphics",
		members={
			"clear": BuiltInSubroutineSymbol(
				"clear",
				Type("procedure"),
				parameters=[Type("int")],
				is_proc=True,
				builtin_id=BuiltInID.GRAPHICS_CLEAR,
			),
			"pixel": BuiltInSubroutineSymbol(
				"pixel",
				Type("procedure"),
				parameters=[
					Type("int"),
					Type("int"),
					Type("int"),
				],
				is_proc=True,
				builtin_id=BuiltInID.GRAPHICS_PIXEL,
			),
			"line": BuiltInSubroutineSymbol(
				"line",
				Type("procedure"),
				parameters=[
					Type("int"),
					Type("int"),
					Type("int"),
					Type("int"),
					Type("int"),
				],
				is_proc=True,
				builtin_id=BuiltInID.GRAPHICS_LINE,
			),
		},
	)

	scope.symbols["os"] = make_library(
		"os",
		members={
			"exit": BuiltInSubroutineSymbol(
				"exit",
				Type("procedure"),
				parameters=[Type("int")],
				is_proc=True,
				builtin_id=BuiltInID.OS_EXIT,
			),
			"sleep": BuiltInSubroutineSymbol(
				"sleep",
				Type("procedure"),
				parameters=[Type("int")],
				is_proc=True,
				builtin_id=BuiltInID.OS_SLEEP,
			),
			"clock": BuiltInSubroutineSymbol(
				"clock",
				Type("function"),
				return_type=Type("int"),
				builtin_id=BuiltInID.OS_CLOCK,
			),
		},
	)
