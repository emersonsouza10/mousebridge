import unittest

from zephyrlink.transport.framing import MAX_FRAME_SIZE, FrameDecoder, encode_frame
from zephyrlink.transport.messages import (
    Message,
    MsgType,
    ProtocolError,
    VideoFrame,
    is_video_frame,
)


class VideoFrameTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        frame = VideoFrame({"w": 1920, "h": 1080, "fmt": "jpeg", "seq": 7}, b"\xff\xd8\xff\x00dados")
        decoded = VideoFrame.decode(frame.encode())
        self.assertEqual(decoded.meta, frame.meta)
        self.assertEqual(decoded.blob, frame.blob)

    def test_empty_blob(self) -> None:
        frame = VideoFrame({"seq": 0}, b"")
        decoded = VideoFrame.decode(frame.encode())
        self.assertEqual(decoded.blob, b"")
        self.assertEqual(decoded.meta, {"seq": 0})

    def test_is_video_frame_true_for_binary(self) -> None:
        self.assertTrue(is_video_frame(VideoFrame({"seq": 1}, b"x").encode()))

    def test_is_video_frame_false_for_json_message(self) -> None:
        # Toda Message JSON começa com '{' (0x7b), nunca com o tag 0x01.
        for mtype in (MsgType.RD_START, MsgType.INPUT_MOVE_ABS, MsgType.PING):
            encoded = Message(mtype, {"x": 1}).encode()
            self.assertFalse(is_video_frame(encoded))
            self.assertEqual(encoded[0], ord("{"))

    def test_is_video_frame_false_for_empty(self) -> None:
        self.assertFalse(is_video_frame(b""))

    def test_truncated_header_raises(self) -> None:
        with self.assertRaises(ProtocolError):
            VideoFrame.decode(b"\x01\x00")  # tag + meta_len incompleto

    def test_truncated_meta_raises(self) -> None:
        # anuncia 50 bytes de meta mas não os fornece
        payload = b"\x01" + (50).to_bytes(4, "big") + b"{}"
        with self.assertRaises(ProtocolError):
            VideoFrame.decode(payload)

    def test_meta_not_object_raises(self) -> None:
        meta = b"[1,2,3]"
        payload = b"\x01" + len(meta).to_bytes(4, "big") + meta
        with self.assertRaises(ProtocolError):
            VideoFrame.decode(payload)

    def test_large_frame_within_limit_framing(self) -> None:
        # Um JPEG grande, porém dentro do MAX_FRAME_SIZE, faz o framing normal.
        blob = b"\x00" * (2 * 1024 * 1024)
        payload = VideoFrame({"w": 3840, "h": 2160}, blob).encode()
        self.assertLess(len(payload), MAX_FRAME_SIZE)
        decoder = FrameDecoder()
        frames = decoder.feed(encode_frame(payload))
        self.assertEqual(len(frames), 1)
        self.assertEqual(VideoFrame.decode(frames[0]).blob, blob)


if __name__ == "__main__":
    unittest.main()
