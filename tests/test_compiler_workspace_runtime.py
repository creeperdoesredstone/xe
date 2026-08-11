from __future__ import annotations

from pathlib import Path

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source
from xe_lang.devices.compiler import CompilerDevice


ROOT = Path(__file__).resolve().parents[1]


def test_small_draggable_window_example_compiles() -> None:
	path = ROOT / "examples" / "small_draggable_window.xe"
	artifact = compile_source(path.read_text(encoding="utf-8"), path.as_posix())
	assert artifact.success, artifact.diagnostics
	assert "app.graphics" in artifact.required_capabilities


def test_compiler_device_links_workspace_declarations_deterministically() -> None:
	device = CompilerDevice()
	sources = {
		"workspace.xe": "out << add(20, 22)",
		"lib/math.xe": "fn add(left: int, right: int) int { return left + right }",
	}
	assert device.compile_workspace(sources)
	first = device.bytecode
	assert device.compile_workspace(dict(reversed(tuple(sources.items()))))
	assert device.bytecode == first


def test_workspace_stdlib_contract_compiles() -> None:
	artifact = compile_source('''
var ok: bool
var output: string
ok = compiler::check_workspace("workspace.xe")
output = compiler::run_workspace("workspace.xe")
out << output
''')
	assert artifact.success, artifact.diagnostics
	assert 290 in artifact.required_syscalls
	assert 291 in artifact.required_syscalls


def test_in_vm_workspace_run_uses_unsaved_document_and_vfs_siblings(tmp_path: Path) -> None:
	drive = tmp_path / "drive"
	drive.mkdir()
	(drive / "workspace.xe").write_text("out << 0", encoding="utf-8")
	(drive / "helper.xe").write_text(
		"fn add(left: int, right: int) int { return left + right }",
		encoding="utf-8",
	)
	context = RuntimeContext(filesystem_root=drive)
	output: list[str] = []
	context.output_handler = output.append
	source = '''
var count: int
count = compiler::load_document(0, "workspace.xe", "out << add(20, 22)")
out << compiler::run_workspace("workspace.xe")
'''
	_, error, _ = run("ide_workspace_test.xe", source, context)
	assert error is None
	assert "".join(output) == "42"


def test_in_vm_workspace_run_presents_graphical_child_frames(tmp_path: Path) -> None:
	drive = tmp_path / "drive"
	drive.mkdir()
	(drive / "workspace.xe").write_text(
		'''var win: graphics::Window
win.x = 24
win.y = 20
win.width = 120
win.height = 80
win.title = "Preview"
win.ui_scale = 1
win.state = graphics::WINDOW_NORMAL
call graphics::begin_draw(win)
call graphics::clear(win, graphics::BLACK)
call graphics::fill_rect(win, 8, 8, 40, 18, graphics::COLOR_5)
call graphics::update(win)
win.close()
''',
		encoding="utf-8",
	)
	frames = []
	context = RuntimeContext(filesystem_root=drive, frame_handler=frames.append)
	_, error, _ = run(
		"graphical_workspace_test.xe",
		'out << compiler::run_workspace("workspace.xe")',
		context,
	)
	assert error is None
	assert frames
	assert any(any(color != 0 for color in frame.indices) for frame in frames)
	assert not any(frames[-1].indices)


def test_graphical_child_composes_over_and_restores_parent_window(tmp_path: Path) -> None:
	drive = tmp_path / "drive"
	drive.mkdir()
	(drive / "workspace.xe").write_text(
		'''var child: graphics::Window
child.x = 24
child.y = 20
child.width = 120
child.height = 80
child.title = "Child"
child.ui_scale = 1
child.state = graphics::WINDOW_NORMAL
call graphics::begin_draw(child)
call graphics::clear(child, graphics::BLACK)
call graphics::fill_rect(child, 8, 8, 40, 18, graphics::COLOR_5)
call graphics::update(child)
child.close()
''',
		encoding="utf-8",
	)
	frames = []
	context = RuntimeContext(filesystem_root=drive, frame_handler=frames.append)
	output: list[str] = []
	context.output_handler = output.append
	_, error, _ = run(
		"graphical_parent_test.xe",
		'''var parent: graphics::Window
var output: string
parent.x = 230
parent.y = 170
parent.width = 200
parent.height = 140
parent.title = "Virtual IDE"
parent.ui_scale = 1
parent.state = graphics::WINDOW_NORMAL
call graphics::begin_draw(parent)
call graphics::clear(parent, graphics::COLOR_1)
call graphics::fill_rect(parent, 20, 20, 36, 16, graphics::COLOR_12)
call graphics::update(parent)
output = compiler::run_workspace("workspace.xe")
out << parent.state
''',
		context,
	)
	assert error is None
	assert len(frames) >= 3
	parent_frame = frames[0]
	child_frame = frames[-2]
	restored_frame = frames[-1]
	assert child_frame.indices != parent_frame.indices
	assert child_frame.indices[210 * parent_frame.width + 252] == 12
	assert restored_frame.indices == parent_frame.indices
	assert "".join(output) == "0"


def test_in_vm_workspace_run_routes_audio_only_child_to_host(tmp_path: Path) -> None:
	from xe_lang.media import NoteEvent, Track, encode_xmusic

	drive = tmp_path / "drive"
	drive.mkdir()
	(drive / "workspace.xe").write_text(
		'''var track: audio::Track
var playing: bool
track = audio::load("tone.xmusic")
playing = audio::play(track)
''',
		encoding="utf-8",
	)
	words = encode_xmusic(Track(120, 480, (NoteEvent(0, 480, 60),)))
	(drive / "tone.xmusic").write_text("\n".join(hex(word) for word in words), encoding="ascii")
	states = []
	context = RuntimeContext(filesystem_root=drive, audio_handler=states.append)
	_, error, _ = run(
		"audio_workspace_test.xe",
		'out << compiler::run_workspace("workspace.xe")',
		context,
	)
	assert error is None
	assert any(state.playing and state.voices for state in states)


def test_nested_workspace_ignores_unrelated_projects_in_private_drive(tmp_path: Path) -> None:
	drive = tmp_path / "drive"
	project = drive / "project"
	(project / "lib").mkdir(parents=True)
	(project / "workspace.xe").write_text("out << add(20, 22)", encoding="utf-8")
	(project / "lib" / "math.xe").write_text(
		"fn add(left: int, right: int) int { return left + right }",
		encoding="utf-8",
	)
	# Executable top-level code outside the entry's parent tree would be an error
	# if the runtime accidentally treated the complete private drive as one build.
	(drive / "unrelated.xe").write_text("out << 99", encoding="utf-8")
	context = RuntimeContext(filesystem_root=drive)
	output: list[str] = []
	context.output_handler = output.append
	_, error, _ = run(
		"nested_workspace_test.xe",
		'out << compiler::run_workspace("project/workspace.xe")',
		context,
	)
	assert error is None
	assert "".join(output) == "42"


def test_workspace_check_reports_missing_entry_without_host_path_escape(tmp_path: Path) -> None:
	context = RuntimeContext(filesystem_root=tmp_path / "drive")
	output: list[str] = []
	context.output_handler = output.append
	_, error, _ = run(
		"missing_workspace_test.xe",
		'out << compiler::check_workspace("../workspace.xe")',
		context,
	)
	assert error is None
	assert "".join(output) == "0"
	assert "portable" in context.vm.devices.compiler.snapshot.error.lower()
