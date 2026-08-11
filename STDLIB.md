# Xe graphics, OS, currency, compiler, and audio standard libraries

Five device-facing extension modules are documented here. `graphics` owns the frameless `Screen` target,
the plain `Window` type, drawing, widgets, and input. `os` owns the solid screen
background, system settings,
process utilities, and filesystem access. `currency` provides bundled reference
rates and history without runtime network access. `compiler` exposes the in-VM Xe
compiler and visual-document model. `audio` owns portable XMusic tracks and their
deterministic sequencer state. There is no separate `window` module or shell UI.

## Screen, Window, and frame lifecycle

`graphics::Screen` draws directly into the complete Scratch stage. It has no title,
position, state, border, lifecycle methods, dragging, resizing, minimizing, or
maximizing. After `begin_draw`, its runtime-owned `width` and `height` fields contain
the current framebuffer dimensions (`480x360` in XVM and the intended Scratch host).

```xe
var bg: graphics::Screen

call graphics::begin_draw(bg)
call graphics::clear(bg, graphics::COLOR_1)
call graphics::fill_rect(bg, 8, 8, 96, 28, graphics::COLOR_5)
call graphics::draw_text(bg, 14, 18, "Desktop", graphics::WHITE)
call graphics::draw_icon(bg, 116, 12, 5, 5, ".AAA.A...AAAAAAA...AA.A.A")
call graphics::draw_icon_scaled(bg, 128, 12, 5, 5, ".AAA.A...AAAAAAA...AA.A.A", 2)
call graphics::update(bg)
```

Screen coordinates are absolute stage pixels with origin `(0, 0)` and scale `1`.
`begin_draw(bg)` restores the OS-selected solid background and a full-stage clip;
`update(bg)` publishes one immutable frame. It creates no `Window` object or hidden
window-manager record and therefore cannot produce window chrome accidentally.

`graphics::Window` exposes the framebuffer-pixel
geometry fields `x`, `y`, `width`, and `height`, plus `title`, `ui_scale`, and
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
}
```

For a `Window`, `begin_draw` updates window input/state, clears the solid background,
draws the window chrome, and clips subsequent rendering to the content area. Drawing
coordinates are relative to the top-left of that content area. `update` draws any
move/resize outline last and publishes one immutable frame.

`graphics::update` is the host's 60 Hz frame boundary. Application loops should use
`os::ticks()` deltas for motion and should not add a second fixed `sleep(16)` after
each update; this avoids double throttling while keeping the same deterministic
state progression for the Scratch host.

`Window.ui_scale` selects an integer pixel density. New Xenon apps use `1`; legacy
programs that leave it unset retain the crisp `2x2` default. Resizing reveals more or
less logical content instead of stretching an old frame. There is no fractional
scaling inside the Scratch framebuffer. During maximize or restore,
`is_fullscreen()` reports the target state as soon as the transition starts. This
lets an application select its matching responsive density before the captured
window reaches its target and prevents a final-frame typography or menu-size jump.
Calculator keeps a stable logical density through the transition and uses the newly
available content area to reflow its controls without a final-frame scale jump.

Use `graphics::content_width(win)` and `graphics::content_height(win)` after
`begin_draw` to read the current drawable size in logical application pixels.
These values exclude the title bar and border and update after a resize or
maximize/restore operation. Use them to anchor controls and choose responsive
layouts. The `Window.width` and `Window.height` fields and `graphics::width()` /
`graphics::height()` remain framebuffer-pixel values.

The title bar contains only maximize/restore and close controls. Maximize fills the
available framebuffer and pressing the same control again restores the last normal
bounds. Dragging a title to the six-pixel top-edge snap region previews those bounds
with the same white outline; moving away cancels the preview, while releasing starts
the normal maximize transition and retains the pointer-aligned floating bounds for a
later restore. Both directions use one 210 ms cubic ease-out curve for position, size, title bar,
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

- Lifecycle: `begin_draw(target)`, `update(target)` for a `Screen` or `Window`
- Screen information: `width()`, `height()`, `content_width(target)`,
  `content_height(target)`, `pointer_x(target)`, `pointer_y(target)`
- Shapes: `clear`, `set_pixel`, `draw_circle`, `draw_line`, `draw_rect`, `fill_rect`,
  `draw_atom`, `draw_icon`, `draw_icon_scaled`, and checked `draw_commands`
- Text: `draw_text`, `draw_char`, `draw_int`, `draw_float`, plus the compact
  `draw_text_small`, `draw_char_small`, `draw_int_small`, and `draw_float_small`;
  `char_advance` and `draw_char_styled` provide proportional layout with composable
  bold, italic, and underline flags
- Controls: `button`, `button_tone`, `button_flat`, `slider`
- Input: `mouse_x`, `mouse_y`, `mouse_down`, `mouse_pressed`, `mouse_released`,
  `right_mouse_down`, `right_mouse_pressed`, `right_mouse_released`, `key_down`,
  `read_key`, `modifiers`, and `scroll_delta`
- Constants: screen dimensions, `COLOR_0` through `COLOR_15`, `BLACK`, `WHITE`,
  window states, atom/ring states, `MOUSE_LEFT`, `MOUSE_RIGHT`, common key codes,
  `MOD_SHIFT`, `MOD_CTRL`, `MOD_ALT`, `FONT_SMALL`, `FONT_NORMAL`, `FONT_LARGE`,
  `TEXT_BOLD`, `TEXT_ITALIC`, `TEXT_UNDERLINE`, and the `COMMAND_*` stream-layout
  constants

`read_key()` emits an initial key press and deterministic held-key repeats after a
380 ms delay at roughly 45 ms intervals. The queue is bounded after a stalled frame,
and Ctrl/Alt shortcuts are never repeated. `modifiers()` returns a bit mask composed
from the three `MOD_*` constants, so editors can implement Ctrl commands without
depending on host-specific key values.

The three unprefixed mouse functions report the primary/left button. The matching
`right_mouse_*` functions expose the secondary button without changing the raw
mouse ABI, allowing contextual menus to remain deterministic in Xe applications.

`scroll_delta()` returns signed logical wheel notches: positive values mean up or
away from the user, and negative values mean down or toward the user. Multiple
events accumulate, so the magnitude may exceed one. The value is stable across
all reads in one `begin_draw`/`update` frame and is consumed by `update`; the next
frame reads zero unless more wheel input arrived. Standalone and IDE hosts normalize
their native wheel units. A native Shift+wheel event latches `MOD_SHIFT` with that
wheel step even if the physical key state changes before the next Xe frame, allowing
horizontal viewports to use `scroll_delta()` and `modifiers()` without a race.

Vanilla Scratch reports wheel motion by starting Up/Down key hats and does not add
that motion to its held-key list. It also hides physical modifier keys from projects.
The full Scratch VM therefore consumes Up/Down hats as vertical wheel pulses and
offers a visible horizontal-axis latch plus Left/Right fallback; those horizontal
pulses are exposed to Xe with `MOD_SHIFT`. Native hosts retain physical Shift+wheel.
Other embedders may inject the same signed steps or leave the value at its
deterministic zero default.

Lifecycle, size/pointer, shape, text, atom, and icon functions accept either a
`graphics::Screen` or `graphics::Window` as their first argument. Controls remain
Window-only because they participate in window-local capture and overlay ordering.
Colors are palette indices, so an OS palette change recolors output without
changing application drawing code. Coordinates supplied to shapes, text, and
controls use the target's fixed logical-pixel density.

`draw_icon(target, x, y, width, height, pixels)` consumes row-major pixel symbols:
`0-9` and `A-F`/`a-f` select palette indices, `.` is transparent, and whitespace is
ignored. Missing pixels and unsupported symbols remain transparent. This compact
text representation is intended for small Xe-owned desktop and application icons.

`draw_icon_scaled(target, x, y, width, height, pixels, scale)` draws the same sprite
without changing or repeating its pixel string. Each source pixel becomes a square
of `scale` logical pixels; for example, a `7x7` icon at scale `3` occupies `21x21`
logical pixels. Valid positive scales are capped at `16`; zero or a negative value
draws nothing. Window UI scaling composes with the icon scale, while Screen remains
at stage-pixel density. `os::icon_size` is an OS preference (`0`, `1`, or `2`), not
an implicit rendering transform; pass `os::icon_size + 1` as the explicit scale when
desktop icons should follow that preference.

Portable images use the first-class one-word `graphics::Image` resource:

```xe
var sprite: graphics::Image
sprite = graphics::load_image("assets/player.ximg")
call graphics::draw_image(win, sprite, 12, 18, 0, 2)
```

`image_width`, `image_height`, `image_frame_count`, and
`image_frame_duration(image, frame)` expose validated metadata. `draw_image` uses
integer nearest-neighbor scaling, clips to the current target, and skips transparent
index `16`. The runtime caches decoded assets by private-drive revision and blits
opaque runs directly, so animated images are not reparsed or redrawn one Python call
per pixel.

`.ximg` files are canonical XIMG2 32-bit word streams. Their checked header contains
dimensions, frame count, loop metadata, table/data offsets, total words, and CRC32.
Each frame deterministically chooses packed raw, RLE, or bounded previous-frame
delta-RLE encoding. Six 5-bit pixels fit in one word (`0-15` palette indices and
`16` transparent). Dimensions, offsets, frame chains, decoded size, checksum, and
the hard 200,000-word budget are validated before a handle is returned.

`draw_commands` is the versioned checked accelerator used by dense scenes whose
projection loop would otherwise execute once per object in Xe bytecode. XGC1 version
1 deliberately supports the bounded atom/orbit scene described below; it is not a
general replacement for the ordinary pixel, line, text, or icon drawing functions:

```xe
result = graphics::draw_commands(
	win, stream, words, names, short_names, selected,
	projected_x, projected_y, projected_depth, node_radius, depth_order
)
```

The exact parameter types are `(Window|Screen, int*, int, string*, string*, int*,
int*, int*, int*, int*, int*) -> int`. `names` and `short_names` are arrays of
one-word string-descriptor handles; every referenced handle is validated as the
normal three-word Xe string descriptor before drawing. `selected` is read-only.
The four projected arrays and `depth_order` are caller-owned outputs with one word
per entry. The radius output is the base visual node radius; hit testing uses exactly
`radius + 10`, matching the Xe fallback and drag logic (folders draw one pixel larger
without changing their hit geometry). An invalid prior depth-order permutation is deterministically initialized
to entry order; a valid permutation supplies stable painter-order tie breaking.

The current XGC1 record is a reusable atom/orbit scene. The framing is versioned so
future command types can be added explicitly, but unknown opcodes are rejected rather
than guessed. Its eight-word header is:

| Offset | Field |
| ---: | --- |
| 0 | `COMMAND_MAGIC` (`0x58474331`) |
| 1 | `COMMAND_VERSION` (`1`) |
| 2 | exact total word count |
| 3 | command count, currently exactly `1` |
| 4 | first command offset, exactly `COMMAND_HEADER_WORDS` (`8`) |
| 5-7 | zero |

The 36-word orbit command uses these relative offsets:

| Offset | Field |
| ---: | --- |
| 0-3 | opcode, record words, entry count, shell count |
| 4-10 | scene x/y, nucleus center x/y, area width/height, sidebar width |
| 11-18 | render scale, outer radius, shell gap, center radius, node radius, tilt, roll, rotation |
| 19-23 | surface, outline, accent, shell, and highlight palette indices |
| 24-29 | pointer x/y, shell-button hover, zoom-control hover, camera zoom, label-character limit |
| 30-33 | item-table offset/stride and shell-table offset/stride |
| 34 | flags (`COMMAND_ORBIT_DRAW_LABELS`) |
| 35 | ring sample count (`4-64`; File Explorer uses `20`) |

All named `COMMAND_ORBIT_*_OFFSET` constants map these fields without duplicating
numeric offsets in applications. A shell record is exactly two words: phase and
population. An item record is exactly six words: shell, position, directory flag,
child count, full-name array index, and short-name array index. Shell populations
are `0-8`; the populated positions must be unique and contiguous, and their sum must
equal the entry count (`0-64`). Exact accounting is required: the shell table starts
at word `44`, the item table immediately follows the active shell records, and the
stream ends immediately after all item records. There are no ignored trailing words.

Validation covers the complete header, every record, all descriptor and output
spans, colors, flags, geometry, non-overlap of mutable spans, and exact table
accounting before any output word or pixel is changed. A nonnegative return packs
hover as `(entry + 1) + ((shell + 1) * 256)`; zero means no item. Negative values are
`-1` invalid target, `-2` invalid stream span/count, `-3` bad magic/version, `-4`
bad header/record length, `-5` unsupported opcode, or `-6` invalid orbit data or
external array. Hosts, including the eventual Scratch implementation, must preserve
this all-or-nothing validation and the written projected geometry.

`button_tone` has the same
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
os::window_transparency = 0
os::window_corner_style = os::CORNER_ROUNDED
os::icon_size = os::ICON_MEDIUM
os::clock_format = os::CLOCK_24_HOUR
os::settings_enabled = true
```

Reading the same names returns the current OS-owned value. All three volume values
are clamped to `0-100`; enum-like values and background/palette
IDs are validated. `background_count()` and `palette_count()` expose the valid ID
ranges. `apply_settings` remains available for the original atomic three-value
commit. `apply_preferences(master, music, effects, background, palette, theme,
transparency, corners, icons, clock, enabled)` validates first and atomically
commits the complete Settings model.

Only `CORNER_SQUARE` and `CORNER_ROUNDED` are supported. Legacy persisted value
`2` is migrated to Rounded, so older settings remain readable without retaining a
separate soft-corner renderer.

Settings-style live previews use
`preview_preferences(background, palette, theme, corners)`. It validates and
normalizes the four values, then changes the effective framebuffer palette,
backdrop, theme, and window chrome without modifying persisted settings.
`clear_preview()` restores the committed appearance. A successful
`apply_preferences` also clears the transient preview; an invalid preview or apply
fails closed to the committed appearance.

Window transparency has been removed. The legacy property and syscall pair remain
for bytecode compatibility, but reads return `0`, writes normalize to `0`, and all
window content, title, border, text, and controls render opaque.

Palettes `0-2` are the dark Xenon variants and `3-5` are coordinated light
variants. Changing `theme_mode` preserves the palette variant while moving it to
the matching light or dark group. The OS device remains the source of truth; apps
stage edits locally and use `apply_preferences` only when Apply is chosen. The host
IDE and command-line runtime persist accepted values atomically in XenonOS's private
application-data settings file. Programmatic embedders that do not provide a
settings path intentionally keep session-only state.

`background_id` selects a solid backdrop color. The OS clears the framebuffer to
that palette-backed color before drawing a Screen or Window; it does not synthesize
desktop art, a dock, or taskbar sprites. A Screen program may draw those elements
itself. Screen is a direct frame target rather than a retained compositor layer, so
one program should finish a Screen frame before starting a separate Window frame.
The current built-in backdrop IDs are Black,
Navy, and Slate, and applications should use IDs returned by the OS rather than
assuming that list will never grow.

The window chrome measurements and palette roles are exposed as `graphics`
constants (`WINDOW_TITLE_HEIGHT`, `WINDOW_BORDER_WIDTH`, `WINDOW_BORDER_COLOR`,
`WINDOW_TITLE_COLOR`, `WINDOW_CONTENT_COLOR`, `WINDOW_TEXT_COLOR`,
`WINDOW_CONTROL_COLOR`, `WINDOW_CONTROL_SIZE`, `WINDOW_CONTROL_GAP`,
`WINDOW_TITLE_TEXT_OFFSET`, and `WINDOW_ROUNDED_INSET`). They are generated from
the VM's primitive → semantic → component token source, allowing applications and
portable renderers to reproduce the runtime window design without copying magic
numbers.

Runtime utilities are `sleep(milliseconds)`, `exit(status)`, and `ticks()`.
`exit` ends the current VM cooperatively and records its status code.
Local calendar components are available as integer functions `year()`, `month()`,
`day()`, `hour()`, and `minute()`. `hour()` and `minute()` preserve the canonical
raw syscalls `28` and `29`; the date components are high-level app extensions.

The opt-in host bridge `clipboard_read() -> string` and
`clipboard_write(text) -> bool` accesses the computer clipboard only when the
visible **System clipboard** toggle in the desktop IDE is enabled. The toggle is
disabled by default and must be enabled explicitly on the Code toolbar. When disabled
or unavailable, reads return an empty string and writes return `false`; payloads are
bounded to 32,768 characters. Scratch implements the unavailable case locally:
reads are empty and writes return `false`, so an exported project cannot access or
leak the computer clipboard.

Text files use the opaque `os::File` resource:

```xe
var file: os::File
file = os::open_read("example.txt")
out << os::read(file)
call os::close("example.txt")
```

`open_write(path)` and `write(file, text)` provide the write side;
`open_append(path)` appends without truncating. `is_directory`, `copy`, `file_size`,
`modified_ticks`, `revision`, and `normalize_path` support deterministic editors and
workspace tools without exposing host paths. Unless a host
explicitly supplies a test/integration root, the runtime uses the private
`%LOCALAPPDATA%/XenonOS/VirtualDrive` directory (or
`~/.xenonos/VirtualDrive` when `LOCALAPPDATA` is unavailable). It never defaults to
the process working directory. Paths are relative to that virtual drive; absolute
paths, internal metadata names, and `..` traversal outside the root are rejected.
Normalized aliases cannot reach internal recovery data. Text reads are bounded to
1 MiB on disk and 200,000 decoded characters so a guest cannot allocate an
unbounded host file; larger files fail closed. Files are also closed automatically
when the VM stops.

Directory and workspace operations are `entry_count(path)`, `entry_name(path,
index)`, `entry_is_directory(path, index)`, `path_exists(path)`, `make_file(path)`,
`make_directory(path)`, `rename(old_path, new_path)`, and `delete(path)`. Entries are
sorted with directories first and then names case-insensitively. Open files cannot be
renamed or deleted until closed, and the filesystem root itself cannot be deleted. `delete`
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
code. Release updates replace the generated
`xe_lang/devices/currency_snapshot.py` module and its `SNAPSHOT_DATE`; applications
never fetch rates while they run.

## `compiler`

`compiler::check(source)` runs the Xe lexer, parser, semantic analyzer, optimizer,
code generator, and assembler in-process. Diagnostics are available through
`error()`, `error_line()`, and `error_column()`; successful builds expose
`assembly()` and `bytecode_size()`.

`compiler::run(source)` compiles and executes source in a bounded child XVM and
returns the program's captured output. It shares the calling VM's private virtual
filesystem root and OS preference state, uses non-blocking empty text input, has no
backend request handler, and caps output at 8192 characters. A nongraphical child
stops after 500,000 instructions and checks a two-second execution deadline between
instructions; sleeps are clamped to the remaining deadline. A child that uses the
graphics/window API temporarily owns the live stage, input device, image cache, and
audio sequencer so a program run from the virtual IDE can display and drag its own
window. The virtual IDE resumes when that child closes; the host Stop action cancels
it. Interactive graphical children therefore use cancellation and their window
lifecycle instead of the two-second batch deadline. Source is capped at 32,768
characters, NUL output is escaped as `\0`, and all returned diagnostics obey the same
output bound. Compile and runtime failures are returned as readable text; nested
`compiler::run` calls are rejected.

`compiler::check_workspace(entry_path)` and `compiler::run_workspace(entry_path)`
link a deterministic private-drive workspace. The entry file contributes executable
top-level statements; `.xe` files recursively beneath the entry file's parent folder
contribute declarations. Paths are sorted canonically, in-memory documents loaded
with `load_document` override their saved versions, unrelated projects elsewhere in
the private drive are ignored, and traversal never leaves the Xenon virtual drive.
A workspace is bounded to 128 Xe files and 131,072 source characters. Missing
entries, executable non-entry statements, nonportable paths, and duplicate logical
paths produce normal compiler diagnostics instead of silently falling back to
active-file execution.

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
Sixteen independent document slots are available for Scratch-portable multi-view tools.
Use `document_script_count`, `document_script_name`, `document_script_shell`,
`document_script_line`, `document_script_enabled`, and `document_source`. The
single-document `script_*` calls describe the most recent `load_visual` result.

## `audio`

`audio::Track` is a first-class handle to a checked XMusic word stream. The portable
sequencer API is `load`, `play`, `pause`, `stop`, `seek`, `position`, `duration`,
`is_playing`, `update`, and `active_pitch`:

```xe
var disc: audio::Track
disc = audio::load("music/demo.xmusic")
if (audio::play(disc)) {
	call audio::update(disc, 16)
	out << audio::active_pitch(disc)
}
```

XMusic stores tempo, ticks per beat, loop points, and sorted MIDI-range note events
in a length- and CRC-checked 32-bit stream. `seek`, `position`, and `duration` use
XMusic timeline ticks; only `update(track, delta)` takes milliseconds. `update`
clamps delta to `0-50 ms` so native and Scratch hosts advance identically after a
delayed frame. When Qt exposes a compatible Int16 stereo output, the Python IDE
renders active notes through a lazy native synthesizer honoring the combined master
and music volumes; otherwise sequencing remains deterministic and silent. Scratch
can map the same portable events to its sound blocks without changing Xe application
state. The full profile blocks dynamic XMusic paths until a deterministic project
asset ROM supplies the requested track.

## Host Image Studio and Scratch export

The Python IDE contains host-level `Xe → SB3`, `Image Studio`, and `Help` tabs; these
are workbench tools, not Xe windows. Image Studio edits an RGBA project with tools,
layers, frames, synchronized millisecond/FPS controls, onion-skin preview, undo/redo,
a brush-footprint cursor, drag-to-pan canvas surround, and deterministic exports.
`.xip` is the editable canonical ZIP container (sorted members, canonical JSON, fixed
timestamps); `.ximg` is the compact 16-color runtime form. PNG/GIF/sprite-sheet
exports preserve host artwork. `.sprite3` writes every flattened animation frame as
a Scratch costume plus a green-flag playback stack; frame wait values are preserved,
while the UI truthfully notes that Scratch's scheduler can introduce live timing
jitter. The Xe preview makes palette quantization and transparency explicit before
XIMG export.

The Xe-to-SB3 converter can analyze the active editor, a workspace, or any `.xe`
source selected through the Xenon virtual-drive picker. It compiles through the same
side-effect-free compiler service
as Run. It injects XBN into a checksum-pinned Scratch VM template only after an exact
compatibility gate verifies the address model, static budget, every syscall, and
literal asset requirements. ZIP member names, duplicate project members, template
shape/hash, output overwrite, canonical JSON, member order, timestamps, and atomic
replacement are validated. The optional fallback stages both files and attempts to
restore the complete previous pair if either replacement fails, preserving any
backup that cannot be restored automatically. It is clearly labeled
`.xbn + .compatibility.json` and is never presented as an SB3.

The converter's default full-ABI profile is pinned to the generated two-target
Scratch VM. It provides ten physical 200,000-word memory lists, checked bank routing,
and a conservative allowlist of portable core and application services verified by
the compiled File Explorer. Dispatcher presence alone never marks a syscall exact.
The command-stream accelerator and native right-click remain unavailable in vanilla
Scratch; a hash-bound File Explorer allowance is safe because that exact source has
tested primitive-drawing and 500 ms left-hold fallbacks. The compatibility gate still
blocks host compilation and portable image/audio assets until a deterministic project
ROM is implemented.
`scratch_vm/profile.json` and `xenon131-vm.sb3` retain
the original 65,536-word legacy template solely for regression auditing; they are
not the converter default.

The generated full-ABI projects run in the standard Scratch VM when loaded from a
computer. Their two million materialized memory cells produce an uncompressed
`project.json` above the Scratch website's current save/share limit, so the checked
artifacts are local-load projects. This distribution boundary does not change the
compatibility result or use nonstandard blocks.

The complete authoring, XIMG runtime, wallpaper, icon, animation, compression, and
Scratch workflow is in [docs/IMAGE_STUDIO.md](docs/IMAGE_STUDIO.md).

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
- Raw graphics: `30-58` (buffer operations, clipping, primitives,
  image/text/window/button drawing). Raw taskbar `52` and TaskAtom `53` remain
  available to assembly programs, but Calculator and Settings do not call them.
- Raw input: `60-64` (mouse polling, previous event, keyboard polling, key state,
  and pointer bounds testing).
- Backend request: `80` `REQUEST`, through the runtime's injectable synchronous request
  handler. With no handler, it deterministically writes an empty response.
- High-level app extensions: graphics `100-129` (including `124`
  `APP_GRAPHICS_SCROLL_DELTA`), `142-146`, `208-209`, `246-249`, and `254`, OS
  settings/utilities `130-141` and `180-196`, `Window` methods `150-152`, files
  `160-164` and `210-217`, mutable string append operations `170-171`, currency
  `200-207`, compiler services `220-245`, `255`, and `290-291`, calendar date
  components `250-252`, compact palette-icon drawing `253`, portable VFS helpers
  `260-266`, portable images `270-275`, checked graphics command streams `276`, and
  XMusic sequencing `280-289`.

Compiled Screen resource references set bit 31 and retain the static address in bits
`0-30`. The runtime strips that tag before bounds checks. Static resources remain in
the 16-bit XAssembly address range, so existing Window references and bytecode are
unchanged. The full-ABI Scratch template implements the portable Screen and window
handlers; the separately pinned legacy audit template does not.

IDs absent from those ranges are reserved, including `13-19` and `59`;
invoking one reports `Unknown system call` rather than silently doing the wrong
operation.

## VM address space

The default XVM data space is exactly `2,000,000` unsigned 32-bit registers,
numbered `0..1,999,999`. Addresses `0..999,999` form the working set; allocator
pressure activates the standby tier at `1,000,000..1,999,999` only after collection
cannot satisfy a request. The complete logical space is ten banks of 200,000 words.
In the full-ABI Scratch VM, address `a` maps to list `floor(a / 200000)` and Scratch
item `(a mod 200000) + 1`, with exactly one list item per register. Banks `0-4` are
the working tier and banks `5-9` are the standby tier. The separate legacy audit
template remains at one 65,536-address list.

The direct `LOAD`/`STORE` XAssembly encoding and text/static sections remain 16-bit
for binary compatibility; the expanded heap is reached through 32-bit pointers and
indirect loads/stores. Embedders may request `65,536..2,000,000` addresses with
`RuntimeContext(memory_words=...)`; larger or smaller requests are rejected. The
full mapping, deterministic standby policy, and storage costs are documented in
[docs/VM_MEMORY.md](docs/VM_MEMORY.md).
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

Run `python -W error -m pytest -q` for compiler, VM, app-contract, interaction,
format, exporter, and offscreen host-UI coverage. Graphical smoke tests run against
the complete `480x360` Scratch stage and cancel after bounded frames instead of
requiring an interactive desktop session.
