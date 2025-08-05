import logging
import os
import threading
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
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
logger = logging.getLogger(__name__)

# --- Constantes ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

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
    
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .persistence(persistence)
        .defaults(defaults)
        .build()
    )

    # --- Registro de Manejadores (Handlers) ---
    # Comandos principales
    application.add_handler(CommandHandler("start", command_handler.start_command))
    application.add_handler(CommandHandler("panel", command_handler.panel_command))
    application.add_handler(CommandHandler("settings", command_handler.settings_command))
    application.add_handler(CommandHandler("findmusic", command_handler.findmusic_command))
    
    # Manejador de todos los botones inline
    application.add_handler(CallbackQueryHandler(button_handler.button_callback_handler))

    # Manejador de URLs y enlaces
    application.add_handler(MessageHandler(
        (filters.Entity("url") | filters.Entity("text_link")) & (~filters.UpdateType.EDITED_MESSAGE), 
        media_handler.url_handler
    ))
    
    # Manejador UNIFICADO para todos los archivos (video, audio, foto, documento)
    application.add_handler(MessageHandler(
        (filters.VIDEO | filters.AUDIO | filters.PHOTO | filters.Document.ALL) & (~filters.UpdateType.EDITED_MESSAGE), 
        media_handler.any_file_handler
    ))
    
    # Manejador para texto (usado para la configuración interactiva)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (~filters.UpdateType.EDITED_MESSAGE), 
        media_handler.text_input_handler
    ))
    
    # Manejador de errores
    application.add_error_handler(command_handler.error_handler)

    # --- Inicio del Worker en un Hilo Separado ---
    worker_thread = threading.Thread(
        target=worker.worker_thread_runner,
        daemon=True
    )
    worker_thread.start()
    logger.info("Worker de procesamiento iniciado en segundo plano.")

    # --- Inicio del Bot ---
    logger.info("El bot está ahora en línea y escuchando...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()