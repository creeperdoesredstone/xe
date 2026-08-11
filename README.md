Documentation for this programming language can be found [here](https://creeperdoesredstone.github.io/xe-docs/)

Graphics, frameless Screen, Window, and OS standard-library APIs are documented in
[STDLIB.md](STDLIB.md).

Use `graphics::Screen` with the same target-first drawing calls to render rectangles,
text, atoms, and compact palette icons directly to the complete `480x360` stage
without creating a window, title bar, or border.

Launch the Xenon IDE with `python ide.py`. Pass an Xe file path as the first
argument to open it directly, for example `python ide.py apps/calculator.xe`.
Add `--run` to execute it immediately. Use `--tab image-studio`, `--tab converter`,
or `--tab help` to open a host workbench tool directly.

The host workbench has top-level Code, Xe → SB3, Image Studio, and Help tabs. The
converter uses the pinned full-ABI Scratch VM and exports an `.sb3` only when the
compiled program is exact for its verified portable profile. File Explorer's two
unavailable native conveniences are allowed only for the pinned artifact whose Xe
fallbacks are tested; the exception cannot leak to edited or unrelated programs.
Otherwise the optional
fallback is an explicit `.xbn + .compatibility.json` pair. Image Studio provides layers, frames,
palette preview, undo/redo, animation playback, and deterministic XIP/XIMG plus
PNG/GIF/sprite-sheet exports. Frame duration and FPS stay synchronized; the canvas
supports direct pan and a live brush-footprint cursor. Scratch animation export
produces a deterministic `.sprite3`, while XIMG remains the compact format Xe
programs can load. Help contains searchable Xe/XAssembly basics, app and
workbench guidance, and a link to the official language reference. The default
Scratch template has ten physical 200,000-word banks and a checked portable syscall
profile, so compatible Xe programs export directly. Host compiler services and
portable image/audio asset loading remain blocked until deterministic project-ROM
support is implemented; the converter reports those boundaries instead of degrading
the program.
The full VM projects load and run through Scratch's **Load from your computer** flow.
Their required two million physical memory cells make `project.json` larger than the
Scratch website's current save/share service limit, so they are local-load projects
until the merged VM is compacted without weakening the one-register-per-list-item
runtime contract.

See [docs/IMAGE_STUDIO.md](docs/IMAGE_STUDIO.md) for the complete image and
animation workflow: editable XIP projects, XIMG use from Xe, wallpapers and Screen
targets, icons, elapsed-time playback, compression, `.sprite3`, and Xe-to-SB3 asset
limits. A copy-paste virtual-IDE example is available at
[examples/small_draggable_window.xe](examples/small_draggable_window.xe).

Included graphical applications:

- `apps/calculator.xe` - seven-mode calculator with Standard, Scientific,
  Programmer, Graphing, offline-snapshot Currency, Unit Conversion, and History views;
  currency values compact safely at narrow widths and equation editing supports
  Ctrl+A/C/X/V.
- `apps/settings.xe` - staged system preferences with an animated side drawer that
  pushes the active page right and collapses back to its compact tab, plus live
  background, palette, icon, clock, and exact runtime window previews before Apply.
  Window corners are Square or Rounded, and built-in windows remain opaque.
- `apps/xenon_terminal.xe` - tabbed sandboxed terminal with deterministic parsing
  and autocomplete, history, saved commands, a named per-tab ribbon, adjustable
  text, split view, themes, a resource monitor, host-local `date`/`time` commands,
  and Ctrl+A/C/X/V command editing.
- `apps/minesweeper.xe` - deterministic three-preset Minesweeper with first-click
  safety, exact mine counts, iterative flood reveal, chording, keyboard and visible
  Reveal/Flag controls, timer, restart, and responsive cell sizing.
- `apps/xenon_music.xe` - tactile vinyl-style XMusic sequencer with three generated
  demo discs, full-record drag scrubbing, a pivoted S-arm/headshell playback control,
  disc removal, and drag/drop inventory. When a compatible native Qt audio output is available, the
  host IDE synthesizes the portable note stream; otherwise sequencing remains
  deterministic and silent. The app state stays suitable for a future Scratch audio
  backend.
- `apps/xenon_daw.xe` - compact pattern-based workstation with an on-screen keyboard,
  note insertion, play/stop transport, tempo control, and a scrollable event list.
- `apps/text_editor.xe` - compact text editor with Xe mode off by default, optional
  syntax diagnostics, composable bold/italic/underline styles, proportional caret,
  held-key input, private-drive Open/Save dialogs, wheel-scrolled file lists, and safe
  clipboard edits.
- `apps/xenon_ide.xe` - code/visual Xe IDE with genuine bounded compile/run output,
  proportional source/terminal text, a private-drive Open picker, diagnostics, terminal,
  expandable workspace explorer, visible source selections, file-nucleus visual
  scripts, inline filesystem rename, a resizable Explorer pane, and up to sixteen
  scrollable/closeable views with two-tab overflow pages.
- `apps/file_explorer.xe` - orbit-based filesystem explorer with file electrons,
  folder mini-atoms, breadcrumbs, centered two-scene zoom navigation, file viewing,
  extension-safe rename/delete actions, double-click folder opening, Ctrl/Shift and
  marquee multi-selection, eased wheel/button zoom, shell-wide hover slowdown,
  Shift+wheel horizontal rotation, a portable half-second context hold, adaptive
  shell spacing, adjustable orbit speed, always-visible shell growth, and direct
  shell/folder drag operations. Native and portable render paths share the same
  stable depth ordering, and cached entries reconcile by filesystem identity after
  bulk operations.

Xe filesystem apps use a private XenonOS virtual drive by default; they never open
the repository or current working directory. Deletes are moved to the drive's hidden
recovery trash instead of being permanently removed.
The host IDE also persists applied OS preferences in XenonOS's private application
data, so Settings remains staged while Apply becomes the durable commit point.
Its visible **System clipboard** toggle is disabled by default. Enabling it is an
explicit host-only choice; editor-like apps retain their private in-app clipboard
while the bridge is disabled or unavailable.

For example: `python ide.py apps/xenon_terminal.xe --run`.

The XVM defaults to 2,000,000 logical 32-bit data registers: a 1,000,000-word
working set followed by a 1,000,000-word standby tier. The logical space is split
into ten 200,000-word banks; the full Scratch VM uses one list item per register
without exceeding Scratch's per-list ceiling. See
[docs/VM_MEMORY.md](docs/VM_MEMORY.md) for the exact mapping and reserve policy.
Compiled Xe embeds its static-word count so the heap starts after globals instead
of overlapping them.
Runtime-created service strings are garbage-collected conservatively, while blocks
obtained through `os::malloc` retain explicit ownership. Both primary text renderers
load width-prefixed 3x5/5x7 JSON glyphs and advance proportionally. Before integrating
a new delivery, run `git fetch origin` followed by `git rev-parse HEAD origin/main`
and reconcile any upstream movement first.

Graphical update syscalls are the 60 Hz frame boundary, preventing a tight Xe render
loop from flooding the host with redundant full-stage copies; built-in apps do not
add a second fixed sleep. Portable image frames are cached
and blitted as clipped opaque runs; transparent index `16` preserves the existing
frame. These rules are deterministic and map cleanly to the Scratch host.

Dragging a maximized window title restores the saved normal bounds under the
pointer's proportional title-bar position, then continues through the normal
white-outline drag session. Dragging into the top-edge snap region previews a full
frame and releases through the same eased maximize path; leaving the region restores
the floating outline. A title click without pointer movement remains maximized.

Scrollable app menus and pickers accept normalized mouse-wheel input while retaining
their visible arrow controls. `graphics::scroll_delta()` exposes the same signed,
frame-stable logical wheel steps to Xe applications.

Run the integrated regression suite with `python -W error -m pytest -q`.

App identity, default geometry, generated outputs, and portable asset namespaces are
declared once in `apps/manifest.json`. See `apps/README.md` for the standalone app
boundaries and `apps/assets/README.md` for XIP/XIMG/XMusic/Scratch animation extension
points. Shared palette, window, Settings-preview, and Scratch design tokens are
documented in [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md). Run
`python tools/build_apps.py` to rebuild every declared `.xas` and `.xbn`,
or `python tools/build_apps.py --check` to detect missing or stale artifacts without
writing.
