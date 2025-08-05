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
from src.core import ffmpeg, downloader
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
    
    if action == "noop":
        return

    elif action == "panel":
        payload = parts[1]
        if payload == "delete_all":
            count = db_instance.tasks.delete_many(
                {"user_id": query.from_user.id, "status": {"$in": ["pending_review", "queued", "error"]}}
            ).deleted_count
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
            text = (f"🛠️ {get_greeting(query.from_user.id)}¿Qué desea hacer con:\n"
                    f"<code>{escape_html(task.get('original_filename', '...'))}</code>?")
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        
        elif action_type == "queue":
            db_instance.update_task(task_id, "status", "queued")
            await query.edit_message_text("✅ ¡Entendido! La tarea ha sido enviada a la cola de procesamiento.")
            
    elif action == "config":
        action_type, task_id = parts[1], parts[2]
        if action_type in ["rename", "trim", "split", "gif", "screenshot", "caption", "addtrack", "sample", "extract", "audiotags", "bulkrename"]:
            await processing_handler.show_config_menu(update, context, task_id, action_type, payload=parts[3] if len(parts) > 3 else None)
        elif action_type == "quality":
            keyboard = build_quality_menu(task_id)
            await query.edit_message_text("⚙️ Seleccione el perfil de calidad/conversión:", reply_markup=keyboard)
        elif action_type == "tracks":
            # Esta lógica podría simplificarse o mejorarse, pero la mantenemos por ahora
            task = db_instance.get_task(task_id)
            if not task: await query.edit_message_text("❌ Tarea no encontrada."); return
            await query.edit_message_text("🛠️ Funcionalidad de pistas en desarrollo.", reply_markup=None)
        elif action_type == "audioconvert":
            keyboard = build_audio_convert_menu(task_id)
            await query.edit_message_text("🔊 Configure la conversión de audio:", reply_markup=keyboard)
        elif action_type == "audioeffects":
            task = db_instance.get_task(task_id)
            keyboard = build_audio_effects_menu(task_id, task.get('processing_config', {}))
            await query.edit_message_text("🎧 Aplique efectos de audio:", reply_markup=keyboard)

    elif action == "set":
        config_type, task_id = parts[1], parts[2]
        value = "_".join(parts[3:])
        
        if config_type == "dlformat":
            format_id = value
            db_instance.update_task_config(task_id, "download_format_id", format_id)
            db_instance.update_task(task_id, "status", "queued")
            await query.edit_message_text(f"✅ ¡Entendido! He enviado la descarga de <code>{format_id}</code> a la cola.", parse_mode=ParseMode.HTML)
            return

        task = db_instance.get_task(task_id)
        if not task: await query.edit_message_text("❌ Error: La tarea ya no existe.", reply_markup=None); return
        if config_type == "quality": db_instance.update_task_config(task_id, "quality", value)
        elif config_type == "mute" and value == "toggle": db_instance.update_task_config(task_id, "mute_audio", not task.get('processing_config', {}).get('mute_audio', False))
        elif config_type == "subconvert": db_instance.update_task_config(task_id, "subtitle_convert_to", value)
        elif config_type == "trackop":
            op, track_type, track_index = parts[3], parts[4], parts[5]
            if op == "remove": db_instance.push_to_task_config_list(task_id, f"remove_{track_type}_indices", int(track_index)); await query.answer(f"Pista {track_index} marcada para eliminación."); return
        elif config_type == "audioprop": db_instance.update_task_config(task_id, f"audio_{parts[3]}", parts[4])
        elif config_type == "audioeffect":
            effect, toggle = parts[3], parts[4]
            if toggle == "toggle":
                db_instance.update_task_config(task_id, effect, not task.get('processing_config', {}).get(effect, False))
                task = db_instance.get_task(task_id)
                keyboard = build_audio_effects_menu(task_id, task.get('processing_config', {}))
                await query.edit_message_text("🎧 Aplique efectos de audio:", reply_markup=keyboard)
                return
        task = db_instance.get_task(task_id)
        keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
        await query.edit_message_text(f"🛠️ Configuración actualizada.", reply_markup=keyboard, parse_mode=ParseMode.HTML)
    
    elif action == "song":
        command, result_id = parts[1], parts[2]
        
        if command == "select":
            user = query.from_user
            greeting_prefix = get_greeting(user.id)
            await query.edit_message_text(f"🔎 {greeting_prefix}Analizando selección...")
            
            search_result = db_instance.search_results.find_one_and_delete({"_id": ObjectId(result_id)})
            if not search_result:
                await query.edit_message_text("❌ Error: Este resultado de búsqueda ha expirado."); return

            search_term_or_url = search_result.get('url') or f"ytsearch:{search_result.get('search_term')}"
            
            info = downloader.get_url_info(search_term_or_url)
            if not info: await query.edit_message_text(f"❌ No pude obtener información para descargar."); return
            
            task_id = db_instance.add_task(user_id=user.id, file_type='video' if info['is_video'] else 'audio', url=info['url'], file_name=sanitize_filename(info['title']), processing_config={'url_info': info})
            if not task_id: await query.edit_message_text(f"❌ Error al crear la tarea en la DB."); return
            
            keyboard = build_download_quality_menu(str(task_id), info['formats'])
            text = f"✅ <b>{escape_html(info['title'])}</b>\n\nSeleccione la calidad a descargar:"
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    elif action == "bulk":
        action_type = parts[1]
        task_ids_str = "_".join(parts[2:])
        task_ids = task_ids_str.split(',')
        if action_type == "start":
            keyboard = build_bulk_actions_menu(task_ids_str)
            await query.edit_message_text(f"✨ <b>Modo Bulk</b>\n\nHa seleccionado {len(task_ids)} tareas. ¿Qué desea realizar?", reply_markup=keyboard, parse_mode=ParseMode.HTML)
        elif action_type == "action":
            bulk_op = "_".join(parts[2:-1])
            task_ids_str = parts[-1]
            task_ids = task_ids_str.split(',')
            if bulk_op == "convert720p":
                count = 0
                for tid in task_ids:
                    if (task_to_update := db_instance.get_task(tid)) and task_to_update.get('file_type') == 'video':
                        db_instance.update_task_config(tid, "quality", "720p"); count += 1
                db_instance.update_many_tasks_status(task_ids, "queued")
                await query.edit_message_text(f"✅ {count} tareas de video encoladas para conversión.")
            elif bulk_op == "rename": await processing_handler.show_config_menu(update, context, task_ids_str, "bulkrename")
            elif bulk_op in ["zip", "unify"]:
                special_type = "zip_bulk" if bulk_op == "zip" else "unify_videos"
                file_name = "Archivo-Bulk.zip" if bulk_op == "zip" else "Video-Unificado.mp4"
                file_type = "document" if bulk_op == "zip" else "video"
                new_task_id = db_instance.add_task(user_id=query.from_user.id, file_type=file_type, special_type=special_type, file_name=file_name)
                db_instance.update_task_config(str(new_task_id), "source_task_ids", task_ids)
                db_instance.update_task(str(new_task_id), "status", "queued")
                await query.edit_message_text(f"✅ Tarea de {bulk_op} en lote creada y encolada.")
    else:
        logger.warning(f"Callback desconocido recibido: {data}")