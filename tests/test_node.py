# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for liblore.node (LoreNode)."""
from __future__ import annotations

import gzip
import os
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

    def test_404_falls_back_to_head_redirect(self, sample_mbox: bytes) -> None:
        """On 404, try HEAD against the bare origin to discover the list path."""
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()

        # First GET returns 404
        mock_404 = MagicMock()
        mock_404.status_code = 404

        # HEAD follows redirect and succeeds
        mock_head = MagicMock()
        mock_head.status_code = 200
        mock_head.url = 'https://lore.kernel.org/tools/test%40example.com/'

        # Second GET (to resolved URL) succeeds
        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.content = gzip.compress(sample_mbox)

        mock_session.get.side_effect = [mock_404, mock_200]
        mock_session.head.return_value = mock_head
        node.set_requests_session(mock_session)

        result = node.get_mbox_by_msgid('test@example.com')
        assert result == sample_mbox
        # Verify the HEAD was sent to the bare origin
        mock_session.head.assert_called_once()
        head_url = mock_session.head.call_args[0][0]
        assert head_url == 'https://lore.kernel.org/test%40example.com/'

    def test_404_no_redirect_raises(self) -> None:
        """When HEAD also 404s (no redirect), raise RemoteError."""
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()

        mock_404 = MagicMock()
        mock_404.status_code = 404

        mock_head_404 = MagicMock()
        mock_head_404.status_code = 404

        mock_session.get.return_value = mock_404
        mock_session.head.return_value = mock_head_404
        node.set_requests_session(mock_session)

        with pytest.raises(RemoteError, match='Server returned an error: 404'):
            node.get_mbox_by_msgid('nonexistent@example.com')


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
        mock_resp.status_code = 200
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


# =====================================================================
# URL fallback
# =====================================================================

class TestFallback:
    """Tests for the fallback_urls feature."""

    def test_no_fallbacks_unchanged(self, sample_mbox: bytes) -> None:
        """Without fallback_urls, behavior is identical to before."""
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        result = node.get_mbox_by_msgid('test@example.com')
        assert result == sample_mbox
        assert mock_session.get.call_count == 1
        url = mock_session.get.call_args[0][0]
        assert url.startswith('https://lore.kernel.org/')

    def test_fallback_on_connection_error(self, sample_mbox: bytes) -> None:
        """Primary raises ConnectionError, fallback succeeds."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.side_effect = [
            requests.ConnectionError('refused'),
            mock_resp,
        ]
        node.set_requests_session(mock_session)

        result = node.get_mbox_by_msgid('test@example.com')
        assert result == sample_mbox
        assert mock_session.get.call_count == 2
        # First call goes to the fallback (tried first)
        first_url = mock_session.get.call_args_list[0][0][0]
        assert first_url.startswith('http://mirror.local/all/')
        # Second call goes to the canonical URL
        second_url = mock_session.get.call_args_list[1][0][0]
        assert second_url.startswith('https://lore.kernel.org/all/')

    def test_fallback_on_timeout(self, sample_mbox: bytes) -> None:
        """Primary raises Timeout, fallback succeeds."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://ams.lore.kernel.org'],
        )
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.side_effect = [
            requests.Timeout('timed out'),
            mock_resp,
        ]
        node.set_requests_session(mock_session)

        result = node.get_mbox_by_msgid('test@example.com')
        assert result == sample_mbox
        assert mock_session.get.call_count == 2

    def test_fallback_on_5xx(self, sample_mbox: bytes) -> None:
        """Primary returns 500, fallback returns 200."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        mock_session = MagicMock()
        mock_500 = MagicMock()
        mock_500.status_code = 500
        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.content = gzip.compress(sample_mbox)
        mock_session.get.side_effect = [mock_500, mock_200]
        node.set_requests_session(mock_session)

        result = node.get_mbox_by_msgid('test@example.com')
        assert result == sample_mbox
        assert mock_session.get.call_count == 2

    def test_no_fallback_on_4xx(self) -> None:
        """4xx is not retriable — fallback should NOT be tried."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        with pytest.raises(RemoteError, match='Server returned an error'):
            node.get_mbox_by_msgid('test@example.com')
        # Only 1 call — the fallback, which returned 404, no retry
        assert mock_session.get.call_count == 1

    def test_all_hosts_fail_connection(self) -> None:
        """All origins raise ConnectionError → RemoteError."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.ConnectionError('refused')
        node.set_requests_session(mock_session)

        with pytest.raises(RemoteError, match='All hosts failed'):
            node.get_mbox_by_msgid('test@example.com')
        assert mock_session.get.call_count == 2

    def test_all_hosts_fail_5xx(self) -> None:
        """All origins return 5xx → caller gets the error response."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        # get_mbox_by_msgid checks status_code and raises RemoteError
        with pytest.raises(RemoteError, match='Server returned an error'):
            node.get_mbox_by_msgid('test@example.com')
        assert mock_session.get.call_count == 2

    def test_all_hosts_fail_no_raise(self) -> None:
        """_fetch_thread_since path: all fail, returns empty list."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        result = node._fetch_thread_since('test@example.com', 'dt:20240101..')
        assert result == []
        assert mock_session.post.call_count == 2

    def test_url_rewriting_preserves_path(self, sample_mbox: bytes) -> None:
        """Verify full URL rewriting with scheme change."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mymirror.local'],
        )
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        # First call (fallback) succeeds
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        node.get_mbox_by_msgid('test@example.com')
        url = mock_session.get.call_args_list[0][0][0]
        assert url.startswith('http://mymirror.local/all/')
        assert url.endswith('/t.mbox.gz')

    def test_url_rewriting_post(self, sample_mbox: bytes) -> None:
        """Verify URL rewriting works for POST requests too."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://ams.lore.kernel.org'],
        )
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)

        node.get_mbox_by_query('test query')
        url = mock_session.post.call_args[0][0]
        assert url.startswith('https://ams.lore.kernel.org/all/')

    def test_validate_does_not_use_fallback(self) -> None:
        """validate() hits canonical URL only, ignoring fallbacks."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        mock_session = MagicMock()
        mock_session.head.side_effect = Exception('connection refused')
        node.set_requests_session(mock_session)

        with pytest.raises(RemoteError, match='Failed to reach'):
            node.validate()
        # Only 1 call to the canonical URL — no fallback
        assert mock_session.head.call_count == 1
        url = mock_session.head.call_args[0][0]
        assert 'lore.kernel.org' in url

    def test_invalid_fallback_url_no_scheme(self) -> None:
        """Fallback URL without scheme raises LibloreError."""
        from liblore import LibloreError
        with pytest.raises(LibloreError, match='Invalid fallback URL'):
            LoreNode(
                'https://lore.kernel.org/all',
                fallback_urls=['mirror.local'],
            )

    def test_invalid_fallback_url_with_path(self) -> None:
        """Fallback URL with a path component raises LibloreError."""
        from liblore import LibloreError
        with pytest.raises(LibloreError, match='must be a scheme://host origin'):
            LoreNode(
                'https://lore.kernel.org/all',
                fallback_urls=['http://mirror.local/some/path'],
            )

    def test_hostname_returns_canonical(self) -> None:
        """hostname property reflects the canonical URL, not fallbacks."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        assert node.hostname == 'lore.kernel.org'

    def test_multiple_fallbacks_tried_in_order(self, sample_mbox: bytes) -> None:
        """With 3 fallbacks, they are tried in the configured order."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=[
                'http://mirror1.local',
                'https://ams.lore.kernel.org',
            ],
        )
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.side_effect = [
            requests.ConnectionError('refused'),
            requests.ConnectionError('refused'),
            mock_resp,
        ]
        node.set_requests_session(mock_session)

        result = node.get_mbox_by_msgid('test@example.com')
        assert result == sample_mbox
        assert mock_session.get.call_count == 3
        urls = [c[0][0] for c in mock_session.get.call_args_list]
        assert urls[0].startswith('http://mirror1.local/')
        assert urls[1].startswith('https://ams.lore.kernel.org/')
        assert urls[2].startswith('https://lore.kernel.org/')


# =====================================================================
# Origin probing
# =====================================================================

class TestProbeOrigins:
    """Tests for the probe_origins() fastest-mirror feature."""

    def test_probe_reorders_by_latency(self) -> None:
        """Origins are reordered fastest-first after probing."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=[
                'https://slow.example.com',
                'https://fast.example.com',
            ],
        )
        # Map each origin to a deterministic fake latency
        latencies = {
            'slow.example.com': 2.0,
            'fast.example.com': 0.1,
            'lore.kernel.org': 0.5,
        }

        def fake_probe_one(origin: str) -> tuple[str, float]:
            for host, lat in latencies.items():
                if host in origin:
                    return origin, lat
            return origin, float('inf')

        with patch.object(node, '_probe_one', side_effect=fake_probe_one):
            results = node.probe_origins()

        assert len(results) == 3
        origins = [o for o, _ in results]
        assert origins[0] == 'https://fast.example.com'
        assert origins[1] == 'https://lore.kernel.org'
        assert origins[2] == 'https://slow.example.com'
        # _all_origins should be reordered too
        assert node._all_origins == origins

    def test_probe_unreachable_sorted_last(self) -> None:
        """Unreachable origins get inf elapsed and sort to the end."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://dead.example.com'],
        )

        def fake_head(url: str, **kwargs: object) -> MagicMock:
            if 'dead' in url:
                raise requests.ConnectionError('refused')
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch('liblore.node.requests.head', side_effect=fake_head):
            results = node.probe_origins()

        assert len(results) == 2
        # canonical should be first (reachable), dead last
        assert results[0][0] == 'https://lore.kernel.org'
        assert results[0][1] < float('inf')
        assert results[1][0] == 'https://dead.example.com'
        assert results[1][1] == float('inf')

    def test_probe_4xx_treated_as_unreachable(self) -> None:
        """Origins returning 4xx are treated as unreachable."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://nomanifest.example.com'],
        )

        def fake_head(url: str, **kwargs: object) -> MagicMock:
            resp = MagicMock()
            if 'nomanifest' in url:
                resp.status_code = 404
            else:
                resp.status_code = 200
            return resp

        with patch('liblore.node.requests.head', side_effect=fake_head):
            results = node.probe_origins()

        assert results[0][0] == 'https://lore.kernel.org'
        assert results[1][1] == float('inf')

    def test_probe_single_origin_noop(self) -> None:
        """With only one origin, probe is a no-op."""
        node = LoreNode('https://lore.kernel.org/all')
        results = node.probe_origins()
        assert len(results) == 1
        assert results[0] == ('https://lore.kernel.org', 0.0)
        assert node._probe_done is True

    def test_probe_uses_manifest_url(self) -> None:
        """Probe hits /manifest.js.gz on each origin."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        probed_urls: list[str] = []

        def fake_head(url: str, **kwargs: object) -> MagicMock:
            probed_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch('liblore.node.requests.head', side_effect=fake_head):
            node.probe_origins()

        assert len(probed_urls) == 2
        assert 'http://mirror.local/manifest.js.gz' in probed_urls
        assert 'https://lore.kernel.org/manifest.js.gz' in probed_urls

    def test_probe_sends_user_agent(self) -> None:
        """Probe requests include the configured User-Agent."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        node.set_user_agent('myapp', '1.0')

        captured_headers: list[dict[str, str]] = []

        def fake_head(url: str, **kwargs: object) -> MagicMock:
            headers = kwargs.get('headers', {})
            assert isinstance(headers, dict)
            captured_headers.append(headers)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch('liblore.node.requests.head', side_effect=fake_head):
            node.probe_origins()

        for h in captured_headers:
            assert h['User-Agent'] == 'myapp/1.0'

    def test_auto_probe_triggers_on_first_request(
        self, sample_mbox: bytes,
    ) -> None:
        """With auto_probe=True, first _request() triggers probe."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://fast.example.com'],
            auto_probe=True,
        )
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        def fake_head(url: str, **kwargs: object) -> MagicMock:
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch('liblore.node.requests.head', side_effect=fake_head):
            node.get_mbox_by_msgid('test@example.com')

        assert node._probe_done is True

    def test_auto_probe_only_once(self, sample_mbox: bytes) -> None:
        """auto_probe fires only on the first request, not subsequent ones."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://fast.example.com'],
            auto_probe=True,
        )
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        probe_count = 0
        def fake_head(url: str, **kwargs: object) -> MagicMock:
            nonlocal probe_count
            probe_count += 1
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch('liblore.node.requests.head', side_effect=fake_head):
            node.get_mbox_by_msgid('first@example.com')
            first_probe_count = probe_count
            node.get_mbox_by_msgid('second@example.com')

        # Second request should NOT trigger another probe
        assert probe_count == first_probe_count

    def test_probe_cache_write_and_read(self, tmp_path: object) -> None:
        """Probe results are cached and restored on next probe call."""
        cache_dir = str(tmp_path)
        node1 = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://fast.example.com'],
            cache_dir=cache_dir,
        )

        def fake_head(url: str, **kwargs: object) -> MagicMock:
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch('liblore.node.requests.head', side_effect=fake_head):
            with patch('liblore.node.time.monotonic') as mock_mono:
                # fast: 0.1s, canonical: 0.5s
                mock_mono.side_effect = [0.0, 0.1, 0.0, 0.5]
                node1.probe_origins()

        expected_order = node1._all_origins[:]

        # New node with same origins should get cached order
        node2 = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://fast.example.com'],
            cache_dir=cache_dir,
        )
        # Without patching requests.head — cache should be used
        node2.probe_origins()
        assert node2._all_origins == expected_order

    def test_probe_cache_expired(self, tmp_path: object) -> None:
        """Expired probe cache triggers a fresh probe."""
        cache_dir = str(tmp_path)
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://fast.example.com'],
            cache_dir=cache_dir,
            probe_ttl=10,
        )

        def fake_head(url: str, **kwargs: object) -> MagicMock:
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch('liblore.node.requests.head', side_effect=fake_head):
            node.probe_origins()

        # Backdate cache file to force expiry
        import glob as glob_mod
        for f in glob_mod.glob(os.path.join(cache_dir, '*.lore.cache')):
            os.utime(f, (0, 0))

        probe_called = False
        def fake_head_2(url: str, **kwargs: object) -> MagicMock:
            nonlocal probe_called
            probe_called = True
            resp = MagicMock()
            resp.status_code = 200
            return resp

        node._probe_done = False
        with patch('liblore.node.requests.head', side_effect=fake_head_2):
            node.probe_origins()

        assert probe_called

    def test_probe_cache_ignored_when_origins_change(
        self, tmp_path: object,
    ) -> None:
        """Cache is ignored when the set of origins differs."""
        cache_dir = str(tmp_path)
        node1 = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://fast.example.com'],
            cache_dir=cache_dir,
        )

        def fake_head(url: str, **kwargs: object) -> MagicMock:
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch('liblore.node.requests.head', side_effect=fake_head):
            node1.probe_origins()

        # New node with DIFFERENT fallbacks
        node2 = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://other.example.com'],
            cache_dir=cache_dir,
        )

        probe_called = False
        def fake_head_2(url: str, **kwargs: object) -> MagicMock:
            nonlocal probe_called
            probe_called = True
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch('liblore.node.requests.head', side_effect=fake_head_2):
            node2.probe_origins()

        # Different origins → cache miss → fresh probe
        assert probe_called

    def test_probe_nocache_skips_cache(self, tmp_path: object) -> None:
        """nocache=True forces a live probe even when cache is fresh."""
        cache_dir = str(tmp_path)
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://fast.example.com'],
            cache_dir=cache_dir,
        )

        def fake_head(url: str, **kwargs: object) -> MagicMock:
            resp = MagicMock()
            resp.status_code = 200
            return resp

        # First probe — populates cache
        with patch('liblore.node.requests.head', side_effect=fake_head):
            with patch('liblore.node.time.monotonic') as mock_mono:
                mock_mono.side_effect = [0.0, 0.1, 0.0, 0.5]
                node.probe_origins()

        # Second probe with nocache — should do a live probe, not read cache
        probe_called = False

        def fake_head_2(url: str, **kwargs: object) -> MagicMock:
            nonlocal probe_called
            probe_called = True
            resp = MagicMock()
            resp.status_code = 200
            return resp

        node._probe_done = False
        with patch('liblore.node.requests.head', side_effect=fake_head_2):
            with patch('liblore.node.time.monotonic') as mock_mono:
                mock_mono.side_effect = [0.0, 0.2, 0.0, 0.3]
                results = node.probe_origins(nocache=True)

        assert probe_called
        # Results should have real timing, not 0.0
        assert all(elapsed > 0.0 for _, elapsed in results)


# =====================================================================
# Git config integration
# =====================================================================

class TestFromGitConfig:
    """Tests for LoreNode.from_git_config()."""

    def test_reads_all_config_keys(self) -> None:
        """Reads all lore.* keys from a single git config call."""
        gitcfg: dict[str, str | list[str]] = {
            'fallback': [
                'https://tor.lore.kernel.org',
                'https://sea.lore.kernel.org',
            ],
            'autoprobe': 'true',
            'probetimeout': '10.0',
            'probettl': '7200',
        }

        with patch('liblore.node._get_config_from_git', return_value=gitcfg):
            node = LoreNode.from_git_config()

        assert node._all_origins == [
            'https://tor.lore.kernel.org',
            'https://sea.lore.kernel.org',
            'https://lore.kernel.org',
        ]
        assert node._auto_probe is True
        assert node._probe_timeout == 10.0
        assert node._probe_ttl == 7200

    def test_explicit_kwargs_override_git_config(self) -> None:
        """Explicit kwargs take precedence over git config values."""
        gitcfg: dict[str, str | list[str]] = {
            'fallback': ['https://from-git-config.example.com'],
            'autoprobe': 'true',
        }

        with patch('liblore.node._get_config_from_git', return_value=gitcfg):
            node = LoreNode.from_git_config(
                fallback_urls=['https://explicit.example.com'],
                auto_probe=False,
            )

        assert node._all_origins == [
            'https://explicit.example.com',
            'https://lore.kernel.org',
        ]
        assert node._auto_probe is False

    def test_git_not_installed(self) -> None:
        """Works fine when _get_config_from_git returns empty."""
        with patch('liblore.node._get_config_from_git', return_value={}):
            node = LoreNode.from_git_config()

        assert node._all_origins == ['https://lore.kernel.org']

    def test_no_config_keys(self) -> None:
        """Works fine when no lore.* keys exist in git config."""
        with patch('liblore.node._get_config_from_git', return_value={}):
            node = LoreNode.from_git_config()

        assert node._all_origins == ['https://lore.kernel.org']
        assert node._auto_probe is False

    def test_invalid_probe_timeout_ignored(self) -> None:
        """Non-numeric lore.probetimeout is silently ignored."""
        gitcfg: dict[str, str | list[str]] = {'probetimeout': 'notanumber'}

        with patch('liblore.node._get_config_from_git', return_value=gitcfg):
            node = LoreNode.from_git_config()

        assert node._probe_timeout == 5.0  # default

    def test_custom_url_passed_through(self) -> None:
        """The url argument is forwarded to __init__."""
        with patch('liblore.node._get_config_from_git', return_value={}):
            node = LoreNode.from_git_config(
                url='https://my-inbox.example.com/lists',
            )

        assert node._url == 'https://my-inbox.example.com/lists'

    def test_reads_useragentplus(self) -> None:
        """Reads lore.useragentplus and applies it via set_user_agent."""
        gitcfg: dict[str, str | list[str]] = {
            'useragentplus': '550e8400-e29b-41d4',
        }

        with patch('liblore.node._get_config_from_git', return_value=gitcfg):
            node = LoreNode.from_git_config()

        assert node._user_agent_plus == '550e8400-e29b-41d4'
        # Now when the caller sets their app name, plus is auto-applied
        node.set_user_agent('korgalore', '0.7')
        assert node._user_agent == 'korgalore/0.7+550e8400-e29b-41d4'

    def test_useragentplus_overridden_by_explicit_plus(self) -> None:
        """Explicit plus= in set_user_agent overrides git config value."""
        gitcfg: dict[str, str | list[str]] = {
            'useragentplus': 'from-git-config',
        }

        with patch('liblore.node._get_config_from_git', return_value=gitcfg):
            node = LoreNode.from_git_config()

        node.set_user_agent('myapp', '1.0', plus='explicit')
        assert node._user_agent == 'myapp/1.0+explicit'

    def test_useragentplus_not_set_without_git_config(self) -> None:
        """Without git config, set_user_agent works as before."""
        node = LoreNode()
        node.set_user_agent('myapp', '1.0')
        assert node._user_agent == 'myapp/1.0'

    def test_useragentplus_applied_to_session(self) -> None:
        """The plus identifier propagates to the session headers."""
        gitcfg: dict[str, str | list[str]] = {
            'useragentplus': 'myuuid',
        }

        with patch('liblore.node._get_config_from_git', return_value=gitcfg):
            node = LoreNode.from_git_config()

        node.set_user_agent('bugspray', '0.3')
        session = node._get_session()
        assert session.headers['User-Agent'] == 'bugspray/0.3+myuuid'


class TestGetConfigFromGit:
    """Tests for the _get_config_from_git() helper."""

    def test_parses_nul_separated_output(self) -> None:
        """Parses git config -z output correctly."""
        from liblore.node import _get_config_from_git

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            'lore.fallback\nhttps://tor.lore.kernel.org\x00'
            'lore.fallback\nhttps://sea.lore.kernel.org\x00'
            'lore.autoprobe\ntrue\x00'
            'lore.probettl\n7200\x00'
        )
        with patch('liblore.node.subprocess.run', return_value=mock_result):
            cfg = _get_config_from_git(r'^lore\.', multivals=['fallback'])

        assert cfg == {
            'fallback': [
                'https://tor.lore.kernel.org',
                'https://sea.lore.kernel.org',
            ],
            'autoprobe': 'true',
            'probettl': '7200',
        }

    def test_git_not_installed(self) -> None:
        """Returns empty dict when git is not installed."""
        from liblore.node import _get_config_from_git

        with patch(
            'liblore.node.subprocess.run',
            side_effect=FileNotFoundError('git not found'),
        ):
            cfg = _get_config_from_git(r'^lore\.')

        assert cfg == {}

    def test_no_matching_keys(self) -> None:
        """Returns empty dict when no keys match the regexp."""
        from liblore.node import _get_config_from_git

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        with patch('liblore.node.subprocess.run', return_value=mock_result):
            cfg = _get_config_from_git(r'^lore\.')

        assert cfg == {}

    def test_bare_key_defaults_to_true(self) -> None:
        """A key without a value (no newline) defaults to 'true'."""
        from liblore.node import _get_config_from_git

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'lore.autoprobe\x00'
        with patch('liblore.node.subprocess.run', return_value=mock_result):
            cfg = _get_config_from_git(r'^lore\.')

        assert cfg == {'autoprobe': 'true'}


# =====================================================================
# Public API: request()
# =====================================================================

class TestRequest:
    """Tests for the public request() method."""

    def test_delegates_to_private_request(self, sample_mbox: bytes) -> None:
        """request() delegates to _request() with raise_on_error=True."""
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        resp = node.request('GET', 'https://lore.kernel.org/manifest.js.gz')
        assert resp.status_code == 200

    def test_failover_works(self, sample_mbox: bytes) -> None:
        """First origin fails, second succeeds."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        mock_session = MagicMock()
        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_session.get.side_effect = [
            requests.ConnectionError('refused'),
            mock_200,
        ]
        node.set_requests_session(mock_session)

        resp = node.request('GET', 'https://lore.kernel.org/manifest.js.gz')
        assert resp.status_code == 200
        assert mock_session.get.call_count == 2

    def test_raises_remote_error_when_all_fail(self) -> None:
        """RemoteError raised when every origin fails."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.ConnectionError('refused')
        node.set_requests_session(mock_session)

        with pytest.raises(RemoteError, match='All hosts failed'):
            node.request('GET', 'https://lore.kernel.org/manifest.js.gz')

    def test_kwargs_forwarded(self) -> None:
        """Extra kwargs (e.g. timeout) are passed through."""
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        node.request(
            'GET', 'https://lore.kernel.org/manifest.js.gz',
            timeout=30,
        )
        _, kwargs = mock_session.get.call_args
        assert kwargs['timeout'] == 30


# =====================================================================
# Public API: user_agent_plus property
# =====================================================================

class TestUserAgentPlusProperty:
    """Tests for the user_agent_plus read-only property."""

    def test_none_when_created_directly(self) -> None:
        """None when created via LoreNode() without git config."""
        node = LoreNode()
        assert node.user_agent_plus is None

    def test_set_via_from_git_config(self) -> None:
        """Populated when created via from_git_config() with the key set."""
        gitcfg: dict[str, str | list[str]] = {
            'useragentplus': 'my-tracking-uuid',
        }
        with patch('liblore.node._get_config_from_git', return_value=gitcfg):
            node = LoreNode.from_git_config()

        assert node.user_agent_plus == 'my-tracking-uuid'

    def test_read_only(self) -> None:
        """Property has no setter — assignment raises AttributeError."""
        node = LoreNode()
        with pytest.raises(AttributeError):
            node.user_agent_plus = 'nope'  # type: ignore[misc]


# =====================================================================
# Public API: origins property
# =====================================================================

class TestOriginsProperty:
    """Tests for the origins read-only property."""

    def test_returns_fallbacks_plus_canonical(self) -> None:
        """Returns fallbacks followed by the canonical origin."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local', 'https://ams.lore.kernel.org'],
        )
        assert node.origins == [
            'http://mirror.local',
            'https://ams.lore.kernel.org',
            'https://lore.kernel.org',
        ]

    def test_returns_copy(self) -> None:
        """Mutating the returned list does not affect the node."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['http://mirror.local'],
        )
        origins = node.origins
        origins.append('https://injected.example.com')
        assert 'https://injected.example.com' not in node.origins

    def test_reflects_reordering_after_probe(self) -> None:
        """After probe_origins(), list reflects the new order."""
        node = LoreNode(
            'https://lore.kernel.org/all',
            fallback_urls=['https://slow.example.com', 'https://fast.example.com'],
        )
        latencies = {
            'slow.example.com': 2.0,
            'fast.example.com': 0.1,
            'lore.kernel.org': 0.5,
        }

        def fake_probe_one(origin: str) -> tuple[str, float]:
            for host, lat in latencies.items():
                if host in origin:
                    return origin, lat
            return origin, float('inf')

        with patch.object(node, '_probe_one', side_effect=fake_probe_one):
            node.probe_origins()

        assert node.origins[0] == 'https://fast.example.com'


# =====================================================================
# Public API: canonical_origin property
# =====================================================================

class TestCanonicalOriginProperty:
    """Tests for the canonical_origin read-only property."""

    def test_matches_scheme_host(self) -> None:
        """Returns scheme://host from the URL passed to __init__."""
        node = LoreNode('https://lore.kernel.org/all')
        assert node.canonical_origin == 'https://lore.kernel.org'

    def test_no_path_component(self) -> None:
        """The origin has no path, even when the URL does."""
        node = LoreNode('https://lore.kernel.org/some/deep/path')
        assert node.canonical_origin == 'https://lore.kernel.org'
        assert '/some' not in node.canonical_origin

    def test_preserves_port(self) -> None:
        """Port number is included in the origin when present."""
        node = LoreNode('http://localhost:8080/inbox')
        assert node.canonical_origin == 'http://localhost:8080'
