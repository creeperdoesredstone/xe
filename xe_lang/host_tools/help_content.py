"""Bundled, offline-first help for the host IDE."""

from __future__ import annotations

from dataclasses import dataclass


OFFICIAL_DOCS_URL = "https://creeperdoesredstone.github.io/xe-docs/"


@dataclass(frozen=True, slots=True)
class HelpTopic:
	title: str
	category: str
	markdown: str
	keywords: tuple[str, ...] = ()

	def matches(self, query: str) -> bool:
		terms = tuple(part for part in query.casefold().split() if part)
		if not terms:
			return True
		haystack = " ".join(
			(self.title, self.category, self.markdown, *self.keywords)
		).casefold()
		return all(term in haystack for term in terms)


HELP_TOPICS: tuple[HelpTopic, ...] = (
	HelpTopic(
		"Welcome to Xe",
		"Start here",
		"""# Xe in a minute

Xe is a compiled language for the Xenon virtual machine. Source files use the `.xe`
extension. The desktop IDE can edit and run a file directly; a workspace build uses
`workspace.xe` as its single entry point.

```xe
var message: string
message = "Hello from Xe"
out << message
```

Use **File → Open** to load a source file, **Ctrl+S** to save it, and **Ctrl+Enter**
to run it. Program output and input appear in the terminal pane. Graphical programs
render into the 480×360 graphics view.
""",
		("quick start", "hello world", "run"),
	),
	HelpTopic(
		"Values, variables, and control flow",
		"Xe language",
		"""# Core language

Declare variables with an explicit type. Xe includes integers, floats, booleans,
characters, strings, arrays, structs, classes, enums, functions, and procedures.

```xe
var total: int
var label: string
var i: int
total = 0
label = "items"

for (i = 0; i < 5; i += 1) {
    total += i
}

if (total > 0) {
    out << total
} else {
    out << 0
}
```

Comments start with `#`. Names are case-sensitive. Keep declarations in library
units and executable top-level statements in `workspace.xe` when building a
multi-file workspace.
""",
		("types", "if", "for", "while", "array", "comment"),
	),
	HelpTopic(
		"Functions and procedures",
		"Xe language",
		"""# Reusable code

Functions return a value. Procedures perform work without returning a value.

```xe
fn square(value: float) float {
    return value * value
}

proc greet(name: string) {
    out << "Hello, " + name
}
```

The standard libraries use qualified names such as `math::sqrt` and
`graphics::begin_draw`. Hover symbols in the editor for a signature and Ctrl-click
a known definition to navigate to it.
""",
		("func", "proc", "return", "signature"),
	),
	HelpTopic(
		"Graphics and windows",
		"Standard library",
		"""# Drawing

Windowed applications draw between `begin_draw` and `update` calls. Desktop shells
can draw directly to a `graphics::Screen`; an app window uses `graphics::Window`.

```xe
var win: graphics::Window
win.x = 40
win.y = 30
win.width = 320
win.height = 220
win.title = "Demo"
win.ui_scale = 1
win.state = graphics::WINDOW_NORMAL
call graphics::begin_draw(win)
call graphics::clear(win, graphics::BLACK)
call graphics::draw_rect(win, 16, 16, 80, 40, graphics::COLOR_5)
call graphics::draw_line(win, 16, 70, 120, 70, graphics::COLOR_12)
call graphics::update(win)
```

The VM stage is 480×360. Keep input, animation, and file formats deterministic so
the same program can be moved to the Scratch VM profile.
""",
		("graphics", "screen", "window", "draw", "480", "360"),
	),
	HelpTopic(
		"Files, OS, and input",
		"Standard library",
		"""# Host services

The `os` library exposes process, settings, time, and virtual-file operations. The
Python host uses a private Xenon virtual drive rather than the computer's real
folders. The full-ABI Scratch profile maps portable file operations to a
project-local list-backed VFS. It never exposes the computer's filesystem.

```xe
os::volume = 75
var file: os::File
file = os::open_read("notes.txt")
out << os::read(file)
call os::sleep(16)
call os::close("notes.txt")
```

The desktop IDE can expose the computer clipboard through
`os::clipboard_read()` and `os::clipboard_write(text)`. Its visible System
clipboard toggle on the Code toolbar is the permission boundary and is off by
default: disabled reads are empty and writes return `false`. Scratch export reports
these host-only calls as unsupported.

Use the documented constants and check errors returned by file operations. A virtual
path must never be treated as permission to modify the host workspace.
""",
		("os", "file", "filesystem", "keyboard", "mouse", "volume", "clipboard"),
	),
	HelpTopic(
		"XAssembly overview",
		"XAssembly",
		"""# XAssembly

XAssembly is the textual instruction form consumed by the Xenon assembler. The Xe
compiler emits it as an intermediate artifact; ordinary application development
should prefer Xe source.

Instructions operate on the XVM stack and memory. Labels identify branch and call
targets. Syscalls bridge deterministic VM code to registered devices. The exact
instruction set, operand forms, calling convention, and syscall table are maintained
in the official Xe/XAssembly documentation.

Use the repository's disassembler when inspecting an `.xbn` binary. Do not hand-edit
generated assembly unless you are intentionally testing the assembler or VM.
""",
		("xas", "assembly", "xbn", "opcode", "syscall", "disassemble"),
	),
	HelpTopic(
		"Using the desktop IDE",
		"IDE guide",
		"""# Code workspace

- **Code** keeps the source editor, terminal, program-input field, and graphics view.
- **Xe → SB3** checks exact Scratch compatibility before it writes a project.
- **Image Studio** creates layered, frame-based assets and target previews.
- **Help** searches this bundled reference without a network connection.

Keyboard shortcuts:

| Action | Shortcut |
|---|---|
| New / Open / Save | Ctrl+N / Ctrl+O / Ctrl+S |
| Find / Replace | Ctrl+F / Ctrl+H |
| Run | Ctrl+Enter |
| Toggle Comment | Ctrl+/ |
| Rename Symbol | F2 |
| Maximize graphics | Ctrl+Shift+G |
| Open Help | F1 |

The graphics-view maximize control changes only the IDE layout. It never changes the
VM stage dimensions or the program's framebuffer.
""",
		("shortcut", "terminal", "graphics view", "code tab"),
	),
	HelpTopic(
		"Exporting to Scratch",
		"IDE guide",
		"""# Xe → SB3

Choose **Whole workspace** for an application whose entry point is `workspace.xe`,
**Active editor** for a single-file experiment, or **Choose .xe file** to use the
read-only Xenon virtual-drive picker. The picker opens a selected source immediately
on double-click and offers an explicit computer browser for files outside the private
drive. Run **Check compatibility** first.

An **Exact** result means the pinned full-ABI VM profile can reproduce the program. A
**Blocked** result lists the source locations, syscalls, assets, or memory constraints
that prevent exact export. If enabled, fallback export writes an XBN compatibility
bundle and labels it as a fallback; it never claims to be an exact `.sb3`.

The full profile includes the ten-bank 2,000,000-word memory router and a conservative
allowlist of verified portable services. A dispatcher branch by itself never counts
as exact. File Explorer's native right-click and command-stream calls are allowed only
for the pinned artifact whose left-hold and primitive-drawing fallbacks are tested.
Host compiler calls and portable image/audio assets remain blocked until a
deterministic project ROM is implemented. The older 65,536-word template is retained
only as a regression-audit fixture.

The full-ABI profile is labeled **local load**: its two million physical list cells
make the project JSON larger than the Scratch website's save/share service limit.
Use **File → Load from your computer** to run the exported project.

Exports are explicit. Compatibility analysis is side-effect free.
""",
		("converter", "scratch", "sb3", "compatibility", "fallback"),
	),
	HelpTopic(
		"Using Image Studio",
		"IDE guide",
		"""# Image Studio

Use the toolbar for pencil, eraser, fill, eyedropper, line, rectangle, ellipse, and
selection tools. Wheel over the canvas to zoom; drag the empty canvas surround with
the left button, or hold the middle mouse button anywhere, to pan. Pencil and eraser
show their exact brush footprint under the pointer.
Layers compose from bottom to top. Frames form a timeline and can be previewed with
onion skinning or playback. Milliseconds and FPS are synchronized per frame.

PNG and JPEG import as one frame. GIF imports its frames when the local Qt image
plugin supports it. PNG and sprite-sheet export are always checked before writing;
GIF export requires Pillow and fails without a partial file if it is unavailable.
`.xip` preserves editable layers and frames;
`.ximg` applies the deterministic 16-colour Scratch/XVM palette and reports any
format or memory-limit error before replacing an existing destination.
`.sprite3` flattens each visible frame into a Scratch costume and adds deterministic
green-flag playback. Requested wait values are preserved, while Scratch may schedule
them with small live timing variations.

Use the Scratch preview to check the 480×360 target and the indexed preview to catch
palette/transparency loss before export.

For Xe, export XIMG into the private virtual drive, load it with
`graphics::load_image("SystemAssets/MyApp/image.ximg")`, and draw it with
`graphics::draw_image(target, image, x, y, frame, scale)`. Use
`image_frame_duration` with `os::ticks()` instead of assuming a fixed frame rate.
A `480×360` image drawn at `(0, 0)` into `graphics::Screen` is a frameless desktop;
a small transparent XIMG drawn at `os::icon_size + 1` is a preference-scaled icon.

The repository guide `docs/IMAGE_STUDIO.md` covers project limits, compression,
asset declarations, complete Xe animation code, desktop and icon recipes, Scratch
sprite import, and the exact Xe-to-SB3 compatibility gate.
""",
		("image", "animation", "layer", "frame", "fps", "xip", "ximg", "gif", "sprite3"),
	),
	HelpTopic(
		"Official reference",
		"Reference",
		f"""# Complete documentation

This help is an offline quick reference. The upstream Xe grammar and XAssembly
instruction set are published at:

[{OFFICIAL_DOCS_URL}]({OFFICIAL_DOCS_URL})

Use the official reference that matches the Xe compiler version in your repository
for the base language. Use this repository's `STDLIB.md` and `syscall_abi.py` for its
new graphics, OS, currency, compiler, image, and audio extensions.
""",
		("online", "documentation", "official", "api"),
	),
)
