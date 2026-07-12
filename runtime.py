from xe_lang.lexer import lex
from xe_lang.parser import parse
from xe_lang.semantic import SemanticAnalyzer
from xe_lang.optimizer import Optimizer
from xe_lang.asm import compile_ast, format_instructions
from xe_lang.assembler import assemble
from xe_lang.vm import VM, MAGIC, VERSION
from xe_lang.helper import ANSI


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
	else:
		tokens, error = lex(fn, ftxt)
		if error:
			return None, error, None

		ast = parse(tokens)
		if ast.error:
			return None, ast.error, None

		seman_res = context.semantic.analyze(ast.value)
		if seman_res.error:
			return None, seman_res.error, None
		
		optimized_ast = Optimizer().optimize(ast.value)

		if __name__ == "__main__": print(optimized_ast)

		assembly = compile_ast(optimized_ast, fn)
		if assembly.error:
			return None, assembly.error, None
	
		bytecode = assemble(fn, format_instructions(assembly.value))
		if bytecode.error:
			return None, bytecode.error, None

	context.vm = VM(bytecode.value, output_handler=context.output_handler)
	context.vm.ip = 0

	result = context.vm.run()

	return (
		result.value,
		result.error,
		format_instructions(assembly.value),
	)


if __name__ == "__main__":
	path = input(">>> ")

	file = open(path, "r")
	result, error, asm = run(path, file.read(), None)

	print()

	if error: print(error)
	else:
		print(f"Stack: {result}\n{ANSI.PURPLE}")
		print(asm + ANSI.END)

	print()

	file.close()
