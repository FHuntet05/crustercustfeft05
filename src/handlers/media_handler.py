import logging
from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.utils import get_greeting, escape_html, sanitize_filename
from src.helpers.keyboards import build_download_quality_menu
from src.core import downloader

logger = logging.getLogger(__name__)

async def any_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manejador que captura cualquier tipo de archivo enviado (video, audio, documento)
    y lo añade a la mesa de trabajo del usuario en la base de datos.
    """
    if not update.effective_user:
        logger.warning("No se pudo obtener effective_user de la actualización.")
        return
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    message = update.effective_message
    
    file_obj, file_type = None, None
    
    if message.video:
        file_obj, file_type = message.video, 'video'
    elif message.audio:
        file_obj, file_type = message.audio, 'audio'
    elif message.document:
        file_obj, file_type = message.document, 'document'
    else:
        logger.warning("any_file_handler recibió un mensaje sin archivo adjunto válido.")
        return

    if file_obj:
        file_id = file_obj.file_id
        file_name = sanitize_filename(file_obj.file_name) if file_obj.file_name else "Archivo Sin Nombre"
        file_size = file_obj.file_size
    
        task_id = db_instance.add_task(
            user_id=user.id,
            file_type=file_type,
            file_id=file_id,
            file_name=file_name,
            file_size=file_size
        )
    
        if task_id:
            await message.reply_html(
                f"✅ {greeting_prefix}He recibido <code>{escape_html(file_name)}</code> y lo he añadido a su mesa de trabajo.\n\n"
                "Use /panel para ver y procesar sus tareas."
            )
        else:
            await message.reply_html(f"❌ {greeting_prefix}Hubo un error al registrar el archivo en la base de datos.")

async def url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manejador para URLs. Analiza el enlace con yt-dlp y, si es válido,
    presenta un menú de selección de calidad para la descarga.
    """
    if not update.effective_user:
        logger.warning("No se pudo obtener effective_user de la actualización de URL.")
        return
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    url = update.message.text

    status_message = await update.message.reply_html(f"🔎 {greeting_prefix}Analizando enlace...")

    info = downloader.get_url_info(url)

    if not info:
        await status_message.edit_text(f"❌ {greeting_prefix}Lo siento, no pude obtener información de ese enlace. "
                                       "Puede que no sea compatible o que el servicio esté caído.")
        return

    task_id = db_instance.add_task(
        user_id=user.id,
        file_type='video' if info['is_video'] else 'audio',
        url=info['url'],
        file_name=sanitize_filename(info['title']),
        processing_config={'url_info': info}
    )
    
    if not task_id:
        await status_message.edit_text(f"❌ {greeting_prefix}Hubo un error al crear la tarea en la base de datos.")
        return

    keyboard = build_download_quality_menu(str(task_id), info['formats'])
    
    title = escape_html(info['title'])
    uploader = escape_html(info['uploader'])
    
    response_text = (
        f"✅ {greeting_prefix}Enlace analizado:\n\n"
        f"<b>Título:</b> {title}\n"
        f"<b>Canal:</b> {uploader}\n\n"
        "Seleccione la calidad que desea descargar:"
    )

    await status_message.edit_text(
        response_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )