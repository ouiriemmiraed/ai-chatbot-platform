"""
Retry logic avec exponential backoff pour les opérations MongoDB et autres services
Gère les échecs temporaires avec des tentatives intelligentes
"""

import asyncio
import functools
import logging
import time
from typing import Callable, Optional, Type, Tuple, Any
from enum import Enum

logger = logging.getLogger(__name__)


class RetryStrategy(Enum):
    """Stratégies de retry disponibles"""
    EXPONENTIAL = "exponential"  # 1s, 2s, 4s, 8s...
    LINEAR = "linear"            # 1s, 2s, 3s, 4s...
    CONSTANT = "constant"        # 1s, 1s, 1s, 1s...
    FIBONACCI = "fibonacci"      # 1s, 1s, 2s, 3s, 5s, 8s...


class RetryConfig:
    """Configuration pour le retry logic"""
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
        jitter: bool = True,
        exceptions: Tuple[Type[Exception], ...] = (Exception,)
    ):
        """
        Args:
            max_retries: Nombre maximum de tentatives (0 = pas de retry)
            base_delay: Délai de base en secondes pour la première tentative
            max_delay: Délai maximum entre deux tentatives
            strategy: Stratégie de calcul du délai
            jitter: Ajouter un jitter aléatoire pour éviter thundering herd
            exceptions: Tuple des exceptions à capturer pour retry
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.strategy = strategy
        self.jitter = jitter
        self.exceptions = exceptions
    
    def calculate_delay(self, attempt: int) -> float:
        """Calcule le délai pour une tentative donnée"""
        if self.strategy == RetryStrategy.EXPONENTIAL:
            delay = self.base_delay * (2 ** attempt)
        elif self.strategy == RetryStrategy.LINEAR:
            delay = self.base_delay * (attempt + 1)
        elif self.strategy == RetryStrategy.FIBONACCI:
            # Fibonacci: 1, 1, 2, 3, 5, 8, 13...
            fib = self._fibonacci(attempt + 1)
            delay = self.base_delay * fib
        else:  # CONSTANT
            delay = self.base_delay
        
        # Appliquer le max_delay
        delay = min(delay, self.max_delay)
        
        # Ajouter jitter (±25% aléatoire)
        if self.jitter:
            import random
            jitter_factor = 1 + (random.random() - 0.5) * 0.5  # 0.75 à 1.25
            delay *= jitter_factor
        
        return delay
    
    @staticmethod
    def _fibonacci(n: int) -> int:
        """Calcule le nième nombre de Fibonacci"""
        if n <= 2:
            return 1
        a, b = 1, 1
        for _ in range(n - 2):
            a, b = b, a + b
        return b


def retry_sync(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None
):
    """
    Décorateur pour retry synchrone avec exponential backoff
    
    Usage:
        @retry_sync(max_retries=5, base_delay=2.0)
        def risky_operation():
            # operation qui peut échouer
            pass
    """
    config = RetryConfig(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        strategy=strategy,
        exceptions=exceptions
    )
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(config.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except config.exceptions as e:
                    last_exception = e
                    
                    if attempt >= config.max_retries:
                        logger.error(
                            f"❌ {func.__name__} failed after {config.max_retries} retries: {e}"
                        )
                        raise
                    
                    delay = config.calculate_delay(attempt)
                    
                    logger.warning(
                        f"⚠️ {func.__name__} failed (attempt {attempt + 1}/{config.max_retries + 1}): {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    
                    if on_retry:
                        on_retry(e, attempt)
                    
                    time.sleep(delay)
            
            # Ne devrait jamais arriver ici, mais au cas où
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator


def retry_async(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], Any]] = None
):
    """
    Décorateur pour retry asynchrone avec exponential backoff
    
    Usage:
        @retry_async(max_retries=5, base_delay=2.0)
        async def risky_operation():
            # operation async qui peut échouer
            pass
    """
    config = RetryConfig(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        strategy=strategy,
        exceptions=exceptions
    )
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(config.max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except config.exceptions as e:
                    last_exception = e
                    
                    if attempt >= config.max_retries:
                        logger.error(
                            f"❌ {func.__name__} failed after {config.max_retries} retries: {e}"
                        )
                        raise
                    
                    delay = config.calculate_delay(attempt)
                    
                    logger.warning(
                        f"⚠️ {func.__name__} failed (attempt {attempt + 1}/{config.max_retries + 1}): {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    
                    if on_retry:
                        result = on_retry(e, attempt)
                        if asyncio.iscoroutine(result):
                            await result
                    
                    await asyncio.sleep(delay)
            
            # Ne devrait jamais arriver ici, mais au cas où
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator


# Configurations prédéfinies pour MongoDB
class MongoDBRetry:
    """Configurations de retry spécifiques à MongoDB"""
    
    @staticmethod
    def connection():
        """Retry pour connexion MongoDB (aggressive)"""
        from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
        return retry_async(
            max_retries=5,
            base_delay=2.0,
            max_delay=30.0,
            strategy=RetryStrategy.EXPONENTIAL,
            exceptions=(ConnectionFailure, ServerSelectionTimeoutError, OSError)
        )
    
    @staticmethod
    def operation():
        """Retry pour opérations MongoDB normales (moderate)"""
        from pymongo.errors import (
            AutoReconnect,
            NetworkTimeout,
            ServerSelectionTimeoutError
        )
        return retry_async(
            max_retries=3,
            base_delay=1.0,
            max_delay=10.0,
            strategy=RetryStrategy.EXPONENTIAL,
            exceptions=(AutoReconnect, NetworkTimeout, ServerSelectionTimeoutError)
        )
    
    @staticmethod
    def write():
        """Retry pour écritures MongoDB (conservative)"""
        from pymongo.errors import WTimeoutError, WriteConcernError
        return retry_async(
            max_retries=2,
            base_delay=0.5,
            max_delay=5.0,
            strategy=RetryStrategy.LINEAR,
            exceptions=(WTimeoutError, WriteConcernError)
        )


# Configurations prédéfinies pour Redis
class RedisRetry:
    """Configurations de retry spécifiques à Redis"""
    
    @staticmethod
    def connection():
        """Retry pour connexion Redis"""
        import redis.exceptions as redis_exc
        return retry_async(
            max_retries=3,
            base_delay=1.0,
            max_delay=15.0,
            strategy=RetryStrategy.EXPONENTIAL,
            exceptions=(
                redis_exc.ConnectionError,
                redis_exc.TimeoutError,
                OSError
            )
        )
    
    @staticmethod
    def operation():
        """Retry pour opérations Redis"""
        import redis.exceptions as redis_exc
        return retry_async(
            max_retries=2,
            base_delay=0.5,
            max_delay=5.0,
            strategy=RetryStrategy.LINEAR,
            exceptions=(
                redis_exc.ConnectionError,
                redis_exc.TimeoutError,
                redis_exc.ResponseError
            )
        )


# Configurations prédéfinies pour HTTP/API externe
class HTTPRetry:
    """Configurations de retry spécifiques aux requêtes HTTP"""
    
    @staticmethod
    def api_call():
        """Retry pour appels API externes"""
        return retry_async(
            max_retries=3,
            base_delay=2.0,
            max_delay=20.0,
            strategy=RetryStrategy.EXPONENTIAL,
            exceptions=(
                TimeoutError,
                ConnectionError,
                OSError
            )
        )
    
    @staticmethod
    def idempotent():
        """Retry pour requêtes idempotentes (GET, HEAD, OPTIONS)"""
        return retry_async(
            max_retries=5,
            base_delay=1.0,
            max_delay=30.0,
            strategy=RetryStrategy.EXPONENTIAL,
            exceptions=(TimeoutError, ConnectionError, OSError)
        )


# Helper pour tester le retry logic
async def test_retry_logic():
    """Teste le retry logic avec une fonction qui échoue N fois"""
    
    @retry_async(max_retries=3, base_delay=0.5, strategy=RetryStrategy.EXPONENTIAL)
    async def flaky_function(fail_count: int):
        nonlocal attempts
        attempts += 1
        logger.info(f"Attempt {attempts}")
        
        if attempts <= fail_count:
            raise ValueError(f"Simulated failure {attempts}")
        
        return "Success!"
    
    attempts = 0
    
    try:
        result = await flaky_function(2)
        logger.info(f"✅ Result: {result}")
    except ValueError as e:
        logger.error(f"❌ Failed: {e}")


if __name__ == "__main__":
    # Test du retry logic
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    asyncio.run(test_retry_logic())
