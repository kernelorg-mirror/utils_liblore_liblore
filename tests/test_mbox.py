# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for liblore.mbox."""
from __future__ import annotations

import textwrap

from email.message import EmailMessage

from liblore.utils import (
    get_clean_msgid,
    get_preferred_duplicate,
    split_and_dedupe,
    split_and_dedupe_as_bytes,
    split_mbox,
    split_mbox_as_bytes,
)

from liblore import emlpolicy


class TestSplitMbox:
    def test_splits_two_messages(self, sample_mbox: bytes) -> None:
        msgs = split_mbox(sample_mbox)
        assert len(msgs) == 2
        subjects = [m['Subject'] for m in msgs]
        assert 'First message' in subjects
        assert 'Second message' in subjects

    def test_empty_mbox(self) -> None:
        msgs = split_mbox(b'')
        assert msgs == []

    def test_mboxrd_single_level_unescaping(self) -> None:
        """A line starting with >From in the body should be unescaped."""
        raw = textwrap.dedent("""\
            From sender@example.com Mon Jan  1 00:00:00 2024
            From: sender@example.com
            Subject: Test mboxrd
            Message-Id: <mboxrd-test@example.com>

            Here is a quoted line:
            >From someone
            And normal text.
        """).encode()
        msgs = split_mbox(raw)
        assert len(msgs) == 1
        body = msgs[0].get_payload()
        assert 'From someone' in body
        assert '>From someone' not in body

    def test_mboxrd_double_level_unescaping(self) -> None:
        """>>From should become >From (only one level stripped)."""
        raw = textwrap.dedent("""\
            From sender@example.com Mon Jan  1 00:00:00 2024
            From: sender@example.com
            Subject: Double escaped
            Message-Id: <double@example.com>

            Doubly escaped:
            >>From someone
            End.
        """).encode()
        msgs = split_mbox(raw)
        assert len(msgs) == 1
        body = msgs[0].get_payload()
        assert '>From someone' in body
        assert '>>From someone' not in body

    def test_mboxrd_triple_level_unescaping(self) -> None:
        """>>>From should become >>From."""
        raw = textwrap.dedent("""\
            From sender@example.com Mon Jan  1 00:00:00 2024
            From: sender@example.com
            Subject: Triple
            Message-Id: <triple@example.com>

            >>>From someone
        """).encode()
        msgs = split_mbox(raw)
        assert len(msgs) == 1
        body = msgs[0].get_payload()
        assert '>>From someone' in body

    def test_from_in_headers_not_unescaped(self) -> None:
        """The >From stripping must not touch header lines."""
        raw = textwrap.dedent("""\
            From sender@example.com Mon Jan  1 00:00:00 2024
            From: sender@example.com
            Subject: >From in subject
            Message-Id: <hdr-test@example.com>

            Body text.
        """).encode()
        msgs = split_mbox(raw)
        assert len(msgs) == 1
        assert '>From in subject' in msgs[0]['Subject']

    def test_real_from_line_not_stripped(self) -> None:
        """A genuine 'From ' at the start of body should NOT be unescaped
        (it doesn't match >+From)."""
        raw = textwrap.dedent("""\
            From sender@example.com Mon Jan  1 00:00:00 2024
            From: sender@example.com
            Subject: Test
            Message-Id: <real-from@example.com>

            From the beginning of a sentence.
        """).encode()
        msgs = split_mbox(raw)
        assert len(msgs) == 1
        body = msgs[0].get_payload()
        assert 'From the beginning of a sentence.' in body

    def test_multiple_messages_with_escaping(self) -> None:
        raw = textwrap.dedent("""\
            From a@example.com Mon Jan  1 00:00:00 2024
            From: a@example.com
            Subject: First
            Message-Id: <first@example.com>

            >From escaped in first.

            From b@example.com Mon Jan  1 00:01:00 2024
            From: b@example.com
            Subject: Second
            Message-Id: <second@example.com>

            >>From double-escaped in second.
        """).encode()
        msgs = split_mbox(raw)
        assert len(msgs) == 2
        body1 = msgs[0].get_payload()
        body2 = msgs[1].get_payload()
        assert 'From escaped in first.' in body1
        assert '>From escaped in first.' not in body1
        assert '>From double-escaped in second.' in body2
        assert '>>From double-escaped in second.' not in body2


class TestSplitMboxAsBytes:
    def test_splits_two_messages(self, sample_mbox: bytes) -> None:
        chunks = split_mbox_as_bytes(sample_mbox)
        assert len(chunks) == 2
        assert all(isinstance(c, bytes) for c in chunks)

    def test_empty_mbox(self) -> None:
        assert split_mbox_as_bytes(b'') == []

    def test_from_line_stripped(self, sample_mbox: bytes) -> None:
        """The 'From ' separator must not appear in the returned bytes."""
        for chunk in split_mbox_as_bytes(sample_mbox):
            for line in chunk.split(b'\n'):
                if line:
                    assert not line.startswith(b'From ')

    def test_headers_preserved(self, sample_mbox: bytes) -> None:
        chunks = split_mbox_as_bytes(sample_mbox)
        assert b'Subject: First message' in chunks[0]
        assert b'Subject: Second message' in chunks[1]

    def test_mboxrd_unescaping(self) -> None:
        raw = textwrap.dedent("""\
            From sender@example.com Mon Jan  1 00:00:00 2024
            From: sender@example.com
            Subject: Test
            Message-Id: <bytes-esc@example.com>

            >From someone
            >>From deeper
        """).encode()
        chunks = split_mbox_as_bytes(raw)
        assert len(chunks) == 1
        assert b'From someone\n' in chunks[0]
        assert b'>From deeper\n' in chunks[0]
        # No double-escaped lines should remain
        assert b'>>From' not in chunks[0]

    def test_roundtrip_with_split_mbox(self, sample_mbox: bytes) -> None:
        """split_mbox_as_bytes + parse_message should match split_mbox."""
        from liblore.utils import parse_message
        raw_chunks = split_mbox_as_bytes(sample_mbox)
        parsed = split_mbox(sample_mbox)
        assert len(raw_chunks) == len(parsed)
        for raw, msg in zip(raw_chunks, parsed):
            reparsed = parse_message(raw)
            assert reparsed['Message-Id'] == msg['Message-Id']
            assert reparsed['Subject'] == msg['Subject']


class TestSplitAndDedupeAsBytes:
    def test_deduplicates(self, sample_mbox: bytes) -> None:
        doubled = sample_mbox + sample_mbox
        chunks = split_and_dedupe_as_bytes(doubled)
        assert len(chunks) == 2
        assert all(isinstance(c, bytes) for c in chunks)

    def test_preserves_order(self, sample_mbox: bytes) -> None:
        chunks = split_and_dedupe_as_bytes(sample_mbox)
        assert b'Message-Id: <first@example.com>' in chunks[0]
        assert b'Message-Id: <second@example.com>' in chunks[1]

    def test_drops_no_msgid(self) -> None:
        raw = (
            b'From test@example.com Mon Jan  1 00:00:00 2024\n'
            b'From: test@example.com\n'
            b'Subject: No msgid\n'
            b'\n'
            b'Body.\n'
            b'\n'
        )
        assert split_and_dedupe_as_bytes(raw) == []

    def test_prefers_better_source(self) -> None:
        mailman = textwrap.dedent("""\
            From test@example.com Mon Jan  1 00:00:00 2024
            From: test@example.com
            Subject: Test
            Message-Id: <dup@example.com>
            List-Id: <list.vger.kernel.org>

            Mailman copy with footer.

        """).encode()
        feeds = textwrap.dedent("""\
            From test@example.com Mon Jan  1 00:00:00 2024
            From: test@example.com
            Subject: Test
            Message-Id: <dup@example.com>
            List-Id: <list.feeds.kernel.org>

            Clean feeds copy.

        """).encode()
        chunks = split_and_dedupe_as_bytes(mailman + feeds)
        assert len(chunks) == 1
        assert b'Clean feeds copy' in chunks[0]

        chunks = split_and_dedupe_as_bytes(feeds + mailman)
        assert len(chunks) == 1
        assert b'Clean feeds copy' in chunks[0]

    def test_empty_preference_keeps_first(self) -> None:
        mailman = textwrap.dedent("""\
            From test@example.com Mon Jan  1 00:00:00 2024
            From: test@example.com
            Subject: Test
            Message-Id: <dup@example.com>
            List-Id: <list.vger.kernel.org>

            First copy.

        """).encode()
        feeds = textwrap.dedent("""\
            From test@example.com Mon Jan  1 00:00:00 2024
            From: test@example.com
            Subject: Test
            Message-Id: <dup@example.com>
            List-Id: <list.feeds.kernel.org>

            Second copy.

        """).encode()
        chunks = split_and_dedupe_as_bytes(mailman + feeds, listid_preference=[])
        assert len(chunks) == 1
        assert b'First copy' in chunks[0]

    def test_roundtrip_with_split_and_dedupe(self, sample_mbox: bytes) -> None:
        """Parsing the bytes output should match split_and_dedupe."""
        from liblore.utils import parse_message
        doubled = sample_mbox + sample_mbox
        chunks = split_and_dedupe_as_bytes(doubled)
        msgs = split_and_dedupe(doubled)
        assert len(chunks) == len(msgs)
        for raw, msg in zip(chunks, msgs):
            reparsed = parse_message(raw)
            assert reparsed['Message-Id'] == msg['Message-Id']


class TestSplitAndDedupe:
    def test_deduplicates(self, sample_mbox: bytes) -> None:
        doubled = sample_mbox + sample_mbox
        msgs = split_and_dedupe(doubled)
        assert len(msgs) == 2

    def test_preserves_order(self, sample_mbox: bytes) -> None:
        msgs = split_and_dedupe(sample_mbox)
        assert get_clean_msgid(msgs[0]) == 'first@example.com'
        assert get_clean_msgid(msgs[1]) == 'second@example.com'

    def test_drops_no_msgid(self) -> None:
        raw = (
            b'From test@example.com Mon Jan  1 00:00:00 2024\n'
            b'From: test@example.com\n'
            b'Subject: No msgid\n'
            b'\n'
            b'Body.\n'
            b'\n'
        )
        msgs = split_and_dedupe(raw)
        assert msgs == []

    def test_prefers_better_source(self) -> None:
        """When duplicates exist, the copy from the preferred source wins."""
        # Build two mbox messages with the same msgid but different List-Id
        mailman = textwrap.dedent("""\
            From test@example.com Mon Jan  1 00:00:00 2024
            From: test@example.com
            Subject: Test
            Message-Id: <dup@example.com>
            List-Id: <list.vger.kernel.org>

            Mailman copy with footer.

        """).encode()
        feeds = textwrap.dedent("""\
            From test@example.com Mon Jan  1 00:00:00 2024
            From: test@example.com
            Subject: Test
            Message-Id: <dup@example.com>
            List-Id: <list.feeds.kernel.org>

            Clean feeds copy.

        """).encode()
        # feeds.kernel.org copy should win regardless of order
        msgs = split_and_dedupe(mailman + feeds)
        assert len(msgs) == 1
        assert 'Clean feeds copy' in msgs[0].get_payload()

        msgs = split_and_dedupe(feeds + mailman)
        assert len(msgs) == 1
        assert 'Clean feeds copy' in msgs[0].get_payload()

    def test_empty_preference_keeps_first(self) -> None:
        """An empty preference list disables source selection."""
        mailman = textwrap.dedent("""\
            From test@example.com Mon Jan  1 00:00:00 2024
            From: test@example.com
            Subject: Test
            Message-Id: <dup@example.com>
            List-Id: <list.vger.kernel.org>

            First copy.

        """).encode()
        feeds = textwrap.dedent("""\
            From test@example.com Mon Jan  1 00:00:00 2024
            From: test@example.com
            Subject: Test
            Message-Id: <dup@example.com>
            List-Id: <list.feeds.kernel.org>

            Second copy.

        """).encode()
        msgs = split_and_dedupe(mailman + feeds, listid_preference=[])
        assert len(msgs) == 1
        assert 'First copy' in msgs[0].get_payload()


class TestGetPreferredDuplicate:
    def _make_msg(self, listid: str | None = None) -> EmailMessage:
        msg = EmailMessage(policy=emlpolicy)
        msg['Message-Id'] = '<test@example.com>'
        msg['From'] = 'test@example.com'
        if listid:
            msg['List-Id'] = f'<{listid}>'
        return msg

    def test_prefers_feeds_over_vger(self) -> None:
        feeds = self._make_msg('list.feeds.kernel.org')
        vger = self._make_msg('list.vger.kernel.org')
        assert get_preferred_duplicate(feeds, vger) is feeds
        assert get_preferred_duplicate(vger, feeds) is feeds

    def test_prefers_linux_dev_over_kernel_org(self) -> None:
        ldev = self._make_msg('list.linux.dev')
        korg = self._make_msg('list.kernel.org')
        assert get_preferred_duplicate(ldev, korg) is ldev
        assert get_preferred_duplicate(korg, ldev) is ldev

    def test_prefers_kernel_org_over_unknown(self) -> None:
        korg = self._make_msg('list.kernel.org')
        unknown = self._make_msg('list.example.com')
        assert get_preferred_duplicate(korg, unknown) is korg
        assert get_preferred_duplicate(unknown, korg) is korg

    def test_no_listid_treated_as_wildcard(self) -> None:
        korg = self._make_msg('list.kernel.org')
        bare = self._make_msg()
        assert get_preferred_duplicate(korg, bare) is korg

    def test_both_no_listid_keeps_first(self) -> None:
        msg1 = self._make_msg()
        msg2 = self._make_msg()
        assert get_preferred_duplicate(msg1, msg2) is msg1

    def test_custom_preference(self) -> None:
        custom = self._make_msg('list.custom.org')
        feeds = self._make_msg('list.feeds.kernel.org')
        prefs = ['*.custom.org', '*.feeds.kernel.org', '*']
        assert get_preferred_duplicate(custom, feeds, prefs) is custom
        assert get_preferred_duplicate(feeds, custom, prefs) is custom
