import logging
import os
from telegram import Update
from telegram.ext import ContextTypes

from src.db.mongo_manager import db_instance
from src.helpers.utils import get_greeting, escape_html

logger = logging.getLogger(__name__)

async def any_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador que captura cualquier tipo de archivo enviado."""
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    
    message = update.effective_message
    
    if message.video:
        file_obj, file_type = message.video, 'video'
    elif message.audio:
        file_obj, file_type = message.audio, 'audio'
    elif message.document:
        file_obj, file_type = message.document, 'document'
    else:
        # Ignorar otros tipos que no queremos manejar
        return

    file_id = file_obj.file_id
    file_name = escape_html(file_obj.file_name) if file_obj.file_name else "Archivo Sin Nombre"
    file_size = file_obj.file_size
    
    # Guardar la tarea en la base de datos
    success = db_instance.add_task(user.id, file_id, file_name, file_size, file_type)
    
    if success:
        await message.reply_html(
            f"✅ {greeting_prefix}he recibido <code>{file_name}</code> y lo he añadido a su mesa de trabajo.\n\n"
            "Use /panel para ver y gestionar sus tareas."
        )
    else:
        await message.reply_html(
            f"❌ Lo siento, {greeting_prefix}hubo un error al registrar el archivo en la base de datos."
        )