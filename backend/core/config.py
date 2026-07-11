"""
Validation et configuration robuste des variables d'environnement
Vérifie que toutes les variables critiques sont présentes au démarrage
"""

import os
import logging
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

class ConfigValidationError(Exception):
    """Exception levée quand la configuration est invalide"""
    pass

class Config:
    """Configuration centralisée et validée du backend"""
    
    # Variables d'environnement REQUISES
    REQUIRED_VARS = {
        "OPENROUTER_API_KEY": "API key for OpenRouter LLM service",
        "GEMINI_API_KEY": "API key for Google Gemini service",
        "MONGODB_URL": "MongoDB connection string",
    }
    
    # Variables sensibles à ne jamais logger/exposer
    SENSITIVE_VARS = {
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "MONGODB_URL",
    }
    
    # Variables d'environnement OPTIONNELLES avec valeurs par défaut
    OPTIONAL_VARS = {
        "ENV": "development",
        "DATABASE_NAME": "chatbot_db",
        "ALLOWED_ORIGINS": "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003",
        "REDIS_URL": "redis://localhost:6379/0",
        "SITE_URL": "http://localhost:3000",
        "SITE_NAME": "Chatbot Project",
        
        # MongoDB
        "MONGO_MAX_POOL_SIZE": "50",
        "MONGO_MIN_POOL_SIZE": "10",
        
        # RAG
        "EMBEDDING_MODEL": "intfloat/multilingual-e5-small",
        "CROSS_ENCODER_MODEL": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "RAG_CHUNK_SIZE": "1200",
        "RAG_CHUNK_OVERLAP": "200",
        "RAG_RETRIEVAL_MULTIPLIER": "8",
        "RAG_MIN_SCORE": "-100.0",
        "RAG_CACHE_TTL": "300",
        "RAG_BATCH_SIZE": "64",
        
        # Crawler
        "CRAWL_CONCURRENCY": "10",
        "DOCKER_NETWORK_MODE": "bridge",
        
        # Health checks
        "HEALTH_CHECK_INTERVAL": "60",  # secondes
        "DB_CONNECTION_RETRY": "3",
        "DB_CONNECTION_RETRY_DELAY": "5",
    }
    
    # Variables sensibles (ne seront pas loggées)
    SENSITIVE_VARS = {
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "SECRET_KEY",
        "CHROMA_AUTH_TOKEN",
        "SMTP_PASSWORD",
    }
    
    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._missing_required: List[str] = []
        self._warnings: List[str] = []
    
    def validate_and_load(self) -> bool:
        """
        Valide et charge toutes les variables d'environnement
        Retourne True si la config est valide, False sinon
        """
        logger.info("🔍 Validation de la configuration...")
        
        # 1. Vérifier les variables REQUISES
        for var, description in self.REQUIRED_VARS.items():
            value = os.getenv(var)
            if not value or value.strip() == "":
                self._missing_required.append(f"{var} ({description})")
                logger.error(f"❌ Variable requise manquante: {var}")
            else:
                self._config[var] = value
                if var not in self.SENSITIVE_VARS:
                    logger.info(f"✅ {var} = {value[:20]}...")
                else:
                    logger.info(f"✅ {var} = ****")
        
        # 2. Charger les variables OPTIONNELLES avec valeurs par défaut
        for var, default_value in self.OPTIONAL_VARS.items():
            value = os.getenv(var, default_value)
            self._config[var] = value
            
            if os.getenv(var) is None:
                self._warnings.append(f"{var} utilise la valeur par défaut: {default_value}")
                logger.debug(f"⚠️  {var} = {default_value} (défaut)")
        
        # 3. Validations spécifiques
        self._validate_mongodb_url()
        self._validate_redis_url()
        self._validate_numeric_configs()
        
        # 4. Résumé
        if self._missing_required:
            logger.error(f"\n{'='*60}")
            logger.error("❌ CONFIGURATION INVALIDE")
            logger.error(f"{'='*60}")
            logger.error("Variables requises manquantes:")
            for var in self._missing_required:
                logger.error(f"  • {var}")
            logger.error(f"\n💡 Créez un fichier .env avec ces variables")
            logger.error(f"   Exemple: cp .env.example .env")
            logger.error(f"{'='*60}\n")
            return False
        
        if self._warnings:
            logger.warning(f"\n⚠️  Avertissements de configuration ({len(self._warnings)}):")
            for warning in self._warnings:
                logger.warning(f"  • {warning}")
        
        logger.info(f"\n{'='*60}")
        logger.info("✅ CONFIGURATION VALIDE")
        logger.info(f"{'='*60}")
        logger.info(f"  • Environnement: {self.get('ENV')}")
        logger.info(f"  • Base de données: {self.get('DATABASE_NAME')}")
        logger.info(f"  • Mode réseau Docker: {self.get('DOCKER_NETWORK_MODE')}")
        logger.info(f"{'='*60}\n")
        
        return True
    
    def _validate_mongodb_url(self):
        """Valide le format de l'URL MongoDB"""
        url = self._config.get("MONGODB_URL", "")
        if not url.startswith(("mongodb://", "mongodb+srv://")):
            self._warnings.append("MONGODB_URL devrait commencer par mongodb:// ou mongodb+srv://")
    
    def _validate_redis_url(self):
        """Valide le format de l'URL Redis"""
        url = self._config.get("REDIS_URL", "")
        if url and not url.startswith("redis://"):
            self._warnings.append("REDIS_URL devrait commencer par redis://")
    
    def _validate_numeric_configs(self):
        """Valide que les configs numériques sont bien des nombres"""
        numeric_vars = [
            "MONGO_MAX_POOL_SIZE", "MONGO_MIN_POOL_SIZE",
            "RAG_CHUNK_SIZE", "RAG_CHUNK_OVERLAP", "RAG_RETRIEVAL_MULTIPLIER",
            "RAG_CACHE_TTL", "RAG_BATCH_SIZE", "CRAWL_CONCURRENCY",
            "HEALTH_CHECK_INTERVAL", "DB_CONNECTION_RETRY", "DB_CONNECTION_RETRY_DELAY"
        ]
        
        for var in numeric_vars:
            value = self._config.get(var)
            if value:
                try:
                    int(value)
                except ValueError:
                    self._warnings.append(f"{var} devrait être un nombre entier, reçu: {value}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Récupère une valeur de configuration"""
        return self._config.get(key, default)
    
    def get_int(self, key: str, default: int = 0) -> int:
        """Récupère une valeur entière"""
        value = self._config.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            logger.warning(f"Impossible de convertir {key}={value} en entier, utilisation de {default}")
            return default
    
    def get_float(self, key: str, default: float = 0.0) -> float:
        """Récupère une valeur flottante"""
        value = self._config.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            logger.warning(f"Impossible de convertir {key}={value} en float, utilisation de {default}")
            return default
    
    def get_bool(self, key: str, default: bool = False) -> bool:
        """Récupère une valeur booléenne"""
        value = self._config.get(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).lower() in ('true', '1', 'yes', 'on')
    
    def get_list(self, key: str, separator: str = ",", default: Optional[List[str]] = None) -> List[str]:
        """Récupère une liste de valeurs"""
        value = self._config.get(key)
        if not value:
            return default or []
        return [item.strip() for item in value.split(separator) if item.strip()]
    
    def is_production(self) -> bool:
        """Vérifie si on est en environnement de production"""
        return self.get("ENV", "").lower() in ("production", "prod")
    
    def is_development(self) -> bool:
        """Vérifie si on est en environnement de développement"""
        return self.get("ENV", "").lower() in ("development", "dev")


# Instance globale de configuration
config = Config()


def validate_config() -> bool:
    """
    Fonction helper pour valider la configuration au démarrage
    À appeler dans main.py avant de démarrer FastAPI
    """
    return config.validate_and_load()


def get_config() -> Config:
    """
    Récupère l'instance de configuration validée
    """
    return config


# Alias pour faciliter l'import
__all__ = ["config", "validate_config", "get_config", "ConfigValidationError"]
