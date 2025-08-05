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

def main():
    if not TELEGRAM_TOKEN:
        logger.critical("TELEGRAM_TOKEN no encontrado.")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- Registrar todos los manejadores ---
    # Comandos
    application.add_handler(CommandHandler("start", command_handler.start_command))
    application.add_handler(CommandHandler("panel", command_handler.panel_command))
    application.add_handler(CommandHandler("settings", command_handler.settings_command))
    application.add_handler(CommandHandler("findmusic", command_handler.findmusic_command))
    
    # Manejadores de Mensajes
    application.add_handler(MessageHandler(filters.VIDEO | filters.AUDIO | filters.Document.ALL, media_handler.any_file_handler))
    application.add_handler(MessageHandler(filters.Entity("url") | filters.Entity("text_link"), media_handler.url_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processing_handler.text_input_handler))
    
    # Manejador de Botones (debe ir después de otros manejadores que puedan usar texto)
    application.add_handler(CallbackQueryHandler(button_handler.button_callback_handler))

    # Manejador de errores
    application.add_error_handler(command_handler.error_handler)

    # Iniciar el worker en un hilo separado
    worker_thread = threading.Thread(target=worker.worker_thread_runner, args=(application,))
    worker_thread.daemon = True
    worker_thread.start()
    logger.info("Worker iniciado en segundo plano.")

    logger.info("El bot está ahora en línea y escuchando...")
    application.run_polling()

if __name__ == '__main__':
    main()