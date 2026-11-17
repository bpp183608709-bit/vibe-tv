"""The DeepSeek call layer: one client, four rules, no surprises.

This is the only module in the repository that talks to a language model, and the rules it keeps are
deployment constraints rather than preferences:

  * **The key comes from `os.environ["DEEPSEEK_API_KEY"]` and from nowhere else.** It is injected by
    `/usr/local/bin/tvault-run.sh` before this process starts. There is no config file to read it from,
    no CLI flag to pass it in, and no fallback, because every one of those paths ends with the key in a
    place that outlives the process.
  * **Nothing about the key is ever logged** — not the value, not a prefix, not a suffix. The startup
    line says `key_present=true`; that is the whole of what a log may know.
  * **Neither the request nor the response body is logged.** Both contain a child's answers.
  * **The four failures mean four different things.** A 401 is a broken deployment and no amount of
    retrying fixes it; a 402 is an empty balance and someone should be woken up; a 400 is our bug; a 429
    or a 5xx is the other side having a bad minute and is the only case worth retrying.

Transport is `http.client` from the standard library rather than `httpx`/`requests`/`openai`, for the
same reason `api.py` uses `http.server`: the box has 1.6 GB and every dependency is something else that
can go wrong at 3 a.m. `http.client` also gives the connect timeout and the read timeout separately,
which `urllib.request` cannot express — connecting is 10 seconds, waiting for the first byte is 60.

    from server.app.llm import client
    client.require_key()                       # exits 78 if the vault did not inject one
    reply = client.chat([{"role": "user", "content": "..."}])
"""

from __future__ import annotations

import http.client
import json
import os
import random
import socket
import ssl
import sys
import threading
import time

#: Where the API lives. No `/v1` — DeepSeek serves `/chat/completions` at the root, and adding the
#: suffix that OpenAI's SDK expects is the most common way to get a 404 that looks like a network error.
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")

#: `deepseek-flash` is the cheap one and the right default. `deepseek-chat` and `deepseek-reasoner`
#: were retired on 2026-07-24. The name lives in an environment variable so a model change is a
#: restart, not a code change.
DEFAULT_MODEL = "deepseek-flash"

#: Two timeouts, because they answer different questions. Ten seconds to get a connection; sixty to
#: get a whole answer. The interactive path uses the shorter total so a television is never left
#: staring at a spinner.
CONNECT_TIMEOUT = 10.0
TOTAL_TIMEOUT = 60.0
INTERACTIVE_TIMEOUT = 30.0

#: Retries are 1s, 2s, 4s plus up to 30 % jitter. Four attempts total, which is what the deployment
#: spec asks for and enough to ride out a flaky minute without holding a request for a minute.
RETRY_DELAYS = (1.0, 2.0, 4.0)

#: A client-side cap. The account allows 2500 concurrent calls on flash; this system will never need
#: more than a dozen and a runaway loop should queue up rather than bill in parallel.
DEFAULT_CONCURRENCY = 8

#: Bodies are JSON and small. A 1 MB ceiling stops a misbehaving endpoint from being read into memory
#: in full before anyone notices.
MAX_BODY = 1_000_000


class LLMError(RuntimeError):
    """A call that will not be retried, carrying the kind the caller has to branch on.

    `kind` is one of: `auth` (401, key rejected), `balance` (402, out of credit), `bad-request`
    (400/422, our fault), `http` (any other non-retryable status), `network` (transport gave up),
    `exhausted` (retryable statuses, every attempt used), `shape` (200 but not the JSON we asked for).
    """

    def __init__(self, kind: str, detail: str = "", status: int | None = None) -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind == "exhausted"


#: Set when a call came back 401 or 402. The health endpoint reports it; nothing else acts on it,
#: because a degraded flag that quietly changes behaviour is worse than one a human reads.
_degraded = False
_degraded_lock = threading.Lock()

_slots: threading.BoundedSemaphore | None = None
_slots_lock = threading.Lock()


def concurrency() -> int:
    try:
        return max(1, int(os.environ.get("DEEPSEEK_CONCURRENCY", DEFAULT_CONCURRENCY)))
    except ValueError:
        return DEFAULT_CONCURRENCY


def _semaphore() -> threading.BoundedSemaphore:
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(concurrency())
        return _slots


def model() -> str:
    return os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def key_present() -> bool:
    return bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())


def require_key() -> str:
    """Return the key, or exit 78 (EX_CONFIG) with exactly one line.

    No event line here on purpose: this is the one message a human will read in `systemctl status`,
    and it has to fit in the five lines systemd shows.
    """
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        print("FATAL: DEEPSEEK_API_KEY 未注入，请检查 tvault 凭证", file=sys.stderr)
        raise SystemExit(78)
    return key


def degraded() -> bool:
    return _degraded


def _mark_degraded() -> None:
    global _degraded
    with _degraded_lock:
        _degraded = True


def event(level: str, name: str, **fields: object) -> None:
    """One line, `key=value`, no exception, no traceback, no content.

    journald already timestamps and tags every line, so the timestamp here is only there to make a
    copied log readable. Values are flattened; anything with a space is expected to have been
    sanitised by the caller (we only ever pass numbers, enums and durations).
    """
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tail = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"{stamp} level={level} event={name}{' ' + tail if tail else ''}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- transport


def _post(path: str, payload: dict, key: str, total_timeout: float) -> tuple[int, bytes]:
    """One HTTP round trip. Returns (status, body); raises LLMError('network') on transport failure."""
    parts = BASE_URL.split("://", 1)
    scheme = parts[0]
    host_port = parts[1] if len(parts) > 1 else ""
    host, _, port_text = host_port.partition("/")
    port = None
    if ":" in host:
        host, _, port_text2 = host.partition(":")
        port = int(port_text2)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    if scheme == "https":
        conn = http.client.HTTPSConnection(
            host, port or 443, timeout=CONNECT_TIMEOUT, context=ssl.create_default_context()
        )
    else:
        conn = http.client.HTTPConnection(host, port or 80, timeout=CONNECT_TIMEOUT)

    try:
        try:
            conn.connect()
        except (OSError, ssl.SSLError, socket.timeout) as exc:
            raise LLMError("network", f"connect {type(exc).__name__}") from exc
        # From here on the clock is the read budget, not the connect budget. `socket.settimeout`
        # applies to the socket underneath, which http.client reuses for the response.
        if conn.sock is not None:
            conn.sock.settimeout(total_timeout)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            conn.request("POST", path, body=body, headers=headers)
            response = conn.getresponse()
            data = response.read(MAX_BODY)
            return response.status, data
        except (OSError, ssl.SSLError, socket.timeout) as exc:
            raise LLMError("network", f"io {type(exc).__name__}") from exc
    finally:
        conn.close()


def _sleep_for(attempt: int) -> float:
    base = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
    return base * (1.0 + random.random() * 0.3)


def _retryable(status: int) -> bool:
    return status in (429, 500, 502, 503, 504)


def chat(
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    json_mode: bool = False,
    thinking: bool = False,
    reasoning_effort: str = "high",
    interactive: bool = False,
    timeout: float | None = None,
) -> dict:
    """Call `/chat/completions` and return the decoded response body.

    The payload follows the deployment spec's baseline: `stream` off (a television has nothing to do
    with a token stream), thinking off except where a caller asks for it, no `frequency_penalty` or
    `presence_penalty` (DeepSeek ignores both, and sending them invites the reader to believe they do
    something).

    `json_mode` sets `response_format`. DeepSeek rejects that with a 400 unless the word "json" also
    appears in the conversation, which is the caller's job — `prompts.py` does it.
    """
    key = require_key()
    payload: dict = {
        "model": model(),
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if thinking:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = reasoning_effort
    if interactive:
        payload["thinking"] = {"type": "disabled"}

    budget = timeout or (INTERACTIVE_TIMEOUT if interactive else TOTAL_TIMEOUT)
    started = time.monotonic()
    last = LLMError("exhausted", "no attempt made")

    with _semaphore():
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                status, raw = _post("/chat/completions", payload, key, budget)
            except LLMError as exc:
                last = exc
                if attempt >= len(RETRY_DELAYS):
                    break
                wait = _sleep_for(attempt)
                event("WARNING", "llm_retry", attempt=attempt + 1, reason="network", wait_ms=int(wait * 1000))
                time.sleep(wait)
                continue

            if status == 200:
                try:
                    decoded = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise LLMError("shape", "response was not json") from exc
                usage = decoded.get("usage") or {}
                event(
                    "DEBUG",
                    "llm_call",
                    ms=int((time.monotonic() - started) * 1000),
                    attempts=attempt + 1,
                    prompt_tokens=usage.get("prompt_tokens", -1),
                    completion_tokens=usage.get("completion_tokens", -1),
                )
                return decoded

            if status == 401:
                # The key is wrong. Retrying a wrong key is a way to burn the rate limit and hide the
                # actual problem behind a wall of identical errors.
                _mark_degraded()
                event("ERROR", "llm_auth_failed", status=401)
                raise LLMError("auth", "key rejected", status=401)
            if status == 402:
                _mark_degraded()
                event("ERROR", "llm_no_balance", status=402)
                raise LLMError("balance", "account out of credit", status=402)
            if status in (400, 422):
                event("ERROR", "llm_bad_request", status=status)
                raise LLMError("bad-request", "prompt or payload rejected", status=status)
            if _retryable(status) and attempt < len(RETRY_DELAYS):
                wait = _sleep_for(attempt)
                event("WARNING", "llm_retry", attempt=attempt + 1, status=status, wait_ms=int(wait * 1000))
                time.sleep(wait)
                last = LLMError("exhausted", f"status {status}", status=status)
                continue
            event("ERROR", "llm_give_up", status=status, attempts=attempt + 1)
            raise LLMError("http" if not _retryable(status) else "exhausted", f"status {status}", status=status)

    if last.kind == "exhausted":
        event("ERROR", "llm_give_up", reason=last.detail, attempts=len(RETRY_DELAYS) + 1)
    raise last


def content_of(reply: dict) -> str:
    """The assistant's text, with the shape of the reply checked once here rather than everywhere."""
    try:
        return reply["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("shape", "no choices[0].message.content") from exc
