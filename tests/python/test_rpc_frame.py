import json
import struct
import unittest

from atlas_toolkit.app.rpc_frame import decode_frame, encode_frame


class RpcFrameTests(unittest.TestCase):
    def test_pinned_vector(self):
        header = {"method": "m"}
        frame = encode_frame(header, [b"ab", b""])
        self.assertEqual(frame.hex(), "1c0000007b226d6574686f64223a226d222c2273697a6573223a5b322c305d7d6162")
        self.assertEqual(decode_frame(frame), (header, [b"ab", b""]))
        self.assertEqual(header, {"method": "m"})

    def test_pinned_unicode_vector(self):
        header = {"method": "ทดสอบ\n\"\\\u0001\u2028😀"}
        frame = encode_frame(header, [b"\xff"])
        self.assertEqual(frame.hex(), "3b0000007b226d6574686f64223a22e0b897e0b894e0b8aae0b8ade0b89a5c6e5c225c5c5c7530303031e280a8f09f9880222c2273697a6573223a5b315d7dff")
        self.assertEqual(decode_frame(frame), (header, [b"\xff"]))

    def test_memoryview(self):
        self.assertEqual(decode_frame(memoryview(encode_frame({}, [b"x"]))), ({}, [b"x"]))

    def test_unicode_and_large_buffer(self):
        header = {"method": "ทดสอบ", "argsJson": "雪"}
        buffers = [b"x" * 1_000_000, b"", b"\x00\xff"]
        self.assertEqual(decode_frame(encode_frame(header, buffers)), (header, buffers))
        self.assertEqual(decode_frame(encode_frame({}, [])), ({}, []))

    def test_reserved_sizes(self):
        with self.assertRaises(ValueError):
            encode_frame({"sizes": []}, [])

    def test_invalid_frames(self):
        frames = [b"", b"abc", struct.pack("<I", 10) + b"{}"]
        for header in [None, [], 1, {}, {"sizes": None}, {"sizes": [-1]}, {"sizes": [-1, 2]},
                       {"sizes": [1.5]}, {"sizes": [True]}, {"sizes": ["1"]},
                       {"sizes": [2]}, {"sizes": []}]:
            raw = json.dumps(header).encode()
            frames.append(struct.pack("<I", len(raw)) + raw + b"x")
        for raw in [b"{", b"\xff", b'{"method":"\xff","sizes":[]}']:
            frames.append(struct.pack("<I", len(raw)) + raw)
        frames.append(encode_frame({}, [b"ab"])[:-1])
        for frame in frames:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                decode_frame(frame)
