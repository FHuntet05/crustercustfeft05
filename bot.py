import logging
import os
import threading
from dotenv import load_dotenv
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    PicklePersistence,
)
from telegram.constants import ParseMode

# --- Carga de Entorno y Configuración Inicial ---
load_dotenv()

# --- Importación de Módulos del Proyecto ---
from src.handlers import command_handler, media_handler, button_handler, processing_handler
from src.core import worker
from src.db.mongo_manager import db_instance # Para una verificación inicial

# --- Configuración del Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(), # Muestra logs en la consola
        logging.FileHandler("bot.log", mode='a', encoding='utf-8') # Guarda logs en un archivo
    ]
)
# Silenciar logs muy verbosos de librerías externas
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Constantes ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

def main():
    """Punto de entrada principal. Configura y ejecuta el bot."""
    
    # --- Verificaciones Iniciales ---
    if not TELEGRAM_TOKEN:
        logger.critical("¡ERROR CRÍTICO! La variable de entorno TELEGRAM_TOKEN no está definida.")
        return
    if not ADMIN_USER_ID:
        logger.warning("ADVERTENCIA: La variable de entorno ADMIN_USER_ID no está definida. No habrá un 'Jefe'.")
    
    # Verificar conexión a la base de datos al inicio
    try:
        db_instance.client.admin.command('ping')
    except Exception as e:
        logger.critical(f"¡ERROR CRÍTICO! No se pudo conectar a MongoDB al iniciar. Error: {e}")
        return

    # --- Configuración de la Persistencia ---
    # Usaremos PicklePersistence para guardar context.user_data y chat_data entre reinicios.
    persistence = PicklePersistence(filepath="bot_persistence")

    # --- Creación de la Aplicación del Bot ---
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .persistence(persistence)
        .parse_mode(ParseMode.HTML) # Establecer modo de parseo por defecto
        .build()
    )

    # --- Registro de Manejadores (Handlers) ---
    
    # Comandos Principales
    application.add_handler(CommandHandler("start", command_handler.start_command))
    application.add_handler(CommandHandler("panel", command_handler.panel_command))
    application.add_handler(CommandHandler("settings", command_handler.settings_command))
    application.add_handler(CommandHandler("findmusic", command_handler.findmusic_command))
    
    # Manejadores de Recepción de Media
    # El filtro `~filters.UpdateType.EDITED_MESSAGE` evita que el bot reaccione a mensajes editados.
    application.add_handler(MessageHandler(filters.VIDEO | filters.AUDIO | filters.Document.ALL & (~filters.UpdateType.EDITED_MESSAGE), media_handler.any_file_handler))
    application.add_handler(MessageHandler(filters.Entity("url") | filters.Entity("text_link") & (~filters.UpdateType.EDITED_MESSAGE), media_handler.url_handler))
    
    # Manejadores de Input para Configuración (en orden de especificidad)
    # 1. Foto (para carátulas de audio)
    application.add_handler(MessageHandler(filters.PHOTO & (~filters.UpdateType.EDITED_MESSAGE), processing_handler.photo_input_handler))
    # 2. Texto (para nombres, tiempos, etc.)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (~filters.UpdateType.EDITED_MESSAGE), processing_handler.text_input_handler))
    
    # Manejador de Botones Inline (CallbackQuery)
    # Este es uno de los manejadores más importantes.
    application.add_handler(CallbackQueryHandler(button_handler.button_callback_handler))

    # Manejador de Errores Global
    # Este handler debe ir al final para capturar cualquier excepción no manejada.
    application.add_error_handler(command_handler.error_handler)

    # --- Inicio del Worker en un Hilo Separado ---
    # Esto es crucial para que el bot no se bloquee mientras procesa archivos.
    worker_thread = threading.Thread(
        target=worker.worker_thread_runner,
        args=(application,),
        daemon=True # El hilo se cerrará cuando el programa principal termine
    )
    worker_thread.start()
    logger.info("Worker de procesamiento iniciado en segundo plano.")

    # --- Inicio del Bot ---
    logger.info("El bot está ahora en línea y escuchando...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()