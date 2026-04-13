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
