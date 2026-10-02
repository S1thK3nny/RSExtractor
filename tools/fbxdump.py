"""Independent binary FBX reader used to validate RSExtractor output.

Usage: python tools/fbxdump.py file.fbx [--tree]
Checks header/footer, every node's end offset, and decodes all property arrays.
"""
import struct
import sys
import zlib

import numpy as np


def read_props(b, p, n):
    props = []
    for _ in range(n):
        t = chr(b[p]); p += 1
        if t in "YCIFDL":
            fmt = {"Y": "<h", "C": "<?", "I": "<i", "F": "<f", "D": "<d", "L": "<q"}[t]
            props.append(struct.unpack_from(fmt, b, p)[0]); p += struct.calcsize(fmt)
        elif t in "SR":
            ln = struct.unpack_from("<I", b, p)[0]; p += 4
            v = b[p:p + ln]; p += ln
            props.append(v.decode("utf-8", "replace") if t == "S" else v)
        elif t in "ifdlb":
            cnt, enc, ln = struct.unpack_from("<III", b, p); p += 12
            raw = b[p:p + ln]; p += ln
            if enc:
                raw = zlib.decompress(raw)
            dt = {"i": "<i4", "f": "<f4", "d": "<f8", "l": "<i8", "b": "u1"}[t]
            arr = np.frombuffer(raw, dtype=dt)
            assert len(arr) == cnt, (t, cnt, len(arr))
            props.append(arr)
        else:
            raise ValueError(f"bad property type {t!r} at {p - 1}")
    return props, p


def read_node(b, p):
    end, nprops, plen, nlen = struct.unpack_from("<IIIB", b, p)
    if end == 0:
        return None, p + 13
    name = b[p + 13:p + 13 + nlen].decode()
    q = p + 13 + nlen
    props, q2 = read_props(b, q, nprops)
    assert q2 - q == plen, f"{name}: property length mismatch"
    children = []
    while q2 < end:
        child, q2 = read_node(b, q2)
        if child is None:
            break
        children.append(child)
    assert q2 == end, f"{name}: end offset {end} != parsed {q2}"
    return (name, props, children), end


def parse(path):
    b = open(path, "rb").read()
    assert b[:23] == b"Kaydara FBX Binary\x20\x20\x00\x1a\x00", "bad magic"
    version = struct.unpack_from("<I", b, 23)[0]
    p = 27
    nodes = []
    while True:
        node, p = read_node(b, p)
        if node is None:
            break
        nodes.append(node)
    assert b[-16:] == b"\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b", "bad footer"
    return version, nodes


def dump(node, depth=0, max_depth=3):
    name, props, children = node
    short = []
    for v in props:
        if isinstance(v, np.ndarray):
            short.append(f"<{v.dtype} x{len(v)}>")
        elif isinstance(v, bytes):
            short.append(f"<raw {len(v)}>")
        else:
            short.append(repr(v).replace("\\x00\\x01", "::"))
    print("  " * depth + name, " ".join(short))
    if depth < max_depth:
        for c in children:
            dump(c, depth + 1, max_depth)


if __name__ == "__main__":
    version, nodes = parse(sys.argv[1])
    objects = next(n for n in nodes if n[0] == "Objects")
    kinds = {}
    for o in objects[2]:
        kinds[o[0]] = kinds.get(o[0], 0) + 1
    print(f"FBX {version}: OK, objects: {kinds}")
    if "--tree" in sys.argv:
        for n in nodes:
            dump(n)
