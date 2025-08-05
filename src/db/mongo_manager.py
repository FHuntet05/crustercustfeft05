import os
import pymongo
from pymongo.errors import ConnectionFailure
import logging
from datetime import datetime
from dotenv import load_dotenv
from bson.objectid import ObjectId

# Cargar las variables de entorno para que este módulo sea autosuficiente
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
                    raise ValueError("La variable de entorno MONGO_URI no está definida o el archivo .env no se encuentra.")
                
                cls._instance.client = pymongo.MongoClient(mongo_uri)
                cls._instance.client.admin.command('ping')
                logger.info("Conexión con MongoDB Atlas establecida con éxito.")
                
                cls._instance.db = cls._instance.client.get_database("JefesMediaSuiteDB")
                cls._instance.tasks = cls._instance.db.tasks
                cls._instance.user_settings = cls._instance.db.user_settings

            except (ConnectionFailure, ValueError) as e:
                logger.critical(f"¡FALLO CRÍTICO AL INICIAR LA BASE DE DATOS! Error: {e}")
                cls._instance = None
                # Lanzamos una excepción para que el bot no inicie sin base de datos
                raise ConnectionError("No se pudo conectar a la base de datos. El bot no puede continuar.")
        return cls._instance

    def add_task(self, user_id, file_id, file_name, file_size, file_type):
        """Añade una nueva tarea a la mesa de trabajo."""
        task_doc = {
            "user_id": user_id,
            "file_id": file_id,
            "original_filename": file_name,
            "final_filename": os.path.splitext(file_name)[0], # Nombre sin extensión por defecto
            "file_size": file_size,
            "file_type": file_type,
            "status": "pending_review", # 'pending_review', 'queued', 'downloading', 'processing', 'uploading', 'done', 'error'
            "created_at": datetime.utcnow(),
            "processing_config": {} # Aquí guardaremos la configuración de la tarea (calidad, marca de agua, etc.)
        }
        try:
            result = self.tasks.insert_one(task_doc)
            logger.info(f"Nueva tarea {result.inserted_id} añadida para el usuario {user_id}: {file_name}")
            return True
        except Exception as e:
            logger.error(f"No se pudo añadir la tarea a la DB: {e}")
            return False

    def get_task(self, task_id):
        """Obtiene una tarea específica por su ID."""
        try:
            return self.tasks.find_one({"_id": ObjectId(task_id)})
        except Exception as e:
            logger.error(f"Error al obtener la tarea {task_id}: {e}")
            return None

    def get_pending_tasks(self, user_id):
        """Obtiene las tareas pendientes de revisión de un usuario."""
        try:
            return list(self.tasks.find({"user_id": user_id, "status": "pending_review"}).sort("created_at", 1))
        except Exception as e:
            logger.error(f"No se pudieron obtener las tareas pendientes: {e}")
            return []
            
    def update_task_status(self, task_id, status):
        """Actualiza el estado de una tarea."""
        try:
            self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"status": status}})
            logger.info(f"Estado de la tarea {task_id} actualizado a: {status}")
            return True
        except Exception as e:
            logger.error(f"Error al actualizar el estado de la tarea {task_id}: {e}")
            return False

    def delete_task(self, task_id, user_id):
        """Borra una tarea específica, verificando que pertenece al usuario."""
        try:
            result = self.tasks.delete_one({"_id": ObjectId(task_id), "user_id": user_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error al borrar la tarea {task_id}: {e}")
            return False
            
    def delete_all_pending(self, user_id):
        """Borra todas las tareas pendientes de un usuario."""
        try:
            result = self.tasks.delete_many({"user_id": user_id, "status": "pending_review"})
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error al borrar todas las tareas pendientes del usuario {user_id}: {e}")
            return 0

# Crear una instancia global para ser importada en otros módulos
db_instance = Database()