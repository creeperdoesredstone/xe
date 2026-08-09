from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Iterable

from xe_lang.compiler_service import CompileArtifact, compile_source


CATALOG_SCHEMA_VERSION = 1
APP_SOURCE_SUFFIX = ".xe"
ASSEMBLY_SUFFIX = ".xas"
BYTECODE_SUFFIX = ".xbn"


class AppCatalogError(ValueError):
	pass


@dataclass(frozen=True)
class AssetSpec:
	id: str
	kind: str
	path: str
	source: str


@dataclass(frozen=True)
class ArtifactSpec:
	assembly: str
	bytecode: str


@dataclass(frozen=True)
class AppSpec:
	id: str
	title: str
	category: str
	status: str
	entry: str
	default_width: int
	default_height: int
	asset_namespace: str
	assets: tuple[AssetSpec, ...]
	artifacts: ArtifactSpec


@dataclass(frozen=True)
class AppCatalog:
	schema_version: int
	stage_width: int
	stage_height: int
	asset_formats: dict[str, str]
	apps: tuple[AppSpec, ...]

	def app(self, app_id: str) -> AppSpec:
		for spec in self.apps:
			if spec.id == app_id:
				return spec
		raise KeyError(app_id)


@dataclass(frozen=True)
class BuiltApp:
	spec: AppSpec
	artifact: CompileArtifact
	assembly_bytes: bytes
	bytecode_bytes: bytes


@dataclass(frozen=True)
class ArtifactCheck:
	path: str
	status: str


def repository_root() -> Path:
	return Path(__file__).resolve().parents[1]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
	value: dict[str, Any] = {}
	for key, item in pairs:
		if key in value:
			raise AppCatalogError(f"Duplicate manifest key: {key}")
		value[key] = item
	return value


def _mapping(value: Any, context: str) -> dict[str, Any]:
	if not isinstance(value, dict):
		raise AppCatalogError(f"{context} must be an object")
	return value


def _sequence(value: Any, context: str) -> list[Any]:
	if not isinstance(value, list):
		raise AppCatalogError(f"{context} must be an array")
	return value


def _string(value: Any, context: str) -> str:
	if not isinstance(value, str) or not value.strip():
		raise AppCatalogError(f"{context} must be a non-empty string")
	return value


def _integer(value: Any, context: str) -> int:
	if isinstance(value, bool) or not isinstance(value, int):
		raise AppCatalogError(f"{context} must be an integer")
	return value


def _portable_path(value: Any, context: str) -> str:
	text = _string(value, context)
	if "\\" in text:
		raise AppCatalogError(f"{context} must use portable '/' separators")
	if any(ord(character) < 32 for character in text) or ":" in text:
		raise AppCatalogError(f"{context} contains a non-portable character")
	path = PurePosixPath(text)
	if path.is_absolute() or text.startswith("//") or ".." in path.parts or str(path) in {"", "."}:
		raise AppCatalogError(f"{context} is not a confined portable path: {text!r}")
	return str(path)


def _parse_asset(raw: Any, context: str, formats: dict[str, str]) -> AssetSpec:
	data = _mapping(raw, context)
	kind = _string(data.get("kind"), f"{context}.kind")
	if kind not in formats:
		raise AppCatalogError(f"{context}.kind is not declared in asset_formats: {kind}")
	path = _portable_path(data.get("path"), f"{context}.path")
	if PurePosixPath(path).suffix.casefold() != formats[kind].casefold():
		raise AppCatalogError(f"{context}.path must end in {formats[kind]}")
	return AssetSpec(
		_string(data.get("id"), f"{context}.id"),
		kind,
		path,
		_string(data.get("source"), f"{context}.source"),
	)


def _parse_app(raw: Any, index: int, formats: dict[str, str]) -> AppSpec:
	context = f"apps[{index}]"
	data = _mapping(raw, context)
	window = _sequence(data.get("default_window"), f"{context}.default_window")
	if len(window) != 2:
		raise AppCatalogError(f"{context}.default_window must contain width and height")
	artifacts = _mapping(data.get("artifacts"), f"{context}.artifacts")
	assets = tuple(
		_parse_asset(item, f"{context}.assets[{asset_index}]", formats)
		for asset_index, item in enumerate(_sequence(data.get("assets"), f"{context}.assets"))
	)
	return AppSpec(
		id=_string(data.get("id"), f"{context}.id"),
		title=_string(data.get("title"), f"{context}.title"),
		category=_string(data.get("category"), f"{context}.category"),
		status=_string(data.get("status"), f"{context}.status"),
		entry=_portable_path(data.get("entry"), f"{context}.entry"),
		default_width=_integer(window[0], f"{context}.default_window[0]"),
		default_height=_integer(window[1], f"{context}.default_window[1]"),
		asset_namespace=_portable_path(data.get("asset_namespace"), f"{context}.asset_namespace"),
		assets=assets,
		artifacts=ArtifactSpec(
			_portable_path(artifacts.get("assembly"), f"{context}.artifacts.assembly"),
			_portable_path(artifacts.get("bytecode"), f"{context}.artifacts.bytecode"),
		),
	)


def load_app_catalog(root: Path | str | None = None) -> AppCatalog:
	repo = Path(root).resolve() if root is not None else repository_root()
	manifest_path = repo / "apps" / "manifest.json"
	try:
		raw = json.loads(
			manifest_path.read_text(encoding="utf-8"),
			object_pairs_hook=_reject_duplicate_keys,
			parse_constant=lambda value: (_ for _ in ()).throw(
				AppCatalogError(f"Non-finite JSON value is not permitted: {value}")
			),
		)
	except (OSError, json.JSONDecodeError) as error:
		raise AppCatalogError(f"Cannot load app manifest: {error}") from error
	data = _mapping(raw, "manifest")
	version = _integer(data.get("schema_version"), "schema_version")
	if version != CATALOG_SCHEMA_VERSION:
		raise AppCatalogError(f"Unsupported app manifest schema {version}")
	stage = _mapping(data.get("stage"), "stage")
	formats_raw = _mapping(data.get("asset_formats"), "asset_formats")
	formats = {
		_string(key, "asset_formats key"): _string(value, f"asset_formats.{key}")
		for key, value in formats_raw.items()
	}
	for name, extension in formats.items():
		if not extension.startswith(".") or "/" in extension or "\\" in extension:
			raise AppCatalogError(f"asset_formats.{name} must be one file extension")
	catalog = AppCatalog(
		schema_version=version,
		stage_width=_integer(stage.get("width"), "stage.width"),
		stage_height=_integer(stage.get("height"), "stage.height"),
		asset_formats=formats,
		apps=tuple(
			_parse_app(item, index, formats)
			for index, item in enumerate(_sequence(data.get("apps"), "apps"))
		),
	)
	validate_app_catalog(catalog, repo)
	return catalog


def validate_app_catalog(catalog: AppCatalog, repo: Path | str) -> None:
	root = Path(repo).resolve()
	if catalog.stage_width <= 0 or catalog.stage_height <= 0:
		raise AppCatalogError("Stage dimensions must be positive")
	ids: set[str] = set()
	entries: set[str] = set()
	artifact_paths: set[str] = set()
	asset_paths: set[str] = set()
	asset_paths_folded: set[str] = set()
	for spec in catalog.apps:
		_portable_path(spec.entry, f"{spec.id}.entry")
		_portable_path(spec.asset_namespace, f"{spec.id}.asset_namespace")
		if re.fullmatch(r"[a-z][a-z0-9_]*", spec.id) is None:
			raise AppCatalogError(f"Invalid app id: {spec.id}")
		if spec.id in ids:
			raise AppCatalogError(f"Duplicate app id: {spec.id}")
		ids.add(spec.id)
		if spec.status not in {"current", "legacy"}:
			raise AppCatalogError(f"Invalid app status for {spec.id}: {spec.status}")
		if spec.entry in entries:
			raise AppCatalogError(f"Duplicate app entry: {spec.entry}")
		entries.add(spec.entry)
		if not spec.entry.startswith("apps/") or PurePosixPath(spec.entry).suffix.casefold() != APP_SOURCE_SUFFIX:
			raise AppCatalogError(f"App entry must be an apps/*.xe source: {spec.entry}")
		if PurePosixPath(spec.entry).stem != spec.id:
			raise AppCatalogError(f"App entry stem must match its id: {spec.id}")
		if not (root / spec.entry).is_file():
			raise AppCatalogError(f"App source does not exist: {spec.entry}")
		if not 1 <= spec.default_width <= catalog.stage_width or not 1 <= spec.default_height <= catalog.stage_height:
			raise AppCatalogError(f"Default window for {spec.id} is outside the stage")
		for path, suffix, directory in (
			(spec.artifacts.assembly, ASSEMBLY_SUFFIX, "asm/"),
			(spec.artifacts.bytecode, BYTECODE_SUFFIX, "exe/"),
		):
			_portable_path(path, f"{spec.id}.artifact")
			if not path.startswith(directory) or PurePosixPath(path).suffix.casefold() != suffix:
				raise AppCatalogError(f"Invalid artifact path for {spec.id}: {path}")
			if PurePosixPath(path).stem != spec.id:
				raise AppCatalogError(f"Artifact stem must match its app id: {path}")
			if path in artifact_paths:
				raise AppCatalogError(f"Duplicate artifact path: {path}")
			artifact_paths.add(path)
		asset_ids: set[str] = set()
		for asset in spec.assets:
			_portable_path(asset.path, f"{spec.id}.{asset.id}.path")
			if asset.id in asset_ids:
				raise AppCatalogError(f"Duplicate asset id in {spec.id}: {asset.id}")
			asset_ids.add(asset.id)
			if asset.source not in {"bundled", "generated", "external"}:
				raise AppCatalogError(f"Invalid asset source for {spec.id}.{asset.id}: {asset.source}")
			if not asset.path.startswith(spec.asset_namespace.rstrip("/") + "/"):
				raise AppCatalogError(f"Asset is outside {spec.id}'s namespace: {asset.path}")
			if asset.kind not in catalog.asset_formats:
				raise AppCatalogError(f"Unknown asset kind for {spec.id}.{asset.id}: {asset.kind}")
			if PurePosixPath(asset.path).suffix.casefold() != catalog.asset_formats[asset.kind].casefold():
				raise AppCatalogError(f"Asset extension does not match its kind: {asset.path}")
			if asset.path in asset_paths:
				raise AppCatalogError(f"Duplicate asset path: {asset.path}")
			asset_paths.add(asset.path)
			folded_path = asset.path.casefold()
			if folded_path in asset_paths_folded:
				raise AppCatalogError(f"Case-colliding asset path: {asset.path}")
			asset_paths_folded.add(folded_path)
	declared = entries
	actual = {
		path.relative_to(root).as_posix()
		for path in (root / "apps").glob(f"*{APP_SOURCE_SUFFIX}")
	}
	if declared != actual:
		missing = sorted(actual - declared)
		extra = sorted(declared - actual)
		raise AppCatalogError(f"Manifest/source mismatch; missing={missing}, extra={extra}")


def _bytecode_text(program: Iterable[int]) -> str:
	return "".join(f"0x{int(word) & 0xFFFFFFFF:09X}\n" for word in program)


def compile_catalog(catalog: AppCatalog, repo: Path | str) -> tuple[BuiltApp, ...]:
	root = Path(repo).resolve()
	built: list[BuiltApp] = []
	for spec in catalog.apps:
		try:
			source = (root / spec.entry).read_text(encoding="utf-8")
		except (OSError, UnicodeError) as error:
			raise AppCatalogError(f"Cannot read {spec.entry}: {error}") from error
		artifact = compile_source(source, spec.entry)
		if not artifact.success:
			details = "; ".join(str(item) for item in artifact.diagnostics)
			raise AppCatalogError(f"Cannot compile {spec.id}: {details}")
		assembly = (artifact.assembly.rstrip("\n") + "\n").encode("utf-8")
		bytecode = _bytecode_text(artifact.program).encode("ascii")
		built.append(BuiltApp(spec, artifact, assembly, bytecode))
	return tuple(built)


def expected_artifacts(catalog: AppCatalog, repo: Path | str) -> dict[str, bytes]:
	values: dict[str, bytes] = {}
	for built in compile_catalog(catalog, repo):
		values[built.spec.artifacts.assembly] = built.assembly_bytes
		values[built.spec.artifacts.bytecode] = built.bytecode_bytes
	return values


def check_app_artifacts(catalog: AppCatalog, repo: Path | str) -> tuple[ArtifactCheck, ...]:
	root = Path(repo).resolve()
	checks: list[ArtifactCheck] = []
	for relative, expected in expected_artifacts(catalog, root).items():
		path = root / relative
		if not path.exists():
			status = "missing"
		else:
			try:
				status = "current" if path.read_bytes() == expected else "stale"
			except OSError:
				status = "unreadable"
		checks.append(ArtifactCheck(relative, status))
	return tuple(checks)


def write_app_artifacts(catalog: AppCatalog, repo: Path | str) -> tuple[str, ...]:
	root = Path(repo).resolve()
	changed: list[str] = []
	for relative, expected in expected_artifacts(catalog, root).items():
		path = root / relative
		if path.exists():
			try:
				if path.read_bytes() == expected:
					continue
			except OSError as error:
				raise AppCatalogError(f"Cannot read {relative}: {error}") from error
		path.parent.mkdir(parents=True, exist_ok=True)
		temporary_name = ""
		try:
			with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as temporary:
				temporary.write(expected)
				temporary.flush()
				os.fsync(temporary.fileno())
				temporary_name = temporary.name
			os.replace(temporary_name, path)
		except OSError as error:
			if temporary_name:
				try:
					Path(temporary_name).unlink(missing_ok=True)
				except OSError:
					pass
			raise AppCatalogError(f"Cannot write {relative}: {error}") from error
		changed.append(relative)
	return tuple(changed)
