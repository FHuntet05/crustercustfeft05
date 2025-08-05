import logging
import os
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# Importar los nuevos manejadores
from src.handlers.media_handler import any_file_handler, panel_command

# --- Configuración Inicial ---
load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))

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
    """Manejador de errores."""
    logger.error(f"Error: {context.error} causado por una actualización: {update}")

# --- Función Principal ---
def main():
    """Inicia el bot y lo mantiene corriendo."""
    logger.info("Iniciando el bot...")

    if not TELEGRAM_TOKEN:
        logger.critical("¡ERROR CRÍTICO! No se encontró el TELEGRAM_TOKEN en el entorno.")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- Registrar todos los manejadores ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("panel", panel_command))

    # --- LÍNEA CORREGIDA ---
    application.add_handler(MessageHandler(filters.VIDEO | filters.AUDIO | filters.Document.ALL, any_file_handler))

    application.add_error_handler(error_handler)

    logger.info("El bot está ahora en línea y escuchando...")
    application.run_polling()


if __name__ == '__main__':
    main()