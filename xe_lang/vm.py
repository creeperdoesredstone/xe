import struct
import tkinter as tk
import time
import math
import threading
from xe_lang.helper import Result, VMError, Position
from xe_lang.assembler import INSTRUCTION_MAP

TRUE = 0xFFFFFFFF
FALSE = 0
MAGIC = 0x58424E31  # "XBN1"
VERSION = 1

PALETTE = [
	"#000000",
	"#0000AA",
	"#00AA00",
	"#00AAAA",
	"#AA0000",
	"#AA00AA",
	"#AA5500",
	"#AAAAAA",
	"#555555",
	"#5555FF",
	"#55FF55",
	"#55FFFF",
	"#FF5555",
	"#FF55FF",
	"#FFFF55",
	"#FFFFFF",
]


def u32_to_float(bits: int) -> float:
	return struct.unpack(">f", struct.pack(">I", bits & TRUE))[0]


def float_to_u32(value: float) -> int:
	return struct.unpack(">I", struct.pack(">f", value))[0]


def to_u32(value: int) -> int:
	return value & TRUE


class VM:
	def __init__(self, program: list[int], output_handler=None):
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

		self.program = program[4:]
		self.instructions = program[4:4 + self.text_size]
		self.program_memory = program[4 + self.text_size:]
		self.stack: list = []
		self.call_stack: list = []
		self.ip: int = 0
		self.data_memory: list = [0] * 65536
		self.free_list: list = [
			(0x2000, 0xE000)
		]
		self.allocations: dict[int, int] = {}

		self.fp: int = 0
		self.sp: int = 0
		self.cr: int = 0
		self.im: int = TRUE

		self.labels = {}
		self.start_time = time.time()
		self.output_handler = output_handler  # for ide

		self.width = 240
		self.height = 180
		self.back_buffer = [[0 for _ in range(self.width)] for _ in range(self.height)]
		self.front_buffer = [[0 for _ in range(self.width)] for _ in range(self.height)]

		self.clip_rect = (0, 0, self.width, self.height)

		self.mouse_x = 0
		self.mouse_y = 0
		self.mouse_btn = 0
		self.key_queue = []
		self.keys_down = set()
		self.modifiers = 0

		# for standalone execution
		self.root = None
		self.canvas = None
		self.img = None
		self.canvas_image_id = None
		self.display_img = None
		self.heap_pointer = 0x2000

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

			self.root.update()
		except Exception:
			self.root = None

	def _on_mouse_move(self, event):
		self.mouse_x = max(0, min(self.width - 1, event.x // 3))
		self.mouse_y = max(0, min(self.height - 1, event.y // 3))

	def _on_mouse_press(self, event):
		self.mouse_btn = event.num

	def _on_mouse_release(self, event):
		self.mouse_btn = 0

	def _on_key_press(self, event):
		code = event.keycode
		mod = self._get_mod_state(event.state)
		self.modifiers = mod
		if code not in self.keys_down:
			self.keys_down.add(code)
			self.key_queue.append((1, code, mod))

	def _on_key_release(self, event):
		code = event.keycode
		mod = self._get_mod_state(event.state)
		self.modifiers = mod
		if code in self.keys_down:
			self.keys_down.discard(code)
			self.key_queue.append((2, code, mod))

	def _get_mod_state(self, state):
		mask = 0
		if state & 0x0001:
			mask |= 1
		if state & 0x0004:
			mask |= 2
		if state & 0x0008:
			mask |= 4
		return mask

	def render_front_buffer(self):
		if not self.img or not self.root:
			return
		try:
			pixel_data = " ".join(
				"{"
				+ " ".join(
					PALETTE[self.front_buffer[y][x] % 16] for x in range(self.width)
				)
				+ "}"
				for y in range(self.height)
			)
			self.img.put(pixel_data)

			self.display_img = self.img.zoom(3)
			self.canvas.itemconfig(self.canvas_image_id, image=self.display_img)

			self.root.update_idletasks()
			self.root.update()
		except Exception:
			pass

	def write_pixel(self, x: int, y: int, color_idx: int):
		if (
			self.clip_rect[0] <= x < self.clip_rect[2]
			and self.clip_rect[1] <= y < self.clip_rect[3]
		):
			self.back_buffer[y][x] = color_idx % 16

	def _output(self, text: str):
		if self.output_handler:
			self.output_handler(text)
		else:
			print(text, end="")

	def read_mem_string(self, address: int) -> str:
		chars = []
		while True:
			val = self.data_memory[address]
			if val == 0:
				break
			chars.append(chr(val & 0xFF))
			address += 1
		return "".join(chars)

	def write_mem_string(self, address: int, string: str):
		for char in string:
			self.data_memory[address] = ord(char)
			address += 1
		self.data_memory[address] = 0

	def run(self) -> Result:
		res = Result()
		self.stack.clear()
		self.call_stack.clear()
		self.cr = 0
		self.im = TRUE
		self.fp = 0
		self.ip = 0
		self.heap_pointer = 0x2000

		self.free_list = [
			(0x2000, 0xE000)
		]
		self.allocations = {}

		while self.ip < len(self.instructions):
			self.sp = len(self.stack)
			exec_res = self.execute(self.instructions[self.ip])

			if exec_res.error:
				if self.root:
					try:
						self.root.destroy()
					except Exception:
						pass
				return exec_res

			should_continue: bool = exec_res.value
			if not should_continue:
				break

			self.ip += 1

			# process window events if the window is alive
			if self.ip % 200 == 0 and self.root:
				try:
					self.root.update()
				except Exception:
					pass

		if self.root:
			try:
				self.root.destroy()
			except Exception:
				pass

		return res.success(self.stack)

	def pop(self) -> Result:
		res = Result()
		pos = Position(0, 0, 0, "<bin>", "")
		if not self.stack:
			return res.fail(VMError("Stack underflow", pos.copy(), pos.copy()))
		val = self.stack.pop()
		self.sp = len(self.stack)
		return res.success(val)

	def check_stack(self, n: int) -> Result:
		pos = Position(0, 0, 0, "<bin>", "")
		if len(self.stack) < n:
			return Result().fail(VMError("Stack underflow", pos.copy(), pos.copy()))
		return Result().success(None)

	def execute(self, instruction: int) -> Result:
		res = Result()
		pos = Position(0, 0, 0, "<bin>", "")

		ins_type = instruction >> 32
		ins_mod = (instruction >> 16) & 0xFFFF
		ins_arg = instruction & 0xFFFF
		ins_arg32 = (ins_mod << 16) | ins_arg

		if ins_type == 0:  # PUSH
			self.stack.append(ins_arg32)

		if ins_type == 1:  # Other Stack Instructions
			match ins_mod:
				case 0:  # LOAD
					self.stack.append(self.data_memory[ins_arg])
				case 1:  # STORE
					value = res.register(self.pop())
					if res.error:
						return res

					self.data_memory[ins_arg] = value
				case 2:  # POP
					res.register(self.pop())
					if res.error:
						return res
				case 3:  # DUP
					res.register(self.check_stack(1))
					if res.error:
						return res

					self.stack.append(self.stack[-1])
				case 4:  # SWAP
					res.register(self.check_stack(2))
					if res.error:
						return res

					b = res.register(self.pop())
					a = res.register(self.pop())

					self.stack.append(b)
					self.stack.append(a)
				case 5:  # OVER
					res.register(self.check_stack(2))
					if res.error:
						return res

					self.stack.append(self.stack[-2])
				case 6:  # ROT
					res.register(self.check_stack(3))
					if res.error:
						return res

					c = res.register(self.pop())
					b = res.register(self.pop())
					a = res.register(self.pop())

					self.stack.append(c)
					self.stack.append(a)
					self.stack.append(b)
				case 7:  # LOADIND
					addr = res.register(self.pop())
					if res.error:
						return res
					self.stack.append(self.data_memory[addr])
				case 8:  # STOREIND
					value = res.register(self.pop())
					addr = res.register(self.pop())

					if res.error:
						return res
					self.data_memory[addr] = value
				case 9:  # PUSHFP
					self.stack.append(self.fp)
				case 10:  # POPFP
					fp = res.register(self.pop())
					if res.error:
						return res
					self.fp = fp
				case 11:  # SETFP
					self.fp = self.sp
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

		if ins_type == 2:  # Conversion
			res.register(self.check_stack(1))
			if res.error:
				return res
			if ins_mod == 0 and ins_arg == 1:  # I2F
				self.stack[-1] = float_to_u32(float(self.stack[-1]))
			elif ins_mod == 0 and ins_arg == 2:  # I2B
				self.stack[-1] = FALSE if self.stack[-1] == 0 else TRUE
			elif ins_mod == 1 and ins_arg == 0:  # F2I
				self.stack[-1] = int(u32_to_float(self.stack[-1]))
			elif ins_mod == 1 and ins_arg == 2:  # F2B
				self.stack[-1] = FALSE if u32_to_float(self.stack[-1]) == 0 else TRUE
			elif ins_mod == 2 and ins_arg == 0:  # B2I
				pass
			elif ins_mod == 2 and ins_arg == 1:  # B2F
				self.stack[-1] = float_to_u32(float(self.stack[-1]))

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
							return VMError("Division by 0", pos.copy(), pos.copy())
						val = a / b if is_float_op else int(a / b)
					case 4:
						if b == 0:
							return VMError("Division by 0", pos.copy(), pos.copy())
						val = a % b
						self.cr = FALSE
					case 5:
						val = a**b
						self.cr = TRUE if not is_float_op and a**b > TRUE else FALSE
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
							val = math.asin(math.radians(a))
					case 8:
						if is_float_op:
							val = math.acos(math.radians(a))
					case 9:
						if is_float_op:
							val = math.atan(math.radians(a))
					case 10:
						val = math.sqrt(a)
						if not is_float_op:
							val = int(val)

			if is_float_op:
				val = float_to_u32(float(val))
			self.stack.append(val & TRUE)

		if ins_type == 4:  # Branching
			addr = ins_arg
			if ins_mod % 3 != 0:
				res.register(self.check_stack(1))
				if res.error: return res
				value = res.register(self.pop())

			match ins_mod:
				case 0: # JUMP
					self.ip = addr - 1
				case 1: # BRZ
					if value == 0:
						self.ip = addr - 1
				case 2: # BRNZ
					if value != 0:
						self.ip = addr - 1
				case 3: # CALL
					self.call_stack.append(self.ip)
					self.ip = addr - 1
				case 4: # CALZ
					if value == 0:
						self.call_stack.append(self.ip)
						self.ip = addr - 1
				case 5: # CALN
					if value != 0:
						self.call_stack.append(self.ip)
						self.ip = addr - 1
				case 6: # RET
					self.ip = self.call_stack[-1]
					self.call_stack.pop()
				case 7: # RETZ
					if value == 0:
						self.ip = self.call_stack[-1]
						self.call_stack.pop()
				case 8: # RETN
					if value != 0:
						self.ip = self.call_stack[-1]
						self.call_stack.pop()

		if ins_type == 5:  # System instructions
			match ins_mod:
				case 0: return res.success(False)
				case 1: return res.success(False)
				case 2: self.stack.append(self.im)
				case 3:
					self.im = res.register(self.pop())
					if res.error: return res
				# no interrupt handling yet
				case 7: # SYS
					match ins_arg:
						case 1:  # OUTPUT_CHARS
							addr = res.register(self.pop())
							if res.error: return res
							self._output(self.read_mem_string(addr))
						case 2:  # READ_CHARS
							...
						case 3:  # CHARS2INT
							addr = res.register(self.pop())
							if res.error: return res
							string = self.read_mem_string(addr)
							self.stack.append(int(string))
						case 4:  # CHARS2FLOAT
							addr = res.register(self.pop())
							if res.error: return res
							string = self.read_mem_string(addr)
							self.stack.append(float_to_u32(float(string)))
						case 5:  # INT2CHARS
							value = res.register(self.pop())
							addr = res.register(self.pop())
							if res.error: return res

							self.write_mem_string(addr, str(value))
						case 6:  # FLOAT2CHARS
							value = res.register(self.pop())
							value = u32_to_float(value)
							addr = res.register(self.pop())
							if res.error: return res

							self.write_mem_string(addr, str(value))
						case 7:  # BOOL2CHARS
							value = bool(res.register(self.pop()))
							addr = res.register(self.pop())
							if res.error: return res

							self.write_mem_string(addr, str(value).lower())
						case 8:  # INT2HEX
							value = res.register(self.pop())
							addr = res.register(self.pop())
							if res.error: return res

							self.write_mem_string(addr, f"{value:08X}")
						case 9:  # PUT_CHAR
							value = res.register(self.pop())
							if res.error: return res

							self._output(chr(value))
						case 21:  # MALLOC
							words = res.register(self.pop())
							if res.error:
								return res

							if words <= 0:
								return res.fail(VMError("Invalid allocation size", pos.copy(), pos.copy()))

							for i, (start, size) in enumerate(self.free_list):
								if size >= words:
									ptr = start

									self.allocations[ptr] = words

									if size == words:
										self.free_list.pop(i)
									else:
										self.free_list[i] = (start + words, size - words)

									self.stack.append(ptr)
									break
							else:
								return res.fail(VMError("Out of memory", pos.copy(), pos.copy()))
						case 22:  # FREE
							ptr = res.register(self.pop())
							if res.error:
								return res

							words = self.allocations.pop(ptr, None)
							if words is None:
								return res.fail(VMError("Invalid free", pos.copy(), pos.copy()))

							i = 0
							while i < len(self.free_list) and self.free_list[i][0] < ptr:
								i += 1

							self.free_list.insert(i, (ptr, words))

							if i > 0:
								prev_start, prev_size = self.free_list[i - 1]
								curr_start, curr_size = self.free_list[i]

								if prev_start + prev_size == curr_start:
									self.free_list[i - 1] = (prev_start, prev_size + curr_size)
									self.free_list.pop(i)
									i -= 1

							if i + 1 < len(self.free_list):
								curr_start, curr_size = self.free_list[i]
								next_start, next_size = self.free_list[i + 1]

								if curr_start + curr_size == next_start:
									self.free_list[i] = (curr_start, curr_size + next_size)
									self.free_list.pop(i + 1)

		if ins_type == 8:  # Other
			match ins_mod:
				case 0: # LOOKUP
					src = res.register(self.pop()) - self.text_size
					dst = res.register(self.pop())

					print(f"LOOKUP src={src} dst={dst} size={ins_arg}")
					print(self.program_memory[src:src+ins_arg])

					if res.error:
						return res

					if src < 0 or src + ins_arg > len(self.program_memory):
						return res.fail(VMError(
							"Program memory out of bounds",
							pos.copy(),
							pos.copy()
						))
					
					if dst < 0 or dst + ins_arg > len(self.data_memory):
						return res.fail(
							VMError(
								"Data memory out of bounds",
								pos.copy(),
								pos.copy(),
							)
						)

					for i in range(ins_arg):
						self.data_memory[dst + i] = self.program_memory[src + i]
				case 1:      # WRITE
					src = res.register(self.pop())
					dst = res.register(self.pop())

					if res.error:
						return res

					for i in range(ins_arg):
						self.program_memory[dst + i] = self.data_memory[src + i]

		self.sp = len(self.stack)
		return res.success(True)
