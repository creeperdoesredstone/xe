from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xe_lang.assembler import INSTRUCTION_MAP
from xe_lang.compiler_service import compile_source
from xe_lang.syscall_abi import SyscallID


def _assembly_usage(assembly: str) -> tuple[set[str], set[int]]:
    instructions: set[str] = set()
    syscalls: set[int] = set()

    for raw_line in assembly.splitlines():
        line = raw_line.split(";", 1)[0].strip()
        if not line or line.startswith(":"):
            continue

        fields = line.split()
        opcode = fields[0].upper()
        if opcode not in INSTRUCTION_MAP:
            continue

        instructions.add(opcode)
        if opcode == "SYS" and len(fields) >= 2:
            try:
                syscalls.add(int(fields[1], 0))
            except ValueError:
                continue

    return instructions, syscalls


def _syscall_names_by_value() -> dict[int, tuple[str, ...]]:
    names: dict[int, list[str]] = defaultdict(list)
    for name, syscall in SyscallID.__members__.items():
        names[int(syscall)].append(name)
    return {value: tuple(items) for value, items in names.items()}


def _format_report(
    app_count: int,
    used_instructions: set[str],
    used_syscalls: set[int],
    failures: list[tuple[str, str]],
) -> str:
    unused_instructions = sorted(set(INSTRUCTION_MAP) - used_instructions)
    syscall_names = _syscall_names_by_value()
    unused_syscalls = sorted(set(syscall_names) - used_syscalls)

    lines = [
        "Unused Xe app ABI entries",
        "==========================",
        f"Apps compiled: {app_count}",
        "",
        "UNUSED INSTRUCTIONS",
        "--------------------",
    ]
    lines.extend(unused_instructions or ["(none)"])
    lines.extend(
        [
            "",
            "UNUSED SYSCALLS",
            "----------------",
        ]
    )
    if unused_syscalls:
        for value in unused_syscalls:
            lines.append(f"{value}: {', '.join(syscall_names[value])}")
    else:
        lines.append("(none)")

    if failures:
        lines.extend(
            [
                "",
                "COMPILE FAILURES",
                "----------------",
            ]
        )
        lines.extend(f"{path}: {message}" for path, message in failures)

    lines.extend(
        [
            "",
            "USED COUNTS",
            "-----------",
            f"Instructions: {len(used_instructions)} / {len(INSTRUCTION_MAP)}",
            f"Syscalls: {len(used_syscalls)} / {len(syscall_names)}",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile every app and report unused instructions and syscalls.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root (default: inferred from this file)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="report path (default: tools/unused_app_abi.txt)",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    output = args.output or root / "tools" / "unused_app_abi.txt"
    app_paths = sorted((root / "apps").glob("*.xe"), key=lambda path: (path.name.casefold(), path.name))

    used_instructions: set[str] = set()
    used_syscalls: set[int] = set()
    failures: list[tuple[str, str]] = []

    for app_path in app_paths:
        artifact = compile_source(app_path.read_text(encoding="utf-8"), app_path.as_posix())
        if not artifact.success:
            message = "; ".join(str(diagnostic) for diagnostic in artifact.diagnostics) or "compilation failed"
            failures.append((app_path.relative_to(root).as_posix(), message))
            continue

        instructions, syscalls = _assembly_usage(artifact.assembly)
        used_instructions.update(instructions)
        used_syscalls.update(syscalls)

    report = _format_report(len(app_paths), used_instructions, used_syscalls, failures)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(f"Wrote {output}")
    print(f"Compiled {len(app_paths)} app(s); {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
