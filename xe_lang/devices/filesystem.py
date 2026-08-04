from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TextIO


@dataclass
class FileRecord:
	handle: int
	path: Path
	stream: TextIO
	mode: str


class FileSystemDevice:
	"""Small, sandboxed text-file device used by the Xe OS library."""

	def __init__(self, root: str | Path | None = None) -> None:
		self.root = Path(root or Path.cwd()).resolve()
		self._records: dict[int, FileRecord] = {}
		self._next_handle = 1
		self._lock = RLock()

	def _resolve(self, name: str) -> Path | None:
		candidate = Path(name)
		if not name or candidate.is_absolute():
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

	def close_all(self) -> None:
		with self._lock:
			for record in self._records.values():
				try:
					record.stream.close()
				except OSError:
					pass
			self._records.clear()
