# bot.py

import asyncio
import logging
import os
from dotenv import load_dotenv

# Cargar variables de entorno PRIMERO
load_dotenv()

from pyrogram import Client

# Importar componentes de la aplicación después de cargar el .env
from src.db.mongo_manager import db_instance
from src.core.worker import worker_loop

# Configuración de logging mejorada
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_activity.log"),
        logging.StreamHandler()
    ]
)
# Silenciar los logs de INFO de Pyrogram para una consola más limpia
logging.getLogger("pyrogram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Configuración del Cliente de Pyrogram
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
SESSION_NAME = os.getenv("SESSION_NAME", "JefesMediaSuiteBot")

# Validar que las credenciales esenciales están presentes
if not all([API_ID, API_HASH, BOT_TOKEN]):
    logger.critical("API_ID, API_HASH, o TELEGRAM_TOKEN no están definidos en el .env. Abortando.")
    exit(1)

# Definición de los plugins que el bot cargará
PLUGINS = dict(root="src/plugins")

# Punto de Entrada Principal
async def main():
    """
    Función principal para inicializar y correr el bot.
    """
    app = Client(
        SESSION_NAME,
        api_id=int(API_ID),
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        plugins=PLUGINS,
        workers=20 # Aumentar workers para mayor concurrencia
    )

    try:
        # 1. Conectar y inicializar la base de datos de forma segura
        await db_instance.init_db()
        
        # 2. Iniciar el cliente de Pyrogram
        await app.start()
        
        # Guardar la instancia del cliente para que el worker pueda usarla
        # Esto es más limpio que pasar 'app' a cada función.
        bot_info = await app.get_me()
        logger.info(f"Bot iniciado y conectado a Telegram.")
        logger.info(f"Nombre del bot: {bot_info.first_name}")
        logger.info(f"Username: @{bot_info.username}")
        
        # 3. Iniciar el worker asíncrono que procesará las tareas
        # Pasamos la instancia del cliente 'app' al worker
        worker_task = asyncio.create_task(worker_loop(app))
        
        # 4. Mantener todo corriendo
        logger.info("El bot está ahora en línea y el worker está escuchando por tareas.")
        await asyncio.gather(worker_task)
        
    except Exception as e:
        logger.critical(f"Error crítico durante el arranque o ejecución: {e}", exc_info=True)
    finally:
        if app.is_initialized:
            await app.stop()
            logger.info("Bot detenido.")

if __name__ == "__main__":
    # Crear directorios necesarios si no existen
    os.makedirs("downloads", exist_ok=True)
    os.makedirs("outputs", exist_ok=True)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Detención manual del bot (Ctrl+C).")