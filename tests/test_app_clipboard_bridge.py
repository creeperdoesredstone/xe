from __future__ import annotations

from pathlib import Path

import pytest

from xe_lang.compiler_service import compile_source
from xe_lang.syscall_abi import SyscallID


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
	"relative_path",
	(
		"apps/calculator.xe",
		"apps/file_explorer.xe",
		"apps/xenon_terminal.xe",
		"apps/text_editor.xe",
		"apps/xenon_ide.xe",
	),
)
def test_editor_like_apps_use_the_optional_system_clipboard_bridge(relative_path: str) -> None:
	path = ROOT / relative_path
	artifact = compile_source(path.read_text(encoding="utf-8"), relative_path)
	assert artifact.success, artifact.diagnostics
	assert SyscallID.APP_OS_CLIPBOARD_READ in artifact.required_syscalls
	assert SyscallID.APP_OS_CLIPBOARD_WRITE in artifact.required_syscalls
