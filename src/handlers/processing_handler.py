import logging
from telegram import Update
from telegram.ext import ContextTypes

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_back_button
from src.helpers.utils import get_greeting, escape_html

logger = logging.getLogger(__name__)

async def show_rename_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: str):
    """Muestra el menú para renombrar un archivo (placeholder)."""
    query = update.callback_query
    greeting_prefix = get_greeting(query.from_user.id)
    
    task = db_instance.get_task(task_id)
    if not task:
        await query.edit_message_text("❌ Error: Tarea no encontrada.", reply_markup=None)
        return
        
    text = (
        f"✏️ <b>Renombrar Archivo</b>\n\n"
        f"{greeting_prefix}envíeme el nuevo nombre para <code>{escape_html(task['original_filename'])}</code>.\n"
        f"No incluya la extensión del archivo."
    )
    keyboard = build_back_button(f"process_{task_id}")
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')


async def show_unimplemented_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: str, feature_name: str):
    """Placeholder genérico para funciones no implementadas."""
    query = update.callback_query
    
    text = (
        f"🛠️ <b>{feature_name}</b>\n\n"
        f"<i>Esta función aún no ha sido implementada.</i>"
    )
    keyboard = build_back_button(f"process_{task_id}")
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')