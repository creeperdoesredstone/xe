STATIC_LAYOUT_MAGIC = 0x58455354  # "XEST"
STATIC_LAYOUT_VERSION = 1
STATIC_LAYOUT_TRAILER_WORDS = 4
MAX_STATIC_WORDS = 0x10000


def static_layout_trailer(static_words: int) -> tuple[int, int, int, int]:
	static_words = int(static_words)
	if not 0 <= static_words <= MAX_STATIC_WORDS:
		raise ValueError(f"Static layout must contain 0 to {MAX_STATIC_WORDS} words")
	return (
		STATIC_LAYOUT_MAGIC,
		STATIC_LAYOUT_VERSION,
		static_words,
		static_words ^ 0xFFFFFFFF,
	)


def decode_static_layout(data: list[int]) -> tuple[list[int], int]:
	if len(data) < STATIC_LAYOUT_TRAILER_WORDS:
		return data, 0

	magic, version, static_words, checksum = data[-STATIC_LAYOUT_TRAILER_WORDS:]
	if (
		magic != STATIC_LAYOUT_MAGIC
		or version != STATIC_LAYOUT_VERSION
		or checksum != (static_words ^ 0xFFFFFFFF) & 0xFFFFFFFF
	):
		return data, 0
	if not 0 <= static_words <= MAX_STATIC_WORDS:
		raise ValueError(f"Static layout exceeds the {MAX_STATIC_WORDS}-word address space")

	return data[:-STATIC_LAYOUT_TRAILER_WORDS], static_words
