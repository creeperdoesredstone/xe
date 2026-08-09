from dataclasses import dataclass

from xe_lang import graphics_commands as gc
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
		_builtin("draw_bg", BuiltInID.GRAPHICS_DRAW_BG, (), None, SyscallID.GRAPHICS_DRAW_BG),
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
		_builtin("scroll_delta", BuiltInID.GRAPHICS_SCROLL_DELTA, (), "int", SyscallID.APP_GRAPHICS_SCROLL_DELTA),
		_builtin("key_down", BuiltInID.GRAPHICS_KEY_DOWN, ("int",), "bool", SyscallID.APP_GRAPHICS_KEY_DOWN),
		_builtin("read_key", BuiltInID.GRAPHICS_READ_KEY, (), "int", SyscallID.APP_GRAPHICS_READ_KEY),
		_builtin("content_width", BuiltInID.GRAPHICS_CONTENT_WIDTH, ("Window",), "int", SyscallID.APP_GRAPHICS_CONTENT_WIDTH, WINDOW_REF),
		_builtin("content_height", BuiltInID.GRAPHICS_CONTENT_HEIGHT, ("Window",), "int", SyscallID.APP_GRAPHICS_CONTENT_HEIGHT, WINDOW_REF),
		_builtin("draw_char", BuiltInID.GRAPHICS_DRAW_CHAR, ("Window", "int", "int", "char", "int"), None, SyscallID.APP_GRAPHICS_DRAW_CHAR, WINDOW_REF),
		_builtin("button_tone", BuiltInID.GRAPHICS_BUTTON_TONE, ("Window", "int", "int", "int", "int", "string", "int"), "bool", SyscallID.APP_GRAPHICS_BUTTON_TONE, WINDOW_REF),
		_builtin("pointer_x", BuiltInID.GRAPHICS_POINTER_X, ("Window",), "int", SyscallID.APP_GRAPHICS_POINTER_X, WINDOW_REF),
		_builtin("pointer_y", BuiltInID.GRAPHICS_POINTER_Y, ("Window",), "int", SyscallID.APP_GRAPHICS_POINTER_Y, WINDOW_REF),
		_builtin("draw_text_small", BuiltInID.GRAPHICS_DRAW_TEXT_SMALL, ("Window", "int", "int", "string", "int"), None, SyscallID.APP_GRAPHICS_DRAW_TEXT_SMALL, WINDOW_REF),
		_builtin("draw_char_small", BuiltInID.GRAPHICS_DRAW_CHAR_SMALL, ("Window", "int", "int", "char", "int"), None, SyscallID.APP_GRAPHICS_DRAW_CHAR_SMALL, WINDOW_REF),
		_builtin("draw_int_small", BuiltInID.GRAPHICS_DRAW_INT_SMALL, ("Window", "int", "int", "int", "int"), None, SyscallID.APP_GRAPHICS_DRAW_INT_SMALL, WINDOW_REF),
		_builtin("draw_float_small", BuiltInID.GRAPHICS_DRAW_FLOAT_SMALL, ("Window", "int", "int", "float", "int"), None, SyscallID.APP_GRAPHICS_DRAW_FLOAT_SMALL, WINDOW_REF),
		_builtin("button_flat", BuiltInID.GRAPHICS_BUTTON_FLAT, ("Window", "int", "int", "int", "int", "string", "int"), "bool", SyscallID.APP_GRAPHICS_BUTTON_FLAT, WINDOW_REF),
		_builtin("draw_atom", BuiltInID.GRAPHICS_DRAW_ATOM, ("Window", "int", "int", "int", "int", "int", "int"), None, SyscallID.APP_GRAPHICS_DRAW_ATOM, WINDOW_REF),
		_builtin("draw_icon", BuiltInID.GRAPHICS_DRAW_ICON, ("Window", "int", "int", "int", "int", "string"), None, SyscallID.APP_GRAPHICS_DRAW_ICON, WINDOW_REF),
		_builtin("draw_icon_scaled", BuiltInID.GRAPHICS_DRAW_ICON_SCALED, ("Window", "int", "int", "int", "int", "string", "int"), None, SyscallID.APP_GRAPHICS_DRAW_ICON_SCALED, WINDOW_REF),
		_builtin("char_advance", BuiltInID.GRAPHICS_CHAR_ADVANCE, ("char", "int"), "int", SyscallID.APP_GRAPHICS_CHAR_ADVANCE),
		_builtin("draw_char_styled", BuiltInID.GRAPHICS_DRAW_CHAR_STYLED, ("Window", "int", "int", "char", "int", "int", "int"), None, SyscallID.APP_GRAPHICS_DRAW_CHAR_STYLED, WINDOW_REF),
		_builtin("load_image", BuiltInID.GRAPHICS_LOAD_IMAGE, ("string",), "Image", SyscallID.APP_GRAPHICS_LOAD_IMAGE),
		_builtin("image_width", BuiltInID.GRAPHICS_IMAGE_WIDTH, ("Image",), "int", SyscallID.APP_GRAPHICS_IMAGE_WIDTH),
		_builtin("image_height", BuiltInID.GRAPHICS_IMAGE_HEIGHT, ("Image",), "int", SyscallID.APP_GRAPHICS_IMAGE_HEIGHT),
		_builtin("image_frame_count", BuiltInID.GRAPHICS_IMAGE_FRAME_COUNT, ("Image",), "int", SyscallID.APP_GRAPHICS_IMAGE_FRAME_COUNT),
		_builtin("image_frame_duration", BuiltInID.GRAPHICS_IMAGE_FRAME_DURATION, ("Image", "int"), "int", SyscallID.APP_GRAPHICS_IMAGE_FRAME_DURATION),
		_builtin("draw_image", BuiltInID.GRAPHICS_DRAW_IMAGE, ("Window", "Image", "int", "int", "int", "int"), None, SyscallID.APP_GRAPHICS_DRAW_IMAGE, WINDOW_REF),
		_builtin(
			"draw_commands",
			BuiltInID.GRAPHICS_DRAW_COMMANDS,
			(
				"Window", "int*", "int", "string*", "string*", "int*",
				"int*", "int*", "int*", "int*", "int*",
			),
			"int",
			SyscallID.APP_GRAPHICS_DRAW_COMMANDS,
			WINDOW_REF,
		),
		_builtin("modifiers", BuiltInID.GRAPHICS_MODIFIERS, (), "int", SyscallID.APP_GRAPHICS_MODIFIERS),
		_builtin("right_mouse_down", BuiltInID.GRAPHICS_RIGHT_MOUSE_DOWN, (), "bool", SyscallID.APP_GRAPHICS_RIGHT_MOUSE_DOWN),
		_builtin("right_mouse_pressed", BuiltInID.GRAPHICS_RIGHT_MOUSE_PRESSED, (), "bool", SyscallID.APP_GRAPHICS_RIGHT_MOUSE_PRESSED),
		_builtin("right_mouse_released", BuiltInID.GRAPHICS_RIGHT_MOUSE_RELEASED, (), "bool", SyscallID.APP_GRAPHICS_RIGHT_MOUSE_RELEASED),
		_builtin("get_cwidth", BuiltInID.GRAPHICS_GET_CWIDTH, ("char",), "int", SyscallID.GRAPHICS_GET_CWIDTH),
		_builtin("get_cwidth_small", BuiltInID.GRAPHICS_GET_CWIDTH_SMALL, ("char",), "int", SyscallID.GRAPHICS_GET_CWIDTH_SMALL),
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
		ConstantSpec("MOUSE_RIGHT", 2),
		ConstantSpec("KEY_BACKSPACE", 8),
		ConstantSpec("KEY_TAB", 9),
		ConstantSpec("KEY_ENTER", 13),
		ConstantSpec("KEY_ESCAPE", 27),
		ConstantSpec("KEY_SPACE", 32),
		ConstantSpec("KEY_LEFT", 3),
		ConstantSpec("KEY_UP", 4),
		ConstantSpec("KEY_RIGHT", 5),
		ConstantSpec("KEY_DOWN", 6),
		ConstantSpec("KEY_DELETE", 127),
		ConstantSpec("MOD_SHIFT", 1),
		ConstantSpec("MOD_CTRL", 2),
		ConstantSpec("MOD_ALT", 4),
		ConstantSpec("ATOM_CONNECTED", 0),
		ConstantSpec("ATOM_MISSING", 1),
		ConstantSpec("ATOM_EXECUTING", 2),
		ConstantSpec("ATOM_ERROR", 3),
		ConstantSpec("ATOM_DISABLED", 4),
		ConstantSpec("RING_NONE", 0),
		ConstantSpec("RING_SOLID", 1),
		ConstantSpec("RING_DOTTED", 2),
		ConstantSpec("FONT_SMALL", 1),
		ConstantSpec("FONT_NORMAL", 2),
		ConstantSpec("FONT_LARGE", 3),
		ConstantSpec("TEXT_BOLD", 1),
		ConstantSpec("TEXT_ITALIC", 2),
		ConstantSpec("TEXT_UNDERLINE", 4),
		ConstantSpec("COMMAND_MAGIC", gc.MAGIC),
		ConstantSpec("COMMAND_VERSION", gc.VERSION),
		ConstantSpec("COMMAND_HEADER_WORDS", gc.HEADER_WORDS),
		ConstantSpec("COMMAND_HEADER_MAGIC_OFFSET", gc.HEADER_MAGIC_OFFSET),
		ConstantSpec("COMMAND_HEADER_VERSION_OFFSET", gc.HEADER_VERSION_OFFSET),
		ConstantSpec("COMMAND_HEADER_TOTAL_WORDS_OFFSET", gc.HEADER_TOTAL_WORDS_OFFSET),
		ConstantSpec("COMMAND_HEADER_COUNT_OFFSET", gc.HEADER_COMMAND_COUNT_OFFSET),
		ConstantSpec("COMMAND_HEADER_FIRST_OFFSET", gc.HEADER_FIRST_COMMAND_OFFSET),
		ConstantSpec("COMMAND_HEADER_RESERVED_OFFSET", gc.HEADER_RESERVED_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SCENE", gc.ORBIT_SCENE),
		ConstantSpec("COMMAND_ORBIT_WORDS", gc.ORBIT_WORDS),
		ConstantSpec("COMMAND_ORBIT_OPCODE_OFFSET", gc.ORBIT_OPCODE_OFFSET),
		ConstantSpec("COMMAND_ORBIT_WORDS_OFFSET", gc.ORBIT_WORDS_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ENTRY_COUNT_OFFSET", gc.ORBIT_ENTRY_COUNT_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SHELL_COUNT_OFFSET", gc.ORBIT_SHELL_COUNT_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SCENE_X_OFFSET", gc.ORBIT_SCENE_X_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SCENE_Y_OFFSET", gc.ORBIT_SCENE_Y_OFFSET),
		ConstantSpec("COMMAND_ORBIT_CENTER_X_OFFSET", gc.ORBIT_CENTER_X_OFFSET),
		ConstantSpec("COMMAND_ORBIT_CENTER_Y_OFFSET", gc.ORBIT_CENTER_Y_OFFSET),
		ConstantSpec("COMMAND_ORBIT_AREA_WIDTH_OFFSET", gc.ORBIT_AREA_WIDTH_OFFSET),
		ConstantSpec("COMMAND_ORBIT_AREA_HEIGHT_OFFSET", gc.ORBIT_AREA_HEIGHT_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SIDEBAR_WIDTH_OFFSET", gc.ORBIT_SIDEBAR_WIDTH_OFFSET),
		ConstantSpec("COMMAND_ORBIT_RENDER_SCALE_OFFSET", gc.ORBIT_RENDER_SCALE_OFFSET),
		ConstantSpec("COMMAND_ORBIT_OUTER_RADIUS_OFFSET", gc.ORBIT_OUTER_RADIUS_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SHELL_GAP_OFFSET", gc.ORBIT_SHELL_GAP_OFFSET),
		ConstantSpec("COMMAND_ORBIT_CENTER_RADIUS_OFFSET", gc.ORBIT_CENTER_RADIUS_OFFSET),
		ConstantSpec("COMMAND_ORBIT_NODE_RADIUS_OFFSET", gc.ORBIT_NODE_RADIUS_OFFSET),
		ConstantSpec("COMMAND_ORBIT_TILT_OFFSET", gc.ORBIT_TILT_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ROLL_OFFSET", gc.ORBIT_ROLL_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ROTATION_OFFSET", gc.ORBIT_ROTATION_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SURFACE_OFFSET", gc.ORBIT_SURFACE_COLOR_OFFSET),
		ConstantSpec("COMMAND_ORBIT_OUTLINE_OFFSET", gc.ORBIT_OUTLINE_COLOR_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ACCENT_OFFSET", gc.ORBIT_ACCENT_COLOR_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SHELL_COLOR_OFFSET", gc.ORBIT_SHELL_COLOR_OFFSET),
		ConstantSpec("COMMAND_ORBIT_HIGHLIGHT_OFFSET", gc.ORBIT_HIGHLIGHT_COLOR_OFFSET),
		ConstantSpec("COMMAND_ORBIT_POINTER_X_OFFSET", gc.ORBIT_POINTER_X_OFFSET),
		ConstantSpec("COMMAND_ORBIT_POINTER_Y_OFFSET", gc.ORBIT_POINTER_Y_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SHELL_HOVER_OFFSET", gc.ORBIT_SHELL_BUTTON_HOVERED_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ZOOM_HOVER_OFFSET", gc.ORBIT_ZOOM_CONTROLS_HOVERED_OFFSET),
		ConstantSpec("COMMAND_ORBIT_CAMERA_ZOOM_OFFSET", gc.ORBIT_CAMERA_ZOOM_OFFSET),
		ConstantSpec("COMMAND_ORBIT_LABEL_LIMIT_OFFSET", gc.ORBIT_LABEL_CHAR_LIMIT_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ITEM_TABLE_OFFSET", gc.ORBIT_ITEM_TABLE_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ITEM_STRIDE_OFFSET", gc.ORBIT_ITEM_STRIDE_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SHELL_TABLE_OFFSET", gc.ORBIT_SHELL_TABLE_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SHELL_STRIDE_OFFSET", gc.ORBIT_SHELL_STRIDE_OFFSET),
		ConstantSpec("COMMAND_ORBIT_FLAGS_OFFSET", gc.ORBIT_FLAGS_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SHELL_POINTS_OFFSET", gc.ORBIT_SHELL_POINTS_OFFSET),
		ConstantSpec("COMMAND_ORBIT_DRAW_LABELS", gc.ORBIT_FLAG_DRAW_LABELS),
		ConstantSpec("COMMAND_ORBIT_SHELL_WORDS", gc.ORBIT_SHELL_WORDS),
		ConstantSpec("COMMAND_ORBIT_SHELL_PHASE_OFFSET", gc.ORBIT_SHELL_PHASE_OFFSET),
		ConstantSpec("COMMAND_ORBIT_SHELL_POPULATION_OFFSET", gc.ORBIT_SHELL_POPULATION_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ITEM_WORDS", gc.ORBIT_ITEM_WORDS),
		ConstantSpec("COMMAND_ORBIT_ITEM_SHELL_OFFSET", gc.ORBIT_ITEM_SHELL_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ITEM_POSITION_OFFSET", gc.ORBIT_ITEM_POSITION_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ITEM_DIRECTORY_OFFSET", gc.ORBIT_ITEM_DIRECTORY_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ITEM_CHILD_COUNT_OFFSET", gc.ORBIT_ITEM_CHILD_COUNT_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ITEM_NAME_OFFSET", gc.ORBIT_ITEM_NAME_INDEX_OFFSET),
		ConstantSpec("COMMAND_ORBIT_ITEM_SHORT_NAME_OFFSET", gc.ORBIT_ITEM_SHORT_NAME_INDEX_OFFSET),
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
		_builtin("ticks", BuiltInID.OS_TICKS, (), "int", SyscallID.OS_GET_TICKS),
		_builtin("year", BuiltInID.OS_YEAR, (), "int", SyscallID.APP_OS_YEAR),
		_builtin("month", BuiltInID.OS_MONTH, (), "int", SyscallID.APP_OS_MONTH),
		_builtin("day", BuiltInID.OS_DAY, (), "int", SyscallID.APP_OS_DAY),
		_builtin("hour", BuiltInID.OS_HOUR, (), "int", SyscallID.OS_GET_HOUR),
		_builtin("minute", BuiltInID.OS_MINUTE, (), "int", SyscallID.OS_GET_MINUTE),
		_builtin("open_read", BuiltInID.OS_OPEN_READ, ("string",), "File", SyscallID.APP_OS_OPEN_READ),
		_builtin("open_write", BuiltInID.OS_OPEN_WRITE, ("string",), "File", SyscallID.APP_OS_OPEN_WRITE),
		_builtin("open_append", BuiltInID.OS_OPEN_APPEND, ("string",), "File", SyscallID.APP_OS_OPEN_APPEND),
		_builtin("read", BuiltInID.OS_READ, ("File",), "string", SyscallID.APP_OS_READ),
		_builtin("write", BuiltInID.OS_WRITE, ("File", "string"), "bool", SyscallID.APP_OS_WRITE),
		_builtin("close", BuiltInID.OS_CLOSE, ("string",), None, SyscallID.APP_OS_CLOSE),
		_builtin("entry_count", BuiltInID.OS_ENTRY_COUNT, ("string",), "int", SyscallID.APP_OS_ENTRY_COUNT),
		_builtin("entry_name", BuiltInID.OS_ENTRY_NAME, ("string", "int"), "string", SyscallID.APP_OS_ENTRY_NAME),
		_builtin("entry_is_directory", BuiltInID.OS_ENTRY_IS_DIRECTORY, ("string", "int"), "bool", SyscallID.APP_OS_ENTRY_IS_DIRECTORY),
		_builtin("path_exists", BuiltInID.OS_PATH_EXISTS, ("string",), "bool", SyscallID.APP_OS_PATH_EXISTS),
		_builtin("make_file", BuiltInID.OS_MAKE_FILE, ("string",), "bool", SyscallID.APP_OS_MAKE_FILE),
		_builtin("make_directory", BuiltInID.OS_MAKE_DIRECTORY, ("string",), "bool", SyscallID.APP_OS_MAKE_DIRECTORY),
		_builtin("rename", BuiltInID.OS_RENAME, ("string", "string"), "bool", SyscallID.APP_OS_RENAME),
		_builtin("delete", BuiltInID.OS_DELETE, ("string",), "bool", SyscallID.APP_OS_DELETE),
		_builtin("is_directory", BuiltInID.OS_IS_DIRECTORY, ("string",), "bool", SyscallID.APP_OS_IS_DIRECTORY),
		_builtin("copy", BuiltInID.OS_COPY, ("string", "string"), "bool", SyscallID.APP_OS_COPY),
		_builtin("file_size", BuiltInID.OS_FILE_SIZE, ("string",), "int", SyscallID.APP_OS_FILE_SIZE),
		_builtin("modified_ticks", BuiltInID.OS_MODIFIED_TICKS, ("string",), "int", SyscallID.APP_OS_MODIFIED_TICKS),
		_builtin("revision", BuiltInID.OS_REVISION, (), "int", SyscallID.APP_OS_REVISION),
		_builtin("normalize_path", BuiltInID.OS_NORMALIZE_PATH, ("string",), "string", SyscallID.APP_OS_NORMALIZE_PATH),
		_builtin(
			"apply_preferences",
			BuiltInID.OS_APPLY_PREFERENCES,
			("int", "int", "int", "int", "int", "int", "int", "int", "int", "int", "bool"),
			"bool",
			SyscallID.APP_OS_APPLY_PREFERENCES,
		),
	),
	(
		ConstantSpec("VOLUME_MIN", 0),
		ConstantSpec("VOLUME_MAX", 100),
		ConstantSpec("THEME_DARK", 0),
		ConstantSpec("THEME_LIGHT", 1),
		ConstantSpec("CORNER_SQUARE", 0),
		ConstantSpec("CORNER_ROUNDED", 1),
		ConstantSpec("CORNER_SOFT", 2),
		ConstantSpec("ICON_SMALL", 0),
		ConstantSpec("ICON_MEDIUM", 1),
		ConstantSpec("ICON_LARGE", 2),
		ConstantSpec("CLOCK_12_HOUR", 0),
		ConstantSpec("CLOCK_24_HOUR", 1),
	),
	(
		PropertySpec("volume", "int", BuiltInID.OS_GET_VOLUME, BuiltInID.OS_SET_VOLUME, SyscallID.APP_OS_GET_VOLUME, SyscallID.APP_OS_SET_VOLUME),
		PropertySpec("background_id", "int", BuiltInID.OS_GET_BACKGROUND, BuiltInID.OS_SET_BACKGROUND, SyscallID.APP_OS_GET_BACKGROUND, SyscallID.APP_OS_SET_BACKGROUND),
		PropertySpec("palette", "int", BuiltInID.OS_GET_PALETTE, BuiltInID.OS_SET_PALETTE, SyscallID.APP_OS_GET_PALETTE, SyscallID.APP_OS_SET_PALETTE),
		PropertySpec("music_volume", "int", BuiltInID.OS_GET_MUSIC_VOLUME, BuiltInID.OS_SET_MUSIC_VOLUME, SyscallID.APP_OS_GET_MUSIC_VOLUME, SyscallID.APP_OS_SET_MUSIC_VOLUME),
		PropertySpec("sound_effect_volume", "int", BuiltInID.OS_GET_SOUND_EFFECT_VOLUME, BuiltInID.OS_SET_SOUND_EFFECT_VOLUME, SyscallID.APP_OS_GET_SOUND_EFFECT_VOLUME, SyscallID.APP_OS_SET_SOUND_EFFECT_VOLUME),
		PropertySpec("theme_mode", "int", BuiltInID.OS_GET_THEME_MODE, BuiltInID.OS_SET_THEME_MODE, SyscallID.APP_OS_GET_THEME_MODE, SyscallID.APP_OS_SET_THEME_MODE),
		PropertySpec("window_transparency", "int", BuiltInID.OS_GET_WINDOW_TRANSPARENCY, BuiltInID.OS_SET_WINDOW_TRANSPARENCY, SyscallID.APP_OS_GET_WINDOW_TRANSPARENCY, SyscallID.APP_OS_SET_WINDOW_TRANSPARENCY),
		PropertySpec("window_corner_style", "int", BuiltInID.OS_GET_WINDOW_CORNER_STYLE, BuiltInID.OS_SET_WINDOW_CORNER_STYLE, SyscallID.APP_OS_GET_WINDOW_CORNER_STYLE, SyscallID.APP_OS_SET_WINDOW_CORNER_STYLE),
		PropertySpec("icon_size", "int", BuiltInID.OS_GET_ICON_SIZE, BuiltInID.OS_SET_ICON_SIZE, SyscallID.APP_OS_GET_ICON_SIZE, SyscallID.APP_OS_SET_ICON_SIZE),
		PropertySpec("clock_format", "int", BuiltInID.OS_GET_CLOCK_FORMAT, BuiltInID.OS_SET_CLOCK_FORMAT, SyscallID.APP_OS_GET_CLOCK_FORMAT, SyscallID.APP_OS_SET_CLOCK_FORMAT),
		PropertySpec("settings_enabled", "bool", BuiltInID.OS_GET_SETTINGS_ENABLED, BuiltInID.OS_SET_SETTINGS_ENABLED, SyscallID.APP_OS_GET_SETTINGS_ENABLED, SyscallID.APP_OS_SET_SETTINGS_ENABLED),
	),
)


CURRENCY_SPEC = LibrarySpec(
	"currency",
	(
		_builtin("count", BuiltInID.CURRENCY_COUNT, (), "int", SyscallID.APP_CURRENCY_COUNT),
		_builtin("code", BuiltInID.CURRENCY_CODE, ("int",), "string", SyscallID.APP_CURRENCY_CODE),
		_builtin("load", BuiltInID.CURRENCY_LOAD, ("int", "int", "int"), "bool", SyscallID.APP_CURRENCY_LOAD),
		_builtin("status", BuiltInID.CURRENCY_STATUS, (), "int", SyscallID.APP_CURRENCY_STATUS),
		_builtin("rate", BuiltInID.CURRENCY_RATE, (), "float", SyscallID.APP_CURRENCY_RATE),
		_builtin("point_count", BuiltInID.CURRENCY_POINT_COUNT, (), "int", SyscallID.APP_CURRENCY_POINT_COUNT),
		_builtin("point", BuiltInID.CURRENCY_POINT, ("int",), "float", SyscallID.APP_CURRENCY_POINT),
		_builtin("point_date", BuiltInID.CURRENCY_POINT_DATE, ("int",), "string", SyscallID.APP_CURRENCY_POINT_DATE),
	),
	(
		ConstantSpec("RANGE_1D", 0),
		ConstantSpec("RANGE_5D", 1),
		ConstantSpec("RANGE_1W", 2),
		ConstantSpec("RANGE_1M", 3),
		ConstantSpec("RANGE_YTD", 4),
		ConstantSpec("RANGE_5Y", 5),
		ConstantSpec("STATUS_IDLE", 0),
		ConstantSpec("STATUS_LOADING", 1),
		ConstantSpec("STATUS_READY", 2),
		ConstantSpec("STATUS_ERROR", 3),
	),
)


COMPILER_SPEC = LibrarySpec(
	"compiler",
	(
		_builtin("check", BuiltInID.COMPILER_CHECK, ("string",), "bool", SyscallID.APP_COMPILER_CHECK),
		_builtin("error", BuiltInID.COMPILER_ERROR, (), "string", SyscallID.APP_COMPILER_ERROR),
		_builtin("error_line", BuiltInID.COMPILER_ERROR_LINE, (), "int", SyscallID.APP_COMPILER_ERROR_LINE),
		_builtin("error_column", BuiltInID.COMPILER_ERROR_COLUMN, (), "int", SyscallID.APP_COMPILER_ERROR_COLUMN),
		_builtin("assembly", BuiltInID.COMPILER_ASSEMBLY, (), "string", SyscallID.APP_COMPILER_ASSEMBLY),
		_builtin("bytecode_size", BuiltInID.COMPILER_BYTECODE_SIZE, (), "int", SyscallID.APP_COMPILER_BYTECODE_SIZE),
		_builtin("load_visual", BuiltInID.COMPILER_LOAD_VISUAL, ("string",), "int", SyscallID.APP_COMPILER_LOAD_VISUAL),
		_builtin("atom_count", BuiltInID.COMPILER_ATOM_COUNT, (), "int", SyscallID.APP_COMPILER_ATOM_COUNT),
		_builtin("atom_text", BuiltInID.COMPILER_ATOM_TEXT, ("int",), "string", SyscallID.APP_COMPILER_ATOM_TEXT),
		_builtin("atom_kind", BuiltInID.COMPILER_ATOM_KIND, ("int",), "int", SyscallID.APP_COMPILER_ATOM_KIND),
		_builtin("atom_line", BuiltInID.COMPILER_ATOM_LINE, ("int",), "int", SyscallID.APP_COMPILER_ATOM_LINE),
		_builtin("atom_enabled", BuiltInID.COMPILER_ATOM_ENABLED, ("int",), "bool", SyscallID.APP_COMPILER_ATOM_ENABLED),
		_builtin("set_atom_enabled", BuiltInID.COMPILER_SET_ATOM_ENABLED, ("int", "bool"), "bool", SyscallID.APP_COMPILER_SET_ATOM_ENABLED),
		_builtin("visual_source", BuiltInID.COMPILER_VISUAL_SOURCE, (), "string", SyscallID.APP_COMPILER_VISUAL_SOURCE),
		_builtin("script_count", BuiltInID.COMPILER_SCRIPT_COUNT, (), "int", SyscallID.APP_COMPILER_SCRIPT_COUNT),
		_builtin("script_name", BuiltInID.COMPILER_SCRIPT_NAME, ("int",), "string", SyscallID.APP_COMPILER_SCRIPT_NAME),
		_builtin("script_shell", BuiltInID.COMPILER_SCRIPT_SHELL, ("int",), "int", SyscallID.APP_COMPILER_SCRIPT_SHELL),
		_builtin("script_line", BuiltInID.COMPILER_SCRIPT_LINE, ("int",), "int", SyscallID.APP_COMPILER_SCRIPT_LINE),
		_builtin("script_enabled", BuiltInID.COMPILER_SCRIPT_ENABLED, ("int",), "bool", SyscallID.APP_COMPILER_SCRIPT_ENABLED),
		_builtin("load_document", BuiltInID.COMPILER_LOAD_DOCUMENT, ("int", "string", "string"), "int", SyscallID.APP_COMPILER_LOAD_DOCUMENT),
		_builtin("document_script_count", BuiltInID.COMPILER_DOCUMENT_SCRIPT_COUNT, ("int",), "int", SyscallID.APP_COMPILER_DOCUMENT_SCRIPT_COUNT),
		_builtin("document_script_name", BuiltInID.COMPILER_DOCUMENT_SCRIPT_NAME, ("int", "int"), "string", SyscallID.APP_COMPILER_DOCUMENT_SCRIPT_NAME),
		_builtin("document_script_shell", BuiltInID.COMPILER_DOCUMENT_SCRIPT_SHELL, ("int", "int"), "int", SyscallID.APP_COMPILER_DOCUMENT_SCRIPT_SHELL),
		_builtin("document_script_line", BuiltInID.COMPILER_DOCUMENT_SCRIPT_LINE, ("int", "int"), "int", SyscallID.APP_COMPILER_DOCUMENT_SCRIPT_LINE),
		_builtin("document_script_enabled", BuiltInID.COMPILER_DOCUMENT_SCRIPT_ENABLED, ("int", "int"), "bool", SyscallID.APP_COMPILER_DOCUMENT_SCRIPT_ENABLED),
		_builtin("document_source", BuiltInID.COMPILER_DOCUMENT_SOURCE, ("int",), "string", SyscallID.APP_COMPILER_DOCUMENT_SOURCE),
		_builtin("run", BuiltInID.COMPILER_RUN, ("string",), "string", SyscallID.APP_COMPILER_RUN),
		_builtin("check_workspace", BuiltInID.COMPILER_CHECK_WORKSPACE, ("string",), "bool", SyscallID.APP_COMPILER_CHECK_WORKSPACE),
		_builtin("run_workspace", BuiltInID.COMPILER_RUN_WORKSPACE, ("string",), "string", SyscallID.APP_COMPILER_RUN_WORKSPACE),
	),
	(
		ConstantSpec("ATOM_EVENT", 0),
		ConstantSpec("ATOM_INSTRUCTION", 1),
		ConstantSpec("ATOM_VALUE", 2),
		ConstantSpec("ATOM_CONDITION", 3),
		ConstantSpec("ATOM_DECLARATION", 4),
	),
)


AUDIO_SPEC = LibrarySpec(
	"audio",
	(
		_builtin("load", BuiltInID.AUDIO_LOAD_TRACK, ("string",), "Track", SyscallID.APP_AUDIO_LOAD_TRACK),
		_builtin("play", BuiltInID.AUDIO_PLAY, ("Track",), "bool", SyscallID.APP_AUDIO_PLAY),
		_builtin("pause", BuiltInID.AUDIO_PAUSE, ("Track",), "bool", SyscallID.APP_AUDIO_PAUSE),
		_builtin("stop", BuiltInID.AUDIO_STOP, ("Track",), "bool", SyscallID.APP_AUDIO_STOP),
		_builtin("seek", BuiltInID.AUDIO_SEEK, ("Track", "int"), "bool", SyscallID.APP_AUDIO_SEEK),
		_builtin("position", BuiltInID.AUDIO_POSITION, ("Track",), "int", SyscallID.APP_AUDIO_POSITION),
		_builtin("duration", BuiltInID.AUDIO_DURATION, ("Track",), "int", SyscallID.APP_AUDIO_DURATION),
		_builtin("is_playing", BuiltInID.AUDIO_IS_PLAYING, ("Track",), "bool", SyscallID.APP_AUDIO_IS_PLAYING),
		_builtin("update", BuiltInID.AUDIO_UPDATE, ("Track", "int"), None, SyscallID.APP_AUDIO_UPDATE),
		_builtin("active_pitch", BuiltInID.AUDIO_ACTIVE_PITCH, ("Track",), "int", SyscallID.APP_AUDIO_ACTIVE_PITCH),
	),
)


STANDARD_LIBRARY_SPECS = (GRAPHICS_SPEC, OS_SPEC, CURRENCY_SPEC, COMPILER_SPEC, AUDIO_SPEC)

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
