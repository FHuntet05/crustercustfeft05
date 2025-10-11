# --- START OF FILE bot.py ---

import asyncio
import logging
import os
from dotenv import load_dotenv

# Cargar variables de entorno PRIMERO, antes de que cualquier otro módulo las necesite.
load_dotenv()

from pyrogram import Client
from pyrogram.enums import ParseMode

# Importar componentes de la aplicación DESPUÉS de cargar el .env
from src.db.mongo_manager import db_instance
from src.core.worker import worker_loop

# Configuración de logging mejorada para diagnóstico claro
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

# --- Configuración del Cliente de Pyrogram ---
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
SESSION_NAME = os.getenv("SESSION_NAME", "JefesMediaSuiteBot")

# Validar que las credenciales esenciales están presentes antes de intentar conectar
if not all([API_ID, API_HASH, BOT_TOKEN]):
    logger.critical("CRITICAL: API_ID, API_HASH, o TELEGRAM_TOKEN no están definidos en el .env. Abortando.")
    exit(1)

# Definición de los plugins que el bot cargará
PLUGINS = dict(root="src/plugins")

# --- Punto de Entrada Principal ---
async def main():
    """
    Función principal para inicializar y correr el bot de forma segura.
    """
    # Cliente Bot
    app = Client(
        SESSION_NAME,
        api_id=int(API_ID),
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        plugins=PLUGINS,
        workers=20
    )

    # Cliente UserBot para operaciones restringidas
    user_client = Client(
        "user_bot",
        api_id=int(API_ID),
        api_hash=API_HASH,
        session_string=os.getenv("USER_SESSION_STRING"),  # String de sesión del userbot
        parse_mode=ParseMode.HTML  # Configurar parse_mode por defecto
    )

    try:
        # 1. Conectar y inicializar la base de datos
        logger.info("Iniciando conexión con la base de datos...")
        await db_instance.init_db()
        logger.info("Conexión con la base de datos establecida y índices asegurados.")
        
        # 2. Iniciar los clientes de Telegram
        logger.info("Iniciando clientes de Telegram...")
        await app.start()
        await user_client.start()
        logger.info("Clientes de Telegram iniciados correctamente.")
        
        # Health check y log de información del bot
        bot_info = await app.get_me()
        user_info = await user_client.get_me()
        logger.info(f"Bot conectado como:")
        logger.info(f"  -> Nombre: {bot_info.first_name}")
        logger.info(f"  -> Username: @{bot_info.username}")
        logger.info(f"UserBot conectado como:")
        logger.info(f"  -> Nombre: {user_info.first_name}")
        logger.info(f"  -> Username: @{user_info.username}")
        
        # Guardar el cliente de userbot en un lugar accesible
        app.user_client = user_client
        
        # 3. Iniciar el worker asíncrono que procesará las tareas en segundo plano
        logger.info("Iniciando el bucle del worker para procesar tareas...")
        worker_task = asyncio.create_task(worker_loop(app))
        
        # 4. Mantener todo corriendo
        logger.info("¡El bot está en línea y listo para recibir tareas!")
        # await asyncio.gather() mantiene el programa vivo para que el worker y el cliente sigan funcionando.
        await asyncio.gather(worker_task)
        
    except Exception as e:
        logger.critical(f"Error crítico durante el arranque o ejecución principal: {e}", exc_info=True)
    finally:
        # Asegurar una detención limpia del cliente si estaba inicializado
        if app.is_initialized:
            logger.info("Deteniendo el cliente de Telegram...")
            await app.stop()
            logger.info("Bot detenido de forma segura.")

if __name__ == "__main__":
    # Crear directorios necesarios al inicio para evitar errores
    os.makedirs("downloads", exist_ok=True)
    os.makedirs("outputs", exist_ok=True)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Detención manual del bot (Ctrl+C).")