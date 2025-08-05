import logging
from telegram import Update
from telegram.ext import ContextTypes

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_back_button, build_processing_menu
from src.helpers.utils import get_greeting, escape_html

logger = logging.getLogger(__name__)

# Estados para la conversación
STATE_RENAME = 1

async def show_rename_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: str):
    """Pide al usuario que envíe el nuevo nombre para el archivo."""
    query = update.callback_query
    greeting_prefix = get_greeting(query.from_user.id)
    
    task = db_instance.get_task(task_id)
    if not task:
        await query.edit_message_text("❌ Error: Tarea no encontrada.", reply_markup=None)
        return
        
    # Guardamos el ID de la tarea en el contexto del usuario para saber a qué tarea aplicar el nombre
    context.user_data['renaming_task_id'] = task_id
    
    text = (
        f"✏️ <b>Renombrar Archivo</b>\n\n"
        f"{greeting_prefix}envíeme ahora el nuevo nombre para <code>{escape_html(task['original_filename'])}</code>.\n\n"
        f"<i>No incluya la extensión del archivo. Puede usar /cancel para abortar.</i>"
    )
    keyboard = build_back_button(f"process_{task_id}")
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')
    
    return STATE_RENAME # Entramos en el estado de espera de texto


async def process_rename_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el texto del nuevo nombre y lo guarda en la DB."""
    user_id = update.effective_user.id
    task_id = context.user_data.get('renaming_task_id')
    new_name = update.message.text
    
    if not task_id:
        # Si no estábamos esperando un renombrado, ignoramos el mensaje
        return
    
    # Limpiar el contexto
    del context.user_data['renaming_task_id']

    # Validar el nombre (simple, por ahora)
    if "/" in new_name or "\\" in new_name:
        await update.message.reply_html("❌ Nombre inválido. No puede contener <code>/</code> o <code>\\</code>.")
        # Devolver al menú de procesamiento
        task = db_instance.get_task(task_id)
        keyboard = build_processing_menu(task_id, task['file_type'])
        await update.message.reply_html(f"Seleccione una opción para <code>{escape_html(task['original_filename'])}</code>", reply_markup=keyboard)
        return

    # Actualizar el nombre en la base de datos
    db_instance.tasks.update_one({"_id": task_id}, {"$set": {"final_filename": new_name}})
    
    await update.message.reply_html(f"✅ Nombre actualizado a <code>{escape_html(new_name)}</code>.")
    
    # Devolver al menú de procesamiento
    task = db_instance.get_task(task_id)
    keyboard = build_processing_menu(task_id, task['file_type'])
    await update.message.reply_html(f"¿Qué más desea hacer con <code>{escape_html(task['original_filename'])}</code>?", reply_markup=keyboard)
    
    return -1 # Finalizar la conversación


async def cancel_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela la operación de renombrado."""
    if 'renaming_task_id' in context.user_data:
        del context.user_data['renaming_task_id']
    await update.message.reply_html("Operación de renombrado cancelada.")
    return -1


async def show_unimplemented_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: str, feature_name: str):
    """Placeholder genérico para funciones no implementadas."""
    query = update.callback_query
    
    text = (
        f"🛠️ <b>{feature_name}</b>\n\n"
        f"<i>Esta función aún no ha sido implementada.</i>"
    )
    keyboard = build_back_button(f"process_{task_id}")
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')