"""A small Gekko (GameCube PowerPC) interpreter for the per-model setup code.

Factor 5 compiled each model's Maya dependency graph (animation curves feeding
node attributes, expressions, constraints) into PowerPC code stored in the
``_su`` part. Running that code is the only faithful way to get the animated
node values, so this module interprets the integer, floating point and paired
single instructions it uses. Calls to host functions (the curve sampler and
similar callbacks) are dispatched to Python through ``Machine.hooks``.
"""
from __future__ import annotations

import math
import struct
from typing import Callable, Dict, List, Optional, Tuple

_F32 = struct.Struct(">f")
_U32 = struct.Struct(">I")
_F64 = struct.Struct(">d")


def f32(v: float) -> float:
    """Round to single precision."""
    try:
        return _F32.unpack(_F32.pack(v))[0]
    except OverflowError:
        return math.copysign(math.inf, v)


def s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def _mask(mb: int, me: int) -> int:
    if mb <= me:
        m = ((0xFFFFFFFF >> mb) & (0xFFFFFFFF << (31 - me))) & 0xFFFFFFFF
    else:
        m = ((0xFFFFFFFF >> mb) | (0xFFFFFFFF << (31 - me))) & 0xFFFFFFFF
    return m


def _rotl(v: int, n: int) -> int:
    n &= 31
    return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF if n else v


class EmulationError(Exception):
    pass


_PAGE = 16  # 64 KiB lookup pages


class Memory:
    """Sparse big-endian memory made of segments, found through a page table."""

    def __init__(self) -> None:
        self.segments: List[Tuple[int, bytearray]] = []
        self._pages: Dict[int, Tuple[int, bytearray, int]] = {}

    def map(self, base: int, data) -> bytearray:
        buf = bytearray(data)
        self.segments.append((base, buf))
        end = base + len(buf)
        for page in range(base >> _PAGE, ((end - 1) >> _PAGE) + 1):
            self._pages[page] = (base, buf, end)
        return buf

    def _find(self, addr: int, size: int) -> Tuple[bytearray, int]:
        seg = self._pages.get(addr >> _PAGE)
        if seg is not None:
            base, buf, end = seg
            if base <= addr and addr + size <= end:
                return buf, addr - base
        for base, buf in self.segments:
            if base <= addr and addr + size <= base + len(buf):
                return buf, addr - base
        raise EmulationError(f"unmapped access at {addr:#010x}")

    def read(self, addr: int, size: int) -> bytes:
        buf, o = self._find(addr, size)
        return bytes(buf[o:o + size])

    def write(self, addr: int, data: bytes) -> None:
        buf, o = self._find(addr, len(data))
        buf[o:o + len(data)] = data

    def u8(self, a: int) -> int:
        buf, o = self._find(a, 1)
        return buf[o]

    def u16(self, a: int) -> int:
        buf, o = self._find(a, 2)
        return (buf[o] << 8) | buf[o + 1]

    def u32(self, a: int) -> int:
        buf, o = self._find(a, 4)
        return _U32.unpack_from(buf, o)[0]

    def f32(self, a: int) -> float:
        buf, o = self._find(a, 4)
        return _F32.unpack_from(buf, o)[0]

    def f64(self, a: int) -> float:
        buf, o = self._find(a, 8)
        return _F64.unpack_from(buf, o)[0]

    def w8(self, a: int, v: int) -> None:
        buf, o = self._find(a, 1)
        buf[o] = v & 0xFF

    def w16(self, a: int, v: int) -> None:
        buf, o = self._find(a, 2)
        buf[o] = (v >> 8) & 0xFF
        buf[o + 1] = v & 0xFF

    def w32(self, a: int, v: int) -> None:
        buf, o = self._find(a, 4)
        _U32.pack_into(buf, o, v & 0xFFFFFFFF)

    def wf32(self, a: int, v: float) -> None:
        buf, o = self._find(a, 4)
        try:
            _F32.pack_into(buf, o, v)
        except OverflowError:
            _F32.pack_into(buf, o, math.copysign(math.inf, v))

    def wf64(self, a: int, v: float) -> None:
        buf, o = self._find(a, 8)
        _F64.pack_into(buf, o, v)


# Graphics quantization register types: 0 float, 4 u8, 5 u16, 6 s8, 7 s16
def _dequant(mem: Memory, addr: int, typ: int, scale: int) -> Tuple[float, int]:
    if typ == 0:
        return mem.f32(addr), 4
    sc = 2.0 ** -(scale - 64 if scale & 0x20 else scale)
    if typ == 4:
        return mem.u8(addr) * sc, 1
    if typ == 6:
        v = mem.u8(addr)
        return (v - 256 if v & 0x80 else v) * sc, 1
    if typ == 5:
        return mem.u16(addr) * sc, 2
    if typ == 7:
        return s16(mem.u16(addr)) * sc, 2
    raise EmulationError(f"bad GQR type {typ}")


def _quant(mem: Memory, addr: int, v: float, typ: int, scale: int) -> int:
    if typ == 0:
        mem.wf32(addr, v)
        return 4
    sc = 2.0 ** (scale - 64 if scale & 0x20 else scale)
    q = int(v * sc)
    if typ in (4, 6):
        lo, hi = (0, 255) if typ == 4 else (-128, 127)
        mem.w8(addr, max(lo, min(hi, q)))
        return 1
    lo, hi = (0, 65535) if typ == 5 else (-32768, 32767)
    mem.w16(addr, max(lo, min(hi, q)))
    return 2


class Machine:
    """CPU state plus memory. ``hooks`` maps addresses to Python callables that
    replace the function at that address (they return to LR afterwards)."""

    RETURN = 0x0FFF0000  # sentinel return address that stops ``call``

    def __init__(self, mem: Memory) -> None:
        self.mem = mem
        self.r = [0] * 32
        self.f = [0.0] * 32
        self.ps1 = [0.0] * 32
        self.cr = [0] * 8          # 4-bit fields, bit 3 = LT ... bit 0 = SO
        self.lr = 0
        self.ctr = 0
        self.xer_ca = 0
        self.gqr = [0] * 8
        self.pc = 0
        self.hooks: Dict[int, Callable[["Machine"], None]] = {}
        self.max_steps = 5_000_000
        self._code: Dict[int, int] = {}  # fetched instruction words (code is never modified)
        self._decoded: Dict[int, tuple] = {}  # address -> (handler, fields...)

    # --- condition register helpers
    def _set_cr(self, field: int, lt: bool, gt: bool, eq: bool, so: bool = False) -> None:
        self.cr[field] = (lt << 3) | (gt << 2) | (eq << 1) | int(so)

    def _cr_bit(self, bi: int) -> int:
        return (self.cr[bi >> 2] >> (3 - (bi & 3))) & 1

    def _set_cr_bit(self, bi: int, v: int) -> None:
        f, s = bi >> 2, 3 - (bi & 3)
        self.cr[f] = (self.cr[f] & ~(1 << s)) | ((v & 1) << s)

    def _rc(self, v: int) -> None:
        sv = s32(v)
        self._set_cr(0, sv < 0, sv > 0, sv == 0)

    def _fcmp(self, field: int, a: float, b: float) -> None:
        if math.isnan(a) or math.isnan(b):
            self.cr[field] = 1
        else:
            self._set_cr(field, a < b, a > b, a == b)

    def call(self, addr: int, *, lr: Optional[int] = None) -> None:
        """Run the function at ``addr`` until it returns."""
        self.lr = self.RETURN if lr is None else lr
        ret = self.RETURN
        hooks = self.hooks
        decoded = self._decoded
        pc = addr
        steps = 0
        limit = self.max_steps
        while pc != ret:
            if pc in hooks:
                self.pc = pc
                hooks[pc](self)
                pc = self.lr
                continue
            d = decoded.get(pc)
            if d is None:
                d = decoded[pc] = _decode(self, pc)
            pc = d[0](self, pc, d)
            steps += 1
            if steps > limit:
                raise EmulationError("step limit reached")
        self.pc = pc

    # --- the interpreter
    def step(self) -> None:
        pc = self.pc
        w = self._code.get(pc)
        if w is None:
            w = self._code[pc] = self.mem.u32(pc)
        op = w >> 26
        rd = (w >> 21) & 31
        ra = (w >> 16) & 31
        rb = (w >> 11) & 31
        r = self.r
        f = self.f
        mem = self.mem
        nxt = pc + 4
        simm = s16(w)
        uimm = w & 0xFFFF

        if op == 14:  # addi
            r[rd] = (simm + (r[ra] if ra else 0)) & 0xFFFFFFFF
        elif op == 15:  # addis
            r[rd] = ((simm << 16) + (r[ra] if ra else 0)) & 0xFFFFFFFF
        elif op in (12, 13):  # addic, addic.
            v = r[ra] + (simm & 0xFFFFFFFF)
            self.xer_ca = v >> 32
            r[rd] = v & 0xFFFFFFFF
            if op == 13:
                self._rc(r[rd])
        elif op == 8:  # subfic
            v = (simm & 0xFFFFFFFF) + ((~r[ra]) & 0xFFFFFFFF) + 1
            self.xer_ca = v >> 32
            r[rd] = v & 0xFFFFFFFF
        elif op == 7:  # mulli
            r[rd] = (s32(r[ra]) * simm) & 0xFFFFFFFF
        elif op == 10:  # cmpli
            a = r[ra]
            self._set_cr(rd >> 2, a < uimm, a > uimm, a == uimm)
        elif op == 11:  # cmpi
            a = s32(r[ra])
            self._set_cr(rd >> 2, a < simm, a > simm, a == simm)
        elif op == 24:  # ori
            r[ra] = r[rd] | uimm
        elif op == 25:  # oris
            r[ra] = r[rd] | (uimm << 16)
        elif op == 26:  # xori
            r[ra] = r[rd] ^ uimm
        elif op == 27:  # xoris
            r[ra] = r[rd] ^ (uimm << 16)
        elif op == 28:  # andi.
            r[ra] = r[rd] & uimm
            self._rc(r[ra])
        elif op == 29:  # andis.
            r[ra] = r[rd] & (uimm << 16)
            self._rc(r[ra])
        elif op in (20, 21, 23):  # rlwimi, rlwinm, rlwnm
            sh = rb if op != 23 else r[rb] & 31
            mb, me = (w >> 6) & 31, (w >> 1) & 31
            m = _mask(mb, me)
            rot = _rotl(r[rd], sh)
            if op == 20:
                r[ra] = (rot & m) | (r[ra] & ~m & 0xFFFFFFFF)
            else:
                r[ra] = rot & m
            if w & 1:
                self._rc(r[ra])
        elif 32 <= op <= 55 or op in (46, 47):
            self._load_store(op, w, rd, ra, simm)
        elif op in (56, 57, 60, 61):  # psq_l(u), psq_st(u)
            d = w & 0xFFF
            d = d - 0x1000 if d & 0x800 else d
            wbit = (w >> 15) & 1
            qr = self.gqr[(w >> 12) & 7]
            ea = ((r[ra] if ra or op in (57, 61) else 0) + d) & 0xFFFFFFFF
            self._psq(op in (56, 57), rd, ea, wbit, qr)
            if op in (57, 61):
                r[ra] = ea
        elif op == 18:  # b
            li = w & 0x03FFFFFC
            if li & 0x02000000:
                li -= 0x04000000
            target = (li if w & 2 else pc + li) & 0xFFFFFFFF
            if w & 1:
                self.lr = nxt
            nxt = target
        elif op == 16:  # bc
            bd = w & 0xFFFC
            if bd & 0x8000:
                bd -= 0x10000
            if self._branch_cond(rd, ra):
                if w & 1:
                    self.lr = nxt
                nxt = ((bd if w & 2 else pc + bd)) & 0xFFFFFFFF
        elif op == 19:
            nxt = self._op19(w, rd, ra, rb, nxt)
        elif op == 31:
            self._op31(w, rd, ra, rb)
        elif op == 59:
            self._op59(w, rd, ra, rb)
        elif op == 63:
            self._op63(w, rd, ra, rb)
        elif op == 4:
            self._op4(w, rd, ra, rb)
        else:
            raise EmulationError(f"unsupported opcode {op} ({w:#010x}) at {pc:#x}")
        self.pc = nxt

    def _branch_cond(self, bo: int, bi: int) -> bool:
        if not bo & 4:
            self.ctr = (self.ctr - 1) & 0xFFFFFFFF
            ctr_ok = (self.ctr != 0) != bool(bo & 2)
        else:
            ctr_ok = True
        cond_ok = bool(bo & 16) or self._cr_bit(bi) == ((bo >> 3) & 1)
        return ctr_ok and cond_ok

    def _op19(self, w: int, bo: int, bi: int, rb: int, nxt: int) -> int:
        xo = (w >> 1) & 0x3FF
        if xo == 16:  # bclr
            if self._branch_cond(bo, bi):
                target = self.lr & ~3
                if w & 1:
                    self.lr = nxt
                return target
            return nxt
        if xo == 528:  # bcctr
            if self._branch_cond(bo | 4, bi):
                if w & 1:
                    self.lr = nxt
                return self.ctr & ~3
            return nxt
        if xo == 0:  # mcrf
            self.cr[bo >> 2] = self.cr[bi >> 2]
            return nxt
        if xo == 150:  # isync
            return nxt
        a, b = self._cr_bit(bi), self._cr_bit(rb)
        ops = {257: a & b, 449: a | b, 193: a ^ b, 225: 1 - (a & b), 33: 1 - (a | b),
               289: 1 - (a ^ b), 129: a & (1 - b), 417: a | (1 - b)}
        if xo not in ops:
            raise EmulationError(f"unsupported op19 xo {xo} at {self.pc:#x}")
        self._set_cr_bit(bo, ops[xo])
        return nxt

    def _load_store(self, op: int, w: int, rd: int, ra: int, d: int) -> None:
        r, mem = self.r, self.mem
        update = op in (33, 35, 37, 39, 41, 43, 45, 49, 51, 53, 55)
        ea = ((r[ra] if ra or update else 0) + d) & 0xFFFFFFFF
        if op in (32, 33):
            r[rd] = mem.u32(ea)
        elif op in (34, 35):
            r[rd] = mem.u8(ea)
        elif op in (40, 41):
            r[rd] = mem.u16(ea)
        elif op in (42, 43):
            r[rd] = s16(mem.u16(ea)) & 0xFFFFFFFF
        elif op in (36, 37):
            mem.w32(ea, r[rd])
        elif op in (38, 39):
            mem.w8(ea, r[rd])
        elif op in (44, 45):
            mem.w16(ea, r[rd])
        elif op == 46:  # lmw
            for i in range(rd, 32):
                r[i] = mem.u32(ea + 4 * (i - rd))
        elif op == 47:  # stmw
            for i in range(rd, 32):
                mem.w32(ea + 4 * (i - rd), r[i])
        elif op in (48, 49):
            self.f[rd] = self.ps1[rd] = mem.f32(ea)
        elif op in (50, 51):
            self.f[rd] = mem.f64(ea)
        elif op in (52, 53):
            mem.wf32(ea, self.f[rd])
        elif op in (54, 55):
            mem.wf64(ea, self.f[rd])
        if update:
            r[ra] = ea

    def _psq(self, load: bool, fr: int, ea: int, single: int, qr: int) -> None:
        if load:
            typ, scale = (qr >> 16) & 7, (qr >> 24) & 0x3F
            v0, n = _dequant(self.mem, ea, typ, scale)
            v1 = 1.0 if single else _dequant(self.mem, ea + n, typ, scale)[0]
            self.f[fr], self.ps1[fr] = v0, v1
        else:
            typ, scale = qr & 7, (qr >> 8) & 0x3F
            n = _quant(self.mem, ea, self.f[fr], typ, scale)
            if not single:
                _quant(self.mem, ea + n, self.ps1[fr], typ, scale)

    def _op31(self, w: int, rd: int, ra: int, rb: int) -> None:
        r, mem = self.r, self.mem
        xo = (w >> 1) & 0x3FF
        rc = w & 1
        a, b = r[ra], r[rb]
        xo9 = xo & 0x1FF
        if xo9 == 266:  # add
            r[rd] = (a + b) & 0xFFFFFFFF
            if rc:
                self._rc(r[rd])
        elif xo9 == 40:  # subf
            r[rd] = (b - a) & 0xFFFFFFFF
            if rc:
                self._rc(r[rd])
        elif xo9 == 8:  # subfc
            v = b + ((~a) & 0xFFFFFFFF) + 1
            self.xer_ca = v >> 32
            r[rd] = v & 0xFFFFFFFF
        elif xo9 == 136:  # subfe
            v = b + ((~a) & 0xFFFFFFFF) + self.xer_ca
            self.xer_ca = v >> 32
            r[rd] = v & 0xFFFFFFFF
        elif xo9 == 10:  # addc
            v = a + b
            self.xer_ca = v >> 32
            r[rd] = v & 0xFFFFFFFF
        elif xo9 == 138:  # adde
            v = a + b + self.xer_ca
            self.xer_ca = v >> 32
            r[rd] = v & 0xFFFFFFFF
        elif xo9 == 202:  # addze
            v = a + self.xer_ca
            self.xer_ca = v >> 32
            r[rd] = v & 0xFFFFFFFF
        elif xo9 == 104:  # neg
            r[rd] = (-a) & 0xFFFFFFFF
            if rc:
                self._rc(r[rd])
        elif xo9 == 235:  # mullw
            r[rd] = (s32(a) * s32(b)) & 0xFFFFFFFF
            if rc:
                self._rc(r[rd])
        elif xo9 == 75:  # mulhw
            r[rd] = ((s32(a) * s32(b)) >> 32) & 0xFFFFFFFF
        elif xo9 == 11:  # mulhwu
            r[rd] = ((a * b) >> 32) & 0xFFFFFFFF
        elif xo9 == 491:  # divw
            sa, sb = s32(a), s32(b)
            r[rd] = (int(sa / sb) if sb else 0) & 0xFFFFFFFF
        elif xo9 == 459:  # divwu
            r[rd] = (a // b if b else 0) & 0xFFFFFFFF
        elif xo == 28:  # and
            r[ra] = r[rd] & b
            if rc:
                self._rc(r[ra])
        elif xo == 60:  # andc
            r[ra] = r[rd] & ~b & 0xFFFFFFFF
            if rc:
                self._rc(r[ra])
        elif xo == 444:  # or
            r[ra] = r[rd] | b
            if rc:
                self._rc(r[ra])
        elif xo == 412:  # orc
            r[ra] = (r[rd] | ~b) & 0xFFFFFFFF
        elif xo == 316:  # xor
            r[ra] = r[rd] ^ b
            if rc:
                self._rc(r[ra])
        elif xo == 124:  # nor
            r[ra] = ~(r[rd] | b) & 0xFFFFFFFF
            if rc:
                self._rc(r[ra])
        elif xo == 284:  # eqv
            r[ra] = ~(r[rd] ^ b) & 0xFFFFFFFF
        elif xo == 476:  # nand
            r[ra] = ~(r[rd] & b) & 0xFFFFFFFF
        elif xo == 24:  # slw
            n = b & 0x3F
            r[ra] = (r[rd] << n) & 0xFFFFFFFF if n < 32 else 0
            if rc:
                self._rc(r[ra])
        elif xo == 536:  # srw
            n = b & 0x3F
            r[ra] = r[rd] >> n if n < 32 else 0
            if rc:
                self._rc(r[ra])
        elif xo == 792:  # sraw
            n = b & 0x3F
            v = s32(r[rd])
            res = v >> min(n, 31)
            self.xer_ca = int(v < 0 and (n >= 32 or (v & ((1 << n) - 1)) != 0))
            r[ra] = res & 0xFFFFFFFF
            if rc:
                self._rc(r[ra])
        elif xo == 824:  # srawi
            v = s32(r[rd])
            r[ra] = (v >> rb) & 0xFFFFFFFF
            self.xer_ca = int(v < 0 and (v & ((1 << rb) - 1)) != 0)
            if rc:
                self._rc(r[ra])
        elif xo == 954:  # extsb
            v = r[rd] & 0xFF
            r[ra] = (v - 256 if v & 0x80 else v) & 0xFFFFFFFF
            if rc:
                self._rc(r[ra])
        elif xo == 922:  # extsh
            r[ra] = s16(r[rd]) & 0xFFFFFFFF
            if rc:
                self._rc(r[ra])
        elif xo == 26:  # cntlzw
            v = r[rd]
            r[ra] = 32 - v.bit_length()
        elif xo == 0:  # cmp
            x, y = s32(a), s32(b)
            self._set_cr(rd >> 2, x < y, x > y, x == y)
        elif xo == 32:  # cmpl
            self._set_cr(rd >> 2, a < b, a > b, a == b)
        elif xo in (23, 55, 87, 119, 279, 311, 343, 375, 151, 183, 215, 247, 407, 439,
                    535, 567, 663, 695, 599, 631, 727, 759):
            update = xo in (55, 119, 311, 375, 183, 247, 439, 567, 695, 631, 759)
            ea = ((r[ra] if ra or update else 0) + b) & 0xFFFFFFFF
            if xo in (23, 55):
                r[rd] = mem.u32(ea)
            elif xo in (87, 119):
                r[rd] = mem.u8(ea)
            elif xo in (279, 311):
                r[rd] = mem.u16(ea)
            elif xo in (343, 375):
                r[rd] = s16(mem.u16(ea)) & 0xFFFFFFFF
            elif xo in (151, 183):
                mem.w32(ea, r[rd])
            elif xo in (215, 247):
                mem.w8(ea, r[rd])
            elif xo in (407, 439):
                mem.w16(ea, r[rd])
            elif xo in (535, 567):
                self.f[rd] = self.ps1[rd] = mem.f32(ea)
            elif xo in (599, 631):
                self.f[rd] = mem.f64(ea)
            elif xo in (663, 695):
                mem.wf32(ea, self.f[rd])
            elif xo in (727, 759):
                mem.wf64(ea, self.f[rd])
            if update:
                r[ra] = ea
        elif xo == 983:  # stfiwx
            ea = ((r[ra] if ra else 0) + b) & 0xFFFFFFFF
            mem.w32(ea, struct.unpack(">Q", struct.pack(">d", self.f[rd]))[0] & 0xFFFFFFFF)
        elif xo == 339:  # mfspr
            spr = ((w >> 16) & 31) | (((w >> 11) & 31) << 5)
            if spr == 8:
                r[rd] = self.lr
            elif spr == 9:
                r[rd] = self.ctr
            elif spr == 1:
                r[rd] = self.xer_ca << 29
            elif 912 <= spr <= 919:
                r[rd] = self.gqr[spr - 912]
            else:
                r[rd] = 0
        elif xo == 467:  # mtspr
            spr = ((w >> 16) & 31) | (((w >> 11) & 31) << 5)
            if spr == 8:
                self.lr = r[rd]
            elif spr == 9:
                self.ctr = r[rd]
            elif spr == 1:
                self.xer_ca = (r[rd] >> 29) & 1
            elif 912 <= spr <= 919:
                self.gqr[spr - 912] = r[rd]
        elif xo == 19:  # mfcr
            v = 0
            for i in range(8):
                v = (v << 4) | self.cr[i]
            r[rd] = v
        elif xo == 144:  # mtcrf
            crm = (w >> 12) & 0xFF
            for i in range(8):
                if crm & (0x80 >> i):
                    self.cr[i] = (r[rd] >> (28 - 4 * i)) & 0xF
        elif xo in (86, 54, 278, 246, 1014, 598, 982, 470):  # cache ops, sync, icbi
            pass
        elif xo in (6, 7, 38, 39):  # psq_lx, psq_stx (paired single indexed)
            raise EmulationError(f"unexpected op31 xo {xo} at {self.pc:#x}")
        else:
            raise EmulationError(f"unsupported op31 xo {xo} ({w:#010x}) at {self.pc:#x}")

    def _op59(self, w: int, fd: int, fa: int, fb: int) -> None:
        f = self.f
        fc = (w >> 6) & 31
        xo = (w >> 1) & 31
        a, b, c = f[fa], f[fb], f[fc]
        if xo == 21:
            v = a + b
        elif xo == 20:
            v = a - b
        elif xo == 25:
            v = a * c
        elif xo == 18:
            v = a / b if b else math.copysign(math.inf, a) if a else math.nan
        elif xo == 29:
            v = a * c + b
        elif xo == 28:
            v = a * c - b
        elif xo == 31:
            v = -(a * c + b)
        elif xo == 30:
            v = -(a * c - b)
        elif xo == 24:
            v = 1.0 / b if b else math.inf
        elif xo == 22:
            v = math.sqrt(b) if b >= 0 else math.nan
        else:
            raise EmulationError(f"unsupported op59 xo {xo} at {self.pc:#x}")
        f[fd] = self.ps1[fd] = f32(v)
        if w & 1:
            self.cr[1] = 0

    def _op63(self, w: int, fd: int, fa: int, fb: int) -> None:
        f = self.f
        xo = (w >> 1) & 0x3FF
        xo5 = xo & 31
        fc = (w >> 6) & 31
        a, b, c = f[fa], f[fb], f[fc]
        if xo5 in (18, 20, 21, 23, 25, 26, 28, 29, 30, 31, 22):
            if xo5 == 21:
                v = a + b
            elif xo5 == 20:
                v = a - b
            elif xo5 == 25:
                v = a * c
            elif xo5 == 18:
                v = a / b if b else math.copysign(math.inf, a) if a else math.nan
            elif xo5 == 29:
                v = a * c + b
            elif xo5 == 28:
                v = a * c - b
            elif xo5 == 31:
                v = -(a * c + b)
            elif xo5 == 30:
                v = -(a * c - b)
            elif xo5 == 23:  # fsel
                v = c if a >= 0.0 else b
            elif xo5 == 26:  # frsqrte
                v = 1.0 / math.sqrt(b) if b > 0 else math.inf
            else:
                v = math.sqrt(b) if b >= 0 else math.nan
            f[fd] = v
        elif xo == 0 or xo == 32:  # fcmpu / fcmpo
            self._fcmp(fd >> 2, a, b)
        elif xo == 12:  # frsp
            f[fd] = self.ps1[fd] = f32(b)
        elif xo in (14, 15):  # fctiw(z)
            if math.isnan(b):
                iv = -0x80000000
            elif xo == 15:
                iv = int(b)
            else:
                iv = int(round(b))
            iv = max(-0x80000000, min(0x7FFFFFFF, iv))
            f[fd] = struct.unpack(">d", struct.pack(">II", 0xFFF80000, iv & 0xFFFFFFFF))[0]
        elif xo == 40:
            f[fd] = -b
        elif xo == 72:
            f[fd] = b
        elif xo == 136:
            f[fd] = -abs(b)
        elif xo == 264:
            f[fd] = abs(b)
        elif xo in (583, 711, 70, 38, 134, 64):  # mffs, mtfsf, mtfsb0/1, mtfsfi, mcrfs
            if xo == 583:
                f[fd] = 0.0
        else:
            raise EmulationError(f"unsupported op63 xo {xo} at {self.pc:#x}")

    def _op4(self, w: int, fd: int, fa: int, fb: int) -> None:
        f, p = self.f, self.ps1
        xo5 = (w >> 1) & 31
        xo6 = (w >> 1) & 0x3F
        xo10 = (w >> 1) & 0x3FF
        fc = (w >> 6) & 31
        if xo6 in (6, 7, 38, 39):  # psq_lx / psq_stx / psq_lux / psq_stux
            r = self.r
            ea = ((r[fa] if fa or xo6 in (38, 39) else 0) + r[fb]) & 0xFFFFFFFF
            qr = self.gqr[(w >> 7) & 7]
            single = (w >> 10) & 1
            self._psq(xo6 in (6, 38), fd, ea, single, qr)
            if xo6 in (38, 39):
                r[fa] = ea
            return
        if xo5 in (10, 11, 12, 13, 14, 15, 18, 20, 21, 23, 24, 25, 26, 28, 29, 30, 31):
            a0, a1, b0, b1, c0, c1 = f[fa], p[fa], f[fb], p[fb], f[fc], p[fc]
            if xo5 == 21:
                r0, r1 = a0 + b0, a1 + b1
            elif xo5 == 20:
                r0, r1 = a0 - b0, a1 - b1
            elif xo5 == 25:
                r0, r1 = a0 * c0, a1 * c1
            elif xo5 == 18:
                r0, r1 = (a0 / b0 if b0 else math.inf), (a1 / b1 if b1 else math.inf)
            elif xo5 == 29:
                r0, r1 = a0 * c0 + b0, a1 * c1 + b1
            elif xo5 == 28:
                r0, r1 = a0 * c0 - b0, a1 * c1 - b1
            elif xo5 == 31:
                r0, r1 = -(a0 * c0 + b0), -(a1 * c1 + b1)
            elif xo5 == 30:
                r0, r1 = -(a0 * c0 - b0), -(a1 * c1 - b1)
            elif xo5 == 23:
                r0, r1 = (c0 if a0 >= 0 else b0), (c1 if a1 >= 0 else b1)
            elif xo5 == 24:
                r0, r1 = (1 / b0 if b0 else math.inf), (1 / b1 if b1 else math.inf)
            elif xo5 == 26:
                r0 = 1 / math.sqrt(b0) if b0 > 0 else math.inf
                r1 = 1 / math.sqrt(b1) if b1 > 0 else math.inf
            elif xo5 == 10:  # ps_sum0
                r0, r1 = a0 + b1, c1
            elif xo5 == 11:  # ps_sum1
                r0, r1 = c0, a0 + b1
            elif xo5 == 12:  # ps_muls0
                r0, r1 = a0 * c0, a1 * c0
            elif xo5 == 13:  # ps_muls1
                r0, r1 = a0 * c1, a1 * c1
            elif xo5 == 14:  # ps_madds0
                r0, r1 = a0 * c0 + b0, a1 * c0 + b1
            else:  # 15 ps_madds1
                r0, r1 = a0 * c1 + b0, a1 * c1 + b1
            f[fd], p[fd] = f32(r0), f32(r1)
            return
        if xo10 == 72:  # ps_mr
            f[fd], p[fd] = f[fb], p[fb]
        elif xo10 == 40:  # ps_neg
            f[fd], p[fd] = -f[fb], -p[fb]
        elif xo10 == 264:
            f[fd], p[fd] = abs(f[fb]), abs(p[fb])
        elif xo10 == 136:
            f[fd], p[fd] = -abs(f[fb]), -abs(p[fb])
        elif xo10 == 528:
            f[fd], p[fd] = f[fa], f[fb]
        elif xo10 == 560:
            f[fd], p[fd] = f[fa], p[fb]
        elif xo10 == 592:
            f[fd], p[fd] = p[fa], f[fb]
        elif xo10 == 624:
            f[fd], p[fd] = p[fa], p[fb]
        elif xo10 in (0, 32):
            self._fcmp(fd >> 2, f[fa], f[fb])
        elif xo10 in (64, 96):
            self._fcmp(fd >> 2, p[fa], p[fb])
        else:
            raise EmulationError(f"unsupported paired single xo {xo10} at {self.pc:#x}")


# --------------------------------------------------------------------------- fast path
# Hot instructions are decoded once per address into (handler, fields...); a
# handler returns the next pc. Everything else goes through Machine.step.

_M32 = 0xFFFFFFFF
_pack_f, _unpack_f = _F32.pack, _F32.unpack


def _rf(v: float) -> float:
    try:
        return _unpack_f(_pack_f(v))[0]
    except OverflowError:
        return math.copysign(math.inf, v)


def _h_generic(m: Machine, pc: int, d) -> int:
    m.pc = pc
    m.step()
    return m.pc


def _h_addi(m, pc, d):
    r = m.r
    r[d[1]] = (d[3] + (r[d[2]] if d[2] else 0)) & _M32
    return pc + 4


def _h_lwz(m, pc, d):
    r = m.r
    r[d[1]] = m.mem.u32(((r[d[2]] if d[2] else 0) + d[3]) & _M32)
    return pc + 4


def _h_stw(m, pc, d):
    r = m.r
    m.mem.w32(((r[d[2]] if d[2] else 0) + d[3]) & _M32, r[d[1]])
    return pc + 4


def _h_lfs(m, pc, d):
    r = m.r
    m.f[d[1]] = m.ps1[d[1]] = m.mem.f32(((r[d[2]] if d[2] else 0) + d[3]) & _M32)
    return pc + 4


def _h_stfs(m, pc, d):
    r = m.r
    m.mem.wf32(((r[d[2]] if d[2] else 0) + d[3]) & _M32, m.f[d[1]])
    return pc + 4


def _h_lfd(m, pc, d):
    r = m.r
    m.f[d[1]] = m.mem.f64(((r[d[2]] if d[2] else 0) + d[3]) & _M32)
    return pc + 4


def _h_stfd(m, pc, d):
    r = m.r
    m.mem.wf64(((r[d[2]] if d[2] else 0) + d[3]) & _M32, m.f[d[1]])
    return pc + 4


def _h_psq_l(m, pc, d):
    r = m.r
    m._psq(True, d[1], ((r[d[2]] if d[2] else 0) + d[3]) & _M32, d[4], m.gqr[d[5]])
    return pc + 4


def _h_psq_st(m, pc, d):
    r = m.r
    m._psq(False, d[1], ((r[d[2]] if d[2] else 0) + d[3]) & _M32, d[4], m.gqr[d[5]])
    return pc + 4


def _h_b(m, pc, d):
    if d[2]:
        m.lr = pc + 4
    return d[1]


def _h_blr(m, pc, d):
    return m.lr & ~3


def _h_blrl(m, pc, d):
    target = m.lr & ~3
    m.lr = pc + 4
    return target


def _h_mr(m, pc, d):
    m.r[d[1]] = m.r[d[2]]
    return pc + 4


def _h_mtlr(m, pc, d):
    m.lr = m.r[d[1]]
    return pc + 4


def _h_mflr(m, pc, d):
    m.r[d[1]] = m.lr
    return pc + 4


def _h_fmr(m, pc, d):
    m.f[d[1]] = m.f[d[2]]
    return pc + 4


def _h_fneg(m, pc, d):
    m.f[d[1]] = -m.f[d[2]]
    return pc + 4


def _h_fadds(m, pc, d):
    f = m.f
    f[d[1]] = m.ps1[d[1]] = _rf(f[d[2]] + f[d[3]])
    return pc + 4


def _h_fsubs(m, pc, d):
    f = m.f
    f[d[1]] = m.ps1[d[1]] = _rf(f[d[2]] - f[d[3]])
    return pc + 4


def _h_fmuls(m, pc, d):
    f = m.f
    f[d[1]] = m.ps1[d[1]] = _rf(f[d[2]] * f[d[4]])
    return pc + 4


def _h_fmadds(m, pc, d):
    f = m.f
    f[d[1]] = m.ps1[d[1]] = _rf(f[d[2]] * f[d[4]] + f[d[3]])
    return pc + 4


def _h_fmsubs(m, pc, d):
    f = m.f
    f[d[1]] = m.ps1[d[1]] = _rf(f[d[2]] * f[d[4]] - f[d[3]])
    return pc + 4


def _h_fnmsubs(m, pc, d):
    f = m.f
    f[d[1]] = m.ps1[d[1]] = _rf(-(f[d[2]] * f[d[4]] - f[d[3]]))
    return pc + 4


def _h_ps_madd(m, pc, d):
    f, p = m.f, m.ps1
    a, b, c = d[2], d[3], d[4]
    r0, r1 = f[a] * f[c] + f[b], p[a] * p[c] + p[b]
    f[d[1]], p[d[1]] = _rf(r0), _rf(r1)
    return pc + 4


def _h_ps_mul(m, pc, d):
    f, p = m.f, m.ps1
    a, c = d[2], d[4]
    r0, r1 = f[a] * f[c], p[a] * p[c]
    f[d[1]], p[d[1]] = _rf(r0), _rf(r1)
    return pc + 4


def _h_ps_add(m, pc, d):
    f, p = m.f, m.ps1
    a, b = d[2], d[3]
    r0, r1 = f[a] + f[b], p[a] + p[b]
    f[d[1]], p[d[1]] = _rf(r0), _rf(r1)
    return pc + 4


def _h_ps_sub(m, pc, d):
    f, p = m.f, m.ps1
    a, b = d[2], d[3]
    r0, r1 = f[a] - f[b], p[a] - p[b]
    f[d[1]], p[d[1]] = _rf(r0), _rf(r1)
    return pc + 4


def _h_cmpwi(m, pc, d):
    a = s32(m.r[d[2]])
    v = d[3]
    m.cr[d[1]] = ((a < v) << 3) | ((a > v) << 2) | ((a == v) << 1)
    return pc + 4


def _h_cmplwi(m, pc, d):
    a = m.r[d[2]]
    v = d[3]
    m.cr[d[1]] = ((a < v) << 3) | ((a > v) << 2) | ((a == v) << 1)
    return pc + 4


def _h_rlwinm(m, pc, d):
    m.r[d[1]] = _rotl(m.r[d[2]], d[3]) & d[4]
    return pc + 4


def _h_add(m, pc, d):
    r = m.r
    r[d[1]] = (r[d[2]] + r[d[3]]) & _M32
    return pc + 4


def _h_mulli(m, pc, d):
    r = m.r
    r[d[1]] = (s32(r[d[2]]) * d[3]) & _M32
    return pc + 4


_FP59 = {21: _h_fadds, 20: _h_fsubs, 25: _h_fmuls, 29: _h_fmadds, 28: _h_fmsubs, 30: _h_fnmsubs}
_PS4 = {29: _h_ps_madd, 25: _h_ps_mul, 21: _h_ps_add, 20: _h_ps_sub}


def _decode(m: Machine, pc: int) -> tuple:
    w = m._code.get(pc)
    if w is None:
        w = m._code[pc] = m.mem.u32(pc)
    op = w >> 26
    rd, ra, rb = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
    rc = (w >> 6) & 31
    simm = s16(w)
    if op == 14:
        return (_h_addi, rd, ra, simm)
    if op == 32:
        return (_h_lwz, rd, ra, simm)
    if op == 36:
        return (_h_stw, rd, ra, simm)
    if op == 48:
        return (_h_lfs, rd, ra, simm)
    if op == 52:
        return (_h_stfs, rd, ra, simm)
    if op == 50:
        return (_h_lfd, rd, ra, simm)
    if op == 54:
        return (_h_stfd, rd, ra, simm)
    if op in (56, 60):
        disp = w & 0xFFF
        disp = disp - 0x1000 if disp & 0x800 else disp
        return (_h_psq_l if op == 56 else _h_psq_st, rd, ra, disp, (w >> 15) & 1, (w >> 12) & 7)
    if op == 18:
        li = w & 0x03FFFFFC
        if li & 0x02000000:
            li -= 0x04000000
        return (_h_b, (li if w & 2 else pc + li) & _M32, w & 1)
    if op == 19 and ((w >> 1) & 0x3FF) == 16 and (rd & 0x14) == 0x14:  # unconditional bclr
        return (_h_blrl if w & 1 else _h_blr,)
    if op == 31:
        xo = (w >> 1) & 0x3FF
        spr = ra | (rb << 5)
        if xo == 444 and rd == rb and not w & 1:
            return (_h_mr, ra, rd)
        if xo == 467 and spr == 8:
            return (_h_mtlr, rd)
        if xo == 339 and spr == 8:
            return (_h_mflr, rd)
        if xo == 266 and not w & 1:
            return (_h_add, rd, ra, rb)
    if op == 59 and not w & 1:
        h = _FP59.get((w >> 1) & 31)
        if h is not None:
            return (h, rd, ra, rb, rc)
    if op == 63 and not w & 1:
        xo = (w >> 1) & 0x3FF
        if xo == 72:
            return (_h_fmr, rd, rb)
        if xo == 40:
            return (_h_fneg, rd, rb)
    if op == 4 and not w & 1 and ((w >> 1) & 0x3F) not in (6, 7, 38, 39):
        h = _PS4.get((w >> 1) & 31)
        if h is not None:
            return (h, rd, ra, rb, rc)
    if op == 11 and not (rd & 1):
        return (_h_cmpwi, rd >> 2, ra, simm)
    if op == 10 and not (rd & 1):
        return (_h_cmplwi, rd >> 2, ra, w & 0xFFFF)
    if op == 21 and not w & 1:
        return (_h_rlwinm, ra, rd, rb, _mask((w >> 6) & 31, (w >> 1) & 31))
    if op == 7:
        return (_h_mulli, rd, ra, simm)
    return (_h_generic,)


def _h_addis(m, pc, d):
    r = m.r
    r[d[1]] = (d[3] + (r[d[2]] if d[2] else 0)) & _M32
    return pc + 4


def _h_bc(m, pc, d):
    # d: (h, bo, bi, target, link)
    bo = d[1]
    if not bo & 4:
        m.ctr = (m.ctr - 1) & _M32
        if (m.ctr != 0) == bool(bo & 2):
            return pc + 4
    if not bo & 16:
        bi = d[2]
        if ((m.cr[bi >> 2] >> (3 - (bi & 3))) & 1) != ((bo >> 3) & 1):
            return pc + 4
    if d[4]:
        m.lr = pc + 4
    return d[3]


def _h_ps_muls0(m, pc, d):
    f, p = m.f, m.ps1
    c = f[d[4]]
    r0, r1 = f[d[2]] * c, p[d[2]] * c
    f[d[1]], p[d[1]] = _rf(r0), _rf(r1)
    return pc + 4


def _h_ps_muls1(m, pc, d):
    f, p = m.f, m.ps1
    c = p[d[4]]
    r0, r1 = f[d[2]] * c, p[d[2]] * c
    f[d[1]], p[d[1]] = _rf(r0), _rf(r1)
    return pc + 4


def _h_ps_madds0(m, pc, d):
    f, p = m.f, m.ps1
    a, b, c = d[2], d[3], f[d[4]]
    r0, r1 = f[a] * c + f[b], p[a] * c + p[b]
    f[d[1]], p[d[1]] = _rf(r0), _rf(r1)
    return pc + 4


def _h_ps_madds1(m, pc, d):
    f, p = m.f, m.ps1
    a, b, c = d[2], d[3], p[d[4]]
    r0, r1 = f[a] * c + f[b], p[a] * c + p[b]
    f[d[1]], p[d[1]] = _rf(r0), _rf(r1)
    return pc + 4


def _h_ps_sum0(m, pc, d):
    f, p = m.f, m.ps1
    r0, r1 = f[d[2]] + p[d[3]], p[d[4]]
    f[d[1]], p[d[1]] = _rf(r0), _rf(r1)
    return pc + 4


def _h_ps_sum1(m, pc, d):
    f, p = m.f, m.ps1
    r0, r1 = f[d[4]], f[d[2]] + p[d[3]]
    f[d[1]], p[d[1]] = _rf(r0), _rf(r1)
    return pc + 4


def _h_ps_merge(m, pc, d):
    # d: (h, fd, fa, fb, hi_a, hi_b)
    f, p = m.f, m.ps1
    v0 = p[d[2]] if d[4] else f[d[2]]
    v1 = p[d[3]] if d[5] else f[d[3]]
    f[d[1]], p[d[1]] = v0, v1
    return pc + 4


def _h_fmul(m, pc, d):
    f = m.f
    f[d[1]] = f[d[2]] * f[d[4]]
    return pc + 4


def _h_fmadd(m, pc, d):
    f = m.f
    f[d[1]] = f[d[2]] * f[d[4]] + f[d[3]]
    return pc + 4


def _h_fadd(m, pc, d):
    f = m.f
    f[d[1]] = f[d[2]] + f[d[3]]
    return pc + 4


def _h_fsub(m, pc, d):
    f = m.f
    f[d[1]] = f[d[2]] - f[d[3]]
    return pc + 4


def _h_frsp(m, pc, d):
    m.f[d[1]] = m.ps1[d[1]] = _rf(m.f[d[2]])
    return pc + 4


def _h_fabs(m, pc, d):
    m.f[d[1]] = abs(m.f[d[2]])
    return pc + 4


def _h_fcmp(m, pc, d):
    a, b = m.f[d[2]], m.f[d[3]]
    if a != a or b != b:
        m.cr[d[1]] = 1
    else:
        m.cr[d[1]] = ((a < b) << 3) | ((a > b) << 2) | ((a == b) << 1)
    return pc + 4


def _h_andi(m, pc, d):
    v = m.r[d[2]] & d[3]
    m.r[d[1]] = v
    sv = s32(v)
    m.cr[0] = ((sv < 0) << 3) | ((sv > 0) << 2) | ((sv == 0) << 1)
    return pc + 4


def _h_cmpw(m, pc, d):
    a, b = s32(m.r[d[2]]), s32(m.r[d[3]])
    m.cr[d[1]] = ((a < b) << 3) | ((a > b) << 2) | ((a == b) << 1)
    return pc + 4


def _h_cmplw(m, pc, d):
    a, b = m.r[d[2]], m.r[d[3]]
    m.cr[d[1]] = ((a < b) << 3) | ((a > b) << 2) | ((a == b) << 1)
    return pc + 4


def _h_stwu(m, pc, d):
    r = m.r
    ea = (r[d[2]] + d[3]) & _M32
    m.mem.w32(ea, r[d[1]])
    r[d[2]] = ea
    return pc + 4


def _h_lhz(m, pc, d):
    r = m.r
    r[d[1]] = m.mem.u16(((r[d[2]] if d[2] else 0) + d[3]) & _M32)
    return pc + 4


def _h_lbz(m, pc, d):
    r = m.r
    r[d[1]] = m.mem.u8(((r[d[2]] if d[2] else 0) + d[3]) & _M32)
    return pc + 4


def _h_xoris(m, pc, d):
    m.r[d[1]] = m.r[d[2]] ^ d[3]
    return pc + 4


def _h_ori(m, pc, d):
    m.r[d[1]] = m.r[d[2]] | d[3]
    return pc + 4


_PS_EXTRA = {12: _h_ps_muls0, 13: _h_ps_muls1, 14: _h_ps_madds0, 15: _h_ps_madds1,
             10: _h_ps_sum0, 11: _h_ps_sum1}
_MERGE = {528: (0, 0), 560: (0, 1), 592: (1, 0), 624: (1, 1)}
_FP63 = {25: _h_fmul, 29: _h_fmadd, 21: _h_fadd, 20: _h_fsub}


def _decode2(m: Machine, pc: int) -> tuple:
    t = _decode1(m, pc)
    if t[0] is not _h_generic:
        return t
    w = m._code[pc]
    op = w >> 26
    rd, ra, rb = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
    rc = (w >> 6) & 31
    simm = s16(w)
    if op == 15:
        return (_h_addis, rd, ra, (simm << 16) & _M32)
    if op == 16:
        bd = w & 0xFFFC
        if bd & 0x8000:
            bd -= 0x10000
        return (_h_bc, rd, ra, (bd if w & 2 else pc + bd) & _M32, w & 1)
    if op == 4 and not w & 1:
        x6 = (w >> 1) & 0x3F
        x10 = (w >> 1) & 0x3FF
        if x6 not in (6, 7, 38, 39):
            if x10 in _MERGE:
                hi_a, hi_b = _MERGE[x10]
                return (_h_ps_merge, rd, ra, rb, hi_a, hi_b)
            h = _PS_EXTRA.get((w >> 1) & 31)
            if h is not None and x10 not in (0, 32, 40, 64, 72, 96, 136, 264):
                return (h, rd, ra, rb, rc)
    if op == 63 and not w & 1:
        xo = (w >> 1) & 0x3FF
        h = _FP63.get(xo & 31)
        if h is not None:  # A-form: the low five bits identify these uniquely
            return (h, rd, ra, rb, rc)
        if xo == 12:
            return (_h_frsp, rd, rb)
        if xo == 264:
            return (_h_fabs, rd, rb)
        if xo in (0, 32):
            return (_h_fcmp, rd >> 2, ra, rb)
    if op == 28:
        return (_h_andi, ra, rd, w & 0xFFFF)
    if op == 27:
        return (_h_xoris, ra, rd, (w & 0xFFFF) << 16)
    if op == 24:
        return (_h_ori, ra, rd, w & 0xFFFF)
    if op == 37 and ra:
        return (_h_stwu, rd, ra, simm)
    if op == 40:
        return (_h_lhz, rd, ra, simm)
    if op == 34:
        return (_h_lbz, rd, ra, simm)
    if op == 31 and not (rd & 1):
        xo = (w >> 1) & 0x3FF
        if xo == 0:
            return (_h_cmpw, rd >> 2, ra, rb)
        if xo == 32:
            return (_h_cmplw, rd >> 2, ra, rb)
    return t


_decode1 = _decode
_decode = _decode2
