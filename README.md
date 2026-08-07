Documentation for this programming language can be found [here](https://creeperdoesredstone.github.io/xe-docs/)

Graphics, frameless Screen, Window, and OS standard-library APIs are documented in
[STDLIB.md](STDLIB.md).

Use `graphics::Screen` with the same target-first drawing calls to render rectangles,
text, atoms, and compact palette icons directly to the complete `480x360` stage
without creating a window, title bar, or border.

Launch the Xenon IDE with `python ide.py`. Pass an Xe file path as the first
argument to open it directly, for example `python ide.py apps/calculator.xe`.
Add `--run` to execute it immediately.

Included graphical applications:

- `apps/calculator.xe` - seven-mode calculator with Standard, Scientific,
  Programmer, Graphing, offline-snapshot Currency, Unit Conversion, and History views.
- `apps/settings.xe` - staged system preferences with an animated compact menu.
- `apps/xenon_terminal.xe` - tabbed deterministic terminal with history,
  autocomplete, saved commands, a named per-tab ribbon, adjustable text, split
  view, themes, a resource monitor, and host-local `date`/`time` commands.
- `apps/text_editor.xe` - compact text editor with optional Xe syntax highlighting,
  held-key input, visible range selection, and safe clipboard edits.
- `apps/xenon_ide.xe` - code/visual Xe IDE with compiler diagnostics, terminal,
  expandable workspace explorer, visible source selections, file-nucleus visual
  scripts, inline filesystem rename, a resizable Explorer pane, and up to sixteen
  scrollable/closeable views with two-tab overflow pages.
- `apps/file_explorer.xe` - orbit-based filesystem explorer with file electrons,
  folder mini-atoms, breadcrumbs, centered two-scene zoom navigation, file viewing,
  contextual rename/delete actions, eight-item shells, manual shell growth, and
  direct shell/folder drag operations.

Xe filesystem apps use a private XenonOS virtual drive by default; they never open
the repository or current working directory. Deletes are moved to the drive's hidden
recovery trash instead of being permanently removed.

For example: `python ide.py apps/xenon_terminal.xe --run`.

The XVM defaults to the Scratch-safe maximum of 200,000 addresses. Compiled Xe embeds
its static-word count so the heap starts after globals instead of overlapping them.
Runtime-created service strings are garbage-collected conservatively, while blocks
obtained through `os::malloc` retain explicit ownership. Both primary text renderers
load width-prefixed 3x5/5x7 JSON glyphs and advance proportionally. The current
audited Xe upstream baseline is
`640f8cd15a1cf5b4a17c47b8d06854abb793df1c`; verify it with
`python tools/check_xe_upstream.py`.

Dragging a maximized window title restores the saved normal bounds under the
pointer's proportional title-bar position, then continues through the normal
white-outline drag session. A title click without pointer movement remains maximized.

Run the integrated regression suite with `python -W error -m pytest -q`.
