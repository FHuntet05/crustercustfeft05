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
                
                cls._instance.client = pymongo.MongoClient(mongo_uri)
                cls._instance.client.admin.command('ping')
                logger.info("Conexión con MongoDB Atlas establecida con éxito.")
                
                cls._instance.db = cls._instance.client.get_database("JefesMediaSuiteDB")
                cls._instance.tasks = cls._instance.db.tasks
                cls._instance.user_settings = cls._instance.db.user_settings

            except (ConnectionFailure, ValueError) as e:
                logger.critical(f"¡FALLO CRÍTICO AL INICIAR LA BASE DE DATOS! Error: {e}")
                raise ConnectionError("No se pudo conectar a la base de datos.")
        return cls._instance

    # --- Métodos de Tareas ---
    def add_task(self, user_id, file_type, file_id=None, file_name=None, file_size=None, url=None):
        task_doc = {
            "user_id": user_id, "file_id": file_id, "url": url,
            "original_filename": file_name,
            "final_filename": os.path.splitext(file_name)[0] if file_name else "descarga_url",
            "file_size": file_size, "file_type": file_type,
            "status": "pending_review",
            "created_at": datetime.utcnow(),
            "processing_config": {}
        }
        try:
            result = self.tasks.insert_one(task_doc)
            logger.info(f"Nueva tarea {result.inserted_id} añadida para {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error al añadir tarea a la DB: {e}")
            return False

    def get_task(self, task_id):
        try:
            return self.tasks.find_one({"_id": ObjectId(task_id)})
        except Exception: return None

    def get_pending_tasks(self, user_id):
        return list(self.tasks.find({"user_id": user_id, "status": "pending_review"}).sort("created_at", 1))

    def update_task_config(self, task_id, config_key, value):
        return self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {f"processing_config.{config_key}": value}})

    def update_task(self, task_id, field, value):
        return self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {field: value}})

    def delete_task(self, task_id, user_id):
        result = self.tasks.delete_one({"_id": ObjectId(task_id), "user_id": user_id})
        return result.deleted_count > 0
            
    def delete_all_pending(self, user_id):
        result = self.tasks.delete_many({"user_id": user_id, "status": "pending_review"})
        return result.deleted_count

    # --- Métodos de Ajustes de Usuario ---
    def get_user_settings(self, user_id):
        settings = self.user_settings.find_one({"user_id": user_id})
        if not settings:
            default_settings = {
                "user_id": user_id,
                "naming": {"prefix": "", "suffix": ""},
                "watermark": {"type": "text", "content": f"@{user_id}", "position": "bottom-right", "opacity": 0.7, "enabled": False},
                "thumbnail": {"file_id": None},
                "auto_profile": {}
            }
            self.user_settings.insert_one(default_settings)
            return default_settings
        return settings

    def update_user_setting(self, user_id, key, value):
        self.user_settings.update_one({"user_id": user_id}, {"$set": {key: value}}, upsert=True)

db_instance = Database()