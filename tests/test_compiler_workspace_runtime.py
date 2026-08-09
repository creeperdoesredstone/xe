from __future__ import annotations

from pathlib import Path

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source
from xe_lang.devices.compiler import CompilerDevice


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
