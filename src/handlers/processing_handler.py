import logging
from telegram import Update
from telegram.ext import ContextTypes

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_back_button, build_processing_menu
from src.helpers.utils import get_greeting, escape_html

logger = logging.getLogger(__name__)

async def show_config_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: str, menu_type: str):
    """Función genérica para mostrar un menú de configuración y pedir entrada al usuario."""
    query = update.callback_query
    greeting_prefix = get_greeting(query.from_user.id)
    
    task = db_instance.get_task(task_id)
    if not task:
        await query.edit_message_text("❌ Error: Tarea no encontrada.", reply_markup=None)
        return

    # Guardar en el contexto qué estamos configurando y para qué tarea
    context.user_data['active_config'] = {
        "task_id": task_id,
        "menu_type": menu_type
    }
    
    # Textos personalizados para cada menú
    menu_texts = {
        "rename": (
            f"✏️ <b>Renombrar Archivo</b>\n\n"
            f"{greeting_prefix}envíeme el nuevo nombre para <code>{escape_html(task['original_filename'])}</code>.\n"
            f"<i>No incluya la extensión del archivo.</i>"
        ),
        "trim": (
            f"✂️ <b>Cortar Video</b>\n\n"
            f"{greeting_prefix}envíeme el tiempo de inicio y fin en formato <code>MM:SS-MM:SS</code> o solo <code>-MM:SS</code> para cortar desde el inicio."
        ),
        "split": (
            f"🧩 <b>Dividir Video</b>\n\n"
            f"{greeting_prefix}envíeme el criterio de división: por tiempo (ej. <code>300s</code>) o por tamaño (ej. <code>50MB</code>)."
        ),
        "zip": (
            f"📦 <b>Comprimir en ZIP</b>\n\n"
            f"{greeting_prefix}envíeme la contraseña para el archivo ZIP, o envíe <code>no</code> para crearlo sin contraseña."
        ),
        "screenshot": (
            f"📸 <b>Capturas de Pantalla</b>\n\n"
            f"{greeting_prefix}envíeme los timestamps de las capturas, separados por comas (ej. <code>00:10, 01:25, 50%</code>)."
        )
    }
    
    text = menu_texts.get(menu_type, "Configuración no reconocida.")
    keyboard = build_back_button(f"task_process_{task_id}")
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador genérico de texto que procesa la entrada según el menú de configuración activo."""
    if 'active_config' not in context.user_data:
        return
        
    config_info = context.user_data.pop('active_config')
    task_id = config_info['task_id']
    menu_type = config_info['menu_type']
    user_input = update.message.text

    feedback_message = ""

    # Lógica de procesamiento para cada tipo de configuración
    if menu_type == "rename":
        if "/" in user_input or "\\" in user_input or len(user_input) > 100:
            feedback_message = "❌ Nombre inválido."
        else:
            db_instance.update_task(task_id, "final_filename", user_input)
            feedback_message = f"✅ Nombre actualizado a <code>{escape_html(user_input)}</code>."
            
    elif menu_type == "trim":
        db_instance.update_task_config(task_id, "trim_times", user_input)
        feedback_message = f"✅ Tiempo de corte establecido en: <code>{escape_html(user_input)}</code>."

    elif menu_type == "split":
        db_instance.update_task_config(task_id, "split_criteria", user_input)
        feedback_message = f"✅ Criterio de división establecido en: <code>{escape_html(user_input)}</code>."

    elif menu_type == "zip":
        password = user_input if user_input.lower() not in ['no', 'none'] else None
        db_instance.update_task_config(task_id, "zip_password", password)
        feedback_message = "✅ Tarea configurada para ser comprimida en ZIP."
        
    elif menu_type == "screenshot":
        db_instance.update_task_config(task_id, "screenshot_points", user_input)
        feedback_message = f"✅ Puntos de captura establecidos."

    await update.message.reply_html(feedback_message)

    # Devolver al menú de procesamiento para continuar configurando
    task = db_instance.get_task(task_id)
    if task:
        keyboard = build_processing_menu(str(task['_id']), task['file_type'], task.get('processing_config', {}))
        await update.message.reply_html(f"¿Qué más desea hacer con <code>{escape_html(task.get('original_filename', '...'))}</code>?", reply_markup=keyboard)