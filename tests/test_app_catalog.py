from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

import pytest

from xe_lang.app_catalog import (
	AppCatalogError,
	check_app_artifacts,
	compile_catalog,
	load_app_catalog,
	validate_app_catalog,
	write_app_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_accounts_for_every_app_and_declares_safe_extension_points() -> None:
	catalog = load_app_catalog(ROOT)
	assert {item.entry for item in catalog.apps} == {
		path.relative_to(ROOT).as_posix() for path in (ROOT / "apps").glob("*.xe")
	}
	assert catalog.asset_formats["portable_image_animation"] == ".ximg"
	assert catalog.asset_formats["portable_music"] == ".xmusic"
	assert catalog.asset_formats["scratch_animation"] == ".sprite3"
	assert all(item.asset_namespace.startswith("SystemAssets/") for item in catalog.apps)


def test_every_catalog_app_compiles_deterministically() -> None:
	catalog = load_app_catalog(ROOT)
	first = compile_catalog(catalog, ROOT)
	second = compile_catalog(catalog, ROOT)
	assert [item.artifact.artifact_hash for item in first] == [
		item.artifact.artifact_hash for item in second
	]
	assert all(item.assembly_bytes.endswith(b"\n") for item in first)
	assert all(item.bytecode_bytes.startswith(b"0x") for item in first)


def test_source_metadata_headers_match_the_catalog() -> None:
	catalog = load_app_catalog(ROOT)
	for spec in catalog.apps:
		source = (ROOT / spec.entry).read_text(encoding="utf-8")
		title = re.search(r'^const APP_TITLE = "([^"]*)"$', source, re.MULTILINE)
		width = re.search(r"^const APP_DEFAULT_WIDTH = (\d+)$", source, re.MULTILINE)
		height = re.search(r"^const APP_DEFAULT_HEIGHT = (\d+)$", source, re.MULTILINE)
		assert title is not None, spec.entry
		assert width is not None, spec.entry
		assert height is not None, spec.entry
		assert title.group(1) == spec.title
		assert int(width.group(1)) == spec.default_width
		assert int(height.group(1)) == spec.default_height
		for asset in spec.assets:
			assert asset.path in source, f"{spec.entry} does not reference {asset.path}"


def test_catalog_rejects_duplicate_ids_and_unsafe_asset_paths() -> None:
	catalog = load_app_catalog(ROOT)
	duplicate = replace(catalog, apps=catalog.apps + (catalog.apps[0],))
	with pytest.raises(AppCatalogError, match="Duplicate app id"):
		validate_app_catalog(duplicate, ROOT)
	asset = catalog.app("xenon_music").assets[0]
	bad_app = replace(
		catalog.app("xenon_music"),
		assets=(replace(asset, path="../outside.xmusic"),),
	)
	bad_catalog = replace(
		catalog,
		apps=tuple(bad_app if item.id == bad_app.id else item for item in catalog.apps),
	)
	with pytest.raises(AppCatalogError, match="Duplicate|asset|path|outside|portable"):
		validate_app_catalog(bad_catalog, ROOT)


def test_build_writes_and_checks_all_artifacts_in_an_isolated_repository(tmp_path: Path) -> None:
	(tmp_path / "apps").mkdir()
	(tmp_path / "apps" / "manifest.json").write_bytes((ROOT / "apps" / "manifest.json").read_bytes())
	for source in (ROOT / "apps").glob("*.xe"):
		(tmp_path / "apps" / source.name).write_bytes(source.read_bytes())
	catalog = load_app_catalog(tmp_path)
	assert all(item.status == "missing" for item in check_app_artifacts(catalog, tmp_path))
	changed = write_app_artifacts(catalog, tmp_path)
	assert len(changed) == len(catalog.apps) * 2
	assert all(item.status == "current" for item in check_app_artifacts(catalog, tmp_path))
	assert write_app_artifacts(catalog, tmp_path) == ()


def test_checked_in_app_artifacts_match_current_sources() -> None:
	catalog = load_app_catalog(ROOT)
	stale = [
		f"{item.status}: {item.path}"
		for item in check_app_artifacts(catalog, ROOT)
		if item.status != "current"
	]
	assert stale == [], "Run `python tools/build_apps.py`:\n" + "\n".join(stale)
