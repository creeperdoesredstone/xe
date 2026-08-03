from dataclasses import dataclass

from xe_lang.stdlib.ids import BuiltInID
from xe_lang.syscall_abi import SyscallID


@dataclass(frozen=True)
class BuiltinSpec:
	name: str
	builtin_id: BuiltInID
	parameters: tuple[str, ...]
	return_type: str | None
	syscall: int
	reference_parameters: tuple[int, ...] = ()

	@property
	def is_proc(self) -> bool:
		return self.return_type is None


@dataclass(frozen=True)
class PropertySpec:
	name: str
	type_name: str
	getter_id: BuiltInID
	setter_id: BuiltInID
	getter_syscall: int
	setter_syscall: int


@dataclass(frozen=True)
class ConstantSpec:
	name: str
	value: int


@dataclass(frozen=True)
class LibrarySpec:
	name: str
	builtins: tuple[BuiltinSpec, ...]
	constants: tuple[ConstantSpec, ...] = ()
	properties: tuple[PropertySpec, ...] = ()


def _builtin(
	name: str,
	builtin_id: BuiltInID,
	parameters: tuple[str, ...],
	return_type: str | None,
	syscall: int,
	reference_parameters: tuple[int, ...] = (),
) -> BuiltinSpec:
	return BuiltinSpec(
		name,
		builtin_id,
		parameters,
		return_type,
		syscall,
		reference_parameters,
	)


WINDOW_REF = (0,)


GRAPHICS_SPEC = LibrarySpec(
	"graphics",
	(
		_builtin("width", BuiltInID.GRAPHICS_WIDTH, (), "int", SyscallID.APP_GRAPHICS_WIDTH),
		_builtin("height", BuiltInID.GRAPHICS_HEIGHT, (), "int", SyscallID.APP_GRAPHICS_HEIGHT),
		_builtin("begin_draw", BuiltInID.GRAPHICS_BEGIN_DRAW, ("Window",), None, SyscallID.APP_GRAPHICS_BEGIN_DRAW, WINDOW_REF),
		_builtin("update", BuiltInID.GRAPHICS_UPDATE, ("Window",), None, SyscallID.APP_GRAPHICS_UPDATE, WINDOW_REF),
		_builtin("clear", BuiltInID.GRAPHICS_CLEAR, ("Window", "int"), None, SyscallID.APP_GRAPHICS_CLEAR, WINDOW_REF),
		_builtin("set_pixel", BuiltInID.GRAPHICS_SET_PIXEL, ("Window", "int", "int", "int"), None, SyscallID.APP_GRAPHICS_SET_PIXEL, WINDOW_REF),
		_builtin("draw_circle", BuiltInID.GRAPHICS_DRAW_CIRCLE, ("Window", "int", "int", "int", "int"), None, SyscallID.APP_GRAPHICS_DRAW_CIRCLE, WINDOW_REF),
		_builtin("draw_line", BuiltInID.GRAPHICS_DRAW_LINE, ("Window", "int", "int", "int", "int", "int"), None, SyscallID.APP_GRAPHICS_DRAW_LINE, WINDOW_REF),
		_builtin("draw_rect", BuiltInID.GRAPHICS_DRAW_RECT, ("Window", "int", "int", "int", "int", "int"), None, SyscallID.APP_GRAPHICS_DRAW_RECT, WINDOW_REF),
		_builtin("fill_rect", BuiltInID.GRAPHICS_FILL_RECT, ("Window", "int", "int", "int", "int", "int"), None, SyscallID.APP_GRAPHICS_FILL_RECT, WINDOW_REF),
		_builtin("draw_text", BuiltInID.GRAPHICS_DRAW_TEXT, ("Window", "int", "int", "string", "int"), None, SyscallID.APP_GRAPHICS_DRAW_TEXT, WINDOW_REF),
		_builtin("draw_int", BuiltInID.GRAPHICS_DRAW_INT, ("Window", "int", "int", "int", "int"), None, SyscallID.APP_GRAPHICS_DRAW_INT, WINDOW_REF),
		_builtin("draw_float", BuiltInID.GRAPHICS_DRAW_FLOAT, ("Window", "int", "int", "float", "int"), None, SyscallID.APP_GRAPHICS_DRAW_FLOAT, WINDOW_REF),
		_builtin("button", BuiltInID.GRAPHICS_BUTTON, ("Window", "int", "int", "int", "int", "string"), "bool", SyscallID.APP_GRAPHICS_BUTTON, WINDOW_REF),
		_builtin("slider", BuiltInID.GRAPHICS_SLIDER, ("Window", "int", "int", "int", "int", "int", "int"), "int", SyscallID.APP_GRAPHICS_SLIDER, WINDOW_REF),
		_builtin("mouse_x", BuiltInID.GRAPHICS_MOUSE_X, (), "int", SyscallID.APP_GRAPHICS_MOUSE_X),
		_builtin("mouse_y", BuiltInID.GRAPHICS_MOUSE_Y, (), "int", SyscallID.APP_GRAPHICS_MOUSE_Y),
		_builtin("mouse_down", BuiltInID.GRAPHICS_MOUSE_DOWN, (), "bool", SyscallID.APP_GRAPHICS_MOUSE_DOWN),
		_builtin("mouse_pressed", BuiltInID.GRAPHICS_MOUSE_PRESSED, (), "bool", SyscallID.APP_GRAPHICS_MOUSE_PRESSED),
		_builtin("mouse_released", BuiltInID.GRAPHICS_MOUSE_RELEASED, (), "bool", SyscallID.APP_GRAPHICS_MOUSE_RELEASED),
		_builtin("key_down", BuiltInID.GRAPHICS_KEY_DOWN, ("int",), "bool", SyscallID.APP_GRAPHICS_KEY_DOWN),
		_builtin("read_key", BuiltInID.GRAPHICS_READ_KEY, (), "int", SyscallID.APP_GRAPHICS_READ_KEY),
		_builtin("content_width", BuiltInID.GRAPHICS_CONTENT_WIDTH, ("Window",), "int", SyscallID.APP_GRAPHICS_CONTENT_WIDTH, WINDOW_REF),
		_builtin("content_height", BuiltInID.GRAPHICS_CONTENT_HEIGHT, ("Window",), "int", SyscallID.APP_GRAPHICS_CONTENT_HEIGHT, WINDOW_REF),
		_builtin("draw_char", BuiltInID.GRAPHICS_DRAW_CHAR, ("Window", "int", "int", "char", "int"), None, SyscallID.APP_GRAPHICS_DRAW_CHAR, WINDOW_REF),
		# _builtin("button_tone", BuiltInID.GRAPHICS_BUTTON_TONE, ("Window", "int", "int", "int", "int", "string", "int"), "bool", SyscallID.APP_GRAPHICS_BUTTON_TONE, WINDOW_REF),
	),
	(
		ConstantSpec("SCREEN_WIDTH", 480),
		ConstantSpec("SCREEN_HEIGHT", 360),
		*(ConstantSpec(f"COLOR_{index}", index) for index in range(16)),
		ConstantSpec("COLOR_BLACK", 0),
		ConstantSpec("COLOR_WHITE", 15),
		ConstantSpec("BLACK", 0),
		ConstantSpec("WHITE", 15),
		ConstantSpec("WINDOW_NORMAL", 0),
		ConstantSpec("WINDOW_MINIMIZED", 1),
		ConstantSpec("WINDOW_FULLSCREEN", 2),
		ConstantSpec("WINDOW_CLOSED", 3),
		ConstantSpec("MOUSE_LEFT", 1),
		ConstantSpec("KEY_BACKSPACE", 8),
		ConstantSpec("KEY_ENTER", 13),
		ConstantSpec("KEY_ESCAPE", 27),
		ConstantSpec("KEY_SPACE", 32),
		ConstantSpec("KEY_LEFT", 3),
		ConstantSpec("KEY_UP", 4),
		ConstantSpec("KEY_RIGHT", 5),
		ConstantSpec("KEY_DOWN", 6),
		ConstantSpec("KEY_DELETE", 127),
	),
)


OS_SPEC = LibrarySpec(
	"os",
	(
		_builtin("sleep", BuiltInID.OS_SLEEP, ("int",), None, SyscallID.APP_OS_SLEEP),
		_builtin("exit", BuiltInID.OS_EXIT, ("int",), None, SyscallID.APP_OS_EXIT),
		_builtin("apply_settings", BuiltInID.OS_APPLY_SETTINGS, ("int", "int", "int"), "bool", SyscallID.APP_OS_APPLY_SETTINGS),
		_builtin("background_count", BuiltInID.OS_BACKGROUND_COUNT, (), "int", SyscallID.APP_OS_BACKGROUND_COUNT),
		_builtin("palette_count", BuiltInID.OS_PALETTE_COUNT, (), "int", SyscallID.APP_OS_PALETTE_COUNT),
		_builtin("ticks", BuiltInID.OS_TICKS, (), "int", SyscallID.APP_OS_TICKS),
		_builtin("open_read", BuiltInID.OS_OPEN_READ, ("string",), "File", SyscallID.APP_OS_OPEN_READ),
		_builtin("open_write", BuiltInID.OS_OPEN_WRITE, ("string",), "File", SyscallID.APP_OS_OPEN_WRITE),
		_builtin("read", BuiltInID.OS_READ, ("File",), "string", SyscallID.APP_OS_READ),
		_builtin("write", BuiltInID.OS_WRITE, ("File", "string"), "bool", SyscallID.APP_OS_WRITE),
		_builtin("close", BuiltInID.OS_CLOSE, ("string",), None, SyscallID.APP_OS_CLOSE),
	),
	(
		ConstantSpec("VOLUME_MIN", 0),
		ConstantSpec("VOLUME_MAX", 100),
	),
	(
		PropertySpec("volume", "int", BuiltInID.OS_GET_VOLUME, BuiltInID.OS_SET_VOLUME, SyscallID.APP_OS_GET_VOLUME, SyscallID.APP_OS_SET_VOLUME),
		PropertySpec("background_id", "int", BuiltInID.OS_GET_BACKGROUND, BuiltInID.OS_SET_BACKGROUND, SyscallID.APP_OS_GET_BACKGROUND, SyscallID.APP_OS_SET_BACKGROUND),
		PropertySpec("palette", "int", BuiltInID.OS_GET_PALETTE, BuiltInID.OS_SET_PALETTE, SyscallID.APP_OS_GET_PALETTE, SyscallID.APP_OS_SET_PALETTE),
	),
)


STANDARD_LIBRARY_SPECS = (GRAPHICS_SPEC, OS_SPEC)

BUILTIN_SYSCALLS = {
	builtin.builtin_id: builtin.syscall
	for library in STANDARD_LIBRARY_SPECS
	for builtin in library.builtins
}

PROPERTY_GETTER_SYSCALLS = {
	prop.getter_id: prop.getter_syscall
	for library in STANDARD_LIBRARY_SPECS
	for prop in library.properties
}

PROPERTY_SETTER_SYSCALLS = {
	prop.setter_id: prop.setter_syscall
	for library in STANDARD_LIBRARY_SPECS
	for prop in library.properties
}

METHOD_SYSCALLS = {
	BuiltInID.GRAPHICS_WINDOW_CLOSE: SyscallID.APP_WINDOW_CLOSE,
	BuiltInID.GRAPHICS_WINDOW_IS_FULLSCREEN: SyscallID.APP_WINDOW_IS_FULLSCREEN,
	BuiltInID.GRAPHICS_WINDOW_IS_MINIMIZED: SyscallID.APP_WINDOW_IS_MINIMIZED,
}