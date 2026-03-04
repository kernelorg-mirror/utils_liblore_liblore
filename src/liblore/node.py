# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2025-2026 The Linux Foundation
"""LoreNode — primary API for interacting with public-inbox servers."""
from __future__ import annotations

import gzip
import logging
import time
import types
import urllib.parse
from datetime import datetime, timezone

import requests

from email.message import EmailMessage

from liblore import LibloreError, RemoteError
from liblore.utils import (
    get_strict_thread,
    sort_msgs_by_received,
    split_and_dedupe,
)

logger = logging.getLogger(__name__)


class LoreNode:
    """A connection to a single public-inbox endpoint.

    Encapsulates HTTP session management and all operations against a
    public-inbox server.  Use as a context manager for automatic
    resource cleanup::

        with LoreNode('https://lore.kernel.org/all') as node:
            msgs = node.get_thread_by_msgid('test@example.com')
    """

    def __init__(
        self,
        url: str = 'https://lore.kernel.org/all',
        *,
        add_auth_headers: bool = False,
    ) -> None:
        self._url = url.rstrip('/')
        self._session: requests.Session | None = None
        self._owns_session = False
        self._user_agent: str = f'liblore/{__import__("liblore").__version__}'
        self._authheaders: types.ModuleType | None = None
        if add_auth_headers:
            try:
                import authheaders
                self._authheaders = authheaders
            except ImportError:
                raise LibloreError(
                    'authheaders library is required for add_auth_headers. '
                    'Install with: pip install liblore[auth]'
                )

    # -----------------------------------------------------------------
    # Session management
    # -----------------------------------------------------------------

    def set_user_agent(self, app_name: str, version: str, plus: str | None = None) -> None:
        """Set the User-Agent to ``app_name/version`` (optionally ``+plus``)."""
        self._user_agent = f'{app_name}/{version}'
        if plus:
            self._user_agent += f'+{plus}'
        logger.debug('Set user-agent to: %s', self._user_agent)
        if self._session is not None and self._owns_session:
            self._session.headers.update({'User-Agent': self._user_agent})

    def set_requests_session(self, session: requests.Session) -> None:
        """Inject a pre-existing :class:`requests.Session`.

        The session's User-Agent header is **not** overwritten — the
        caller owns it.
        """
        self._session = session
        self._owns_session = False
        logger.debug('Using caller-provided requests session')

    def _get_session(self) -> requests.Session:
        """Return the session, creating one if needed."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({'User-Agent': self._user_agent})
            self._owns_session = True
        return self._session

    def close(self) -> None:
        """Close the session if we created it (not externally injected)."""
        if self._session is not None and self._owns_session:
            self._session.close()
        self._session = None
        self._owns_session = False

    def __enter__(self) -> LoreNode:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    # -----------------------------------------------------------------
    # Authentication
    # -----------------------------------------------------------------

    def _authenticate_msgs(self, msgs: list[EmailMessage]) -> None:
        """Add Authentication-Results headers via authheaders."""
        if self._authheaders is None:
            return
        for msg in msgs:
            msg_bytes = msg.as_bytes()
            auth_result = self._authheaders.authenticate_message(
                msg_bytes, 'liblore',
                dkim=True, dmarc=True, arc=True, spf=False,
            )
            if auth_result:
                # authheaders returns the full header line, so strip
                # the name prefix before adding to the message.
                msg['Authentication-Results'] = auth_result.removeprefix(
                    'Authentication-Results: '
                )

    # -----------------------------------------------------------------
    # Primary API — raw mbox
    # -----------------------------------------------------------------

    def get_mbox_by_msgid(self, msgid: str) -> bytes:
        """Fetch a thread mbox by message ID and return the raw bytes."""
        mbox_url = f'{self._url}/{urllib.parse.quote_plus(msgid)}/t.mbox.gz'
        logger.debug('Fetching mbox from: %s', mbox_url)
        session = self._get_session()
        resp = session.get(mbox_url)
        if resp.status_code != 200:
            raise RemoteError('Server returned an error: %s' % resp.status_code)
        t_mbox = gzip.decompress(resp.content)
        resp.close()
        return t_mbox

    def get_mbox_by_query(self, query: str) -> bytes:
        """POST a search query and return the raw mbox bytes."""
        query_url = self._url + '/?x=m&q=' + urllib.parse.quote_plus(query)
        logger.debug('query=%s', query_url)
        session = self._get_session()
        resp = session.post(query_url, data='x=m')
        if resp.status_code != 200:
            raise RemoteError('Server returned an error: %s' % resp.status_code)
        t_mbox = gzip.decompress(resp.content)
        resp.close()
        return t_mbox

    # -----------------------------------------------------------------
    # Primary API — parsed messages
    # -----------------------------------------------------------------

    def get_thread_by_msgid(
        self,
        msgid: str,
        *,
        strict: bool = True,
        sort: bool = False,
        since: str | None = None,
    ) -> list[EmailMessage]:
        """Fetch a thread from the public-inbox server.

        When *since* is given (a ``YYYYMMDDHHMMSS`` date string), only
        thread messages with a ``Date:`` header after that timestamp are
        returned.  This uses the per-message search endpoint
        (``/{msgid}/?x=m&q=dt:{since}..``) which scopes the query to
        the thread identified by *msgid* and filters individual messages
        by date.

        Without *since*, the full thread mbox is fetched via
        ``/{msgid}/t.mbox.gz``.

        When *strict* is ``True`` (default), the results are filtered
        to only messages belonging to the thread rooted at *msgid*.

        When *sort* is ``True``, messages are sorted by Received date.

        Raises :class:`LookupError` when no messages match.
        """
        if since:
            msgs = self._fetch_thread_since(msgid, f'dt:{since}..')
            if not msgs:
                raise LookupError(
                    'No messages found for msgid=%s since=%s' % (msgid, since)
                )
        else:
            # Full thread: GET /{msgid}/t.mbox.gz
            t_mbox = self.get_mbox_by_msgid(msgid)
            if not t_mbox:
                raise LookupError('No messages found for msgid=%s' % msgid)
            msgs = split_and_dedupe(t_mbox)

        if strict:
            strict_msgs = get_strict_thread(msgs, msgid)
            if not isinstance(strict_msgs, list) or not len(strict_msgs):
                raise LookupError(
                    'No messages found for msgid=%s' % msgid
                )
            msgs = strict_msgs

        if sort:
            msgs = sort_msgs_by_received(msgs)

        self._authenticate_msgs(msgs)
        return msgs

    def _fetch_thread_since(
        self,
        msgid: str,
        query_fragment: str,
    ) -> list[EmailMessage]:
        """Fetch thread messages matching a date-range query fragment.

        *query_fragment* is a ready-to-use search term such as
        ``dt:20240101..`` or ``rt:1704067200..``.

        Uses the per-message search endpoint: the ``/{msgid}/`` path
        scopes the query to the thread.  POSTs with an empty body so
        that thread expansion is NOT triggered — only messages matching
        the range are returned.

        Returns an empty list when the server finds no matches.
        """
        qmsgid = urllib.parse.quote_plus(msgid)
        query = urllib.parse.quote_plus(query_fragment)
        search_url = f'{self._url}/{qmsgid}/?x=m&q={query}'
        logger.debug('Fetching thread updates from: %s', search_url)
        session = self._get_session()
        resp = session.post(search_url, data='')
        if resp.status_code != 200:
            return []
        t_mbox = gzip.decompress(resp.content)
        resp.close()
        if not t_mbox:
            return []
        return split_and_dedupe(t_mbox)

    def get_thread_updates_since(
        self,
        msgid: str,
        since: datetime,
        *,
        strict: bool = True,
        sort: bool = False,
    ) -> list[EmailMessage]:
        """Check a thread for messages newer than *since*.

        Uses the ``rt:`` (Received-date) search prefix, which filters
        by the server-set ``Received:`` header rather than the
        client-set ``Date:`` header, making it more reliable.
        Accepts a :class:`~datetime.datetime` (converted to a UTC
        epoch timestamp internally) and returns an empty list when
        there are no updates, making it easy to poll::

            from datetime import datetime, timedelta, timezone
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            updates = node.get_thread_updates_since(msgid, cutoff)
            if updates:
                print(f'{len(updates)} new message(s)')

        When *strict* is ``True`` (default), results are filtered to
        only messages belonging to the thread rooted at *msgid*.

        When *sort* is ``True``, messages are sorted by Received date.
        """
        epoch = int(since.astimezone(timezone.utc).timestamp())
        msgs = self._fetch_thread_since(msgid, f'rt:{epoch}..')

        if strict and msgs:
            strict_msgs = get_strict_thread(msgs, msgid)
            if isinstance(strict_msgs, list) and len(strict_msgs):
                msgs = strict_msgs
            else:
                return []

        if sort and msgs:
            msgs = sort_msgs_by_received(msgs)

        self._authenticate_msgs(msgs)
        return msgs

    def get_thread_by_query(
        self,
        query: str,
    ) -> list[EmailMessage]:
        """POST a search query and return deduplicated messages."""
        t_mbox = self.get_mbox_by_query(query)
        if not t_mbox:
            raise LookupError('No results for query: %s' % query)
        msgs = split_and_dedupe(t_mbox)
        self._authenticate_msgs(msgs)
        return msgs

    def get_message_by_msgid(self, msgid: str) -> bytes:
        """Fetch a single raw email message by message ID."""
        raw_url = f'{self._url}/{urllib.parse.quote_plus(msgid)}/raw'
        logger.debug('Fetching message from: %s', raw_url)
        try:
            session = self._get_session()
            response = session.get(raw_url)
            response.raise_for_status()
            return response.content
        except Exception as ex:
            raise RemoteError(f'Failed to fetch message from {raw_url}: {ex}') from ex

    # -----------------------------------------------------------------
    # Batch API
    # -----------------------------------------------------------------

    def batch_get_thread_by_msgid(
        self,
        msgids: list[str],
        *,
        strict: bool = True,
        sort: bool = False,
        since: str | None = None,
    ) -> list[list[EmailMessage]]:
        """Fetch threads for multiple message IDs with rate limiting.

        Calls :meth:`get_thread_by_msgid` for each *msgid* with a
        100 ms cooldown between requests (no sleep before the first).

        Returns a list of results in the same order as the input.
        """
        results: list[list[EmailMessage]] = []
        for i, msgid in enumerate(msgids):
            if i > 0:
                time.sleep(0.1)
            results.append(
                self.get_thread_by_msgid(msgid, strict=strict, sort=sort, since=since)
            )
        return results

    def batch_get_thread_by_query(
        self,
        queries: list[str],
    ) -> list[list[EmailMessage]]:
        """Run multiple queries with rate limiting.

        Calls :meth:`get_thread_by_query` for each query with a
        100 ms cooldown between requests (no sleep before the first).

        Returns a list of results in the same order as the input.
        """
        results: list[list[EmailMessage]] = []
        for i, query in enumerate(queries):
            if i > 0:
                time.sleep(0.1)
            results.append(self.get_thread_by_query(query))
        return results

    def validate(self) -> None:
        """Validate the URL as a public-inbox endpoint.

        Sends a HEAD request to ``{url}/_/text/help/`` and checks for a
        200 response.

        Raises :class:`~liblore.RemoteError` when the URL does not
        appear to be a public-inbox server.
        """
        help_url = f'{self._url}/_/text/help/'
        logger.debug('Validating public-inbox URL: %s', help_url)
        try:
            session = self._get_session()
            resp = session.head(help_url)
        except Exception as ex:
            raise RemoteError(
                f'Failed to reach public-inbox at {help_url}: {ex}'
            ) from ex
        if resp.status_code != 200:
            raise RemoteError(
                f'URL does not appear to be a public-inbox server '
                f'(HEAD {help_url} returned {resp.status_code})'
            )
