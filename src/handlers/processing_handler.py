import logging
from telegram import Update
from telegram.ext import ContextTypes

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_back_button, build_processing_menu
from src.helpers.utils import get_greeting, escape_html

logger = logging.getLogger(__name__)

async def show_rename_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: str):
    """Pide al usuario que envíe el nuevo nombre para el archivo."""
    query = update.callback_query
    greeting_prefix = get_greeting(query.from_user.id)
    
    task = db_instance.get_task(task_id)
    if not task:
        await query.edit_message_text("❌ Error: Tarea no encontrada.", reply_markup=None)
        return
        
    # Guardamos el ID de la tarea en el contexto para saber a qué tarea aplicar el nombre
    context.user_data['renaming_task_id'] = task_id
    
    text = (
        f"✏️ <b>Renombrar Archivo</b>\n\n"
        f"{greeting_prefix}envíeme ahora el nuevo nombre para <code>{escape_html(task['original_filename'])}</code>.\n\n"
        f"<i>No incluya la extensión del archivo.</i>"
    )
    keyboard = build_back_button(f"process_{task_id}")
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador genérico de texto que comprueba si estamos esperando un renombrado."""
    user_id = update.effective_user.id
    task_id_to_rename = context.user_data.get('renaming_task_id')

    if task_id_to_rename:
        # Si estábamos esperando un nombre, lo procesamos
        new_name = update.message.text
        
        # Limpiar el contexto para no volver a entrar aquí
        del context.user_data['renaming_task_id']

        # Validar el nombre
        if "/" in new_name or "\\" in new_name or len(new_name) > 100:
            await update.message.reply_html("❌ Nombre inválido. No puede contener <code>/</code>, <code>\\</code> o ser muy largo.")
        else:
            # Actualizar el nombre en la base de datos
            db_instance.tasks.update_one({"_id": task_id_to_rename}, {"$set": {"final_filename": new_name}})
            await update.message.reply_html(f"✅ Nombre actualizado a <code>{escape_html(new_name)}</code>.")
        
        # Devolver al menú de procesamiento de esa tarea
        task = db_instance.get_task(task_id_to_rename)
        if task:
            keyboard = build_processing_menu(str(task['_id']), task['file_type'])
            await update.message.reply_html(f"¿Qué más desea hacer con <code>{escape_html(task['original_filename'])}</code>?", reply_markup=keyboard)
    else:
        # Si no esperábamos texto, podríamos responder algo o simplemente ignorarlo
        await update.message.reply_html("🤔 No estoy seguro de qué hacer con eso. Use /panel para ver sus tareas.")


async def show_unimplemented_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: str, feature_name: str):
    """Placeholder genérico para funciones no implementadas."""
    query = update.callback_query
    
    text = (
        f"🛠️ <b>{feature_name}</b>\n\n"
        f"<i>Esta función aún no ha sido implementada.</i>"
    )
    keyboard = build_back_button(f"process_{task_id}")
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')