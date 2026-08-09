from pathlib import Path

from xe_lang.compiler_service import compile_source


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "apps" / "file_explorer.xe").read_text(encoding="utf-8")


def test_file_explorer_compiles() -> None:
    artifact = compile_source(SOURCE, "apps/file_explorer.xe")
    assert artifact.success, artifact.diagnostics


def test_projection_uses_unsigned_tilt_depth() -> None:
    assert "projection_depth_scale = math::sin" in SOURCE
    assert "if (projection_depth_scale < 0.0)" in SOURCE
    assert "projected_depth = (int)(-sine * (float)radius * projection_depth_scale" in SOURCE


def test_nucleus_highlight_is_fixed_in_screen_space() -> None:
    body = SOURCE.split("proc draw_placeholder_nucleus", 1)[1].split("proc draw_placeholder_folder", 1)[0]
    assert "draw_placeholder_pixel(x - radius / 2, y - radius / 2" in body
    assert "orbit_project_rotation_offset" not in body


def test_depth_order_and_overlay_layers_are_explicit() -> None:
    assert "array entry_depth_order: int[64]" in SOURCE
    assert "Stable painter order" in SOURCE
    scene = SOURCE.index("call draw_file_atom(3, 12")
    drawer = SOURCE.index("call draw_explorer_sidebar(3, 12", scene)
    drag = SOURCE.index("call draw_explorer_drag_overlay()", drawer)
    modal = SOURCE.index("if (viewer_open)", drag)
    assert scene < drawer < drag < modal


def test_trash_is_subdued_until_valid_drag_hover() -> None:
    sidebar = SOURCE.split("proc draw_explorer_sidebar", 1)[1].split("proc draw_explorer_drag_overlay", 1)[0]
    assert "danger_color = graphics::COLOR_1" in sidebar
    assert "danger_color = graphics::COLOR_4" in sidebar
