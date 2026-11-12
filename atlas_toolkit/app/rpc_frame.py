"""Binary RPC frames: a length-prefixed JSON header and concatenated buffers."""

import json
import struct


def encode_frame(header: dict, buffers: list[bytes]) -> bytes:
    """Encode a header and buffers without modifying the caller's header."""
    if "sizes" in header:
        raise ValueError("sizes is reserved")
    raw = json.dumps(
        {**header, "sizes": [len(buffer) for buffer in buffers]},
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return b"".join((struct.pack("<I", len(raw)), raw, *buffers))


def decode_frame(data: bytes) -> tuple[dict, list[bytes]]:
    """Decode a complete frame, rejecting malformed lengths and headers."""
    if len(data) < 4:
        raise ValueError("Missing header length")
    end = 4 + struct.unpack_from("<I", data)[0]
    if end > len(data):
        raise ValueError("Truncated header")
    try:
        header = json.loads(str(data[4:end], "utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON header") from exc
    if not isinstance(header, dict):
        raise ValueError("Header must be an object")
    sizes = header.pop("sizes", None)
    if not isinstance(sizes, list) or any(type(size) is not int or size < 0 for size in sizes):
        raise ValueError("Invalid buffer sizes")
    if sum(sizes) != len(data) - end:
        raise ValueError("Buffer sizes do not match body length")
    buffers = []
    for size in sizes:
        buffers.append(bytes(data[end:end + size]))
        end += size
    return header, buffers
