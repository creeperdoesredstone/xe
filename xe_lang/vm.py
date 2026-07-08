import struct
import tkinter as tk
import time
import math
import threading
from xe_lang.helper import Result, VMError, Position

TRUE = 0xFFFFFFFF
FALSE = 0

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
	def __init__(self, instructions: list[tuple], output_handler=None) -> None:
		self.instructions: list[tuple] = instructions
		self.stack: list = []
		self.call_stack: list = []
		self.ip: int = 0
		self.memory: list = [0] * 65536

		self.fp: int = 0
		self.sp: int = 0
		self.offset: int = 0
		self.actual_offset: int = 0
		self.enable_offset: bool = False

		self.labels = {}
		self.start_time = time.time()
		self.output_handler = output_handler # for ide

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
		self.heap_pointer = 32768

	def process_labels(self):
		self.labels = {}
		processed_instructions = []
		line = 0
		for inst in self.instructions:
			opcode = inst[2]
			if opcode[0] == ":":
				self.labels[opcode] = line
			else:
				line += 1
				processed_instructions.append(inst)

		return processed_instructions

	def init_graphics_window(self):
		"""Creates the GUI display canvas lazily only when requested by a syscall."""
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
		"""Output text to handler if available (IDE), otherwise to stdout."""
		if self.output_handler:
			self.output_handler(text)
		else:
			print(text, end="")

	def read_mem_string(self, address: int) -> str:
		chars = []
		while True:
			val = self.memory[address]
			if val == 0:
				break
			chars.append(chr(val & 0xFF))
			address += 1
		return "".join(chars)

	def write_mem_string(self, address: int, string: str):
		for char in string:
			self.memory[address] = ord(char)
			address += 1
		self.memory[address] = 0

	def run(self) -> Result:
		res = Result()
		self.instructions = self.process_labels()
		self.stack.clear()

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

	def pop(self, start_pos: Position, end_pos: Position) -> Result:
		res = Result()
		if not self.stack:
			return res.fail(VMError("Stack underflow", start_pos, end_pos))
		val = self.stack.pop()
		self.sp = len(self.stack)
		return res.success(val)

	def execute(self, instruction: tuple) -> Result:
		res = Result()

		start_pos = instruction[0]
		end_pos = instruction[1]
		op = instruction[2]
		args = instruction[3:]

		# resolve an immediate argument if it references a label
		def get_arg(index: int = 0):
			if index < len(args):
				target = args[index]
				if f":{target}" in self.labels:
					return self.labels[f":{target}"]
				return target
			return None

		match op:
			case "PUSH":
				self.stack.append(get_arg(0))
			case "POP":
				res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
			case "DUP":
				if not self.stack:
					return res.fail(VMError("Stack underflow", start_pos, end_pos))
				self.stack.append(self.stack[-1])
			case "SWAP":
				if len(self.stack) < 2:
					return res.fail(VMError("Stack underflow", start_pos, end_pos))
				self.stack[-1], self.stack[-2] = self.stack[-2], self.stack[-1]
			case "OVER":
				if len(self.stack) < 2:
					return res.fail(VMError("Stack underflow", start_pos, end_pos))
				self.stack.append(self.stack[-2])
			case "ROT":
				if len(self.stack) < 3:
					return res.fail(VMError("Stack underflow", start_pos, end_pos))
				c, b, a = self.stack.pop(), self.stack.pop(), self.stack.pop()
				self.stack.extend([c, a, b])

			case "PUSHFP":
				self.stack.append(self.fp)
			case "POPFP":
				val = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.fp = val
			case "SETFP":
				self.fp = self.sp
			case "LOADSP":
				offset = self.fp + (self.sp - get_arg(0))
				self.stack.append(self.memory[offset])
			case "STORESP":
				val = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				offset = self.fp + (self.sp - get_arg(0))
				self.memory[offset] = val

			case "I2F":
				value = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.stack.append(float_to_u32(float(value)))
			case "F2I":
				value = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.stack.append(int(u32_to_float(value)))
			case "BOOL":
				value = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.stack.append(TRUE if value != 0 else FALSE)

			case "ADDI" | "SUBI" | "MULI" | "DIVI" | "MODI" | "POWI":
				b = res.register(self.pop(start_pos, end_pos))
				a = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				if op == "ADDI":
					result = a + b
				elif op == "SUBI":
					result = a - b
				elif op == "MULI":
					result = a * b
				elif op == "DIVI":
					if b == 0:
						return res.fail(VMError("Division by 0", start_pos, end_pos))
					result = a // b
				elif op == "MODI":
					if b == 0:
						return res.fail(VMError("Division by 0", start_pos, end_pos))
					result = a % b
				elif op == "POWI":
					result = a**b
				self.stack.append(to_u32(result))

			case "ADDF" | "SUBF" | "MULF" | "DIVF" | "MODF" | "POWF":
				b_bits = res.register(self.pop(start_pos, end_pos))
				a_bits = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				a, b = u32_to_float(a_bits), u32_to_float(b_bits)
				if op == "ADDF":
					result = a + b
				elif op == "SUBF":
					result = a - b
				elif op == "MULF":
					result = a * b
				elif op == "DIVF":
					if b == 0:
						return res.fail(VMError("Division by 0", start_pos, end_pos))
					result = a / b
				elif op == "MODF":
					if b == 0:
						return res.fail(VMError("Division by 0", start_pos, end_pos))
					result = a % b
				elif op == "POWF":
					result = a**b
				self.stack.append(float_to_u32(result))

			case "IEQ" | "INE" | "ILT" | "ILE" | "IGT" | "IGE":
				b = res.register(self.pop(start_pos, end_pos))
				a = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				if op[1:] == "EQ":
					ok = a == b
				elif op[1:] == "NE":
					ok = a != b
				elif op[1:] == "LT":
					ok = a < b
				elif op[1:] == "LE":
					ok = a <= b
				elif op[1:] == "GT":
					ok = a > b
				elif op[1:] == "GE":
					ok = a >= b
				self.stack.append(TRUE if ok else FALSE)

			case "FEQ" | "FNE" | "FLT" | "FLE" | "FGT" | "FGE":
				b = res.register(self.pop(start_pos, end_pos))
				a = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				a, b = u32_to_float(a), u32_to_float(b)
				if op == "FEQ":
					ok = a == b
				elif op == "FNE":
					ok = a != b
				elif op == "FLT":
					ok = a < b
				elif op == "FLE":
					ok = a <= b
				elif op == "FGT":
					ok = a > b
				elif op == "FGE":
					ok = a >= b
				self.stack.append(TRUE if ok else FALSE)

			case "AND":
				b = res.register(self.pop(start_pos, end_pos))
				a = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.stack.append(a & b)
			case "OR":
				b = res.register(self.pop(start_pos, end_pos))
				a = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.stack.append(a | b)
			case "XOR":
				b = res.register(self.pop(start_pos, end_pos))
				a = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.stack.append(a ^ b)
			case "NOT":
				a = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.stack.append((~a) & TRUE)

			case "INCI" | "DECI" | "INCF" | "DECF":
				a = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				if op == "INCI":
					self.stack.append(to_u32(a + 1))
				elif op == "DECI":
					self.stack.append(to_u32(a - 1))
				elif op == "INCF":
					self.stack.append(float_to_u32(u32_to_float(a) + 1.0))
				elif op == "DECF":
					self.stack.append(float_to_u32(u32_to_float(a) - 1.0))
			case "NEGI":
				value = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.stack.append(to_u32(-value))
			case "NEGF":
				bits = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.stack.append(float_to_u32(-u32_to_float(bits)))

			case "JUMP":
				self.ip = get_arg(0) - 1
			case "BRZ":
				top = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				if top == 0:
					self.ip = get_arg(0) - 1
			case "BRNZ":
				top = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				if top != 0:
					self.ip = get_arg(0) - 1
			case "CALL":
				self.call_stack.append(self.ip + 1)
				self.ip = get_arg(0) - 1
			case "CALZ":
				top = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				if top == 0:
					self.call_stack.append(self.ip + 1)
					self.ip = get_arg(0) - 1
			case "CALN":
				top = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				if top != 0:
					self.call_stack.append(self.ip + 1)
					self.ip = get_arg(0) - 1
			case "RET":
				if not self.call_stack:
					return res.fail(VMError("Call stack underflow", start_pos, end_pos))
				self.ip = self.call_stack.pop() - 1
				pop_count = get_arg(0) if args else 0
				for _ in range(pop_count):
					res.register(self.pop(start_pos, end_pos))
			case "RETZ":
				top = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				if top == 0:
					if not self.call_stack:
						return res.fail(VMError("Call stack underflow", start_pos, end_pos))
					self.ip = self.call_stack.pop() - 1
					pop_count = get_arg(0) if args else 0
					for _ in range(pop_count):
						res.register(self.pop(start_pos, end_pos))
			case "RETN":
				top = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				if top != 0:
					if not self.call_stack:
						return res.fail(VMError("Call stack underflow", start_pos, end_pos))
					self.ip = self.call_stack.pop() - 1
					pop_count = get_arg(0) if args else 0
					for _ in range(pop_count):
						res.register(self.pop(start_pos, end_pos))

			case "HALT":
				return res.success(False)

			case "STORE":
				value = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.memory[get_arg(0) + self.offset] = value
			case "LOAD":
				self.stack.append(self.memory[get_arg(0) + self.offset])
			case "LOADIND":
				address = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.stack.append(self.memory[address + self.offset])
			case "STREIND":
				b = res.register(self.pop(start_pos, end_pos))
				a = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.memory[a + self.offset] = b

			case "OFFSET":
				self.actual_offset = get_arg(0)
				self.offset = self.actual_offset if self.enable_offset else 0
			case "PUSHOFF":
				self.stack.append(self.offset)
			case "POPOFF":
				value = res.register(self.pop(start_pos, end_pos))
				if res.error:
					return res
				self.actual_offset = value
				self.offset = self.actual_offset if self.enable_offset else 0
			case "ENABOFF":
				self.enable_offset = True
				self.offset = self.actual_offset
			case "DISABOFF":
				self.enable_offset = False
				self.offset = 0

			case "SYS":
				sys_code = get_arg(0)

				if 40 <= sys_code <= 65:
					self.init_graphics_window()

				if sys_code == 1: # OUTPUT_CHARS
					addr = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					self._output(self.read_mem_string(addr))
				elif sys_code == 2: # READ_CHARS
					addr = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					self.write_mem_string(addr, input())
				elif sys_code == 3: # CHARS2INT
					addr = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					self.stack.append(int(self.read_mem_string(addr)))
				elif sys_code == 4: # CHARS2FLOAT
					addr = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					self.stack.append(float_to_u32(float(self.read_mem_string(addr))))
				elif sys_code == 5: # INT2CHARS
					val = res.register(self.pop(start_pos, end_pos))
					addr = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					self.write_mem_string(addr, str(val))
				elif sys_code == 6: # FLOAT2CHARS
					val = res.register(self.pop(start_pos, end_pos))
					addr = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					self.write_mem_string(addr, str(round(u32_to_float(val), 6)))
				elif sys_code == 7: # BOOL2CHARS
					val = res.register(self.pop(start_pos, end_pos))
					addr = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					self.write_mem_string(addr, "true" if val == TRUE else "false")
				elif sys_code == 8: # INT2HEX
					val = res.register(self.pop(start_pos, end_pos))
					addr = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					self.write_mem_string(addr, f"0x{val:04X}")
				elif sys_code == 9: # PUT_CHAR
					val = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					self._output(chr(val & 0xFF))

				elif sys_code == 10: # STR_CONCAT
					dest, str2, str1 = (
						res.register(self.pop(start_pos, end_pos)),
						res.register(self.pop(start_pos, end_pos)),
						res.register(self.pop(start_pos, end_pos)),
					)
					if res.error:
						return res
					self.write_mem_string(
						dest, self.read_mem_string(str1) + self.read_mem_string(str2)
					)
				elif sys_code == 11:
					str2, str1 = res.register(self.pop(start_pos, end_pos)), res.register(
						self.pop(start_pos, end_pos)
					)
					if res.error:
						return res
					self.stack.append(
						TRUE
						if self.read_mem_string(str1) == self.read_mem_string(str2)
						else FALSE
					)
				elif sys_code == 12:
					str2, str1 = res.register(self.pop(start_pos, end_pos)), res.register(
						self.pop(start_pos, end_pos)
					)
					if res.error:
						return res
					self.write_mem_string(str2, self.read_mem_string(str1))
				elif sys_code == 20:
					self.stack.append(int((time.time() - self.start_time) * 1000))
				elif sys_code == 21:
					size = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					if self.heap_pointer + size >= 65536:
						return res.fail(VMError("HeapOverflow", start_pos, end_pos))
					self.stack.append(self.heap_pointer)
					self.heap_pointer += size
				elif sys_code == 22:
					res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
				elif sys_code == 30:
					return res.success(False)

				elif sys_code == 40:  # GFX_CLEAR
					c = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					self.back_buffer = [
						[c % 16 for _ in range(self.width)] for _ in range(self.height)
					]
				elif sys_code == 41:  # GFX_PIXEL
					c, y, x = (
						res.register(self.pop(start_pos, end_pos)),
						res.register(self.pop(start_pos, end_pos)),
						res.register(self.pop(start_pos, end_pos)),
					)
					if res.error:
						return res
					self.write_pixel(x, y, c)
				elif sys_code == 42:  # GFX_LINE
					c, y2, x2, y1, x1 = [
						res.register(self.pop(start_pos, end_pos)) for _ in range(5)
					]
					if res.error:
						return res
					dx, dy = abs(x2 - x1), abs(y2 - y1)
					sx = 1 if x1 < x2 else -1
					sy = 1 if y1 < y2 else -1
					err = dx - dy
					while True:
						self.write_pixel(x1, y1, c)
						if x1 == x2 and y1 == y2:
							break
						e2 = 2 * err
						if e2 > -dy:
							err -= dy
							x1 += sx
						if e2 < dx:
							err += dx
							y1 += sy
				elif sys_code in (43, 44):  # DRAW/FILL RECT
					c, h, w, y, x = [
						res.register(self.pop(start_pos, end_pos)) for _ in range(5)
					]
					if res.error:
						return res
					for yi in range(y, y + h):
						for xi in range(x, x + w):
							if sys_code == 44 or (
								yi == y or yi == y + h - 1 or xi == x or xi == x + w - 1
							):
								self.write_pixel(xi, yi, c)
				elif sys_code in (45, 46):  # DRAW/FILL CIRCLE
					c, r, yc, xc = [
						res.register(self.pop(start_pos, end_pos)) for _ in range(4)
					]
					if res.error:
						return res
					for yi in range(yc - r, yc + r + 1):
						for xi in range(xc - r, xc + r + 1):
							if (xi - xc) ** 2 + (yi - yc) ** 2 <= r**2:
								if (
									sys_code == 46
									or abs((xi - xc) ** 2 + (yi - yc) ** 2 - r**2) < r
								):
									self.write_pixel(xi, yi, c)
				elif sys_code in (47, 48):  # DRAW/FILL TRIANGLE
					c, y3, x3, y2, x2, y1, x1 = [
						res.register(self.pop(start_pos, end_pos)) for _ in range(7)
					]
					if res.error:
						return res
					if sys_code == 48:
						min_x, max_x = max(0, min(x1, x2, x3)), min(
							self.width - 1, max(x1, x2, x3)
						)
						min_y, max_y = max(0, min(y1, y2, y3)), min(
							self.height - 1, max(y1, y2, y3)
						)
						for y in range(min_y, max_y + 1):
							for x in range(min_x, max_x + 1):
								d1 = (x - x2) * (y1 - y2) - (x1 - x2) * (y - y2)
								d2 = (x - x3) * (y2 - y3) - (x2 - x3) * (y - y3)
								d3 = (x - x1) * (y3 - y1) - (x3 - x1) * (y - y1)
								if (d1 >= 0 and d2 >= 0 and d3 >= 0) or (
									d1 <= 0 and d2 <= 0 and d3 <= 0
								):
									self.write_pixel(x, y, c)
					else:
						for pA, pB in [
							((x1, y1), (x2, y2)),
							((x2, y2), (x3, y3)),
							((x3, y3), (x1, y1)),
						]:
							self.execute((start_pos, end_pos, "PUSH", pA[0]))
							self.execute((start_pos, end_pos, "PUSH", pA[1]))
							self.execute((start_pos, end_pos, "PUSH", pB[0]))
							self.execute((start_pos, end_pos, "PUSH", pB[1]))
							self.execute((start_pos, end_pos, "PUSH", c))
							self.execute((start_pos, end_pos, "SYS", 42))
				elif sys_code == 49:  # GFX_IMAGE
					addr, h, w, y, x = [
						res.register(self.pop(start_pos, end_pos)) for _ in range(5)
					]
					if res.error:
						return res
					idx = 0
					for yi in range(y, y + h):
						for xi in range(x, x + w):
							c = self.memory[addr + idx]
							self.write_pixel(xi, yi, c)
							idx += 1

				elif sys_code == 50:  # DRAW_CHR
					c, char_code, y, x = [
						res.register(self.pop(start_pos, end_pos)) for _ in range(4)
					]
					if res.error:
						return res
					for yi in range(y, y + 7):
						for xi in range(x, x + 5):
							self.write_pixel(xi, yi, c)
				elif sys_code == 51:  # DRAW_STR
					c, addr, y, x = [
						res.register(self.pop(start_pos, end_pos)) for _ in range(4)
					]
					if res.error:
						return res
					text = self.read_mem_string(addr)
					for i, _ in enumerate(text):
						self.write_pixel(x + (i * 6), y, c)
				elif sys_code in (52, 53, 54, 55):  # Mock UI Components
					c, h, w, y, x = [
						res.register(self.pop(start_pos, end_pos)) for _ in range(5)
					]
					if res.error:
						return res
					for yi in range(y, y + h):
						for xi in range(x, x + w):
							self.write_pixel(xi, yi, c)

				elif sys_code == 56:  # GFX_SET_CLIP
					h, w, y, x = [
						res.register(self.pop(start_pos, end_pos)) for _ in range(4)
					]
					if res.error:
						return res
					self.clip_rect = (x, y, x + w, y + h)
				elif sys_code == 57:  # GFX_RESET_CLIP / GFX_REFRESH
					self.clip_rect = (0, 0, self.width, self.height)
					self.front_buffer = [row[:] for row in self.back_buffer]
					self.render_front_buffer()

				elif sys_code == 60:  # DVC_GETMOUSEX
					self.stack.append(self.mouse_x)
				elif sys_code == 61:  # DVC_GETMOUSEY
					self.stack.append(self.mouse_y)
				elif sys_code == 62:  # DVC_GETMOUSEBTN
					self.stack.append(self.mouse_btn)
				elif sys_code == 63:  # DVC_POLL_KEYBOARD
					if self.key_queue:
						evt, code, mod = self.key_queue.pop(0)
						self.stack.extend([evt, code, mod])
					else:
						self.stack.extend([0, 0, 0])
				elif sys_code == 64:  # DVC_KEY_IS_DOWN
					code = res.register(self.pop(start_pos, end_pos))
					if res.error:
						return res
					self.stack.append(TRUE if code in self.keys_down else FALSE)
				elif sys_code == 65:  # DVC_GET_MODIFIERS
					self.stack.append(self.modifiers)

			case _ if op.startswith(":"):
				pass
			case _:
				return res.fail(VMError(f"Unknown instruction '{op}'", start_pos, end_pos))

		if res.error:
			return res
		return res.success(True)
