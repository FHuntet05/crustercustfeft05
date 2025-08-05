import logging
import os
import threading
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler

load_dotenv()

from src.handlers import command_handler, media_handler, button_handler, processing_handler
from src.core import worker

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    logger.critical("ADMIN_USER_ID no definido o inválido.")
    exit()

def main():
    """Inicia el bot y el worker."""
    logger.info("Iniciando el bot...")

    if not TELEGRAM_TOKEN:
        logger.critical("TELEGRAM_TOKEN no encontrado.")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- Registrar todos los manejadores ---
    application.add_handler(CommandHandler("start", command_handler.start_command))
    application.add_handler(CommandHandler("panel", command_handler.panel_command))
    application.add_handler(CommandHandler("settings", command_handler.settings_command))
    
    application.add_handler(MessageHandler(
        filters.VIDEO | filters.AUDIO | filters.Document.ALL,
        media_handler.any_file_handler
    ))
    
    # Manejador de texto genérico para cosas como el renombrado
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processing_handler.text_input_handler))
    
    application.add_handler(CallbackQueryHandler(button_handler.button_callback_handler))

    application.add_error_handler(command_handler.error_handler)

    # El worker ahora no necesita el contexto de la app, lo obtendrá de otra forma si es necesario
    worker_thread = threading.Thread(target=worker.start_worker_loop, args=(application,))
    worker_thread.daemon = True
    worker_thread.start()
    logger.info("Worker iniciado en segundo plano.")

    logger.info("El bot está ahora en línea y escuchando...")
    application.run_polling()

if __name__ == '__main__':
    main()