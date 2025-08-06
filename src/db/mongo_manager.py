import os
import pymongo
from pymongo.errors import ConnectionFailure
import logging
from datetime import datetime
from dotenv import load_dotenv
from bson.objectid import ObjectId

load_dotenv()
logger = logging.getLogger(__name__)

class Database:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            try:
                mongo_uri = os.getenv("MONGO_URI")
                if not mongo_uri:
                    raise ValueError("La variable de entorno MONGO_URI no está definida.")
                
                cls._instance.client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=10000)
                cls._instance.client.admin.command('ping')
                logger.info("Conexión con MongoDB Atlas establecida con éxito.")
                
                cls._instance.db = cls._instance.client.get_database("JefesMediaSuiteDB")
                cls._instance.tasks = cls._instance.db.tasks
                cls._instance.user_settings = cls._instance.db.user_settings
                cls._instance.search_results = cls._instance.db.search_results
                
                if "created_at_ttl" not in cls._instance.search_results.index_information():
                    cls._instance.search_results.create_index("created_at", expireAfterSeconds=3600, name="created_at_ttl")
                    logger.info("Índice TTL para 'search_results' creado/verificado.")

            except (ConnectionFailure, ValueError) as e:
                logger.critical(f"¡FALLO CRÍTICO AL INICIAR LA BASE DE DATOS! Error: {e}")
                raise ConnectionError("No se pudo conectar a la base de datos.")
        return cls._instance

    def add_task(self, user_id, file_type, file_id=None, file_name=None, file_size=None, url=None, special_type=None, processing_config=None, message_url=None):
        task_doc = {
            "user_id": int(user_id),
            "file_id": file_id,
            "url": url,
            "message_url": message_url,
            "original_filename": file_name,
            "final_filename": os.path.splitext(file_name)[0] if file_name else "descarga_url",
            "file_size": file_size,
            "file_type": file_type,
            "status": "pending_processing", # <-- CAMBIO CLAVE
            "special_type": special_type,
            "created_at": datetime.utcnow(),
            "processed_at": None,
            "processing_config": processing_config if processing_config else {},
        }
        try:
            result = self.tasks.insert_one(task_doc)
            logger.info(f"Nueva tarea {result.inserted_id} añadida para {user_id}")
            return result.inserted_id
        except Exception as e:
            logger.error(f"Error al añadir tarea a la DB: {e}")
            return None

    def get_task(self, task_id):
        try:
            return self.tasks.find_one({"_id": ObjectId(task_id)})
        except Exception: 
            return None

    def get_multiple_tasks(self, task_ids):
        try:
            object_ids = [ObjectId(tid) for tid in task_ids]
            return list(self.tasks.find({"_id": {"$in": object_ids}}))
        except Exception as e:
            logger.error(f"Error al obtener múltiples tareas: {e}")
            return []

    def get_pending_tasks(self, user_id):
        return list(self.tasks.find({"user_id": int(user_id), "status": "pending_processing"}).sort("created_at", 1))

    def update_task_config(self, task_id, config_key, value):
        try:
            return self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {f"processing_config.{config_key}": value}})
        except Exception as e:
            logger.error(f"Error al actualizar config de tarea {task_id}: {e}")
            return None
            
    def push_to_task_config_list(self, task_id, list_key, value):
        try:
            return self.tasks.update_one({"_id": ObjectId(task_id)}, {"$addToSet": {f"processing_config.{list_key}": value}})
        except Exception as e:
            logger.error(f"Error al añadir a lista en tarea {task_id}: {e}")
            return None

    def update_task(self, task_id, field, value):
        try:
            return self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {field: value}})
        except Exception as e:
            logger.error(f"Error al actualizar tarea {task_id}: {e}")
            return None
            
    def update_many_tasks_status(self, task_ids, new_status):
        try:
            object_ids = [ObjectId(tid) for tid in task_ids]
            result = self.tasks.update_many({"_id": {"$in": object_ids}}, {"$set": {"status": new_status}})
            return result.modified_count
        except Exception as e:
            logger.error(f"Error al actualizar estado de múltiples tareas: {e}")
            return 0

# Instancia única para ser importada en otros módulos
db_instance = Database()