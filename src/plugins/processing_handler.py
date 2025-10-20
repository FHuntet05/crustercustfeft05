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

@Client.on_callback_query(filters.regex(r"^(profile_|batch_|join_|zip_|panel_delete_all_|cancel_task_)"))
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