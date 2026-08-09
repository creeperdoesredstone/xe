import os
import json
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import QApplication

from xe_lang.host_tools.image_document import (
	ImageProject,
	ImageStudioDocument,
	QtImageProjectCodec,
)
from xe_lang.host_tools.image_studio import ImageStudioPane


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
