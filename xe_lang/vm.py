import struct
import tkinter as tk
import time
import math
import threading
from bisect import bisect_right
from xe_lang.helper import Result, VMError, Position
from xe_lang.devices import DEFAULT_PALETTE, DeviceRuntime, FrameSnapshot, OSDevice
from xe_lang.devices.keymap import normalize_key_code
from xe_lang.executable import MAX_STATIC_WORDS, decode_static_layout
from xe_lang.syscall_abi import SyscallID
from disassemble import decode_instruction

TRUE = 0xFFFFFFFF
FALSE = 0
MAGIC = 0x58424E31  # "XBN1"
VERSION = 1
STACK_SIZE = 65_536
MIN_DATA_WORDS = 65_536
MAX_ADDRESS_COUNT = 200_000
DEFAULT_DATA_WORDS = MAX_ADDRESS_COUNT
HEAP_START = 0x2000
GC_ALLOCATION_INTERVAL = 0x2000

PALETTE = list(DEFAULT_PALETTE)


def u32_to_float(bits: int) -> float:
	return struct.unpack(">f", struct.pack(">I", bits & TRUE))[0]


def float_to_u32(value: float) -> int:
	return struct.unpack(">I", struct.pack(">f", value))[0]


def to_u32(value: int) -> int:
	return value & TRUE


def integer_power_overflows_u32(base: int, exponent: int) -> bool:
	if exponent == 0 or base in (-1, 0, 1):
		return False
	if base < 0 and exponent % 2 == 1:
		return False
	if exponent > 63:
		return True
	return base ** exponent > TRUE


class VM:
	def __init__(
		self,
		program: list[int],
		output_handler=None,
		os_device: OSDevice | None = None,
		frame_handler=None,
		cancel_event: threading.Event | None = None,
		filesystem_root=None,
		input_handler=None,
		request_handler=None,
		memory_words: int = DEFAULT_DATA_WORDS,
	):
		if len(program) < 4:
			raise ValueError("Executable too small")

		self.magic, self.version, self.text_size, self.data_size = program[:4]

		if self.magic != MAGIC:
			raise ValueError("Invalid executable")

		if self.version != VERSION:
			raise ValueError(f"Unsupported executable version {self.version}")

		expected = 4 + self.text_size + self.data_size
		if len(program) != expected:
			raise ValueError("Corrupt executable")

		memory_words = int(memory_words)
		if not MIN_DATA_WORDS <= memory_words <= MAX_ADDRESS_COUNT:
			raise ValueError(
				f"Data memory must contain {MIN_DATA_WORDS} to {MAX_ADDRESS_COUNT} addresses"
			)

		self.program = program[4:]
		self.instructions = program[4 : 4 + self.text_size]
		self.program_memory, self.static_words = decode_static_layout(
			program[4 + self.text_size :]
		)
		if not 0 <= self.static_words <= MAX_STATIC_WORDS:
			raise ValueError("Static data exceeds the 65536-word XAssembly address space")
		if self.static_words > memory_words:
			raise ValueError("Static data exceeds available data memory")
		self.heap_start = max(HEAP_START, self.static_words)
		self.stack = [0] * STACK_SIZE
		self.call_stack: list = []
		self.ip: int = 0
		self.data_memory: list = [0] * memory_words
		self.free_list: list = self._fresh_free_list()
		self.allocations: dict[int, int] = {}
		self._managed_allocations: set[int] = set()
		self._gc_protected: set[int] = set()
		self._words_since_gc = 0
		self.gc_runs = 0
		self.gc_reclaimed_words = 0

		self.fp: int = 0
		self.sp: int = 0
		self.cr: int = 0
		self.im: int = TRUE
		self.bp: int = 0
		self.max_sp: int = 0

		self.labels = {}
		self.start_time = time.monotonic()
		self.output_handler = output_handler  # for ide
		self.input_handler = input_handler or input
		self.request_handler = request_handler
		self.cancel_event = cancel_event or threading.Event()
		self._external_frame_handler = frame_handler
		self._last_snapshot: FrameSnapshot | None = None

		self.devices = DeviceRuntime(
			os_device, self._frame_presented, filesystem_root=filesystem_root
		)
		self.exit_code = 0
		self.halt_requested = False
		self.width = self.devices.graphics.width
		self.height = self.devices.graphics.height
		self.back_buffer = self.devices.graphics.back_buffer
		self.front_buffer = self.devices.graphics.front_buffer

		self.clip_rect = self.devices.graphics.clip_rect

		self._legacy_mouse_btn = 0
		self.key_queue = self.devices.input.key_queue
		self.keys_down = self.devices.input.keys_down

		# for standalone execution
		self.root = None
		self.canvas = None
		self.img = None
		self.canvas_image_id = None
		self.display_img = None
		self._tk_forwarded_keys: dict[int, int] = {}
		self.heap_pointer = self.heap_start
		self._binary_position = Position(0, 0, 0, "<bin>", "")

	def _fresh_free_list(self) -> list[tuple[int, int]]:
		available = len(self.data_memory) - self.heap_start
		return [] if available <= 0 else [(self.heap_start, available)]

	@property
	def mouse_x(self) -> int:
		return self.devices.input.pointer_position()[0]

	@mouse_x.setter
	def mouse_x(self, value: int) -> None:
		_, y = self.devices.input.pointer_position()
		self.devices.input.move_pointer(value, y)

	@property
	def mouse_y(self) -> int:
		return self.devices.input.pointer_position()[1]

	@mouse_y.setter
	def mouse_y(self, value: int) -> None:
		x, _ = self.devices.input.pointer_position()
		self.devices.input.move_pointer(x, value)

	@property
	def mouse_btn(self) -> int:
		return self._legacy_mouse_btn

	@mouse_btn.setter
	def mouse_btn(self, value: int) -> None:
		value = int(value)
		mapping = {1: 1, 2: 2, 3: 4}
		old_button = mapping.get(self._legacy_mouse_btn)
		new_button = mapping.get(value)
		if old_button and old_button != new_button:
			self.devices.input.set_button(old_button, False)
		if new_button and new_button != old_button:
			self.devices.input.set_button(new_button, True)
		self._legacy_mouse_btn = value

	@property
	def modifiers(self) -> int:
		return self.devices.input.modifiers

	def init_graphics_window(self):
		if self.root is not None:
			return

		# skip tkinter if running on ide
		if threading.current_thread() is not threading.main_thread():
			return

		try:
			self.root = tk.Tk()
			self.root.title("Xenon-131 Virtual System")
			self.root.resizable(False, False)

			self.canvas = tk.Canvas(
				self.root,
				width=self.width * 3,
				height=self.height * 3,
				bg="black",
				highlightthickness=0,
			)
			self.canvas.pack()

			self.img = tk.PhotoImage(width=self.width, height=self.height)
			self.canvas_image_id = self.canvas.create_image(
				0, 0, anchor="nw", image=self.img
			)

			self.canvas.bind("<Motion>", self._on_mouse_move)
			self.canvas.bind("<ButtonPress>", self._on_mouse_press)
			self.canvas.bind("<ButtonRelease>", self._on_mouse_release)
			self.root.bind("<KeyPress>", self._on_key_press)
			self.root.bind("<KeyRelease>", self._on_key_release)
			self.root.bind("<FocusOut>", self._release_host_input)
			self.root.protocol("WM_DELETE_WINDOW", self.cancel_event.set)
			self.canvas.focus_set()

			self.root.update()
		except Exception:
			self.close_graphics_window()

	def close_graphics_window(self) -> None:
		self._release_host_input()
		root = self.root
		canvas = self.canvas
		canvas_image_id = self.canvas_image_id
		if canvas is not None and canvas_image_id is not None:
			try:
				canvas.itemconfig(canvas_image_id, image="")
				canvas.delete(canvas_image_id)
			except Exception:
				pass
		self.canvas_image_id = None
		self.display_img = None
		self.img = None
		self.canvas = None
		self.root = None
		if root is not None:
			try:
				root.update_idletasks()
			except Exception:
				pass
			try:
				root.destroy()
			except Exception:
				pass

	def _on_mouse_move(self, event):
		self.devices.input.move_pointer(event.x // 3, event.y // 3)

	def _on_mouse_press(self, event):
		self.devices.input.move_pointer(event.x // 3, event.y // 3)
		button = {1: 1, 2: 4, 3: 2}.get(event.num)
		if button:
			self.devices.input.set_button(button, True)

	def _on_mouse_release(self, event):
		self.devices.input.move_pointer(event.x // 3, event.y // 3)
		button = {1: 1, 2: 4, 3: 2}.get(event.num)
		if button:
			self.devices.input.set_button(button, False)

	def _on_key_press(self, event):
		mod = self._get_mod_state(event.state)
		code = normalize_key_code(
			str(getattr(event, "keysym", "")),
			str(getattr(event, "char", "")),
			control=bool(mod & 2),
			fallback=int(event.keycode),
		)
		previous = self._tk_forwarded_keys.get(int(event.keycode))
		if previous is not None and previous != code:
			self.devices.input.set_key(previous, False, mod)
		self._tk_forwarded_keys[int(event.keycode)] = code
		self.devices.input.set_key(code, True, mod)

	def _on_key_release(self, event):
		mod = self._get_mod_state(event.state)
		code = self._tk_forwarded_keys.pop(int(event.keycode), None)
		if code is None:
			code = normalize_key_code(
				str(getattr(event, "keysym", "")),
				str(getattr(event, "char", "")),
				control=bool(mod & 2),
				fallback=int(event.keycode),
			)
		self.devices.input.set_key(code, False, mod)

	def _release_host_input(self, _event=None):
		self.devices.input.release_all()
		self._tk_forwarded_keys.clear()

	def _get_mod_state(self, state):
		mask = 0
		if state & 0x0001:
			mask |= 1
		if state & 0x0004:
			mask |= 2
		if state & 0x0008:
			mask |= 4
		return mask

	def _frame_presented(self, snapshot: FrameSnapshot) -> None:
		self._last_snapshot = snapshot
		if self._external_frame_handler:
			self._external_frame_handler(snapshot)
		elif threading.current_thread() is threading.main_thread():
			self.init_graphics_window()
			self.render_front_buffer(snapshot)

	def render_front_buffer(self, snapshot: FrameSnapshot | None = None):
		snapshot = snapshot or self._last_snapshot
		if snapshot is None:
			indices = bytes(pixel for row in self.front_buffer for pixel in row)
			snapshot = FrameSnapshot(
				self.width,
				self.height,
				indices,
				self.devices.os.palette,
				0,
			)
		if not self.img or not self.root:
			return
		try:
			pixel_data = " ".join(
				"{"
				+ " ".join(
					snapshot.palette[
						snapshot.indices[y * snapshot.width + x] % len(snapshot.palette)
					]
					for x in range(snapshot.width)
				)
				+ "}"
				for y in range(snapshot.height)
			)
			self.img.put(pixel_data)

			self.display_img = self.img.zoom(3)
			self.canvas.itemconfig(self.canvas_image_id, image=self.display_img)

			self.root.update_idletasks()
			self.root.update()
		except Exception:
			pass

	def write_pixel(self, x: int, y: int, color_idx: int):
		self.devices.graphics.set_pixel(x, y, color_idx)

	def _output(self, text: str):
		if self.output_handler:
			self.output_handler(text)
		else:
			print(text, end="")

	def read_mem_string(self, address: int, maximum_words: int | None = None) -> str:
		if not 0 <= address < len(self.data_memory):
			raise ValueError("Invalid string address")
		chars = []
		limit = len(self.data_memory) - address
		if maximum_words is not None:
			limit = min(limit, max(0, int(maximum_words)))
		for offset in range(limit):
			val = self.data_memory[address + offset]
			if val == 0:
				return "".join(chars)
			chars.append(chr(val & 0xFF))
		raise ValueError("Unterminated string")

	def write_mem_string(self, address: int, string: str):
		words = len(string) + 1
		if not 0 <= address <= len(self.data_memory) - words:
			raise ValueError("String write extends beyond memory")
		for offset, char in enumerate(string):
			self.data_memory[address + offset] = ord(char) & 0xFF
		self.data_memory[address + len(string)] = 0

	def read_string_descriptor(self, descriptor: int) -> str:
		if not 0 <= descriptor <= len(self.data_memory) - 3:
			raise ValueError("Invalid string descriptor")
		address = self.data_memory[descriptor]
		length = self.data_memory[descriptor + 1]
		capacity = self.data_memory[descriptor + 2]
		if length < 1 or capacity < length:
			raise ValueError("Invalid string descriptor length or capacity")
		if not 0 <= address <= len(self.data_memory) - capacity:
			raise ValueError("String descriptor points outside memory")
		value = self.read_mem_string(address, length)
		if len(value) + 1 != length:
			raise ValueError("String descriptor length is stale")
		return value

	def allocate_string(self, value: str, result: Result) -> int | None:
		result.register(self.malloc(3, managed=True))
		if result.error:
			return None
		descriptor = result.register(self.pop())
		self._gc_protected.add(descriptor)
		try:
			result.register(self.malloc(len(value) + 1, managed=True))
			if result.error:
				self.free(descriptor, Result())
				return None
			chars = result.register(self.pop())
		finally:
			self._gc_protected.discard(descriptor)
		self.data_memory[descriptor] = chars
		self.data_memory[descriptor + 1] = len(value) + 1
		self.data_memory[descriptor + 2] = len(value) + 1
		self.write_mem_string(chars, value)
		return descriptor

	def write_string_descriptor(self, descriptor: int, value: str, result: Result) -> bool:
		if not 0 <= descriptor <= len(self.data_memory) - 3:
			result.fail(self._error("Invalid string descriptor"))
			return False
		old_chars = self.data_memory[descriptor]
		capacity = self.data_memory[descriptor + 2]
		required = len(value) + 1
		if capacity < required or not 0 <= old_chars <= len(self.data_memory) - max(1, capacity):
			self._gc_protected.add(descriptor)
			try:
				result.register(self.malloc(required, managed=True))
				if result.error:
					return False
				chars = result.register(self.pop())
			finally:
				self._gc_protected.discard(descriptor)
			if old_chars in self.allocations:
				self.free(old_chars, Result())
			self.data_memory[descriptor] = chars
			self.data_memory[descriptor + 2] = required
		else:
			chars = old_chars
		self.data_memory[descriptor + 1] = required
		self.write_mem_string(chars, value)
		return True

	def _error(self, message: str) -> VMError:
		position = self._binary_position
		return VMError(message, position.copy(), position.copy())

	def run(self) -> Result:
		res = Result()
		self.start_time = time.monotonic()
		self.stack = [0] * STACK_SIZE
		self.call_stack.clear()
		self.cr = 0
		self.im = TRUE
		self.fp = 0
		self.ip = 0
		self.heap_pointer = self.heap_start
		self.sp = 0
		self.exit_code = 0
		self.halt_requested = False
		self.max_sp = 0

		self.free_list = self._fresh_free_list()
		self.allocations = {}
		self._managed_allocations.clear()
		self._gc_protected.clear()
		self._words_since_gc = 0
		self.gc_runs = 0
		self.gc_reclaimed_words = 0
		instruction_count = 0

		try:
			while self.ip < len(self.instructions):
				if instruction_count & 0xFF == 0 and self.cancel_event.is_set():
					break
				exec_res = self.execute(self.instructions[self.ip])
				instruction_count += 1
				# print(self.stack[: self.sp][:32])
				# print(self.data_memory[8192 : 8192 + 16])
				# print("SP:", self.sp)
				# print("FP:", self.fp)

				if exec_res.error:
					return exec_res
				if self.halt_requested:
					break

				should_continue: bool = exec_res.value
				if not should_continue:
					break

				self.ip += 1
				if self.sp > self.max_sp:
					self.max_sp = self.sp

				# process window events if the window is alive
				if self.ip % 200 == 0 and self.root:
					try:
						self.root.update()
					except Exception:
						pass

			return res.success(self.stack[:self.sp])
		finally:
			self.close_graphics_window()
			self.devices.files.close_all()

	def push(self, value) -> None:
		if self.sp >= len(self.stack):
			raise RuntimeError("VM stack overflow")
		self.stack[self.sp] = value
		self.sp += 1

	def pop(self) -> Result:
		res = Result()
		if self.sp <= 0:
			return res.fail(self._error("Stack underflow"))

		self.sp -= 1
		return res.success(self.stack[self.sp])

	def check_stack(self, n: int) -> Result:
		if n < 0 or self.sp < n:
			return Result().fail(self._error("Stack underflow"))
		return Result().success(None)

	def _malloc_from_free_list(self, words: int, result: Result, managed: bool) -> bool:
		for i, (start, size) in enumerate(self.free_list):
			if size >= words:
				ptr = start

				self.allocations[ptr] = words
				if managed:
					self._managed_allocations.add(ptr)

				if size == words:
					self.free_list.pop(i)
				else:
					self.free_list[i] = (start + words, size - words)

				self._words_since_gc += words
				self.push(ptr)
				result.success(True)
				return True
		return False

	@staticmethod
	def _allocation_for_value(
		value: int,
		starts: list[int],
		allocations: dict[int, int],
	) -> int | None:
		if not isinstance(value, int) or not starts:
			return None
		index = bisect_right(starts, value) - 1
		if index < 0:
			return None
		start = starts[index]
		return start if value < start + allocations[start] else None

	def collect_garbage(self) -> int:
		"""Conservatively reclaim heap blocks no live Xe value can reach."""
		if not self.allocations:
			self._words_since_gc = 0
			return 0

		starts = sorted(self.allocations)
		marked: set[int] = set()
		pending: list[int] = []

		def mark(value: int) -> None:
			allocation = self._allocation_for_value(value, starts, self.allocations)
			if allocation is not None and allocation not in marked:
				marked.add(allocation)
				pending.append(allocation)

		for value in self.data_memory[: min(self.heap_start, len(self.data_memory))]:
			mark(value)
		for value in self.stack[: self.sp]:
			mark(value)
		for value in self._gc_protected:
			mark(value)
		for pointer in self.allocations.keys() - self._managed_allocations:
			mark(pointer)

		while pending:
			start = pending.pop()
			for value in self.data_memory[start : start + self.allocations[start]]:
				mark(value)

		dead = [
			pointer
			for pointer in starts
			if pointer in self._managed_allocations and pointer not in marked
		]
		reclaimed = sum(self.allocations[pointer] for pointer in dead)
		for pointer in dead:
			self.free(pointer, Result())

		self._words_since_gc = 0
		self.gc_runs += 1
		self.gc_reclaimed_words += reclaimed
		return reclaimed

	def malloc(self, words: int, managed: bool = False):
		res = Result()
		if words <= 0:
			return res.fail(self._error("Invalid allocation size"))

		if self._words_since_gc >= GC_ALLOCATION_INTERVAL:
			self.collect_garbage()
		if self._malloc_from_free_list(words, res, managed):
			return res

		self.collect_garbage()
		if self._malloc_from_free_list(words, res, managed):
			return res
		return res.fail(self._error("Out of memory"))

	def free(self, pointer: int, result: Result) -> bool:
		words = self.allocations.pop(pointer, None)
		if words is None:
			result.fail(self._error("Invalid free"))
			return False
		self._managed_allocations.discard(pointer)

		i = 0
		while i < len(self.free_list) and self.free_list[i][0] < pointer:
			i += 1
		self.free_list.insert(i, (pointer, words))
		if i > 0:
			previous_start, previous_size = self.free_list[i - 1]
			current_start, current_size = self.free_list[i]
			if previous_start + previous_size == current_start:
				self.free_list[i - 1] = (previous_start, previous_size + current_size)
				self.free_list.pop(i)
				i -= 1
		if i + 1 < len(self.free_list):
			current_start, current_size = self.free_list[i]
			next_start, next_size = self.free_list[i + 1]
			if current_start + current_size == next_start:
				self.free_list[i] = (current_start, current_size + next_size)
				self.free_list.pop(i + 1)
		return True

	def execute(self, instruction: int) -> Result:
		res = Result()
		pos = self._binary_position

		ins_type = instruction >> 32
		ins_mod = (instruction >> 16) & 0xFFFF
		ins_arg = instruction & 0xFFFF
		ins_arg32 = (ins_mod << 16) | ins_arg
		# print(decode_instruction(instruction))

		if ins_type == 0:  # PUSH
			self.push(ins_arg32)

		if ins_type == 1:  # Other Stack Instructions
			match ins_mod:
				case 0:  # LOAD
					self.push(self.data_memory[ins_arg])
				case 1:  # STORE
					value = res.register(self.pop())
					if res.error:
						return res

					self.data_memory[ins_arg] = value
				case 2:  # POP
					for _ in range(ins_arg):
						res.register(self.pop())
						if res.error:
							return res
				case 3:  # DUP
					res.register(self.check_stack(ins_arg + 1))
					if res.error:
						return res

					self.push(self.stack[self.sp - ins_arg - 1])
				case 4:  # SWAP
					res.register(self.check_stack(2))
					if res.error:
						return res

					b = res.register(self.pop())
					a = res.register(self.pop())

					self.push(b)
					self.push(a)
				case 5:  # OVER
					res.register(self.check_stack(2))
					if res.error:
						return res

					self.push(self.stack[self.sp - 2])
				case 6:  # ROT
					res.register(self.check_stack(3))
					if res.error:
						return res

					c = res.register(self.pop())
					b = res.register(self.pop())
					a = res.register(self.pop())

					self.push(c)
					self.push(a)
					self.push(b)
				case 7:  # LOADIND
					addr = res.register(self.pop())
					if res.error:
						return res
					if not 0 <= addr < len(self.data_memory):
						return res.fail(self._error("Data memory out of bounds"))
					self.push(self.data_memory[addr])
				case 8:  # STOREIND
					value = res.register(self.pop())
					addr = res.register(self.pop())

					if res.error:
						return res
					if not 0 <= addr < len(self.data_memory):
						return res.fail(self._error("Data memory out of bounds"))
					self.data_memory[addr] = value
				case 9:  # PUSHFP
					self.push(self.fp)
				case 10:  # POPFP
					if not 0 <= self.fp < len(self.stack):
						return res.fail(self._error("Frame pointer out of bounds"))
					self.fp = self.stack[self.fp]
				case 11:  # SETFP
					self.fp = self.sp - 1
				case 12:  # LOADFP
					addr = ins_arg
					if res.error:
						return res
					self.fp = self.data_memory[addr]
				case 13:  # STOREFP
					addr = ins_arg
					if res.error:
						return res
					self.data_memory[addr] = self.fp
				case 14:  # LOADSP
					offset = ins_arg
					if ins_arg > 0x7FFF:
						offset -= 0x10000
					stack_index = self.fp - offset
					if not 0 <= stack_index < self.sp:
						return res.fail(self._error("Stack frame load out of bounds"))
					self.push(self.stack[stack_index])
				case 15:  # STORESP
					offset = ins_arg
					if ins_arg > 0x7FFF:
						offset -= 0x10000
					value = res.register(self.pop())
					if res.error:
						return res
					stack_index = self.fp - offset
					if not 0 <= stack_index < len(self.stack):
						return res.fail(self._error("Stack frame store out of bounds"))
					self.stack[stack_index] = value
				case 16:  # LEAVE
					self.sp = self.fp + 2

		if ins_type == 2:  # Conversion
			res.register(self.check_stack(1))
			if res.error:
				return res
			value = self.stack[self.sp - 1]
			if ins_mod != 1 and value > 0x7fffffff:
				value -= 0x100000000
			if ins_mod == 0 and ins_arg == 1:  # I2F
				self.stack[self.sp - 1] = float_to_u32(float(value))
			elif ins_mod == 0 and ins_arg == 2:  # I2B
				self.stack[self.sp - 1] = (
					FALSE if value == 0 else TRUE
				)
			elif ins_mod == 1 and ins_arg == 0:  # F2I
				self.stack[self.sp - 1] = int(u32_to_float(value))
			elif ins_mod == 1 and ins_arg == 2:  # F2B
				self.stack[self.sp - 1] = FALSE if u32_to_float(value) == 0 else TRUE
			elif ins_mod == 2 and ins_arg == 0:  # B2I
				pass
			elif ins_mod == 2 and ins_arg == 1:  # B2F
				self.stack[self.sp - 1] = float_to_u32(float(value))

		if ins_type == 3:  # Math
			is_float_op = ins_mod % 2 == 1
			val = 0

			if ins_mod < 2:
				b = res.register(self.pop())
				a = res.register(self.pop())
				if res.error:
					return res

				if is_float_op:
					b = u32_to_float(b)
					a = u32_to_float(a)
				else:
					if a > 0x7FFFFFFF:
						a -= 0x100000000
					if b > 0x7FFFFFFF:
						b -= 0x100000000
				val = 0

				match ins_arg:
					case 0:
						val = a + b
						self.cr = TRUE if not is_float_op and a + b > TRUE else FALSE
					case 1:
						val = a - b
						self.cr = TRUE if not is_float_op and a < b else FALSE
					case 2:
						val = a * b
						self.cr = TRUE if not is_float_op and a * b > TRUE else FALSE
					case 3:
						if b == 0:
							return res.fail(VMError("Division by 0", pos.copy(), pos.copy()))
						val = a / b if is_float_op else int(a / b)
					case 4:
						if b == 0:
							return res.fail(VMError("Division by 0", pos.copy(), pos.copy()))
						val = a % b
						self.cr = FALSE
					case 5:
						if is_float_op:
							try:
								val = math.pow(a, b)
							except ValueError:
								return res.fail(self._error("pow domain error"))
							except OverflowError:
								return res.fail(self._error("pow overflow"))
							if not math.isfinite(val):
								return res.fail(self._error("pow overflow"))
						else:
							if b < 0:
								return res.fail(self._error("integer exponent must be non-negative"))
							self.cr = TRUE if integer_power_overflows_u32(a, b) else FALSE
							val = pow(a & TRUE, b, 1 << 32)
					case 6:
						if not is_float_op:
							val = a & b
						else:
							t = res.register(self.pop())
							if res.error:
								return res
							t = u32_to_float(t)

							val = a + (b - a) * t
						self.cr = 0
					case 7:
						if not is_float_op:
							val = a | b
						self.cr = 0
					case 8:
						if not is_float_op:
							val = a ^ b
						self.cr = 0
					case 0x11:
						val = TRUE * int(a < b)
						self.cr = 0
					case 0x12:
						val = TRUE * int(a == b)
						self.cr = 0
					case 0x13:
						val = TRUE * int(a <= b)
						self.cr = 0
					case 0x14:
						val = TRUE * int(a > b)
						self.cr = 0
					case 0x15:
						val = TRUE * int(a != b)
						self.cr = 0
					case 0x16:
						val = TRUE * int(a >= b)
						self.cr = 0
					case 0x17:
						if not is_float_op:
							self.push(a)
							self.push(b)
							val = self.cr
			else:
				a = res.register(self.pop())
				if res.error:
					return res

				if is_float_op:
					a = u32_to_float(a)

				match ins_arg:
					case 0:
						val = a + 1
					case 1:
						val = a - 1
					case 2:
						val = -a
					case 3:
						if not is_float_op:
							val = ~a
					case 4:
						if is_float_op:
							val = math.sin(math.radians(a))
					case 5:
						if is_float_op:
							val = math.cos(math.radians(a))
					case 6:
						if is_float_op:
							val = math.tan(math.radians(a))
					case 7:
						if is_float_op:
							if a < -1.0 or a > 1.0:
								return res.fail(self._error("asin domain error"))
							val = math.degrees(math.asin(a))
					case 8:
						if is_float_op:
							if a < -1.0 or a > 1.0:
								return res.fail(self._error("acos domain error"))
							val = math.degrees(math.acos(a))
					case 9:
						if is_float_op:
							val = math.degrees(math.atan(a))
					case 10:
						if a < 0:
							return res.fail(self._error("sqrt domain error"))
						val = math.sqrt(a)
						if not is_float_op:
							val = int(val)

			if is_float_op:
				try:
					float_value = float(val)
					if not math.isfinite(float_value):
						return res.fail(self._error("floating-point overflow"))
					val = float_to_u32(float_value)
				except (OverflowError, struct.error):
					return res.fail(self._error("floating-point overflow"))
			self.push(val & TRUE)

		if ins_type == 4:  # Branching
			addr = ins_arg
			value = 0
			if ins_mod in (1, 2, 5, 6, 9, 10):
				res.register(self.check_stack(1))
				if res.error:
					return res
				value = res.register(self.pop())
			elif ins_mod in (3, 7):
				res.register(self.check_stack(1))
				if res.error:
					return res
				addr = res.register(self.pop())

			match ins_mod:
				case 0:  # JUMP
					self.ip = addr - 1
				case 1:  # BRZ
					if value == 0:
						self.ip = addr - 1
				case 2:  # BRNZ
					if value != 0:
						self.ip = addr - 1
				case 3:  # JUMPIND
					self.ip = addr - 1
				case 4:  # CALL
					self.call_stack.append(self.ip)
					self.ip = addr - 1
				case 5:  # CALZ
					if value == 0:
						self.call_stack.append(self.ip)
						self.ip = addr - 1
				case 6:  # CALN
					if value != 0:
						self.call_stack.append(self.ip)
						self.ip = addr - 1
				case 7:  # CALLIND
					self.call_stack.append(self.ip)
					self.ip = addr - 1
				case 8:  # RET
					if not self.call_stack:
						return res.fail(self._error("Call stack underflow"))
					self.ip = self.call_stack[-1]
					self.call_stack.pop()
				case 9:  # RETZ
					if value == 0:
						if not self.call_stack:
							return res.fail(self._error("Call stack underflow"))
						self.ip = self.call_stack[-1]
						self.call_stack.pop()
				case 10:  # RETN
					if value != 0:
						if not self.call_stack:
							return res.fail(self._error("Call stack underflow"))
						self.ip = self.call_stack[-1]
						self.call_stack.pop()

		if ins_type == 5:  # System instructions
			match ins_mod:
				case 0:
					return res.success(False)
				case 1:
					return res.success(False)
				case 2:
					self.push(self.im)
				case 3:
					self.im = res.register(self.pop())
					if res.error:
						return res
				# no interrupt handling yet
				case 7:  # SYS
					match ins_arg:
						case SyscallID.OUTPUT_CHARS:
							addr = res.register(self.pop())
							if res.error:
								return res
							try:
								self._output(self.read_mem_string(addr))
							except ValueError as error:
								return res.fail(self._error(str(error)))
						case SyscallID.READ_STRING:
							try:
								value = self.input_handler()
							except (EOFError, KeyboardInterrupt):
								value = ""
							except Exception as error:
								return res.fail(self._error(f"Input failed: {error}"))
							descriptor = self.allocate_string(str(value), res)
							if res.error:
								return res
							self.push(descriptor)
						case SyscallID.CHARS_TO_INT:
							addr = res.register(self.pop())
							if res.error:
								return res
							try:
								self.push(int(self.read_mem_string(addr)) & TRUE)
							except (ValueError, OverflowError) as error:
								return res.fail(self._error(f"Cannot convert characters to int: {error}"))
						case SyscallID.CHARS_TO_FLOAT:
							addr = res.register(self.pop())
							if res.error:
								return res
							try:
								self.push(float_to_u32(float(self.read_mem_string(addr))))
							except (ValueError, OverflowError) as error:
								return res.fail(self._error(f"Cannot convert characters to float: {error}"))
						case SyscallID.INT_TO_CHARS:
							value = res.register(self.pop())
							addr = res.register(self.pop())
							if res.error:
								return res

							if value > 0x7fffffff:
								value -= 0x100000000

							try:
								self.write_mem_string(addr, str(value))
							except ValueError as error:
								return res.fail(self._error(str(error)))
						case SyscallID.FLOAT_TO_CHARS:
							value = res.register(self.pop())
							value = u32_to_float(value)
							addr = res.register(self.pop())
							if res.error:
								return res

							try:
								self.write_mem_string(addr, f"{value:.6f}")
							except ValueError as error:
								return res.fail(self._error(str(error)))
						case SyscallID.BOOL_TO_CHARS:
							value = bool(res.register(self.pop()))
							addr = res.register(self.pop())
							if res.error:
								return res

							try:
								self.write_mem_string(addr, str(value).lower())
							except ValueError as error:
								return res.fail(self._error(str(error)))
						case SyscallID.INT_TO_HEX:
							value = res.register(self.pop())
							addr = res.register(self.pop())
							if res.error:
								return res

							try:
								self.write_mem_string(addr, f"0x{value:08x}")
							except ValueError as error:
								return res.fail(self._error(str(error)))
						case SyscallID.PUT_CHAR:
							value = res.register(self.pop())
							if res.error:
								return res

							self._output(chr(value & 0xFF))
						case SyscallID.STRING_CONCAT:
							str2_addr = res.register(self.pop())
							str1_addr = res.register(self.pop())
							if res.error:
								return res
							try:
								value = self.read_string_descriptor(str1_addr) + self.read_string_descriptor(str2_addr)
							except ValueError as error:
								return res.fail(self._error(str(error)))
							descriptor = self.allocate_string(value, res)
							if res.error:
								return res
							self.push(descriptor)
						case SyscallID.STRING_COMPARE:
							str2_addr = res.register(self.pop())
							str1_addr = res.register(self.pop())
							if res.error:
								return res
							try:
								str1 = self.read_string_descriptor(str1_addr)
								str2 = self.read_string_descriptor(str2_addr)
							except ValueError as error:
								return res.fail(self._error(str(error)))
							self.push(TRUE if str1 == str2 else FALSE)
						case SyscallID.STRING_UPDATE_LENGTH:
							descriptor = res.register(self.pop())
							if res.error:
								return res
							if not 0 <= descriptor <= len(self.data_memory) - 3:
								return res.fail(self._error("Invalid string descriptor"))
							chars = self.data_memory[descriptor]
							capacity = self.data_memory[descriptor + 2]
							if capacity < 1 or not 0 <= chars <= len(self.data_memory) - capacity:
								return res.fail(self._error("Invalid string descriptor capacity"))
							try:
								value = self.read_mem_string(chars, capacity)
							except ValueError as error:
								return res.fail(self._error(str(error)))
							self.data_memory[descriptor + 1] = len(value) + 1
							self.push(descriptor)
						case SyscallID.OS_GET_TICKS:
							self.push(int((time.monotonic() - self.start_time) * 1000) & TRUE)
						case SyscallID.OS_MALLOC:
							words = res.register(self.pop())
							if res.error:
								return res

							return self.malloc(words)
						case SyscallID.OS_FREE:
							ptr = res.register(self.pop())
							if res.error:
								return res
							self.free(ptr, res)
							if res.error:
								return res
						case SyscallID.OS_EXIT:
							code = res.register(self.pop())
							if res.error:
								return res
							self.exit_code = code - 0x100000000 if code > 0x7FFFFFFF else code
							return res.success(False)
						case SyscallID.OS_SLEEP:
							duration = res.register(self.pop())
							if res.error:
								return res
							if duration > 0x7FFFFFFF:
								duration -= 0x100000000
							self.cancel_event.wait(max(0, duration) / 1000.0)
						case SyscallID.REQUEST:
							destination = res.register(self.pop())
							backend_id = res.register(self.pop())
							if res.error:
								return res
							try:
								value = "" if self.request_handler is None else self.request_handler(backend_id)
							except Exception as error:
								return res.fail(self._error(f"Request failed: {error}"))
							if not self.write_string_descriptor(destination, str(value), res):
								return res
							self.push(destination)
						case _:
							if not self.devices.dispatch(ins_arg, self, res):
								return res.fail(
									VMError(
										f"Unknown system call {ins_arg}",
										pos.copy(),
										pos.copy(),
									)
								)
							if res.error:
								return res

		if ins_type == 8:  # Other
			match ins_mod:
				case 0:  # LOOKUP
					src = res.register(self.pop()) - self.text_size
					dst = res.register(self.pop())

					if res.error:
						return res

					if src < 0 or src + ins_arg > len(self.program_memory):
						return res.fail(
							VMError(
								"Program memory out of bounds", pos.copy(), pos.copy()
							)
						)

					if dst < 0 or dst + ins_arg > len(self.data_memory):
						return res.fail(
							VMError(
								"Data memory out of bounds",
								pos.copy(),
								pos.copy(),
							)
						)

					self.data_memory[dst:dst + ins_arg] = self.program_memory[src:src + ins_arg]
				case 1:  # WRITE
					src = res.register(self.pop())
					dst = res.register(self.pop())

					if res.error:
						return res

					if src < 0 or src + ins_arg > len(self.data_memory):
						return res.fail(self._error("Data memory out of bounds"))
					if dst < 0 or dst + ins_arg > len(self.program_memory):
						return res.fail(self._error("Program memory out of bounds"))
					self.program_memory[dst:dst + ins_arg] = self.data_memory[src:src + ins_arg]

		return res.success(True)
