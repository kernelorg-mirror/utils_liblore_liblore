# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for optional authheaders integration in LoreNode."""
from __future__ import annotations

import gzip
import sys
from email.message import EmailMessage
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from liblore import LibloreError
from liblore.node import LoreNode


# =====================================================================
# Import-time validation
# =====================================================================

class TestAuthHeadersImport:
    def test_raises_when_authheaders_missing(self) -> None:
        with patch.dict(sys.modules, {'authheaders': None}):
            with pytest.raises(LibloreError, match='authheaders library is required'):
                LoreNode(add_auth_headers=True)

    def test_ok_when_authheaders_installed(self) -> None:
        fake = ModuleType('authheaders')
        fake.authenticate_message = MagicMock()  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {'authheaders': fake}):
            node = LoreNode(add_auth_headers=True)
            assert node._authheaders is not None
            node.close()

    def test_default_is_disabled(self) -> None:
        node = LoreNode()
        assert node._authheaders is None
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
        fake = ModuleType('authheaders')
        fake.authenticate_message = MagicMock(  # type: ignore[attr-defined]
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
        fake = ModuleType('authheaders')
        fake.authenticate_message = MagicMock(return_value='')  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {'authheaders': fake}):
            node = LoreNode(add_auth_headers=True)
            msg = EmailMessage()
            msg['Subject'] = 'Test'
            msg.set_content('Hello\n')

            node._authenticate_msgs([msg])
            assert 'Authentication-Results' not in msg
            node.close()

    def test_multiple_messages(self) -> None:
        fake = ModuleType('authheaders')
        fake.authenticate_message = MagicMock(  # type: ignore[attr-defined]
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
    def auth_node(self, sample_mbox: bytes) -> LoreNode:
        fake = ModuleType('authheaders')
        fake.authenticate_message = MagicMock(  # type: ignore[attr-defined]
            return_value='Authentication-Results: liblore; dkim=pass',
        )
        with patch.dict(sys.modules, {'authheaders': fake}):
            node = LoreNode(add_auth_headers=True)
        # Patch authheaders into sys.modules for _authenticate_msgs
        self._fake = fake
        self._patcher = patch.dict(sys.modules, {'authheaders': fake})
        self._patcher.start()

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.return_value = mock_resp
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)
        return node

    def teardown_method(self) -> None:
        if hasattr(self, '_patcher'):
            self._patcher.stop()

    def test_get_thread_by_msgid(self, auth_node: LoreNode) -> None:
        msgs = auth_node.get_thread_by_msgid('first@example.com')
        for msg in msgs:
            assert msg['Authentication-Results'] == 'liblore; dkim=pass'

    def test_get_thread_by_query(self, auth_node: LoreNode) -> None:
        msgs = auth_node.get_thread_by_query('test query')
        for msg in msgs:
            assert msg['Authentication-Results'] == 'liblore; dkim=pass'
