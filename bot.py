# --- START OF FILE bot.py ---

import asyncio
import logging
import os
from dotenv import load_dotenv

# Cargar variables de entorno PRIMERO, antes de que cualquier otro módulo las necesite.
load_dotenv()

# Importar componentes de la aplicación DESPUÉS de cargar el .env
from src.config import Config  # <-- AHORA IMPORTAMOS NUESTRA CLASE CONFIG
from src.db.mongo_manager import db_instance
from src.core.worker import main_worker # Cambiado de worker_loop a main_worker como en tu archivo

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

# Validar que las credenciales esenciales están presentes antes de intentar conectar
if not all([Config.API_ID, Config.API_HASH, Config.TELEGRAM_TOKEN]):
    logger.critical("CRITICAL: API_ID, API_HASH, o TELEGRAM_TOKEN no están definidos en el .env. Abortando.")
    exit(1)

# Definición de los plugins que el bot cargará
PLUGINS = dict(root="src/plugins")

# --- Punto de Entrada Principal ---
async def main():
    """
    Función principal para inicializar y correr el bot de forma segura.
    """
    app = Client(
        Config.SESSION_NAME,
        api_id=int(Config.API_ID),
        api_hash=Config.API_HASH,
        bot_token=Config.TELEGRAM_TOKEN,
        plugins=PLUGINS,
        workers=20
    )

    try:
        # 1. Conectar y inicializar la base de datos
        logger.info("Iniciando conexión con la base de datos...")
        await db_instance.init_db()
        logger.info("Conexión con la base de datos establecida y índices asegurados.")
        
        # 2. Iniciar el cliente de Pyrogram
        logger.info("Iniciando cliente de Telegram...")
        await app.start()
        logger.info("Cliente de Telegram iniciado correctamente.")
        
        bot_info = await app.get_me()
        logger.info(f"Bot conectado como:")
        logger.info(f"  -> Nombre: {bot_info.first_name}")
        logger.info(f"  -> Username: @{bot_info.username}")
        
        # 3. Iniciar el worker asíncrono que procesará las tareas en segundo plano
        logger.info("Iniciando el bucle del worker para procesar tareas...")
        worker_task = asyncio.create_task(main_worker(app)) # Usando main_worker
        
        # 4. Mantener todo corriendo
        logger.info("¡El bot está en línea y listo para recibir tareas!")
        await asyncio.gather(worker_task)
        
    except Exception as e:
        logger.critical(f"Error crítico durante el arranque o ejecución principal: {e}", exc_info=True)
    finally:
        if app.is_initialized:
            logger.info("Deteniendo el cliente de Telegram...")
            await app.stop()
            logger.info("Bot detenido de forma segura.")

if __name__ == "__main__":
    # Crear directorios necesarios al inicio para evitar errores
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Detención manual del bot (Ctrl+C).")

# --- END OF FILE bot.py ---