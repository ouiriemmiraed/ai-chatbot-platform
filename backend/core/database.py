from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.server_api import ServerApi
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
import os
from dotenv import load_dotenv
import logging

from core.retry_logic import MongoDBRetry

load_dotenv()

logger = logging.getLogger(__name__)

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "chatbot_db")

# Connection pool settings for performance
MAX_POOL_SIZE = int(os.getenv("MONGO_MAX_POOL_SIZE", "50"))
MIN_POOL_SIZE = int(os.getenv("MONGO_MIN_POOL_SIZE", "10"))

client = None
database = None
db = None

@MongoDBRetry.connection()
async def connect_to_mongo():
    global client, database, db
    try:
        logger.info(f"🔌 Connecting to MongoDB...")
        
        client = AsyncIOMotorClient(
            MONGODB_URL,
            server_api=ServerApi('1'),
            maxPoolSize=MAX_POOL_SIZE,
            minPoolSize=MIN_POOL_SIZE,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
        )
        database = client[DATABASE_NAME]
        # Expose legacy `db` name for modules that import `db` directly
        db = database
        
        # Test connection
        await client.admin.command('ping')
        
        # Create indexes for performance
        await _create_indexes()
        
        logger.info(f"✅ Connected to MongoDB: {DATABASE_NAME}")
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        raise
    except Exception as e:
        logger.error(f"❌ Unexpected error connecting to MongoDB: {e}")
        raise

@MongoDBRetry.operation()
async def _create_indexes():
    """Create database indexes for performance and security"""
    try:
        logger.info("📑 Creating database indexes...")
        
        # User indexes
        await database.users.create_index("email", unique=True)
        await database.users.create_index("username", unique=True)
        
        # Chatbot config indexes
        await database.chatbot_configs.create_index("user_id")
        
        # Conversation indexes
        await database.conversations.create_index("chatbot_id")
        await database.conversations.create_index("updated_at")
        await database.conversations.create_index([("chatbot_id", 1), ("updated_at", -1)])

        # Permission indexes
        await database.chatbot_permissions.create_index("user_email")
        await database.chatbot_permissions.create_index("chatbot_id")
        await database.chatbot_permissions.create_index(
            [("chatbot_id", 1), ("user_email", 1)], unique=True
        )

        # Audit log indexes
        await database.audit_logs.create_index([("chatbot_id", 1), ("timestamp", -1)])
        await database.audit_logs.create_index("performed_by")
        await database.audit_logs.create_index("action")

        # Leads indexes
        await database.leads.create_index("chatbot_id")
        await database.leads.create_index("email")
        await database.leads.create_index("status")
        await database.leads.create_index("created_at")

        # Documents indexes
        await database.documents.create_index("chatbot_id")
        await database.documents.create_index("user_id")

        logger.info("✅ Database indexes created/verified")
    except Exception as e:
        logger.warning(f"⚠️ Could not create all indexes: {e}")
        # Ne pas lever l'exception - les indexes sont optionnels

async def close_mongo_connection():
    global client, db
    if client:
        client.close()
        print("🔌 MongoDB connection closed")
    db = None

def get_database():
    return database
