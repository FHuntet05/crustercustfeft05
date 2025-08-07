# src/db/mongo_manager.py

import logging
import os
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import CollectionInvalid

# Configuración del logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MongoManager:
    def __init__(self, uri, db_name):
        self.client = AsyncIOMotorClient(uri)
        self.db = self.client[db_name]
        self.tasks = self.db.tasks
        self.search_sessions = self.db.search_sessions

    async def initialize_db(self):
        """
        Inicializa la base de datos y crea colecciones e índices de forma verdaderamente idempotente.
        """
        logger.info("Inicializando la base de datos y los índices...")
        try:
            # --- Colección de Tareas ---
            # Crear colección si no existe
            collection_names = await self.db.list_collection_names()
            if "tasks" not in collection_names:
                await self.db.create_collection("tasks")
                logger.info("Colección 'tasks' creada.")
            else:
                logger.info("Colección 'tasks' ya existe.")

            # Asegurar índice para la colección de tareas
            # PyMongo/Motor manejan la idempotencia de create_index si el nombre y las opciones son iguales.
            await self.tasks.create_index([("user_id", 1), ("status", 1)], name="user_status_index", background=True)
            logger.info("Índice 'user_status_index' para tasks asegurado.")

            # --- CORRECCIÓN: Lógica robusta para el índice TTL ---
            if "search_sessions" not in collection_names:
                await self.db.create_collection("search_sessions")
                logger.info("Colección 'search_sessions' creada.")
            else:
                logger.info("Colección 'search_sessions' ya existe.")

            # Verificar si ya existe un índice TTL en la colección
            existing_indexes = await self.search_sessions.index_information()
            ttl_index_exists = any('expireAfterSeconds' in options for options in existing_indexes.values())

            if not ttl_index_exists:
                logger.info("No se encontró un índice TTL en 'search_sessions'. Creando uno nuevo...")
                await self.search_sessions.create_index("created_at", expireAfterSeconds=3600, name="session_ttl_index")
                logger.info("Índice TTL 'session_ttl_index' creado con éxito.")
            else:
                logger.info("Índice TTL ya existe en 'search_sessions'. No se tomarán acciones.")

            logger.info("Inicialización de la base de datos completada con éxito.")

        except Exception as e:
            logger.error(f"Error crítico durante la inicialización de la base de datos: {e}", exc_info=True)
            raise

    # --- Métodos para Tareas ---

    async def create_task(self, task_data):
        result = await self.tasks.insert_one(task_data)
        return str(result.inserted_id)

    async def get_task(self, task_id):
        return await self.tasks.find_one({"_id": ObjectId(task_id)})

    async def update_task(self, task_id, update_data):
        await self.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": update_data})

    async def get_queued_tasks_by_user(self):
        pipeline = [
            {"$match": {"status": "queued"}},
            {"$sort": {"created_at": 1}},
            {"$group": {
                "_id": "$user_id",
                "tasks": {"$push": "$$ROOT"}
            }}
        ]
        cursor = self.tasks.aggregate(pipeline)
        tasks_by_user = {doc["_id"]: doc["tasks"] async for doc in cursor}
        return tasks_by_user

    # --- Métodos para Sesiones de Búsqueda ---
    
    async def create_search_session(self, query_id, results):
        session_data = {
            "_id": query_id,
            "results": results,
            "created_at": datetime.utcnow()
        }
        await self.search_sessions.insert_one(session_data)
    
    async def get_search_session(self, query_id):
        return await self.search_sessions.find_one({"_id": query_id})


# --- Instancia de la Base de Datos ---
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "JefesMediaSuiteDB")

if not MONGO_URI:
    logger.critical("La variable de entorno MONGO_URI no está definida. El bot no puede iniciarse.")
    exit()

db = MongoManager(uri=MONGO_URI, db_name=MONGO_DB_NAME)