import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinyredis.client import ResponseError, _parse_reply
from tinyredis.protocol import encode_command


class ParseReplyTest(unittest.TestCase):
    def _one(self, wire):
        reply, consumed = _parse_reply(wire)
        self.assertEqual(consumed, len(wire))
        return reply

    def test_simple_error_int(self):
        self.assertEqual(self._one(b"+OK\r\n"), "OK")
        self.assertEqual(self._one(b":1024\r\n"), 1024)
        with self.assertRaises(ResponseError):
            _parse_reply(b"-ERR nope\r\n")

    def test_bulk_and_nil(self):
        self.assertEqual(self._one(b"$5\r\nhello\r\n"), b"hello")
        self.assertIsNone(self._one(b"$-1\r\n"))

    def test_binary_bulk_with_crlf_inside(self):
        # 内容 b"a\r\nb"（4 字节）里包含 CRLF，必须按长度读而不是按 CRLF 读。
        self.assertEqual(self._one(b"$4\r\na\r\nb\r\n"), b"a\r\nb")

    def test_array_nested(self):
        wire = b"*3\r\n:1\r\n$2\r\nhi\r\n*-1\r\n"
        self.assertEqual(self._one(wire), [1, b"hi", None])

    def test_empty_array(self):
        self.assertEqual(self._one(b"*0\r\n"), [])

    def test_partial_returns_not_consumed(self):
        self.assertEqual(_parse_reply(b"+OK"), (None, 0))
        self.assertEqual(_parse_reply(b"$5\r\nhel"), (None, 0))
        self.assertEqual(_parse_reply(b"*2\r\n$2\r\nhi\r\n"), (None, 0))

    def test_unknown_type_raises(self):
        with self.assertRaises(Exception):
            _parse_reply(b"%2\r\n")

    def test_trailing_garbage_not_consumed(self):
        reply, consumed = _parse_reply(b"+OK\r\nEXTRA")
        self.assertEqual(reply, "OK")
        self.assertEqual(consumed, 5)


class EncodeCommandTest(unittest.TestCase):
    def test_client_encoder_matches_server_parser(self):
        from tinyredis.protocol import RespParser
        wire = encode_command(["SET", "k", 5])
        parser = RespParser()
        parser.feed(wire)
        self.assertEqual(parser.next_command(), [b"SET", b"k", b"5"])

    def test_bytes_passthrough(self):
        self.assertEqual(encode_command([b"\x00\xff"]),
                         b"*1\r\n$2\r\n\x00\xff\r\n")


if __name__ == "__main__":
    unittest.main()
