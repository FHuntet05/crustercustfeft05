# --- START OF FILE src/db/mongo_manager.py ---

import os
import motor.motor_asyncio
import logging
from pymongo import ASCENDING
from pymongo.errors import OperationFailure
from datetime import datetime
from dotenv import load_dotenv
from bson.objectid import ObjectId
from typing import List, Dict, Any, Optional

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
                
                # Colecciones
                cls._instance.tasks = cls._instance.db.tasks
                cls._instance.user_settings = cls._instance.db.user_settings
                cls._instance.user_presets = cls._instance.db.user_presets
                cls._instance.search_sessions = cls._instance.db.search_sessions
                cls._instance.search_results = cls._instance.db.search_results
                cls._instance.monitored_channels = cls._instance.db.monitored_channels
                
                logger.info("Cliente de base de datos Motor (asíncrono) inicializado.")
            except Exception as e:
                logger.critical(f"FALLO CRÍTICO AL INICIALIZAR LA DB: {e}", exc_info=True)
                raise ConnectionError(f"No se pudo inicializar el cliente de la DB: {e}")
        return cls._instance
    
    async def init_db(self):
        """Asegura que los índices necesarios para el rendimiento existan en la DB."""
        if self._initialized:
            return
        logger.info("Asegurando índices de la base de datos...")
        try:
            # Índices TTL para limpiar documentos de búsqueda expirados automáticamente.
            await self.search_sessions.create_index("created_at", expireAfterSeconds=3600, name="search_sessions_ttl")
            await self.search_results.create_index("created_at", expireAfterSeconds=3600, name="search_results_ttl")
            
            # Índices para consultas comunes.
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

    async def add_task(self, user_id: int, file_type: str, file_name: Optional[str] = None, 
                         final_filename: Optional[str] = None, url: Optional[str] = None,
                         file_id: Optional[str] = None, processing_config: Optional[Dict] = None,
                         status: str = "pending_processing", metadata: Optional[Dict] = None,
                         custom_fields: Optional[Dict] = None) -> ObjectId:
        
        # Lógica mejorada para determinar el nombre de archivo final
        if final_filename:
            final_name = final_filename
        elif file_name:
            final_name = os.path.splitext(file_name)[0]
        else:
            final_name = f"tarea_{int(datetime.utcnow().timestamp())}"
            
        task_doc = {
            "user_id": int(user_id),
            "url": url,
            "file_id": file_id,
            "original_filename": file_name,
            "final_filename": final_name,
            "file_type": file_type,
            "status": status,
            "created_at": datetime.utcnow(),
            "processed_at": None,
            "processing_config": processing_config or {},
            "last_error": None,
            "file_metadata": metadata or {}
        }
        if custom_fields:
            task_doc.update(custom_fields)

        result = await self.tasks.insert_one(task_doc)
        logger.info(f"Nueva tarea {result.inserted_id} añadida para el usuario {user_id} con estado '{status}'")
        return result.inserted_id

    async def get_task(self, task_id: str) -> Optional[Dict]:
        try:
            return await self.tasks.find_one({"_id": ObjectId(task_id)})
        except Exception:
            logger.warning(f"Intento de búsqueda con un ID de tarea inválido: {task_id}")
            return None

    async def get_pending_tasks(self, user_id: int, file_type_filter: Optional[str] = None, 
                                  status_filter: str = "pending_processing") -> List[Dict]:
        query = {"user_id": int(user_id), "status": status_filter}
        if file_type_filter:
            query["file_type"] = file_type_filter
            
        cursor = self.tasks.find(query).sort("created_at", ASCENDING)
        return await cursor.to_list(length=100) # Límite razonable para el panel

    async def update_task(self, task_id: str, field: str, value: Any):
        """Alias para update_task_field por compatibilidad con el worker."""
        return await self.update_task_field(task_id, field, value)

    async def update_task_field(self, task_id: str, field: str, value: Any):
        """Actualiza un campo de nivel superior en el documento de la tarea."""
        return await self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {field: value}})

    async def update_task_config(self, task_id: str, key: str, value: Any):
        """Actualiza una clave específica dentro del diccionario 'processing_config'."""
        return await self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {f"processing_config.{key}": value}})
    
    async def delete_task_by_id(self, task_id: str):
        return await self.tasks.delete_one({"_id": ObjectId(task_id)})

    async def delete_all_pending_tasks(self, user_id: int):
        return await self.tasks.delete_many({"user_id": user_id, "status": "pending_processing"})
    
    # --- Métodos para Perfiles (Presets) ---

    async def add_preset(self, user_id: int, preset_name: str, config_data: Dict):
        preset_doc = {
            "user_id": user_id,
            "preset_name": preset_name.lower().strip(),
            "config_data": config_data,
        }
        await self.user_presets.update_one(
            {"user_id": user_id, "preset_name": preset_name.lower().strip()},
            {"$set": preset_doc, "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True
        )
        logger.info(f"Perfil '{preset_name}' guardado para el usuario {user_id}.")

    async def get_user_presets(self, user_id: int) -> List[Dict]:
        cursor = self.user_presets.find({"user_id": user_id}).sort("preset_name", ASCENDING)
        return await cursor.to_list(length=50) # Límite de 50 perfiles por usuario

    async def get_preset_by_id(self, preset_id: str) -> Optional[Dict]:
        try:
            return await self.user_presets.find_one({"_id": ObjectId(preset_id)})
        except Exception:
            return None

    async def delete_preset_by_id(self, preset_id: str):
        try:
            return await self.user_presets.delete_one({"_id": ObjectId(preset_id)})
        except Exception:
            return None

    # --- Métodos para Ajustes y Estado del Usuario ---

    async def get_user_settings(self, user_id: int) -> Dict:
        settings = await self.user_settings.find_one({"_id": user_id})
        if not settings:
            default_settings = {
                "_id": user_id,
                "created_at": datetime.utcnow(),
                "user_state": {"status": "idle", "data": {}},
                "restricted_channels": {},  # Almacena info de canales restringidos
                "last_used_userbot": datetime.utcnow()  # Para control de rate limit
            }
            await self.user_settings.insert_one(default_settings)
            logger.info(f"Nuevo perfil de usuario creado en la DB para el ID: {user_id}")
            return default_settings
        return settings

    async def add_restricted_channel(self, user_id: int, channel_id: int, channel_title: str) -> bool:
        """Registra un canal restringido para un usuario."""
        try:
            await self.user_settings.update_one(
                {"_id": user_id},
                {"$set": {
                    f"restricted_channels.{channel_id}": {
                        "title": channel_title,
                        "added_at": datetime.utcnow()
                    }
                }},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Error al añadir canal restringido: {e}")
            return False

    async def get_restricted_channels(self, user_id: int) -> Dict:
        """Obtiene la lista de canales restringidos de un usuario."""
        settings = await self.get_user_settings(user_id)
        return settings.get("restricted_channels", {})

    async def set_user_state(self, user_id: int, status: str, data: Optional[Dict] = None):
        state_data = {"status": status, "data": data or {}}
        return await self.user_settings.update_one(
            {"_id": user_id},
            {"$set": {"user_state": state_data}},
            upsert=True
        )

    async def get_user_state(self, user_id: int) -> Dict:
        settings = await self.get_user_settings(user_id)
        return settings.get("user_state", {"status": "idle", "data": {}})

    async def register_user(self, user_id: int) -> bool:
        """Registra un nuevo usuario en la base de datos"""
        try:
            # Verificar si el usuario ya existe
            existing_user = await self.user_settings.find_one({"_id": user_id})
            if existing_user:
                logger.info(f"Usuario {user_id} ya existe en la base de datos")
                return True
            
            # Crear nuevo usuario
            user_doc = {
                "_id": user_id,
                "created_at": datetime.utcnow(),
                "user_state": {"status": "idle", "data": {}},
                "restricted_channels": {},
                "last_used_userbot": datetime.utcnow()
            }
            
            await self.user_settings.insert_one(user_doc)
            logger.info(f"Usuario {user_id} registrado exitosamente")
            return True
            
        except Exception as e:
            logger.error(f"Error registrando usuario {user_id}: {e}")
            return False

    async def create_task(self, task_data: dict) -> str:
        """Crea una nueva tarea de procesamiento"""
        try:
            # Agregar timestamp si no existe
            if 'created_at' not in task_data:
                task_data['created_at'] = datetime.utcnow()
            
            # Insertar en la colección de tareas
            result = await self.tasks.insert_one(task_data)
            
            if result.inserted_id:
                logger.info(f"Tarea creada exitosamente: {result.inserted_id}")
                return str(result.inserted_id)
            else:
                logger.error("No se pudo crear la tarea")
                return None
                
        except Exception as e:
            logger.error(f"Error creando tarea: {e}")
            return None

    # --- Métodos para Canales Monitoreados ---
    
    async def add_monitored_channel(self, channel_id: int, user_id: int) -> bool:
        """Añade un canal a la lista de monitoreo"""
        try:
            await self.monitored_channels.insert_one({
                "channel_id": channel_id,
                "user_id": user_id,
                "added_on": datetime.utcnow(),
                "last_message_id": 0,
                "active": True
            })
            return True
        except Exception as e:
            logger.error(f"Error al añadir canal monitoreado: {e}")
            return False

    async def is_channel_monitored(self, channel_id: int, user_id: int) -> bool:
        """Verifica si un canal ya está siendo monitoreado"""
        count = await self.monitored_channels.count_documents({
            "channel_id": channel_id,
            "user_id": user_id,
            "active": True
        })
        return count > 0

    async def get_monitored_channels(self, user_id: int) -> List[Dict]:
        """Obtiene la lista de canales monitoreados de un usuario"""
        cursor = self.monitored_channels.find({
            "user_id": user_id,
            "active": True
        })
        return await cursor.to_list(length=100)

    async def remove_monitored_channel(self, channel_id: int, user_id: int) -> bool:
        """Elimina un canal de la lista de monitoreo"""
        try:
            result = await self.monitored_channels.update_one(
                {"channel_id": channel_id, "user_id": user_id},
                {"$set": {"active": False}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error al eliminar canal monitoreado: {e}")
            return False

    async def update_last_message_id(self, channel_id: int, message_id: int):
        """Actualiza el último ID de mensaje procesado para un canal"""
        await self.monitored_channels.update_one(
            {"channel_id": channel_id},
            {"$set": {"last_message_id": message_id}}
        )

db_instance = Database()