"""One bounded JSON request and response per invocation; stdout is protocol-only."""

import contextlib
import json
import os
import sys

MAX_REQUEST_BYTES = 64 * 1024


def main() -> int:
    if sys.argv[1:] != ["convert-document"]:
        return 2
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            return 2
        request = json.loads(raw)
        # Dispatch lazily. Future operations share this executable and interpreter.
        with (
            open(os.devnull, "w") as diagnostics,
            contextlib.redirect_stdout(diagnostics),
            contextlib.redirect_stderr(diagnostics),
        ):
            from meeting_worker.documents import convert_request

            response = convert_request(request)
        sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
        sys.stdout.flush()
        return 0
    except Exception:
        # Exception messages may contain source paths and document text.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
