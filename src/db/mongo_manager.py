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
                db_name = os.getenv("MONGO_DB_NAME", "JefesMediaSuiteDB")
                if not mongo_uri: raise ValueError("MONGO_URI no está definida en el .env.")
                
                cls._instance.client = motor.motor_asyncio.AsyncIOMotorClient(mongo_uri)
                cls._instance.db = cls._instance.client.get_database(db_name)
                
                # Colecciones principales
                cls._instance.tasks = cls._instance.db.tasks
                cls._instance.user_settings = cls._instance.db.user_settings
                
                # Colecciones para búsquedas temporales
                cls._instance.search_sessions = cls._instance.db.search_sessions
                cls._instance.search_results = cls._instance.db.search_results
                
                logger.info("Cliente de base de datos Motor (asíncrono) inicializado.")
            except Exception as e:
                logger.critical(f"FALLO CRÍTICO AL INICIALIZAR LA DB: {e}", exc_info=True)
                raise ConnectionError(f"No se pudo inicializar el cliente de la DB: {e}")
        return cls._instance
    
    async def init_db(self):
        """Asegura la creación de índices TTL para la autolimpieza de datos temporales."""
        if self._initialized:
            return
        logger.info("Asegurando índices de la base de datos...")
        try:
            # Sesiones y resultados de búsqueda expiran después de 1 hora (3600s)
            await self.search_sessions.create_index("created_at", expireAfterSeconds=3600, name="search_sessions_ttl")
            await self.search_results.create_index("created_at", expireAfterSeconds=3600, name="search_results_ttl")
            logger.info("Índices TTL de búsqueda verificados y/o creados.")
        except OperationFailure as e:
            # Es normal que esto falle si los índices ya existen. Lo manejamos sin error.
            if "Index already exists" in str(e) or "IndexOptionsConflict" in str(e):
                logger.warning(f"No se crearon los índices porque ya existen o hay un conflicto. El bot continuará. Error: {e}")
            else:
                logger.error(f"Error inesperado al crear los índices de la DB: {e}", exc_info=True)
        finally:
            self._initialized = True

    async def add_task(self, user_id, file_type, file_name=None, file_size=None, url=None, file_id=None, 
                         processing_config=None, url_info=None, status="pending_processing"):
        """Añade una nueva tarea a la base de datos."""
        task_doc = {
            "user_id": int(user_id),
            "url": url,
            "file_id": file_id,
            "original_filename": file_name,
            "final_filename": os.path.splitext(file_name)[0] if file_name else "descarga_url",
            "file_size": file_size,
            "file_type": file_type,
            "status": status, # Estado puede ser 'pending_processing' o 'queued'
            "created_at": datetime.utcnow(),
            "processed_at": None,
            "processing_config": processing_config or {},
            "url_info": url_info or {},
            "last_error": None,
        }
        try:
            result = await self.tasks.insert_one(task_doc)
            logger.info(f"Nueva tarea {result.inserted_id} añadida para el usuario {user_id} con estado '{status}'")
            return result.inserted_id
        except Exception as e:
            logger.error(f"Error al añadir tarea a la DB: {e}")
            return None

    async def get_task(self, task_id: str):
        """Obtiene una tarea por su ID."""
        try:
            return await self.tasks.find_one({"_id": ObjectId(task_id)})
        except Exception:
            return None

    async def get_pending_tasks(self, user_id: int):
        """Obtiene las tareas pendientes de acción por parte de un usuario."""
        cursor = self.tasks.find({"user_id": int(user_id), "status": "pending_processing"}).sort("created_at", 1)
        return await cursor.to_list(length=100)

    async def update_task_config(self, task_id: str, key: str, value):
        """Actualiza un campo dentro del diccionario 'processing_config' de una tarea."""
        try:
            return await self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {f"processing_config.{key}": value}})
        except Exception as e:
            logger.error(f"Error al actualizar config de la tarea {task_id}: {e}")
            return None

    async def update_task(self, task_id: str, field: str, value):
        """Actualiza un campo de nivel superior de una tarea."""
        try:
            return await self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {field: value}})
        except Exception as e:
            logger.error(f"Error al actualizar la tarea {task_id}: {e}")
            return None
    
    async def get_user_settings(self, user_id: int):
        """Obtiene la configuración de un usuario, creándola si no existe."""
        settings = await self.user_settings.find_one({"_id": user_id})
        if not settings:
            await self.user_settings.insert_one({"_id": user_id, "created_at": datetime.utcnow()})
            logger.info(f"Nuevo perfil de usuario creado en la DB para el ID: {user_id}")
        return settings or {}

db_instance = Database()