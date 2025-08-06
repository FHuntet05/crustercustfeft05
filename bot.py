import logging
import os
import threading
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    PicklePersistence,
    Defaults
)
from telegram.constants import ParseMode, UpdateType

# --- Carga de Entorno y Configuración Inicial ---
load_dotenv()

# --- Importación de Módulos del Proyecto ---
from src.handlers import command_handler, media_handler, button_handler
from src.core import worker
from src.core.userbot_manager import userbot_instance
from src.db.mongo_manager import db_instance

# --- Configuración del Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", mode='a', encoding='utf-8')
    ]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Constantes ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

# --- Hooks de Inicialización y Apagado ---
async def post_init(application: Application):
    """Se ejecuta después de que la aplicación se inicialice pero antes de que empiece el polling."""
    logger.info("Hook post_init: Iniciando conexión del Userbot...")
    await userbot_instance.start()

async def post_shutdown(application: Application):
    """Se ejecuta después de que el polling se detenga."""
    logger.info("Hook post_shutdown: Deteniendo conexión del Userbot...")
    await userbot_instance.stop()

def main():
    """Punto de entrada principal. Configura y ejecuta el bot."""
    if not TELEGRAM_TOKEN:
        logger.critical("¡ERROR CRÍTICO! La variable de entorno TELEGRAM_TOKEN no está definida.")
        return
    if not ADMIN_USER_ID:
        logger.warning("ADVERTENCIA: La variable de entorno ADMIN_USER_ID no está definida. No habrá un 'Jefe'.")
    
    try:
        db_instance.client.admin.command('ping')
    except Exception as e:
        logger.critical(f"¡ERROR CRÍTICO! No se pudo conectar a MongoDB al iniciar. Error: {e}")
        return

    persistence = PicklePersistence(filepath="bot_persistence")
    defaults = Defaults(parse_mode=ParseMode.HTML)
    
    # --- Construcción de la Aplicación con Hooks ---
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .persistence(persistence)
        .defaults(defaults)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # --- Registro de Manejadores (Handlers) ---
    application.add_handler(CommandHandler("start", command_handler.start_command))
    application.add_handler(CommandHandler("panel", command_handler.panel_command))
    application.add_handler(CommandHandler("settings", command_handler.settings_command))
    application.add_handler(CommandHandler("findmusic", command_handler.findmusic_command))
    
    application.add_handler(CallbackQueryHandler(button_handler.button_callback_handler))

    application.add_handler(MessageHandler(
        (filters.Entity("url") | filters.Entity("text_link")) & (~filters.UpdateType.EDITED_MESSAGE), 
        media_handler.url_handler
    ))
    
    application.add_handler(MessageHandler(
        (filters.VIDEO | filters.AUDIO | filters.PHOTO | filters.Document.ALL) & (~filters.UpdateType.EDITED_MESSAGE), 
        media_handler.any_file_handler
    ))
    
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (~filters.UpdateType.EDITED_MESSAGE), 
        media_handler.text_input_handler
    ))
    
    application.add_error_handler(command_handler.error_handler)

    # --- Inicio del Worker en un Hilo Separado ---
    worker_thread = threading.Thread(target=worker.worker_thread_runner, daemon=True)
    worker_thread.start()
    logger.info("Worker de procesamiento iniciado en segundo plano.")

    # --- Inicio del Bot (llamada bloqueante que gestiona todo) ---
    logger.info("La aplicación del bot se está iniciando... El Userbot se conectará a continuación.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()