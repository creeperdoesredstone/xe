# Xe graphics, OS, currency, and compiler standard libraries

The public app API has four modules. `graphics` owns the plain `Window` type,
drawing, widgets, and input. `os` owns the solid screen background, system settings,
process utilities, and filesystem access. `currency` provides bundled reference
rates and history without runtime network access. `compiler` exposes the in-VM Xe
compiler and visual-document model. There is no separate `window` module or shell UI.

## Window and frame lifecycle

`graphics::Window` is the only public window type. It exposes the framebuffer-pixel
geometry fields `x`, `y`, `width`, and `height`, plus `title` and
read-only-by-convention `state`. Its method surface remains exactly:

- `close()` — closes and destroys the window.
- `is_fullscreen()` — returns whether the window is fullscreen.
- `is_minimized()` — returns whether the window is minimized.

A normal graphical application is structured like this:

```xe
var win: graphics::Window
var content_width: int
var content_height: int

win.x = 40
win.y = 30
win.width = 200
win.height = 140
win.title = "Example"
win.ui_scale = 1
win.state = graphics::WINDOW_NORMAL

while (win.state != graphics::WINDOW_CLOSED) {
	call graphics::begin_draw(win)
	content_width = graphics::content_width(win)
	content_height = graphics::content_height(win)
	call graphics::clear(win, graphics::BLACK)
	call graphics::draw_circle(win, 30, 30, 12, graphics::COLOR_13)
	call graphics::draw_line(win, 5, 5, content_width - 6, content_height - 6, graphics::WHITE)
	call graphics::update(win)
	call os::sleep(16)
}
```

`begin_draw` updates window input/state, clears the solid background, draws the window chrome, and
clips subsequent rendering to the content area. Drawing coordinates are relative
to the top-left of that content area. `update` draws any move/resize outline last
and publishes one immutable frame.

`Window.ui_scale` selects an integer pixel density. New Xenon apps use `1`; legacy
programs that leave it unset retain the crisp `2x2` default. Resizing reveals more or
less logical content instead of stretching an old frame. There is no fractional
scaling inside the Scratch framebuffer. During maximize or restore,
`is_fullscreen()` reports the target state as soon as the transition starts. This
lets an application select its matching responsive density before the captured
window reaches its target and prevents a final-frame typography or menu-size jump.
Calculator uses that behavior to map its compact `240x190` design to a readable 2x
fullscreen layout.

Use `graphics::content_width(win)` and `graphics::content_height(win)` after
`begin_draw` to read the current drawable size in logical application pixels.
These values exclude the title bar and border and update after a resize or
maximize/restore operation. Use them to anchor controls and choose responsive
layouts. The `Window.width` and `Window.height` fields and `graphics::width()` /
`graphics::height()` remain framebuffer-pixel values.

The title bar contains only maximize/restore and close controls. Maximize fills the
available framebuffer and pressing the same control again restores the last normal
bounds. Both directions use one 210 ms cubic ease-out curve for position, size, title bar,
title text, controls, and content. During the transition the manager scales an
immutable snapshot of the complete last window, blocks content hit-testing, and
commits the exact target geometry at completion; this prevents responsive controls
from colliding or the chrome jumping while the frame is moving. The generic
transition record also accepts a future taskbar-icon target for minimize animation.
Close destroys the window. The three `Window` methods above remain the
stable programmatic API, including `is_minimized()` even though this chrome does
not add a taskbar or minimize button.

The host Xenon IDE always displays the complete `480x360` Scratch stage with a
continuous aspect-preserving viewport zoom and nearest-neighbor sampling. At the
default `1200x760` workbench size the normal graphics pane is at least `480x360`;
Maximize Graphics View lets the same 4:3 stage consume the full workbench instead of
leaving the widget at its former `240x180` size hint.

While a title bar is dragged, the committed window stays in place and a
one-framebuffer-pixel white outline follows the pointer. Releasing commits the
final clamped outline position once. Width and height are clamped to the framebuffer;
geometry writes are ignored while moving, resizing, or fullscreen.

All four edges and four corners resize a normal window. During resizing, the
committed window and its content remain unchanged while the same white outline
follows the pointer. Release atomically commits the clamped outline, with a minimum
window size of `72x54`. Applications switch to explicit summary layouts at that
absolute minimum and restore their full layout without mutating state. Drawing keeps
the same integer pixel density before, during, and after the commit.

## `graphics`

- Lifecycle: `begin_draw(win)`, `update(win)`
- Screen information: `width()`, `height()`, `content_width(win)`,
  `content_height(win)`, `pointer_x(win)`, `pointer_y(win)`
- Shapes: `clear`, `set_pixel`, `draw_circle`, `draw_line`, `draw_rect`, `fill_rect`,
  and `draw_atom`
- Text: `draw_text`, `draw_char`, `draw_int`, `draw_float`, plus the compact
  `draw_text_small`, `draw_char_small`, `draw_int_small`, and `draw_float_small`
- Controls: `button`, `button_tone`, `button_flat`, `slider`
- Input: `mouse_x`, `mouse_y`, `mouse_down`, `mouse_pressed`, `mouse_released`,
  `right_mouse_down`, `right_mouse_pressed`, `right_mouse_released`, `key_down`,
  `read_key`, and `modifiers`
- Constants: screen dimensions, `COLOR_0` through `COLOR_15`, `BLACK`, `WHITE`,
  window states, atom/ring states, `MOUSE_LEFT`, `MOUSE_RIGHT`, common key codes,
  and `MOD_SHIFT`, `MOD_CTRL`, `MOD_ALT`

`read_key()` emits an initial key press and deterministic held-key repeats after a
380 ms delay at roughly 45 ms intervals. The queue is bounded after a stalled frame,
and Ctrl/Alt shortcuts are never repeated. `modifiers()` returns a bit mask composed
from the three `MOD_*` constants, so editors can implement Ctrl commands without
depending on host-specific key values.

The three unprefixed mouse functions report the primary/left button. The matching
`right_mouse_*` functions expose the secondary button without changing the raw
mouse ABI, allowing contextual menus to remain deterministic in Xe applications.

Every drawing/control function takes a `graphics::Window` as its first argument.
Colors are palette indices, so an OS palette change recolors output without
changing application drawing code. Coordinates supplied to shapes, text, and
controls use the same fixed logical-pixel density. `button_tone` has the same
interaction behavior as `button` plus a final palette-index argument for the
normal fill color; hover and pressed colors remain derived by the window theme.
`button_flat` accepts the same arguments as `button_tone` but omits the frame,
which is useful for lightweight menu rows and palette swatches. Controls seven
logical pixels high or shorter use the compact font automatically. Flat dropdown
rows use a palette-adjacent hover tint rather than a dark outline. An unlabeled flat
menu surface also establishes the immediate-mode overlay hit layer, so later-drawn
menu rows own clicks instead of controls visually underneath them.

`read_key()` reports printable characters with their actual case. For example,
an unshifted letter produces its lowercase character code, Shift produces the
uppercase code, and Tab produces `9` for deterministic autocomplete handling.

Mutable string editors can grow a descriptor safely and refresh its cached length:

```xe
call xestring::append(text, "sqrt(")
call xestring::append_char(text, '9')
text[xestring::strlen(text) - 1] = '\0'
call xestring::update_length(text)
```

`append` and `append_char` reallocate the character buffer when required while
keeping the string descriptor stable. `update_length` wraps raw syscall `12` and
is useful after direct character-buffer edits.

## `os`

The mutable settings are properties:

```xe
os::volume = 100
os::music_volume = 80
os::sound_effect_volume = 70
os::background_id = 0
os::palette = 0
os::theme_mode = os::THEME_DARK
os::window_transparency = 20
os::window_corner_style = os::CORNER_ROUNDED
os::icon_size = os::ICON_MEDIUM
os::clock_format = os::CLOCK_24_HOUR
os::settings_enabled = true
```

Reading the same names returns the current OS-owned value. All three volume values
and transparency are clamped to `0-100`; enum-like values and background/palette
IDs are validated. `background_count()` and `palette_count()` expose the valid ID
ranges. `apply_settings` remains available for the original atomic three-value
commit. `apply_preferences(master, music, effects, background, palette, theme,
transparency, corners, icons, clock, enabled)` validates first and atomically
commits the complete Settings model.

Palettes `0-2` are the dark Xenon variants and `3-5` are coordinated light
variants. Changing `theme_mode` preserves the palette variant while moving it to
the matching light or dark group. The OS remains the persistence owner; apps
should stage edits locally and use `apply_preferences` only when Apply is chosen.

`background_id` selects a solid backdrop color. The OS clears the framebuffer to
that palette-backed color before drawing windows; it does not synthesize desktop
art, a dock, or taskbar sprites. The current built-in backdrop IDs are Black,
Navy, and Slate, and applications should use IDs returned by the OS rather than
assuming that list will never grow.

Runtime utilities are `sleep(milliseconds)`, `exit(status)`, and `ticks()`.
`exit` ends the current VM cooperatively and records its status code.
Local calendar components are available as integer functions `year()`, `month()`,
`day()`, `hour()`, and `minute()`. `hour()` and `minute()` preserve the canonical
raw syscalls `28` and `29`; the date components are high-level app extensions.

Text files use the opaque `os::File` resource:

```xe
var file: os::File
file = os::open_read("example.txt")
out << os::read(file)
call os::close("example.txt")
```

`open_write(path)` and `write(file, text)` provide the write side. Unless a host
explicitly supplies a test/integration root, the runtime uses the private
`%LOCALAPPDATA%/XenonOS/VirtualDrive` directory (or
`~/.xenonos/VirtualDrive` when `LOCALAPPDATA` is unavailable). It never defaults to
the process working directory. Paths are relative to that virtual drive; absolute
paths, internal metadata names, and `..` traversal outside the root are rejected.
Files are also closed automatically when the VM stops.

Directory and workspace operations are `entry_count(path)`, `entry_name(path,
index)`, `entry_is_directory(path, index)`, `path_exists(path)`, `make_file(path)`,
`make_directory(path)`, `rename(old_path, new_path)`, and `delete(path)`. Entries are
sorted with directories first and then names case-insensitively. Open files cannot be
deleted until closed, and the filesystem root itself cannot be deleted. `delete`
moves the target into a hidden `.xenon-trash` directory with a collision-safe
timestamped name. That directory and the drive marker are inaccessible and omitted
from Xe directory listings, while remaining available to the host for recovery.

## `currency`

The currency library exposes common currency codes backed by a release-time
reference-rate snapshot:

```xe
var ready: bool
var status: int
var rate: float

ready = currency::load(0, 1, currency::RANGE_1M)
status = currency::status()
if (status == currency::STATUS_READY) {
	rate = currency::rate()
}
```

`count()` returns the selector size and `code(index)` returns its three-letter
code. `load(from_index, to_index, range)` selects bundled data and returns ready
immediately. The status constants remain stable for API compatibility, although
normal bundled loads transition directly to `STATUS_READY`. `rate()` is the
snapshot conversion rate, `point_count()` is the graph sample count, and
`point(index)` reads a sample and `point_date(index)` returns its visible date label.

Ranges are `RANGE_1D`, `RANGE_5D`, `RANGE_1W`, `RANGE_1M`, `RANGE_YTD`, and
`RANGE_5Y`. YTD data is grouped weekly and five-year data monthly. Cross rates
are calculated from the same USD-relative row, so every listed pair and reverse
pair stays internally consistent. The runtime imports no HTTP or worker-thread
code. Before a product update, run `python tools/update_currency_snapshot.py` to
refresh `xe_lang/devices/currency_snapshot.py`; the generated `SNAPSHOT_DATE`
records the final included day.

## `compiler`

`compiler::check(source)` runs the Xe lexer, parser, semantic analyzer, optimizer,
code generator, and assembler in-process. Diagnostics are available through
`error()`, `error_line()`, and `error_column()`; successful builds expose
`assembly()` and `bytecode_size()`.

The original line-atom API remains available: `load_visual`, `atom_count`,
`atom_text`, `atom_kind`, `atom_line`, `atom_enabled`, `set_atom_enabled`, and
`visual_source`. The script graph is a compatible higher-level model:

```xe
var scripts: int
scripts = compiler::load_document(0, "workspace.xe", source)
out << compiler::document_script_name(0, 0)
out << compiler::document_script_shell(0, 0)
```

Top-level main/function/procedure/class-like scripts are grouped by call connectivity.
Connected scripts share a shell; disconnected components receive different shells.
Four independent document slots are available for Scratch-portable multi-view tools.
Use `document_script_count`, `document_script_name`, `document_script_shell`,
`document_script_line`, `document_script_enabled`, and `document_source`. The
single-document `script_*` calls describe the most recent `load_visual` result.

## XVM syscall ABI

`xe_lang/syscall_abi.py` is the single source of truth for numeric syscall IDs.
The documented XVM ABI is kept separate from the higher-level Xe app library so
assembly programs and compiled `graphics::`/`os::` calls cannot collide.

- Text/string: `1-12` (`1` `OUTPUT_CHARS`, `2` `READ_STR`/`READ_CHARS`,
  `3-8` character-buffer conversions, `9` `PUT_CHAR`, and `10-12`
  concatenate/compare/update string length).
- Runtime: `20-29` (`OS_GET_TICKS`, `OS_MALLOC`, `OS_FREE`, `OS_EXIT`, `OS_SLEEP`,
  deterministic `OS_RAND32`/`OS_RANDF`/`OS_RSEED`, and hour/minute queries). The
  former unprefixed enum names remain aliases so existing assembled projects keep
  their numeric ABI.
- Raw graphics: `30-53`, `55`, and `56` (buffer operations, clipping, primitives,
  image/text/window/button drawing). Raw taskbar `52` and TaskAtom `53` remain
  available to assembly programs, but Calculator and Settings do not call them.
- Raw input: `60-64` (mouse polling, previous event, keyboard polling, key state,
  and pointer bounds testing).
- Backend request: `80` `REQUEST`, through the runtime's injectable synchronous request
  handler. With no handler, it deterministically writes an empty response.
- High-level app extensions: graphics `100-129`, `142-145`, `208`, and `246-249`, OS
  settings/utilities `130-141` and `180-196`, `Window` methods `150-152`, files
  `160-164` and `210-217`, mutable string append operations `170-171`, currency
  `200-207`, compiler services `220-245`, and calendar date components `250-252`.

IDs absent from those ranges are reserved, including `13-19`, `54`, and `57-59`;
invoking one reports `Unknown system call` rather than silently doing the wrong
operation.

## VM address space

The default XVM data space is exactly `200,000` addresses, numbered `0..199,999`.
This is also the hard maximum so the same program remains representable in Scratch.
The direct `LOAD`/`STORE` XAssembly encoding remains 16-bit for binary compatibility;
the expanded heap is reached through 32-bit pointers and indirect loads/stores.
Embedders may request `65,536..200,000` addresses with
`RuntimeContext(memory_words=...)`; larger or smaller requests are rejected.
Runtime-created strings use conservative managed-heap collection. Globals, live
stack values, interior pointers, and manually owned `os::malloc` blocks are roots;
only unreachable runtime-managed string blocks are reclaimed. This prevents
per-frame filesystem/compiler/currency string results from exhausting the VM while
preserving explicit `malloc`/`free` ownership.

Raw graphics use the XVM's `240x180` logical coordinate space on the default
`480x360` framebuffer. Coordinates are expanded with nearest-neighbor 2x blocks.
The region passed to syscall `37` is `[x1, y1, x2, y2)`.

Raw image syscall `48` reads `[width, height, pixels...]` at its address. Format
`0` stores one palette index per word. Format `1` packs four palette indices into
each word, least-significant byte first. Its scale argument is an IEEE-754
single-precision float and rendering remains nearest-neighbor.

Mouse events are `0` none, `1` move, `2` press, `3` release. Keyboard events are
`0` none, `1` press, `2` release. Device IDs for syscall `61` are `0` mouse and
`1` keyboard. Syscall `64` leaves the stack unchanged and writes its half-open
rectangle test for `[x, x + width) x [y, y + height)` to the VM condition register.

Xe input is available for string variables:

```xe
var value: string
in >> value
out << value
```

The IDE exposes a temporary terminal input field whenever syscall `2` blocks.
Embedders can provide `RuntimeContext(input_handler=..., request_handler=...)`.
The compiler emits syscall `1` for character-buffer output, syscall `9` for
single-character output, and syscall `2` for `in >> string`. These numeric uses
come from `SyscallID`, so compiler lowering and VM dispatch cannot drift apart.

## Extending a built-in

1. Add its stable ID and typed declaration under `xe_lang/stdlib/`.
2. Implement the operation in the matching modular device under `xe_lang/devices/`.
3. Register its syscall and add compiler plus runtime coverage.

Run `python tests/render_app_preview.py` to render nearest-neighbor PNG previews of
all six apps in normal, dragging, resizing, resized, minimum-size, and maximized
states into `%TEMP%\xe-app-previews`. Additional `visual`, `multiview`, `currency`,
and `menu` states cover the richer overlays.
