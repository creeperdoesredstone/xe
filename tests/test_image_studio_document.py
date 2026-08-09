import os
import json
import threading
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPainter
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from xe_lang.host_tools.image_document import (
	ImageProject,
	ImageStudioDocument,
	ImageStudioError,
	QtImageProjectCodec,
)
from xe_lang.host_tools.image_studio import ImageStudioPane
from xe_lang.host_tools.image_specs import IMAGE_EXPORTS, IMAGE_TOOLS, export_dialog_filter
from xe_lang.host_tools.render_helpers import visible_checker_cells
from xe_lang.media import write_xip


@pytest.fixture(scope="module")
def app():
	instance = QApplication.instance() or QApplication([])
	yield instance


def test_layer_and_frame_shapes_remain_normalized():
	document = ImageStudioDocument(ImageProject.blank(12, 9))
	document.add_frame(copy_current=True)
	document.add_layer("Ink")
	assert document.project.frame_count == 2
	assert len(document.project.layers) == 2
	assert all(len(layer.frames) == 2 for layer in document.project.layers)
	assert all(frame.width() == 12 and frame.height() == 9 for layer in document.project.layers for frame in layer.frames)


def test_frame_duration_is_added_once_with_multiple_layers_and_budget_is_enforced(monkeypatch):
	from xe_lang.host_tools import image_document

	monkeypatch.setattr(image_document, "MAX_PROJECT_PIXELS", 64)
	document = ImageStudioDocument(ImageProject.blank(4, 4))
	document.add_layer("Ink")
	document.add_frame(copy_current=True)
	assert document.project.frame_count == 2
	assert len(document.project.frame_durations_ms) == 2
	assert all(len(layer.frames) == 2 for layer in document.project.layers)
	with pytest.raises(ImageStudioError, match="budget"):
		document.add_frame()
	with pytest.raises(ImageStudioError, match="budget"):
		document.add_layer()


def test_draw_undo_and_redo_restore_pixels():
	document = ImageStudioDocument(ImageProject.blank(8, 8))
	transparent = document.current_image().pixelColor(2, 2)
	document.checkpoint()
	document.draw_line(QPoint(1, 1), QPoint(3, 3), QColor("#ff0044"))
	assert document.current_image().pixelColor(2, 2).alpha() == 255
	assert document.undo()
	assert document.current_image().pixelColor(2, 2).rgba() == transparent.rgba()
	assert document.redo()
	assert document.current_image().pixelColor(2, 2).name() == "#ff0044"


def test_cancelled_interaction_preserves_an_existing_redo_branch():
	document = ImageStudioDocument(ImageProject.blank(4, 4))
	document.checkpoint()
	document.draw_dab(QPoint(1, 1), QColor("#ffffff"))
	document.checkpoint()
	document.draw_dab(QPoint(2, 2), QColor("#ff0044"))
	assert document.undo()
	assert document.can_redo
	document.checkpoint()
	document.discard_checkpoint()
	assert document.can_redo
	assert document.redo()
	assert document.current_image().pixelColor(2, 2).name() == "#ff0044"


def test_flood_fill_stops_at_boundary():
	document = ImageStudioDocument(ImageProject.blank(7, 7))
	border = QColor("#ffffff")
	document.draw_shape("rect", QPoint(1, 1), QPoint(5, 5), border)
	assert document.flood_fill(QPoint(3, 3), QColor("#29cc88"))
	assert document.current_image().pixelColor(3, 3).name() == "#29cc88"
	assert document.current_image().pixelColor(0, 0).alpha() == 0


def test_remove_last_layer_and_frame_is_rejected():
	document = ImageStudioDocument()
	assert not document.remove_layer(0)
	assert not document.remove_frame(0)


def test_png_round_trip_and_sprite_sheet_are_atomic(tmp_path):
	codec = QtImageProjectCodec()
	project = ImageProject.blank(4, 3)
	document = ImageStudioDocument(project)
	document.draw_line(QPoint(0, 0), QPoint(3, 0), QColor("#31d7ff"))
	document.add_frame(copy_current=True)
	document.draw_line(QPoint(0, 2), QPoint(3, 2), QColor("#ff9d3c"))

	png = tmp_path / "image.png"
	codec.export_file(project, png, "png")
	assert png.is_file()
	loaded = codec.import_file(png)
	assert (loaded.width, loaded.height, loaded.frame_count) == (4, 3, 1)

	sheet = tmp_path / "sheet.png"
	codec.export_file(project, sheet, "sprite-sheet")
	loaded_sheet = codec.import_file(sheet)
	assert (loaded_sheet.width, loaded_sheet.height) == (8, 3)
	assert not (tmp_path / ".sheet.png.tmp").exists()


def test_ximg_round_trip_is_deterministic(tmp_path):
	codec = QtImageProjectCodec()
	project = ImageProject.blank(4, 3)
	document = ImageStudioDocument(project)
	document.draw_line(QPoint(0, 0), QPoint(3, 0), QColor("#55ffff"))
	first = tmp_path / "first.ximg"
	second = tmp_path / "second.ximg"
	codec.export_file(project, first, "ximg")
	codec.export_file(project, second, "ximg")
	assert first.read_bytes() == second.read_bytes()
	loaded = codec.import_file(first)
	assert (loaded.width, loaded.height, loaded.frame_count) == (4, 3, 1)
	assert loaded.composite().pixelColor(0, 0).name() == "#55ffff"


def test_ximg_text_import_is_bounded_before_word_parsing(tmp_path, monkeypatch):
	from xe_lang.media import image_format

	path = tmp_path / "oversized.ximg"
	path.write_text("0x00000000\n" * 20, encoding="ascii")
	monkeypatch.setattr(image_format, "XIMG_MAX_WORDS", 4)
	with pytest.raises(ImageStudioError, match="file-size budget"):
		QtImageProjectCodec().import_file(path)


def test_xip_round_trip_preserves_layers_and_frames(tmp_path):
	codec = QtImageProjectCodec()
	project = ImageProject.blank(3, 2, "Layered")
	document = ImageStudioDocument(project)
	document.add_frame(copy_current=True)
	document.add_layer("Ink")
	document.project.layers[0].visible = False
	document.project.layers[1].opacity = 0.5
	path = tmp_path / "project.xip"
	second_path = tmp_path / "project-copy.xip"
	codec.export_file(project, path, "xip")
	codec.export_file(project, second_path, "xip")
	assert path.read_bytes() == second_path.read_bytes()
	loaded = codec.import_file(path)
	assert loaded.name == "Layered"
	assert loaded.frame_count == 2
	assert [layer.name for layer in loaded.layers] == ["Layer 1", "Ink"]
	assert not loaded.layers[0].visible
	assert loaded.layers[1].opacity == pytest.approx(0.5)


def test_project_cardinality_and_names_match_xip_round_trip_limits(monkeypatch):
	from xe_lang.host_tools import image_document

	assert image_document.MAX_PROJECT_CELS + 1 <= 4096
	project = ImageProject.blank(1, 1)
	project.layers[0].name = "x" * 257
	with pytest.raises(ImageStudioError, match="Layer name"):
		project.normalize()

	monkeypatch.setattr(image_document, "MAX_PROJECT_CELS", 2)
	project = ImageProject.blank(1, 1)
	project.frame_durations_ms[:] = [100, 100, 100]
	project.layers[0].frames[:] = [project.layers[0].frames[0].copy() for _ in range(3)]
	with pytest.raises(ImageStudioError, match="cel budget"):
		project.normalize()


def test_xip_rejects_nonfinite_layer_opacity(tmp_path):
	codec = QtImageProjectCodec()
	project = ImageProject.blank(1, 1)
	payload = codec._png_bytes(project.layers[0].frames[0])
	path = tmp_path / "nonfinite.xip"
	write_xip(
		path,
		{
			"format": "xip",
			"version": 1,
			"name": "Invalid",
			"width": 1,
			"height": 1,
			"frame_durations_ms": [100],
			"layers": [
				{"name": "Layer", "visible": True, "opacity": float("nan"), "frames": ["frame.png"]}
			],
		},
		{"frame.png": payload},
	)
	with pytest.raises(Exception, match="finite"):
		codec.import_file(path)


def test_xip_rejects_coerced_manifest_timing_types(tmp_path):
	codec = QtImageProjectCodec()
	project = ImageProject.blank(1, 1)
	payload = codec._png_bytes(project.layers[0].frames[0])
	path = tmp_path / "coerced-types.xip"
	write_xip(
		path,
		{
			"format": "xip",
			"version": 1,
			"name": "Invalid",
			"width": "1",
			"height": 1,
			"frame_durations_ms": "100",
			"layers": [
				{"name": "Layer", "visible": True, "opacity": 1.0, "frames": ["frame.png"]}
			],
		},
		{"frame.png": payload},
	)
	with pytest.raises(Exception, match="invalid types"):
		codec.import_file(path)


def test_scratch_sprite_export_is_deterministic_and_preserves_animation(tmp_path):
	codec = QtImageProjectCodec()
	project = ImageProject.blank(5, 4, "Walk")
	document = ImageStudioDocument(project)
	document.draw_line(QPoint(0, 0), QPoint(2, 0), QColor("#55ffff"))
	document.add_frame(copy_current=True)
	project.frame_durations_ms[:] = [80, 125]
	document.draw_line(QPoint(1, 1), QPoint(3, 1), QColor("#ff6685"))
	first = tmp_path / "walk.sprite3"
	second = tmp_path / "walk-copy.sprite3"
	codec.export_file(project, first, "scratch-sprite")
	codec.export_file(project, second, "scratch-sprite")
	assert first.read_bytes() == second.read_bytes()
	with zipfile.ZipFile(first) as archive:
		sprite = json.loads(archive.read("sprite.json"))
		assert [costume["name"] for costume in sprite["costumes"]] == ["Frame 001", "Frame 002"]
		assert sprite["blocks"]["xenon_wait_0000"]["inputs"]["DURATION"] == [1, [4, "0.08"]]
		assert sprite["blocks"]["xenon_wait_0001"]["inputs"]["DURATION"] == [1, [4, "0.125"]]
		assert sprite["blocks"]["xenon_frame_0000"]["inputs"]["COSTUME"] == [1, "xenon_costume_0000"]
		assert sprite["blocks"]["xenon_costume_0000"]["fields"]["COSTUME"] == ["Frame 001", None]
		assert all(costume["md5ext"] in archive.namelist() for costume in sprite["costumes"])


def test_image_studio_pane_exposes_all_tools_and_timeline(app):
	pane = ImageStudioPane()
	assert set(pane.tool_buttons) == {
		"pencil",
		"eraser",
		"fill",
		"eyedropper",
		"line",
		"rect",
		"ellipse",
		"select",
	}
	assert pane.frame_list.count() == 1
	assert pane.fps_spin.value() == 10
	assert pane.brush_size.buttonSymbols() == pane.brush_size.ButtonSymbols.NoButtons
	assert pane.duration_spin.buttonSymbols() == pane.duration_spin.ButtonSymbols.NoButtons
	assert pane.brush_stepper.up_button.geometry().center().x() == pane.brush_stepper.down_button.geometry().center().x()
	pane.add_frame()
	assert pane.frame_list.count() == 2
	pane.undo()
	assert pane.frame_list.count() == 1
	pane.resize(1200, 720)
	pane.show()
	app.processEvents()
	assert pane.tool_buttons["rect"].text() == "Rect"
	assert pane.tool_buttons["select"].text() == "Select"
	assert pane.tool_buttons["rect"].toolTip().startswith("Rectangle")
	pane.resize(800, 600)
	app.processEvents()
	assert {button.text() for button in pane.tool_buttons.values()} == {
		"P",
		"E",
		"F",
		"I",
		"L",
		"R",
		"O",
		"M",
	}
	pane.close()


def test_image_studio_metadata_preserves_tool_and_export_contracts():
	assert [spec.key for spec in IMAGE_TOOLS] == [
		"pencil", "eraser", "fill", "eyedropper", "line", "rect", "ellipse", "select"
	]
	assert [spec.key for spec in IMAGE_EXPORTS] == [
		"png", "gif", "sprite-sheet", "scratch-sprite", "xip", "ximg"
	]
	assert export_dialog_filter().startswith("PNG image (*.png);;Animated GIF (*.gif)")


def test_fps_duration_controls_stay_synchronized(app):
	pane = ImageStudioPane()
	pane.fps_spin.setValue(25)
	pane._set_frame_fps()
	assert pane.document.project.frame_durations_ms[0] == 40
	assert pane.duration_spin.value() == 40
	pane.duration_spin.setValue(125)
	pane._set_frame_duration()
	assert pane.fps_spin.value() == 8
	pane.close()


def test_image_studio_normal_and_narrow_layout_keeps_controls_in_bounds(app):
	pane = ImageStudioPane()
	pane.show()
	for width, expected_rect_label in ((1200, "Rect"), (800, "R")):
		pane.resize(width, 620)
		app.processEvents()
		assert pane.tool_buttons["rect"].text() == expected_rect_label
		for control in (
			pane.brush_stepper,
			pane.duration_stepper,
			pane.fps_stepper,
			pane.play_button,
			pane.frame_list,
		):
			assert control.isVisibleTo(pane)
			bottom_right = control.mapTo(pane, control.rect().bottomRight())
			assert 0 <= bottom_right.x() < pane.width()
			assert 0 <= bottom_right.y() < pane.height()
		for stepper in (pane.brush_stepper, pane.duration_stepper, pane.fps_stepper):
			assert stepper.up_button.geometry().center().x() == stepper.down_button.geometry().center().x()
			assert stepper.up_button.height() == stepper.down_button.height()
	pane.close()


def test_canvas_empty_surround_pans_and_brush_hover_tracks_grid(app):
	pane = ImageStudioPane()
	canvas = pane.canvas
	canvas.resize(520, 420)
	canvas.zoom = 2
	canvas.pan = QPointF()
	canvas.show()
	app.processEvents()
	outside = QPointF(4, 4)
	press = QMouseEvent(
		QMouseEvent.Type.MouseButtonPress,
		outside,
		outside,
		Qt.MouseButton.LeftButton,
		Qt.MouseButton.LeftButton,
		Qt.KeyboardModifier.NoModifier,
	)
	canvas.mousePressEvent(press)
	assert canvas._panning
	move_at = QPointF(19, 12)
	move = QMouseEvent(
		QMouseEvent.Type.MouseMove,
		move_at,
		move_at,
		Qt.MouseButton.NoButton,
		Qt.MouseButton.LeftButton,
		Qt.KeyboardModifier.NoModifier,
	)
	canvas.mouseMoveEvent(move)
	assert canvas.pan == QPointF(15, 8)
	release = QMouseEvent(
		QMouseEvent.Type.MouseButtonRelease,
		move_at,
		move_at,
		Qt.MouseButton.LeftButton,
		Qt.MouseButton.NoButton,
		Qt.KeyboardModifier.NoModifier,
	)
	canvas.mouseReleaseEvent(release)
	assert not canvas._panning
	inside = canvas._canvas_origin() + QPointF(3.5 * canvas.zoom, 2.5 * canvas.zoom)
	hover = QMouseEvent(
		QMouseEvent.Type.MouseMove,
		inside,
		inside,
		Qt.MouseButton.NoButton,
		Qt.MouseButton.NoButton,
		Qt.KeyboardModifier.NoModifier,
	)
	canvas.mouseMoveEvent(hover)
	assert canvas._hover_image_pos == QPoint(3, 2)
	pane.close()


def test_pixel_brush_click_paints_exact_cursor_footprint(app):
	pane = ImageStudioPane()
	canvas = pane.canvas
	canvas.resize(420, 360)
	canvas.zoom = 8
	canvas.brush_size = 3
	point = canvas._canvas_origin() + QPointF(5.5 * canvas.zoom, 6.5 * canvas.zoom)
	press = QMouseEvent(
		QMouseEvent.Type.MouseButtonPress,
		point,
		point,
		Qt.MouseButton.LeftButton,
		Qt.MouseButton.LeftButton,
		Qt.KeyboardModifier.NoModifier,
	)
	release = QMouseEvent(
		QMouseEvent.Type.MouseButtonRelease,
		point,
		point,
		Qt.MouseButton.LeftButton,
		Qt.MouseButton.NoButton,
		Qt.KeyboardModifier.NoModifier,
	)
	canvas.mousePressEvent(press)
	canvas.mouseReleaseEvent(release)
	image = pane.document.current_image()
	assert all(image.pixelColor(x, y).alpha() == 255 for y in range(5, 8) for x in range(4, 7))
	assert image.pixelColor(3, 5).alpha() == 0
	pane.close()


def test_checker_iteration_is_bounded_to_visible_viewport():
	cells = list(
		visible_checker_cells(
			QRectF(-120_000, -90_000, 262_144, 262_144),
			QRectF(0, 0, 640, 480),
			12,
		)
	)
	assert 1 <= len(cells) < 3_000
	assert all(cell.intersects(QRectF(0, 0, 640, 480)) for _row, _col, cell in cells)


def test_cancelled_selection_move_restores_pixels_and_history(app):
	pane = ImageStudioPane()
	document = pane.document
	document.draw_dab(QPoint(2, 2), QColor("#ff0044"))
	pane.canvas.selection_rect = QRect(2, 2, 1, 1)
	document.checkpoint()
	pane.canvas._selection_source_rect = QRect(pane.canvas.selection_rect)
	pane.canvas._moving_selection = document.current_image().copy(pane.canvas.selection_rect)
	painter = QPainter(document.current_image())
	painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
	painter.fillRect(pane.canvas.selection_rect, Qt.GlobalColor.transparent)
	painter.end()
	pane.canvas._cancel_selection_move()
	assert document.current_image().pixelColor(2, 2).name() == "#ff0044"
	assert not document.modified
	assert not document.can_undo
	pane.close()


def test_undo_preserves_canvas_view_and_clean_state(app):
	pane = ImageStudioPane()
	pane.document.checkpoint()
	pane.document.draw_dab(QPoint(1, 1), QColor("#ffffff"))
	pane.canvas.zoom = 7.5
	pane.canvas.pan = QPointF(19, -11)
	pane.undo()
	assert pane.canvas.zoom == 7.5
	assert pane.canvas.pan == QPointF(19, -11)
	assert not pane.document.modified
	pane.close()


def test_saved_revision_stays_clean_only_at_the_exact_saved_history_state():
	document = ImageStudioDocument(ImageProject.blank(8, 8))
	document.checkpoint()
	document.draw_dab(QPoint(1, 1), QColor("#ffffff"))
	document.modified = False
	assert not document.modified

	document.checkpoint()
	document.draw_dab(QPoint(2, 2), QColor("#ff0044"))
	assert document.modified
	assert document.undo()
	assert not document.modified
	assert document.redo()
	assert document.modified

	document.modified = False
	assert document.undo()
	assert document.modified
	assert document.redo()
	assert not document.modified


def test_long_frame_duration_keeps_fractional_fps_and_blank_layer_name_repairs(app):
	pane = ImageStudioPane()
	pane.duration_spin.setValue(2_000)
	pane._set_frame_duration()
	assert pane.fps_spin.value() == pytest.approx(0.5)
	pane.fps_spin.setValue(0.5)
	pane._set_frame_fps()
	assert pane.document.project.frame_durations_ms[0] == 2_000
	item = pane.layer_list.item(0)
	item.setText("")
	assert item.text() == "Layer 1"
	item.setText("x" * 300)
	assert len(item.text()) == 256
	pane.close()


def test_layer_edits_refresh_dirty_marker_and_noop_opacity_press_stays_clean(app):
	pane = ImageStudioPane()
	assert "*" not in pane.document_label.text()
	pane._begin_layer_opacity()
	pane._end_layer_opacity()
	assert not pane.document.modified
	assert not pane.document.can_undo
	pane.opacity_slider.setSliderDown(True)
	pane._begin_layer_opacity()
	pane.opacity_slider.setValue(50)
	pane.opacity_slider.setValue(100)
	pane.opacity_slider.setSliderDown(False)
	pane._end_layer_opacity()
	assert not pane.document.modified
	assert not pane.document.can_undo

	pane.opacity_slider.setSliderDown(True)
	pane._begin_layer_opacity()
	pane.opacity_slider.setValue(50)
	pane.opacity_slider.setSliderDown(False)
	pane._end_layer_opacity()
	assert pane.document.modified
	assert pane.document.can_undo
	assert "*" in pane.document_label.text()

	pane.document.modified = False
	pane._refresh_document_label()
	item = pane.layer_list.item(0)
	item.setText("Renamed")
	assert pane.document.modified
	assert "*" in pane.document_label.text()
	pane.close()


def test_undo_history_is_bounded_by_memory_budget():
	document = ImageStudioDocument(
		ImageProject.blank(64, 64),
		undo_limit=500,
		undo_byte_limit=4 * 1024 * 1024,
	)
	for _ in range(300):
		document.checkpoint()
	assert document._history_bytes(document._undo) <= document.undo_byte_limit
	assert len(document._undo) < 300


def _process_until(app, predicate, timeout: float = 2.0) -> None:
	deadline = time.monotonic() + timeout
	while not predicate() and time.monotonic() < deadline:
		app.processEvents()
		time.sleep(0.002)
	assert predicate()


def test_image_studio_export_is_backgrounded_and_uses_an_immutable_snapshot(
	app,
	tmp_path,
	monkeypatch,
):
	started = threading.Event()
	release = threading.Event()
	main_thread = threading.get_ident()

	class BlockingCodec:
		def __init__(self):
			self.thread_id = 0
			self.exported_project = None

		def import_file(self, _path):
			raise AssertionError("not used")

		def export_file(self, project, _path, _kind):
			self.thread_id = threading.get_ident()
			started.set()
			assert release.wait(2)
			self.exported_project = project

	codec = BlockingCodec()
	pane = ImageStudioPane(codec=codec)
	output = tmp_path / "snapshot.xip"
	monkeypatch.setattr(
		QFileDialog,
		"getSaveFileName",
		lambda *_args, **_kwargs: (str(output), IMAGE_EXPORTS[4].dialog_filter),
	)
	messages = []
	monkeypatch.setattr(
		QMessageBox,
		"information",
		lambda *_args: messages.append(_args[2]),
	)

	pane.export_file()
	assert started.wait(1)
	assert pane.codec_busy
	assert codec.thread_id != main_thread
	assert all(not button.isEnabled() for button in pane._file_buttons)

	pane.document.checkpoint()
	pane.document.draw_dab(QPoint(1, 1), QColor("#ff0044"))
	release.set()
	_process_until(app, lambda: not pane.codec_busy)

	assert codec.exported_project is not pane.document.project
	assert codec.exported_project.layers[0].frames[0].pixelColor(1, 1).alpha() == 0
	assert pane.document.current_image().pixelColor(1, 1).name() == "#ff0044"
	assert pane.document.modified
	assert pane.current_path is None
	assert all(button.isEnabled() for button in pane._file_buttons)
	assert messages and "newer edits" in messages[-1]
	pane.close()


def test_image_studio_import_decodes_off_thread_and_replaces_on_gui_thread(
	app,
	tmp_path,
	monkeypatch,
):
	started = threading.Event()
	release = threading.Event()
	main_thread = threading.get_ident()
	callback_thread = []

	class BlockingCodec:
		def __init__(self):
			self.thread_id = 0

		def import_file(self, _path):
			self.thread_id = threading.get_ident()
			started.set()
			assert release.wait(2)
			project = ImageProject.blank(3, 2, "Imported")
			project.layers[0].frames[0].setPixelColor(0, 0, QColor("#00ff88"))
			return project

		def export_file(self, _project, _path, _kind):
			raise AssertionError("not used")

	codec = BlockingCodec()
	pane = ImageStudioPane(codec=codec)
	input_path = tmp_path / "source.png"
	monkeypatch.setattr(
		QFileDialog,
		"getOpenFileName",
		lambda *_args, **_kwargs: (str(input_path), "Images (*.png)"),
	)
	original_completed = pane._import_completed

	def completed(*args):
		callback_thread.append(threading.get_ident())
		return original_completed(*args)

	pane._import_completed = completed
	pane.import_file()
	assert started.wait(1)
	assert pane.codec_busy
	assert codec.thread_id != main_thread
	release.set()
	_process_until(app, lambda: not pane.codec_busy)

	assert callback_thread == [main_thread]
	assert pane.document.project.name == "Imported"
	assert pane.document.project.width == 3
	assert pane.document.current_image().pixelColor(0, 0).name() == "#00ff88"
	assert pane.current_path == input_path
	pane.close()


def test_image_studio_codec_errors_and_shutdown_stay_guarded_on_gui_thread(
	app,
	tmp_path,
	monkeypatch,
):
	main_thread = threading.get_ident()
	warnings = []

	class FailingCodec:
		def import_file(self, _path):
			raise RuntimeError("decode failed")

		def export_file(self, _project, _path, _kind):
			raise AssertionError("not used")

	pane = ImageStudioPane(codec=FailingCodec())
	monkeypatch.setattr(
		QFileDialog,
		"getOpenFileName",
		lambda *_args, **_kwargs: (str(tmp_path / "bad.png"), "Images (*.png)"),
	)
	monkeypatch.setattr(
		QMessageBox,
		"warning",
		lambda *_args: warnings.append((threading.get_ident(), _args[2])),
	)
	pane.import_file()
	_process_until(app, lambda: not pane.codec_busy)
	assert warnings == [(main_thread, "decode failed")]
	assert pane.shutdown()
	pane.close()

	started = threading.Event()
	release = threading.Event()
	interrupted = []

	class SlowCodec:
		def import_file(self, _path):
			raise AssertionError("not used")

		def export_file(self, _project, _path, _kind):
			started.set()
			assert release.wait(2)
			from PyQt6.QtCore import QThread

			interrupted.append(QThread.currentThread().isInterruptionRequested())

	slow_pane = ImageStudioPane(codec=SlowCodec())
	monkeypatch.setattr(
		QFileDialog,
		"getSaveFileName",
		lambda *_args, **_kwargs: (
			str(tmp_path / "slow.png"),
			IMAGE_EXPORTS[0].dialog_filter,
		),
	)
	monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
	slow_pane.export_file()
	assert started.wait(1)
	assert not slow_pane.shutdown(0)
	release.set()
	_process_until(app, lambda: not slow_pane.codec_busy)
	assert interrupted == [True]
	assert slow_pane.shutdown()
	slow_pane.close()


def test_image_studio_successful_shutdown_suppresses_queued_completion(
	app,
	tmp_path,
	monkeypatch,
):
	started = threading.Event()
	messages = []

	class InterruptibleCodec:
		def import_file(self, _path):
			raise AssertionError("not used")

		def export_file(self, _project, _path, _kind):
			from PyQt6.QtCore import QThread

			started.set()
			while not QThread.currentThread().isInterruptionRequested():
				time.sleep(0.001)

	pane = ImageStudioPane(codec=InterruptibleCodec())
	monkeypatch.setattr(
		QFileDialog,
		"getSaveFileName",
		lambda *_args, **_kwargs: (
			str(tmp_path / "cancelled.png"),
			IMAGE_EXPORTS[0].dialog_filter,
		),
	)
	monkeypatch.setattr(
		QMessageBox,
		"information",
		lambda *_args: messages.append(_args[2]),
	)
	pane.export_file()
	assert started.wait(1)
	assert pane.shutdown(1_000)
	app.processEvents()
	assert not pane.codec_busy
	assert all(button.isEnabled() for button in pane._file_buttons)
	assert messages == []
	pane.close()
