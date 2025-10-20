# --- START OF FILE src/plugins/processing_handler.py ---

import logging
import os
import re
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import *
from src.helpers.utils import sanitize_filename, escape_html

logger = logging.getLogger(__name__)

# --- Funciones de Apertura de Menú y Manejo de Estado ---

async def open_task_menu_from_p(client: Client, message_or_query, task_id: str):
    """
    Punto de retorno universal. Abre el menú de configuración principal para una tarea.
    Distingue si fue llamado por un comando (responde) o un botón (edita).
    """
    try:
        task = await db_instance.get_task(task_id)
        if not task:
            text = "❌ Error: La tarea ya no existe."
            if isinstance(message_or_query, CallbackQuery):
                await message_or_query.answer(text, show_alert=True)
                try: await message_or_query.message.delete()
                except Exception: pass
            else: await message_or_query.reply(text)
            return

        text_content = f"🛠️ <b>Configurando Tarea:</b>\n<code>{escape_html(task.get('original_filename', '...'))}</code>"
        markup = build_processing_menu(task_id, task['file_type'], task)
        
        if isinstance(message_or_query, CallbackQuery):
            try:
                await message_or_query.message.edit_text(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)
            except MessageNotModified:
                await message_or_query.answer()
        else:
            await message_or_query.reply(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Error crítico en open_task_menu_from_p: {e}", exc_info=True)


async def handle_text_input_for_state(client: Client, message: Message, user_state: dict):
    """Maneja la entrada de texto del usuario y SIEMPRE restaura el menú principal."""
    user_id, user_input = message.from_user.id, message.text.strip()
    state, data = user_state['status'], user_state['data']
    task_id, source_message_id = data.get('task_id'), data.get('source_message_id')

    if not task_id or not source_message_id:
        await db_instance.set_user_state(user_id, "idle")
        await message.reply("❌ Error: No se encontró la tarea asociada. Por favor, inténtalo de nuevo.", quote=True)
        return

    try:
        if state == "awaiting_watermark_text":
            if not user_input:
                await message.reply("❌ El texto para la marca de agua no puede estar vacío. Por favor, envía un texto válido.", quote=True)
                return
            
            # Validar que el texto no sea demasiado largo
            if len(user_input) > 50:
                await message.reply("❌ El texto de la marca de agua es demasiado largo. Máximo 50 caracteres.", quote=True)
                return
                
            await db_instance.update_task_config(task_id, "watermark", {"type": "text", "text": user_input})
            await message.delete() 
            await db_instance.set_user_state(user_id, 'awaiting_watermark_position', data=data)
            
            try:
                await client.edit_message_text(
                    user_id, 
                    source_message_id, 
                    text="✅ <b>Texto recibido:</b> " + escape_html(user_input) + "\n\n📍 <b>Elija la posición:</b>", 
                    reply_markup=build_position_menu(task_id, "config_watermark"),
                    parse_mode=ParseMode.HTML
                )
            except Exception as edit_error:
                logger.error(f"Error editando mensaje de posición: {edit_error}")
                await message.reply("✅ Texto recibido. Por favor, usa el botón de configuración para continuar.", quote=True)
            return 

        task = await db_instance.get_task(task_id)
        if not task:
            await message.reply("❌ La tarea ya no existe.", quote=True)
            await db_instance.set_user_state(user_id, "idle"); return

        if state == "awaiting_profile_name":
            await db_instance.add_preset(user_id, user_input, task.get('processing_config', {}))
            await message.reply(f"✅ Perfil '<b>{escape_html(user_input)}</b>' guardado.", parse_mode=ParseMode.HTML, quote=True)
        elif state == "awaiting_rename":
            if not user_input:
                await message.reply("❌ El nombre del archivo no puede estar vacío.", quote=True)
                return
            if len(user_input) > 100:
                await message.reply("❌ El nombre del archivo es demasiado largo. Máximo 100 caracteres.", quote=True)
                return
            await db_instance.update_task_config(task_id, "final_filename", sanitize_filename(os.path.splitext(user_input)[0]))
            await message.reply("✅ Nombre del archivo actualizado.", quote=True)
            
        elif state == "awaiting_trim":
            if not user_input:
                await message.reply("❌ Los tiempos de corte no pueden estar vacíos.", quote=True)
                return
            # Validar formato de tiempo básico
            time_pattern = r'^(\d{1,2}:\d{2}(:\d{2})?(-\d{1,2}:\d{2}(:\d{2})?)?|\d{1,2}:\d{2}(:\d{2})?)$'
            if not re.match(time_pattern, user_input):
                await message.reply("❌ Formato de tiempo inválido. Use: `00:10-00:50` o `01:23`", quote=True)
                return
            await db_instance.update_task_config(task_id, "trim_times", user_input)
            await message.reply("✅ Tiempos de corte configurados.", quote=True)
            # Encolar automáticamente al definir trim
            await db_instance.update_task_field(task_id, "status", "queued")
            try:
                await client.edit_message_text(
                    user_id,
                    source_message_id,
                    text="✅ Corte configurado.\n⏳ <b>En Cola...</b>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            
        elif state == "awaiting_gif":
            parts = user_input.split()
            if len(parts) == 2 and parts[0].replace('.','',1).isdigit() and parts[1].isdigit():
                duration = float(parts[0])
                fps = int(parts[1])
                if duration <= 0 or duration > 60:
                    await message.reply("❌ La duración debe estar entre 0.1 y 60 segundos.", quote=True)
                    return
                if fps < 5 or fps > 30:
                    await message.reply("❌ Los FPS deben estar entre 5 y 30.", quote=True)
                    return
                await db_instance.update_task_config(task_id, "gif_options", {"duration": duration, "fps": fps})
                await message.reply("✅ Configuración de GIF actualizada.", quote=True)
            else: 
                await message.reply("❌ Formato inválido. Use: `duración fps` (ej: `5 15`)", quote=True)
                return
        elif state == "awaiting_audiotags":
            tags_to_update, valid_keys = {}, {'título': 'title', 'titulo': 'title', 'artista': 'artist', 'artist': 'artist', 'álbum': 'album', 'album': 'album'}
            for line in user_input.split('\n'):
                if ':' in line:
                    key, value = map(str.strip, line.split(':', 1))
                    if key.lower() in valid_keys: tags_to_update[valid_keys[key.lower()]] = value
            if tags_to_update: await db_instance.update_task_config(task_id, "audio_tags", tags_to_update)
        else: return

        await message.delete()
        await db_instance.set_user_state(user_id, "idle")
        panel_message = await client.get_messages(user_id, source_message_id)
        await open_task_menu_from_p(client, panel_message, task_id)
        
    except Exception as e:
        logger.error(f"Error procesando entrada de texto '{state}': {e}", exc_info=True)
        await message.reply(f"❌ Error al procesar su entrada: `{e}`", quote=True)

async def handle_media_input_for_state(client: Client, message: Message, user_state: dict):
    """Maneja la entrada de media del usuario y SIEMPRE restaura el menú principal."""
    user_id, state, data = message.from_user.id, user_state['status'], user_state['data']
    task_id, source_message_id = data.get('task_id'), data.get('source_message_id')
    if not task_id or not source_message_id:
        await db_instance.set_user_state(user_id, "idle"); return
    media = message.photo or message.document or message.audio
    if not media: return

    if state == "awaiting_watermark_image":
        if not (message.photo or (hasattr(media, 'mime_type') and media.mime_type.startswith("image/"))):
            await message.reply("❌ No es una imagen válida.", quote=True)
            return
        await db_instance.update_task_config(task_id, "watermark", {"type": "image", "file_id": media.file_id})
        await message.delete()
        await db_instance.set_user_state(user_id, 'awaiting_watermark_position', data=data)
        await client.edit_message_text(user_id, source_message_id, "✅ Imagen recibida. Elija la posición:", reply_markup=build_position_menu(task_id, "config_watermark"))
        return

    # ...existing code for other states...

@Client.on_callback_query(filters.regex(r"^(p_open_|task_|config_|set_)"))
async def main_config_callbacks_router(client: Client, query: CallbackQuery):
    try: await query.answer()
    except Exception: pass
    data = query.data
    if data.startswith("p_open_"): await open_task_menu_callback(client, query)
    elif data.startswith("task_"): await handle_task_actions(client, query)
    elif data.startswith("config_"): await show_config_menu_and_set_state(client, query)
    elif data.startswith("set_"): await set_value_callback(client, query)

@Client.on_callback_query(filters.regex(r"^(quality_|set_transcode_|config_extract_audio_|config_trim_)"))
async def quick_actions_router(client: Client, query: CallbackQuery):
    """Router ligero para acciones rápidas (compresión, extraer audio, trim)."""
    try: await query.answer()
    except Exception: pass
    data = query.data
    user_id = query.from_user.id
    # quality_{taskId}_{res}
    if data.startswith("quality_") or data.startswith("set_transcode_"):
        parts = data.split("_")
        # Ambos patrones terminan con ..._{taskId}_resolution_{res} o ..._{taskId}_{res}
        if data.startswith("set_transcode_"):
            # set_transcode_{taskId}_resolution_{res}
            task_id = parts[2]
            if len(parts) >= 5 and parts[3] == "resolution":
                res = parts[4]
            else:
                res = parts[-1]
        else:
            # quality_{taskId}_{res}
            task_id = parts[1]
            res = parts[2] if len(parts) > 2 else "1080"
        # Guardar resolución y encolar
        await db_instance.update_task_config(task_id, "transcode.resolution", res)
        await db_instance.update_task_field(task_id, "status", "queued")
        try:
            await query.message.edit_text(
                f"✅ Compresión seleccionada: {res}p\n⏳ <b>En Cola...</b>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        return
    
    # config_extract_audio_{taskId}
    if data.startswith("config_extract_audio_"):
        task_id = data.split("_")[-1]
        await db_instance.update_task_config(task_id, "extract_audio", True)
        await db_instance.update_task_field(task_id, "status", "queued")
        try:
            await query.message.edit_text("✅ Extracción de audio enviada.\n⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return
    
    # config_trim_{taskId} abre el estado para pedir tiempos, luego encolará al recibir el texto
    if data.startswith("config_trim_"):
        task_id = data.split("_")[-1]
        await db_instance.set_user_state(user_id, "awaiting_trim", data={"task_id": task_id, "source_message_id": query.message.id})
        back_button = build_back_button(f"p_open_{task_id}")
        await query.message.edit_text(
            "✂️ Envíe tiempos de corte:\n• <code>00:10-00:50</code> (inicio-fin)\n• <code>01:23</code> (corta hasta ahí)",
            reply_markup=back_button,
            parse_mode=ParseMode.HTML
        )
        return

@Client.on_callback_query(filters.regex(r"^(profile_|batch_|join_|zip_|panel_delete_all_|cancel_task_|open_panel_main|download_video_guide|open_settings|show_help_detailed|refresh_panel|select_file_to_configure)"))
async def advanced_features_callbacks_router(client: Client, query: CallbackQuery):
    try: await query.answer()
    except Exception: pass
    data = query.data
    if data.startswith("profile_"): await handle_profile_actions(client, query)
    elif data.startswith("batch_"): await handle_batch_actions(client, query)
    elif data.startswith("join_"): await handle_join_actions(client, query)
    elif data.startswith("zip_"): await handle_zip_actions(client, query)
    elif data.startswith("panel_delete_all_"): await handle_panel_delete_all(client, query)
    elif data.startswith("cancel_task_"): await handle_cancel_task(client, query)
    elif data == "open_panel_main": await handle_open_panel_main(client, query)
    elif data == "download_video_guide": await handle_download_video_guide(client, query)
    elif data == "open_settings": await handle_open_settings(client, query)
    elif data == "show_help_detailed": await handle_show_help_detailed(client, query)
    elif data == "refresh_panel": await handle_refresh_panel(client, query)
    elif data == "select_file_to_configure": await handle_select_file_to_configure(client, query)

async def open_task_menu_callback(client: Client, query: CallbackQuery):
    task_id = query.data.split("_")[2]
    await db_instance.set_user_state(query.from_user.id, "idle")
    await open_task_menu_from_p(client, query, task_id)

async def handle_task_actions(client: Client, query: CallbackQuery):
    parts = query.data.split("_"); action, task_id = parts[1], "_".join(parts[2:])
    user_id = query.from_user.id
    if action == "queuesingle":
        await db_instance.update_task_field(task_id, "status", "queued")
        await query.message.edit_text("⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)
    elif action == "delete":
        await db_instance.delete_task_by_id(task_id)
        await query.message.edit_text("🗑️ Tarea cancelada.")
    await db_instance.set_user_state(user_id, "idle")

async def show_config_menu_and_set_state(client: Client, query: CallbackQuery):
    """Único responsable de mostrar submenús o pedir una entrada al usuario."""
    user_id, data = query.from_user.id, query.data
    parts = data.split("_"); menu_type, task_id = parts[1], "_".join(parts[2:])
    task = await db_instance.get_task(task_id)
    if not task: return await query.message.delete()
    config = task.get('processing_config', {})
    
    if menu_type == "extract_audio":
        await db_instance.update_task_config(task_id, "extract_audio", True)
        await db_instance.update_task_field(task_id, "status", "queued")
        return await query.message.edit_text("✅ Tarea de extracción enviada.\n⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)
    
    keyboards = {"transcode": build_transcode_menu(task_id), "tracks": build_tracks_menu(task_id, config), "watermark": build_watermark_menu(task_id), "thumbnail": build_thumbnail_menu(task_id, config), "audiometadata": build_audio_metadata_menu(task_id)}
    menu_messages = {"transcode": "📉 Seleccione resolución:", "tracks": "📜 Gestione pistas:", "watermark": "💧 Añada marca de agua:", "thumbnail": "🖼️ Gestione miniatura:", "audiometadata": "📝 Elija qué metadato editar:"}
    if menu_type in keyboards:
        await db_instance.set_user_state(user_id, "idle")
        return await query.message.edit_text(text=menu_messages[menu_type], reply_markup=keyboards[menu_type])
    
    state_map = {"rename": "awaiting_rename", "trim": "awaiting_trim", "gif": "awaiting_gif", "addsubs": "awaiting_subs", "thumbnail_add": "awaiting_thumbnail_add", "replace_audio": "awaiting_replace_audio", "watermark_text": "awaiting_watermark_text", "watermark_image": "awaiting_watermark_image", "profile_save_request": "awaiting_profile_name", "audiotags": "awaiting_audiotags", "audiothumb": "awaiting_audiothumb"}
    menu_texts = {"rename": "✏️ Envíe el nuevo nombre (sin extensión).", "trim": "✂️ Envíe tiempos de corte:\n• <code>00:10-00:50</code> (inicio-fin)\n• <code>01:23</code> (corta hasta ahí)", "gif": "🎞️ Envíe duración y FPS (ej: <code>5 15</code>).", "addsubs": "➕ Envíe el archivo <code>.srt</code>.", "thumbnail_add": "🖼️ Envíe la nueva miniatura.", "replace_audio": "🎼 Envíe el nuevo audio.", "watermark_text": "💧 Envíe el texto para la marca de agua.", "watermark_image": "🖼️ Envíe la imagen para la marca.", "profile_save_request": "✏️ Envíe un nombre para el perfil.", "audiotags": "✍️ Envíe los metadatos:\n<code>Título: Mi Canción\nArtista: El Artista</code>", "audiothumb": "🖼️ Envíe la imagen de la carátula."}
    
    if menu_type in state_map:
        await db_instance.set_user_state(user_id, state_map[menu_type], data={"task_id": task_id, "source_message_id": query.message.id})
        back_callbacks = {"addsubs": "config_tracks_", "thumbnail_add": "config_thumbnail_", "replace_audio": "config_tracks_", "watermark_text": "config_watermark_", "watermark_image": "config_watermark_", "profile_save_request": "p_open_", "audiotags": "config_audiometadata_", "audiothumb": "config_audiometadata_"}
        back_button_callback = f"{back_callbacks.get(menu_type, 'p_open_')}{task_id}"
        back_button = build_back_button(back_button_callback)
        await query.message.edit_text(menu_texts[menu_type], reply_markup=back_button, parse_mode=ParseMode.HTML)

async def set_value_callback(client: Client, query: CallbackQuery):
    """Maneja toggles simples y SIEMPRE restaura el menú principal."""
    user_id, parts = query.from_user.id, query.data.split("_")
    config_type, task_id = parts[1], parts[2]
    
    await db_instance.set_user_state(user_id, "idle")
    task = await db_instance.get_task(task_id)
    if not task: return await query.message.delete()
    config = task.get('processing_config', {})

    if config_type == "watermark" and parts[3] == "position":
        await db_instance.update_task_config(task_id, "watermark.position", parts[4].replace('-', '_'))
    elif config_type == "transcode":
        value = "_".join(parts[3:])
        if value == "remove_all": await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.transcode": ""}})
        else: prop, val = value.split('_', 1); await db_instance.update_task_config(task_id, f"transcode.{prop}", val)
    elif config_type == "watermark" and parts[3] == "remove": 
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.watermark": ""}})
    elif config_type == "thumb" and parts[3] == "op":
        current_val = config.get("remove_thumbnail", False)
        await db_instance.update_task_config(task_id, "remove_thumbnail", not current_val)
    elif config_type == "mute": 
        await db_instance.update_task_config(task_id, 'mute_audio', not config.get('mute_audio', False))
    elif config_type == "trackopt": 
        opt = "_".join(parts[3:])
        await db_instance.update_task_config(task_id, opt, not config.get(opt, False))
    
    await open_task_menu_from_p(client, query, task_id)

async def handle_profile_actions(client: Client, query: CallbackQuery):
    user_id, parts, action = query.from_user.id, query.data.split("_"), query.data.split("_")[1]
    await db_instance.set_user_state(user_id, "idle")
    if action == "apply":
        task_id, preset_id = parts[2], parts[3]
        preset = await db_instance.get_preset_by_id(preset_id)
        if not preset: return
        await db_instance.update_task_field(task_id, "processing_config", preset['config_data'])
        await open_task_menu_from_p(client, query, task_id)
    elif action == "delete":
        if parts[2] == "req": await query.message.edit_text("¿Seguro que desea eliminar este perfil?", reply_markup=build_profile_delete_confirmation_keyboard(parts[3]))
        elif parts[2] == "confirm":
            await db_instance.delete_preset_by_id(parts[3]); presets = await db_instance.get_user_presets(user_id)
            await query.message.edit_text("✅ Perfil eliminado.", reply_markup=build_profiles_management_keyboard(presets))
    elif action == "open" and parts[2] == "main":
        presets = await db_instance.get_user_presets(user_id)
        await query.message.edit_text("<b>Gestión de Perfiles:</b>", reply_markup=build_profiles_management_keyboard(presets), parse_mode=ParseMode.HTML)
    elif action == "close": await query.message.delete()

async def handle_batch_actions(client: Client, query: CallbackQuery):
    user_id, parts, action = query.from_user.id, query.data.split("_"), query.data.split("_")[1]
    await db_instance.set_user_state(user_id, "idle")
    if action == "cancel": return await query.message.delete()
    tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
    if not tasks: return await query.message.edit_text("❌ No hay tareas en el panel.")
    config_to_apply = {}
    if action == "apply" and len(parts) > 2:
        preset_id = parts[2]
        if preset := await db_instance.get_preset_by_id(preset_id): config_to_apply = preset.get('config_data', {})
    await query.message.edit_text(f"🔥 Procesando en lote {len(tasks)} tareas...")
    for task in tasks:
        await db_instance.update_task_field(str(task['_id']), "processing_config", config_to_apply)
        await db_instance.update_task_field(str(task['_id']), "status", "queued")
    await query.message.edit_text(f"✅ ¡Listo! {len(tasks)} tareas enviadas a la cola.")

async def handle_join_actions(client: Client, query: CallbackQuery):
    user_id, parts, action = query.from_user.id, query.data.split("_"), query.data.split("_")[1]
    state = await db_instance.get_user_state(user_id); selected_ids = state.get("data", {}).get("selected_ids", [])
    if action == "select":
        task_id = parts[2]
        if task_id in selected_ids: selected_ids.remove(task_id)
        else: selected_ids.append(task_id)
        await db_instance.set_user_state(user_id, "selecting_join_files", data={"selected_ids": selected_ids})
        tasks = await db_instance.get_pending_tasks(user_id, file_type_filter="video", status_filter="pending_processing")
        await query.message.edit_reply_markup(reply_markup=build_join_selection_keyboard(tasks, selected_ids))
    elif action == "confirm":
        if len(selected_ids) < 2: return
        await db_instance.add_task(user_id, 'join_operation', final_filename=f"Union_{datetime.now().strftime('%Y%m%d_%H%M')}", status="queued", custom_fields={"source_task_ids": [ObjectId(tid) for tid in selected_ids]})
        await db_instance.tasks.update_many({"_id": {"$in": [ObjectId(tid) for tid in selected_ids]}}, {"$set": {"status": "used_in_join"}})
        await query.message.edit_text(f"✅ Tarea de unión para {len(selected_ids)} videos enviada a la cola.")
        await db_instance.set_user_state(user_id, "idle")
    elif action == "cancel": await db_instance.set_user_state(user_id, "idle"); await query.message.delete()

async def handle_zip_actions(client: Client, query: CallbackQuery):
    user_id, parts, action = query.from_user.id, query.data.split("_"), query.data.split("_")[1]
    state = await db_instance.get_user_state(user_id); selected_ids = state.get("data", {}).get("selected_ids", [])
    if action == "select":
        task_id = parts[2]
        if task_id in selected_ids: selected_ids.remove(task_id)
        else: selected_ids.append(task_id)
        await db_instance.set_user_state(user_id, "selecting_zip_files", data={"selected_ids": selected_ids})
        tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
        await query.message.edit_reply_markup(reply_markup=build_zip_selection_keyboard(tasks, selected_ids))
    elif action == "confirm":
        if not selected_ids: return
        await db_instance.add_task(user_id, 'zip_operation', final_filename=f"Comprimido_{datetime.now().strftime('%Y%m%d_%H%M')}", status="queued", custom_fields={"source_task_ids": [ObjectId(tid) for tid in selected_ids]})
        await db_instance.tasks.update_many({"_id": {"$in": [ObjectId(tid) for tid in selected_ids]}}, {"$set": {"status": "used_in_zip"}})
        await query.message.edit_text(f"✅ Tarea de compresión para {len(selected_ids)} archivos enviada a la cola.")
        await db_instance.set_user_state(user_id, "idle")
    elif action == "cancel": await db_instance.set_user_state(user_id, "idle"); await query.message.delete()

async def handle_panel_delete_all(client: Client, query: CallbackQuery):
    await db_instance.set_user_state(query.from_user.id, "idle")
    action = query.data.split("_")[-1]
    if action == "confirm":
        deleted = await db_instance.delete_all_pending_tasks(query.from_user.id)
        await query.message.edit_text(f"💥 Panel limpiado. Se descartaron {deleted.deleted_count} tareas.")
    elif action == "cancel": await query.message.edit_text("Operación cancelada.")

async def handle_cancel_task(client: Client, query: CallbackQuery):
    """Maneja la cancelación de una tarea en progreso"""
    try:
        task_id = query.data.split("_")[2]
        user_id = query.from_user.id
        
        # Verificar si la tarea existe
        task = await db_instance.get_task(task_id)
        if not task:
            await query.answer("❌ La tarea ya no existe.", show_alert=True)
            return
            
        # Verificar que el usuario es el propietario de la tarea
        if task.get('user_id') != user_id:
            await query.answer("❌ No tienes permisos para cancelar esta tarea.", show_alert=True)
            return
            
        # Cancelar la tarea
        await db_instance.update_task_field(task_id, "status", "cancelled")
        await db_instance.set_user_state(user_id, "idle")
        
        # Notificar al usuario
        await query.message.edit_text(
            "❌ <b>Tarea Cancelada</b>\n\n"
            f"La tarea <code>{task_id}</code> ha sido cancelada exitosamente.",
            parse_mode=ParseMode.HTML
        )
        
        logger.info(f"Tarea {task_id} cancelada por el usuario {user_id}")
        
    except Exception as e:
        logger.error(f"Error cancelando tarea: {e}")
        await query.answer("❌ Error al cancelar la tarea.", show_alert=True)

async def handle_open_panel_main(client: Client, query: CallbackQuery):
    """Maneja el botón de abrir panel principal"""
    try:
        user_id = query.from_user.id
        pending_tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
        
        if not pending_tasks:
            await query.message.edit_text(
                "📋 <b>Panel de Control</b>\n\n"
                "No tienes archivos en el panel.\n\n"
                "💡 <b>Para agregar archivos:</b>\n"
                "• Envía videos directamente al bot\n"
                "• Usa enlaces de Telegram con /get_restricted\n"
                "• Reenvía contenido multimedia",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Construir mensaje del panel
        panel_text = f"📋 <b>Panel de Control</b>\n\n"
        panel_text += f"📊 <b>Total de archivos:</b> {len(pending_tasks)}\n\n"
        
        for i, task in enumerate(pending_tasks, 1):
            file_name = task.get('original_filename', 'Archivo sin nombre')
            file_type = task.get('file_type', 'document')
            file_size = task.get('file_metadata', {}).get('size', 0)
            duration = task.get('file_metadata', {}).get('duration', 0)
            
            # Emoji según tipo de archivo
            emoji_map = {'video': '🎬', 'audio': '🎵', 'document': '📄'}
            emoji = emoji_map.get(file_type, '📁')
            
            # Información del archivo
            panel_text += f"{i}. {emoji} <code>{escape_html(file_name[:50])}</code>\n"
            if file_size > 0:
                panel_text += f"   📊 {format_size(file_size)}"
            if duration > 0:
                panel_text += f" | ⏱️ {format_time(duration)}"
            panel_text += "\n\n"
        
        # Crear teclado con opciones
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Actualizar Panel", callback_data="refresh_panel")],
            [InlineKeyboardButton("🗑️ Limpiar Todo", callback_data="panel_delete_all_confirm")],
            [InlineKeyboardButton("⚙️ Configurar Archivo", callback_data="select_file_to_configure")]
        ])
        
        await query.message.edit_text(panel_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error en handle_open_panel_main: {e}")
        await query.answer("❌ Error al abrir el panel.", show_alert=True)

async def handle_download_video_guide(client: Client, query: CallbackQuery):
    """Maneja el botón de guía de descarga"""
    try:
        guide_text = (
            "📥 <b>Guía de Descarga de Videos</b>\n\n"
            "🔗 <b>Enlaces soportados:</b>\n"
            "• Canales públicos: <code>https://t.me/canal/123</code>\n"
            "• Canales privados: <code>https://t.me/c/123456789/123</code>\n"
            "• Enlaces de invitación: <code>https://t.me/+ABC123</code>\n\n"
            "📤 <b>Envío directo:</b>\n"
            "• Reenvía videos desde otros chats\n"
            "• Envía videos directamente al bot\n\n"
            "⚙️ <b>Procesamiento:</b>\n"
            "• Compresión automática\n"
            "• Aplicación de marcas de agua\n"
            "• Extracción de audio\n"
            "• Conversión a GIF\n\n"
            "💡 <b>Consejo:</b> Usa /panel para ver archivos en cola"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Ver Panel", callback_data="open_panel_main")],
            [InlineKeyboardButton("🔙 Volver", callback_data="back_to_start")]
        ])
        
        await query.message.edit_text(guide_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error en handle_download_video_guide: {e}")
        await query.answer("❌ Error al mostrar la guía.", show_alert=True)

async def handle_open_settings(client: Client, query: CallbackQuery):
    """Maneja el botón de configuraciones"""
    try:
        settings_text = (
            "⚙️ <b>Configuraciones del Bot</b>\n\n"
            "🔧 <b>Configuraciones disponibles:</b>\n"
            "• Calidad de compresión\n"
            "• Marca de agua por defecto\n"
            "• Formatos de salida\n"
            "• Límites de tamaño\n\n"
            "📋 <b>Gestionar archivos:</b>\n"
            "• Ver panel de archivos\n"
            "• Configurar procesamiento\n"
            "• Aplicar efectos\n\n"
            "💡 <b>Nota:</b> Las configuraciones se aplican a cada archivo individualmente."
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Ver Panel", callback_data="open_panel_main")],
            [InlineKeyboardButton("🔙 Volver", callback_data="back_to_start")]
        ])
        
        await query.message.edit_text(settings_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error en handle_open_settings: {e}")
        await query.answer("❌ Error al abrir configuraciones.", show_alert=True)

async def handle_show_help_detailed(client: Client, query: CallbackQuery):
    """Maneja el botón de ayuda detallada"""
    try:
        help_text = (
            "📚 <b>Ayuda Detallada del Bot</b>\n\n"
            "🔑 <b>Comandos principales:</b>\n"
            "• <code>/start</code> - Menú principal\n"
            "• <code>/panel</code> - Ver archivos en cola\n"
            "• <code>/get_restricted</code> - Descargar de canales privados\n"
            "• <code>/help</code> - Ayuda básica\n\n"
            "📤 <b>Envío directo:</b>\n"
            "• Videos, audios, documentos\n"
            "• Enlaces de Telegram\n"
            "• Enlaces de canales privados\n\n"
            "⚙️ <b>Funcionalidades:</b>\n"
            "• Compresión inteligente\n"
            "• Marcas de agua\n"
            "• Extracción de audio\n"
            "• Cortar videos\n"
            "• Conversión a GIF\n"
            "• Gestión de metadatos\n\n"
            "❓ <b>¿Problemas?</b>\n"
            "Contacta al administrador o revisa los logs."
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Ver Panel", callback_data="open_panel_main")],
            [InlineKeyboardButton("🔙 Volver", callback_data="back_to_start")]
        ])
        
        await query.message.edit_text(help_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error en handle_show_help_detailed: {e}")
        await query.answer("❌ Error al mostrar la ayuda.", show_alert=True)

async def handle_refresh_panel(client: Client, query: CallbackQuery):
    """Maneja el botón de actualizar panel"""
    try:
        await handle_open_panel_main(client, query)
    except Exception as e:
        logger.error(f"Error en handle_refresh_panel: {e}")
        await query.answer("❌ Error al actualizar el panel.", show_alert=True)

async def handle_select_file_to_configure(client: Client, query: CallbackQuery):
    """Maneja el botón de seleccionar archivo para configurar"""
    try:
        user_id = query.from_user.id
        pending_tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
        
        if not pending_tasks:
            await query.answer("❌ No hay archivos para configurar.", show_alert=True)
            return
        
        # Mostrar lista de archivos para configurar
        files_text = "⚙️ <b>Seleccionar Archivo para Configurar</b>\n\n"
        
        keyboard = []
        for i, task in enumerate(pending_tasks[:10], 1):  # Máximo 10 archivos
            file_name = task.get('original_filename', 'Archivo sin nombre')
            task_id = str(task['_id'])
            keyboard.append([InlineKeyboardButton(
                f"{i}. {file_name[:30]}...", 
                callback_data=f"p_open_{task_id}"
            )])
        
        keyboard.append([InlineKeyboardButton("🔙 Volver al Panel", callback_data="open_panel_main")])
        
        await query.message.edit_text(
            files_text, 
            parse_mode=ParseMode.HTML, 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error en handle_select_file_to_configure: {e}")
        await query.answer("❌ Error al seleccionar archivo.", show_alert=True)