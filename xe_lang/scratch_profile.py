from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path


@dataclass(frozen=True)
class ScratchVMProfile:
	name: str
	version: str
	template_sha256: str
	supported_syscalls: frozenset[int]
	address_limit: int
	static_limit: int
	capabilities: frozenset[str]
	mem_program_target: str = "Stage"
	mem_program_list: str = "MEM_PROGRAM"
	mem_data_list: str = "MEM_DATA"

	@classmethod
	def load(cls, path: str | Path) -> "ScratchVMProfile":
		payload = json.loads(Path(path).read_text(encoding="utf-8"))
		return cls(
			str(payload["name"]),
			str(payload["version"]),
			str(payload["template_sha256"]).lower(),
			frozenset(int(value) for value in payload["supported_syscalls"]),
			int(payload["address_limit"]),
			int(payload["static_limit"]),
			frozenset(str(value) for value in payload.get("capabilities", ())),
			str(payload.get("mem_program_target", "Stage")),
			str(payload.get("mem_program_list", "MEM_PROGRAM")),
			str(payload.get("mem_data_list", "MEM_DATA")),
		)

	def verify_template(self, path: str | Path) -> bool:
		digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
		return digest == self.template_sha256


def bundled_profile_path() -> Path:
	return Path(__file__).resolve().parent.parent / "scratch_vm" / "profile.json"


def bundled_template_path() -> Path:
	return Path(__file__).resolve().parent.parent / "scratch_vm" / "xenon131-vm.sb3"


def load_bundled_profile() -> ScratchVMProfile:
	return ScratchVMProfile.load(bundled_profile_path())
