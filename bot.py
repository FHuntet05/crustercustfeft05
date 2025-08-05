import logging
import os
import threading
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler
from telegram.ext import PicklePersistence # Para conversaciones

# Cargar variables de entorno
load_dotenv()

# Importar los manejadores
from src.handlers import command_handler, media_handler, button_handler, processing_handler
from src.core import worker

# Configuración de logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Leer credenciales
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

    # Usamos persistencia para el ConversationHandler
    persistence = PicklePersistence(filepath="bot_persistence")
    application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    # --- Configuración del ConversationHandler para Renombrar ---
    rename_handler = ConversationHandler(
        entry_points=[
            # Se activa cuando se pulsa el botón de renombrar
            CallbackQueryHandler(lambda u, c: processing_handler.show_rename_menu(u, c, u.callback_query.data.split('_', 1)[1]), pattern=r'^config_rename_')
        ],
        states={
            processing_handler.STATE_RENAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, processing_handler.process_rename_input)]
        },
        fallbacks=[CommandHandler('cancel', processing_handler.cancel_rename)],
        persistent=True,
        name="rename_conversation"
    )

    # --- Registrar todos los manejadores ---
    application.add_handler(CommandHandler("start", command_handler.start_command))
    application.add_handler(CommandHandler("panel", command_handler.panel_command))
    application.add_handler(CommandHandler("settings", command_handler.settings_command))
    
    application.add_handler(MessageHandler(
        filters.VIDEO | filters.AUDIO | filters.Document.ALL,
        media_handler.any_file_handler
    ))
    
    # Añadir el ConversationHandler
    application.add_handler(rename_handler)
    
    # El manejador de botones debe ir DESPUÉS de la conversación para que no capture sus entry_points
    application.add_handler(CallbackQueryHandler(button_handler.button_callback_handler))

    application.add_error_handler(command_handler.error_handler)

    # Pasamos el contexto de la aplicación al worker para que pueda usar el bot
    worker_thread = threading.Thread(target=worker.start_worker_loop, args=(application,))
    worker_thread.daemon = True
    worker_thread.start()
    logger.info("Worker iniciado en segundo plano.")

    logger.info("El bot está ahora en línea y escuchando...")
    application.run_polling()

if __name__ == '__main__':
    main()