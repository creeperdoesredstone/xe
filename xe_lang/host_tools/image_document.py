"""Layered raster document and codec boundary used by Image Studio."""

from __future__ import annotations

from dataclasses import dataclass, field

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Literal, Protocol, runtime_checkable
import zipfile

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QImage, QImageReader, QPainter, QPen


class ImageStudioError(RuntimeError):
	pass


def _blank_image(width: int, height: int) -> QImage:
	image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
	image.fill(Qt.GlobalColor.transparent)
	return image


def xvm_palette() -> tuple[QColor, ...]:
	from xe_lang.devices import DEFAULT_PALETTE
	return tuple(QColor(value) for value in DEFAULT_PALETTE)


def xvm_palette_index(color: QColor, palette: tuple[QColor, ...] | None = None) -> int:
	if color.alpha() < 128:
		return 16
	colors = palette or xvm_palette()
	return min(
		range(16),
		key=lambda index: (
			(color.red() - colors[index].red()) ** 2
			+ (color.green() - colors[index].green()) ** 2
			+ (color.blue() - colors[index].blue()) ** 2
		),
	)


def quantize_xvm_image(image: QImage) -> QImage:
	palette = xvm_palette()
	result = _blank_image(image.width(), image.height())
	for y in range(image.height()):
		for x in range(image.width()):
			index = xvm_palette_index(image.pixelColor(x, y), palette)
			result.setPixelColor(x, y, QColor(0, 0, 0, 0) if index == 16 else palette[index])
	return result


@dataclass(slots=True)
class RasterLayer:
	name: str
	frames: list[QImage]
	visible: bool = True
	opacity: float = 1.0

	def clone(self) -> "RasterLayer":
		return RasterLayer(
			name=self.name,
			frames=[frame.copy() for frame in self.frames],
			visible=self.visible,
			opacity=self.opacity,
		)


@dataclass(slots=True)
class ImageProject:
	width: int
	height: int
	layers: list[RasterLayer]
	frame_durations_ms: list[int]
	current_layer: int = 0
	current_frame: int = 0
	name: str = "Untitled"
	_composite_cache: dict[int, QImage] = field(default_factory=dict, repr=False)

	@classmethod
	def blank(cls, width: int = 64, height: int = 64, name: str = "Untitled") -> "ImageProject":
		if width <= 0 or height <= 0 or width > 4096 or height > 4096:
			raise ImageStudioError("Canvas dimensions must be between 1 and 4096 pixels.")
		return cls(
			width=width,
			height=height,
			layers=[RasterLayer("Layer 1", [_blank_image(width, height)])],
			frame_durations_ms=[100],
			name=name,
		)

	@property
	def frame_count(self) -> int:
		return len(self.frame_durations_ms)

	def clone(self) -> "ImageProject":
		return ImageProject(
			width=self.width,
			height=self.height,
			layers=[layer.clone() for layer in self.layers],
			frame_durations_ms=list(self.frame_durations_ms),
			current_layer=self.current_layer,
			current_frame=self.current_frame,
			name=self.name,
		)

	def normalize(self) -> None:
		if not self.layers:
			self.layers.append(
				RasterLayer("Layer 1", [_blank_image(self.width, self.height) for _ in range(self.frame_count or 1)])
			)
		if not self.frame_durations_ms:
			self.frame_durations_ms.append(100)
		for layer in self.layers:
			while len(layer.frames) < self.frame_count:
				layer.frames.append(_blank_image(self.width, self.height))
			if len(layer.frames) > self.frame_count:
				del layer.frames[self.frame_count :]
		self.current_layer = min(max(self.current_layer, 0), len(self.layers) - 1)
		self.current_frame = min(max(self.current_frame, 0), self.frame_count - 1)
		self.invalidate()

	def invalidate(self) -> None:
		self._composite_cache.clear()

	def composite(self, frame_index: int | None = None) -> QImage:
		index = self.current_frame if frame_index is None else frame_index
		if not 0 <= index < self.frame_count:
			raise ImageStudioError("Frame index is out of range.")
		cached = self._composite_cache.get(index)
		if cached is not None:
			return cached
		result = _blank_image(self.width, self.height)
		painter = QPainter(result)
		painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
		for layer in self.layers:
			if layer.visible and layer.opacity > 0:
				painter.setOpacity(min(max(layer.opacity, 0.0), 1.0))
				painter.drawImage(0, 0, layer.frames[index])
		painter.end()
		self._composite_cache[index] = result
		return result


class ImageStudioDocument:
	def __init__(self, project: ImageProject | None = None, undo_limit: int = 64):
		self.project = project or ImageProject.blank()
		self.project.normalize()
		self.undo_limit = max(1, undo_limit)
		self._undo: list[ImageProject] = []
		self._redo: list[ImageProject] = []
		self.modified = False

	@property
	def can_undo(self) -> bool:
		return bool(self._undo)

	@property
	def can_redo(self) -> bool:
		return bool(self._redo)

	def checkpoint(self) -> None:
		self._undo.append(self.project.clone())
		if len(self._undo) > self.undo_limit:
			del self._undo[0]
		self._redo.clear()
		self.modified = True

	def undo(self) -> bool:
		if not self._undo:
			return False
		self._redo.append(self.project.clone())
		self.project = self._undo.pop()
		self.modified = True
		return True

	def redo(self) -> bool:
		if not self._redo:
			return False
		self._undo.append(self.project.clone())
		self.project = self._redo.pop()
		self.modified = True
		return True

	def replace_project(self, project: ImageProject, *, modified: bool = False) -> None:
		project.normalize()
		self.project = project
		self._undo.clear()
		self._redo.clear()
		self.modified = modified

	def current_image(self) -> QImage:
		project = self.project
		return project.layers[project.current_layer].frames[project.current_frame]

	def add_layer(self, name: str | None = None) -> int:
		self.checkpoint()
		project = self.project
		index = len(project.layers)
		project.layers.append(
			RasterLayer(
				name or f"Layer {index + 1}",
				[_blank_image(project.width, project.height) for _ in range(project.frame_count)],
			)
		)
		project.current_layer = index
		project.invalidate()
		return index

	def remove_layer(self, index: int) -> bool:
		if len(self.project.layers) <= 1 or not 0 <= index < len(self.project.layers):
			return False
		self.checkpoint()
		del self.project.layers[index]
		self.project.current_layer = min(index, len(self.project.layers) - 1)
		self.project.invalidate()
		return True

	def add_frame(self, *, copy_current: bool = False) -> int:
		self.checkpoint()
		project = self.project
		insert_at = project.current_frame + 1
		for layer in project.layers:
			frame = layer.frames[project.current_frame].copy() if copy_current else _blank_image(project.width, project.height)
			layer.frames.insert(insert_at, frame)
		project.frame_durations_ms.insert(insert_at, project.frame_durations_ms[project.current_frame])
		project.current_frame = insert_at
		project.invalidate()
		return insert_at

	def remove_frame(self, index: int) -> bool:
		if self.project.frame_count <= 1 or not 0 <= index < self.project.frame_count:
			return False
		self.checkpoint()
		for layer in self.project.layers:
			del layer.frames[index]
		del self.project.frame_durations_ms[index]
		self.project.current_frame = min(index, self.project.frame_count - 1)
		self.project.invalidate()
		return True

	def draw_line(self, start: QPoint, end: QPoint, color: QColor, width: int = 1, erase: bool = False) -> None:
		image = self.current_image()
		painter = QPainter(image)
		if erase:
			painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
		painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
		painter.setPen(QPen(color, max(1, width), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
		painter.drawLine(start, end)
		painter.end()
		self.project.invalidate()

	def draw_shape(
		self,
		kind: Literal["line", "rect", "ellipse"],
		start: QPoint,
		end: QPoint,
		color: QColor,
		width: int = 1,
	) -> None:
		image = self.current_image()
		painter = QPainter(image)
		painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
		painter.setPen(QPen(color, max(1, width)))
		if kind == "line":
			painter.drawLine(start, end)
		else:
			rect = QRect(start, end).normalized()
			if kind == "rect":
				painter.drawRect(rect)
			else:
				painter.drawEllipse(rect)
		painter.end()
		self.project.invalidate()

	def flood_fill(self, point: QPoint, color: QColor) -> bool:
		image = self.current_image()
		if not (0 <= point.x() < image.width() and 0 <= point.y() < image.height()):
			return False
		image.convertTo(QImage.Format.Format_ARGB32)
		target = image.pixelColor(point)
		if target.rgba() == color.rgba():
			return False
		stack = [(point.x(), point.y())]
		visited: set[tuple[int, int]] = set()
		while stack:
			x, y = stack.pop()
			if (x, y) in visited or not (0 <= x < image.width() and 0 <= y < image.height()):
				continue
			visited.add((x, y))
			if image.pixelColor(x, y).rgba() != target.rgba():
				continue
			image.setPixelColor(x, y, color)
			stack.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
		self.project.invalidate()
		return True


ExportKind = Literal["png", "gif", "sprite-sheet", "scratch-sprite", "xip", "ximg"]


@runtime_checkable
class ImageProjectCodec(Protocol):
	def import_file(self, path: Path) -> ImageProject:
		...

	def export_file(self, project: ImageProject, path: Path, kind: ExportKind) -> None:
		...


class QtImageProjectCodec:
	"""Portable host fallback for standard raster formats.

	When the canonical Xe media module is installed, `.xip` and `.ximg` pass through
	its deterministic encoders. No look-alike native format is ever written.
	"""

	def import_file(self, path: Path) -> ImageProject:
		if path.suffix.lower() == ".ximg":
			return self._load_ximg(path)
		if path.suffix.lower() == ".xip":
			return self._load_xip(path)
		reader = QImageReader(str(path))
		reader.setAutoTransform(True)
		if not reader.canRead():
			raise ImageStudioError(reader.errorString() or f"Cannot read {path.name}.")
		frames: list[QImage] = []
		durations: list[int] = []
		decoded_pixels = 0
		while True:
			image = reader.read()
			if image.isNull():
				if not frames:
					raise ImageStudioError(reader.errorString() or f"Cannot decode {path.name}.")
				break
			image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
			if image.width() > 4096 or image.height() > 4096:
				raise ImageStudioError("Imported image dimensions exceed the 4096-pixel project limit.")
			decoded_pixels += image.width() * image.height()
			if decoded_pixels > 4096 * 4096:
				raise ImageStudioError("Imported animation exceeds the 64 MiB decoded image budget.")
			frames.append(image)
			durations.append(max(20, reader.nextImageDelay() or 100))
			if not reader.jumpToNextImage():
				break
		width = max(frame.width() for frame in frames)
		height = max(frame.height() for frame in frames)
		normalized: list[QImage] = []
		for frame in frames:
			if frame.size() == QSize(width, height):
				normalized.append(frame)
			else:
				canvas = _blank_image(width, height)
				painter = QPainter(canvas)
				painter.drawImage(0, 0, frame)
				painter.end()
				normalized.append(canvas)
		return ImageProject(
			width=width,
			height=height,
			layers=[RasterLayer("Imported", normalized)],
			frame_durations_ms=durations,
			name=path.stem,
		)

	def export_file(self, project: ImageProject, path: Path, kind: ExportKind) -> None:
		if kind == "png":
			self._save_image(project.composite(), path, "PNG")
			return
		if kind == "sprite-sheet":
			sheet = _blank_image(project.width * project.frame_count, project.height)
			painter = QPainter(sheet)
			for index in range(project.frame_count):
				painter.drawImage(index * project.width, 0, project.composite(index))
			painter.end()
			self._save_image(sheet, path, "PNG")
			return
		if kind == "gif":
			self._save_gif(project, path)
			return
		if kind == "scratch-sprite":
			self._save_scratch_sprite(project, path)
			return
		if kind == "ximg":
			self._save_ximg(project, path)
			return
		if kind == "xip":
			self._save_xip(project, path)
			return
		raise ImageStudioError(f"Unsupported export type: {kind}")

	@staticmethod
	def _save_image(image: QImage, path: Path, format_name: str) -> None:
		path.parent.mkdir(parents=True, exist_ok=True)
		temporary = path.with_name(f".{path.name}.tmp")
		if not image.save(str(temporary), format_name):
			if temporary.exists():
				temporary.unlink()
			raise ImageStudioError(f"Qt could not encode {format_name}.")
		temporary.replace(path)

	@staticmethod
	def _save_gif(project: ImageProject, path: Path) -> None:
		try:
			from PIL import Image
		except ImportError as exc:
			raise ImageStudioError(
				"Animated GIF export requires Pillow; no file was written."
			) from exc
		frames = []
		for index in range(project.frame_count):
			qimage = project.composite(index).convertToFormat(QImage.Format.Format_RGBA8888)
			buffer = bytes(qimage.constBits().asstring(qimage.sizeInBytes()))
			frames.append(Image.frombytes("RGBA", (qimage.width(), qimage.height()), buffer))
		path.parent.mkdir(parents=True, exist_ok=True)
		temporary = path.with_name(f".{path.name}.tmp")
		try:
			frames[0].save(
				temporary,
				format="GIF",
				save_all=True,
				append_images=frames[1:],
				duration=project.frame_durations_ms,
				loop=0,
				disposal=2,
				optimize=False,
			)
			temporary.replace(path)
		finally:
			if temporary.exists():
				temporary.unlink()

	@classmethod
	def _save_scratch_sprite(cls, project: ImageProject, path: Path) -> None:
		"""Write a deterministic Scratch 3 sprite with one costume per frame.

		The generated playback stack uses each frame's millisecond duration. Scratch
		may schedule waits at its own tick rate, so the file preserves the requested
		timing values without claiming host-clock playback precision.
		"""
		costumes: list[dict[str, object]] = []
		members: dict[str, bytes] = {}
		for index in range(project.frame_count):
			payload = cls._png_bytes(project.composite(index))
			digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
			member = f"{digest}.png"
			members[member] = payload
			costumes.append(
				{
					"assetId": digest,
					"name": f"Frame {index + 1:03d}",
					"bitmapResolution": 1,
					"md5ext": member,
					"dataFormat": "png",
					"rotationCenterX": project.width / 2,
					"rotationCenterY": project.height / 2,
				}
			)
		blocks = cls._scratch_animation_blocks(project)
		sprite = {
			"isStage": False,
			"name": project.name.strip() or "Xenon Animation",
			"variables": {},
			"lists": {},
			"broadcasts": {},
			"blocks": blocks,
			"comments": {},
			"currentCostume": min(project.current_frame, project.frame_count - 1),
			"costumes": costumes,
			"sounds": [],
			"volume": 100,
			"layerOrder": 1,
			"visible": True,
			"x": 0,
			"y": 0,
			"size": 100,
			"direction": 90,
			"draggable": False,
			"rotationStyle": "all around",
		}
		members["sprite.json"] = json.dumps(
			sprite,
			ensure_ascii=False,
			sort_keys=True,
			separators=(",", ":"),
		).encode("utf-8")
		path.parent.mkdir(parents=True, exist_ok=True)
		fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
		os.close(fd)
		temporary = Path(temporary_name)
		try:
			with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
				for name in sorted(members):
					info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
					info.compress_type = zipfile.ZIP_DEFLATED
					info.create_system = 0
					archive.writestr(info, members[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
			os.replace(temporary, path)
		finally:
			if temporary.exists():
				temporary.unlink()

	@staticmethod
	def _scratch_animation_blocks(project: ImageProject) -> dict[str, dict[str, object]]:
		if project.frame_count <= 1:
			return {}
		blocks: dict[str, dict[str, object]] = {
			"xenon_event": {
				"opcode": "event_whenflagclicked",
				"next": "xenon_forever",
				"parent": None,
				"inputs": {},
				"fields": {},
				"shadow": False,
				"topLevel": True,
				"x": 24,
				"y": 24,
			},
			"xenon_forever": {
				"opcode": "control_forever",
				"next": None,
				"parent": "xenon_event",
				"inputs": {"SUBSTACK": [2, "xenon_frame_0000"]},
				"fields": {},
				"shadow": False,
				"topLevel": False,
			},
		}
		for index, duration in enumerate(project.frame_durations_ms):
			frame_id = f"xenon_frame_{index:04d}"
			wait_id = f"xenon_wait_{index:04d}"
			costume_id = f"xenon_costume_{index:04d}"
			next_frame = f"xenon_frame_{index + 1:04d}" if index + 1 < project.frame_count else None
			blocks[frame_id] = {
				"opcode": "looks_switchcostumeto",
				"next": wait_id,
				"parent": "xenon_forever" if index == 0 else f"xenon_wait_{index - 1:04d}",
				"inputs": {"COSTUME": [1, costume_id]},
				"fields": {},
				"shadow": False,
				"topLevel": False,
			}
			blocks[costume_id] = {
				"opcode": "looks_costume",
				"next": None,
				"parent": frame_id,
				"inputs": {},
				"fields": {"COSTUME": [f"Frame {index + 1:03d}", None]},
				"shadow": True,
				"topLevel": False,
			}
			seconds = f"{duration / 1000:.3f}".rstrip("0").rstrip(".")
			blocks[wait_id] = {
				"opcode": "control_wait",
				"next": next_frame,
				"parent": frame_id,
				"inputs": {"DURATION": [1, [4, seconds]]},
				"fields": {},
				"shadow": False,
				"topLevel": False,
			}
		return blocks

	@staticmethod
	def _portable_palette() -> tuple[QColor, ...]:
		return xvm_palette()

	@classmethod
	def _to_portable(cls, project: ImageProject):
		from xe_lang.media import ImageFrame, PortableImage
		palette = cls._portable_palette()
		frames = []
		for index in range(project.frame_count):
			image = project.composite(index)
			pixels: list[int] = []
			for y in range(image.height()):
				for x in range(image.width()):
					color = image.pixelColor(x, y)
					pixels.append(xvm_palette_index(color, palette))
			frames.append(ImageFrame(tuple(pixels), project.frame_durations_ms[index]))
		return PortableImage(project.width, project.height, tuple(frames), loop_count=0)

	@classmethod
	def _from_portable(cls, portable, name: str) -> ImageProject:
		palette = cls._portable_palette()
		frames: list[QImage] = []
		for portable_frame in portable.frames:
			image = _blank_image(portable.width, portable.height)
			for offset, value in enumerate(portable_frame.pixels):
				x = offset % portable.width
				y = offset // portable.width
				image.setPixelColor(x, y, QColor(0, 0, 0, 0) if value == 16 else palette[value])
			frames.append(image)
		return ImageProject(
			width=portable.width,
			height=portable.height,
			layers=[RasterLayer("Imported", frames)],
			frame_durations_ms=[frame.duration_ms for frame in portable.frames],
			name=name,
		)

	@classmethod
	def _save_ximg(cls, project: ImageProject, path: Path) -> None:
		from xe_lang.media import encode_ximg
		words = encode_ximg(cls._to_portable(project))
		payload = "\n".join(f"0x{word:08X}" for word in words) + "\n"
		cls._atomic_write(path, payload.encode("ascii"))

	@classmethod
	def _load_ximg(cls, path: Path) -> ImageProject:
		from xe_lang.media import decode_ximg
		words: list[int] = []
		for line in path.read_text(encoding="ascii").splitlines():
			for raw in line.split("#", 1)[0].replace(",", " ").split():
				words.append(int(raw, 0))
		return cls._from_portable(decode_ximg(words), path.stem)

	@staticmethod
	def _png_bytes(image: QImage) -> bytes:
		data = QByteArray()
		buffer = QBuffer(data)
		if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
			raise ImageStudioError("Cannot allocate a PNG buffer.")
		if not image.save(buffer, "PNG"):
			buffer.close()
			raise ImageStudioError("Qt could not encode a project layer.")
		buffer.close()
		return bytes(data)

	@classmethod
	def _save_xip(cls, project: ImageProject, path: Path) -> None:
		from xe_lang.media import write_xip
		members: dict[str, bytes] = {}
		layers: list[dict[str, object]] = []
		for layer_index, layer in enumerate(project.layers):
			frame_paths: list[str] = []
			for frame_index, image in enumerate(layer.frames):
				member = f"layers/{layer_index:04d}/frames/{frame_index:04d}.png"
				members[member] = cls._png_bytes(image)
				frame_paths.append(member)
			layers.append(
				{
					"frames": frame_paths,
					"name": layer.name,
					"opacity": round(min(max(layer.opacity, 0.0), 1.0), 6),
					"visible": bool(layer.visible),
				}
			)
		manifest = {
			"format": "xip",
			"version": 1,
			"name": project.name,
			"width": project.width,
			"height": project.height,
			"frame_durations_ms": list(project.frame_durations_ms),
			"layers": layers,
		}
		write_xip(path, manifest, members, overwrite=True)

	@classmethod
	def _load_xip(cls, path: Path) -> ImageProject:
		from xe_lang.media import read_xip
		manifest, members = read_xip(path)
		if manifest.get("format") != "xip" or manifest.get("version") != 1:
			raise ImageStudioError("Unsupported XIP project version.")
		try:
			width = int(manifest["width"])
			height = int(manifest["height"])
			durations = [int(value) for value in manifest["frame_durations_ms"]]
			layer_specs = list(manifest["layers"])
		except (KeyError, TypeError, ValueError) as exc:
			raise ImageStudioError("XIP project manifest is incomplete.") from exc
		if not durations or not layer_specs or any(value <= 0 for value in durations):
			raise ImageStudioError("XIP project has no valid layers or frames.")
		if not 1 <= width <= 4096 or not 1 <= height <= 4096:
			raise ImageStudioError("XIP project dimensions exceed the supported range.")
		if width * height * len(durations) * len(layer_specs) > 4096 * 4096:
			raise ImageStudioError("XIP project exceeds the 64 MiB decoded layer budget.")
		layers: list[RasterLayer] = []
		for spec in layer_specs:
			if not isinstance(spec, dict):
				raise ImageStudioError("XIP layer record is invalid.")
			paths = spec.get("frames")
			if not isinstance(paths, list) or len(paths) != len(durations):
				raise ImageStudioError("XIP layer frame count is inconsistent.")
			frames: list[QImage] = []
			for member in paths:
				payload = members.get(str(member))
				image = QImage.fromData(payload or b"", "PNG")
				if image.isNull() or image.size() != QSize(width, height):
					raise ImageStudioError("XIP layer image is missing or has the wrong dimensions.")
				frames.append(image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))
			layers.append(
				RasterLayer(
					name=str(spec.get("name", f"Layer {len(layers) + 1}")),
					frames=frames,
					visible=bool(spec.get("visible", True)),
					opacity=min(max(float(spec.get("opacity", 1.0)), 0.0), 1.0),
				)
			)
		return ImageProject(
			width=width,
			height=height,
			layers=layers,
			frame_durations_ms=durations,
			name=str(manifest.get("name", path.stem)),
		)

	@staticmethod
	def _atomic_write(path: Path, payload: bytes) -> None:
		path.parent.mkdir(parents=True, exist_ok=True)
		fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
		try:
			with os.fdopen(fd, "wb") as handle:
				handle.write(payload)
				handle.flush()
				os.fsync(handle.fileno())
			os.replace(temporary_name, path)
		finally:
			if os.path.exists(temporary_name):
				os.unlink(temporary_name)


def load_default_image_codec() -> ImageProjectCodec:
	try:
		from xe_lang.media import get_image_project_codec
		codec = get_image_project_codec()
		if isinstance(codec, ImageProjectCodec):
			return codec
	except (ImportError, AttributeError, RuntimeError):
		pass
	return QtImageProjectCodec()
