import os
import motor.motor_asyncio
import logging
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure
from datetime import datetime
from dotenv import load_dotenv
from bson.objectid import ObjectId
from typing import List, Dict, Any

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
                if not mongo_uri:
                    raise ValueError("MONGO_URI no está definida en el archivo .env.")
                
                cls._instance.client = motor.motor_asyncio.AsyncIOMotorClient(mongo_uri)
                cls._instance.db = cls._instance.client.get_database(db_name)
                
                cls._instance.tasks = cls._instance.db.tasks
                cls._instance.user_settings = cls._instance.db.user_settings
                cls._instance.user_presets = cls._instance.db.user_presets
                cls._instance.search_sessions = cls._instance.db.search_sessions
                cls._instance.search_results = cls._instance.db.search_results
                
                logger.info("Cliente de base de datos Motor (asíncrono) inicializado.")
            except Exception as e:
                logger.critical(f"FALLO CRÍTICO AL INICIALIZAR LA DB: {e}", exc_info=True)
                raise ConnectionError(f"No se pudo inicializar el cliente de la DB: {e}")
        return cls._instance
    
    async def init_db(self):
        if self._initialized:
            return
        logger.info("Asegurando índices de la base de datos...")
        try:
            # Índices TTL para sesiones de búsqueda (expiran después de 1 hora)
            await self.search_sessions.create_index("created_at", expireAfterSeconds=3600, name="search_sessions_ttl")
            await self.search_results.create_index("created_at", expireAfterSeconds=3600, name="search_results_ttl")
            
            # Índices para acelerar consultas comunes de tareas
            await self.tasks.create_index([("user_id", ASCENDING), ("status", ASCENDING)], name="user_status_index")
            await self.tasks.create_index([("status", ASCENDING), ("created_at", ASCENDING)], name="worker_queue_index")

            logger.info("Índices de la base de datos verificados y/o creados.")
        except OperationFailure as e:
            if "Index already exists" in str(e) or "IndexOptionsConflict" in str(e):
                logger.warning(f"No se crearon los índices porque ya existen o hay un conflicto. Error: {e}")
            else:
                logger.error(f"Error inesperado al crear los índices de la DB: {e}", exc_info=True)
        finally:
            self._initialized = True

    async def add_task(self, user_id: int, file_type: str, file_name: str = None, final_filename: str = None,
                         url: str = None, file_id: str = None, processing_config: Dict = None,
                         url_info: Dict = None, status: str = "pending_processing", metadata: Dict = None,
                         custom_fields: Dict = None) -> ObjectId:
        
        # Lógica de nombre de archivo final mejorada
        if final_filename:
            final_name = final_filename
        elif file_name:
            final_name = os.path.splitext(file_name)[0]
        else:
            final_name = f"descarga_url_{datetime.utcnow().timestamp()}"
            
        task_doc = {
            "user_id": int(user_id),
            "url": url,
            "file_id": file_id,
            "original_filename": file_name,
            "final_filename": final_name,
            "file_type": file_type,
            "status": status, # ej: pending_processing, queued, processing, completed, failed
            "created_at": datetime.utcnow(),
            "processed_at": None,
            "processing_config": processing_config or {},
            "url_info": url_info or {},
            "last_error": None,
            "file_metadata": metadata or {}
        }
        if custom_fields:
            task_doc.update(custom_fields)

        result = await self.tasks.insert_one(task_doc)
        logger.info(f"Nueva tarea {result.inserted_id} añadida para el usuario {user_id} con estado '{status}'")
        return result.inserted_id

    async def get_task(self, task_id: str) -> Dict | None:
        try:
            return await self.tasks.find_one({"_id": ObjectId(task_id)})
        except Exception:
            return None

    async def get_pending_tasks(self, user_id: int, file_type_filter: str = None, status_filter: str = "pending_processing") -> List[Dict]:
        """
        Obtiene tareas de un usuario, con filtros opcionales.
        Por defecto, obtiene las tareas en el "panel" (pending_processing).
        """
        query = {"user_id": int(user_id)}
        if status_filter:
            query["status"] = status_filter
        if file_type_filter:
            query["file_type"] = file_type_filter
            
        cursor = self.tasks.find(query).sort("created_at", ASCENDING)
        return await cursor.to_list(length=100) # Límite de 100 tareas en panel

    async def update_task_config(self, task_id: str, key: str, value: Any):
        """Actualiza una clave específica dentro del diccionario 'processing_config'."""
        return await self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {f"processing_config.{key}": value}})

    async def update_task_field(self, task_id: str, field: str, value: Any):
        """Actualiza un campo de nivel superior en el documento de la tarea."""
        return await self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {field: value}})
    
    async def update_task_fields(self, task_id: str, updates: Dict):
        """Actualiza múltiples campos de nivel superior en un documento de tarea."""
        return await self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": updates})

    async def delete_task_by_id(self, task_id: str):
        return await self.tasks.delete_one({"_id": ObjectId(task_id)})

    async def delete_all_pending_tasks(self, user_id: int):
        """Elimina todas las tareas que están en el panel (pending_processing)."""
        return await self.tasks.delete_many({"user_id": user_id, "status": "pending_processing"})
    
    async def add_preset(self, user_id: int, preset_name: str, config_data: Dict):
        preset_doc = {
            "user_id": user_id,
            "preset_name": preset_name.lower().strip(),
            "config_data": config_data,
        }
        result = await self.user_presets.update_one(
            {"user_id": user_id, "preset_name": preset_name.lower().strip()},
            {"$set": preset_doc, "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True
        )
        logger.info(f"Perfil '{preset_name}' guardado para el usuario {user_id}.")
        return result

    async def get_user_presets(self, user_id: int) -> List[Dict]:
        cursor = self.user_presets.find({"user_id": user_id}).sort("preset_name", ASCENDING)
        return await cursor.to_list(length=50) # Límite de 50 perfiles por usuario

    async def get_preset_by_id(self, preset_id: str) -> Dict | None:
        try:
            return await self.user_presets.find_one({"_id": ObjectId(preset_id)})
        except Exception:
            return None

    async def delete_preset_by_id(self, preset_id: str):
        try:
            return await self.user_presets.delete_one({"_id": ObjectId(preset_id)})
        except Exception:
            return None

    async def get_user_settings(self, user_id: int) -> Dict:
        settings = await self.user_settings.find_one({"_id": user_id})
        if not settings:
            default_settings = {
                "_id": user_id,
                "created_at": datetime.utcnow(),
                "user_state": {"status": "idle", "data": {}}
            }
            await self.user_settings.insert_one(default_settings)
            logger.info(f"Nuevo perfil de usuario creado en la DB para el ID: {user_id}")
            return default_settings
        return settings

    async def set_user_state(self, user_id: int, status: str, data: Dict = None):
        state_data = {"status": status, "data": data or {}}
        return await self.user_settings.update_one(
            {"_id": user_id},
            {"$set": {"user_state": state_data}},
            upsert=True
        )

    async def get_user_state(self, user_id: int) -> Dict:
        settings = await self.get_user_settings(user_id)
        return settings.get("user_state", {"status": "idle", "data": {}})

db_instance = Database()