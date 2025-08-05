import logging
import os
from telegram import Update, InlineKeyboardMarkup
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
            count = db_instance.delete_all_pending(query.from_user.id)
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
        action_type = parts[1]
        task_id = parts[2]
        
        if action_type in ["rename", "trim", "split", "gif", "screenshot", "caption", "addtrack", "sample", "extract"]:
            await processing_handler.show_config_menu(update, context, task_id, action_type, payload=parts[3] if len(parts) > 3 else None)
        
        elif action_type == "quality":
            keyboard = build_quality_menu(task_id)
            await query.edit_message_text("⚙️ Seleccione el perfil de calidad/conversión:", reply_markup=keyboard)
        
        elif action_type == "tracks":
            task = db_instance.get_task(task_id)
            if not task:
                await query.edit_message_text("❌ Tarea no encontrada."); return
            
            download_path = os.path.join(DOWNLOAD_DIR, str(task_id))
            if not os.path.exists(download_path):
                await query.edit_message_text("⬇️ Analizando archivo, un momento...", reply_markup=None)
                try:
                    file_to_download = await context.bot.get_file(task['file_id'])
                    await file_to_download.download_to_drive(download_path)
                except Exception as e:
                    await query.edit_message_text(f"❌ No se pudo descargar el archivo para análisis: {e}")
                    return

            media_info = ffmpeg.get_media_info(download_path)
            keyboard = build_tracks_menu(task_id, media_info)
            await query.edit_message_text("🎵/📜 Gestor de Pistas:", reply_markup=keyboard)
        
        elif action_type == "audioconvert":
            keyboard = build_audio_convert_menu(task_id)
            await query.edit_message_text("🔊 Configure la conversión de audio:", reply_markup=keyboard)
        
        elif action_type == "audioeffects":
            task = db_instance.get_task(task_id)
            keyboard = build_audio_effects_menu(task_id, task.get('processing_config', {}))
            await query.edit_message_text("🎧 Aplique efectos de audio:", reply_markup=keyboard)

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
        
        elif config_type == "subconvert":
            db_instance.update_task_config(task_id, "subtitle_convert_to", value)

        elif config_type == "trackop":
            op, track_type, track_index = parts[3], parts[4], parts[5]
            if op == "remove":
                list_key = f"remove_{track_type}_indices"
                db_instance.push_to_task_config_list(task_id, list_key, int(track_index))
                await query.answer(f"Pista {track_index} marcada para eliminación.")
                # No refrescar el menú aquí para permitir múltiples selecciones
                return

        elif config_type == "audioprop":
            prop_name, prop_value = parts[3], parts[4]
            db_instance.update_task_config(task_id, f"audio_{prop_name}", prop_value)
            
        elif config_type == "audioeffect":
            effect, toggle = parts[3], parts[4]
            if toggle == "toggle":
                current_value = task.get('processing_config', {}).get(effect, False)
                db_instance.update_task_config(task_id, effect, not current_value)
                task = db_instance.get_task(task_id)
                keyboard = build_audio_effects_menu(task_id, task.get('processing_config', {}))
                await query.edit_message_text("🎧 Aplique efectos de audio:", reply_markup=keyboard)
                return

        task = db_instance.get_task(task_id)
        keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
        await query.edit_message_text(
            f"🛠️ Configuración actualizada. ¿Algo más con <code>{escape_html(task.get('original_filename'))}</code>?",
            reply_markup=keyboard, parse_mode=ParseMode.HTML
        )

    elif action == "song":
        command, payload = parts[1], "_".join(parts[2:])
        if command == "download":
            user = query.from_user
            greeting_prefix = get_greeting(user.id)
            await query.edit_message_text(f"🔎 {greeting_prefix}Analizando selección...")
            
            # El payload es un término de búsqueda, lo usamos con yt-dlp
            search_term_or_url = f"ytsearch:{payload}" if not payload.startswith("http") else payload
            info = downloader.get_url_info(search_term_or_url)
            
            if not info:
                await query.edit_message_text(f"❌ Lo siento, no pude obtener información para descargar esa canción.")
                return

            task_id = db_instance.add_task(user_id=user.id, file_type='video' if info['is_video'] else 'audio', url=info['url'], file_name=sanitize_filename(info['title']), processing_config={'url_info': info})
            
            if not task_id:
                await query.edit_message_text(f"❌ Hubo un error al crear la tarea en la base de datos.")
                return
            
            keyboard = build_download_quality_menu(str(task_id), info['formats'])
            text = f"✅ <b>{escape_html(info['title'])}</b>\n\nSeleccione la calidad que desea descargar:"
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

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
                count = 0
                for tid in task_ids:
                    task_to_update = db_instance.get_task(tid)
                    if task_to_update and task_to_update.get('file_type') == 'video':
                         db_instance.update_task_config(tid, "quality", "720p")
                         db_instance.update_task(tid, "status", "queued")
                         count += 1
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
                await query.edit_message_text("✅ Tarea de unificación de videos creada y encolada.")

    else:
        logger.warning(f"Callback desconocido recibido: {data}")