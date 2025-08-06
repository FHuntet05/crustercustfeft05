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
                if not mongo_uri: raise ValueError("MONGO_URI no está definida.")
                cls._instance.client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
                cls._instance.client.admin.command('ping')
                logger.info("Conexión con MongoDB Atlas establecida.")
                cls._instance.db = cls._instance.client.get_database("JefesMediaSuiteDB")
                cls._instance.tasks = cls._instance.db.tasks
                cls._instance.user_settings = cls._instance.db.user_settings
                cls._instance.search_results = cls._instance.db.search_results
                if "created_at_ttl" not in cls._instance.search_results.index_information():
                    cls._instance.search_results.create_index("created_at", expireAfterSeconds=3600)
            except Exception as e:
                logger.critical(f"FALLO CRÍTICO DB: {e}")
                raise ConnectionError("No se pudo conectar a la DB.")
        return cls._instance

    def add_task(self, user_id, file_type, file_name=None, file_size=None, url=None, processing_config=None, forwarded_chat_id=None, forwarded_message_id=None):
        task_doc = {
            "user_id": int(user_id),
            "url": url,
            "forwarded_chat_id": forwarded_chat_id,
            "forwarded_message_id": forwarded_message_id,
            "original_filename": file_name,
            "final_filename": os.path.splitext(file_name)[0] if file_name else "descarga_url",
            "file_size": file_size,
            "file_type": file_type,
            "status": "pending_processing",
            "created_at": datetime.utcnow(),
            "processed_at": None,
            "processing_config": processing_config or {},
        }
        try:
            result = self.tasks.insert_one(task_doc)
            logger.info(f"Nueva tarea {result.inserted_id} añadida para {user_id}")
            return result.inserted_id
        except Exception as e:
            logger.error(f"Error al añadir tarea a la DB: {e}")
            return None

    def get_task(self, task_id):
        try: return self.tasks.find_one({"_id": ObjectId(task_id)})
        except: return None

    def get_pending_tasks(self, user_id):
        return list(self.tasks.find({"user_id": int(user_id), "status": "pending_processing"}).sort("created_at", 1))

    def update_task_config(self, task_id, key, value):
        try: return self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {f"processing_config.{key}": value}})
        except Exception as e:
            logger.error(f"Error al actualizar config {task_id}: {e}")
            return None

    def update_task(self, task_id, field, value):
        try: return self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {field: value}})
        except Exception as e:
            logger.error(f"Error al actualizar tarea {task_id}: {e}")
            return None

# Instancia única para ser importada
db_instance = Database()