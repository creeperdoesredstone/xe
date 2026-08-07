from __future__ import annotations


SPECIAL_KEY_CODES = {
	"backspace": 8,
	"tab": 9,
	"backtab": 9,
	"isolefttab": 9,
	"return": 13,
	"enter": 13,
	"escape": 27,
	"space": 32,
	"left": 3,
	"up": 4,
	"right": 5,
	"down": 6,
	"delete": 127,
}


def normalize_key_code(
	key_name: str,
	text: str = "",
	*,
	control: bool = False,
	fallback: int = 0,
) -> int:
	"""Map host-toolkit key data to Xe's ASCII/special-key contract."""
	normalized = key_name.casefold().replace("_", "")
	special = SPECIAL_KEY_CODES.get(normalized)
	if special is not None:
		return special
	if control and len(normalized) == 1 and "a" <= normalized <= "z":
		return ord(normalized)
	if len(text) == 1 and ord(text) <= 0xFF:
		return ord(text)
	if len(normalized) == 1 and ord(normalized) <= 0xFF:
		return ord(normalized)
	return int(fallback)
