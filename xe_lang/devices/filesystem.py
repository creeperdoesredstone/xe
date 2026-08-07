from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
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


VIRTUAL_DRIVE_DIRECTORY = "XenonOS/VirtualDrive"
VIRTUAL_DRIVE_MARKER = ".xenon-virtual-drive"
TRASH_DIRECTORY = ".xenon-trash"
INTERNAL_NAMES = frozenset((VIRTUAL_DRIVE_MARKER, TRASH_DIRECTORY))


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

	def _resolve(self, name: str) -> Path | None:
		candidate = Path(name)
		if not name or candidate.is_absolute():
			return None
		if candidate.parts and candidate.parts[0] in INTERNAL_NAMES:
			return None
		resolved = (self.root / candidate).resolve()
		try:
			resolved.relative_to(self.root)
		except ValueError:
			return None
		return resolved

	def _open(self, name: str, mode: str) -> int:
		path = self._resolve(name)
		if path is None:
			return 0
		try:
			if mode == "w":
				path.parent.mkdir(parents=True, exist_ok=True)
			stream = path.open(mode, encoding="utf-8", newline="")
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

	def read(self, handle: int) -> str:
		with self._lock:
			record = self._records.get(int(handle))
			if not record or record.mode != "r":
				return ""
			try:
				return record.stream.read()
			except (OSError, UnicodeError):
				return ""

	def write(self, handle: int, text: str) -> bool:
		with self._lock:
			record = self._records.get(int(handle))
			if not record or record.mode != "w":
				return False
			try:
				record.stream.write(text)
				record.stream.flush()
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
		if path is None or not path.is_dir():
			return ()
		try:
			items = [
				FileEntry(child.name, child.is_dir())
				for child in path.iterdir()
				if child.name not in INTERNAL_NAMES
				if self._resolve(str(child.relative_to(self.root)).replace("\\", "/")) is not None
			]
		except OSError:
			return ()
		return tuple(sorted(items, key=lambda item: (not item.is_directory, item.name.casefold(), item.name)))

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

	def make_file(self, name: str) -> bool:
		path = self._resolve(name)
		if path is None or path.exists():
			return False
		try:
			path.parent.mkdir(parents=True, exist_ok=True)
			path.touch(exist_ok=False)
			return True
		except OSError:
			return False

	def make_directory(self, name: str) -> bool:
		path = self._resolve(name)
		if path is None or path.exists():
			return False
		try:
			path.mkdir(parents=True, exist_ok=False)
			return True
		except OSError:
			return False

	def rename(self, name: str, new_name: str) -> bool:
		path = self._resolve(name)
		destination = self._resolve(new_name)
		if path is None or destination is None or not path.exists() or destination.exists():
			return False
		try:
			destination.parent.mkdir(parents=True, exist_ok=True)
			path.rename(destination)
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
