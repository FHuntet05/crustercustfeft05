import logging
import os
from bson.objectid import ObjectId
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import (
    build_processing_menu, build_quality_menu, build_tracks_menu,
    build_audio_convert_menu, build_audio_effects_menu, build_bulk_actions_menu,
    build_download_quality_menu
)
from src.helpers.utils import get_greeting, escape_html, sanitize_filename
from src.core import downloader
from . import processing_handler, command_handler

logger = logging.getLogger(__name__)

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split('_')
    action = parts[0]
    
    if action == "noop": return

    elif action == "panel":
        payload = parts[1]
        if payload == "delete_all":
            count = db_instance.tasks.delete_many({"user_id": query.from_user.id, "status": "pending_review"}).deleted_count
            await query.edit_message_text(f"💥 Limpieza completada. Se descartaron {count} tareas.")
        elif payload == "show":
            await command_handler.panel_command(update, context, is_callback=True)

    elif action == "task":
        action_type, task_id = parts[1], parts[2]
        task = db_instance.get_task(task_id)
        if not task:
            await query.edit_message_text("❌ Error: La tarea ya no existe.", reply_markup=None)
            return

        if action_type == "process":
            keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
            await query.edit_message_text(f"🛠️ ¿Qué desea hacer con:\n<code>{escape_html(task.get('original_filename', '...'))}</code>?", reply_markup=keyboard, parse_mode=ParseMode.HTML)
        
        elif action_type == "queue":
            db_instance.update_task(task_id, "status", "queued")
            await query.edit_message_text("✅ Tarea enviada a la cola de procesamiento.")
            
    elif action == "config":
        action_type, task_id = parts[1], parts[2]
        if action_type in ["rename", "trim", "split", "gif", "audiotags", "watermark"]:
            await processing_handler.show_config_menu(update, context, task_id, action_type)
        elif action_type == "quality":
            await query.edit_message_text("⚙️ Seleccione el perfil de calidad:", reply_markup=build_quality_menu(task_id))
        elif action_type == "tracks":
            await query.edit_message_text("🎵/📜 Gestor de Pistas (En desarrollo):", reply_markup=build_tracks_menu(task_id))
        elif action_type == "audioconvert":
            await query.edit_message_text("🔊 Configure la conversión de audio:", reply_markup=build_audio_convert_menu(task_id))
        elif action_type == "audioeffects":
            task = db_instance.get_task(task_id)
            keyboard = build_audio_effects_menu(task_id, task.get('processing_config', {}))
            await query.edit_message_text("🎧 Aplique efectos de audio:", reply_markup=keyboard)

    elif action == "set":
        config_type, task_id, value = parts[1], parts[2], "_".join(parts[3:])
        task = db_instance.get_task(task_id)
        if not task: await query.edit_message_text("❌ Error: La tarea ya no existe."); return
        
        if config_type == "dlformat":
            db_instance.update_task_config(task_id, "download_format_id", value)
            db_instance.update_task(task_id, "status", "queued")
            await query.edit_message_text(f"✅ Descarga de <code>{value}</code> encolada.", parse_mode=ParseMode.HTML)
            return
        elif config_type == "quality": db_instance.update_task_config(task_id, "quality", value)
        elif config_type == "mute" and value == "toggle": db_instance.update_task_config(task_id, "mute_audio", not task.get('processing_config', {}).get('mute_audio', False))
        elif config_type == "audioprop": db_instance.update_task_config(task_id, f"audio_{parts[3]}", parts[4])
        elif config_type == "audioeffect":
            effect = parts[3]
            db_instance.update_task_config(task_id, effect, not task.get('processing_config', {}).get(effect, False))
            task = db_instance.get_task(task_id) # Recargar
            await query.edit_message_text("🎧 Aplique efectos de audio:", reply_markup=build_audio_effects_menu(task_id, task.get('processing_config', {})))
            return
        
        task = db_instance.get_task(task_id) # Recargar
        keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
        await query.edit_message_text("🛠️ Configuración actualizada.", reply_markup=keyboard)
    
    elif action == "song":
        command, result_id = parts[1], parts[2]
        if command == "select":
            user = query.from_user
            await query.edit_message_text(f"🔎 Analizando selección...")
            search_result = db_instance.search_results.find_one_and_delete({"_id": ObjectId(result_id)})
            if not search_result:
                await query.edit_message_text("❌ Error: Este resultado de búsqueda ha expirado."); return

            search_term_or_url = search_result.get('url') or f"ytsearch:{search_result.get('search_term')}"
            info = downloader.get_url_info(search_term_or_url)
            if not info: await query.edit_message_text(f"❌ No pude obtener información para descargar."); return
            
            task_id = db_instance.add_task(user_id=user.id, file_type='video' if info['is_video'] else 'audio', url=info['url'], file_name=sanitize_filename(info['title']), processing_config={'url_info': info})
            if not task_id: await query.edit_message_text(f"❌ Error al crear la tarea en la DB."); return
            
            keyboard = build_download_quality_menu(str(task_id), info['formats'])
            await query.edit_message_text(f"✅ <b>{escape_html(info['title'])}</b>\n\nSeleccione la calidad:", reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)