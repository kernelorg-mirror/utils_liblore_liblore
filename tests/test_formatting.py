# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for email formatting and thread minimization."""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from conftest import MsgFactory

from liblore.utils import (
    clean_header,
    format_addrs,
    get_msg_as_bytes,
    minimize_thread,
    wrap_header,
)


# Adapted from b4 test_format_addrs — validates cpython#100900 workaround
# and RFC 2047 handling in address formatting.
class TestFormatAddrs:
    @pytest.mark.parametrize(
        'pairs,verify,clean',
        [
            (
                [('', 'foo@example.com'), ('Foo Bar', 'bar@example.com')],
                'foo@example.com, Foo Bar <bar@example.com>',
                True,
            ),
            (
                [('', 'foo@example.com'), ('Foo, Bar', 'bar@example.com')],
                'foo@example.com, "Foo, Bar" <bar@example.com>',
                True,
            ),
            (
                [('', 'foo@example.com'), ('F\u00f4o, Bar', 'bar@example.com')],
                'foo@example.com, "F\u00f4o, Bar" <bar@example.com>',
                True,
            ),
            (
                [
                    ('', 'foo@example.com'),
                    ('=?utf-8?q?Qu=C3=BBx_Foo?=', 'quux@example.com'),
                ],
                'foo@example.com, Qu\u00fbx Foo <quux@example.com>',
                True,
            ),
            (
                [
                    ('', 'foo@example.com'),
                    ('=?utf-8?q?Qu=C3=BBx=2C_Foo?=', 'quux@example.com'),
                ],
                'foo@example.com, "Qu\u00fbx, Foo" <quux@example.com>',
                True,
            ),
            (
                [
                    ('', 'foo@example.com'),
                    ('=?utf-8?q?Qu=C3=BBx=2C_Foo?=', 'quux@example.com'),
                ],
                'foo@example.com, =?utf-8?q?Qu=C3=BBx=2C_Foo?= <quux@example.com>',
                False,
            ),
        ],
    )
    def test_format_addrs(
        self, pairs: list[tuple[str, str]], verify: str, clean: bool
    ) -> None:
        assert format_addrs(pairs, clean) == verify


# Adapted from b4 test_header_wrapping — validates RFC 2047 encoding,
# line wrapping, and proper handling of address vs non-address headers.
class TestWrapHeader:
    @pytest.mark.parametrize(
        'hval,verify',
        [
            # Short ASCII — no wrapping needed
            ('short-ascii', 'short-ascii'),
            # Short Unicode — RFC 2047 QP encoded
            ('short-unic\u00f4de', '=?utf-8?q?short-unic=C3=B4de?='),
            # Long ASCII — wrapped at word boundary
            (
                'Lorem ipsum dolor sit amet consectetur adipiscing elit '
                'sed do eiusmod tempor incididunt ut labore et dolore magna aliqua',
                'Lorem ipsum dolor sit amet consectetur adipiscing elit sed do\n'
                ' eiusmod tempor incididunt ut labore et dolore magna aliqua',
            ),
            # Long Unicode — split across multiple encoded lines
            (
                'Lorem \u00eepsum dolor sit amet consectetur adipiscing el\u00eet '
                'sed do eiusmod temp\u00f4r incididunt ut labore et dol\u00f4re magna aliqua',
                '=?utf-8?q?Lorem_=C3=AEpsum_dolor_sit_amet_consectetur_adipiscin?=\n'
                ' =?utf-8?q?g_el=C3=AEt_sed_do_eiusmod_temp=C3=B4r_incididunt_ut_labore_et?=\n'
                ' =?utf-8?q?_dol=C3=B4re_magna_aliqua?=',
            ),
            # Exactly 75 chars — boundary condition
            (
                'Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiu',
                'Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiu',
            ),
            # Unicode on escape boundary
            (
                'Lorem ipsum dolor sit amet consectetur adipiscin el\u00eet',
                '=?utf-8?q?Lorem_ipsum_dolor_sit_amet_consectetur_adipiscin_el?=\n'
                ' =?utf-8?q?=C3=AEt?=',
            ),
            # Unicode 1 char too long
            (
                'Lorem ipsum dolor sit amet consectetur adipi el\u00eet',
                '=?utf-8?q?Lorem_ipsum_dolor_sit_amet_consectetur_adipi_el=C3=AE?=\n'
                ' =?utf-8?q?t?=',
            ),
        ],
    )
    def test_non_address_header(self, hval: str, verify: str) -> None:
        wrapped = wrap_header(('X-Header', hval))
        assert wrapped.decode() == f'X-Header: {verify}'

    @pytest.mark.parametrize(
        'hval,verify',
        [
            # Single address
            ('foo@example.com', 'foo@example.com'),
            # Two addresses
            ('foo@example.com, bar@example.com', 'foo@example.com, bar@example.com'),
            # Mixed plain + display name
            (
                'foo@example.com, Foo Bar <bar@example.com>',
                'foo@example.com, Foo Bar <bar@example.com>',
            ),
            # Mixed Unicode — non-ASCII name gets QP encoded
            (
                'foo@example.com, Foo Bar <bar@example.com>, F\u00f4o Baz <baz@example.com>',
                'foo@example.com, Foo Bar <bar@example.com>, \n'
                ' =?utf-8?q?F=C3=B4o_Baz?= <baz@example.com>',
            ),
            # Complex with quoted specials
            (
                'foo@example.com, Foo Bar <bar@example.com>, '
                'F\u00f4o Baz <baz@example.com>, "Quux, Foo" <quux@example.com>',
                'foo@example.com, Foo Bar <bar@example.com>, \n'
                ' =?utf-8?q?F=C3=B4o_Baz?= <baz@example.com>, '
                '"Quux, Foo" <quux@example.com>',
            ),
            # Long local part forces line wrap
            (
                '01234567890123456789012345678901234567890123456789012345678901@example.org, '
                '\u00e4 <foo@example.org>',
                '01234567890123456789012345678901234567890123456789012345678901@example.org, \n'
                ' =?utf-8?q?=C3=A4?= <foo@example.org>',
            ),
            # cpython#100900 — Unicode name with RFC 5322 specials
            (
                'foo@example.com, Foo Bar <bar@example.com>, '
                'F\u00f4o Baz <baz@example.com>, "Qu\u00fbx, Foo" <quux@example.com>',
                'foo@example.com, Foo Bar <bar@example.com>, \n'
                ' =?utf-8?q?F=C3=B4o_Baz?= <baz@example.com>, \n'
                ' =?utf-8?q?Qu=C3=BBx=2C_Foo?= <quux@example.com>',
            ),
        ],
    )
    def test_address_header(self, hval: str, verify: str) -> None:
        wrapped = wrap_header(('To', hval))
        assert wrapped.decode() == f'To: {verify}'

    @pytest.mark.parametrize(
        'hval,verify',
        [
            # Short message-id
            (
                '<20240319-short-message-id@example.com>',
                '<20240319-short-message-id@example.com>',
            ),
            # Long message-id — unbreakable, stays on one line
            (
                '<20240319-very-long-message-id-that-spans-multiple-lines-for-sure'
                '-because-longer-than-75-characters-abcde123456@longdomain.example.com>',
                '<20240319-very-long-message-id-that-spans-multiple-lines-for-sure'
                '-because-longer-than-75-characters-abcde123456@longdomain.example.com>',
            ),
        ],
    )
    def test_message_id(self, hval: str, verify: str) -> None:
        wrapped = wrap_header(('Message-ID', hval))
        assert wrapped.decode() == f'Message-ID: {verify}'

    def test_round_trip(self) -> None:
        """Encoded non-address headers should decode back to the original."""
        hval = 'L\u00f4rem \u00eepsum d\u00f4lor sit \u00e0met'
        wrapped = wrap_header(('X-Header', hval))
        _, wval = wrapped.split(b':', maxsplit=1)
        assert clean_header(wval.decode()) == hval


class TestGetMsgAsBytes:
    def test_headers_and_body(self) -> None:
        msg = EmailMessage()
        msg['Subject'] = 'Test subject'
        msg['To'] = 'dest@example.com'
        msg.set_payload('Hello world.\n')
        msg.set_charset('utf-8')
        bdata = get_msg_as_bytes(msg)
        text = bdata.decode()
        assert 'Subject: Test subject\n' in text
        assert 'To: dest@example.com\n' in text
        assert 'Hello world.\n' in text

    def test_smtp_line_endings(self) -> None:
        msg = EmailMessage()
        msg['Subject'] = 'Test'
        msg.set_payload('Body.\n')
        msg.set_charset('utf-8')
        bdata = get_msg_as_bytes(msg, nl='\r\n')
        # Every line should end with \r\n, no bare LF or CR
        stripped = bdata.replace(b'\r\n', b'')
        assert b'\n' not in stripped
        assert b'\r' not in stripped

    def test_non_ascii_header_encoded(self) -> None:
        msg = EmailMessage()
        msg['Subject'] = 'H\u00e9llo w\u00f6rld'
        msg.set_payload('Body.\n')
        msg.set_charset('utf-8')
        bdata = get_msg_as_bytes(msg)
        # Subject should be RFC 2047 encoded, not raw UTF-8
        assert b'=?utf-8?q?' in bdata
        assert 'H\u00e9llo'.encode() not in bdata


class TestMinimizeThread:
    def test_headers_filtered(self, make_msg: MsgFactory) -> None:
        """Only headers in MINIMIZE_KEEP_HEADERS are kept."""
        msg = make_msg(subject='Test', body='Hello.\n')
        msg['X-Custom'] = 'should be dropped'
        msg['List-Id'] = '<test.example.com>'
        result = minimize_thread([msg])
        assert len(result) == 1
        assert result[0]['Subject'] == 'Test'
        assert result[0]['X-Custom'] is None
        assert result[0]['List-Id'] is None

    def test_keep_headers_default(self, make_msg: MsgFactory) -> None:
        """All default MINIMIZE_KEEP_HEADERS are preserved when present."""
        msg = make_msg(
            subject='Test',
            from_addr=('Alice', 'alice@example.com'),
            body='Hello.\n',
            date='Mon, 01 Jan 2024 00:00:00 +0000',
            in_reply_to='parent@example.com',
        )
        msg['To'] = 'bob@example.com'
        msg['Cc'] = 'carol@example.com'
        msg['Reply-To'] = 'alice@example.com'
        result = minimize_thread([msg])
        assert len(result) == 1
        mmsg = result[0]
        assert mmsg['From'] is not None
        assert mmsg['To'] is not None
        assert mmsg['Cc'] is not None
        assert mmsg['Subject'] is not None
        assert mmsg['Date'] is not None
        assert mmsg['Message-ID'] is not None
        assert mmsg['Reply-To'] is not None
        assert mmsg['In-Reply-To'] is not None

    def test_custom_keep_headers(self, make_msg: MsgFactory) -> None:
        """Callers can override which headers to keep."""
        msg = make_msg(
            subject='Test', body='Hello.\n', date='Mon, 01 Jan 2024 00:00:00 +0000'
        )
        result = minimize_thread([msg], keep_headers=('Subject',))
        assert len(result) == 1
        assert result[0]['Subject'] == 'Test'
        assert result[0]['From'] is None
        assert result[0]['Date'] is None

    def test_multi_level_quotes_stripped(self, make_msg: MsgFactory) -> None:
        """Lines with >> (multi-level quoting) are removed."""
        body = 'My reply.\n> Single quote.\n>> Double quote.\nMore text.\n'
        msg = make_msg(body=body)
        result = minimize_thread([msg])
        assert len(result) == 1
        text = result[0].get_payload()
        assert 'My reply.' in text
        assert 'Single quote.' in text
        assert 'Double quote.' not in text
        assert 'More text.' in text

    def test_empty_quote_lines_stripped(self, make_msg: MsgFactory) -> None:
        """Bare '>' lines are cleaned up."""
        body = 'Reply.\n>\n> Real quote.\nMore text.\n'
        msg = make_msg(body=body)
        result = minimize_thread([msg])
        text = result[0].get_payload()
        assert 'Reply.' in text
        assert 'Real quote.' in text
        assert 'More text.' in text

    def test_bottom_quotes_dropped(self, make_msg: MsgFactory) -> None:
        """Trailing quoted blocks at the end of a message are dropped."""
        body = 'My reply.\n> Original message.\n'
        msg = make_msg(body=body)
        result = minimize_thread([msg])
        text = result[0].get_payload()
        assert 'My reply.' in text
        assert 'Original message.' not in text

    def test_diff_preserved(self, make_msg: MsgFactory) -> None:
        """Messages with diff content are not minimized."""
        body = (
            '> Quoted context.\n'
            'diff --git a/file.c b/file.c\n'
            '--- a/file.c\n'
            '+++ b/file.c\n'
            '@@ -1,3 +1,3 @@\n'
            '-old\n'
            '+new\n'
        )
        msg = make_msg(body=body)
        result = minimize_thread([msg])
        text = result[0].get_payload()
        assert 'diff --git' in text
        assert 'Quoted context.' in text

    def test_diffstat_preserved(self, make_msg: MsgFactory) -> None:
        """Messages with diffstat content are not minimized."""
        body = '> Quoted.\n 3 files changed, 10 insertions(+), 5 deletions(-)\n'
        msg = make_msg(body=body)
        result = minimize_thread([msg])
        text = result[0].get_payload()
        assert 'Quoted.' in text
        assert '3 files changed' in text

    def test_empty_after_minimize_dropped(self, make_msg: MsgFactory) -> None:
        """Messages that become empty after minimization are dropped."""
        body = '> Only quoted text.\n>> And deeper quotes.\n'
        msg = make_msg(body=body)
        result = minimize_thread([msg])
        assert len(result) == 0

    def test_signature_preserved(self, make_msg: MsgFactory) -> None:
        """Compliant signatures are kept after quote processing."""
        body = 'My reply.\n-- \nJane Doe\n'
        msg = make_msg(body=body)
        result = minimize_thread([msg])
        text = result[0].get_payload()
        assert 'My reply.' in text
        assert '-- \n' in text
        assert 'Jane Doe' in text

    def test_trailing_quote_before_sig_dropped(self, make_msg: MsgFactory) -> None:
        """A trailing quoted block before a signature is dropped."""
        body = 'My reply.\n> Huge quoted original.\n> More quoting.\n-- \nJane Doe\n'
        msg = make_msg(body=body)
        result = minimize_thread([msg])
        text = result[0].get_payload()
        assert 'My reply.' in text
        assert 'Huge quoted original.' not in text
        assert 'More quoting.' not in text
        assert 'Jane Doe' in text

    def test_only_quotes_before_sig_dropped(self, make_msg: MsgFactory) -> None:
        """Message with only quotes before a signature is dropped entirely."""
        body = '> Only quoted text.\n-- \nJane Doe\n'
        msg = make_msg(body=body)
        result = minimize_thread([msg])
        assert len(result) == 0

    def test_reduce_quote_context(self, make_msg: MsgFactory) -> None:
        """Long quoted blocks are reduced to the last paragraph."""
        body = (
            'On Monday, Julius Caesar wrote:\n'
            '> First paragraph line one.\n'
            '> First paragraph line two.\n'
            '> First paragraph line three.\n'
            '> First paragraph line four.\n'
            '>\n'
            '> Second paragraph line one.\n'
            '> Second paragraph line two.\n'
            '>\n'
            '> Third paragraph line one.\n'
            '> Third paragraph line two.\n'
            'My reply here.\n'
        )
        msg = make_msg(body=body)
        result = minimize_thread([msg], reduce_quote_context=True)
        text = result[0].get_payload()
        assert '... skip 7 lines ...' in text
        assert 'Third paragraph line one.' in text
        assert 'Third paragraph line two.' in text
        assert 'First paragraph' not in text
        assert 'Second paragraph' not in text
        assert 'My reply here.' in text

    def test_reduce_quote_context_short_quote_untouched(
        self, make_msg: MsgFactory
    ) -> None:
        """Quotes with 5 or fewer skippable lines are left alone."""
        body = '> Line one.\n> Line two.\n>\n> Last para.\nReply.\n'
        msg = make_msg(body=body)
        result = minimize_thread([msg], reduce_quote_context=True)
        text = result[0].get_payload()
        assert 'skip' not in text
        assert 'Line one.' in text
        assert 'Last para.' in text

    def test_reduce_quote_context_off_by_default(self, make_msg: MsgFactory) -> None:
        """Long quotes are untouched when reduce_quote_context is False."""
        lines = ''.join(f'> Line {i}.\n' for i in range(20))
        body = f'{lines}Reply.\n'
        msg = make_msg(body=body)
        result = minimize_thread([msg])
        text = result[0].get_payload()
        assert 'skip' not in text
        assert 'Line 0.' in text
        assert 'Line 19.' in text

    def test_reduce_quote_context_preserves_sig(self, make_msg: MsgFactory) -> None:
        """Signature is preserved when reducing quote context."""
        lines = ''.join(f'> Line {i}.\n' for i in range(10))
        body = f'On Monday, someone wrote:\n{lines}>\n> Last para.\nReply.\n-- \nKR\n'
        msg = make_msg(body=body)
        result = minimize_thread([msg], reduce_quote_context=True)
        text = result[0].get_payload()
        assert 'skip' in text
        assert 'Last para.' in text
        assert 'Reply.' in text
        assert '-- \n' in text
        assert 'KR' in text

    def test_multiple_messages(self, make_msg: MsgFactory) -> None:
        """Multiple messages in a thread are all processed."""
        msg1 = make_msg(body='First message.\n')
        msg2 = make_msg(body='Reply.\n> First message.\n')
        msg3 = make_msg(body='> Only quotes.\n')
        result = minimize_thread([msg1, msg2, msg3])
        # msg3 should be dropped (all quotes)
        assert len(result) == 2
