from xe_lang.helper import TT

BINARY_RULES = {
	
	# Arithmetic
	(TT.ADD, "int", "int"): "int",
	(TT.ADD, "int", "float"): "float",
	(TT.ADD, "float", "int"): "float",
	(TT.ADD, "float", "float"): "float",
    (TT.ADD, "string", "string"): "string",

	(TT.SUB, "int", "int"): "int",
	(TT.SUB, "int", "float"): "float",
	(TT.SUB, "float", "int"): "float",
	(TT.SUB, "float", "float"): "float",

	(TT.MUL, "int", "int"): "int",
	(TT.MUL, "int", "float"): "float",
	(TT.MUL, "float", "int"): "float",
	(TT.MUL, "float", "float"): "float",

	(TT.DIV, "int", "int"): "int",      # floor division in VM
	(TT.DIV, "int", "float"): "float",
	(TT.DIV, "float", "int"): "float",
	(TT.DIV, "float", "float"): "float",

	(TT.MOD, "int", "int"): "int",
	(TT.MOD, "int", "float"): "float",
	(TT.MOD, "float", "int"): "float",
	(TT.MOD, "float", "float"): "float",

	(TT.POW, "int", "int"): "int",
	(TT.POW, "int", "float"): "float",
	(TT.POW, "float", "int"): "float",
	(TT.POW, "float", "float"): "float",

	# Comparisons
	(TT.LT, "int", "int"): "bool",
	(TT.LT, "int", "float"): "bool",
	(TT.LT, "float", "int"): "bool",
	(TT.LT, "float", "float"): "bool",

	(TT.LE, "int", "int"): "bool",
	(TT.LE, "int", "float"): "bool",
	(TT.LE, "float", "int"): "bool",
	(TT.LE, "float", "float"): "bool",

	(TT.GT, "int", "int"): "bool",
	(TT.GT, "int", "float"): "bool",
	(TT.GT, "float", "int"): "bool",
	(TT.GT, "float", "float"): "bool",

	(TT.GE, "int", "int"): "bool",
	(TT.GE, "int", "float"): "bool",
	(TT.GE, "float", "int"): "bool",
	(TT.GE, "float", "float"): "bool",
	
	# Equality
	(TT.EQ, "int", "int"): "bool",
	(TT.EQ, "int", "float"): "bool",
	(TT.EQ, "float", "int"): "bool",
	(TT.EQ, "float", "float"): "bool",
	(TT.EQ, "bool", "bool"): "bool",

	(TT.NE, "int", "int"): "bool",
	(TT.NE, "int", "float"): "bool",
	(TT.NE, "float", "int"): "bool",
	(TT.NE, "float", "float"): "bool",
	(TT.NE, "bool", "bool"): "bool",
	
	# Logical
	(TT.ANDL, "bool", "bool"): "bool",
	(TT.ORL, "bool", "bool"): "bool",
	(TT.XORL, "bool", "bool"): "bool",
	
	# Bitwise
	(TT.AND, "int", "int"): "int",
	(TT.OR, "int", "int"): "int",
	(TT.XOR, "int", "int"): "int",
}

UNARY_RULES = {
	(TT.ADD, "int"): "int",
	(TT.ADD, "float"): "float",

	(TT.SUB, "int"): "int",
	(TT.SUB, "float"): "float",

	(TT.NOTL, "bool"): "bool",

	(TT.NOT, "int"): "int",
}

BINARY_OPCODE_MAP = {
	# arithmetic
	(TT.ADD, "int"): "ADDI",
	(TT.ADD, "float"): "ADDF",
	(TT.SUB, "int"): "SUBI",
	(TT.SUB, "float"): "SUBF",
	(TT.MUL, "int"): "MULI",
	(TT.MUL, "float"): "MULF",
	(TT.DIV, "int"): "DIVI",
	(TT.DIV, "float"): "DIVF",
	(TT.MOD, "int"): "MODI",
	(TT.MOD, "float"): "MODF",
	(TT.POW, "int"): "POWI",
	(TT.POW, "float"): "POWF",
    
	# bitwise
	(TT.AND, "int"): "AND",
	(TT.OR, "int"): "OR",
	(TT.XOR, "int"): "XOR",
    
	# logical
	(TT.ANDL, "bool"): "AND",
	(TT.ORL, "bool"): "OR",
	(TT.XORL, "bool"): "XOR",
    
	# comparison
	(TT.EQ, "bool"): "EQ",
	(TT.NE, "bool"): "NE",
	(TT.LT, "bool"): "LT",
	(TT.LE, "bool"): "LE",
	(TT.GT, "bool"): "GT",
	(TT.GE, "bool"): "GE",
}