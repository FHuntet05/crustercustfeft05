import logging
import os
import threading
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler

# Cargar variables de entorno ANTES de importar otros módulos nuestros
load_dotenv()

# Importar los manejadores de sus respectivos módulos
from src.handlers import command_handler, media_handler, button_handler
from src.core import worker

# Configurar el logging para ver errores y actividad en la consola
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Leer las credenciales del entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    logger.critical("ADMIN_USER_ID no está definido o no es válido. El bot no puede iniciar.")
    exit()


def main():
    """Inicia el bot y el worker, y los mantiene corriendo."""
    logger.info("Iniciando el bot...")

    if not TELEGRAM_TOKEN:
        logger.critical("¡ERROR CRÍTICO! No se encontró el TELEGRAM_TOKEN en el entorno.")
        return

    # Crear la aplicación del bot
    # Usaremos persistencia para poder guardar datos entre reinicios si fuera necesario
    # (por ejemplo, para conversaciones de varios pasos)
    # persistence = PicklePersistence(filepath="bot_persistence")
    # application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- Registrar todos los manejadores ---
    # Comandos: /start, /panel, /settings, etc.
    application.add_handler(CommandHandler("start", command_handler.start_command))
    application.add_handler(CommandHandler("panel", command_handler.panel_command))
    application.add_handler(CommandHandler("settings", command_handler.settings_command))
    
    # Manejador de recepción de archivos y URLs
    application.add_handler(MessageHandler(
        filters.VIDEO | filters.AUDIO | filters.Document.ALL,
        media_handler.any_file_handler
    ))
    # Aquí iría el handler para URLs (filters.Entity("url"))

    # Manejador de botones inline. Este es el router principal para las acciones.
    application.add_handler(CallbackQueryHandler(button_handler.button_callback_handler))

    # Manejador de errores (debe ser el último en registrarse)
    application.add_error_handler(command_handler.error_handler)

    # Iniciar el worker en un hilo separado para que no bloquee el bot
    worker_thread = threading.Thread(target=worker.start_worker_loop)
    worker_thread.daemon = True  # El hilo morirá cuando el programa principal muera
    worker_thread.start()
    logger.info("Worker iniciado en segundo plano.")

    # Iniciar el bot
    logger.info("El bot está ahora en línea y escuchando...")
    application.run_polling()


if __name__ == '__main__':
    main()