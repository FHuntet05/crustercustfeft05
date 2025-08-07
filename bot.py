# bot.py

import asyncio
import logging
import os
from dotenv import load_dotenv

# Cargar variables de entorno PRIMERO
load_dotenv()

from pyrogram import Client

# Importar componentes de la aplicación después de cargar el .env
from src.db.mongo_manager import db
from src.core.worker import Worker

# Configuración de logging mejorada
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Silenciar los logs de INFO de Pyrogram para una consola más limpia
logging.getLogger("pyrogram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Configuración del Cliente de Pyrogram
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Validar que las credenciales esenciales están presentes
if not all([API_ID, API_HASH, BOT_TOKEN]):
    logger.critical("API_ID, API_HASH, o TELEGRAM_TOKEN no están definidos en el .env. Abortando.")
    exit()

# Definición de los plugins que el bot cargará
PLUGINS = dict(root="src/plugins")

# Creación de la instancia del cliente del Bot
app = Client(
    "JefesMediaSuiteBot",
    api_id=int(API_ID),
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=PLUGINS
)

# Punto de Entrada Principal
async def main():
    """
    Función principal para inicializar y correr el bot.
    """
    try:
        # 1. Conectar y inicializar la base de datos de forma segura
        await db.initialize_db()
        
        # 2. Iniciar el cliente de Pyrogram
        await app.start()
        logger.info("Bot iniciado y conectado a Telegram.")
        
        bot_info = await app.get_me()
        logger.info(f"Nombre del bot: {bot_info.first_name}")
        logger.info(f"Username: @{bot_info.username}")
        
        # 3. Iniciar el worker asíncrono que procesará las tareas
        worker_instance = Worker(app)
        worker_task = asyncio.create_task(worker_instance.start())
        
        # 4. Mantener todo corriendo
        await asyncio.gather(worker_task)
        
    except Exception as e:
        logger.critical(f"Error crítico durante el arranque o ejecución: {e}", exc_info=True)
    finally:
        if app.is_initialized:
            await app.stop()
            logger.info("Bot detenido.")


if __name__ == "__main__":
    # Crear el directorio de descargas si no existe
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
        logger.info("Directorio 'downloads' creado.")

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Detención manual del bot (Ctrl+C).")