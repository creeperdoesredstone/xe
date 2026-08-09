# Xe workspace context

This file records the state of the integrated Xenon application/runtime delivery.
Treat it as the release source of truth when continuing work in this repository.

## Upstream and scope

- The work was developed against upstream `creeperdoesredstone/xe` commit
  `cb8eb8988ee8c7d31fe6e288a35e7225f69de1d3`. Fetch and compare `origin/main`
  before publishing; do not assume this baseline remains current.
- The delivery extends the existing compiler/runtime rather than replacing the Xe
  grammar or XBN format. Existing IDE startup behavior is preserved.
- Xenon Paint remains intentionally removed. The maintained graphical apps are
  Calculator, Settings, Terminal, Minesweeper, Music, Text Editor, Xenon IDE, and
  File Explorer.

## Host IDE

- `ide.py` has top-level Code, Xe to SB3, Image Studio, and Help tabs. The VM stage
  keeps the Scratch 480x360 aspect ratio at both fractional and enlarged sizes,
  with safe pointer mapping and maximize/restore behavior.
- Xe to SB3 uses the side-effect-free compiler service and a checksum-pinned VM
  profile. It analyzes the current immutable source/workspace snapshot before
  export. Exact SB3 export is enabled only after every memory, syscall, asset, and
  profile check passes. The opt-in fallback is explicitly an `.xbn` plus
  `.compatibility.json` pair, never a disguised SB3; pair replacement stages and
  restores existing outputs on failure.
- Image Studio supports layers, frames, undo/redo, pencil, eraser, fill, picker,
  line, rectangle, ellipse, selection, pan/zoom, onion skinning, playback, PNG/GIF
  and sprite-sheet output, deterministic editable XIP, and compact runtime XIMG.
- Help is searchable and available offline. The official site is authoritative for
  upstream Xe/XAssembly; local `STDLIB.md` and `xe_lang/syscall_abi.py` document the
  repository-specific APIs.
- Host typography selects an installed preferred monospace face case-insensitively,
  then falls back through Qt's system fixed font and generic `monospace`. Both fresh
  workbench construction and the application entry point use the same resolver.
- Native XMusic playback is a lazy Qt stereo synthesizer. Sequencing remains
  deterministic and silent if no compatible audio device is available. Program
  output and the execution-complete banner are separated correctly.

## Applications

- Calculator starts empty and supports Standard, Scientific, Programmer, Graphing,
  Currency, Unit Conversion, and History modes. It has one exponent key, postfix
  percent (`9% == 0.09`), responsive key layouts, exact-pointer graph hover, offline
  currency snapshots, swaps, large-value fitting, and app-local Ctrl+A/C/X/V.
- Settings uses a smoothly animated left push drawer and staged Apply/Cancel.
  Volume, background, palette, theme, window style, icon/clock preferences, and
  transparency use the OS API. Host IDE and CLI settings persist through a
  versioned atomic JSON file; embedded runtimes without a settings path are
  session-only. Transparency is `0` opaque through `100` clear.
- Xenon Terminal keeps per-tab state, append-only bounded output, word-safe cached
  wrapping, scrolling/follow-tail, history, deterministic tokenization/autocomplete,
  themes, monitor/text/split controls, Ctrl commands, and portable private-VFS
  commands. About says `Placeholder`; it is not a host-shell escape.
- Minesweeper includes three presets, first-click safety, flags, chording, iterative
  flood reveal, timer, restart, and a visible Reveal/Flag fallback suitable for
  Scratch where right-click is unavailable.
- Xenon Music supplies checksum-validated XMusic demo discs with vinyl scrubbing,
  tonearm pause/resume, disc removal, and inventory drag/drop. It uses deterministic
  sequencer data rather than bundled copyrighted recordings.
- Text Editor defaults Xe source mode OFF; syntax/error behavior is disabled until
  enabled. It supports proportional caret placement, held keys, Ctrl editing,
  font sizing, bold/italic/underline combinations, dirty prompts, Open, Save, and
  Save As through the private file picker.
- Xenon IDE retains independent source/dirty/cursor/selection state per tab, no
  reload-on-switch loss, dirty close prompts, nested folder rows, folder navigation,
  active-file/workspace Run choices, a `main` nucleus, and a scrollable Help view
  with Xe syntax and usage examples. `workspace.xe` starts blank.
- File Explorer operates only on the private virtual drive. It has stable tilted
  orbit depth/occlusion, multi-select and group drag/delete, rename, zoom, shells,
  responsive labels, folder child dots, transition animations, and recovery trash.

## Compiler, VM, and public APIs

- `xe_lang/compiler_service.py` is the canonical side-effect-free source/workspace
  compiler. Artifacts include normalized filenames, XBN sections, memory use,
  required syscall/capability data, assets, diagnostics, and reproducibility data.
- Workspace compilation recursively collects `.xe` files under the selected entry
  file's parent directory, not unrelated projects elsewhere in the private drive.
  In-VM check/run workspace calls are syscalls 290/291. Child runs capture text and
  bounded VFS/OS effects but do not present graphics, native audio, or backend
  requests.
- The XVM supports at most 200,000 data addresses, keeps 16-bit text/static limits,
  starts the heap after static data, and manages strings without overwriting static
  storage. The 200,000 ceiling is the target for an eventual vanilla-Scratch VM,
  not a claim that the bundled legacy template already implements it.
- Public extensions include `graphics::Window`, frameless `graphics::Screen`, image
  handles and XIMG drawing, private-VFS helpers, compiler services, currency data,
  OS settings, XMusic audio, and the checked graphics command stream. ABI numbers
  and signatures live in `xe_lang/syscall_abi.py` and `STDLIB.md`.
- XIMG2 is a checked word stream with magic/version, dimensions, frame table,
  durations, offsets, CRC, and deterministic raw/RLE/delta selection. Runtime
  pixels are 5-bit values (`0..15` palette plus `16` transparent), six per word.
- XMusic is checked deterministic sequence data. Seek/position/duration are XMusic
  ticks; `audio::update` receives clamped milliseconds.

## File Explorer command stream and performance

- `graphics::draw_commands` is the versioned XGC1 checked accelerator at syscall
  `276`. Version 1 intentionally supports one bounded atom/orbit scene rather than
  general drawing primitives; ordinary graphics calls remain the portable fallback.
  Its canonical layout is `xe_lang/graphics_commands.py`; Xe exposes matching named
  `COMMAND_*` constants and `STDLIB.md` documents the full contract.
- `apps/file_explorer.xe` uses one orbit-scene command only while its view is steady.
  Transitions, insertion/removal animation, dragging, and unsupported hosts retain
  the primitive Xe fallback. The handler validates every record, descriptor, and
  caller span before drawing, then projects, depth-sorts, renders, hit-tests, and
  writes x/y/depth/radius/order in one host call.
- The 32-file plus 32-folder benchmark has a three-run median of approximately
  `10.65 ms/frame`, versus `132.4 ms/frame` before optimization. The regression
  ceiling is `33 ms`. A populated steady frame executes about 5,217 VM instructions
  and 58 syscalls; orbit work itself is one syscall.
- End-to-end 60-frame QA measured the same 64-item Explorer at `15.75 ms/frame`
  average (`22.94 ms` p95). The worst eight-child-dot stress case averaged
  `19.06 ms/frame`; its `35.99 ms` p95 remains a rare tail-frame stutter risk.
  A populated Xenon IDE averaged `20.90 ms/frame` (about 47.8 fps).
- Dark/light representative frames are pixel-identical to the Xe fallback with
  negative tilt, roll/rotation, labels, folders, child dots, selection, and nucleus
  occlusion. Tests cover both Screen and Window, packed hover, stable signed-tilt
  occlusion, descriptor/span validation, and all-or-nothing failures.

## Scratch portability

- `scratch_vm/` contains the pinned legacy template, profile, deterministic audit,
  capability manifest, and portability documentation. Template SHA-256:
  `69595617bb84a183b12208fee070d1270222bd57c4e861fb5013f0ff9b9e4f5e`.
- The bundled profile currently has a 65,536-address data list and 48 legacy
  syscalls. Current Xe artifacts declare the 200,000-address runtime contract and
  use newer APIs, so exact export is deliberately blocked with named reasons.
  Syscall 276 is also explicitly reported as missing until a Scratch implementation
  exists. No UI path silently degrades or falsely labels a fallback as SB3.
- The formats and command streams are bounded integer data designed for eventual
  project-list implementations. Current apps/assets are audited rather than claimed
  compatible before the corresponding Scratch handlers exist.

## Verification

- Full strict suite: `python -W error -m pytest -q` -> `152 passed`.
- Python syntax: `python -m compileall -q ide.py runtime.py xe_lang scratch_vm tests`.
- Scratch manifest: `python -B -m scratch_vm.audit --check`.
- Whitespace: `git diff --check`.
- Every `apps/*.xe` file is canonically compiled and then smoke-run for three real
  480x360 frames with an isolated temporary VFS by `tests/test_all_apps_smoke.py`.
- File Explorer focus: `python -W error -m pytest -q
  tests/test_graphics_command_stream.py tests/test_file_explorer_performance.py
  tests/test_file_explorer_interactions.py`.

## Known boundaries

- Exact SB3 generation remains blocked for current builds until a profile implements
  200,000 addresses and all required core/high-level syscalls. The explicit fallback
  is useful for inspection or future templates but is not a runnable SB3 by itself.
- Clipboard shortcuts inside Xe apps use app-local clipboard state because there is
  no system clipboard syscall yet.
- Native sound depends on an available Qt audio output; vanilla Scratch audio seek
  and reverse playback still require a separately implemented portable backend.
- Maximum child-dot Explorer loads can still produce occasional frames above 33 ms,
  and a heavily populated virtual IDE is smooth at roughly 48 fps rather than 60.
- The host design was reconciled in code and visually checked with contact sheets.
  A Figma file was created at
  `https://www.figma.com/design/4O3v7iflhRogbOiw9aujD2`, but Starter-plan MCP quota
  prevented canvas publication during this delivery.
