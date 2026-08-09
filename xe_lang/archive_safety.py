from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath
import zipfile


MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4096
MAX_COMPRESSION_RATIO = 1000


class ArchiveSafetyError(ValueError):
	pass


def normalize_archive_member(name: str) -> str:
	if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
		raise ArchiveSafetyError(f"Unsafe archive member name: {name!r}")
	value = PurePosixPath(name)
	if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
		raise ArchiveSafetyError(f"Unsafe archive member name: {name!r}")
	if ":" in value.parts[0] or any(ord(char) < 32 for char in name):
		raise ArchiveSafetyError(f"Unsafe archive member name: {name!r}")
	return value.as_posix()


def load_safe_zip_members(payload: bytes) -> tuple[tuple[str, bytes], ...]:
	if len(payload) > MAX_ARCHIVE_BYTES:
		raise ArchiveSafetyError("Archive exceeds the compressed-size limit")
	try:
		with zipfile.ZipFile(BytesIO(payload), "r") as archive:
			infos = archive.infolist()
			if len(infos) > MAX_ARCHIVE_MEMBERS:
				raise ArchiveSafetyError("Archive contains too many members")
			seen: set[str] = set()
			total = 0
			members: list[tuple[str, bytes]] = []
			for info in infos:
				if info.is_dir() or info.flag_bits & 1:
					raise ArchiveSafetyError(f"Unsupported archive member: {info.filename!r}")
				name = normalize_archive_member(info.filename)
				portable_name = name.casefold()
				if portable_name in seen:
					raise ArchiveSafetyError(f"Duplicate archive member: {name}")
				seen.add(portable_name)
				if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
					raise ArchiveSafetyError(f"Archive member is too large: {name}")
				if info.file_size > 0 and info.compress_size == 0:
					raise ArchiveSafetyError(f"Invalid compressed size for archive member: {name}")
				if info.compress_size and info.file_size > info.compress_size * MAX_COMPRESSION_RATIO:
					raise ArchiveSafetyError(f"Archive member compression ratio is unsafe: {name}")
				total += info.file_size
				if total > MAX_ARCHIVE_BYTES:
					raise ArchiveSafetyError("Archive exceeds the expanded-size limit")
				data = archive.read(info)
				if len(data) != info.file_size or len(data) > MAX_ARCHIVE_MEMBER_BYTES:
					raise ArchiveSafetyError(f"Archive member size mismatch: {name}")
				members.append((name, data))
			return tuple(members)
	except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as error:
		raise ArchiveSafetyError(f"Invalid ZIP archive: {error}") from error
