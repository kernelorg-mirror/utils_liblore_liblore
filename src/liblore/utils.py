# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2025-2026 The Linux Foundation
"""Message parsing, email utilities, threading, and mbox splitting."""
from __future__ import annotations

from collections.abc import Sequence
import datetime
import email.header
import email.parser
import email.quoprimime
import email.utils
import fnmatch
import logging
import re
import textwrap
import urllib.parse

from email.message import EmailMessage

from liblore import emlpolicy

logger = logging.getLogger(__name__)

# Matches the "From " separator line that starts each message in an mbox.
# Mirrors git's is_from_line(): "From " + address + timestamp with a
# colon surrounded by digits (HH:MM:SS) and a year > 90.
_FROM_RE = re.compile(
    rb'^From \S+.*\d\d:\d\d:\d\d.*\d{4}',
)

# Matches one or more ">" followed by "From " — the mboxrd escaping
# that needs one level of ">" stripped.  Mirrors git's is_gtfrom().
_GTFROM_RE = re.compile(rb'^>+From ')


# =====================================================================
# Header and message-ID utilities
# =====================================================================

def clean_header(hdrval: str | None) -> str:
    """Decode an RFC 2047 encoded header and normalise whitespace.

    Handles encoded words, email addresses with encoded display names,
    and the CPython formataddr quoting bug (bpo-100900).
    """
    if hdrval is None:
        return ''

    if hdrval.find('=?') >= 0:
        # If the header contains email addresses, decode them individually
        if re.search(r'<\S+@\S+>', hdrval, flags=re.I | re.M):
            newaddrs: list[str] = []
            for addr in email.utils.getaddresses([hdrval]):
                if addr[0].find('=?') >= 0:
                    addr = (clean_header(addr[0]), addr[1])
                # Work around https://github.com/python/cpython/issues/100900
                if re.search(r'[^\w\s]', addr[0]):
                    newaddrs.append(f'"{addr[0]}" <{addr[1]}>')
                else:
                    newaddrs.append(email.utils.formataddr(addr))
            return ', '.join(newaddrs)

        decoded = ''
        for hstr, hcs in email.header.decode_header(hdrval):
            if hcs is None:
                hcs = 'utf-8'
            try:
                decoded += hstr.decode(hcs, errors='replace')
            except LookupError:
                decoded += hstr.decode('utf-8', errors='replace')
            except (UnicodeDecodeError, AttributeError):
                decoded += hstr
    else:
        decoded = hdrval

    new_hdrval = re.sub(r'\n?\s+', ' ', decoded)
    return new_hdrval.strip()


def get_clean_msgid(msg: EmailMessage, header: str = 'Message-Id') -> str | None:
    """Extract a clean message ID (without angle brackets) from a header."""
    raw = msg.get(header)
    if raw:
        matches = re.search(r'<([^>]+)>', clean_header(raw))
        if matches:
            return matches.groups()[0]
    return None


def parse_message(raw: bytes) -> EmailMessage:
    """Parse raw email bytes into an EmailMessage using our policy."""
    msg: EmailMessage = email.parser.BytesParser(
        policy=emlpolicy,
    ).parsebytes(raw)
    return msg


# =====================================================================
# Email formatting and extraction
# =====================================================================


def msg_get_subject(msg: EmailMessage, strip_prefixes: bool = False) -> str:
    """Return the decoded Subject header.

    When *strip_prefixes* is ``True``, any leading ``[bracketed]``
    prefixes (e.g. ``[PATCH v3 2/5]``, ``[RFC]``) and ``Re:``/``Aw:``
    tags are removed, leaving just the bare subject text.
    """
    raw = msg.get('Subject', '')
    subject = clean_header(raw)
    if not strip_prefixes:
        return subject
    # Strip Re:/Aw:/Fwd: prefixes
    subject = re.sub(r'^(\w{2,3}:\s*)+', '', subject)
    # Strip all leading [bracketed] prefixes
    subject = re.sub(r'^(\s*\[[^]]*]\s*)+', '', subject)
    return subject.strip()


def msg_get_author(msg: EmailMessage) -> tuple[str, str]:
    """Return ``(name, email)`` for the From header."""
    author = ('', 'missing@address.local')
    fh = msg.get('from')
    if fh:
        author = email.utils.getaddresses([fh])[0]
    return author


def msg_get_payload(
    msg: EmailMessage,
    strip_quoted: bool = False,
    strip_signature: bool = True,
) -> str:
    """Return the plain-text body of a message.

    Optionally strips the email signature (``-- `` marker) and/or
    quoted lines (lines starting with ``> ``).
    """
    mp = msg.get_body(preferencelist=('plain',))
    if mp is None:
        return ''
    bbody = mp.get_payload(decode=True)
    if bbody is None:
        return ''
    cs = mp.get_content_charset()
    if not cs:
        cs = 'utf-8'
    if isinstance(bbody, bytes):
        cpay = bbody.decode(cs, errors='replace')
    else:
        cpay = str(bbody)

    if strip_signature:
        chunks = cpay.rsplit('\n-- \n', maxsplit=1)
        cbody = chunks[0]
    else:
        cbody = cpay

    if not strip_quoted:
        return cbody

    stripped: list[str] = []
    for line in cbody.splitlines():
        if not line.startswith('> '):
            stripped.append(line)
    return '\n'.join(stripped)



# Adapted from email._parseaddr — RFC 5322 specials that require quoting
_QSPECIALS = re.compile(r'[()<>@,:;.\"\[\]]')


def format_addrs(pairs: list[tuple[str, str]], clean: bool = True) -> str:
    """Format ``(name, email)`` pairs into an RFC 5322 address string.

    Works around CPython issue #100900 where ``formataddr`` mis-quotes
    display names containing RFC 5322 special characters.  When *clean*
    is ``True`` (the default), RFC 2047 encoded names are decoded first
    via :func:`clean_header`.
    """
    addrs: list[str] = []
    for name, addr in pairs:
        if not name or name == addr:
            addrs.append(addr)
            continue
        if clean:
            name = clean_header(name)
        # Work around https://github.com/python/cpython/issues/100900
        if not name.startswith('=?') and not name.startswith('"') and _QSPECIALS.search(name):
            addrs.append(f'"{email.utils.quote(name)}" <{addr}>')
            continue
        if name.isascii():
            addrs.append(email.utils.formataddr((name, addr)))
        else:
            # Avoid email.utils.formataddr re-encoding unicode to RFC 2047
            addrs.append(f'{name} <{addr}>')
    return ', '.join(addrs)


def wrap_header(hdr: tuple[str, str], width: int = 75, nl: str = '\n') -> bytes:
    """Wrap and RFC 2047-encode a single header for outbound email.

    Address headers (To, Cc, From, X-Original-From) are parsed and each
    address is encoded individually.  Non-address headers are
    word-wrapped (ASCII) or RFC 2047 quoted-printable encoded (non-ASCII).

    *nl* controls the line ending: ``'\\n'`` for dry-run display,
    ``'\\r\\n'`` for SMTP transmission.
    """
    hname, hval = hdr
    _parts: list[str] = []
    if hname.lower() in ('to', 'cc', 'from', 'x-original-from'):
        _parts.append(f'{hname}: ')
        first = True
        for addr in email.utils.getaddresses([hval]):
            if not addr[0].isascii():
                enc_name = email.quoprimime.header_encode(
                    addr[0].encode(), charset='utf-8')
                qp = format_addrs([(enc_name, addr[1])], clean=False)
            else:
                qp = format_addrs([addr], clean=False)
            if first:
                _parts[-1] += qp
                first = False
                continue
            if len(_parts[-1] + ', ' + qp) > width:
                _parts[-1] += ', '
                _parts.append(qp)
                continue
            _parts[-1] += ', ' + qp
    else:
        hdata = f'{hname}: {hval}'
        if hval.isascii():
            if len(hdata) <= width:
                return hdata.encode()
            # Trick: replace ': ' with ':_' so textwrap doesn't break there
            hdata = hdata.replace(': ', ':_', 1)
            wrapped = textwrap.wrap(hdata, break_long_words=False,
                                    break_on_hyphens=False,
                                    subsequent_indent=' ', width=width)
            return nl.join(wrapped).replace(':_', ': ', 1).encode()

        # Non-ASCII: encode as RFC 2047 quoted-printable
        qp = f'{hname}: ' + email.quoprimime.header_encode(
            hval.encode(), charset='utf-8')
        if len(qp) <= width:
            return qp.encode()

        while len(qp) > width:
            wrapat = width - 2
            if len(_parts):
                wrapat -= 1
            # Don't break in the middle of a =XX escape sequence
            while '=' in qp[wrapat - 2:wrapat]:
                wrapat -= 1
            _parts.append(qp[:wrapat] + '?=')
            qp = '=?utf-8?q?' + qp[wrapat:]
        _parts.append(qp)
    return f'{nl} '.join(_parts).encode()


def get_msg_as_bytes(msg: EmailMessage, nl: str = '\n') -> bytes:
    """Serialize a message with proper header encoding for SMTP.

    Uses :func:`wrap_header` for RFC 2047 encoding and line wrapping
    instead of Python's buggy ``as_bytes()``.

    *nl* controls the line ending: ``'\\n'`` for dry-run display,
    ``'\\r\\n'`` for SMTP transmission.
    """
    bdata = b''
    for hname, hval in msg.items():
        bdata += wrap_header((hname, str(hval)), nl=nl) + nl.encode()
    bdata += nl.encode()
    payload = msg.get_payload(decode=True)
    if not isinstance(payload, bytes):
        payload = str(msg.get_payload() or '').encode('utf-8', errors='replace')
    for bline in payload.split(b'\n'):
        bdata += re.sub(rb'[\r\n]*$', b'', bline) + nl.encode()
    return bdata


def msg_get_recipients(msg: EmailMessage) -> set[str]:
    """Return the set of lowercased email addresses from To, Cc, and From."""
    pairs: list[tuple[str, str]] = []
    if msg.get('To'):
        pairs += email.utils.getaddresses([str(x) for x in msg.get_all('to', [])])
    if msg.get('Cc'):
        pairs += email.utils.getaddresses([str(x) for x in msg.get_all('cc', [])])
    if msg.get('From'):
        pairs += email.utils.getaddresses([str(x) for x in msg.get_all('from', [])])
    return {x[1].lower() for x in pairs}


def sort_msgs_by_received(msgs: list[EmailMessage]) -> list[EmailMessage]:
    """Sort messages by latest Received header timestamp, falling back to Date."""
    tosort: list[tuple[datetime.datetime, EmailMessage]] = []
    for msg in msgs:
        latest_rdt: datetime.datetime | None = None
        for rhdr in msg.get_all('Received', []):
            # Received headers generally end with "; datetimeinfo"
            chunks = str(rhdr).rsplit(';', maxsplit=1)
            if len(chunks) < 2:
                continue
            rdate = chunks[1].strip()
            try:
                rdt = email.utils.parsedate_to_datetime(rdate)
            except (ValueError, TypeError):
                continue
            if not latest_rdt or latest_rdt < rdt:
                latest_rdt = rdt
        if not latest_rdt and msg.get('Date'):
            try:
                latest_rdt = email.utils.parsedate_to_datetime(str(msg.get('Date')))
            except (ValueError, TypeError):
                pass
        if not latest_rdt:
            logger.debug('Message without a date: %s!', msg.get('Message-ID'))
            continue
        tosort.append((latest_rdt, msg))

    return [msg for _rdt, msg in sorted(tosort, key=lambda x: x[0])]


# =====================================================================
# Thread filtering
# =====================================================================
def get_strict_thread(
    msgs: list[EmailMessage],
    msgid: str,
    noparent: bool = False,
) -> list[EmailMessage] | None:
    """Walk the reference graph and return only messages belonging to a thread.

    Starting from *msgid*, follows In-Reply-To and References headers to
    collect all messages that are part of the same thread.  Messages
    outside the thread are discarded.

    When *noparent* is ``True``, the parent references of *msgid* are
    ignored, effectively breaking the thread at that point.  This is
    useful for standalone patches posted in the middle of an unrelated
    discussion.

    Returns ``None`` when no messages match.
    """
    # Build msgid -> message map
    msgid_map: dict[str, EmailMessage] = {}
    for msg in msgs:
        c_msgid = get_clean_msgid(msg)
        if c_msgid is not None:
            msgid_map[c_msgid] = msg

    want: set[str] = {msgid}
    ignore: set[str] = set()
    got: set[str] = set()
    seen: set[str] = set()
    maybe: dict[str, set[str]] = {}
    strict: list[EmailMessage] = []

    while True:
        for c_msgid, msg in msgid_map.items():
            if c_msgid in ignore:
                continue
            seen.add(c_msgid)
            if c_msgid in got:
                continue

            refs: set[str] = set()
            raw_refs: list[str] = []
            for hdr_name in ('in-reply-to', 'references'):
                for hval in msg.get_all(hdr_name, []):
                    raw_refs += re.findall(r'<([^>]+)>', str(hval))

            # If noparent, pretend our target message has no references
            if noparent and msgid == c_msgid:
                logger.info('Breaking thread to remove parents of %s', msgid)
                ignore = set(raw_refs)
                raw_refs = []

            for ref in set(raw_refs):
                if ref in ignore:
                    continue
                if ref in got or ref in want:
                    want.add(c_msgid)
                elif len(ref):
                    refs.add(ref)
                    if c_msgid not in want:
                        if ref not in maybe:
                            maybe[ref] = set()
                        maybe[ref].add(c_msgid)

            if c_msgid in want:
                strict.append(msg)
                got.add(c_msgid)
                want.update(refs)
                want.discard(c_msgid)
                if c_msgid in maybe:
                    want.update(maybe[c_msgid])
                    maybe.pop(c_msgid)
                for ref in refs:
                    if ref in maybe:
                        want.update(maybe[ref])
                        maybe.pop(ref)

        # Remove entries that are missing or already collected
        for c_msgid in set(want):
            if c_msgid not in seen or c_msgid in got:
                want.remove(c_msgid)
        if not want:
            break

    if not strict:
        return None

    if len(msgs) > len(strict):
        logger.debug(
            'Reduced thread to requested matches only (%s->%s)',
            len(msgs),
            len(strict),
        )

    return strict


# =====================================================================
# Thread minimization
# =====================================================================

# Detect diff and diffstat content — messages containing these are
# preserved verbatim by minimize_thread().
DIFF_RE = re.compile(r'^diff\s', re.M)
DIFFSTAT_RE = re.compile(r'^\s*\d+ file.*changed', re.M)

# Default set of headers to keep when minimizing messages.
MINIMIZE_KEEP_HEADERS: tuple[str, ...] = (
    'From', 'To', 'Cc', 'Subject', 'Date',
    'Message-ID', 'Reply-To', 'In-Reply-To',
)


def minimize_thread(
    msgs: list[EmailMessage],
    keep_headers: Sequence[str] | None = None,
    reduce_quote_context: bool = False,
) -> list[EmailMessage]:
    """Reduce thread messages to essential headers and clean up excessive quoting.

    Creates lightweight copies of each message, keeping only the headers
    listed in *keep_headers* (defaults to :data:`MINIMIZE_KEEP_HEADERS`).

    For non-patch messages the body is processed to remove:
    - Multi-level quotes (``>>`` and deeper)
    - Empty quote-only lines (bare ``>``)
    - Trailing quoted blocks at the bottom of the message

    When *reduce_quote_context* is ``True``, quoted blocks that precede
    a reply are further reduced to just the last paragraph, with
    earlier paragraphs replaced by a ``> [... skip NN lines ...]``
    marker.  This only kicks in when more than 5 lines would be
    skipped, so short quotes are left untouched.

    Compliant email signatures (``-- `` marker) are split off before
    quote processing and re-appended afterwards, so that a trailing
    quoted block followed only by a signature is correctly recognised
    as a bottom quote and dropped.

    Messages containing diffs or diffstats are preserved as-is.
    Messages that become empty after minimization are dropped.
    """
    if keep_headers is None:
        keep_headers = MINIMIZE_KEEP_HEADERS

    mmsgs: list[EmailMessage] = []
    for msg in msgs:
        mmsg = EmailMessage()
        for hdr in keep_headers:
            val = clean_header(msg[hdr])
            if val:
                mmsg[hdr] = val

        body = msg_get_payload(msg, strip_signature=False)

        if not re.search(r'^>', body, re.M) or DIFF_RE.search(body) or DIFFSTAT_RE.search(body):
            mmsg.set_payload(body, charset='utf-8')
            mmsgs.append(mmsg)
            continue

        # Split off the signature before quote processing so trailing
        # quoted blocks before the sig are recognised as bottom quotes.
        sig = ''
        sig_parts = body.rsplit('\n-- \n', maxsplit=1)
        if len(sig_parts) == 2:
            body = sig_parts[0]
            sig = '\n-- \n' + sig_parts[1]

        # Split lines into alternating quoted / unquoted chunks
        chunks: list[tuple[bool, list[str]]] = []
        chunk: list[str] = []
        current: bool | None = None
        for line in body.rstrip().splitlines():
            quoted = line.startswith('>')
            if current is None:
                current = quoted
            if current == quoted:
                if quoted and re.search(r'^>\s*>', line):
                    # Strip multi-level quotes
                    continue
                if quoted and not chunk and line.strip() == '>':
                    # Strip leading empty quote lines
                    continue
                chunk.append(line)
                continue

            # Flush previous chunk, trimming trailing empty quote lines
            if current:
                while chunk and chunk[-1].strip() == '>':
                    chunk.pop(-1)
            if chunk:
                chunks.append((current, chunk))
            chunk = []
            if not (quoted and line.strip() == '>'):
                chunk.append(line)
            current = quoted

        if current is None:
            current = False

        # Don't append trailing quoted chunks
        if chunk and not current:
            chunks.append((current, chunk))

        # Reduce long quoted blocks to just the last paragraph
        if reduce_quote_context:
            for idx, (is_quoted, qchunk) in enumerate(chunks):
                if not is_quoted:
                    continue
                # Only reduce quoted chunks followed by an unquoted reply
                if idx + 1 >= len(chunks) or chunks[idx + 1][0]:
                    continue
                # Find the last paragraph boundary (empty quote line)
                last_break = -1
                for li, line in enumerate(qchunk):
                    if line.strip() in ('>', ''):
                        last_break = li
                if last_break <= 0:
                    continue
                skipped = last_break
                if skipped <= 5:
                    continue
                last_para = qchunk[last_break + 1:]
                chunks[idx] = (True, [
                    f'> [... skip {skipped} lines ...]',
                    '>',
                ] + last_para)

        parts: list[str] = []
        for _quoted, chunk in chunks:
            parts.append('\n'.join(chunk))
        body = '\n'.join(parts).strip() + '\n'

        if not body.strip():
            continue

        # Re-append the signature
        if sig:
            body = body.rstrip('\n') + sig

        mmsg.set_payload(body, charset='utf-8')
        mmsgs.append(mmsg)

    return mmsgs


# =====================================================================
# Mbox splitting and deduplication
# =====================================================================
def _is_from_line(line: bytes) -> bool:
    """Check whether *line* is an mbox ``From `` separator."""
    return len(line) >= 20 and _FROM_RE.match(line) is not None


def split_mbox(mbox_bytes: bytes) -> list[EmailMessage]:
    """Split mboxrd bytes into a list of parsed EmailMessage objects.

    Handles the mboxrd variant used by public-inbox: for every body
    line that matches ``>+From ``, one leading ``>`` is stripped.
    """
    if not mbox_bytes:
        return []

    msgs: list[EmailMessage] = []
    current: list[bytes] = []
    in_header = False

    for raw_line in mbox_bytes.split(b'\n'):
        line = raw_line + b'\n'

        if _is_from_line(line):
            # Flush the previous message
            if current:
                msgs.append(parse_message(b''.join(current)))
                current = []
            in_header = True
            continue

        if in_header and line == b'\n':
            # End of headers, start of body
            in_header = False
            current.append(line)
            continue

        if not in_header and _GTFROM_RE.match(line):
            # Undo one level of mboxrd >From escaping
            line = line[1:]

        current.append(line)

    # Flush the last message
    if current:
        msgs.append(parse_message(b''.join(current)))

    return msgs


# Default List-Id preference order.  Sources listed first are less
# likely to mangle messages (e.g. rewrite From: or add footers), so
# they are preferred when the same message arrives from multiple lists.
DEFAULT_LISTID_PREFERENCE: list[str] = [
    '*.feeds.kernel.org',
    '*.linux.dev',
    '*.kernel.org',
    '*',
]


def get_preferred_duplicate(
    msg1: EmailMessage,
    msg2: EmailMessage,
    listid_preference: list[str] | None = None,
) -> EmailMessage:
    """Return the preferred copy of a duplicate message.

    When the same message (same Message-ID) arrives from multiple
    mailing lists, some lists mangle it (Mailman 2 rewrites From:,
    adds footers, etc.).  This function picks the copy from the
    source least likely to mangle, based on the ``List-Id`` header
    and the *listid_preference* glob patterns.

    Patterns earlier in the list are preferred.  The default order
    matches b4's ``listid-preference`` setting.
    """
    if listid_preference is None:
        listid_preference = DEFAULT_LISTID_PREFERENCE

    def _preference_index(msg: EmailMessage) -> int:
        listid = get_clean_msgid(msg, 'List-Id')
        if listid:
            for idx, pattern in enumerate(listid_preference):
                if fnmatch.fnmatch(listid, pattern):
                    return idx
        # No List-Id or no pattern matched — use the wildcard position
        try:
            return listid_preference.index('*')
        except ValueError:
            return len(listid_preference)

    idx1 = _preference_index(msg1)
    idx2 = _preference_index(msg2)
    if idx1 <= idx2:
        logger.debug('Picked duplicate from preferred source: %s',
                      get_clean_msgid(msg1, 'List-Id'))
        return msg1
    logger.debug('Picked duplicate from preferred source: %s',
                  get_clean_msgid(msg2, 'List-Id'))
    return msg2


def split_and_dedupe(
    mbox_bytes: bytes,
    listid_preference: list[str] | None = None,
) -> list[EmailMessage]:
    """Split mboxrd bytes and deduplicate by Message-ID.

    When duplicate Message-IDs are found, the copy from the
    preferred source is kept (based on ``List-Id`` header matching
    against *listid_preference* patterns).  Pass an empty list to
    disable preference logic and keep the first occurrence.

    Messages without a Message-ID are silently dropped.
    """
    if listid_preference is None:
        listid_preference = DEFAULT_LISTID_PREFERENCE
    use_preference = len(listid_preference) > 0

    msgs = split_mbox(mbox_bytes)
    deduped: dict[str, EmailMessage] = {}
    for msg in msgs:
        msgid = get_clean_msgid(msg)
        if msgid is None:
            logger.debug(
                'No message-id found, ignoring %s',
                msg.get('Subject', '(no subject)'),
            )
            continue
        if msgid in deduped:
            if use_preference:
                deduped[msgid] = get_preferred_duplicate(
                    deduped[msgid], msg, listid_preference,
                )
            else:
                logger.debug('Dropping duplicate message-id: %s', msgid)
            continue
        deduped[msgid] = msg
    return list(deduped.values())


# =====================================================================
# Pure helpers 
# =====================================================================

def get_msgid_from_url(url: str) -> str:
    """Extract a message ID from a lore.kernel.org URL.

    If the input is already a bare message ID (no ``://``), it is
    returned with angle brackets stripped.
    """
    if '://' in url:
        matches = re.search(r'^https?://[^@]+/([^/]+(?:@|%40)[^/]+)', url, re.IGNORECASE)
        if matches:
            return urllib.parse.unquote(matches.groups()[0])
    return url.strip('<>')
