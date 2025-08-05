import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_panel_keyboard, build_song_results_keyboard, build_settings_menu
from src.helpers.utils import get_greeting, escape_html
from src.core import downloader

logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador para el comando /start. Saluda al usuario y crea su perfil si no existe."""
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    db_instance.get_user_settings(user.id)
    start_message = (
        f"A sus órdenes, {greeting_prefix}bienvenido a la <b>Suite de Medios</b>.\n\n"
        "Soy su Asistente personal, Forge. Estoy listo para procesar sus archivos.\n\n"
        "<b>¿Cómo empezar?</b>\n"
        "• <b>Envíe un archivo:</b> video, audio o documento.\n"
        "• <b>Pegue un enlace:</b> de YouTube, etc.\n"
        "• <b>Use /panel:</b> para ver su mesa de trabajo y procesar archivos.\n"
        "• <b>Use /findmusic:</b> para buscar y descargar canciones.\n"
        "• <b>Use /settings:</b> para configurar sus preferencias."
    )
    await update.message.reply_html(start_message)

async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback: bool = False):
    """
    Muestra la 'mesa de trabajo' del usuario con todos los archivos pendientes de procesar.
    """
    user = update.callback_query.from_user if is_callback else update.effective_user
    message = update.callback_query.message if is_callback else update.effective_message
    greeting_prefix = get_greeting(user.id)
    pending_tasks = db_instance.get_pending_tasks(user.id)
    
    if not pending_tasks:
        text = f"✅ ¡{greeting_prefix}Su mesa de trabajo está vacía!"
        try:
            if is_callback: await message.edit_text(text, parse_mode=ParseMode.HTML)
            else: await message.reply_html(text)
        except Exception as e: logger.warning(f"No se pudo editar/enviar mensaje del panel: {e}")
        return
        
    keyboard = build_panel_keyboard(pending_tasks)
    response_text = f"📋 <b>{greeting_prefix}Su mesa de trabajo actual:</b>"
    
    try:
        if is_callback: await message.edit_text(response_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else: await message.reply_html(response_text, reply_markup=keyboard)
    except Exception as e: logger.warning(f"No se pudo editar/enviar mensaje del panel: {e}")

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador para el comando /settings. Muestra el menú de configuración."""
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    keyboard = build_settings_menu(user.id)
    await update.message.reply_html(
        f"⚙️ {greeting_prefix}Panel de Configuración General.",
        reply_markup=keyboard
    )

async def findmusic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Busca música y guarda los resultados en la DB para una selección segura.
    """
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    query = " ".join(context.args)

    if not query:
        await update.message.reply_html("Por favor, deme algo que buscar. Uso: <code>/findmusic [nombre]</code>")
        return
    
    status_message = await update.message.reply_html(f"🔎 {greeting_prefix}Buscando <code>{escape_html(query)}</code>...")
    
    search_results = downloader.search_music(query, limit=5)
    
    if not search_results:
        await status_message.edit_text(f"❌ {greeting_prefix}No encontré resultados para su búsqueda.")
        return

    docs_to_insert = []
    for res in search_results:
        res['created_at'] = datetime.utcnow()
        docs_to_insert.append(res)
    
    result_ids = db_instance.search_results.insert_many(docs_to_insert).inserted_ids
    
    for i, res_id in enumerate(result_ids):
        search_results[i]['_id'] = str(res_id)

    keyboard = build_song_results_keyboard(search_results)
    
    response_text = f"✅ {greeting_prefix}He encontrado esto. Seleccione una para descargar:"
    await status_message.edit_text(
        response_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Manejador de errores global."""
    logger.error("Excepción al manejar una actualización:", exc_info=context.error)
    
    if isinstance(update, Update) and update.effective_user:
        try:
            greeting_prefix = get_greeting(update.effective_user.id)
            error_message = (
                f"❌ Lo siento, {greeting_prefix}ha ocurrido un error inesperado.\n"
                "El incidente ha sido registrado. Por favor, intente de nuevo."
            )
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=error_message,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"No se pudo enviar mensaje de error al usuario {update.effective_user.id}: {e}")