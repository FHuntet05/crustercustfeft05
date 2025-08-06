# src/db/mongo_manager.py

import os
import motor.motor_asyncio
import logging
from pymongo.errors import OperationFailure
from datetime import datetime
from dotenv import load_dotenv
from bson.objectid import ObjectId

load_dotenv()
logger = logging.getLogger(__name__)

class Database:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            try:
                mongo_uri = os.getenv("MONGO_URI")
                if not mongo_uri: raise ValueError("MONGO_URI no está definida.")
                cls._instance.client = motor.motor_asyncio.AsyncIOMotorClient(mongo_uri)
                cls._instance.db = cls._instance.client.get_database("JefesMediaSuiteDB")
                cls._instance.tasks = cls._instance.db.tasks
                cls._instance.user_settings = cls._instance.db.user_settings
                cls._instance.search_sessions = cls._instance.db.search_sessions
                cls._instance.search_results = cls._instance.db.search_results
                logger.info("Cliente de base de datos Motor (asíncrono) inicializado.")
            except Exception as e:
                logger.critical(f"FALLO CRÍTICO DB: {e}")
                raise ConnectionError(f"No se pudo inicializar el cliente de la DB: {e}")
        return cls._instance
    
    async def init_db(self):
        if self._initialized:
            return
        logger.info("Asegurando índices de la base de datos...")
        try:
            await self.search_sessions.create_index("created_at", expireAfterSeconds=3600, name="search_sessions_ttl")
            await self.search_results.create_index("created_at", expireAfterSeconds=3600, name="search_results_ttl")
            logger.info("Índices TTL de búsqueda verificados y listos.")
        except OperationFailure as e:
            if "Index already exists" in str(e) or "IndexOptionsConflict" in str(e):
                logger.warning(f"No se crearon los índices porque ya existen o hay un conflicto. El bot continuará. Error: {e}")
            else:
                logger.error(f"Error inesperado al crear los índices de la DB: {e}")
        finally:
            self._initialized = True

    async def add_task(self, user_id, file_type, file_name=None, file_size=None, url=None, file_id=None, message_id=None, processing_config=None, url_info=None):
        task_doc = {
            "user_id": int(user_id), "url": url, "file_id": file_id, "message_id": message_id,
            "original_filename": file_name,
            "final_filename": os.path.splitext(file_name)[0] if file_name else "descarga_url",
            "file_size": file_size, "file_type": file_type, "status": "pending_processing",
            "created_at": datetime.utcnow(), "processed_at": None,
            "processing_config": processing_config or {},
            "url_info": url_info or {}, "last_error": None,
        }
        try:
            result = await self.tasks.insert_one(task_doc)
            logger.info(f"Nueva tarea {result.inserted_id} añadida para {user_id}")
            return result.inserted_id
        except Exception as e:
            logger.error(f"Error al añadir tarea a la DB: {e}")
            return None

    async def get_task(self, task_id):
        try: return await self.tasks.find_one({"_id": ObjectId(task_id)})
        except: return None

    async def get_pending_tasks(self, user_id):
        cursor = self.tasks.find({"user_id": int(user_id), "status": "pending_processing"}).sort("created_at", 1)
        return await cursor.to_list(length=100)

    async def update_task_config(self, task_id, key, value):
        try: return await self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {f"processing_config.{key}": value}})
        except Exception as e: logger.error(f"Error al actualizar config {task_id}: {e}"); return None

    async def update_task(self, task_id, field, value):
        try: return await self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {field: value}})
        except Exception as e: logger.error(f"Error al actualizar tarea {task_id}: {e}"); return None
    
    async def get_user_settings(self, user_id):
        if not await self.user_settings.find_one({"_id": user_id}):
            await self.user_settings.insert_one({"_id": user_id, "created_at": datetime.utcnow()})
        return

db_instance = Database()