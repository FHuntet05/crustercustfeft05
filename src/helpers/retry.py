"""Helpers for retrying operations with exponential backoff."""

import asyncio
import logging
import random
from functools import wraps
from typing import Any, Callable, Optional, Type, TypeVar, Union

from pyrogram.errors import FloodWait

logger = logging.getLogger(__name__)

T = TypeVar('T')

def exponential_backoff(attempt: int, base_delay: float = 1, max_delay: float = 64) -> float:
    """Calculate delay with exponential backoff and jitter."""
    delay = min(base_delay * (2 ** attempt), max_delay)
    jitter = random.uniform(0.0, 0.1) * delay
    return delay + jitter

async def async_retry_with_backoff(
    func: Callable[..., Any],
    *args: Any,
    retry_exceptions: Union[Type[Exception], tuple[Type[Exception], ...]] = Exception,
    max_attempts: int = 5,
    base_delay: float = 1,
    max_delay: float = 64,
    **kwargs: Any
) -> Any:
    """Retry an async function with exponential backoff."""
    attempt = 0
    last_exception: Optional[Exception] = None

    while attempt < max_attempts:
        try:
            return await func(*args, **kwargs)
        except FloodWait as e:
            logger.warning(f"FloodWait detected, waiting {e.value} seconds")
            await asyncio.sleep(e.value + random.uniform(0.1, 1.0))
            continue
        except retry_exceptions as e:
            attempt += 1
            last_exception = e
            if attempt >= max_attempts:
                logger.error(f"Max retry attempts ({max_attempts}) reached")
                raise
            
            delay = exponential_backoff(attempt, base_delay, max_delay)
            logger.warning(f"Attempt {attempt} failed, retrying in {delay:.2f}s: {str(e)}")
            await asyncio.sleep(delay)

    if last_exception:
        raise last_exception
    
def retry_async(
    retry_exceptions: Union[Type[Exception], tuple[Type[Exception], ...]] = Exception,
    max_attempts: int = 5,
    base_delay: float = 1,
    max_delay: float = 64
) -> Callable:
    """Decorator for retrying async functions with exponential backoff."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await async_retry_with_backoff(
                func,
                *args,
                retry_exceptions=retry_exceptions,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                **kwargs
            )
        return wrapper
    return decorator