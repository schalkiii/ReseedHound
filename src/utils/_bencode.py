from typing import Any


def bencode(data: Any) -> bytes:
    if isinstance(data, bytes):
        return str(len(data)).encode() + b":" + data
    if isinstance(data, str):
        encoded = data.encode("utf-8")
        return str(len(encoded)).encode() + b":" + encoded
    if isinstance(data, int):
        return b"i" + str(data).encode() + b"e"
    if isinstance(data, list):
        result = b"l"
        for item in data:
            result += bencode(item)
        return result + b"e"
    if isinstance(data, dict):
        result = b"d"
        for key in sorted(data.keys(), key=lambda k: k if isinstance(k, bytes) else k.encode()):
            result += bencode(key) + bencode(data[key])
        return result + b"e"
    raise TypeError(f"Unsupported type for bencode: {type(data)}")


def bdecode(data: bytes) -> tuple:
    def _decode(data: bytes, pos: int) -> tuple:
        if pos >= len(data):
            return None, pos
        ch = data[pos:pos + 1]
        if ch == b"i":
            end = data.index(b"e", pos)
            return int(data[pos + 1:end]), end + 1
        if ch == b"l":
            result = []
            pos += 1
            while data[pos:pos + 1] != b"e":
                item, pos = _decode(data, pos)
                result.append(item)
            return result, pos + 1
        if ch == b"d":
            result = {}
            pos += 1
            while data[pos:pos + 1] != b"e":
                key, pos = _decode(data, pos)
                value, pos = _decode(data, pos)
                result[key] = value
            return result, pos + 1
        if ch in b"0123456789":
            colon = data.index(b":", pos)
            length = int(data[pos:colon])
            start = colon + 1
            return data[start:start + length], start + length
        raise ValueError(f"Invalid bencode at position {pos}: {data[pos:pos + 10]}")

    result, _ = _decode(data, 0)
    return result
