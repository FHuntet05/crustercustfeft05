import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_panel_keyboard
from src.helpers.utils import get_greeting, escape_html
from src.core import downloader

logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador para el comando /start. Saluda al usuario y crea su perfil si no existe."""
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    
    # Asegura que el documento de ajustes del usuario exista en la DB
    db_instance.get_user_settings(user.id)
    
    start_message = (
        f"A sus órdenes, {greeting_prefix}bienvenido a la <b>Suite de Medios</b>.\n\n"
        "Soy su Asistente personal, Forge. Estoy listo para procesar sus archivos.\n\n"
        "<b>¿Cómo empezar?</b>\n"
        "• <b>Envíe un archivo:</b> video, audio o documento.\n"
        "• <b>Pegue un enlace:</b> de YouTube, etc.\n"
        "• <b>Use /panel:</b> para ver su mesa de trabajo y procesar archivos.\n"
        "• <b>Use /findmusic:</b> para buscar y descargar canciones."
    )
    await update.message.reply_html(start_message)

async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback: bool = False):
    """
    Muestra la 'mesa de trabajo' del usuario con todos los archivos pendientes de procesar.
    Se puede invocar con el comando /panel o desde un callback de botón.
    """
    if is_callback:
        user = update.callback_query.from_user
        message = update.callback_query.message
    else:
        user = update.effective_user
        message = update.effective_message

    greeting_prefix = get_greeting(user.id)
    pending_tasks = db_instance.get_pending_tasks(user.id)
    
    if not pending_tasks:
        text = f"✅ ¡{greeting_prefix}Su mesa de trabajo está vacía!"
        if is_callback:
            await message.edit_text(text, parse_mode=ParseMode.HTML)
        else:
            await message.reply_html(text)
        return
        
    keyboard = build_panel_keyboard(pending_tasks)
    response_text = f"📋 <b>{greeting_prefix}Su mesa de trabajo actual:</b>"
    
    if is_callback:
        await message.edit_text(response_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        await message.reply_html(response_text, reply_markup=keyboard)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador para el comando /settings. Placeholder."""
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    await update.message.reply_html(
        f"⚙️ {greeting_prefix}Panel de Configuración.\n\n"
        "<i>(Función no implementada todavía)</i>"
    )

async def findmusic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Busca música usando /findmusic [término].
    Delega la búsqueda al módulo downloader y presenta los resultados.
    """
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    query = " ".join(context.args)

    if not query:
        await update.message.reply_html("Por favor, deme algo que buscar. Uso: <code>/findmusic [nombre de la canción]</code>")
        return
    
    status_message = await update.message.reply_html(f"🔎 {greeting_prefix}Buscando <code>{escape_html(query)}</code>...")
    
    search_results = downloader.search_music(query)
    
    if not search_results:
        await status_message.edit_text(f"❌ {greeting_prefix}No encontré resultados para su búsqueda.")
        return

    # Aquí se construiría un teclado con los resultados. Placeholder por ahora.
    text_results = [
        f"<b>{i+1}. {escape_html(r['title'])}</b> - {escape_html(r['artist'])}\n"
        f"   <code>/download_url {r['url']}</code>"
        for i, r in enumerate(search_results)
    ]
    
    response_text = f"✅ {greeting_prefix}He encontrado esto:\n\n" + "\n\n".join(text_results)
    await status_message.edit_text(response_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Manejador de errores global. Registra la excepción y notifica al usuario."""
    logger.error("Excepción al manejar una actualización:", exc_info=context.error)
    
    # Intenta notificar al usuario del error de forma segura
    if isinstance(update, Update) and update.effective_user:
        try:
            greeting_prefix = get_greeting(update.effective_user.id)
            error_message = (
                f"❌ Lo siento, {greeting_prefix}ha ocurrido un error inesperado.\n"
                "El incidente ha sido registrado para su revisión. Por favor, intente de nuevo más tarde."
            )
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=error_message,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"No se pudo enviar el mensaje de error al usuario {update.effective_user.id}: {e}")