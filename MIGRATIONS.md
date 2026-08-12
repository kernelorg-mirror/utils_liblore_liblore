# Migration notes

This document describes the changes that downstream tools (such as
[b4](https://b4.docs.kernel.org/)) need to make when moving between
liblore versions that change API behaviour. Routine additions are
listed in the `CHANGELOG`; only migrations that require action from
library users are documented here.

## 0.8 → 0.9: operation-scoped cancellation

### What changed

In 0.8 and earlier, cancellation was a **sticky flag** on the node:
once `cancel()` was called, *every* subsequent request raised
`OperationCancelledError` until someone called `reset_cancel()`. On a
long-lived shared node this forced a "reset before every fetch"
convention onto callers, and a forgotten reset turned into mysterious
cancellation errors on unrelated fetches much later.

In 0.9, cancellation is **operation-scoped**. Every public entry point
(`get_thread_by_msgid()`, `get_mbox_by_query()`, the `batch_*`
methods, `probe_origins()`, `validate()`, and so on) runs as one
logical operation, and cancellation is expressed with two new methods:

- **`cancel_active()`** — cancels only the operations that are in
  flight *right now*. Each one raises `OperationCancelledError` at its
  next request and stays cancelled for the rest of its run (a batch
  aborts fully, even when the cancel lands between its requests).
  Operations started *after* the call are unaffected. There is no
  flag, so there is nothing to reset.

- **`shutdown()`** — the terminal state for application exit. Cancels
  everything in flight like `cancel_active()`, and additionally
  refuses every operation started afterwards (it raises
  `OperationCancelledError` immediately, before any connection is
  opened). Cannot be undone. The new `is_shutdown` property reports
  whether the node has been shut down.

The old methods still work, but are deprecated and emit
`DeprecationWarning`:

- `cancel()` is now an alias for `cancel_active()`. Note the semantic
  change: it **no longer poisons future requests**.
- `reset_cancel()` is now a **no-op**. It exists only so that 0.8-era
  call sites keep running.

### What you need to do

1. **Delete every `reset_cancel()` call.** They do nothing anymore.
   If your code wrapped fetches in a "reset first" helper or context
   manager, the reset half of that helper can go away.

2. **Replace each `cancel()` call with the method that matches its
   intent:**

   | You were using `cancel()` to... | Replace with |
   |---|---|
   | Let the user abort an in-progress fetch (Esc, Ctrl-C) | `cancel_active()` |
   | Stop everything because the application is exiting | `shutdown()` |

   Getting this right matters: `cancel_active()` alone no longer
   prevents *new* requests from starting, so an exit path that used to
   rely on the sticky flag to keep racing workers from opening
   connections should use `shutdown()`.

3. **Drop any workarounds for the sticky flag.** Code that caught
   `OperationCancelledError` and retried after a reset, or that
   defensively reset the flag "just in case", can be simplified to a
   plain call.

### What stays the same

- `OperationCancelledError` is still the exception raised on
  cancellation, and it is still a subclass of `LibloreError`.
- Cancelling still closes the node-owned `requests` session, so a
  thread blocked in a socket read is interrupted immediately. An
  injected session (`set_requests_session()`) is still left alone.
- A socket closed by cancellation is still distinguished from a real
  origin failure: the request loop raises `OperationCancelledError`
  instead of failing over to a mirror.
- Within a single operation, cancellation is still "sticky": once an
  operation has been cancelled, all of its remaining requests raise.
  What changed is only that this no longer leaks into *other*
  operations.

### Notes for test suites

The deprecated shims warn with `DeprecationWarning`, which Python
hides by default at runtime but pytest surfaces (and some
configurations promote to errors). If you cannot migrate all call
sites at once, filter the warning temporarily:

```ini
# pytest.ini / pyproject.toml [tool.pytest.ini_options]
filterwarnings = [
    "ignore:cancel\\(\\) is deprecated:DeprecationWarning",
    "ignore:reset_cancel\\(\\) is deprecated:DeprecationWarning",
]
```

Tests that asserted the sticky behaviour itself (for example,
"request raises unless reset_cancel() was called first") describe
0.8 semantics and should be removed or rewritten against
`cancel_active()`/`shutdown()`.

### Removal timeline

`cancel()` and `reset_cancel()` will be removed in a future release,
no earlier than 1.0. New code should not use them.
