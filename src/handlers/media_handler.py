import logging
from telegram import Update
from telegram.ext import ContextTypes

from src.db.mongo_manager import db_instance
from src.helpers.utils import get_greeting, escape_html

logger = logging.getLogger(__name__)

async def any_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador que captura cualquier tipo de archivo enviado y lo añade a la mesa de trabajo."""
    # --- CORRECCIÓN ---
    # Obtenemos el usuario de forma segura desde el objeto 'update'
    if not update.effective_user:
        logger.warning("No se pudo obtener effective_user de la actualización.")
        return
    user = update.effective_user
    
    greeting_prefix = get_greeting(user.id)
    message = update.effective_message
    
    file_obj, file_type, file_name, file_size = None, None, None, None
    
    if message.video: file_obj, file_type = message.video, 'video'
    elif message.audio: file_obj, file_type = message.audio, 'audio'
    elif message.document: file_obj, file_type = message.document, 'document'
    else: return

    if file_obj:
        file_id = file_obj.file_id
        file_name = escape_html(file_obj.file_name) if file_obj.file_name else "Archivo Sin Nombre"
        file_size = file_obj.file_size
    
    # Pasamos user.id directamente
    success = db_instance.add_task(user_id=user.id, file_type=file_type, file_id=file_id, file_name=file_name, file_size=file_size)
    
    if success:
        await message.reply_html(
            f"✅ {greeting_prefix}He recibido <code>{file_name}</code> y lo he añadido a su mesa de trabajo.\n\n"
            "Use /panel para ver sus tareas."
        )
    else:
        await message.reply_html(f"❌ {greeting_prefix}Hubo un error al registrar el archivo.")

async def url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador para URLs. Añade una tarea de tipo URL a la mesa de trabajo."""
    # --- CORRECCIÓN ---
    if not update.effective_user:
        logger.warning("No se pudo obtener effective_user de la actualización de URL.")
        return
    user = update.effective_user

    greeting_prefix = get_greeting(user.id)
    url = update.message.text
    
    # Por ahora, asumimos que es un video genérico
    success = db_instance.add_task(user_id=user.id, file_type='video', url=url, file_name=url)

    if success:
        await update.message.reply_html(
            f"🔗 {greeting_prefix}He recibido la URL y la he añadido a su mesa de trabajo.\n\n"
            "Vaya a /panel para procesarla."
        )
    else:
        await update.message.reply_html(f"❌ {greeting_prefix}Hubo un error al registrar la URL.")