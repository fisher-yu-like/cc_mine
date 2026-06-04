"""Decompile memory.pyc to recover source using Python's dis module."""
import dis
import marshal
import sys

# Load the pyc file
with open('__pycache__/memory.cpython-312.pyc', 'rb') as f:
    f.read(16)  # Skip header (Python 3.12 uses 16 bytes)
    code = marshal.load(f)

# Print disassembly for all code objects
def dis_code(c, indent=0):
    prefix = '  ' * indent
    print(f'{prefix}# ===== {c.co_name} =====')
    print(f'{prefix}# vars: {c.co_varnames}')
    print(f'{prefix}# names: {c.co_names}')
    consts = [x for x in c.co_consts if not hasattr(x, 'co_code')]
    print(f'{prefix}# consts: {consts}')
    print(f'{prefix}# flags: {c.co_flags}')
    print(f'{prefix}# argcount: {c.co_argcount}')
    dis.dis(c)
    print()
    for const in c.co_consts:
        if hasattr(const, 'co_code'):
            dis_code(const, indent + 1)

# Redirect to file
with open('calc_bug_fix/memory_dis.txt', 'w') as out:
    sys.stdout = out
    dis_code(code)
    sys.stdout = sys.__stdout__

print("Done! Output written to calc_bug_fix/memory_dis.txt")
print(f"Top-level code name: {code.co_name}")
print(f"Top-level varnames: {code.co_varnames}")
print(f"Top-level names: {code.co_names}")
