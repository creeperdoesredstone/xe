import os
from array import array

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QApplication

from ide import VMGraphicsWidget, X26IDE, _ToneStream
from xe_lang.devices.assets import AudioDevice, AudioState, AudioVoice
from xe_lang.devices.filesystem import FileSystemDevice
from xe_lang.media.music_format import NoteEvent, Track
from xe_lang.vm import MAGIC, VERSION, VM


def _track() -> Track:
	return Track(120, 480, (NoteEvent(0, 480, 69, 127, 0),))


def test_audio_device_emits_note_volume_and_stop_states(tmp_path):
	states: list[AudioState] = []
	volume = [80]
	device = AudioDevice(
		FileSystemDevice(tmp_path),
		states.append,
		lambda: volume[0],
	)
	handle = device.register("tone.xmusic", _track())

	assert device.play(handle)
	assert states[-1] == AudioState((AudioVoice(69, 127, 0),), 80, True)

	volume[0] = 50
	assert device.update(handle, 10)
	assert states[-1].volume == 50
	assert states[-1].voices == (AudioVoice(69, 127, 0),)

	assert device.seek(handle, 240)
	assert states[-1].playing
	assert device.stop(handle)
	assert states[-1] == AudioState((), 50, False)
	assert not device.update(999, 10)


def test_vm_termination_always_silences_native_audio(tmp_path):
	states: list[AudioState] = []
	vm = VM(
		[MAGIC, VERSION, 0, 0],
		filesystem_root=tmp_path,
		audio_handler=states.append,
	)
	handle = vm.devices.audio.register("tone.xmusic", _track())
	assert vm.devices.audio.play(handle)
	assert states[-1].playing

	result = vm.run()

	assert result.error is None
	assert states[-1].voices == ()
	assert not states[-1].playing


def test_tone_stream_generates_stereo_pcm_without_audio_hardware():
	stream = _ToneStream(48_000)
	assert stream.readData(4_096) == bytes(4_096)

	stream.set_state(AudioState((AudioVoice(69, 127, 0),), 100, True))
	pcm = stream.readData(4_096)
	samples = array("h")
	samples.frombytes(pcm)

	assert len(pcm) == 4_096
	assert any(samples)
	assert all(samples[index] == samples[index + 1] for index in range(0, len(samples), 2))

	stream.silence()
	assert stream.readData(4_096) == bytes(4_096)


def test_execution_banner_does_not_join_partial_program_output():
	app = QApplication.instance() or QApplication([])
	window = X26IDE()
	window.append_output("partial")

	window.handle_execution_finished([], None, "")

	assert "partial\nExecution finished successfully." in window.output.toPlainText()
	window.close()
	app.processEvents()


class _PointerEvent:
	def __init__(self, x: float, y: float):
		self._position = QPointF(x, y)

	def position(self) -> QPointF:
		return self._position


def test_graphics_stage_preserves_fractional_fit_when_shrunk_and_restored():
	app = QApplication.instance() or QApplication([])
	stage = VMGraphicsWidget()

	for width, height, scale in ((240, 180, 0.5), (120, 90, 0.25), (960, 720, 2.0)):
		stage.resize(width, height)
		stage._fit_stage()
		assert stage.scale == scale
		assert (stage.render_width, stage.render_height) == (width, height)
		assert stage._pointer_position(_PointerEvent(width, height)) == (479, 359)

	stage.resize(240, 180)
	stage._fit_stage()
	assert (stage.render_width, stage.render_height) == (240, 180)
	app.processEvents()
