import logging
import asyncio
import re
import os
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InputMediaPhoto
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from src.core import downloader
from src.helpers.keyboards import *
from src.helpers.utils import get_greeting, escape_html, sanitize_filename, format_time

logger = logging.getLogger(__name__)
DOWNLOAD_DIR = "downloads"

# --- Funciones de Apertura de Menú y Manejo de Estado ---

async def open_task_menu_from_p(client: Client, message_or_query, task_id: str):
    """Abre el menú de configuración principal para una tarea específica."""
    try:
        task = await db_instance.get_task(task_id)
        if not task:
            text = "❌ Error: La tarea ya no existe o fue procesada."
            if isinstance(message_or_query, CallbackQuery):
                await message_or_query.answer(text, show_alert=True)
                await message_or_query.message.delete()
            else:
                await message_or_query.reply(text)
            return

        text_content = f"🛠️ <b>Configurando Tarea:</b>\n<code>{escape_html(task.get('original_filename', '...'))}</code>"
        markup = build_processing_menu(task_id, task['file_type'], task)
        
        if isinstance(message_or_query, CallbackQuery):
            await message_or_query.message.edit_text(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)
        else:
            await message_or_query.reply(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)
    except MessageNotModified:
        pass
    except Exception as e:
        logger.error(f"Error en open_task_menu_from_p: {e}", exc_info=True)


async def handle_text_input_for_state(client: Client, message: Message, user_state: dict):
    """Maneja la entrada de texto del usuario cuando el bot está esperando una configuración."""
    user_id = message.from_user.id
    user_input = message.text.strip()
    state, data = user_state['status'], user_state['data']
    task_id, source_message_id = data.get('task_id'), data.get('source_message_id')
    
    if not task_id or not source_message_id:
        logger.warning(f"Estado '{state}' para {user_id} sin task_id o source_message_id. Reseteando.")
        await db_instance.set_user_state(user_id, "idle")
        return

    try:
        task = await db_instance.get_task(task_id)
        if not task:
            await message.reply("❌ La tarea para esta configuración ya no existe.", quote=True)
            await db_instance.set_user_state(user_id, "idle")
            return

        if state == "awaiting_profile_name":
            await db_instance.add_preset(user_id, user_input, task.get('processing_config', {}))
            await message.reply(f"✅ Perfil '<b>{escape_html(user_input)}</b>' guardado.", parse_mode=ParseMode.HTML, quote=True)
        
        elif state == "awaiting_rename":
            # Eliminamos la extensión si el usuario la incluye por error.
            sanitized_name = sanitize_filename(os.path.splitext(user_input)[0])
            await db_instance.update_task_config(task_id, "final_filename", sanitized_name)

        elif state == "awaiting_trim":
            await db_instance.update_task_config(task_id, "trim_times", user_input)

        elif state == "awaiting_split":
            await db_instance.update_task_config(task_id, "split_criteria", user_input)

        elif state == "awaiting_gif":
            parts = user_input.split()
            if len(parts) == 2 and parts[0].replace('.','',1).isdigit() and parts[1].isdigit():
                await db_instance.update_task_config(task_id, "gif_options", {"duration": float(parts[0]), "fps": int(parts[1])})
            else:
                await message.reply("❌ Formato inválido. Use: `duración fps` (ej: `5 15`)", quote=True)
        
        elif state == "awaiting_audiotags":
            tags_to_update = {}
            valid_keys = {'título': 'title', 'titulo': 'title', 'artista': 'artist', 'artist': 'artist', 'álbum': 'album', 'album': 'album'}
            for line in user_input.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    if key.strip().lower() in valid_keys:
                        tags_to_update[valid_keys[key.strip().lower()]] = value.strip()
            if tags_to_update:
                await db_instance.update_task_config(task_id, "audio_tags", tags_to_update)
        
        elif state == "awaiting_watermark_text":
            await db_instance.update_task_config(task_id, "watermark", {"type": "text", "text": user_input})
            await message.delete()
            await db_instance.set_user_state(user_id, 'awaiting_watermark_position', data=data)
            return await client.edit_message_text(user_id, source_message_id, text="✅ Texto recibido. Elija la posición:", reply_markup=build_position_menu(task_id, "config_watermark"))
        
        else: # Estado no reconocido, no hacer nada.
            return

        await message.delete()
        await db_instance.set_user_state(user_id, "idle")
        source_message = await client.get_messages(user_id, source_message_id)
        await open_task_menu_from_p(client, source_message, task_id)
        
    except Exception as e:
        logger.error(f"Error procesando entrada de config '{state}': {e}", exc_info=True)
        await message.reply(f"❌ Error al procesar su entrada: `{e}`", quote=True)


async def handle_media_input_for_state(client: Client, message: Message, user_state: dict):
    """Maneja la entrada de archivos del usuario cuando el bot está esperando uno."""
    user_id = message.from_user.id
    state, data = user_state['status'], user_state['data']
    task_id, source_message_id = data.get('task_id'), data.get('source_message_id')
    
    if not task_id or not source_message_id:
        logger.warning(f"Estado '{state}' para {user_id} sin task_id o source_message_id. Reseteando.")
        await db_instance.set_user_state(user_id, "idle")
        return

    media = message.photo or message.document or message.audio
    if not media: return
    
    state_map = {
        "awaiting_audiothumb": "thumbnail_file_id", 
        "awaiting_subs": "subs_file_id", 
        "awaiting_watermark_image": "watermark", 
        "awaiting_thumbnail_add": "thumbnail_file_id", 
        "awaiting_replace_audio": "replace_audio_file_id"
    }

    if state not in state_map: return

    if state in ["awaiting_audiothumb", "awaiting_watermark_image", "awaiting_thumbnail_add"]:
        if not (message.photo or (hasattr(media, 'mime_type') and media.mime_type.startswith("image/"))):
            return await message.reply("❌ No es una imagen válida.", quote=True)

    key_to_update = state_map[state]
    value_to_set = {"type": "image", "file_id": media.file_id} if state == "awaiting_watermark_image" else media.file_id
    
    update_query = {"$set": {f"processing_config.{key_to_update}": value_to_set}}
    if state == "awaiting_thumbnail_add":
        update_query["$unset"] = {"processing_config.extract_thumbnail": "", "processing_config.remove_thumbnail": ""}

    await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, update_query)
    await message.delete()

    if state == "awaiting_watermark_image":
        await db_instance.set_user_state(user_id, 'awaiting_watermark_position', data=data)
        await client.edit_message_text(user_id, source_message_id, "✅ Imagen recibida. Elija la posición:", reply_markup=build_position_menu(task_id, "config_watermark"))
    else:
        await db_instance.set_user_state(user_id, "idle")
        source_message = await client.get_messages(user_id, source_message_id)
        await open_task_menu_from_p(client, source_message, task_id)


# --- Routers de Callback ---

@Client.on_callback_query(filters.regex(r"^(p_open_|task_|config_|set_)"))
async def main_config_callbacks_router(client: Client, query: CallbackQuery):
    try:
        data = query.data
        if data.startswith("p_open_"): await open_task_menu_callback(client, query)
        elif data.startswith("task_"): await handle_task_actions(client, query)
        elif data.startswith("config_"): await show_config_menu_and_set_state(client, query)
        elif data.startswith("set_"): await set_value_callback(client, query)
    except MessageNotModified: await query.answer("Nada que cambiar.")
    except Exception as e: logger.error(f"Error en main_config_callbacks_router: {e}", exc_info=True)

@Client.on_callback_query(filters.regex(r"^(profile_|batch_|join_|zip_|panel_delete_all_)"))
async def advanced_features_callbacks_router(client: Client, query: CallbackQuery):
    try:
        data = query.data
        if data.startswith("profile_"): await handle_profile_actions(client, query)
        elif data.startswith("batch_"): await handle_batch_actions(client, query)
        elif data.startswith("join_"): await handle_join_actions(client, query)
        elif data.startswith("zip_"): await handle_zip_actions(client, query)
        elif data.startswith("panel_delete_all_"): await handle_panel_delete_all(client, query)
    except MessageNotModified: await query.answer("Nada que cambiar.")
    except Exception as e: logger.error(f"Error en advanced_features_callbacks_router: {e}", exc_info=True)


# --- Manejadores de Lógica de Callback Específicos ---

async def open_task_menu_callback(client: Client, query: CallbackQuery):
    await query.answer()
    task_id = query.data.split("_")[2]
    await db_instance.set_user_state(query.from_user.id, "idle")
    await open_task_menu_from_p(client, query, task_id)

async def handle_task_actions(client: Client, query: CallbackQuery):
    parts = query.data.split("_"); action, task_id = parts[1], "_".join(parts[2:])
    
    if action == "queuesingle":
        await query.answer("Enviando a la forja...")
        ref = {"chat_id": query.message.chat.id, "message_id": query.message.id}
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"status": "queued", "status_message_ref": ref}})
        await query.message.edit_text("⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)
    
    elif action == "delete":
        await query.answer("Tarea eliminada.")
        await db_instance.delete_task_by_id(task_id)
        await query.message.edit_text("🗑️ Tarea cancelada.")
    
    await db_instance.set_user_state(query.from_user.id, "idle")

async def show_config_menu_and_set_state(client: Client, query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    parts = query.data.split("_")
    menu_type, task_id = parts[1], "_".join(parts[2:])
    
    task = await db_instance.get_task(task_id)
    if not task: return await query.message.delete()
    
    config = task.get('processing_config', {})
    
    # Menús que no requieren cambio de estado
    if menu_type == "extract_audio":
        ref = {"chat_id": query.message.chat.id, "message_id": query.message.id}
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"processing_config.extract_audio": True, "status": "queued", "status_message_ref": ref}})
        return await query.message.edit_text("✅ Tarea de extracción de audio enviada.\n⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)
    
    keyboards = {
        "transcode": build_transcode_menu(task_id),
        "tracks": build_tracks_menu(task_id, config),
        "audioconvert": build_audio_convert_menu(task_id),
        "audioeffects": build_audio_effects_menu(task_id, config),
        "audiometadata": build_audio_metadata_menu(task_id),
        "watermark": build_watermark_menu(task_id),
        "thumbnail": build_thumbnail_menu(task_id, config)
    }
    menu_messages = {
        "transcode": "📉 Seleccione resolución:", "tracks": "📜 Gestione pistas:",
        "audioconvert": "🔊 Convierta audio:", "audioeffects": "🎧 Aplique efectos:",
        "audiometadata": "🖼️ Edite metadatos:", "watermark": "💧 Añada marca de agua:",
        "thumbnail": "🖼️ Gestione miniatura:"
    }
    if menu_type in keyboards:
        return await query.message.edit_text(text=menu_messages[menu_type], reply_markup=keyboards[menu_type])
    
    # Menús que requieren cambio de estado para recibir input
    state_map = {
        "rename": "awaiting_rename", "trim": "awaiting_trim", "split": "awaiting_split",
        "gif": "awaiting_gif", "audiotags": "awaiting_audiotags", "audiothumb": "awaiting_audiothumb",
        "addsubs": "awaiting_subs", "thumbnail_add": "awaiting_thumbnail_add",
        "replace_audio": "awaiting_replace_audio", "watermark_text": "awaiting_watermark_text",
        "watermark_image": "awaiting_watermark_image", "profile_save_request": "awaiting_profile_name"
    }
    if menu_type not in state_map: return
    
    await db_instance.set_user_state(user_id, state_map[menu_type], data={"task_id": task_id, "source_message_id": query.message.id})
    
    menu_texts = {
        "rename": "✏️ Envíe el nuevo nombre (sin extensión).",
        "trim": "✂️ Envíe tiempos de corte. Formatos:\n• <code>00:10-00:50</code> (inicio-fin)\n• <code>01:23</code> (corta desde el inicio hasta ese punto)",
        "split": "🧩 Envíe criterio de división (ej: <code>300s</code> para trozos de 5 min).",
        "gif": "🎞️ Envíe duración y FPS (ej: <code>5 15</code> para 5s de GIF a 15fps).",
        "audiotags": "✍️ Envíe los nuevos metadatos, uno por línea:\n<code>Título: Mi Canción\nArtista: Yo Mismo</code>",
        "audiothumb": "🖼️ Envíe la imagen que desea usar como carátula.",
        "addsubs": "➕ Envíe el archivo de subtítulos (<code>.srt</code>).",
        "thumbnail_add": "🖼️ Envíe la nueva imagen para la miniatura del video.",
        "replace_audio": "🎼 Envíe el archivo de audio que reemplazará al original.",
        "watermark_text": "💧 Envíe el texto para la marca de agua.",
        "watermark_image": "🖼️ Envíe la imagen para la marca de agua.",
        "profile_save_request": "✏️ Envíe un nombre para este perfil de configuración."
    }
    
    back_callbacks = {
        "audiotags": "config_audiometadata_", "audiothumb": "config_audiometadata_",
        "addsubs": "config_tracks_", "thumbnail_add": "config_thumbnail_",
        "replace_audio": "config_tracks_", "watermark_text": "config_watermark_",
        "watermark_image": "config_watermark_", "profile_save_request": "p_open_"
    }
    
    await query.message.edit_text(
        menu_texts[menu_type],
        reply_markup=build_back_button(f"{back_callbacks.get(menu_type, 'p_open_')}{task_id}"),
        parse_mode=ParseMode.HTML
    )


async def set_value_callback(client: Client, query: CallbackQuery):
    await query.answer()
    user_id, parts = query.from_user.id, query.data.split("_")
    config_type, task_id = parts[1], parts[2]
    
    task = await db_instance.get_task(task_id)
    if not task: return await query.message.delete()
    config = task.get('processing_config', {})
    
    if config_type == "watermark" and parts[3] == "position":
        await db_instance.update_task_config(task_id, "watermark.position", parts[4].replace('-', '_'))
        await db_instance.set_user_state(user_id, "idle")
    
    elif config_type == "transcode":
        value = "_".join(parts[3:])
        if value == "remove_all":
            await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.transcode": ""}})
        else:
            prop, val = value.split('_', 1)
            await db_instance.update_task_config(task_id, f"transcode.{prop}", val)
            
    elif config_type == "watermark" and parts[3] == "remove":
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.watermark": ""}})
        
    elif config_type == "thumb_op":
        op, current_val = parts[3], config.get(f"{op}_thumbnail", False)
        update_q = {"$set": {f"processing_config.{op}_thumbnail": not current_val}}
        # Si se activa una opción, desactivar la otra y cualquier thumb personalizado.
        if not current_val:
            other_op = 'remove' if op == 'extract' else 'extract'
            update_q.update({"$unset": {f"processing_config.{other_op}_thumbnail": "", "processing_config.thumbnail_file_id": ""}})
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, update_q)
        
    elif config_type == "mute":
        await db_instance.update_task_config(task_id, 'mute_audio', not config.get('mute_audio', False))
    elif config_type == "audioprop":
        await db_instance.update_task_config(task_id, f"audio_{parts[3]}", parts[4])
    elif config_type == "audioeffect":
        await db_instance.update_task_config(task_id, f"{parts[3]}", not config.get(parts[3], False))
    elif config_type == "trackopt":
        await db_instance.update_task_config(task_id, f"{parts[3]}", not config.get(parts[3], False))

    await open_task_menu_from_p(client, query, task_id)

async def handle_profile_actions(client: Client, query: CallbackQuery):
    user_id = query.from_user.id; parts = query.data.split("_"); action = parts[1]
    
    if action == "apply":
        task_id, preset_id = parts[2], parts[3]
        preset = await db_instance.get_preset_by_id(preset_id)
        if not preset: return await query.answer("❌ Perfil no encontrado.", show_alert=True)
        await db_instance.update_task_field(task_id, "processing_config", preset['config_data'])
        await query.answer("✅ Perfil aplicado.", show_alert=True)
        await open_task_menu_from_p(client, query, task_id)

    elif action == "delete":
        if parts[2] == "req":
            preset_id = parts[3]
            await query.message.edit_text("¿Seguro que desea eliminar este perfil?", reply_markup=build_profile_delete_confirmation_keyboard(preset_id))
        elif parts[2] == "confirm":
            preset_id = parts[3]
            await db_instance.delete_preset_by_id(preset_id)
            presets = await db_instance.get_user_presets(user_id)
            await query.message.edit_text("✅ Perfil eliminado.", reply_markup=build_profiles_management_keyboard(presets))

    elif action == "open" and parts[2] == "main":
        presets = await db_instance.get_user_presets(user_id)
        await query.message.edit_text("<b>Gestión de Perfiles:</b>", reply_markup=build_profiles_management_keyboard(presets), parse_mode=ParseMode.HTML)
    
    elif action == "close":
        await query.message.delete()

async def handle_batch_actions(client: Client, query: CallbackQuery):
    user_id = query.from_user.id; parts = query.data.split("_"); action = parts[1]
    
    if action == "cancel": return await query.message.delete()
    
    config_to_apply = {}
    profile_id = "default"
    if action == "apply": profile_id = parts[2]
    
    tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
    if not tasks: return await query.message.edit_text("❌ No hay tareas en el panel para procesar.")
    
    if profile_id != "default":
        preset = await db_instance.get_preset_by_id(profile_id)
        config_to_apply = preset.get('config_data', {}) if preset else {}
    
    await query.message.edit_text(f"🔥 Procesando en lote {len(tasks)} tareas...")
    for task in tasks:
        tid = str(task['_id'])
        await db_instance.update_task_field(tid, "processing_config", config_to_apply)
        await db_instance.update_task_field(tid, "status", "queued")
    
    await query.message.edit_text(f"✅ ¡Listo! {len(tasks)} tareas enviadas a la cola.")

async def handle_join_actions(client: Client, query: CallbackQuery):
    user_id = query.from_user.id; parts = query.data.split("_"); action = parts[1]
    state = await db_instance.get_user_state(user_id)
    selected_ids = state.get("data", {}).get("selected_ids", [])

    if action == "select":
        task_id = parts[2]
        if task_id in selected_ids: selected_ids.remove(task_id)
        else: selected_ids.append(task_id)
        await db_instance.set_user_state(user_id, "selecting_join_files", data={"selected_ids": selected_ids})
        tasks = await db_instance.get_pending_tasks(user_id, file_type_filter="video")
        await query.message.edit_reply_markup(reply_markup=build_join_selection_keyboard(tasks, selected_ids))

    elif action == "confirm":
        if len(selected_ids) < 2: return await query.answer("❌ Debe seleccionar al menos 2 videos.", show_alert=True)
        # Crear la tarea de unión
        await db_instance.add_task(user_id, 'join_operation', 
                                   file_name=f"Join_{len(selected_ids)}_videos.mp4",
                                   final_filename=f"Union_{datetime.now().strftime('%Y%m%d_%H%M')}",
                                   status="queued", 
                                   custom_fields={"source_task_ids": [ObjectId(tid) for tid in selected_ids]})
        # Marcar tareas originales para que no se procesen individualmente
        await db_instance.tasks.update_many(
            {"_id": {"$in": [ObjectId(tid) for tid in selected_ids]}},
            {"$set": {"status": "used_in_join"}}
        )
        await query.message.edit_text(f"✅ Tarea de unión para {len(selected_ids)} videos enviada a la cola.")
        await db_instance.set_user_state(user_id, "idle")

    elif action == "cancel":
        await db_instance.set_user_state(user_id, "idle")
        await query.message.delete()

async def handle_zip_actions(client: Client, query: CallbackQuery):
    user_id = query.from_user.id; parts = query.data.split("_"); action = parts[1]
    state = await db_instance.get_user_state(user_id)
    selected_ids = state.get("data", {}).get("selected_ids", [])

    if action == "select":
        task_id = parts[2]
        if task_id in selected_ids: selected_ids.remove(task_id)
        else: selected_ids.append(task_id)
        await db_instance.set_user_state(user_id, "selecting_zip_files", data={"selected_ids": selected_ids})
        tasks = await db_instance.get_pending_tasks(user_id)
        await query.message.edit_reply_markup(reply_markup=build_zip_selection_keyboard(tasks, selected_ids))

    elif action == "confirm":
        if not selected_ids: return await query.answer("❌ No ha seleccionado archivos.", show_alert=True)
        await db_instance.add_task(user_id, 'zip_operation',
                                   file_name=f"Zip_{len(selected_ids)}_files.zip",
                                   final_filename=f"Comprimido_{datetime.now().strftime('%Y%m%d_%H%M')}",
                                   status="queued",
                                   custom_fields={"source_task_ids": [ObjectId(tid) for tid in selected_ids]})
        await db_instance.tasks.update_many(
            {"_id": {"$in": [ObjectId(tid) for tid in selected_ids]}},
            {"$set": {"status": "used_in_zip"}}
        )
        await query.message.edit_text(f"✅ Tarea de compresión para {len(selected_ids)} archivos enviada a la cola.")
        await db_instance.set_user_state(user_id, "idle")
        
    elif action == "cancel":
        await db_instance.set_user_state(user_id, "idle")
        await query.message.delete()

async def handle_panel_delete_all(client: Client, query: CallbackQuery):
    action = query.data.split("_")[-1]
    if action == "confirm":
        deleted = await db_instance.delete_all_pending_tasks(query.from_user.id)
        await query.message.edit_text(f"💥 Panel limpiado. Se descartaron {deleted.deleted_count} tareas.")
    elif action == "cancel":
        await query.message.edit_text("Operación cancelada.")