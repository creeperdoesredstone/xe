from xe_lang.lexer import lex
from xe_lang.parser import parse
from xe_lang.semantic import SemanticAnalyzer
from xe_lang.optimizer import Optimizer
from xe_lang.codegen import compile_ast, format_instructions
from xe_lang.ir_optimize import optimize, DEFAULT_PASSES
from xe_lang.assembler import assemble
from xe_lang.vm import VM, MAGIC, VERSION
from xe_lang.helper import ANSI

import traceback


class RuntimeContext:
	def __init__(self) -> None:
		self.semantic = SemanticAnalyzer()
		self.vm = VM([MAGIC, VERSION, 0, 0], output_handler=None)
		self.output_handler = None


def run(
	fn: str,
	ftxt: str,
	context: RuntimeContext | None = None,
) -> tuple:
	if context is None:
		context = RuntimeContext()

	if fn.lower().endswith(".xas"):
		bytecode = assemble(fn, ftxt)
		if bytecode.error:
			return None, bytecode.error, None
		
		context.vm = VM(bytecode.value, output_handler=context.output_handler)
		context.vm.ip = 0

		result = context.vm.run()

		return (
			result.value,
			result.error,
			ftxt,
		)
	else:
		tokens, error = lex(fn, ftxt)
		if error:
			return None, error, None

		ast = parse(tokens)
		if ast.error:
			return None, ast.error, None
		if __name__ == "__main__":
			print(ast.value)

		seman_res = context.semantic.analyze(ast.value)
		if seman_res.error:
			return None, seman_res.error, None
		
		optimized_ast = Optimizer().optimize(ast.value)

		assembly = compile_ast(optimized_ast, fn)
		if assembly.error:
			return None, assembly.error, None
		print(ANSI.BOLD + ANSI.PURPLE + "\nLABELS" + ANSI.END)

		optimized_asm = optimize(assembly.value, DEFAULT_PASSES)
		formatted_asm = format_instructions(optimized_asm)
	
		bytecode = assemble(fn, formatted_asm)
		if bytecode.error:
			return None, bytecode.error, None
		

	if __name__ == "__main__":
		print(f"\n\n{ANSI.BOLD}{ANSI.PURPLE}STDOUT:{ANSI.END}")
	context.vm = VM(bytecode.value, output_handler=context.output_handler)
	context.vm.ip = 0

	result = context.vm.run()

	return (
		result.value[:context.vm.sp][:32],
		result.error,
		formatted_asm,
	)


import os

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
				with open(path, "r") as file:
					source_code = file.read()
				
				result, error, asm = run(path, source_code, None)
				
				print()
				if error:
					print(error)
				else:
					print(f"\nStack: {result}\n\n{ANSI.PURPLE}Assembly:")
					print(ANSI.GREEN + asm + ANSI.END)
				print()
			except Exception as e:
				traceback.print_exc()

	elif choice == "2":
		print("Xe Lang REPL Environment (Type 'exit' or 'quit' to leave)")
		print("-" * 50)
		
		fn = "<repl>"
		context = RuntimeContext()
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
