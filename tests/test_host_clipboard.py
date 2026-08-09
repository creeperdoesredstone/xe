from __future__ import annotations

from runtime import RuntimeContext, run
from xe_lang.compiler_service import capability_for_syscall, compile_source
from xe_lang.devices import OSDevice
from xe_lang.sb3_exporter import analyze_compatibility
from xe_lang.scratch_profile import load_bundled_profile
from xe_lang.syscall_abi import SyscallID


def test_vm_clipboard_round_trip_uses_explicit_host_callbacks() -> None:
	state = {"text": "host value"}

	def write(text: str) -> bool:
		state["text"] = text
		return True

	context = RuntimeContext(
		clipboard_read_handler=lambda: state["text"],
		clipboard_write_handler=write,
	)
	output: list[str] = []
	context.output_handler = output.append
	_, error, _ = run(
		"clipboard.xe",
		'var ok: bool\nok = os::clipboard_write("from Xe")\nout << os::clipboard_read()',
		context,
	)
	assert error is None
	assert state["text"] == "from Xe"
	assert "".join(output) == "from Xe"


def test_disabled_clipboard_contract_is_empty_and_read_only() -> None:
	device = OSDevice()
	assert device.clipboard_read() == ""
	assert not device.clipboard_write("not leaked")
	device.set_clipboard_handlers(lambda: "enabled", lambda _text: True)
	assert device.clipboard_read() == "enabled"
	assert device.clipboard_write("accepted")
	device.set_clipboard_handlers(None, None)
	assert device.clipboard_read() == ""
	assert not device.clipboard_write("not leaked")


def test_clipboard_syscalls_are_host_only_and_block_exact_scratch_export() -> None:
	artifact = compile_source('out << os::clipboard_read()\nvar ok: bool\nok = os::clipboard_write("x")')
	assert artifact.success, artifact.diagnostics
	assert SyscallID.APP_OS_CLIPBOARD_READ in artifact.required_syscalls
	assert SyscallID.APP_OS_CLIPBOARD_WRITE in artifact.required_syscalls
	assert "app.os" in artifact.required_capabilities
	assert capability_for_syscall(SyscallID.APP_OS_CLIPBOARD_READ) == "app.os"
	assert capability_for_syscall(SyscallID.APP_OS_CLIPBOARD_WRITE) == "app.os"
	report = analyze_compatibility(artifact, load_bundled_profile())
	assert not report.exact
	unsupported = {issue.syscall for issue in report.issues if issue.code == "unsupported-syscall"}
	assert SyscallID.APP_OS_CLIPBOARD_READ in unsupported
	assert SyscallID.APP_OS_CLIPBOARD_WRITE in unsupported
