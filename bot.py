import logging
import os
import threading
import asyncio
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
logging.getLogger("pyrogram").setLevel(logging.WARNING) # Silenciar logs de Pyrogram
logger = logging.getLogger(__name__)

# --- Constantes ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

async def main():
    """Punto de entrada asíncrono. Configura y ejecuta el bot y el userbot."""
    
    # --- Verificaciones iniciales ---
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

    # --- Inicialización del Bot de Telegram ---
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
    worker_thread = threading.Thread(
        target=worker.worker_thread_runner,
        daemon=True
    )
    worker_thread.start()
    logger.info("Worker de procesamiento iniciado en segundo plano.")

    # --- Arranque y Apagado Controlado del Bot y Userbot ---
    try:
        # Iniciar el bot y el userbot concurrentemente
        await application.initialize()
        await userbot_instance.start()
        
        logger.info("El bot está ahora en línea y escuchando...")
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        
        # Mantener el script corriendo hasta que se detenga manualmente
        while True:
            await asyncio.sleep(3600)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Detención del bot solicitada.")
    finally:
        # Apagado ordenado
        logger.info("Iniciando secuencia de apagado...")
        await application.updater.stop()
        await application.stop()
        await userbot_instance.stop()
        logger.info("El bot se ha detenido limpiamente.")


if __name__ == '__main__':
    # Ejecutar el bucle de eventos principal
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "There is no current event loop" in str(e):
            logger.warning("Se detectó un reinicio rápido, puede ignorar el error de event loop.")
        else:
            raise