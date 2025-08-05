import os
import pymongo
from pymongo.errors import ConnectionFailure
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class Database:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            # (El código __new__ se mantiene igual, no lo pego para abreviar)
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

            except ConnectionFailure as e:
                logger.critical(f"¡FALLO CRÍTICO DE CONEXIÓN A MONGODB! Error: {e}")
                cls._instance = None
            except Exception as e:
                logger.critical(f"Ocurrió un error al inicializar la base de datos: {e}")
                cls._instance = None

        return cls._instance

    def add_task(self, user_id, file_id, file_name, file_size, file_type):
        """Añade una nueva tarea a la mesa de trabajo."""
        task_doc = {
            "user_id": user_id,
            "file_id": file_id,
            "file_name": file_name,
            "file_size": file_size,
            "file_type": file_type,
            "status": "pending_review", # Estado inicial
            "created_at": datetime.utcnow()
        }
        try:
            self.tasks.insert_one(task_doc)
            logger.info(f"Nueva tarea añadida para el usuario {user_id}: {file_name}")
            return True
        except Exception as e:
            logger.error(f"No se pudo añadir la tarea a la DB: {e}")
            return False

    def get_pending_tasks(self, user_id):
        """Obtiene las tareas pendientes de un usuario."""
        try:
            # Busca todas las tareas del usuario con estado 'pending_review' y las ordena por fecha
            pending_tasks = self.tasks.find({
                "user_id": user_id,
                "status": "pending_review"
            }).sort("created_at", 1)
            return list(pending_tasks)
        except Exception as e:
            logger.error(f"No se pudieron obtener las tareas pendientes de la DB: {e}")
            return []

# Crear una instancia global para ser importada en otros módulos
db_instance = Database()