import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.utils import get_greeting, escape_html, sanitize_filename
from src.helpers.keyboards import build_download_quality_menu
from src.core import downloader
from . import processing_handler

logger = logging.getLogger(__name__)

async def any_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manejador UNIFICADO para todos los archivos. Acepta cualquier archivo
    y lo añade al panel de trabajo para que el worker lo procese.
    """
    user = update.effective_user
    if not user:
        return

    if config := context.user_data.get('active_config'):
        if config.get('menu_type') == 'audiotags' and config.get('stage') == 'cover':
            await processing_handler.handle_cover_art_input(update, context, config)
            return
        if config.get('menu_type') == 'addtrack':
            await processing_handler.handle_track_input(update, context, config)
            return

    greeting_prefix = get_greeting(user.id)
    message = update.effective_message
    
    file_obj, file_type = None, None
    
    if message.video: file_obj, file_type = message.video, 'video'
    elif message.audio: file_obj, file_type = message.audio, 'audio'
    elif message.photo: file_obj, file_type = message.photo[-1], 'document'
    elif message.document: file_obj, file_type = message.document, 'document'
    
    if not file_obj:
        logger.warning("any_file_handler recibió un mensaje sin archivo adjunto válido.")
        return

    # --- INGENIERÍA DE REFERENCIA ROBUSTA ---
    chat = message.chat
    if chat.username:
        message_url = f"https://t.me/{chat.username}/{message.message_id}"
    else:
        # Para chats privados, el ID empieza con -100, quitamos eso para el enlace
        chat_id_short = str(chat.id).replace("-100", "")
        message_url = f"https://t.me/c/{chat_id_short}/{message.message_id}"
    
    task_id = db_instance.add_task(
        user_id=user.id,
        file_type=file_type,
        file_id=file_obj.file_id,
        file_name=sanitize_filename(getattr(file_obj, 'file_name', "Archivo Sin Nombre")),
        file_size=file_obj.file_size,
        message_url=message_url # <-- GUARDAMOS LA URL
    )

    if task_id:
        await message.reply_html(
            f"✅ {greeting_prefix}He recibido <code>{escape_html(getattr(file_obj, 'file_name', 'archivo'))}</code> y lo he añadido a su mesa de trabajo.\n\n"
            "Use /panel para ver y procesar sus tareas."
        )
    else:
        await message.reply_html(f"❌ {greeting_prefix}Hubo un error al registrar el archivo.")


async def url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manejador para URLs. Analiza el enlace y presenta un menú de selección de calidad.
    """
    user = update.effective_user
    if not user: return
    
    greeting_prefix = get_greeting(user.id)
    url = update.message.text
    status_message = await update.message.reply_html(f"🔎 {greeting_prefix}Analizando enlace...")
    info = downloader.get_url_info(url)

    if not info:
        await status_message.edit_text(f"❌ {greeting_prefix}No pude obtener información de ese enlace.")
        return

    task_id = db_instance.add_task(
        user_id=user.id,
        file_type='video' if info['is_video'] else 'audio',
        url=info['url'],
        file_name=sanitize_filename(info['title']),
        processing_config={'url_info': info}
    )
    
    if not task_id:
        await status_message.edit_text(f"❌ {greeting_prefix}Error al crear la tarea en la DB.")
        return

    keyboard = build_download_quality_menu(str(task_id), info['formats'])
    text = (
        f"✅ {greeting_prefix}Enlace analizado:\n\n"
        f"<b>Título:</b> {escape_html(info['title'])}\n"
        f"<b>Canal:</b> {escape_html(info['uploader'])}\n\n"
        "Seleccione la calidad que desea descargar:"
    )
    await status_message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manejador de texto que delega a processing_handler si hay una configuración activa.
    """
    if 'active_config' in context.user_data:
        config = context.user_data['active_config']
        user_input = update.message.text.strip()
        is_skip = user_input.lower() == "/skip"
        await processing_handler.handle_text_input(update, context, config, None if is_skip else user_input)