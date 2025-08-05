import os
import pymongo
from pymongo.errors import ConnectionFailure
import logging
from datetime import datetime
from dotenv import load_dotenv

# --- Cargar las variables de entorno PRIMERO ---
load_dotenv()

logger = logging.getLogger(__name__)

class Database:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            try:
                # La URI se lee después de load_dotenv()
                mongo_uri = os.getenv("MONGO_URI")
                if not mongo_uri:
                    raise ValueError("La variable de entorno MONGO_URI no está definida o el archivo .env no se encuentra.")
                
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

    # ... Las funciones add_task y get_pending_tasks se mantienen exactamente iguales ...
    def add_task(self, user_id, file_id, file_name, file_size, file_type):
        """Añade una nueva tarea a la mesa de trabajo."""
        task_doc = {
            "user_id": user_id,
            "file_id": file_id,
            "file_name": file_name,
            "file_size": file_size,
            "file_type": file_type,
            "status": "pending_review",
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
            pending_tasks = self.tasks.find({
                "user_id": user_id,
                "status": "pending_review"
            }).sort("created_at", 1)
            return list(pending_tasks)
        except Exception as e:
            logger.error(f"No se pudieron obtener las tareas pendientes de la DB: {e}")
            return []


db_instance = Database()

# Asegurarse de que el bot se detenga si la DB no se conecta
if not db_instance or not db_instance.client:
    raise ConnectionError("No se pudo conectar a la base de datos. El bot no puede continuar.")