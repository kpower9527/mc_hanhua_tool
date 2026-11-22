"""二进制 NBT（建筑模板 .nbt 等）的最小读写。

只解析出结构和字符串的位置：字符串节点记录其在解压后数据中的字节区间（含 2 字节长度前缀），
替换文本时按区间改写字节即可，NBT 中没有记录复合标签字节长度的字段，其余内容保持原样。
"""
from __future__ import annotations

import gzip
import struct
import zlib

from .snbt import SString


class NBTError(ValueError):
    pass


def decompress(data: bytes) -> tuple[bytes, str]:
    """返回 (解压后的数据, 压缩方式)，压缩方式为 gzip / zlib / none。"""
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data), "gzip"
    if data[:1] == b"\x78":
        try:
            return zlib.decompress(data), "zlib"
        except zlib.error:
            pass
    return data, "none"


def compress(data: bytes, method: str) -> bytes:
    if method == "gzip":
        return gzip.compress(data, mtime=0)
    if method == "zlib":
        return zlib.compress(data)
    return data


def decode_mutf8(raw: bytes) -> str:
    text = raw.replace(b"\xc0\x80", b"\x00").decode("utf-8", "surrogatepass")
    # Java 把增补字符存成两个各 3 字节的代理项，这里合并回真实字符
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def encode_mutf8(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if code == 0:
            out += b"\xc0\x80"
        elif code > 0xFFFF:
            pair = ch.encode("utf-16-le")
            for i in (0, 2):
                out += chr(int.from_bytes(pair[i:i + 2], "little")).encode("utf-8", "surrogatepass")
        else:
            out += ch.encode("utf-8", "surrogatepass")
    return bytes(out)


_FIXED = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}
_ARRAY = {7: 1, 11: 4, 12: 8}


class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.i = 0

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.d):
            raise NBTError("NBT 数据不完整")
        chunk = self.d[self.i:self.i + n]
        self.i += n
        return chunk

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def string(self) -> SString:
        start = self.i
        raw = self.take(self.u16())
        return SString(decode_mutf8(raw), start, self.i)

    def payload(self, tag: int):
        if tag in _FIXED:
            self.take(_FIXED[tag])
            return None
        if tag in _ARRAY:
            n = self.i32()
            self.take(max(n, 0) * _ARRAY[tag])
            return None
        if tag == 8:
            return self.string()
        if tag == 9:
            elem, n = self.take(1)[0], self.i32()
            return [self.payload(elem) for _ in range(max(n, 0))]
        if tag == 10:
            out: dict = {}
            while True:
                t = self.take(1)[0]
                if t == 0:
                    return out
                name = self.string().value
                out[name] = self.payload(t)
        raise NBTError(f"未知的 NBT 标签类型 {tag}")


def parse(data: bytes):
    """解析未压缩的 NBT 数据，返回根复合标签（dict）；字符串为 SString，数字等为 None。"""
    r = _Reader(data)
    tag = r.take(1)[0]
    if tag != 10:
        raise NBTError("根标签不是复合标签")
    r.string()
    return r.payload(10)


def replace_strings(data: bytes, replacements: list[tuple[int, int, str]]) -> bytes:
    out = data
    for start, end, value in sorted(replacements, key=lambda r: r[0], reverse=True):
        raw = encode_mutf8(value)
        if len(raw) > 0xFFFF:
            raise NBTError("字符串过长，无法写入 NBT")
        out = out[:start] + struct.pack(">H", len(raw)) + raw + out[end:]
    return out
