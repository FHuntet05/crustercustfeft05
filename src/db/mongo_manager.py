import os
import pymongo
from pymongo.errors import ConnectionFailure
import logging

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
                # La siguiente línea fuerza una conexión para verificar que funciona.
                cls._instance.client.admin.command('ping')
                logger.info("Conexión con MongoDB Atlas establecida con éxito.")
                
                # Definir las colecciones que usaremos
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

# Crear una instancia global para ser importada en otros módulos
db_instance = Database()