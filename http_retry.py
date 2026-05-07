#!/usr/bin/env python3
from __future__ import annotations

from socket import error as SocketError
import errno
import json
import time
import urllib.request

RETRYABLE_ERRNOS = {errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE, errno.ETIMEDOUT}


def _request_label(request) -> str:
    try:
        return getattr(request, 'full_url', None) or getattr(request, 'get_full_url')()
    except Exception:
        return request.__class__.__name__


def _log_retry(attempt: int, delay: int, request, exc: Exception) -> None:
    print(
        f'http_retry attempt={attempt} delay={delay}s target={_request_label(request)} '
        f'error={type(exc).__name__}: {exc}'
    )


def urlopen_retry(request, timeout: int, retries: tuple[int, ...] = (1, 2, 4)):
    last_error = None
    for attempt, delay in enumerate(retries, start=1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except ConnectionResetError as exc:
            last_error = exc
            _log_retry(attempt, delay, request, exc)
        except SocketError as exc:
            if getattr(exc, 'errno', None) not in RETRYABLE_ERRNOS:
                raise
            last_error = exc
            _log_retry(attempt, delay, request, exc)
        time.sleep(delay)
    raise last_error


def read_json_retry(request, timeout: int, retries: tuple[int, ...] = (1, 2, 4)) -> dict:
    with urlopen_retry(request, timeout, retries) as resp:
        return json.loads(resp.read().decode('utf-8'))


def read_text_retry(request, timeout: int, encoding: str = 'utf-8', errors: str = 'strict', retries: tuple[int, ...] = (1, 2, 4)) -> str:
    with urlopen_retry(request, timeout, retries) as resp:
        return resp.read().decode(encoding, errors=errors)
