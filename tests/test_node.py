# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for liblore.node (LoreNode)."""
from __future__ import annotations

import gzip
from datetime import datetime, timezone
from email.message import EmailMessage
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from liblore import RemoteError
from liblore.node import LoreNode


# =====================================================================
# Session management
# =====================================================================

class TestSessionManagement:
    def test_creates_session(self) -> None:
        node = LoreNode()
        s = node._get_session()
        assert s is not None
        assert 'liblore/' in s.headers['User-Agent']
        node.close()

    def test_returns_same_session(self) -> None:
        node = LoreNode()
        s1 = node._get_session()
        s2 = node._get_session()
        assert s1 is s2
        node.close()

    def test_close_and_reopen(self) -> None:
        node = LoreNode()
        s1 = node._get_session()
        node.close()
        s2 = node._get_session()
        assert s1 is not s2
        node.close()

    def test_set_user_agent(self) -> None:
        node = LoreNode()
        node.set_user_agent('myapp', '1.0')
        s = node._get_session()
        assert s.headers['User-Agent'] == 'myapp/1.0'
        node.close()

    def test_set_user_agent_updates_existing(self) -> None:
        node = LoreNode()
        s = node._get_session()
        node.set_user_agent('otherapp', '2.0')
        assert s.headers['User-Agent'] == 'otherapp/2.0'
        node.close()

    def test_set_user_agent_plus(self) -> None:
        node = LoreNode()
        node.set_user_agent('korgalore', '0.6', plus='abcd1234')
        s = node._get_session()
        assert s.headers['User-Agent'] == 'korgalore/0.6+abcd1234'
        node.close()

    def test_no_plus(self) -> None:
        node = LoreNode()
        node.set_user_agent('korgalore', '0.6')
        s = node._get_session()
        assert s.headers['User-Agent'] == 'korgalore/0.6'
        node.close()

    def test_default_no_plus(self) -> None:
        node = LoreNode()
        s = node._get_session()
        assert '+' not in s.headers['User-Agent']
        node.close()

    def test_set_requests_session(self) -> None:
        external = requests.Session()
        external.headers.update({'User-Agent': 'custom/1.0'})
        node = LoreNode()
        node.set_requests_session(external)
        s = node._get_session()
        assert s is external
        assert s.headers['User-Agent'] == 'custom/1.0'
        node.close()

    def test_set_requests_session_ua_not_overwritten(self) -> None:
        external = requests.Session()
        external.headers.update({'User-Agent': 'myapp/2.0'})
        node = LoreNode()
        node.set_user_agent('liblore', '0.1')
        node.set_requests_session(external)
        assert node._get_session().headers['User-Agent'] == 'myapp/2.0'
        node.close()

    def test_close_only_owned_session(self) -> None:
        external = requests.Session()
        node = LoreNode()
        node.set_requests_session(external)
        node.close()
        # External session should NOT be closed — still usable
        external.headers.update({'X-Test': 'alive'})
        external.close()

    def test_context_manager(self) -> None:
        with LoreNode() as node:
            s = node._get_session()
            assert s is not None
        # After exiting, session should be cleaned up
        assert node._session is None


# =====================================================================
# get_mbox_by_msgid / get_mbox_by_query
# =====================================================================

class TestGetMboxByMsgid:
    def test_returns_raw_bytes(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        result = node.get_mbox_by_msgid('first@example.com')
        assert result == sample_mbox

    def test_http_error(self) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        with pytest.raises(RemoteError, match='Server returned an error'):
            node.get_mbox_by_msgid('test@x.com')


class TestGetMboxByQuery:
    def test_returns_raw_bytes(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        result = node.get_mbox_by_query('test query')
        assert result == sample_mbox

    def test_full_threads_adds_t_param(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        node.get_mbox_by_query('test query', full_threads=True)
        url = mock_session.post.call_args[0][0]
        assert '&t=1&' in url

    def test_no_full_threads_omits_t_param(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        node.get_mbox_by_query('test query')
        url = mock_session.post.call_args[0][0]
        assert 't=1' not in url

    def test_http_error(self) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        with pytest.raises(RemoteError, match='Server returned an error'):
            node.get_mbox_by_query('test')


# =====================================================================
# get_thread_by_msgid
# =====================================================================

class TestGetThreadByMsgid:
    def test_full_thread(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        msgs = node.get_thread_by_msgid('first@example.com')
        assert len(msgs) >= 1
        # Without since, fetches full thread via GET /{msgid}/t.mbox.gz
        mock_session.get.assert_called_once()

    def test_query_contains_msgid(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        node.get_thread_by_msgid('first@example.com')
        call_url = mock_session.get.call_args[0][0]
        assert 'first%40example.com' in call_url or 'first@example.com' in call_url
        assert call_url.endswith('/t.mbox.gz')

    def test_since_uses_dt_prefix(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        node.get_thread_by_msgid('first@example.com', since='20240101')
        call_url = mock_session.post.call_args[0][0]
        assert 'dt%3A20240101' in call_url or 'dt:20240101' in call_url
        assert 'first%40example.com' in call_url or 'first@example.com' in call_url

    def test_raises_on_empty(self) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(b'')
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        with pytest.raises(LookupError):
            node.get_thread_by_msgid('nonexistent@x.com')

    def test_sort_parameter(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        msgs = node.get_thread_by_msgid('first@example.com', sort=True)
        assert len(msgs) >= 1


# =====================================================================
# get_thread_updates_since
# =====================================================================

class TestGetThreadUpdatesSince:
    def test_returns_messages(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        since = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        msgs = node.get_thread_updates_since('first@example.com', since)
        assert len(msgs) >= 1
        mock_session.post.assert_called_once()

    def test_empty_returns_empty_list(self) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(b'')
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        since = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        msgs = node.get_thread_updates_since('first@example.com', since)
        assert msgs == []

    def test_converts_datetime_to_rt_epoch(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        since = datetime(2024, 3, 15, 8, 30, 45, tzinfo=timezone.utc)
        epoch = int(since.timestamp())  # 1710491445
        node.get_thread_updates_since('first@example.com', since)
        call_url = mock_session.post.call_args[0][0]
        assert f'rt%3A{epoch}' in call_url or f'rt:{epoch}' in call_url
        assert 'first%40example.com' in call_url or 'first@example.com' in call_url

    def test_with_sort(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        since = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        msgs = node.get_thread_updates_since(
            'first@example.com', since, sort=True,
        )
        assert len(msgs) >= 1

    def test_server_error_returns_empty_list(self) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        since = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        msgs = node.get_thread_updates_since('first@example.com', since)
        assert msgs == []


# =====================================================================
# get_thread_by_query
# =====================================================================

class TestGetThreadByQuery:
    def test_posts_query(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        msgs = node.get_thread_by_query('test query')
        assert len(msgs) == 2
        mock_session.post.assert_called_once()

    def test_query_with_date_filter(self, sample_mbox: bytes) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        node.get_thread_by_query('test d:20240101..')
        call_url = mock_session.post.call_args[0][0]
        assert 'd%3A20240101' in call_url or 'd:20240101' in call_url


# =====================================================================
# get_message_by_msgid
# =====================================================================

class TestGetMessageByMsgid:
    def test_fetches_raw(self) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = b'raw email bytes'
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        result = node.get_message_by_msgid('test@x.com')
        assert result == b'raw email bytes'

    def test_raises_remote_error(self) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception('connection refused')
        node.set_requests_session(mock_session)

        with pytest.raises(RemoteError):
            node.get_message_by_msgid('test@x.com')


# =====================================================================
# batch_get_thread_by_msgid
# =====================================================================

class TestBatchGetThreadByMsgid:
    def test_returns_ordered_results(self) -> None:
        node = LoreNode()
        thread_a = [EmailMessage()]
        thread_b = [EmailMessage(), EmailMessage()]
        node.get_thread_by_msgid = MagicMock(side_effect=[thread_a, thread_b])  # type: ignore[method-assign]

        with patch('liblore.node.time.sleep') as mock_sleep:
            results = node.batch_get_thread_by_msgid(['a@x', 'b@x'])

        assert results == [thread_a, thread_b]
        assert node.get_thread_by_msgid.call_count == 2
        mock_sleep.assert_called_once_with(0.1)

    def test_no_sleep_for_single_msgid(self) -> None:
        node = LoreNode()
        thread = [EmailMessage()]
        node.get_thread_by_msgid = MagicMock(return_value=thread)  # type: ignore[method-assign]

        with patch('liblore.node.time.sleep') as mock_sleep:
            results = node.batch_get_thread_by_msgid(['only@x'])

        assert results == [thread]
        mock_sleep.assert_not_called()

    def test_passes_kwargs(self) -> None:
        node = LoreNode()
        node.get_thread_by_msgid = MagicMock(return_value=[EmailMessage()])  # type: ignore[method-assign]

        with patch('liblore.node.time.sleep'):
            node.batch_get_thread_by_msgid(
                ['a@x'], strict=False, sort=True, since='20240101',
            )

        node.get_thread_by_msgid.assert_called_once_with(
            'a@x', strict=False, sort=True, since='20240101',
        )

    def test_sleep_count_matches_gaps(self) -> None:
        node = LoreNode()
        node.get_thread_by_msgid = MagicMock(return_value=[EmailMessage()])  # type: ignore[method-assign]

        with patch('liblore.node.time.sleep') as mock_sleep:
            node.batch_get_thread_by_msgid(['a@x', 'b@x', 'c@x'])

        assert mock_sleep.call_args_list == [call(0.1), call(0.1)]

    def test_empty_list(self) -> None:
        node = LoreNode()
        node.get_thread_by_msgid = MagicMock()  # type: ignore[method-assign]

        with patch('liblore.node.time.sleep') as mock_sleep:
            results = node.batch_get_thread_by_msgid([])

        assert results == []
        mock_sleep.assert_not_called()
        node.get_thread_by_msgid.assert_not_called()


# =====================================================================
# batch_get_thread_by_query
# =====================================================================

class TestBatchGetThreadByQuery:
    def test_returns_ordered_results(self) -> None:
        node = LoreNode()
        result_a = [EmailMessage()]
        result_b = [EmailMessage(), EmailMessage()]
        node.get_thread_by_query = MagicMock(side_effect=[result_a, result_b])  # type: ignore[method-assign]

        with patch('liblore.node.time.sleep') as mock_sleep:
            results = node.batch_get_thread_by_query(['q1', 'q2'])

        assert results == [result_a, result_b]
        assert node.get_thread_by_query.call_count == 2
        mock_sleep.assert_called_once_with(0.1)

    def test_no_sleep_for_single_query(self) -> None:
        node = LoreNode()
        result = [EmailMessage()]
        node.get_thread_by_query = MagicMock(return_value=result)  # type: ignore[method-assign]

        with patch('liblore.node.time.sleep') as mock_sleep:
            results = node.batch_get_thread_by_query(['only_query'])

        assert results == [result]
        mock_sleep.assert_not_called()

    def test_sleep_count_matches_gaps(self) -> None:
        node = LoreNode()
        node.get_thread_by_query = MagicMock(return_value=[EmailMessage()])  # type: ignore[method-assign]

        with patch('liblore.node.time.sleep') as mock_sleep:
            node.batch_get_thread_by_query(['q1', 'q2', 'q3', 'q4'])

        assert mock_sleep.call_args_list == [call(0.1), call(0.1), call(0.1)]

    def test_empty_list(self) -> None:
        node = LoreNode()
        node.get_thread_by_query = MagicMock()  # type: ignore[method-assign]

        with patch('liblore.node.time.sleep') as mock_sleep:
            results = node.batch_get_thread_by_query([])

        assert results == []
        mock_sleep.assert_not_called()
        node.get_thread_by_query.assert_not_called()


# =====================================================================
# validate
# =====================================================================

class TestValidate:
    def test_valid_url(self) -> None:
        node = LoreNode('https://lore.kernel.org/lkml')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.head.return_value = mock_resp
        node.set_requests_session(mock_session)

        node.validate()
        mock_session.head.assert_called_once_with(
            'https://lore.kernel.org/lkml/_/text/help/'
        )

    def test_not_public_inbox(self) -> None:
        node = LoreNode('https://example.com/not-pi')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_session.head.return_value = mock_resp
        node.set_requests_session(mock_session)

        with pytest.raises(RemoteError, match='does not appear'):
            node.validate()

    def test_connection_error(self) -> None:
        node = LoreNode('https://unreachable.example.com')
        mock_session = MagicMock()
        mock_session.head.side_effect = Exception('connection refused')
        node.set_requests_session(mock_session)

        with pytest.raises(RemoteError, match='Failed to reach'):
            node.validate()
