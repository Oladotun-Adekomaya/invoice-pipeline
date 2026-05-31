import asyncio
import functools
import time
from collections.abc import Callable
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import logging

from src.observability.logger import get_logger

logger = get_logger(__name__)


def with_retry(
    max_attempts: int = 3,
    wait_min: float = 1.0,
    wait_max: float = 30.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
) -> Callable:
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 0

            async def run() -> Any:
                nonlocal attempt
                attempt += 1
                try:
                    return await func(*args, **kwargs)
                except retry_on as e:
                    if attempt >= max_attempts:
                        logger.error(
                            "max retries reached",
                            func=func.__name__,
                            attempts=attempt,
                            error=str(e),
                        )
                        raise
                    wait = min(wait_min * (2 ** (attempt - 1)), wait_max)
                    logger.warning(
                        "retrying after error",
                        func=func.__name__,
                        attempt=attempt,
                        wait_seconds=round(wait, 2),
                        error=str(e),
                    )
                    await asyncio.sleep(wait)
                    return await run()

            return await run()

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 0

            def run() -> Any:
                nonlocal attempt
                attempt += 1
                try:
                    return func(*args, **kwargs)
                except retry_on as e:
                    if attempt >= max_attempts:
                        logger.error(
                            "max retries reached",
                            func=func.__name__,
                            attempts=attempt,
                            error=str(e),
                        )
                        raise
                    wait = min(wait_min * (2 ** (attempt - 1)), wait_max)
                    logger.warning(
                        "retrying after error",
                        func=func.__name__,
                        attempt=attempt,
                        wait_seconds=round(wait, 2),
                        error=str(e),
                    )
                    time.sleep(wait)
                    return run()

            return run()

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator