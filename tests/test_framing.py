import unittest

from zephyrlink.transport.framing import (
    HEADER,
    MAX_FRAME_SIZE,
    FrameDecoder,
    FrameError,
    encode_frame,
)


class EncodeFrameTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        payload = b'{"t":"ping","d":{}}'
        frame = encode_frame(payload)
        self.assertEqual(frame[: HEADER.size], HEADER.pack(len(payload)))
        self.assertEqual(frame[HEADER.size :], payload)

    def test_empty_payload(self) -> None:
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(encode_frame(b"")), [b""])

    def test_oversized_payload_rejected(self) -> None:
        with self.assertRaises(FrameError):
            encode_frame(b"x" * (MAX_FRAME_SIZE + 1))


class FrameDecoderTest(unittest.TestCase):
    def test_single_frame(self) -> None:
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(encode_frame(b"hello")), [b"hello"])

    def test_multiple_frames_in_one_chunk(self) -> None:
        decoder = FrameDecoder()
        data = encode_frame(b"one") + encode_frame(b"two") + encode_frame(b"three")
        self.assertEqual(decoder.feed(data), [b"one", b"two", b"three"])

    def test_partial_delivery_byte_by_byte(self) -> None:
        decoder = FrameDecoder()
        frames: list[bytes] = []
        for byte in encode_frame(b"slow"):
            frames.extend(decoder.feed(bytes([byte])))
        self.assertEqual(frames, [b"slow"])

    def test_frame_split_across_chunks(self) -> None:
        decoder = FrameDecoder()
        data = encode_frame(b"abcdef")
        self.assertEqual(decoder.feed(data[:5]), [])
        self.assertEqual(decoder.feed(data[5:]), [b"abcdef"])

    def test_announced_oversize_raises(self) -> None:
        decoder = FrameDecoder()
        with self.assertRaises(FrameError):
            decoder.feed(HEADER.pack(100 * 1024 * 1024))


if __name__ == "__main__":
    unittest.main()
