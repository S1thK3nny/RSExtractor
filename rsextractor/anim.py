"""Animation clips (``_an``) and their evaluation through the model's setup code.

A clip is a list of curves, one per animation channel of the model (``_su``
header word 8). The setup code compiled from the model's Maya scene (``_su``
words 1 and 2) samples every curve through a host callback and writes the
results, plus any expression or constraint results, into per-node attribute
arrays. This module runs that code with :mod:`rsextractor.ppc` and returns the
attribute arrays for each frame.

Curve record (RS2 boot DOL 0x80009ff8, RS3 0x8003a74c)::

    u8 flags, u8, u16 segment count, f32 value scale, f32 end time, keys...

flags 0x02: constant, value = the f32 at +4. Otherwise ``count + 1`` keys of
``stride`` halfwords: a time (f32 when flags & 9, else u16 / 128) followed by
the segment's cubic ``((a*u + b)*u + c)*u + d`` with ``u = t - t_key``:

* flags 0x08: a, b, c, d as f32
* otherwise: s16 d, 24-bit float a, 24-bit float b and, with flags 0x04, s16 c
  (s16 values are scaled by 2^-14 * value scale). Without c the segment is
  solved to end on the next key's value.

Clip: ``u32 object offset``, event records, then the object. RS2 object
``{end, f32 start, f32 end, u32 curve offsets[channels]}``; RS3 object ``{u32,
u32, u32, f32 start, f32 end, u32 count, u32 curve offsets[]}`` (offsets
relative to the object). RS3 models' own ``_an`` parts are a bare object.
Time is in frames at 24 frames per second.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .ppc import EmulationError, Machine, Memory

FPS = 24.0
_K = 2.0 ** -14


def _u16(b: bytes, o: int) -> int:
    return (b[o] << 8) | b[o + 1]


def _s16(b: bytes, o: int) -> int:
    v = (b[o] << 8) | b[o + 1]
    return v - 0x10000 if v & 0x8000 else v


def _f32(b: bytes, o: int) -> float:
    return struct.unpack_from(">f", b, o)[0]


def _f24(b: bytes, o: int) -> float:
    return struct.unpack(">f", bytes(b[o:o + 3]) + b"\0")[0]


def sample_curve(data: bytes, off: int, t: float) -> float:
    """Value of the curve at ``data[off]`` at frame ``t``."""
    flags = data[off]
    if flags & 2:
        return _f32(data, off + 4)
    count = _u16(data, off + 2)
    scale = _f32(data, off + 4)
    end = _f32(data, off + 8)
    keys = off + 0xC
    float_time = bool(flags & 9)
    stride = 2 * ((8 if flags & 8 else 5 if flags & 4 else 4) + (2 if float_time else 1))

    def time(i: int) -> float:
        k = keys + i * stride
        return _f32(data, k) if float_time else _u16(data, k) / 128.0

    t = min(max(t, time(0)), end)
    lo, hi = 0, max(count - 1, 0)
    while lo < hi:  # last segment whose start time <= t
        mid = (lo + hi + 1) // 2
        if time(mid) <= t:
            lo = mid
        else:
            hi = mid - 1
    u = t - time(lo)
    p = keys + lo * stride + (4 if float_time else 2)
    if flags & 8:
        # The end key of the last segment is not always stored; it is not needed.
        a, b, c, d = struct.unpack_from(">4f", data, p)
    else:
        d = _s16(data, p) * _K * scale
        a, b = _f24(data, p + 2), _f24(data, p + 5)
        if flags & 4:
            c = _s16(data, p + 8) * _K * scale
        else:
            dt = time(lo + 1) - time(lo)
            nxt = _s16(data, p + stride) * _K * scale
            c = (nxt - d) / dt - (a * dt + b) * dt if dt else 0.0
    return ((a * u + b) * u + c) * u + d


@dataclass
class Clip:
    data: bytes
    start: float
    end: float
    curves: List[int]          # offset of each channel's curve in ``data``

    @classmethod
    def parse(cls, data: bytes, game: int, channels: int) -> Optional["Clip"]:
        """``channels`` is the model's channel count (``_su`` word 8); RS2 clips
        do not store it."""
        if len(data) < 0x20:
            return None
        if game == 2:
            base = struct.unpack_from(">I", data, 0)[0]
            if base + 12 + 4 * channels > len(data):
                return None
            start, end = struct.unpack_from(">ff", data, base + 4)
            offs = [o + base for o in struct.unpack_from(">%dI" % channels, data, base + 12)]
            if any(o >= len(data) for o in offs):
                return None
            return cls(data, start, end, offs)
        # Standalone clips start with the object offset (events precede the
        # object); a model's own ``_an`` is the bare object.
        for base in (struct.unpack_from(">I", data, 0)[0], 0):
            if base + 0x18 > len(data):
                continue
            start, end, count = struct.unpack_from(">ffI", data, base + 0x0C)
            if count == channels and base + 0x18 + 4 * count <= len(data):
                offs = [o + base for o in struct.unpack_from(">%dI" % count, data, base + 0x18)]
                if all(o < len(data) for o in offs):
                    return cls(data, start, end, offs)
        return None


# Memory layout for the emulated setup code (outside the console's own ranges)
_SU = 0x10000000
_WORK = 0x20000000
_CLIP = 0x30000000
_PTRS = 0x38000000
_CTX = 0x40000000
_STACK = 0x48000000
_CURVE_FN = 0x0E000000

# Boot DOL addresses of the RS3 constraint solver glue (0x80013c80 builds the
# context, 0x8003a6f8 is the solver entry handed to the setup code)
_RS3_SOLVER = 0x8003A6F8
_RS3_SAVED_R2 = 0x8005F540
_RS3_SOLVER_ARRAYS = 0x800614E0
_RS3_SOLVER_CACHE = 0x800644F0
_RS3_OUTPUT_END = -0x6C24   # r13 relative
# Small data bases set by the boot DOL (the game DOL shares them)
_SDA = {2: (0x8009EA20, 0x8009ADA0), 3: (0x800B88A0, 0x800A88A0)}


def _dol_sections(dol: bytes):
    """(address, bytes) for every section, then (bss address, bss size)."""
    offs = struct.unpack_from(">18I", dol, 0)
    addrs = struct.unpack_from(">18I", dol, 0x48)
    sizes = struct.unpack_from(">18I", dol, 0x90)
    secs = [(a, dol[o:o + s]) for o, a, s in zip(offs, addrs, sizes) if s]
    bss, bss_size = struct.unpack_from(">II", dol, 0xD8)
    return secs, (bss, bss_size)


class Runtime:
    """Console memory with the boot and game executables loaded, used to run
    game routines the setup code calls (the RS3 constraint solver)."""

    MEM1 = (0x80000000, 0x01800000)

    def __init__(self, boot: bytes, game_dol: Optional[bytes], game: int):
        self.game = game
        mem1 = bytearray(self.MEM1[1])
        secs, _ = _dol_sections(boot)
        for addr, data in secs:
            o = addr - self.MEM1[0]
            if 0 <= o and o + len(data) <= len(mem1):
                mem1[o:o + len(data)] = data
        self.images = [(self.MEM1[0], bytes(mem1))]
        if game_dol:
            secs, (bss, bss_size) = _dol_sections(game_dol)
            lo = min(a for a, _ in secs)
            hi = max([a + len(d) for a, d in secs] + [bss + bss_size])
            img = bytearray(hi - lo)
            for a, d in secs:
                img[a - lo:a - lo + len(d)] = d
            self.images.append((lo, bytes(img)))
        self.r2, self.r13 = _SDA[game]

    def map_into(self, mem: Memory) -> None:
        for base, img in self.images:
            mem.map(base, img)


def relocate_su(su: bytes, base: int, game: int) -> bytearray:
    """The ``_su`` part as the game holds it after loading it at ``base``
    (RS2 FUN_7fd283f0, RS3 FUN_7f3985f8)."""
    b = bytearray(su)
    n = len(b)

    def get(o: int) -> int:
        return struct.unpack_from(">I", b, o)[0] if 0 <= o <= n - 4 else 0

    def add(o: int, v: int) -> None:
        if 0 <= o <= n - 4:
            struct.pack_into(">I", b, o, (get(o) + v) & 0xFFFFFFFF)

    hdr = [get(4 * i) for i in range(34)]

    def rel(i: int, always: bool = False) -> None:
        if always or hdr[i]:
            add(4 * i, base)

    def table(count_word: int, ptr_word: int, entry: int, member: int) -> None:
        p = hdr[ptr_word]
        if p:
            for k in range(hdr[count_word]):
                if get(p + k * entry + member):
                    add(p + k * entry + member, base)

    code = hdr[1]
    if game == 2:
        for k in range(hdr[4]):
            add(code + get(hdr[3] + 4 * k), base + code)
        table(19, 20, 12, 4)
        table(21, 22, 8, 0)
        table(5, 6, 8, 4)
        for i in (0, 1, 3):
            rel(i, True)
        for i in (6, 10, 12, 14, 16, 18, 20, 22):
            rel(i)
        return b
    # RS3 code relocations: {u8 type, u8, u16 low half, u32 offset}, relative
    # to the code base: 0 = 32-bit address, 1 = high half adjusted, 2 = low half
    target = base + code
    for k in range(hdr[4]):
        e = hdr[3] + 8 * k
        if e + 8 > n:
            break
        typ, off = b[e], get(e + 4)
        if typ == 0:
            add(code + off, target)
        elif typ == 1 and code + off + 2 <= n:
            lo = struct.unpack_from(">H", b, e + 2)[0]
            hi = struct.unpack_from(">H", b, code + off)[0]
            v = (((hi << 16) | lo) + target) & 0xFFFFFFFF
            struct.pack_into(">H", b, code + off, ((v >> 16) + ((v >> 15) & 1)) & 0xFFFF)
        elif typ == 2 and code + off + 2 <= n:
            cur = struct.unpack_from(">H", b, code + off)[0]
            struct.pack_into(">H", b, code + off, (cur + target) & 0xFFFF)
    table(19, 20, 16, 4)
    if hdr[20]:
        rel(21)
    table(22, 23, 8, 0)
    table(5, 6, 8, 4)
    table(24, 25, 4, 0)
    table(26, 27, 8, 4)
    table(28, 29, 8, 4)
    for i in (0, 3, 6, 10, 12, 14, 16, 18, 20, 23, 25, 27, 29, 30, 31, 32):
        rel(i)
    rel(1, True)
    return b


class SetupProgram:
    """One model's setup code bound to its bind-pose attribute arrays."""

    def __init__(self, su: bytes, game: int, runtime: Optional[Runtime] = None):
        self.su = su
        self.game = game
        self.runtime = runtime
        h = struct.unpack_from(">26I", su, 0)
        self.code, self.code_size = h[1], h[2]
        self.channels = h[8]
        self.n_xf, self.p_xf = h[11], h[12]
        self.n_jt, self.p_jt = h[13], h[14]
        self.n_3, self.p_3 = h[15], h[16]
        self.n_4, self.p_4 = h[17], h[18]
        self.locals_size, self.locals_off = h[9], h[10]
        self.solver_data = h[25] if game == 3 else 0
        self.xf_size = 0x64 if game == 2 else 0x68
        self.xf_stride = 0x68 if game == 2 else 0x6C

    @property
    def runnable(self) -> bool:
        return bool(self.code and self.code_size and self.channels)

    def bind_arrays(self) -> Tuple[bytearray, bytearray]:
        su = self.su
        xf = bytearray()
        for i in range(self.n_xf):
            o = self.p_xf + i * self.xf_stride
            xf += su[o:o + self.xf_size]
        jt = bytearray()
        for i in range(self.n_jt):
            o = self.p_jt + i * 0x54
            jt += su[o:o + 0x50]
        return xf, jt

    def evaluate(self, clip: Clip, frames: Sequence[float]) -> List[Tuple[bytes, bytes]]:
        """Run the setup code for every frame; returns (transform values, joint
        values) byte arrays per frame in the ``_su`` record layout."""
        mem = Memory()
        mem.map(_SU, relocate_su(self.su, _SU, self.game) + bytes(64))
        xf, jt = self.bind_arrays()
        t3 = bytearray()
        for i in range(self.n_3):
            o = self.p_3 + i * 0x20
            t3 += self.su[o:o + 0x1C]
        t4 = bytearray()
        for i in range(self.n_4):
            o = self.p_4 + i * 0x1C
            t4 += self.su[o:o + 0x18]
        outs = bytes(16 * self.channels)
        loc = self.su[self.locals_off:self.locals_off + self.locals_size]
        addrs = []
        blob = bytearray()
        for p in (xf, jt, t3, t4, outs, loc):
            addrs.append(_WORK + len(blob))
            blob += p
            blob += bytes((-len(blob)) % 32)
        work = mem.map(_WORK, blob + bytes(64))
        mem.map(_CLIP, clip.data)
        ptrs = [(_CLIP + o) for o in clip.curves[:self.channels]]
        ptrs += [0] * (self.channels - len(ptrs))
        mem.map(_PTRS, struct.pack(">%dI" % len(ptrs), *ptrs) + bytes(16))
        uses_solver = bool(self.solver_data) and self.runtime is not None
        ctx = struct.pack(">8I", *addrs, _PTRS, _CURVE_FN)
        ctx += struct.pack(">2I", _RS3_SOLVER if uses_solver else 0,
                           _SU + self.solver_data if self.solver_data else 0)
        mem.map(_CTX, ctx + bytes(64))
        mem.map(_STACK, bytes(0x20000))
        if self.runtime is not None:
            self.runtime.map_into(mem)

        cpu = Machine(mem)
        data = clip.data

        def curve(m: Machine) -> None:
            addr = m.r[3]
            if _CLIP <= addr < _CLIP + len(data):
                m.mem.wf32(m.r[4], sample_curve(data, addr - _CLIP, m.f[1]))

        cpu.hooks[_CURVE_FN] = curve
        if self.runtime is not None:
            cpu.r[13] = self.runtime.r13
        if uses_solver:
            rt = self.runtime
            mem.w32(_RS3_SAVED_R2, rt.r2)
            mem.write(_RS3_SOLVER_ARRAYS, struct.pack(">4I", addrs[0], addrs[1],
                                                      _SU + self.p_xf, _SU + self.p_jt))
            mem.w32((rt.r13 + _RS3_OUTPUT_END) & 0xFFFFFFFF, addrs[4] + 16 * self.channels)
        xf_off, jt_off = addrs[0] - _WORK, addrs[1] - _WORK
        results = []
        for t in frames:
            if uses_solver:  # the solver's per-evaluation node matrix cache (0x8001572c)
                mem.write(_RS3_SOLVER_CACHE, bytes(0x20))
            cpu.r[1] = _STACK + 0x1FF00
            cpu.r[2] = _SU + self.code + self.code_size
            cpu.r[3] = _CTX
            cpu.f[1] = t / FPS
            cpu.f[2] = t
            cpu.f[3] = FPS
            cpu.call(_SU + self.code)
            results.append((bytes(work[xf_off:xf_off + len(xf)]), bytes(work[jt_off:jt_off + len(jt)])))
        return results
