import logging
import os
from telegram import Update
from telegram.ext import ContextTypes
from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_panel_keyboard # Importamos nuestro generador de teclados

logger = logging.getLogger(__name__)

try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    logger.critical("ADMIN_USER_ID no está definido o no es válido. Saliendo.")
    exit()

def format_bytes(size):
    """Formatea bytes a un formato legible (KB, MB, GB)."""
    if size is None: return "N/A"
    power = 1024
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size >= power and n < len(power_labels) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"

async def any_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador que captura cualquier tipo de archivo enviado."""
    user = update.effective_user
    
    if update.message.video:
        file_obj, file_type = update.message.video, 'video'
    elif update.message.audio:
        file_obj, file_type = update.message.audio, 'audio'
    elif update.message.document:
        file_obj, file_type = update.message.document, 'document'
    else:
        return

    file_id = file_obj.file_id
    file_name = file_obj.file_name
    file_size = file_obj.file_size
    
    success = db_instance.add_task(user.id, file_id, file_name, file_size, file_type)
    
    greeting = "Jefe, he" if user.id == ADMIN_USER_ID else "He"
    
    if success:
        await update.message.reply_html(
            f"✅ {greeting} recibido <code>{file_name}</code> y lo he añadido a su mesa de trabajo.\n\n"
            f"Use /panel para ver sus tareas pendientes."
        )
    else:
        await update.message.reply_html(
            f"❌ Lo siento, Jefe. Hubo un error al registrar el archivo en la base de datos."
        )

async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador para el comando /panel, ahora con botones."""
    user = update.effective_user
    pending_tasks = db_instance.get_pending_tasks(user.id)
    
    greeting = "Jefe, esta es su" if user.id == ADMIN_USER_ID else "Esta es tu"
    
    if not pending_tasks:
        await update.message.reply_html(f"✅ ¡{greeting} mesa de trabajo está vacía!")
        return
        
    # Construir el teclado de botones a partir de las tareas
    keyboard = build_panel_keyboard(pending_tasks)
    
    response_text = f"📋 **{greeting} mesa de trabajo actual:**\n\n"
    response_text += "Seleccione una acción para cada tarea o use los botones globales."

    # Enviar el mensaje con el teclado adjunto. Usamos Markdown por el formato de **.
    await update.message.reply_markdown_v2(response_text, reply_markup=keyboard)