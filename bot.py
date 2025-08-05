import logging
import os
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler

# --- Configuración Inicial ---
# Cargar las variables de entorno desde el archivo .env
load_dotenv()

# Configurar el logging para ver errores y actividad en la consola
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Leer las credenciales del entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))

# --- Definición de Comandos ---
async def start(update, context):
    """Manejador para el comando /start."""
    user = update.effective_user
    user_id = user.id
    
    greeting = "Hola."
    if user_id == ADMIN_USER_ID:
        greeting = f"A sus órdenes, Jefe. Bienvenido de vuelta."
        
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

    # Registrar los manejadores de comandos
    application.add_handler(CommandHandler("start", start))

    # Registrar el manejador de errores
    application.add_error_handler(error_handler)

    # Iniciar el bot
    logger.info("El bot está ahora en línea y escuchando...")
    application.run_polling()


if __name__ == '__main__':
    main()