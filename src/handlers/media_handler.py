import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import MessageType
from src.db.mongo_manager import db_instance # Importamos nuestra instancia de DB

logger = logging.getLogger(__name__)
ADMIN_USER_ID = int(db_instance.client.admin.command('ping') and os.getenv("ADMIN_USER_ID")) # Esto es una forma de obtener la variable desde aquí

def format_bytes(size):
    """Formatea bytes a un formato legible (KB, MB, GB)."""
    if size is None:
        return "N/A"
    power = 1024
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power and n < len(power_labels):
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"


async def any_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador que captura cualquier tipo de archivo enviado."""
    user = update.effective_user
    
    # Determinar el tipo de archivo y obtener sus propiedades
    if update.message.video:
        file_obj = update.message.video
        file_type = 'video'
    elif update.message.audio:
        file_obj = update.message.audio
        file_type = 'audio'
    elif update.message.document:
        file_obj = update.message.document
        file_type = 'document'
    else:
        # Ignorar otros tipos de mensajes por ahora
        return

    file_id = file_obj.file_id
    file_name = file_obj.file_name
    file_size = file_obj.file_size
    
    # Guardar la tarea en la base de datos
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
        response_text += f"**Video {i+1}:** `{file_name}` ({file_size})\n"
        response_text += f"**[ 🎬 Procesar ] [ 🗑️ Descartar ]**\n\n" # Por ahora texto, luego serán botones
    
    # Por ahora enviamos el texto plano. En la siguiente misión añadiremos botones.
    await update.message.reply_markdown(response_text)