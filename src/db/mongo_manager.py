# src/db/mongo_manager.py

import logging
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import CollectionInvalid, OperationFailure
from bson import ObjectId

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
        Inicializa la base de datos y crea colecciones e índices si no existen.
        Esta operación es idempotente.
        """
        logger.info("Inicializando la base de datos y los índices...")
        try:
            # Crear colección de tareas si no existe
            try:
                await self.db.create_collection("tasks")
                logger.info("Colección 'tasks' creada.")
            except CollectionInvalid:
                logger.info("Colección 'tasks' ya existe.")

            # Crear índices para la colección de tareas
            await self.tasks.create_index([("user_id", 1), ("status", 1)], name="user_status_index", background=True)
            logger.info("Índice 'user_status_index' asegurado.")

            # Crear colección de sesiones de búsqueda con TTL (expiración automática)
            try:
                await self.db.create_collection("search_sessions")
                logger.info("Colección 'search_sessions' creada.")
                # Crear índice TTL que borra documentos después de 1 hora (3600 segundos)
                await self.search_sessions.create_index("created_at", expireAfterSeconds=3600, name="session_ttl_index")
                logger.info("Índice TTL 'session_ttl_index' para sesiones de búsqueda asegurado.")
            except CollectionInvalid:
                logger.info("Colección 'search_sessions' ya existe, asegurando índice TTL.")
                # Asegurarse de que el índice TTL existe si la colección ya estaba
                try:
                    await self.search_sessions.create_index("created_at", expireAfterSeconds=3600, name="session_ttl_index")
                    logger.info("Índice TTL 'session_ttl_index' asegurado.")
                except OperationFailure as e:
                    # Ignorar error si el índice ya existe con otras opciones
                    if "index with same options but different name" in str(e) or "Index with name" in str(e):
                         logger.warning(f"Índice TTL ya existe con otro nombre o configuración: {e}")
                    else:
                        raise e

            logger.info("Inicialización de la base de datos completada.")
        except Exception as e:
            logger.error(f"Error durante la inicialización de la base de datos: {e}", exc_info=True)
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
        """
        Obtiene todas las tareas en cola y las agrupa por usuario.
        """
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
        """Guarda los resultados de una búsqueda en una sesión temporal."""
        session_data = {
            "_id": query_id,
            "results": results,
            "created_at": datetime.utcnow()
        }
        await self.search_sessions.insert_one(session_data)
    
    async def get_search_session(self, query_id):
        """Recupera los resultados de una búsqueda de la sesión."""
        return await self.search_sessions.find_one({"_id": query_id})


# Instancia global de la base de datos
# Asegúrate de tener estas variables en tu config o entorno
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "JefesMediaSuiteDB")

db = MongoManager(uri=MONGO_URI, db_name=MONGO_DB_NAME)