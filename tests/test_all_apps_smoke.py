from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source


ROOT = Path(__file__).resolve().parents[1]


def test_every_bundled_app_compiles_and_publishes_bounded_frames(tmp_path: Path) -> None:
	failures: list[str] = []
	for app_path in sorted((ROOT / "apps").glob("*.xe")):
		source = app_path.read_text(encoding="utf-8")
		artifact = compile_source(source, f"apps/{app_path.name}")
		if not artifact.success:
			failures.append(
				f"{app_path.name}: " + "; ".join(str(item) for item in artifact.diagnostics)
			)
			continue

		frames = []
		context: RuntimeContext

		def on_frame(frame) -> None:
			frames.append(frame)
			if len(frames) >= 3:
				context.cancel()

		context = RuntimeContext(
			frame_handler=on_frame,
			filesystem_root=tmp_path / app_path.stem,
		)
		with redirect_stdout(StringIO()):
			_, error, _ = run(f"apps/{app_path.name}", source, context)
		if error is not None:
			failures.append(f"{app_path.name}: {error}")
		elif len(frames) != 3:
			failures.append(f"{app_path.name}: published {len(frames)} frames instead of 3")
		elif any((frame.width, frame.height) != (480, 360) for frame in frames):
			failures.append(f"{app_path.name}: published a non-480x360 stage")

	assert not failures, "\n".join(failures)
