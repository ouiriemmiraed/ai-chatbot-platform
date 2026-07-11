"""
Circuit Breaker Pattern pour gérer les dépendances externes (Redis, MongoDB, etc.)
Évite de surcharger un service défaillant et permet une récupération automatique
"""

import time
import logging
from enum import Enum
from typing import Callable, Any, Optional
from functools import wraps
import asyncio

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """États possibles du circuit breaker"""
    CLOSED = "closed"      # Fonctionnement normal
    OPEN = "open"          # Circuit ouvert, appels bloqués
    HALF_OPEN = "half_open"  # Test de récupération


class CircuitBreaker:
    """
    Circuit Breaker pour protéger les appels vers des services externes
    
    États:
    - CLOSED: Tout va bien, requêtes passent normalement
    - OPEN: Trop d'échecs, on bloque les requêtes pendant un temps
    - HALF_OPEN: On teste si le service est revenu
    
    Paramètres:
    - failure_threshold: Nombre d'échecs avant d'ouvrir le circuit
    - recovery_timeout: Temps d'attente avant de tester la récupération (secondes)
    - expected_exception: Type d'exception qui compte comme échec
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = CircuitState.CLOSED
        
        logger.info(
            f"Circuit Breaker '{name}' initialisé "
            f"(seuil={failure_threshold}, timeout={recovery_timeout}s)"
        )
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Exécute une fonction avec protection circuit breaker
        
        Usage:
            cb = CircuitBreaker("redis", failure_threshold=3)
            result = cb.call(redis_client.get, "key")
        """
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                logger.info(f"Circuit '{self.name}': HALF_OPEN (test de récupération)")
                self.state = CircuitState.HALF_OPEN
            else:
                time_until_retry = int(self.recovery_timeout - (time.time() - self.last_failure_time))
                logger.debug(
                    f"Circuit '{self.name}' OUVERT, "
                    f"réessai dans {time_until_retry}s"
                )
                raise CircuitBreakerError(
                    f"Circuit '{self.name}' is OPEN. "
                    f"Retry in {time_until_retry}s"
                )
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise e
    
    async def call_async(self, func: Callable, *args, **kwargs) -> Any:
        """Version asynchrone de call()"""
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                logger.info(f"Circuit '{self.name}': HALF_OPEN (test de récupération)")
                self.state = CircuitState.HALF_OPEN
            else:
                time_until_retry = int(self.recovery_timeout - (time.time() - self.last_failure_time))
                logger.debug(
                    f"Circuit '{self.name}' OUVERT, "
                    f"réessai dans {time_until_retry}s"
                )
                raise CircuitBreakerError(
                    f"Circuit '{self.name}' is OPEN. "
                    f"Retry in {time_until_retry}s"
                )
        
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise e
    
    def _should_attempt_reset(self) -> bool:
        """Vérifie si on devrait tester la récupération"""
        return (
            self.last_failure_time is not None and
            time.time() - self.last_failure_time >= self.recovery_timeout
        )
    
    def _on_success(self):
        """Callback appelé après un succès"""
        if self.state == CircuitState.HALF_OPEN:
            logger.info(f"✅ Circuit '{self.name}': Service récupéré, retour à CLOSED")
            self.state = CircuitState.CLOSED
            self.failure_count = 0
    
    def _on_failure(self):
        """Callback appelé après un échec"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN:
            logger.warning(
                f"Circuit '{self.name}': Échec en HALF_OPEN, "
                f"retour à OPEN"
            )
            self.state = CircuitState.OPEN
        elif self.failure_count >= self.failure_threshold:
            logger.error(
                f"🔴 Circuit '{self.name}': Seuil d'échecs atteint "
                f"({self.failure_count}/{self.failure_threshold}), "
                f"passage à OPEN"
            )
            self.state = CircuitState.OPEN
        else:
            logger.warning(
                f"Circuit '{self.name}': Échec {self.failure_count}/{self.failure_threshold}"
            )
    
    def reset(self):
        """Réinitialise manuellement le circuit breaker"""
        logger.info(f"Circuit '{self.name}': Réinitialisation manuelle")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
    
    def get_state(self) -> dict:
        """Récupère l'état actuel du circuit"""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "last_failure_time": self.last_failure_time,
            "time_until_retry": (
                int(self.recovery_timeout - (time.time() - self.last_failure_time))
                if self.last_failure_time and self.state == CircuitState.OPEN
                else 0
            )
        }


class CircuitBreakerError(Exception):
    """Exception levée quand le circuit est ouvert"""
    pass


# Décorateur pour faciliter l'utilisation
def circuit_breaker(name: str, failure_threshold: int = 5, recovery_timeout: int = 60):
    """
    Décorateur pour appliquer un circuit breaker à une fonction
    
    Usage:
        @circuit_breaker("redis_get", failure_threshold=3)
        def get_from_redis(key):
            return redis_client.get(key)
    """
    cb = CircuitBreaker(name, failure_threshold, recovery_timeout)
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return cb.call(func, *args, **kwargs)
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await cb.call_async(func, *args, **kwargs)
        
        # Retourner la version appropriée selon le type de fonction
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
    
    return decorator


# Instances globales pour les services communs
redis_circuit_breaker = CircuitBreaker(
    "redis",
    failure_threshold=5,
    recovery_timeout=30
)

mongodb_circuit_breaker = CircuitBreaker(
    "mongodb",
    failure_threshold=3,
    recovery_timeout=60
)

external_api_circuit_breaker = CircuitBreaker(
    "external_api",
    failure_threshold=10,
    recovery_timeout=120
)


__all__ = [
    "CircuitBreaker",
    "CircuitBreakerError",
    "CircuitState",
    "circuit_breaker",
    "redis_circuit_breaker",
    "mongodb_circuit_breaker",
    "external_api_circuit_breaker",
]
