"""
Système de Health Checks périodiques pour surveiller l'état du backend
Vérifie MongoDB, Redis, RAG models, etc.
"""

import asyncio
import time
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """États de santé possibles"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class ComponentHealth:
    """Santé d'un composant individuel"""
    
    def __init__(self, name: str):
        self.name = name
        self.status = HealthStatus.UNKNOWN
        self.last_check: Optional[float] = None
        self.last_success: Optional[float] = None
        self.last_failure: Optional[float] = None
        self.consecutive_failures = 0
        self.total_checks = 0
        self.total_failures = 0
        self.response_time_ms: Optional[float] = None
        self.error_message: Optional[str] = None
    
    def update_success(self, response_time_ms: float):
        """Met à jour après un check réussi"""
        self.status = HealthStatus.HEALTHY
        self.last_check = time.time()
        self.last_success = time.time()
        self.response_time_ms = response_time_ms
        self.consecutive_failures = 0
        self.total_checks += 1
        self.error_message = None
    
    def update_failure(self, error: str):
        """Met à jour après un check échoué"""
        self.last_check = time.time()
        self.last_failure = time.time()
        self.consecutive_failures += 1
        self.total_checks += 1
        self.total_failures += 1
        self.error_message = error
        
        # Déterminer le statut selon le nombre d'échecs consécutifs
        if self.consecutive_failures >= 3:
            self.status = HealthStatus.UNHEALTHY
        elif self.consecutive_failures >= 1:
            self.status = HealthStatus.DEGRADED
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            "name": self.name,
            "status": self.status.value,
            "last_check": datetime.fromtimestamp(self.last_check).isoformat() if self.last_check else None,
            "last_success": datetime.fromtimestamp(self.last_success).isoformat() if self.last_success else None,
            "consecutive_failures": self.consecutive_failures,
            "total_checks": self.total_checks,
            "total_failures": self.total_failures,
            "success_rate": round((self.total_checks - self.total_failures) / self.total_checks * 100, 2) if self.total_checks > 0 else 0,
            "response_time_ms": round(self.response_time_ms, 2) if self.response_time_ms else None,
            "error": self.error_message
        }


class HealthCheckSystem:
    """Système centralisé de health checks"""
    
    def __init__(self):
        self.components: Dict[str, ComponentHealth] = {
            "mongodb": ComponentHealth("mongodb"),
            "redis": ComponentHealth("redis"),
            "rag_models": ComponentHealth("rag_models"),
            "celery": ComponentHealth("celery"),
            "disk_space": ComponentHealth("disk_space"),
        }
        self._check_interval = 60  # secondes
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    async def check_mongodb(self) -> tuple[bool, float, Optional[str]]:
        """Vérifie la santé de MongoDB"""
        try:
            from core.database import get_database
            
            start = time.time()
            db = get_database()
            
            # Ping MongoDB
            await db.command('ping')
            
            # Vérifier qu'on peut lire une collection
            await db.list_collection_names()
            
            response_time = (time.time() - start) * 1000
            return True, response_time, None
            
        except Exception as e:
            return False, 0, str(e)
    
    def check_redis(self) -> tuple[bool, float, Optional[str]]:
        """Vérifie la santé de Redis"""
        try:
            import redis
            import os
            
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            r = redis.from_url(redis_url, socket_timeout=5)
            
            start = time.time()
            r.ping()
            response_time = (time.time() - start) * 1000
            
            # Vérifier la mémoire
            info = r.info("memory")
            used_memory = info.get("used_memory", 0)
            max_memory = info.get("maxmemory", 0)
            
            if max_memory > 0 and used_memory / max_memory > 0.9:
                return True, response_time, "Warning: Redis memory usage > 90%"
            
            return True, response_time, None
            
        except Exception as e:
            return False, 0, str(e)
    
    def check_rag_models(self) -> tuple[bool, float, Optional[str]]:
        """Vérifie que les modèles RAG sont chargés"""
        try:
            from services.rag_service import rag_service
            
            start = time.time()
            
            # Vérifier que le modèle d'embedding est chargé
            if rag_service._embedding_model is None:
                return False, 0, "RAG embedding model not loaded"
            
            # Tester une embedding simple
            test_embedding = rag_service._get_embeddings(["test"])
            if not test_embedding or len(test_embedding) == 0:
                return False, 0, "RAG embedding generation failed"
            
            response_time = (time.time() - start) * 1000
            return True, response_time, None
            
        except Exception as e:
            return False, 0, str(e)
    
    def check_celery(self) -> tuple[bool, float, Optional[str]]:
        """Vérifie que Celery worker est actif"""
        try:
            from celery import Celery
            import os
            
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            celery_app = Celery('tasks', broker=redis_url, backend=redis_url)
            
            start = time.time()
            
            # Inspecter les workers actifs
            inspect = celery_app.control.inspect(timeout=5)
            active_workers = inspect.active()
            
            response_time = (time.time() - start) * 1000
            
            if not active_workers:
                return False, response_time, "No active Celery workers found"
            
            return True, response_time, None
            
        except Exception as e:
            return False, 0, str(e)
    
    def check_disk_space(self) -> tuple[bool, float, Optional[str]]:
        """Vérifie l'espace disque disponible"""
        try:
            import shutil
            
            start = time.time()
            
            # Vérifier l'espace disque sur le dossier de travail
            total, used, free = shutil.disk_usage(".")
            
            free_percent = free / total * 100
            
            response_time = (time.time() - start) * 1000
            
            if free_percent < 10:
                return False, response_time, f"Low disk space: {free_percent:.1f}% free"
            elif free_percent < 20:
                return True, response_time, f"Warning: {free_percent:.1f}% disk space free"
            
            return True, response_time, None
            
        except Exception as e:
            return False, 0, str(e)
    
    async def run_all_checks(self):
        """Exécute tous les checks"""
        logger.debug("🔍 Exécution des health checks...")
        
        # MongoDB (async)
        success, response_time, error = await self.check_mongodb()
        if success:
            self.components["mongodb"].update_success(response_time)
        else:
            self.components["mongodb"].update_failure(error)
        
        # Redis (sync)
        success, response_time, error = self.check_redis()
        if success:
            self.components["redis"].update_success(response_time)
            if error:  # Warning
                logger.warning(f"Redis: {error}")
        else:
            self.components["redis"].update_failure(error)
        
        # RAG Models (sync)
        success, response_time, error = self.check_rag_models()
        if success:
            self.components["rag_models"].update_success(response_time)
        else:
            self.components["rag_models"].update_failure(error)
        
        # Celery (sync)
        success, response_time, error = self.check_celery()
        if success:
            self.components["celery"].update_success(response_time)
        else:
            self.components["celery"].update_failure(error)
        
        # Disk Space (sync)
        success, response_time, error = self.check_disk_space()
        if success:
            self.components["disk_space"].update_success(response_time)
            if error:  # Warning
                logger.warning(f"Disk: {error}")
        else:
            self.components["disk_space"].update_failure(error)
        
        # Logger les composants en mauvaise santé
        unhealthy = [
            comp for comp in self.components.values()
            if comp.status in (HealthStatus.UNHEALTHY, HealthStatus.DEGRADED)
        ]
        
        if unhealthy:
            logger.warning(f"⚠️  Composants en mauvaise santé: {[c.name for c in unhealthy]}")
    
    async def _periodic_check_loop(self):
        """Boucle de vérification périodique"""
        logger.info(f"🏥 Health check system démarré (intervalle: {self._check_interval}s)")
        
        while self._running:
            try:
                await self.run_all_checks()
            except Exception as e:
                logger.error(f"Erreur dans health check loop: {e}")
            
            await asyncio.sleep(self._check_interval)
    
    async def start(self, interval: int = 60):
        """Démarre les health checks périodiques"""
        if self._running:
            logger.warning("Health check system déjà démarré")
            return
        
        self._check_interval = interval
        self._running = True
        self._task = asyncio.create_task(self._periodic_check_loop())
        logger.info(f"✅ Health check system démarré")
    
    async def stop(self):
        """Arrête les health checks périodiques"""
        if not self._running:
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 Health check system arrêté")
    
    def get_overall_status(self) -> HealthStatus:
        """Récupère le statut global du système"""
        statuses = [comp.status for comp in self.components.values()]
        
        if any(s == HealthStatus.UNHEALTHY for s in statuses):
            return HealthStatus.UNHEALTHY
        elif any(s == HealthStatus.DEGRADED for s in statuses):
            return HealthStatus.DEGRADED
        elif all(s == HealthStatus.HEALTHY for s in statuses):
            return HealthStatus.HEALTHY
        else:
            return HealthStatus.UNKNOWN
    
    def get_health_report(self) -> Dict[str, Any]:
        """Génère un rapport de santé complet"""
        overall_status = self.get_overall_status()
        
        return {
            "status": overall_status.value,
            "timestamp": datetime.now().isoformat(),
            "components": {
                name: comp.to_dict()
                for name, comp in self.components.items()
            },
            "summary": {
                "total_components": len(self.components),
                "healthy": sum(1 for c in self.components.values() if c.status == HealthStatus.HEALTHY),
                "degraded": sum(1 for c in self.components.values() if c.status == HealthStatus.DEGRADED),
                "unhealthy": sum(1 for c in self.components.values() if c.status == HealthStatus.UNHEALTHY),
            }
        }


# Instance globale
health_check_system = HealthCheckSystem()


__all__ = ["health_check_system", "HealthStatus", "HealthCheckSystem"]
