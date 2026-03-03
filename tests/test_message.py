# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for liblore.message."""
from __future__ import annotations

from email.message import EmailMessage

from liblore.utils import clean_header, get_clean_msgid, parse_message


class TestCleanHeader:
    def test_plain_header(self) -> None:
        assert clean_header('Hello World') == 'Hello World'

    def test_none_returns_empty(self) -> None:
        assert clean_header(None) == ''

    def test_rfc2047_encoded(self) -> None:
        encoded = '=?utf-8?q?Caf=C3=A9?='
        assert clean_header(encoded) == 'Caf\u00e9'

    def test_folded_whitespace(self) -> None:
        assert clean_header('Hello\n World') == 'Hello World'

    def test_encoded_address(self) -> None:
        # formataddr re-encodes Unicode names, so we just check
        # the address is preserved and the result is valid
        hdr = '=?utf-8?q?=C3=89ric?= <eric@example.com>'
        result = clean_header(hdr)
        assert 'eric@example.com' in result

    def test_special_chars_in_name(self) -> None:
        hdr = '=?utf-8?q?O=27Brien=2C_Joe?= <joe@example.com>'
        result = clean_header(hdr)
        assert 'joe@example.com' in result


class TestGetCleanMsgid:
    def test_extracts_msgid(self, make_msg: type) -> None:
        msg = make_msg.create(msgid='test123@example.com')
        assert get_clean_msgid(msg) == 'test123@example.com'

    def test_missing_header(self) -> None:
        msg = EmailMessage()
        assert get_clean_msgid(msg) is None

    def test_custom_header(self, make_msg: type) -> None:
        msg = make_msg.create(in_reply_to='parent@example.com')
        assert get_clean_msgid(msg, 'In-Reply-To') == 'parent@example.com'


class TestParseMessage:
    def test_roundtrip(self) -> None:
        raw = (
            b'From: test@example.com\r\n'
            b'Subject: Test\r\n'
            b'Message-Id: <test@example.com>\r\n'
            b'\r\n'
            b'Body text\r\n'
        )
        msg = parse_message(raw)
        assert isinstance(msg, EmailMessage)
        assert msg['Subject'] == 'Test'
        assert msg['From'] == 'test@example.com'
