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
	assert reloaded.settings == OSSettings(83, 2, 4, 64, 51, 1, 0, 1, 0, 0, False)
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
	assert device.window_transparency == 0
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


def test_appearance_preview_is_effective_atomic_and_never_persisted(tmp_path) -> None:
	settings_path = tmp_path / "settings.json"
	device = OSDevice(settings_path=settings_path)
	committed = device.settings

	assert device.preview_preferences(2, 4, 0, 2)
	assert device.preview_active
	assert device.background_id == 2
	assert device.palette_id == 1
	assert device.theme_mode == 0
	assert device.window_corner_style == 1
	assert device.settings == committed
	assert not settings_path.exists()

	device.clear_preview()
	assert not device.preview_active
	assert device.effective_settings == committed
	assert not device.preview_preferences(99, 0, 0, 0)
	assert not device.preview_active
	assert device.effective_settings == committed

	assert device.preview_preferences(1, 3, 1, 1)
	assert device.apply_preferences(70, 70, 70, 1, 3, 1, 0, 1, 1, 1, True)
	assert not device.preview_active
	assert device.effective_settings == device.settings

	assert device.preview_preferences(2, 4, 1, 1)
	assert not device.apply_preferences(70, 70, 70, 99, 4, 1, 0, 1, 1, 1, True)
	assert not device.preview_active
	assert device.effective_settings == device.settings


def test_legacy_soft_corner_value_migrates_to_rounded(tmp_path) -> None:
	settings_path = tmp_path / "settings.json"
	settings_path.write_text(
		'{"version":1,"theme_mode":0,"palette_id":0,"window_corner_style":2}',
		encoding="utf-8",
	)
	device = OSDevice(settings_path=settings_path)
	assert device.settings.window_corner_style == 1
	assert device.set_window_corner_style(2)
	assert device.settings.window_corner_style == 1
