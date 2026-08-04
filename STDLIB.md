# Xe graphics, OS, and currency standard libraries

The public app API has three modules. `graphics` owns the plain `Window` type,
drawing, widgets, and input. `os` owns the solid screen background, system settings,
process utilities, and text files. `currency` provides bundled reference rates and
history without runtime network access. There is no separate `window` module or
shell UI.

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

Pixel density is fixed for the lifetime of the graphics device; resizing or
maximizing a window never changes it. On the default `480x360` framebuffer, one
logical application pixel is always a crisp `2x2` framebuffer block. Resizing
therefore reveals more or less logical content instead of stretching, recentering,
or rescaling the existing UI. There is no fractional scaling or interpolation.

Use `graphics::content_width(win)` and `graphics::content_height(win)` after
`begin_draw` to read the current drawable size in logical application pixels.
These values exclude the title bar and border and update after a resize or
maximize/restore operation. Use them to anchor controls and choose responsive
layouts. The `Window.width` and `Window.height` fields and `graphics::width()` /
`graphics::height()` remain framebuffer-pixel values.

The title bar contains only maximize/restore and close controls. Maximize fills the
available framebuffer and pressing the same control again restores the last normal
bounds. Close destroys the window. The three `Window` methods above remain the
stable programmatic API, including `is_minimized()` even though this chrome does
not add a taskbar or minimize button.

While a title bar is dragged, the committed window stays in place and a
one-framebuffer-pixel white outline follows the pointer. Releasing commits the
final clamped outline position once. Width and height are clamped to the framebuffer;
geometry writes are ignored while moving, resizing, or fullscreen.

All four edges and four corners resize a normal window. During resizing, the
committed window and its content remain unchanged while the same white outline
follows the pointer. Release atomically commits the clamped outline, with a minimum
window size of `96x72`. This is only large enough to retain usable title controls
and a compact content region. Application drawing keeps the same pixel density before,
during, and after that commit.

## `graphics`

- Lifecycle: `begin_draw(win)`, `update(win)`
- Screen information: `width()`, `height()`, `content_width(win)`,
  `content_height(win)`, `pointer_x(win)`, `pointer_y(win)`
- Shapes: `clear`, `set_pixel`, `draw_circle`, `draw_line`, `draw_rect`, `fill_rect`
- Text: `draw_text`, `draw_char`, `draw_int`, `draw_float`, plus the compact
  `draw_text_small`, `draw_char_small`, `draw_int_small`, and `draw_float_small`
- Controls: `button`, `button_tone`, `button_flat`, `slider`
- Input: `mouse_x`, `mouse_y`, `mouse_down`, `mouse_pressed`, `mouse_released`,
  `key_down`, `read_key`
- Constants: screen dimensions, `COLOR_0` through `COLOR_15`, `BLACK`, `WHITE`,
  window states, and common key codes

Every drawing/control function takes a `graphics::Window` as its first argument.
Colors are palette indices, so an OS palette change recolors output without
changing application drawing code. Coordinates supplied to shapes, text, and
controls use the same fixed logical-pixel density. `button_tone` has the same
interaction behavior as `button` plus a final palette-index argument for the
normal fill color; hover and pressed colors remain derived by the window theme.
`button_flat` accepts the same arguments as `button_tone` but omits the frame,
which is useful for lightweight menu rows and palette swatches. Controls seven
logical pixels high or shorter use the compact font automatically.

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

Text files use the opaque `os::File` resource:

```xe
var file: os::File
file = os::open_read("example.txt")
out << os::read(file)
call os::close("example.txt")
```

`open_write(path)` and `write(file, text)` provide the write side. Paths are
relative to the runtime filesystem root; absolute paths and `..` traversal outside
that root are rejected. Files are also closed automatically when the VM stops.

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
`point(index)` reads a sample.

Ranges are `RANGE_1D`, `RANGE_5D`, `RANGE_1W`, `RANGE_1M`, `RANGE_YTD`, and
`RANGE_5Y`. YTD data is grouped weekly and five-year data monthly. Cross rates
are calculated from the same USD-relative row, so every listed pair and reverse
pair stays internally consistent. The runtime imports no HTTP or worker-thread
code. Before a product update, run `python tools/update_currency_snapshot.py` to
refresh `xe_lang/devices/currency_snapshot.py`; the generated `SNAPSHOT_DATE`
records the final included day.

## XVM syscall ABI

`xe_lang/syscall_abi.py` is the single source of truth for numeric syscall IDs.
The documented XVM ABI is kept separate from the higher-level Xe app library so
assembly programs and compiled `graphics::`/`os::` calls cannot collide.

- Text/string: `1-12` (`1` `OUTPUT_CHARS`, `2` `READ_STR`/`READ_CHARS`,
  `3-8` character-buffer conversions, `9` `PUT_CHAR`, and `10-12`
  concatenate/compare/update string length).
- Runtime: `20-24` (elapsed milliseconds, allocate, free, exit, sleep).
- Raw graphics: `30-53` and `55` (buffer operations, clipping, primitives,
  image/text/window/button drawing). Raw taskbar `52` and TaskAtom `53` remain
  available to assembly programs, but Calculator and Settings do not call them.
- Raw input: `60-64` (mouse polling, previous event, keyboard polling, key state,
  and pointer bounds testing).
- Backend request: `80` `REQUEST`, through the runtime's injectable synchronous request
  handler. With no handler, it deterministically writes an empty response.
- High-level app extensions: graphics `100-129` and `142-145`, OS
  settings/utilities `130-141` and `180-196`, `Window` methods `150-152`, files
  `160-164`, mutable string append operations `170-171`, and currency `200-206`.

IDs absent from that list are reserved, including `13-19`, `25-29`, `54`, and
`56-59`; invoking one reports `Unknown system call` rather than silently doing
the wrong operation.

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
Calculator, Settings, and Xenon Terminal in normal, dragging, resizing, resized,
minimum-size, and maximized states into `%TEMP%\xe-app-previews`.
