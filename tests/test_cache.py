# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for LoreNode filesystem caching."""
from __future__ import annotations

import gzip
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from liblore import LoreNode, RemoteError


class TestCacheReadWrite:
    """Unit tests for _cache_key, _cache_read, _cache_write."""

    def test_cache_disabled_by_default(self) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        assert node._cache_dir is None
        assert node._cache_read('anything') is None

    def test_cache_write_and_read(self, tmp_path: Path) -> None:
        node = LoreNode(cache_dir=str(tmp_path), cache_ttl=60)
        key = node._cache_key('test', 'data')
        node._cache_write(key, b'hello world')
        assert node._cache_read(key) == b'hello world'

    def test_cache_miss_returns_none(self, tmp_path: Path) -> None:
        node = LoreNode(cache_dir=str(tmp_path))
        key = node._cache_key('test', 'nonexistent')
        assert node._cache_read(key) is None

    def test_cache_expired_returns_none(self, tmp_path: Path) -> None:
        node = LoreNode(cache_dir=str(tmp_path), cache_ttl=1)
        key = node._cache_key('test', 'data')
        node._cache_write(key, b'old data')
        # Backdate the file to make it stale
        path = tmp_path / f'{key}.lore.cache'
        os.utime(path, (0, 0))
        assert node._cache_read(key) is None
        # Stale file should be cleaned up
        assert not path.exists()

    def test_cache_write_noop_when_disabled(self) -> None:
        node = LoreNode()
        # Should not raise even though cache_dir is None
        node._cache_write('key', b'data')

    def test_cache_write_atomic(self, tmp_path: Path) -> None:
        node = LoreNode(cache_dir=str(tmp_path))
        key = node._cache_key('test', 'atomic')
        node._cache_write(key, b'data')
        # No .tmp files left behind
        tmp_files = [f for f in os.listdir(tmp_path) if f.endswith('.tmp')]
        assert tmp_files == []

    def test_cache_key_varies_by_url(self) -> None:
        node_a = LoreNode('https://lore.kernel.org/all')
        node_b = LoreNode('https://other.example.com/all')
        key_a = node_a._cache_key('ns', 'data')
        key_b = node_b._cache_key('ns', 'data')
        assert key_a != key_b

    def test_cache_key_varies_by_namespace(self, tmp_path: Path) -> None:
        node = LoreNode(cache_dir=str(tmp_path))
        key_a = node._cache_key('mbox_by_msgid', 'test@x.com')
        key_b = node._cache_key('mbox_by_query', 'test@x.com')
        assert key_a != key_b

    def test_cache_empty_bytes(self, tmp_path: Path) -> None:
        """Empty bytes (valid server response) should be cached."""
        node = LoreNode(cache_dir=str(tmp_path), cache_ttl=60)
        key = node._cache_key('test', 'empty')
        node._cache_write(key, b'')
        assert node._cache_read(key) == b''

    def test_cache_dir_created(self, tmp_path: Path) -> None:
        cache_dir = str(tmp_path / 'sub' / 'dir')
        LoreNode(cache_dir=cache_dir)
        assert os.path.isdir(cache_dir)


class TestClearCache:

    def test_clears_cache_files(self, tmp_path: Path) -> None:
        node = LoreNode(cache_dir=str(tmp_path))
        node._cache_write(node._cache_key('a', '1'), b'data1')
        node._cache_write(node._cache_key('b', '2'), b'data2')
        assert len(list(tmp_path.glob('*.lore.cache'))) == 2
        node.clear_cache()
        assert len(list(tmp_path.glob('*.lore.cache'))) == 0

    def test_preserves_non_cache_files(self, tmp_path: Path) -> None:
        node = LoreNode(cache_dir=str(tmp_path))
        (tmp_path / 'other.txt').write_text('keep me')
        node._cache_write(node._cache_key('a', '1'), b'data')
        node.clear_cache()
        assert (tmp_path / 'other.txt').exists()

    def test_noop_when_disabled(self) -> None:
        node = LoreNode()
        node.clear_cache()  # Should not raise


class TestProperties:

    def test_url_property(self) -> None:
        node = LoreNode('https://lore.kernel.org/all/')
        # Trailing slash is stripped by constructor
        assert node.url == 'https://lore.kernel.org/all'

    def test_hostname_property(self) -> None:
        node = LoreNode('https://lore.kernel.org/all')
        assert node.hostname == 'lore.kernel.org'

    def test_hostname_different_host(self) -> None:
        node = LoreNode('https://inbox.example.com/linux-kernel')
        assert node.hostname == 'inbox.example.com'


class TestCachedMethods:
    """Integration tests: verify cache hit avoids network, cache miss hits network."""

    def _make_node(self, tmp_path: Path, sample_mbox: bytes) -> tuple[LoreNode, MagicMock]:
        node = LoreNode('https://lore.kernel.org/all', cache_dir=str(tmp_path), cache_ttl=60)
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.return_value = mock_resp
        mock_session.post.return_value = mock_resp
        node.set_requests_session(mock_session)
        return node, mock_session

    def test_get_mbox_by_msgid_caches(self, tmp_path: Path, sample_mbox: bytes) -> None:
        node, mock_session = self._make_node(tmp_path, sample_mbox)
        result1 = node.get_mbox_by_msgid('test@example.com')
        result2 = node.get_mbox_by_msgid('test@example.com')
        assert result1 == result2 == sample_mbox
        # Network should only be hit once
        assert mock_session.get.call_count == 1

    def test_get_mbox_by_query_caches(self, tmp_path: Path, sample_mbox: bytes) -> None:
        node, mock_session = self._make_node(tmp_path, sample_mbox)
        result1 = node.get_mbox_by_query('test query')
        result2 = node.get_mbox_by_query('test query')
        assert result1 == result2 == sample_mbox
        assert mock_session.post.call_count == 1

    def test_get_mbox_by_query_full_threads_separate_key(
        self, tmp_path: Path, sample_mbox: bytes
    ) -> None:
        node, mock_session = self._make_node(tmp_path, sample_mbox)
        node.get_mbox_by_query('test', full_threads=False)
        node.get_mbox_by_query('test', full_threads=True)
        # Different full_threads values = different cache keys = two network calls
        assert mock_session.post.call_count == 2

    def test_get_message_by_msgid_caches(self, tmp_path: Path) -> None:
        node = LoreNode('https://lore.kernel.org/all', cache_dir=str(tmp_path), cache_ttl=60)
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'raw email bytes'
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        result1 = node.get_message_by_msgid('test@example.com')
        result2 = node.get_message_by_msgid('test@example.com')
        assert result1 == result2 == b'raw email bytes'
        assert mock_session.get.call_count == 1

    def test_cache_expired_refetches(self, tmp_path: Path, sample_mbox: bytes) -> None:
        node, mock_session = self._make_node(tmp_path, sample_mbox)
        node._cache_ttl = 1
        node.get_mbox_by_msgid('test@example.com')
        assert mock_session.get.call_count == 1
        # Backdate the cache file
        for f in tmp_path.glob('*.lore.cache'):
            os.utime(f, (0, 0))
        node.get_mbox_by_msgid('test@example.com')
        assert mock_session.get.call_count == 2

    def test_no_cache_no_files(self, tmp_path: Path, sample_mbox: bytes) -> None:
        """When cache_dir is None, no files are written."""
        node = LoreNode('https://lore.kernel.org/all')
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(sample_mbox)
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        node.get_mbox_by_msgid('test@example.com')
        # tmp_path should be empty since we didn't set cache_dir to it
        assert list(tmp_path.iterdir()) == []

    def test_fetch_thread_since_not_cached(self, tmp_path: Path, sample_mbox: bytes) -> None:
        """_fetch_thread_since should NOT be cached."""
        node, mock_session = self._make_node(tmp_path, sample_mbox)
        node._fetch_thread_since('test@example.com', 'dt:20240101..')
        node._fetch_thread_since('test@example.com', 'dt:20240101..')
        # Both calls should hit the network
        assert mock_session.post.call_count == 2

    def test_error_not_cached(self, tmp_path: Path) -> None:
        """Network errors should not be cached."""
        node = LoreNode('https://lore.kernel.org/all', cache_dir=str(tmp_path), cache_ttl=60)
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_session.get.return_value = mock_resp
        node.set_requests_session(mock_session)

        with pytest.raises(RemoteError):
            node.get_mbox_by_msgid('bad@example.com')
        # No cache file should be written
        assert list(tmp_path.glob('*.lore.cache')) == []
