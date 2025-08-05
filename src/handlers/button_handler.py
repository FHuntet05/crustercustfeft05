import logging
import os
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import (build_processing_menu, build_quality_menu, build_tracks_menu,
                                   build_audio_convert_menu, build_audio_effects_menu, build_bulk_actions_menu)
from src.helpers.utils import get_greeting, escape_html
from . import processing_handler, command_handler

logger = logging.getLogger(__name__)
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja TODAS las pulsaciones de botones inline y delega a otros módulos."""
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split('_')
    action = parts[0]
    
    # =================================================================
    # 0. NOOP (No Operation) - para botones que no hacen nada
    # =================================================================
    if action == "noop":
        return

    # =================================================================
    # 1. ACCIONES DEL PANEL PRINCIPAL (/panel)
    # =================================================================
    elif action == "panel":
        payload = parts[1]
        if payload == "delete_all":
            count = db_instance.delete_all_pending(query.from_user.id)
            await query.edit_message_text(f"💥 Limpieza completada. Se descartaron {count} tareas.")
        elif payload == "show":
            await command_handler.panel_command(update, context, is_callback=True)

    # =================================================================
    # 2. ACCIONES SOBRE TAREAS INDIVIDUALES
    # =================================================================
    elif action == "task":
        action_type, task_id = parts[1], parts[2]

        task = db_instance.get_task(task_id)
        if not task:
            await query.edit_message_text("❌ Error: La tarea ya no existe.", reply_markup=None)
            return

        if action_type == "process":
            keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
            text = (f"🛠️ {get_greeting(query.from_user.id)}¿Qué desea hacer con:\n"
                    f"<code>{escape_html(task.get('original_filename', '...'))}</code>?")
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        
        elif action_type == "queue":
            db_instance.update_task(task_id, "status", "queued")
            await query.edit_message_text("✅ ¡Entendido! La tarea ha sido enviada a la cola de procesamiento.")
            
        elif action_type == "delete":
            if db_instance.delete_task(task_id, query.from_user.id):
                 await query.edit_message_text("🗑️ Tarea descartada.")
            else:
                 await query.edit_message_text("❌ No se pudo descartar la tarea.")


    # =================================================================
    # 3. MENÚS DE CONFIGURACIÓN
    # =================================================================
    elif action == "config":
        action_type = parts[1]
        task_id = parts[2]
        
        # Menús que requieren entrada de texto
        if action_type in ["rename", "trim", "split", "gif", "screenshot", "caption", "addtrack",
                           "audiotrim", "audiotags", "audioeffect"]:
            await processing_handler.show_config_menu(update, context, task_id, action_type, payload=parts[3] if len(parts) > 3 else None)
        
        # Menús de Video
        elif action_type == "quality":
            keyboard = build_quality_menu(task_id)
            await query.edit_message_text("⚙️ Seleccione el perfil de calidad/conversión:", reply_markup=keyboard)
        
        # ... (Otros menús de video y audio) ...

    # =================================================================
    # 4. SETTERS (ACCIONES DIRECTAS DE BOTONES)
    # =================================================================
    elif action == "set":
        config_type, task_id = parts[1], parts[2]
        value = parts[3] if len(parts) > 3 else None
            
        task = db_instance.get_task(task_id)
        if not task:
            await query.edit_message_text("❌ Error: La tarea ya no existe.", reply_markup=None)
            return

        if config_type == "quality":
            db_instance.update_task_config(task_id, "quality", value)
        
        elif config_type == "mute" and value == "toggle":
            current_mute = task.get('processing_config', {}).get('mute_audio', False)
            db_instance.update_task_config(task_id, "mute_audio", not current_mute)
        
        elif config_type == "extract":
            db_instance.update_task_config(task_id, "extract_archive", value == "true")

        # --- Setters de Audio ---
        elif config_type == "audioprop":
            prop_name, prop_value = parts[3], parts[4]
            db_instance.update_task_config(task_id, f"audio_{prop_name}", prop_value)
        
        # ... (Otros setters) ...
        
        # Refrescar el menú principal de la tarea
        task = db_instance.get_task(task_id) # Recargar la tarea para obtener la config actualizada
        keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
        await query.edit_message_text(
            f"🛠️ Configuración actualizada. ¿Algo más con <code>{escape_html(task.get('original_filename'))}</code>?",
            reply_markup=keyboard, parse_mode=ParseMode.HTML
        )

    # =================================================================
    # 5. SETTER DE FORMATO DE DESCARGA (DESDE URL)
    # =================================================================
    elif action == "set" and parts[1] == "dlformat":
        _, _, task_id, format_id = parts
        db_instance.update_task_config(task_id, "download_format_id", format_id)
        db_instance.update_task(task_id, "status", "queued")
        await query.edit_message_text(f"✅ ¡Entendido! He enviado la descarga con la calidad seleccionada a la cola.")

    # =================================================================
    # 6. ACCIONES EN LOTE (BULK)
    # =================================================================
    elif action == "bulk":
        action_type = parts[1]
        task_ids_str = parts[2] if len(parts) > 2 else ''
        task_ids = task_ids_str.split(',')

        if action_type == "start":
            keyboard = build_bulk_actions_menu(task_ids_str)
            await query.edit_message_text(f"✨ <b>Modo Bulk</b>\n\nJefe, ha seleccionado {len(task_ids)} tareas. ¿Qué acción desea realizar en lote?",
                                          reply_markup=keyboard, parse_mode=ParseMode.HTML)
        
        elif action_type == "action":
            bulk_op = parts[2]
            task_ids_str = parts[3]
            task_ids = task_ids_str.split(',')

            if bulk_op == "convert720p":
                for tid in task_ids:
                    task_to_update = db_instance.get_task(tid)
                    if task_to_update and task_to_update.get('file_type') == 'video':
                         db_instance.update_task_config(tid, "quality", "720p")
                
                count = db_instance.update_many_tasks_status(task_ids, "queued")
                await query.edit_message_text(f"✅ {count} tareas de video encoladas para conversión a 720p.")
            
            elif bulk_op == "rename":
                await processing_handler.show_config_menu(update, context, task_ids_str, "bulkrename")
            
            elif bulk_op == "zip":
                new_task_id = db_instance.add_task(user_id=query.from_user.id, file_type="document", special_type="zip_bulk", file_name="Archivo-Bulk.zip")
                db_instance.update_task_config(str(new_task_id), "source_task_ids", task_ids)
                db_instance.update_task(str(new_task_id), "status", "queued")
                await query.edit_message_text("✅ Tarea de compresión en lote creada y encolada.")
            
            elif bulk_op == "unify":
                new_task_id = db_instance.add_task(user_id=query.from_user.id, file_type="video", special_type="unify_videos", file_name="Video-Unificado.mp4")
                db_instance.update_task_config(str(new_task_id), "source_task_ids", task_ids)
                db_instance.update_task(str(new_task_id), "status", "queued")
                await query.edit_message_text("✅ Tarea de unificación de videos creada y encolada. Esta operación puede tardar.")
    else:
        logger.warning(f"Callback desconocido recibido: {data}")