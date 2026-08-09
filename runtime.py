from xe_lang.semantic import SemanticAnalyzer
from xe_lang.assembler import assemble
from xe_lang.compiler_service import compile_source
from xe_lang.vm import DEFAULT_DATA_WORDS, VM, MAGIC, VERSION
from xe_lang.devices import OSDevice, default_settings_path
from xe_lang.helper import ANSI

import traceback
import threading
import os
from pathlib import Path


class RuntimeContext:
	def __init__(
		self,
		os_device: OSDevice | None = None,
		frame_handler=None,
		vm_ready_handler=None,
		filesystem_root: str | Path | None = None,
		input_handler=None,
		request_handler=None,
		audio_handler=None,
		clipboard_read_handler=None,
		clipboard_write_handler=None,
		memory_words: int = DEFAULT_DATA_WORDS,
	) -> None:
		self.semantic = SemanticAnalyzer()
		self.os_device = os_device or OSDevice()
		self.frame_handler = frame_handler
		self.vm_ready_handler = vm_ready_handler
		self.input_handler = input_handler
		self.request_handler = request_handler
		self.audio_handler = audio_handler
		self.clipboard_read_handler = clipboard_read_handler
		self.clipboard_write_handler = clipboard_write_handler
		self.os_device.set_clipboard_handlers(
			self.clipboard_read_handler,
			self.clipboard_write_handler,
		)
		self.memory_words = memory_words
		self.filesystem_root = Path(filesystem_root).resolve() if filesystem_root is not None else None
		self.cancel_event = threading.Event()
		self.output_handler = None
		self.vm = VM(
			[MAGIC, VERSION, 0, 0],
			output_handler=None,
			os_device=self.os_device,
			frame_handler=self.frame_handler,
			cancel_event=self.cancel_event,
			filesystem_root=self.filesystem_root,
			input_handler=self.input_handler,
			request_handler=self.request_handler,
			audio_handler=self.audio_handler,
			memory_words=self.memory_words,
		)
		self.filesystem_root = self.vm.devices.files.root

	def create_vm(self, program: list[int]) -> VM:
		self.cancel_event.clear()
		self.os_device.set_clipboard_handlers(
			self.clipboard_read_handler,
			self.clipboard_write_handler,
		)
		self.vm = VM(
			program,
			output_handler=self.output_handler,
			os_device=self.os_device,
			frame_handler=self.frame_handler,
			cancel_event=self.cancel_event,
			filesystem_root=self.filesystem_root,
			input_handler=self.input_handler,
			request_handler=self.request_handler,
			audio_handler=self.audio_handler,
			memory_words=self.memory_words,
		)
		if self.vm_ready_handler:
			self.vm_ready_handler(self.vm)
		return self.vm

	def cancel(self) -> None:
		self.cancel_event.set()


def _stack_result(vm: VM, value: object) -> list[int]:
	if not isinstance(value, list):
		return []
	return value[:vm.sp][:32]


def run(
	fn: str,
	ftxt: str,
	context: RuntimeContext | None = None,
) -> tuple:
	if context is None:
		context = RuntimeContext()

	if fn.lower().endswith(".xas"):
		bytecode = assemble(fn, ftxt, emit_file=False)
		if bytecode.error:
			return None, bytecode.error, None

		context.create_vm(bytecode.value)
		context.vm.ip = 0

		result = context.vm.run()

		return (
			_stack_result(context.vm, result.value),
			result.error,
			ftxt,
		)
	else:
		artifact = compile_source(ftxt, fn)
		if not artifact.success:
			diagnostic = artifact.diagnostics[0] if artifact.diagnostics else "Compilation failed"
			return None, diagnostic, None
		formatted_asm = artifact.assembly
		program = list(artifact.program)


	if __name__ == "__main__":
		print(f"\n\n{ANSI.BOLD}{ANSI.PURPLE}STDOUT:{ANSI.END}")
	context.create_vm(program)
	context.vm.ip = 0

	result = context.vm.run()
	stack_value = _stack_result(context.vm, result.value)

	return (
		stack_value,
		result.error,
		formatted_asm,
	)


if __name__ == "__main__":
	print("Welcome to Xe Lang!")
	print("1. Run a file")
	print("2. Launch REPL")

	choice = input("Select mode (1 or 2): ").strip()
	print()

	if choice == "1":
		path = input("Enter file path: ").strip()
		if not os.path.exists(path):
			print(f"Error: File '{path}' does not exist.")
		else:
			try:
				with open(path, "r", encoding="utf-8", newline="") as file:
					source_code = file.read()

				result, error, asm = run(
					path,
					source_code,
					RuntimeContext(os_device=OSDevice(settings_path=default_settings_path())),
				)

				print()
				if error:
					print(error)
				else:
					print(f"\nStack: {result}\n\n{ANSI.PURPLE}Assembly:")
					print(ANSI.GREEN + asm + ANSI.END)
				print()
			except Exception:
				traceback.print_exc()

	elif choice == "2":
		print("Xe Lang REPL Environment (Type 'exit' or 'quit' to leave)")
		print("-" * 50)

		fn = "<repl>"
		context = RuntimeContext(os_device=OSDevice(settings_path=default_settings_path()))
		while True:
			try:
				text = input("xe >>> ").strip()
				if not text:
					continue
				if text.lower() in ("exit", "quit"):
					break

				result, error, asm = run(fn, text, context)

				if error:
					print(error)
				else:
					if result is not None:
						print(f"Stack: {result}")
					if asm:
						print(f"{ANSI.PURPLE}{asm}{ANSI.END}")

			except KeyboardInterrupt:
				print("\nExiting REPL.")
				break
			except Exception as e:
				print(f"REPL Error: {e}")
	else:
		print("Invalid choice. Exiting.")
