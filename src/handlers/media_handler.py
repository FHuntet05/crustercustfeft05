import logging
import os # Importamos os
from telegram import Update
from telegram.ext import ContextTypes
from src.db.mongo_manager import db_instance

logger = logging.getLogger(__name__)

# --- Obtenemos el ADMIN_USER_ID de forma segura ---
# Lo leemos una vez al cargar el módulo, después de que .env ya haya sido cargado por mongo_manager
try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    logger.critical("ADMIN_USER_ID no está definido en el archivo .env o no es un número válido. Saliendo.")
    exit() # Detiene el bot si el ID del admin no es válido.

# ... La función format_bytes se mantiene igual ...
def format_bytes(size):
    """Formatea bytes a un formato legible (KB, MB, GB)."""
    if size is None: return "N/A"
    power = 1024
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size >= power and n < len(power_labels) -1 :
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"

# ... El resto del archivo se mantiene igual ...
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
            f"✅ {greeting} recibido <code>{file_name}</code> y lo he añadido a su mesa de trabajo.\n"
            f"Use /panel para ver sus tareas pendientes."
        )
    else:
        await update.message.reply_html(
            f"❌ Lo siento, Jefe. Hubo un error al registrar el archivo en la base de datos."
        )

async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador para el comando /panel."""
    user = update.effective_user
    pending_tasks = db_instance.get_pending_tasks(user.id)
    
    greeting = "Jefe, esta es su" if user.id == ADMIN_USER_ID else "Esta es tu"
    
    if not pending_tasks:
        await update.message.reply_html(f"✅ {greeting} mesa de trabajo está vacía.")
        return
        
    response_text = f"📋 **{greeting} mesa de trabajo actual:**\n\n"
    
    for i, task in enumerate(pending_tasks):
        file_name = task.get('file_name', 'Nombre desconocido')
        file_size = format_bytes(task.get('file_size'))
        response_text += f"**Tarea {i+1}:** `{file_name}` ({file_size})\n"
        response_text += f"**[ 🎬 Procesar ] [ 🗑️ Descartar ]**\n\n"
    
    await update.message.reply_markdown(response_text)