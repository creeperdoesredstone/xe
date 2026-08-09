from pathlib import Path

from runtime import RuntimeContext, run
from xe_lang.compiler_service import capability_for_syscall, compile_source, compile_workspace


def test_compile_source_is_deterministic_and_reports_contract() -> None:
	first = compile_source("out << 17", "workspace.xe")
	second = compile_source("out << 17", "workspace.xe")
	assert first.success
	assert first.program == second.program
	assert first.artifact_hash == second.artifact_hash
	assert first.required_syscalls == (1, 5, 21, 22)
	assert first.memory.address_limit == 2_000_000
	assert compile_source("out << 17", r"C:\projects\demo\program.xe").units[0].path == "program.xe"
	assert capability_for_syscall(276) == "app.graphics"


def test_compile_artifact_finds_literal_assets_and_marks_dynamic_loads() -> None:
	artifact = compile_source('''
var image: graphics::Image
var track: audio::Track
var name: string
name = "dynamic.ximg"
image = graphics::load_image("assets/icon.ximg")
track = audio::load("music/demo.xmusic")
image = graphics::load_image(name)
''')
	assert artifact.success, artifact.diagnostics
	assert artifact.assets == ("assets/icon.ximg", "music/demo.xmusic")
	assert len(artifact.dynamic_assets) == 1
	assert "graphics::load_image" in artifact.dynamic_assets[0]


def test_compile_workspace_links_declarations_and_uses_workspace_entry() -> None:
	artifact = compile_workspace({
		"math/helpers.xe": "fn twice(value: int) int { return value * 2 }",
		"workspace.xe": "out << twice(5)",
	})
	assert artifact.success, artifact.diagnostics
	assert artifact.entry_path == "workspace.xe"
	assert tuple(unit.path for unit in artifact.units) == ("math/helpers.xe", "workspace.xe")


def test_compile_workspace_rejects_executable_library_statements() -> None:
	artifact = compile_workspace({
		"library.xe": "out << 1",
		"workspace.xe": "out << 2",
	})
	assert not artifact.success
	assert artifact.diagnostics[0].code == "XE2002"
	assert artifact.diagnostics[0].path == "library.xe"


def test_runtime_compile_does_not_write_build_artifacts(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.chdir(tmp_path)
	output: list[str] = []
	context = RuntimeContext(filesystem_root=tmp_path / "drive")
	context.output_handler = output.append
	stack, error, assembly = run("program.xe", "out << 17", context)
	assert error is None
	assert stack == []
	assert "".join(output) == "17"
	assert assembly
	assert not (tmp_path / "asm").exists()
	assert not (tmp_path / "exe").exists()
