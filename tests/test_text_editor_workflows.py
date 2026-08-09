from pathlib import Path

from xe_lang.compiler_service import compile_source


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "apps" / "text_editor.xe").read_text(encoding="utf-8")


def test_text_editor_compiles() -> None:
    artifact = compile_source(SOURCE, "apps/text_editor.xe")
    assert artifact.success, artifact.diagnostics


def test_xe_feedback_is_opt_in() -> None:
    assert "xe_mode = false" in SOURCE
    assert "if (xe_mode && !compile_ok" in SOURCE
    assert 'editor_status = "Xe source OFF"' in SOURCE
    assert 'xe_label = "Xe OFF"' in SOURCE


def test_dirty_actions_offer_save_discard_cancel() -> None:
    assert "var dirty_prompt_action: int" in SOURCE
    assert '"Save changes?"' in SOURCE
    assert '"Don\'t save"' in SOURCE
    assert '"Cancel"' in SOURCE
    assert "request_new_editor_file" in SOURCE
    assert "Save changes before opening?" in SOURCE


def test_open_picker_supports_direct_double_click() -> None:
    assert "file_dialog_last_entry == index" in SOURCE
    assert "current_ticks - file_dialog_last_ticks <= 450" in SOURCE
    assert "call confirm_file_dialog()" in SOURCE


def test_save_failure_keeps_dirty_state() -> None:
    save_body = SOURCE.split("proc save_editor_to", 1)[1].split("proc begin_save_dialog", 1)[0]
    failure_body = save_body.split("else {", 1)[1]
    assert '"Save failed"' in failure_body
    assert "dirty = false" not in failure_body
