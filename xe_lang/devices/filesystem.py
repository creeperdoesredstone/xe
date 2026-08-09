from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
from threading import RLock
import time
from typing import TextIO


@dataclass
class FileRecord:
	handle: int
	path: Path
	stream: TextIO
	mode: str


@dataclass(frozen=True)
class FileEntry:
	name: str
	is_directory: bool


@dataclass(frozen=True)
class FileStat:
	size: int
	modified_ns: int
	is_directory: bool
	revision: int


VIRTUAL_DRIVE_DIRECTORY = "XenonOS/VirtualDrive"
VIRTUAL_DRIVE_MARKER = ".xenon-virtual-drive"
TRASH_DIRECTORY = ".xenon-trash"
INTERNAL_NAMES = frozenset((VIRTUAL_DRIVE_MARKER, TRASH_DIRECTORY))
INTERNAL_NAMES_CASEFOLD = frozenset(name.casefold() for name in INTERNAL_NAMES)
ENTRY_CACHE_LIMIT = 128
MAX_TEXT_FILE_BYTES = 1_048_576
MAX_TEXT_FILE_CHARACTERS = 200_000


def default_virtual_drive_root() -> Path:
	base = os.environ.get("LOCALAPPDATA")
	if base:
		return (Path(base) / VIRTUAL_DRIVE_DIRECTORY).resolve()
	return (Path.home() / ".xenonos" / "VirtualDrive").resolve()


class FileSystemDevice:
	"""Private, recoverable virtual drive used by the Xe OS library."""

	def __init__(self, root: str | Path | None = None) -> None:
		self.root = Path(root).resolve() if root is not None else default_virtual_drive_root()
		self.root.mkdir(parents=True, exist_ok=True)
		self._marker = self.root / VIRTUAL_DRIVE_MARKER
		self._marker.touch(exist_ok=True)
		self._trash = self.root / TRASH_DIRECTORY
		self._trash.mkdir(exist_ok=True)
		self._records: dict[int, FileRecord] = {}
		self._next_handle = 1
		self._lock = RLock()
		self._entry_cache: dict[Path, tuple[int, tuple[FileEntry, ...]]] = {}
		self._revision = 0

	@property
	def revision(self) -> int:
		with self._lock:
			return self._revision

	def _changed(self) -> None:
		with self._lock:
			self._entry_cache.clear()
			self._revision = (self._revision + 1) & 0x7FFFFFFF

	def _resolve(self, name: str) -> Path | None:
		candidate = Path(str(name).replace("\\", "/"))
		if not name or candidate.is_absolute():
			return None
		resolved = (self.root / candidate).resolve()
		try:
			relative = resolved.relative_to(self.root)
		except ValueError:
			return None
		if any(part.casefold() in INTERNAL_NAMES_CASEFOLD for part in relative.parts):
			return None
		return resolved

	def normalize(self, name: str) -> str:
		path = self._resolve(name)
		if path is None:
			return ""
		try:
			relative = path.relative_to(self.root)
		except ValueError:
			return ""
		value = relative.as_posix()
		return "." if value in {"", "."} else value

	def _open(self, name: str, mode: str) -> int:
		path = self._resolve(name)
		if path is None:
			return 0
		try:
			if mode == "r" and path.stat().st_size > MAX_TEXT_FILE_BYTES:
				return 0
			if mode in {"w", "a"}:
				path.parent.mkdir(parents=True, exist_ok=True)
			stream = path.open(mode, encoding="utf-8", newline="")
			if mode == "w":
				self._changed()
		except (OSError, UnicodeError):
			return 0
		with self._lock:
			handle = self._next_handle
			self._next_handle += 1
			self._records[handle] = FileRecord(handle, path, stream, mode)
		return handle

	def open_read(self, name: str) -> int:
		return self._open(name, "r")

	def open_write(self, name: str) -> int:
		return self._open(name, "w")

	def open_append(self, name: str) -> int:
		return self._open(name, "a")

	def read(self, handle: int) -> str:
		with self._lock:
			record = self._records.get(int(handle))
			if not record or record.mode != "r":
				return ""
			try:
				start = record.stream.tell()
				text = record.stream.read(MAX_TEXT_FILE_CHARACTERS + 1)
				if len(text) > MAX_TEXT_FILE_CHARACTERS:
					record.stream.seek(start)
					return ""
				return text
			except (OSError, UnicodeError):
				return ""

	def read_text(self, name: str) -> str | None:
		path = self._resolve(name)
		if path is None or not path.is_file():
			return None
		try:
			if path.stat().st_size > MAX_TEXT_FILE_BYTES:
				return None
			with path.open("r", encoding="utf-8", newline="") as stream:
				text = stream.read(MAX_TEXT_FILE_CHARACTERS + 1)
			return text if len(text) <= MAX_TEXT_FILE_CHARACTERS else None
		except (OSError, UnicodeError):
			return None

	def write(self, handle: int, text: str) -> bool:
		with self._lock:
			record = self._records.get(int(handle))
			if not record or record.mode not in {"w", "a"}:
				return False
			try:
				record.stream.write(text)
				record.stream.flush()
				self._entry_cache.clear()
				self._revision = (self._revision + 1) & 0x7FFFFFFF
				return True
			except (OSError, UnicodeError):
				return False

	def close_path(self, name: str) -> None:
		path = self._resolve(name)
		if path is None:
			return
		with self._lock:
			for handle, record in list(self._records.items()):
				if record.path == path:
					try:
						record.stream.close()
					finally:
						self._records.pop(handle, None)

	def entries(self, name: str = ".") -> tuple[FileEntry, ...]:
		path = self._resolve(name)
		if path is None:
			return ()
		try:
			status = path.stat()
			if not stat.S_ISDIR(status.st_mode):
				return ()
			stamp = status.st_mtime_ns
			with self._lock:
				cached = self._entry_cache.get(path)
				if cached is not None and cached[0] == stamp:
					return cached[1]
			items = [
				FileEntry(child.name, child.is_dir())
				for child in path.iterdir()
				if child.name not in INTERNAL_NAMES
				if self._resolve(str(child.relative_to(self.root)).replace("\\", "/")) is not None
			]
		except OSError:
			return ()
		entries = tuple(sorted(items, key=lambda item: (not item.is_directory, item.name.casefold(), item.name)))
		with self._lock:
			if (
				path not in self._entry_cache
				and len(self._entry_cache) >= ENTRY_CACHE_LIMIT
			):
				self._entry_cache.pop(next(iter(self._entry_cache)))
			self._entry_cache[path] = (stamp, entries)
		return entries

	def entry_count(self, name: str = ".") -> int:
		return len(self.entries(name))

	def entry_name(self, name: str, index: int) -> str:
		items = self.entries(name)
		return items[index].name if 0 <= index < len(items) else ""

	def entry_is_directory(self, name: str, index: int) -> bool:
		items = self.entries(name)
		return bool(items[index].is_directory) if 0 <= index < len(items) else False

	def exists(self, name: str) -> bool:
		path = self._resolve(name)
		return bool(path and path.exists())

	def is_directory(self, name: str) -> bool:
		path = self._resolve(name)
		try:
			return bool(path and path.is_dir())
		except OSError:
			return False

	def stat(self, name: str) -> FileStat | None:
		path = self._resolve(name)
		if path is None:
			return None
		try:
			status = path.stat()
		except OSError:
			return None
		return FileStat(
			0 if stat.S_ISDIR(status.st_mode) else int(status.st_size),
			int(status.st_mtime_ns),
			stat.S_ISDIR(status.st_mode),
			self.revision,
		)

	def make_file(self, name: str) -> bool:
		path = self._resolve(name)
		if path is None or path.exists():
			return False
		try:
			path.parent.mkdir(parents=True, exist_ok=True)
			path.touch(exist_ok=False)
			self._changed()
			return True
		except OSError:
			return False

	def make_directory(self, name: str) -> bool:
		path = self._resolve(name)
		if path is None or path.exists():
			return False
		try:
			path.mkdir(parents=True, exist_ok=False)
			self._changed()
			return True
		except OSError:
			return False

	def rename(self, name: str, new_name: str) -> bool:
		path = self._resolve(name)
		destination = self._resolve(new_name)
		if path is None or destination is None or not path.exists() or destination.exists():
			return False
		with self._lock:
			if any(record.path == path or path in record.path.parents for record in self._records.values()):
				return False
			try:
				destination.parent.mkdir(parents=True, exist_ok=True)
				path.rename(destination)
				self._changed()
				return True
			except OSError:
				return False

	def copy(self, name: str, destination_name: str) -> bool:
		path = self._resolve(name)
		destination = self._resolve(destination_name)
		if (
			path is None
			or destination is None
			or not path.exists()
			or destination.exists()
			or path == self.root
			or (path.is_dir() and path in destination.parents)
		):
			return False
		try:
			destination.parent.mkdir(parents=True, exist_ok=True)
			if path.is_dir():
				shutil.copytree(path, destination)
			else:
				shutil.copy2(path, destination)
			self._changed()
			return True
		except OSError:
			return False

	def delete(self, name: str) -> bool:
		path = self._resolve(name)
		if path is None or path == self.root or not path.exists():
			return False
		with self._lock:
			if any(record.path == path or path in record.path.parents for record in self._records.values()):
				return False
		try:
			stamp = time.time_ns()
			destination = self._trash / f"{stamp}-{path.name}"
			while destination.exists():
				stamp += 1
				destination = self._trash / f"{stamp}-{path.name}"
			path.replace(destination)
			self._changed()
			return True
		except OSError:
			return False

	def close_all(self) -> None:
		with self._lock:
			for record in self._records.values():
				try:
					record.stream.close()
				except OSError:
					pass
			self._records.clear()
