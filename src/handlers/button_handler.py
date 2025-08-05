import logging
from telegram import Update
from telegram.ext import ContextTypes

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_processing_menu, build_quality_menu, build_watermark_menu
from src.helpers.utils import get_greeting, escape_html
from . import processing_handler, command_handler

logger = logging.getLogger(__name__)

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja TODAS las pulsaciones de botones inline y delega a otros módulos."""
    query = update.callback_query
    await query.answer()

    data = query.data
    action, _, payload = data.partition('_')
    
    # =================================================================
    # 1. ACCIONES DEL PANEL PRINCIPAL (/panel)
    # =================================================================
    if action == "panel":
        if payload == "delete_all":
            count = db_instance.delete_all_pending(query.from_user.id)
            await query.edit_message_text(f"💥 Limpieza completada. Se descartaron {count} tareas.")
        elif payload == "show":
            await command_handler.panel_command(update, context)

    # =================================================================
    # 2. ACCIONES SOBRE TAREAS
    # =================================================================
    elif action == "task":
        action_type, task_id = payload.split('_', 1)

        if action_type == "process":
            task = db_instance.get_task(task_id)
            if not task:
                await query.edit_message_text("❌ Error: Tarea no encontrada.")
                return

            keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}))
            text = (f"🛠️ {get_greeting(query.from_user.id)}¿Qué desea hacer con:\n"
                    f"<code>{escape_html(task.get('original_filename', '...'))}</code>?")
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')
        
        elif action_type == "queue":
            if db_instance.update_task(task_id, "status", "queued"):
                await query.edit_message_text("✅ ¡Entendido! La tarea ha sido enviada a la cola de procesamiento.")
            else:
                await query.edit_message_text("❌ Error al encolar la tarea.")

    # =================================================================
    # 3. ACCIONES DEL MENÚ DE CONFIGURACIÓN
    # =================================================================
    elif action == "config":
        action_type, task_id = payload.split('_', 1)
        
        # Menús que requieren entrada de texto
        if action_type in ["rename", "trim", "split", "zip", "screenshot"]:
            await processing_handler.show_config_menu(update, context, task_id, action_type)
        
        # Menús con botones de selección
        elif action_type == "quality":
            keyboard = build_quality_menu(task_id)
            await query.edit_message_text("⚙️ Seleccione el perfil de calidad:", reply_markup=keyboard)
        
        elif action_type == "watermark":
            task = db_instance.get_task(task_id)
            is_enabled = task.get('processing_config', {}).get('watermark_enabled', False)
            keyboard = build_watermark_menu(task_id, is_enabled)
            await query.edit_message_text("💧 Configure la marca de agua:", reply_markup=keyboard)

        elif action_type == "mute": # Acción directa, sin submenú
            db_instance.update_task_config(task_id, "mute_audio", True)
            await query.edit_message_text("🔇 Audio será eliminado. Vuelva al menú para ver el cambio.")
            # Idealmente se refresca el menú aquí
            
        else: # Placeholder para funciones no implementadas
             await query.edit_message_text(f"<i>Función '{action_type}' no implementada.</i>", parse_mode='HTML')

    # =================================================================
    # 4. ACCIONES DE SELECCIÓN (SETTERS)
    # =================================================================
    elif action == "set":
        try:
            _, config_type, task_id, value = data.split('_', 3)
            
            if config_type == "quality":
                db_instance.update_task_config(task_id, "quality", value)
            
            # Refrescar el menú de procesamiento para mostrar el cambio
            task = db_instance.get_task(task_id)
            if task:
                keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}))
                await query.edit_message_text(
                    f"🛠️ Configuración actualizada. ¿Algo más?", reply_markup=keyboard, parse_mode='HTML'
                )
        except ValueError:
            # Caso especial para el toggle de la marca de agua: set_watermark_TASKID_toggle
            _, config_type, task_id, value = data.split('_', 3)
            if config_type == "watermark" and value == "toggle":
                task = db_instance.get_task(task_id)
                is_enabled = task.get('processing_config', {}).get('watermark_enabled', False)
                db_instance.update_task_config(task_id, "watermark_enabled", not is_enabled)
                
                keyboard = build_watermark_menu(task_id, not is_enabled)
                await query.edit_message_text("💧 Configure la marca de agua:", reply_markup=keyboard)

    else:
        logger.warning(f"Callback desconocido recibido: {data}")