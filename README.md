# liblore

A Python library for working with [public-inbox](https://public-inbox.org/)
servers, particularly [lore.kernel.org](https://lore.kernel.org/). It fetches
email threads, parses mbox files, and provides utilities for working with
email messages from mailing list archives.

## Requirements

- Python 3.11 or newer
- `requests` >= 2.31
- `authheaders` >= 0.15 (optional, for DKIM/DMARC/ARC verification)

## Installation

Install from PyPI:

```shell
pip install liblore
```

To include optional email authentication support (DKIM, DMARC, ARC):

```shell
pip install liblore[auth]
```

Or install from source:

```shell
pip install .
```

## Quick Start

The main entry point is the `LoreNode` class. It connects to a public-inbox
endpoint and lets you fetch threads, search for messages, and work with raw
mbox data. Use it as a context manager so the underlying HTTP session is
cleaned up automatically:

```python
from liblore import LoreNode

with LoreNode("https://lore.kernel.org/all") as node:
    msgs = node.get_thread_by_msgid(
        "20250101-example@kernel.org",
        sort=True,
    )
    for msg in msgs:
        print(msg["Subject"])
```

If you omit the URL, it defaults to `https://lore.kernel.org/all`.

## API Reference

### LoreNode

```python
from liblore import LoreNode

node = LoreNode(url="https://lore.kernel.org/all")
```

#### Fetching Threads

**`node.get_thread_by_msgid(msgid, *, strict=True, sort=False, since=None)`**

Fetch a thread by its message ID. This is the highest-level method and the
one you will reach for most often.

- `strict` (default `True`) -- filter results to only messages that belong
  to the thread rooted at `msgid`. When a query returns messages from
  unrelated threads (common with broad date ranges), strict mode discards
  them.
- `sort` -- sort the returned messages by their `Received` header timestamp.
- `since` -- a date string appended as a `d:` filter. This uses
  public-inbox's approxidate syntax, so you can write things like
  `"20240115"`, `"2.weeks.ago"`, or `"last.month"`.

Returns a `list[EmailMessage]`. Raises `LookupError` if no messages match.

```python
with LoreNode() as node:
    # Fetch a thread, sorted by date, only looking at recent messages
    msgs = node.get_thread_by_msgid(
        "20250101-example@kernel.org",
        strict=True,
        sort=True,
        since="20250101",
    )
```

**`node.get_thread_updates_since(msgid, since, *, strict=True, sort=False)`**

Check whether a thread has new messages since a given point in time. This is
handy for polling use cases where you want to know if anything new has arrived.

- `since` -- a `datetime` object. Converted to a UTC epoch timestamp
  internally and matched against the server-set `Received` header (`rt:`
  prefix), which is more reliable than the client-set `Date` header.
- `strict` (default `True`) -- filter results to only messages belonging to
  the thread rooted at `msgid`.
- `sort` -- sort the returned messages by their `Received` header timestamp.

Returns a `list[EmailMessage]`. Returns an empty list (rather than raising)
when there are no updates.

```python
from datetime import datetime, timedelta, timezone

with LoreNode() as node:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    updates = node.get_thread_updates_since(
        "20250101-example@kernel.org", cutoff,
    )
    if updates:
        print(f"{len(updates)} new message(s)")
```

**`node.get_thread_by_query(query)`**

Run a search query and return a deduplicated `list[EmailMessage]`. The query
uses public-inbox's
[Xapian search syntax](https://public-inbox.org/HOWTO#search), which supports
prefixes like `msgid:`, `s:` (subject), `f:` (from), `d:` (date range), and
more.

```python
with LoreNode() as node:
    # Find all messages from a sender in the last month
    msgs = node.get_thread_by_query("f:alice@example.com d:last.month..")
```

#### Batch Fetching

When you need to fetch multiple threads, the batch methods handle the loop for
you and add a 100 ms cooldown between requests so you're being a good citizen
to the server.

**`node.batch_get_thread_by_msgid(msgids, *, strict=True, sort=False, since=None)`**

Fetch threads for a list of message IDs. Calls `get_thread_by_msgid()` for
each one with a brief pause between requests. Returns a
`list[list[EmailMessage]]` in the same order as the input.

```python
with LoreNode() as node:
    threads = node.batch_get_thread_by_msgid(
        ["msg1@example.com", "msg2@example.com", "msg3@example.com"],
        sort=True,
        since="2.weeks.ago",
    )
    for thread in threads:
        print(f"Thread with {len(thread)} messages")
```

**`node.batch_get_thread_by_query(queries)`**

Run multiple search queries. Same pattern -- calls `get_thread_by_query()` per
query with a 100 ms cooldown. Returns a `list[list[EmailMessage]]`.

```python
with LoreNode() as node:
    results = node.batch_get_thread_by_query([
        "s:fix f:alice@example.com",
        "s:feature f:bob@example.com",
    ])
```

#### Raw Mbox Access

These methods return raw mbox bytes rather than parsed messages. They are
useful when you need the unprocessed data, or when you want to feed the
output into your own parser.

**`node.get_mbox_by_msgid(msgid)`** -- fetch a thread's mbox by message ID.

**`node.get_mbox_by_query(query)`** -- run a search query and return the
matching mbox.

```python
with LoreNode() as node:
    raw = node.get_mbox_by_msgid("20250101-example@kernel.org")
    with open("thread.mbox", "wb") as f:
        f.write(raw)
```

#### Single Messages

**`node.get_message_by_msgid(msgid)`** -- fetch a single raw message (bytes)
by its message ID. Useful when you need exactly one message rather than an
entire thread.

#### Session Configuration

**`node.set_user_agent(app_name, version, plus=None)`** -- set a custom
`User-Agent` header. Being a good citizen of public infrastructure means
identifying your tool:

```python
node.set_user_agent("my-tool", "1.0")
# User-Agent: my-tool/1.0
```

**`node.set_requests_session(session)`** -- inject your own
`requests.Session`. Handy when you need custom timeouts, proxies, or
authentication. Note that the session's `User-Agent` is not overwritten
when you provide your own.

**`node.validate()`** -- check that the configured URL actually points to a
public-inbox server. Raises `RemoteError` if it does not.

**`node.close()`** -- close the HTTP session. Called automatically when
using `LoreNode` as a context manager.

#### Message Authentication

LoreNode can optionally verify DKIM signatures, DMARC alignment, and ARC
chains on every message it retrieves. This requires the `authheaders` package
(install with `pip install liblore[auth]`).

```python
with LoreNode(add_auth_headers=True) as node:
    msgs = node.get_thread_by_msgid("20250101-example@kernel.org")
    for msg in msgs:
        print(msg["Authentication-Results"])
        # liblore; dkim=pass header.d=kernel.org; ...
```

When enabled, each returned `EmailMessage` gets an `Authentication-Results`
header added by the [authheaders](https://pypi.org/project/authheaders/)
library. SPF is not checked because archived messages don't carry the SMTP
transaction info (client IP, MAIL FROM, HELO) that SPF requires.

If `add_auth_headers=True` is set but `authheaders` is not installed, a
`LibloreError` is raised immediately on construction.

### How the API Layers Fit Together

The methods build on each other in layers, from raw bytes up to filtered,
sorted thread views:

```
get_mbox_by_msgid / get_mbox_by_query      ->  raw mbox bytes
        |
get_thread_by_query                        ->  split + dedupe -> list[EmailMessage]
        |
get_thread_by_msgid                        ->  strict + sort  -> list[EmailMessage]
        |
get_thread_updates_since                   ->  poll for new   -> list[EmailMessage]
        |
batch_get_thread_by_msgid / batch_get_...  ->  rate-limited loop -> list[list[EmailMessage]]
```

You can tap into whichever layer suits your needs. Need raw bytes for
archiving? Use the `get_mbox_*` methods. Need parsed messages with
deduplication? Use `get_thread_by_query`. Want the full convenience of
strict filtering and date sorting? Use `get_thread_by_msgid`. Need to
poll for new messages? Use `get_thread_updates_since`.

### Utility Functions

The `liblore.utils` module provides lower-level helpers for parsing and
inspecting email messages.

#### Header Handling

```python
from liblore.utils import clean_header, get_clean_msgid

# Decode RFC 2047 encoded headers
decoded = clean_header("=?utf-8?q?Re=3A_Some_Subject?=")

# Extract a clean message ID (without angle brackets) from a message
msgid = get_clean_msgid(msg)               # reads Message-Id by default
msgid = get_clean_msgid(msg, "In-Reply-To")  # or any other header
```

#### Parsing Messages

```python
from liblore.utils import parse_message

# Parse raw email bytes into an EmailMessage
msg = parse_message(raw_bytes)
```

#### Extracting Message Content

```python
from liblore.utils import (
    msg_get_subject,
    msg_get_author,
    msg_get_payload,
    msg_get_recipients,
)

# Get the decoded subject line
subject = msg_get_subject(msg)

# Strip [PATCH v3 2/5] and Re: prefixes to get the bare subject
bare = msg_get_subject(msg, strip_prefixes=True)

# Get the author as a (name, email) tuple
name, addr = msg_get_author(msg)

# Get the plain-text body, stripping the signature
body = msg_get_payload(msg)

# Get the body without quoted lines or signature
body = msg_get_payload(msg, strip_quoted=True, strip_signature=True)

# Get all recipient email addresses (To + Cc + From)
recipients = msg_get_recipients(msg)
```

#### Email Serialization

These functions replace Python's buggy `as_bytes()` with battle-tested
serialization that correctly handles RFC 2047 header encoding, line wrapping,
and non-ASCII display names.

```python
from liblore.utils import format_addrs, wrap_header, get_msg_as_bytes

# Format (name, email) pairs into an RFC 5322 address string
formatted = format_addrs([
    ("", "foo@example.com"),
    ("Foo Bar", "bar@example.com"),
])
# -> 'foo@example.com, Foo Bar <bar@example.com>'

# Wrap and RFC 2047-encode a header for SMTP
hdr_bytes = wrap_header(("Subject", "Hello world"))

# Serialize a full message to bytes with proper encoding
msg_bytes = get_msg_as_bytes(msg)            # \n line endings (dry-run)
msg_bytes = get_msg_as_bytes(msg, nl="\r\n") # \r\n for SMTP
```

#### Sorting and Threading

```python
from liblore.utils import sort_msgs_by_received, get_strict_thread

# Sort messages by their Received timestamp (falls back to Date)
sorted_msgs = sort_msgs_by_received(msgs)

# Filter a list of messages to only those in a specific thread
thread = get_strict_thread(msgs, "20250101-example@kernel.org")

# Break the thread at msgid, ignoring its parent references
thread = get_strict_thread(msgs, msgid, noparent=True)
```

#### Thread Minimization

```python
from liblore.utils import minimize_thread

# Strip excessive quoting and non-essential headers for compact display
minimized = minimize_thread(msgs)

# Customize which headers to keep
minimized = minimize_thread(msgs, keep_headers=("From", "Subject", "Date"))

# Aggressively reduce long quotes to just the last paragraph
minimized = minimize_thread(msgs, reduce_quote_context=True)
```

`minimize_thread()` creates lightweight copies of each message: it keeps only
essential headers (From, To, Cc, Subject, Date, Message-ID, Reply-To,
In-Reply-To by default), strips multi-level quotes and trailing quoted blocks,
and drops messages that become empty after processing. Messages containing
diffs or diffstats are preserved as-is.

When `reduce_quote_context=True`, long quoted blocks preceding a reply are
trimmed to just the last paragraph, with earlier content replaced by a
`> [... skip NN lines ...]` marker.  This only applies when more than 5 lines
would be skipped.

#### Mbox Splitting

```python
from liblore.utils import split_mbox, split_and_dedupe

# Split mboxrd bytes into a list of EmailMessage objects
msgs = split_mbox(mbox_bytes)

# Split and deduplicate by Message-ID (first occurrence wins)
msgs = split_and_dedupe(mbox_bytes)
```

#### URL Helpers

```python
from liblore.utils import get_msgid_from_url

# Extract a message ID from a lore URL
msgid = get_msgid_from_url("https://lore.kernel.org/all/20250101-example@kernel.org/")
# -> "20250101-example@kernel.org"

# Also works with bare message IDs
msgid = get_msgid_from_url("<20250101-example@kernel.org>")
# -> "20250101-example@kernel.org"
```

### Exceptions

All exceptions inherit from `LibloreError`, so you can catch them broadly or
handle specific cases:

```python
from liblore import LibloreError, RemoteError, PublicInboxError

try:
    msgs = node.get_thread_by_msgid("nonexistent@example.com")
except RemoteError:
    # HTTP request failed (server error, network issue, etc.)
    ...
except PublicInboxError:
    # Something went wrong with the public-inbox operation
    ...
except LibloreError:
    # Catch-all for any liblore error
    ...
```

## Development

Install with development dependencies:

```shell
pip install -e ".[dev]"
```

Run the test suite:

```shell
pytest
```

Type checking:

```shell
mypy src/liblore/ --strict
```

Linting:

```shell
ruff check src/liblore/
```

## Bug Reports

Send bug reports and patches to [tools@kernel.org](mailto:tools@kernel.org).

## Licence

GPL-2.0-or-later. See [LICENSES/GPL-2.0-or-later.txt](LICENSES/GPL-2.0-or-later.txt)
for the full text.

Copyright The Linux Foundation.
