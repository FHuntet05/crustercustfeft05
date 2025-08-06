import logging
import os
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError

from src.db.mongo_manager import db_instance
from src.helpers.utils import get_greeting, escape_html, sanitize_filename
from src.helpers.keyboards import build_download_quality_menu
from src.core import downloader
from . import processing_handler

logger = logging.getLogger(__name__)
FORWARD_CHAT_ID = os.getenv("FORWARD_CHAT_ID")

async def any_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: return

    if not FORWARD_CHAT_ID:
        await update.message.reply_html("❌ <b>Error de Configuración del Sistema:</b> La variable <code>FORWARD_CHAT_ID</code> no está definida en el entorno.")
        logger.critical("La variable de entorno FORWARD_CHAT_ID no está definida. Es necesario crear un canal privado.")
        return

    greeting_prefix = get_greeting(user.id)
    message = update.effective_message
    
    original_media_object, file_type = None, None
    if message.video: original_media_object, file_type = message.video, 'video'
    elif message.audio: original_media_object, file_type = message.audio, 'audio'
    elif message.document: original_media_object, file_type = message.document, 'document'
    
    if not original_media_object:
        logger.warning("any_file_handler recibió un mensaje sin un archivo adjunto procesable.")
        return

    try:
        logger.info(f"Realizando pase de testigo del mensaje {message.message_id} al canal de trabajo {FORWARD_CHAT_ID}")
        
        forwarded_message = await context.bot.forward_message(
            chat_id=FORWARD_CHAT_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )
        
        media_in_forwarded_msg = (
            forwarded_message.video or 
            forwarded_message.audio or 
            forwarded_message.document
        )

        if not media_in_forwarded_msg:
            raise TelegramError("El mensaje reenviado al canal de trabajo no contiene un medio válido.")

        final_file_name = sanitize_filename(getattr(media_in_forwarded_msg, 'file_name', "Archivo Sin Nombre"))
        final_file_size = media_in_forwarded_msg.file_size
        
        task_id = db_instance.add_task(
            user_id=user.id,
            file_type=file_type,
            file_name=final_file_name,
            file_size=final_file_size,
            forwarded_chat_id=forwarded_message.chat_id,
            forwarded_message_id=forwarded_message.message_id
        )

        if task_id:
            await message.reply_html(
                f"✅ {greeting_prefix}He recibido <code>{escape_html(final_file_name)}</code> y lo he añadido a su mesa de trabajo.\n\n"
                "Use /panel para ver y procesar sus tareas."
            )
        else:
            await message.reply_html(f"❌ {greeting_prefix}Hubo un error al registrar la tarea en la base de datos.")
            
    except TelegramError as e:
        logger.error(f"Fallo en el 'Pase de Testigo' debido a un error de Telegram: {e}")
        error_text = str(e)
        if "chat not found" in error_text:
            user_facing_error = "El canal de trabajo no fue encontrado. Verifique el <code>FORWARD_CHAT_ID</code> y que el bot sea administrador del canal."
        else:
            user_facing_error = f"Hubo un error crítico al transferir el archivo al procesador: {e}"
        await message.reply_html(f"❌ {greeting_prefix}{user_facing_error}")
    except Exception as e:
        logger.error(f"Fallo inesperado en el 'Pase de Testigo': {e}", exc_info=True)
        await message.reply_html(f"❌ {greeting_prefix}Hubo un error crítico desconocido al transferir el archivo.")


async def url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    text = (f"✅ {greeting_prefix}Enlace analizado:\n\n"
            f"<b>Título:</b> {escape_html(info['title'])}\n"
            f"<b>Canal:</b> {escape_html(info['uploader'])}\n\n"
            "Seleccione la calidad que desea descargar:")
    await status_message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # --- LÍNEA CRÍTICA CORREGIDA ---
    # Se comprueba primero que context.user_data no sea None antes de buscar la clave.
    # Esto evita el TypeError y hace que el bot ignore texto aleatorio sin crashear.
    if context.user_data and 'active_config' in context.user_data:
        config = context.user_data['active_config']
        user_input = update.message.text.strip()
        is_skip = user_input.lower() == "/skip"
        await processing_handler.handle_text_input(update, context, config, None if is_skip else user_input)