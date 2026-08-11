# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for liblore.thread."""

from __future__ import annotations

from conftest import MsgFactory

from liblore.utils import get_clean_msgid, get_strict_thread


class TestGetStrictThread:
    def test_simple_thread(self, make_msg: MsgFactory) -> None:
        root = make_msg(msgid='root@x.com', subject='Root')
        reply = make_msg(
            msgid='reply@x.com',
            subject='Re: Root',
            in_reply_to='root@x.com',
        )
        unrelated = make_msg(msgid='other@x.com', subject='Other')
        result = get_strict_thread([root, reply, unrelated], 'root@x.com')
        assert result is not None
        ids = {get_clean_msgid(m) for m in result}
        assert ids == {'root@x.com', 'reply@x.com'}

    def test_returns_none_for_missing_msgid(self, make_msg: MsgFactory) -> None:
        msg = make_msg(msgid='exists@x.com')
        result = get_strict_thread([msg], 'nonexistent@x.com')
        assert result is None

    def test_noparent(self, make_msg: MsgFactory) -> None:
        parent = make_msg(msgid='parent@x.com')
        child = make_msg(
            msgid='child@x.com',
            in_reply_to='parent@x.com',
        )
        grandchild = make_msg(
            msgid='grandchild@x.com',
            in_reply_to='child@x.com',
        )
        # Start at child with noparent — should exclude parent
        result = get_strict_thread(
            [parent, child, grandchild], 'child@x.com', noparent=True
        )
        assert result is not None
        ids = {get_clean_msgid(m) for m in result}
        assert 'parent@x.com' not in ids
        assert 'child@x.com' in ids
        assert 'grandchild@x.com' in ids

    def test_noparent_self_referencing_cover(self, make_msg: MsgFactory) -> None:
        # Some tooling emits a cover letter whose In-Reply-To/References point
        # at its own Message-ID.  Breaking the thread there must not treat the
        # cover's own msgid as a reference to ignore, or every patch that
        # legitimately replies to the cover gets dropped.
        cover = make_msg(
            msgid='cover@x.com',
            subject='[PATCH 0/2] Something',
            in_reply_to='cover@x.com',
            references=['cover@x.com'],
        )
        patch1 = make_msg(
            msgid='p1@x.com',
            subject='[PATCH 1/2] First',
            in_reply_to='cover@x.com',
            references=['cover@x.com'],
        )
        patch2 = make_msg(
            msgid='p2@x.com',
            subject='[PATCH 2/2] Second',
            in_reply_to='cover@x.com',
            references=['cover@x.com'],
        )
        review = make_msg(
            msgid='review@x.com',
            subject='Re: [PATCH 2/2] Second',
            in_reply_to='p2@x.com',
            references=['cover@x.com', 'p2@x.com'],
        )
        want = {'cover@x.com', 'p1@x.com', 'p2@x.com', 'review@x.com'}
        # Cover first in the list is the worst case: without the fix only the
        # cover survives.  Ordering must not matter, so check both.
        for msgs in ([cover, patch1, patch2, review], [patch1, cover, patch2, review]):
            result = get_strict_thread(msgs, 'cover@x.com', noparent=True)
            assert result is not None
            assert {get_clean_msgid(m) for m in result} == want

    def test_self_reference_is_not_a_parent(self, make_msg: MsgFactory) -> None:
        # A self-referencing message must not be treated as a reply to itself,
        # even without noparent: it is a thread root, and starting the walk at
        # its child still has to pull it in.
        root = make_msg(
            msgid='root@x.com',
            in_reply_to='root@x.com',
            references=['root@x.com'],
        )
        reply = make_msg(msgid='reply@x.com', in_reply_to='root@x.com')
        result = get_strict_thread([root, reply], 'reply@x.com')
        assert result is not None
        assert {get_clean_msgid(m) for m in result} == {'root@x.com', 'reply@x.com'}

    def test_references_chain(self, make_msg: MsgFactory) -> None:
        msg1 = make_msg(msgid='a@x.com')
        msg2 = make_msg(msgid='b@x.com', references=['a@x.com'])
        msg3 = make_msg(
            msgid='c@x.com',
            references=['a@x.com', 'b@x.com'],
        )
        result = get_strict_thread([msg1, msg2, msg3], 'a@x.com')
        assert result is not None
        assert len(result) == 3

    def test_single_message(self, make_msg: MsgFactory) -> None:
        msg = make_msg(msgid='solo@x.com')
        result = get_strict_thread([msg], 'solo@x.com')
        assert result is not None
        assert len(result) == 1
