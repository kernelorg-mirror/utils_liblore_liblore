# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for optional authheaders integration in LoreNode."""

from __future__ import annotations

import gzip
import sys
from email.message import EmailMessage
from types import ModuleType
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
import responses

from liblore import LibloreError
from liblore.node import LoreNode, _AuthenticateMessage


class _FakeAuthHeaders(ModuleType):
    authenticate_message: _AuthenticateMessage


# =====================================================================
# Import-time validation
# =====================================================================


class TestAuthHeadersImport:
    def test_raises_when_authheaders_missing(self) -> None:
        with patch.dict(sys.modules, {'authheaders': None}):
            with pytest.raises(LibloreError, match='authheaders library is required'):
                LoreNode(add_auth_headers=True)

    def test_ok_when_authheaders_installed(self) -> None:
        fake = _FakeAuthHeaders('authheaders')
        fake.authenticate_message = MagicMock()
        with patch.dict(sys.modules, {'authheaders': fake}):
            node = LoreNode(add_auth_headers=True)
            assert node._authenticate_message is not None
            node.close()

    def test_default_is_disabled(self) -> None:
        node = LoreNode()
        assert node._authenticate_message is None
        node.close()


# =====================================================================
# _authenticate_msgs
# =====================================================================


class TestAuthenticateMsgs:
    def test_noop_when_disabled(self) -> None:
        node = LoreNode()
        msg = EmailMessage()
        msg['Subject'] = 'Test'
        node._authenticate_msgs([msg])
        assert 'Authentication-Results' not in msg

    def test_adds_header_when_enabled(self) -> None:
        fake = _FakeAuthHeaders('authheaders')
        fake.authenticate_message = MagicMock(
            return_value='Authentication-Results: liblore; dkim=pass header.d=example.com',
        )
        with patch.dict(sys.modules, {'authheaders': fake}):
            node = LoreNode(add_auth_headers=True)
            msg = EmailMessage()
            msg['From'] = 'test@example.com'
            msg['Subject'] = 'Test'
            msg.set_content('Hello\n')

            node._authenticate_msgs([msg])

            assert msg['Authentication-Results'] == (
                'liblore; dkim=pass header.d=example.com'
            )
            fake.authenticate_message.assert_called_once()
            call_kwargs = fake.authenticate_message.call_args
            assert call_kwargs[0][1] == 'liblore'
            assert call_kwargs[1]['dkim'] is True
            assert call_kwargs[1]['dmarc'] is True
            assert call_kwargs[1]['arc'] is True
            assert call_kwargs[1]['spf'] is False
            node.close()

    def test_skips_empty_result(self) -> None:
        fake = _FakeAuthHeaders('authheaders')
        fake.authenticate_message = MagicMock(return_value='')
        with patch.dict(sys.modules, {'authheaders': fake}):
            node = LoreNode(add_auth_headers=True)
            msg = EmailMessage()
            msg['Subject'] = 'Test'
            msg.set_content('Hello\n')

            node._authenticate_msgs([msg])
            assert 'Authentication-Results' not in msg
            node.close()

    def test_multiple_messages(self) -> None:
        fake = _FakeAuthHeaders('authheaders')
        fake.authenticate_message = MagicMock(
            side_effect=[
                'liblore; dkim=pass',
                'Authentication-Results: liblore; dkim=fail',
            ],
        )
        with patch.dict(sys.modules, {'authheaders': fake}):
            node = LoreNode(add_auth_headers=True)
            msg1 = EmailMessage()
            msg1['Subject'] = 'Msg 1'
            msg1.set_content('Body 1\n')
            msg2 = EmailMessage()
            msg2['Subject'] = 'Msg 2'
            msg2.set_content('Body 2\n')

            node._authenticate_msgs([msg1, msg2])

            assert msg1['Authentication-Results'] == 'liblore; dkim=pass'
            assert msg2['Authentication-Results'] == 'liblore; dkim=fail'
            node.close()


# =====================================================================
# Integration with fetch methods
# =====================================================================


class TestAuthInFetchMethods:
    @pytest.fixture()
    def auth_node(self) -> Iterator[tuple[LoreNode, responses.RequestsMock]]:
        fake = _FakeAuthHeaders('authheaders')
        fake.authenticate_message = MagicMock(
            return_value='Authentication-Results: liblore; dkim=pass',
        )
        with patch.dict(sys.modules, {'authheaders': fake}):
            node = LoreNode(add_auth_headers=True)
        # Patch authheaders into sys.modules for _authenticate_msgs
        self._fake = fake
        self._patcher = patch.dict(sys.modules, {'authheaders': fake})
        self._patcher.start()
        with responses.RequestsMock() as rsps:
            yield node, rsps

    def teardown_method(self) -> None:
        if hasattr(self, '_patcher'):
            self._patcher.stop()

    def test_get_thread_by_msgid(
        self,
        auth_node: tuple[LoreNode, responses.RequestsMock],
        sample_mbox: bytes,
    ) -> None:
        node, rsps = auth_node
        rsps.add(
            responses.GET,
            'https://lore.kernel.org/all/first%40example.com/t.mbox.gz',
            body=gzip.compress(sample_mbox),
            status=200,
        )
        msgs = node.get_thread_by_msgid('first@example.com')
        for msg in msgs:
            assert msg['Authentication-Results'] == 'liblore; dkim=pass'

    def test_get_thread_by_query(
        self,
        auth_node: tuple[LoreNode, responses.RequestsMock],
        sample_mbox: bytes,
    ) -> None:
        node, rsps = auth_node
        rsps.add(
            responses.POST,
            'https://lore.kernel.org/all/?x=m&q=test+query',
            body=gzip.compress(sample_mbox),
            status=200,
        )
        msgs = node.get_thread_by_query('test query')
        for msg in msgs:
            assert msg['Authentication-Results'] == 'liblore; dkim=pass'
