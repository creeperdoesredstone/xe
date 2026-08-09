from __future__ import annotations

import json

from xe_lang.devices import OSDevice, OSSettings


def test_host_os_preferences_round_trip_atomically(tmp_path) -> None:
	settings_path = tmp_path / "XenonOS" / "settings.json"
	device = OSDevice(settings_path=settings_path)

	assert device.apply_preferences(83, 64, 51, 2, 4, 1, 35, 2, 0, 0, False)
	assert settings_path.is_file()
	assert not tuple(settings_path.parent.glob("*.tmp"))

	reloaded = OSDevice(settings_path=settings_path)
	assert reloaded.settings == OSSettings(83, 2, 4, 64, 51, 1, 35, 2, 0, 0, False)
	payload = json.loads(settings_path.read_text(encoding="utf-8"))
	assert payload["version"] == 1
	assert list(payload) == sorted(payload)


def test_each_public_preference_setter_persists(tmp_path) -> None:
	settings_path = tmp_path / "settings.json"
	device = OSDevice(settings_path=settings_path)
	assert device.set_volume(91)
	assert device.set_background(1)
	assert device.set_palette(5)
	assert device.set_music_volume(62)
	assert device.set_sound_effect_volume(43)
	assert device.set_window_transparency(24)
	assert device.set_window_corner_style(1)
	assert device.set_icon_size(2)
	assert device.set_clock_format(0)
	assert device.set_settings_enabled(False)

	reloaded = OSDevice(settings_path=settings_path)
	assert reloaded.settings == device.settings


def test_corrupt_or_unversioned_preferences_fall_back_safely(tmp_path) -> None:
	settings_path = tmp_path / "settings.json"
	settings_path.write_text("{not-json", encoding="utf-8")
	assert OSDevice(settings_path=settings_path).settings == OSSettings()

	settings_path.write_text('{"volume":1}', encoding="utf-8")
	assert OSDevice(settings_path=settings_path).settings == OSSettings()
