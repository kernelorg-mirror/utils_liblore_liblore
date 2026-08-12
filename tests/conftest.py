# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared fixtures for liblore tests."""

from __future__ import annotations

import email.utils
import textwrap
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Protocol

import pytest
import responses as responses_

from liblore import emlpolicy


@pytest.fixture(autouse=True)
def _block_network() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    with responses_.RequestsMock():
        yield


class MsgFactory(Protocol):
    def __call__(
        self,
        subject: str = ...,
        from_addr: tuple[str, str] = ...,
        msgid: str | None = ...,
        in_reply_to: str | None = ...,
        references: list[str] | None = ...,
        body: str = ...,
        date: str | None = ...,
    ) -> EmailMessage: ...


@pytest.fixture()
def make_msg() -> MsgFactory:
    """Factory fixture that returns a helper callable for test messages."""
    counter = 0

    def create(
        subject: str = 'Test message',
        from_addr: tuple[str, str] = ('Test User', 'test@example.com'),
        msgid: str | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
        body: str = 'Hello, world!\n',
        date: str | None = None,
    ) -> EmailMessage:
        nonlocal counter
        counter += 1
        if msgid is None:
            msgid = f'msg{counter}@example.com'
        msg = EmailMessage(policy=emlpolicy)
        msg['Subject'] = subject
        msg['From'] = email.utils.formataddr(from_addr)
        msg['Message-Id'] = f'<{msgid}>'
        if in_reply_to:
            msg['In-Reply-To'] = f'<{in_reply_to}>'
        if references:
            msg['References'] = ' '.join(f'<{r}>' for r in references)
        if date:
            msg['Date'] = date
        # Pass an explicit cte to match emlpolicy's cte_type and to avoid
        # CPython's auto-CTE heuristic, which crashes under Python < 3.13
        # when the policy sets max_line_length=None (as emlpolicy does).
        msg.set_content(body, cte='8bit')
        return msg

    return create


@pytest.fixture()
def sample_mbox() -> bytes:
    """Return a minimal two-message mbox as bytes."""
    return textwrap.dedent("""\
        From test@example.com Mon Jan  1 00:00:00 2024
        From: Alice <alice@example.com>
        To: bob@example.com
        Subject: First message
        Message-Id: <first@example.com>
        Date: Mon, 01 Jan 2024 00:00:00 +0000

        Hello from Alice.

        From test@example.com Mon Jan  1 00:01:00 2024
        From: Bob <bob@example.com>
        To: alice@example.com
        Subject: Second message
        Message-Id: <second@example.com>
        Date: Mon, 01 Jan 2024 00:01:00 +0000
        In-Reply-To: <first@example.com>

        Hello from Bob.

    """).encode()
