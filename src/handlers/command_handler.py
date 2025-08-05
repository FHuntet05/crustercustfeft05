import logging
from telegram import Update
from telegram.ext import ContextTypes

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_panel_keyboard
from src.helpers.utils import get_greeting
# Placeholder para la funcionalidad de búsqueda, que vivirá en un módulo core
# from src.core.downloader import search_music 

logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    db_instance.get_user_settings(user.id) # Asegura que los ajustes del usuario existan
    
    await update.message.reply_html(
        f"A sus órdenes, {greeting_prefix}Bienvenido.\n\n"
        "Soy su Asistente de Medios personal. "
        "Envíeme un archivo, un enlace, o use /panel para ver su mesa de trabajo."
    )

async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        user = update.callback_query.from_user
        message = update.callback_query.message
        is_callback = True
    else:
        user = update.effective_user
        message = update.effective_message
        is_callback = False

    greeting_prefix = get_greeting(user.id)
    pending_tasks = db_instance.get_pending_tasks(user.id)
    
    if not pending_tasks:
        text = f"✅ ¡{greeting_prefix}Su mesa de trabajo está vacía!"
        if is_callback: await message.edit_text(text, parse_mode='HTML')
        else: await message.reply_html(text)
        return
        
    keyboard = build_panel_keyboard(pending_tasks)
    response_text = f"📋 <b>{greeting_prefix}Su mesa de trabajo actual:</b>"
    
    if is_callback:
        await message.edit_text(response_text, reply_markup=keyboard, parse_mode='HTML')
    else:
        await message.reply_html(response_text, reply_markup=keyboard)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    await update.message.reply_html(
        f"⚙️ {greeting_prefix}Panel de Configuración.\n\n"
        "<i>(Función no implementada todavía)</i>"
    )

async def findmusic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    query = " ".join(context.args)
    if not query:
        await update.message.reply_html("Por favor, deme algo que buscar. Uso: <code>/findmusic [nombre de la canción]</code>")
        return
    
    # Placeholder de la búsqueda
    await update.message.reply_html(f"🔎 {greeting_prefix}Buscando <code>{query}</code>...\n\n<i>(Función no implementada todavía)</i>")
    # resultados = search_music(query)
    # keyboard = build_music_results_keyboard(resultados)
    # await update.message.reply_html("He encontrado esto:", reply_markup=keyboard)


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Excepción al manejar una actualización:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_user:
        try:
            greeting_prefix = get_greeting(update.effective_user.id)
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=f"❌ Lo siento, {greeting_prefix}ha ocurrido un error. El incidente ha sido registrado."
            )
        except Exception as e:
            logger.error(f"No se pudo enviar el mensaje de error al usuario: {e}")