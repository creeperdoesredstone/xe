from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from runtime import RuntimeContext, run
from xe_lang.compiler_service import compile_source


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "apps" / "text_editor.xe").read_text(encoding="utf-8")


def run_editor_probe(probe: str, filesystem_root: Path | None = None) -> str:
    anchor = "while (editor_window.state != graphics::WINDOW_CLOSED) {"
    if anchor not in SOURCE:
        raise AssertionError("Text editor probe anchor is missing")
    modified = SOURCE.replace(anchor, f"{probe}\nif (false) {{", 1)
    parts: list[str] = []
    context = RuntimeContext(filesystem_root=filesystem_root)
    context.output_handler = parts.append
    with redirect_stdout(StringIO()):
        _, error, _ = run("apps/text_editor-probe.xe", modified, context)
    if error is not None:
        raise AssertionError(str(error))
    return "".join(parts)


def test_text_editor_compiles() -> None:
    artifact = compile_source(SOURCE, "apps/text_editor.xe")
    assert artifact.success, artifact.diagnostics


def test_xe_feedback_is_opt_in() -> None:
    assert "xe_mode = false" in SOURCE
    assert "if (xe_mode && !compile_ok" in SOURCE
    assert 'editor_status = "Xe source OFF"' in SOURCE
    assert 'xe_label = "Xe OFF"' in SOURCE


def test_dirty_actions_execute_save_discard_and_cancel(tmp_path: Path) -> None:
    cancel_drive = tmp_path / "cancel"
    cancel_drive.mkdir()
    cancel = run_editor_probe(
        '''dirty = true
call request_new_editor_file()
out << (int)dirty_prompt_open
out << ":"
out << dirty_prompt_action
call resolve_dirty_prompt(0)
out << ":"
out << (int)dirty
out << ":"
out << xestring::strlen(editor_text)''',
        cancel_drive,
    )
    assert cancel.startswith("1:1:1:")
    assert int(cancel.rsplit(":", 1)[1]) > 0

    discard_drive = tmp_path / "discard"
    discard_drive.mkdir()
    discarded = run_editor_probe(
        '''dirty = true
call request_new_editor_file()
call resolve_dirty_prompt(2)
out << (int)dirty_prompt_open
out << ":"
out << dirty_prompt_action
out << ":"
out << (int)dirty
out << ":"
out << xestring::strlen(editor_text)''',
        discard_drive,
    )
    assert discarded == "0:0:0:0"

    save_drive = tmp_path / "save"
    save_drive.mkdir()
    saved = run_editor_probe(
        '''call set_text(editor_path, "saved.txt")
dirty = true
call request_new_editor_file()
call resolve_dirty_prompt(1)
out << (int)dirty_prompt_open
out << ":"
out << dirty_prompt_action
out << ":"
out << xestring::strlen(editor_text)''',
        save_drive,
    )
    assert saved == "0:0:0"
    assert (save_drive / "saved.txt").read_text(encoding="utf-8").startswith("# Xe source")


def test_open_picker_supports_direct_double_click(tmp_path: Path) -> None:
    drive = tmp_path / "drive"
    drive.mkdir()
    (drive / "notes.txt").write_text("opened notes", encoding="utf-8")
    output = run_editor_probe(
        '''call begin_open_dialog()
current_ticks = 100
call select_file_dialog_entry(0)
out << (int)file_dialog_open
out << ":"
out << file_dialog_selected
current_ticks = 350
call select_file_dialog_entry(0)
out << "|"
out << (int)file_dialog_open
out << ":"
out << editor_path
out << ":"
out << editor_text''',
        drive,
    )
    assert output == "1:notes.txt|0:notes.txt:opened notes"


def test_save_failure_keeps_dirty_state(tmp_path: Path) -> None:
    drive = tmp_path / "drive"
    drive.mkdir()
    output = run_editor_probe(
        '''dirty = true
call save_editor_to("../outside.txt")
out << (int)dirty
out << ":"
out << editor_status''',
        drive,
    )
    assert output == "1:Save failed"
    assert not (tmp_path / "outside.txt").exists()
