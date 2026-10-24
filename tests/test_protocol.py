import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinyredis import protocol
from tinyredis.protocol import RespError, RespParser, encode_command


def feed_command(parser, *args):
    parser.feed(encode_command(args))
    return parser.next_command()


class ParserArrayTest(unittest.TestCase):
    def test_simple_array(self):
        parser = RespParser()
        self.assertEqual(feed_command(parser, "GET", "key"), [b"GET", b"key"])

    def test_binary_safe_payload(self):
        parser = RespParser()
        parser.feed(b"*1\r\n$4\r\na\r\nb\r\n")
        self.assertEqual(parser.next_command(), [b"a\r\nb"])

    def test_partial_feed_byte_by_byte(self):
        parser = RespParser()
        wire = encode_command(["SET", "k", "v"])
        for i, byte in enumerate(wire):
            parser.feed(bytes([byte]))
            result = parser.next_command()
            if i < len(wire) - 1:
                self.assertIsNone(result)
        self.assertEqual(result, [b"SET", b"k", b"v"])

    def test_two_commands_in_one_chunk(self):
        parser = RespParser()
        parser.feed(encode_command(["PING"]) + encode_command(["ECHO", "hi"]))
        self.assertEqual(parser.next_command(), [b"PING"])
        self.assertEqual(parser.next_command(), [b"ECHO", b"hi"])
        self.assertIsNone(parser.next_command())

    def test_empty_array(self):
        parser = RespParser()
        parser.feed(b"*0\r\n")
        self.assertEqual(parser.next_command(), [])

    def test_utf8_payload(self):
        parser = RespParser()
        self.assertEqual(feed_command(parser, "SET", "键", "值"),
                         [b"SET", "键".encode(), "值".encode()])


class ParserInlineTest(unittest.TestCase):
    def test_inline_command(self):
        parser = RespParser()
        parser.feed(b"PING\r\n")
        self.assertEqual(parser.next_command(), [b"PING"])

    def test_inline_split_on_spaces(self):
        parser = RespParser()
        parser.feed(b"SET k v\r\n")
        self.assertEqual(parser.next_command(), [b"SET", b"k", b"v"])

    def test_inline_quoted(self):
        parser = RespParser()
        parser.feed(b'SET "my key" "my value"\r\n')
        self.assertEqual(parser.next_command(), [b"SET", b"my key", b"my value"])

    def test_blank_line_returns_empty(self):
        parser = RespParser()
        parser.feed(b"\r\n")
        self.assertEqual(parser.next_command(), [])

    def test_inline_without_newline_waits(self):
        parser = RespParser()
        parser.feed(b"PING")
        self.assertIsNone(parser.next_command())


class ParserErrorTest(unittest.TestCase):
    def _expect_error(self, wire):
        parser = RespParser()
        parser.feed(wire)
        with self.assertRaises(RespError):
            parser.next_command()

    def test_bad_multibulk_header(self):
        self._expect_error(b"*abc\r\n")

    def test_negative_bulk_length(self):
        self._expect_error(b"*1\r\n$-2\r\n")

    def test_wrong_element_type(self):
        self._expect_error(b"*1\r\n:5\r\n")

    def test_missing_crlf_after_payload(self):
        # 声明 1 字节内容，但内容后跟的不是 CRLF（多给了字节，足以判定非法）。
        self._expect_error(b"*1\r\n$1\r\nxY\r\n")

    def test_oversized_bulk(self):
        parser = RespParser(max_bulk_len=8)
        parser.feed(b"*1\r\n$9\r\n012345678\r\n")
        with self.assertRaises(RespError):
            parser.next_command()


class ReplyEncodingTest(unittest.TestCase):
    def test_simple_and_error(self):
        self.assertEqual(protocol.simple("OK"), ("simple", "OK"))
        self.assertEqual(protocol.encode_reply(protocol.simple("OK")), b"+OK\r\n")
        self.assertEqual(protocol.encode_reply(protocol.error("ERR x")), b"-ERR x\r\n")

    def test_integer(self):
        self.assertEqual(protocol.encode_reply(protocol.integer(-3)), b":-3\r\n")

    def test_bulk(self):
        self.assertEqual(protocol.encode_reply(protocol.bulk(b"hello")),
                         b"$5\r\nhello\r\n")
        self.assertEqual(protocol.encode_reply(protocol.bulk(None)), b"$-1\r\n")
        self.assertEqual(protocol.encode_reply(protocol.bulk("文本")),
                         b"$6\r\n".replace(b"6", b"6") + "文本".encode() + b"\r\n")

    def test_nested_array(self):
        reply = protocol.array([protocol.bulk(b"a"), protocol.integer(1), protocol.NIL])
        self.assertEqual(protocol.encode_reply(reply),
                         b"*3\r\n$1\r\na\r\n:1\r\n$-1\r\n")

    def test_null_array(self):
        self.assertEqual(protocol.encode_reply(protocol.array(None)), b"*-1\r\n")


class EncodeCommandTest(unittest.TestCase):
    def test_mixed_arg_types(self):
        self.assertEqual(encode_command(["SET", "n", 5, b"x"]),
                         b"*4\r\n$3\r\nSET\r\n$1\r\nn\r\n$1\r\n5\r\n$1\r\nx\r\n")

    def test_roundtrip_through_parser(self):
        parser = RespParser()
        parser.feed(encode_command(["GET", "中文"]))
        self.assertEqual(parser.next_command(), [b"GET", "中文".encode("utf-8")])


if __name__ == "__main__":
    unittest.main()
