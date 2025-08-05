import logging
from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.utils import get_greeting, escape_html, sanitize_filename, parse_reply_markup
from src.helpers.keyboards import build_download_quality_menu, build_processing_menu
from src.core import downloader
from . import processing_handler # Importar para delegar

logger = logging.getLogger(__name__)

async def any_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manejador UNIFICADO para todos los archivos. Acepta cualquier archivo
    y lo añade al panel de trabajo para que el worker lo procese.
    """
    user = update.effective_user
    if not user:
        logger.warning("No se pudo obtener effective_user de la actualización.")
        return

    # Comprobar si hay una configuración activa que espera un archivo (para carátulas, etc.)
    if config := context.user_data.get('active_config'):
        if config.get('menu_type') == 'audiotags' and config.get('stage') == 'cover':
            await processing_handler.handle_cover_art_input(update, context, config)
            return
        if config.get('menu_type') == 'addtrack':
            await processing_handler.handle_track_input(update, context, config)
            return

    # Flujo normal: tratarlo como una nueva tarea para el panel
    greeting_prefix = get_greeting(user.id)
    message = update.effective_message
    
    file_obj, file_type = None, None
    
    if message.video:
        file_obj, file_type = message.video, 'video'
    elif message.audio:
        file_obj, file_type = message.audio, 'audio'
    elif message.photo:
        file_obj, file_type = message.photo[-1], 'document' 
    elif message.document:
        file_obj, file_type = message.document, 'document'
    
    if not file_obj:
        logger.warning("any_file_handler recibió un mensaje sin archivo adjunto válido.")
        return

    file_id = file_obj.file_id
    file_name = sanitize_filename(getattr(file_obj, 'file_name', "Archivo Sin Nombre"))
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

async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manejador genérico de texto que procesa la entrada del usuario según el menú de configuración activo.
    Delega la lógica a processing_handler.
    """
    if 'active_config' not in context.user_data:
        return
        
    config = context.user_data['active_config']
    user_input = update.message.text.strip()
    is_skip = user_input.lower() == "/skip"
    
    await processing_handler.handle_text_input(update, context, config, None if is_skip else user_input)