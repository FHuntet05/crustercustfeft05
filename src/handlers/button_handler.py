import logging
from telegram import Update
from telegram.ext import ContextTypes

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_processing_menu
from src.helpers.utils import get_greeting, escape_html
from . import processing_handler, command_handler

logger = logging.getLogger(__name__)

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja TODAS las pulsaciones de botones inline y delega a otros módulos."""
    query = update.callback_query
    await query.answer()

    data = query.data
    action, _, payload = data.partition('_')
    user_id = query.from_user.id
    
    # --- Acciones del Panel Principal ---
    if action == "delete":
        if db_instance.delete_task(payload, user_id):
            await query.edit_message_text("🗑️ Tarea descartada con éxito.")
            # Opcional: Refrescar el panel
            # await command_handler.panel_command(update, context) 
        else:
            await query.edit_message_text("❌ Error al descartar la tarea.")

    elif data == "delete_all":
        count = db_instance.delete_all_pending(user_id)
        await query.edit_message_text(f"💥 Limpieza completada. Se descartaron {count} tareas.")

    elif action == "process":
        task_id = payload
        task = db_instance.get_task(task_id)
        if not task:
            await query.edit_message_text("❌ Error: Tarea no encontrada.")
            return

        keyboard = build_processing_menu(task_id, task['file_type'])
        text = (
            f"🛠️ {get_greeting(user_id)}¿qué desea hacer con:\n"
            f"<code>{escape_html(task['original_filename'])}</code>?"
        )
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')

    # --- Acciones del Menú de Procesamiento ---
    elif action == "config":
        action_type = payload.split('_')[0]
        task_id = payload.split('_', 1)[1]

        if action_type == "rename":
            await processing_handler.show_rename_menu(update, context, task_id)
        else:
            # Placeholder para todas las demás funciones
            feature_map = {
                "convert": "Optimizar/Convertir",
                "trim": "Cortar (Trimmer)",
                "watermark": "Marca de Agua",
                "subs": "Subtítulos",
                "screenshot": "Capturas",
                "audio": "Funciones de Audio" # Genérico
            }
            feature_name = "Función Desconocida"
            for key, name in feature_map.items():
                if key in action_type:
                    feature_name = name
                    break
            await processing_handler.show_unimplemented_menu(update, context, task_id, feature_name)
    
    elif action == "queue":
        task_id = payload
        if db_instance.update_task_status(task_id, "queued"):
            await query.edit_message_text("✅ ¡Entendido! La tarea ha sido enviada a la cola de procesamiento.")
        else:
            await query.edit_message_text("❌ Error al encolar la tarea.")

    elif data == "back_to_panel":
        # Simulamos una llamada al comando /panel para refrescar la vista
        query.message.text = "/panel" # Modificamos el mensaje para que parezca un comando
        await command_handler.panel_command(query, context)
        await query.delete_message() # Borramos el mensaje del menú de procesamiento

    else:
        await query.edit_message_text("🤔 Acción desconocida o no implementada.")