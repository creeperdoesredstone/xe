from xe_lang.lexer import lex
from xe_lang.parser import parse
from xe_lang.semantic import SemanticAnalyzer
from xe_lang.optimizer import Optimizer
from xe_lang.asm import compile_ast, format_instructions
from xe_lang.vm import VM


class RuntimeContext:
    def __init__(self) -> None:
        self.semantic = SemanticAnalyzer()
        self.vm = VM([], output_handler=None)
        self.output_handler = None


def run(
    fn: str,
    ftxt: str,
    context: RuntimeContext | None = None,
) -> tuple:
    if context is None:
        context = RuntimeContext()

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

    assembly = compile_ast(optimized_ast, fn)
    if assembly.error:
        return None, assembly.error, None

    context.vm = VM(assembly.value, output_handler=context.output_handler)
    context.vm.ip = 0
    context.vm.process_labels()

    result = context.vm.run()

    return (
        result.value,
        result.error,
        format_instructions(assembly.value),
    )


if __name__ == "__main__":
    ftxt = input(">>> ")
    run("<test>", ftxt, None)
