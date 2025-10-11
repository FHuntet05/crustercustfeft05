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
required_env_vars = {
    "API_ID": API_ID,
    "API_HASH": API_HASH,
    "BOT_TOKEN": BOT_TOKEN,
    "USERBOT_SESSION_STRING": os.getenv("USERBOT_SESSION_STRING")
}

missing_vars = [var for var, value in required_env_vars.items() if not value]

if missing_vars:
    logger.critical(f"CRITICAL: Las siguientes variables no están definidas en el .env: {', '.join(missing_vars)}")
    logger.critical("Por favor, ejecuta generate_session.py para obtener USERBOT_SESSION_STRING")
    exit(1)

# Definición de los plugins que el bot cargará
PLUGINS = dict(root="src/plugins")

# --- Punto de Entrada Principal ---
async def main():
    """
    Función principal para inicializar y correr el bot de forma segura.
    Maneja la inicialización, ejecución y apagado seguro de los clientes.
    """
    app = None  # Definimos las variables fuera del try para el finally
    user_client = None
    
    try:
        # Ya hemos validado las variables de entorno al inicio
        USERBOT_SESSION_STRING = os.getenv("USERBOT_SESSION_STRING")
        
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
            name="user_bot",
            api_id=int(API_ID),
            api_hash=API_HASH,
            session_string=USERBOT_SESSION_STRING,
            parse_mode=ParseMode.HTML,
            no_updates=True
        )

        # 1. Conectar y inicializar la base de datos
        logger.info("Iniciando conexión con la base de datos...")
        await db_instance.init_db()
        logger.info("Conexión con la base de datos establecida y índices asegurados.")
        
        # 2. Iniciar los clientes de Telegram
        logger.info("Iniciando clientes de Telegram...")
        
        # Iniciar el bot primero
        await app.start()
        logger.info("Bot iniciado correctamente")
        
        # Intentar iniciar el userbot
        await user_client.start()
        logger.info("UserBot iniciado correctamente")
            
        # Health check y log de información
        bot_info = await app.get_me()
        user_info = await user_client.get_me()
        
        logger.info(f"Bot conectado como:")
        logger.info(f"  -> Nombre: {bot_info.first_name}")
        logger.info(f"  -> Username: @{bot_info.username}")
        logger.info(f"UserBot conectado como:")
        logger.info(f"  -> Nombre: {user_info.first_name}")
        logger.info(f"  -> Username: @{user_info.username}")
        logger.info(f"  -> ID: {user_info.id}")
    
        # Guardar el cliente de userbot en un lugar accesible
        app.user_client = user_client
        
        # 3. Iniciar el worker asíncrono que procesará las tareas en segundo plano
        logger.info("Iniciando el bucle del worker para procesar tareas...")
        worker_task = asyncio.create_task(worker_loop(app))
        
        # 4. Mantener todo corriendo
        logger.info("¡El bot está en línea y listo para recibir tareas!")
        await asyncio.gather(worker_task)
        
    except Exception as e:
        logger.critical(f"Error crítico durante el arranque o ejecución principal: {e}", exc_info=True)
        raise
    finally:
        # Asegurar una detención limpia de los clientes
        try:
            if app and app.is_initialized:
                logger.info("Deteniendo el bot...")
                await app.stop()
            if user_client and user_client.is_initialized:
                logger.info("Deteniendo el userbot...")
                await user_client.stop()
            logger.info("Clientes detenidos de forma segura.")
        except Exception as e:
            logger.error(f"Error al detener los clientes: {e}")

if __name__ == "__main__":
    # Crear directorios necesarios al inicio para evitar errores
    os.makedirs("downloads", exist_ok=True)
    os.makedirs("outputs", exist_ok=True)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Detención manual del bot (Ctrl+C).")
    except Exception as e:
        logger.critical(f"Error fatal: {e}", exc_info=True)