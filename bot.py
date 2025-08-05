import logging
import os
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler

# Importar los manejadores de sus respectivos módulos
from src.handlers.media_handler import any_file_handler, panel_command
from src.handlers.button_handler import button_callback_handler

# Cargar las variables de entorno desde el archivo .env
# Es importante que esto se haga antes de importar módulos que las necesiten
load_dotenv()

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

# --- Definición de Comandos ---
async def start(update, context):
    """Manejador para el comando /start."""
    user = update.effective_user
    user_id = user.id
    greeting = "A sus órdenes, Jefe. Bienvenido de vuelta." if user_id == ADMIN_USER_ID else "Hola."
    await update.message.reply_html(
        f"¡{greeting}!\n\n"
        f"Soy su Asistente de Medios personal. "
        f"Envíeme un archivo, un enlace o use un comando para empezar.\n\n"
        f"Use /panel para ver su mesa de trabajo."
    )

async def error_handler(update, context):
    """Manejador de errores para loggear excepciones."""
    logger.error(f"Error: {context.error} causado por una actualización: {update}")

# --- Función Principal ---
def main():
    """Inicia el bot y lo mantiene corriendo."""
    logger.info("Iniciando el bot...")

    if not TELEGRAM_TOKEN:
        logger.critical("¡ERROR CRÍTICO! No se encontró el TELEGRAM_TOKEN en el entorno.")
        return

    # Crear la aplicación del bot
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- Registrar todos los manejadores ---
    # Comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("panel", panel_command))

    # Manejador de archivos
    application.add_handler(MessageHandler(filters.VIDEO | filters.AUDIO | filters.Document.ALL, any_file_handler))
    
    # Manejador de botones inline
    application.add_handler(CallbackQueryHandler(button_callback_handler))

    # Manejador de errores (debe ser el último en registrarse)
    application.add_error_handler(error_handler)

    # Iniciar el bot
    logger.info("El bot está ahora en línea y escuchando...")
    application.run_polling()


if __name__ == '__main__':
    main()