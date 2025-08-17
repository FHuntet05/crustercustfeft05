# --- START OF FILE src/plugins/processing_handler.py ---

import logging
import os
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import *
from src.helpers.utils import sanitize_filename

logger = logging.getLogger(__name__)

# --- Funciones de Apertura de Menú y Manejo de Estado ---

async def open_task_menu_from_p(client: Client, message_or_query, task_id: str):
    """Abre el menú de configuración principal para una tarea específica."""
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
        
        # [FIX] Lógica de edición robusta para evitar borrado de mensajes
        target_message = message_or_query.message if isinstance(message_or_query, CallbackQuery) else message_or_query
        try:
            await target_message.edit_text(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)
        except MessageNotModified:
            if isinstance(message_or_query, CallbackQuery): await message_or_query.answer("Nada que cambiar.")
        except AttributeError: # Si es un mensaje nuevo y no se puede editar
             await message_or_query.reply(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Error en open_task_menu_from_p: {e}", exc_info=True)


async def handle_text_input_for_state(client: Client, message: Message, user_state: dict):
    """Maneja la entrada de texto del usuario cuando el bot está esperando una configuración."""
    user_id, user_input = message.from_user.id, message.text.strip()
    state, data = user_state['status'], user_state['data']
    task_id, source_message_id = data.get('task_id'), data.get('source_message_id')
    
    if not task_id or not source_message_id:
        await db_instance.set_user_state(user_id, "idle"); return

    try:
        task = await db_instance.get_task(task_id)
        if not task:
            await message.reply("❌ La tarea ya no existe.", quote=True)
            await db_instance.set_user_state(user_id, "idle"); return

        if state == "awaiting_profile_name":
            await db_instance.add_preset(user_id, user_input, task.get('processing_config', {}))
            await message.reply(f"✅ Perfil '<b>{escape_html(user_input)}</b>' guardado.", parse_mode=ParseMode.HTML, quote=True)
        elif state == "awaiting_rename":
            await db_instance.update_task_config(task_id, "final_filename", sanitize_filename(os.path.splitext(user_input)[0]))
        elif state == "awaiting_trim":
            await db_instance.update_task_config(task_id, "trim_times", user_input)
        elif state == "awaiting_gif":
            parts = user_input.split()
            if len(parts) == 2 and parts[0].replace('.','',1).isdigit() and parts[1].isdigit():
                await db_instance.update_task_config(task_id, "gif_options", {"duration": float(parts[0]), "fps": int(parts[1])})
            else: await message.reply("❌ Formato inválido. Use: `duración fps` (ej: `5 15`)", quote=True); return
        elif state == "awaiting_watermark_text":
            await db_instance.update_task_config(task_id, "watermark", {"type": "text", "text": user_input})
            await message.delete() # Borra el mensaje de texto del usuario
            # [FIX] Ahora el handler de texto también es responsable de la transición de estado y la edición del mensaje.
            await db_instance.set_user_state(user_id, 'awaiting_watermark_position', data=data)
            await client.edit_message_text(user_id, source_message_id, text="✅ Texto recibido. Elija la posición:", reply_markup=build_position_menu(task_id, "config_watermark"))
            return # Detenemos la ejecución aquí porque ya hemos manejado la respuesta.
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
        # Obtenemos una referencia al mensaje del panel para actualizarlo
        panel_message = await client.get_messages(user_id, source_message_id)
        await open_task_menu_from_p(client, panel_message, task_id)
        
    except Exception as e:
        logger.error(f"Error procesando entrada de config '{state}': {e}", exc_info=True)
        await message.reply(f"❌ Error al procesar su entrada: `{e}`", quote=True)

async def handle_media_input_for_state(client: Client, message: Message, user_state: dict):
    user_id, state, data = message.from_user.id, user_state['status'], user_state['data']
    task_id, source_message_id = data.get('task_id'), data.get('source_message_id')
    if not task_id or not source_message_id:
        await db_instance.set_user_state(user_id, "idle"); return
    media = message.photo or message.document or message.audio
    if not media: return
    state_map = {"awaiting_subs": "subs_file_id", "awaiting_watermark_image": "watermark", "awaiting_thumbnail_add": "thumbnail_file_id", "awaiting_replace_audio": "replace_audio_file_id", "awaiting_audiothumb": "audio_thumbnail_file_id"}
    if state not in state_map: return

    # Validaciones
    if state in ["awaiting_watermark_image", "awaiting_thumbnail_add", "awaiting_audiothumb"]:
        if not (message.photo or (hasattr(media, 'mime_type') and media.mime_type.startswith("image/"))):
            return await message.reply("❌ No es una imagen válida.", quote=True)
    if state == "awaiting_replace_audio" and not message.audio:
        return await message.reply("❌ No es un audio válido.", quote=True)
    if state == "awaiting_subs" and (not message.document or not getattr(message.document, 'file_name', '').lower().endswith('.srt')):
        return await message.reply("❌ No es un documento <code>.srt</code> válido.", quote=True, parse_mode=ParseMode.HTML)

    key_to_update = state_map[state]
    value_to_set = {"type": "image", "file_id": media.file_id} if state == "awaiting_watermark_image" else media.file_id
    update_query = {"$set": {f"processing_config.{key_to_update}": value_to_set}}
    if state == "awaiting_thumbnail_add": update_query["$unset"] = {"processing_config.remove_thumbnail": ""}
    if state == "awaiting_replace_audio": update_query["$unset"] = {"processing_config.mute_audio": "", "processing_config.extract_audio": ""}
    if state == "awaiting_subs": update_query["$unset"] = {"processing_config.remove_subtitles": ""}

    await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, update_query)
    await message.delete()

    if state == "awaiting_watermark_image":
        await db_instance.set_user_state(user_id, 'awaiting_watermark_position', data=data)
        await client.edit_message_text(user_id, source_message_id, "✅ Imagen recibida. Elija la posición:", reply_markup=build_position_menu(task_id, "config_watermark"))
    else:
        await db_instance.set_user_state(user_id, "idle")
        panel_message = await client.get_messages(user_id, source_message_id)
        await open_task_menu_from_p(client, panel_message, task_id)

@Client.on_callback_query(filters.regex(r"^(p_open_|task_|config_|set_)"))
async def main_config_callbacks_router(client: Client, query: CallbackQuery):
    try: await query.answer()
    except Exception: pass
    data = query.data
    if data.startswith("p_open_"): await open_task_menu_callback(client, query)
    elif data.startswith("task_"): await handle_task_actions(client, query)
    elif data.startswith("config_"): await show_config_menu_and_set_state(client, query)
    elif data.startswith("set_"): await set_value_callback(client, query)

@Client.on_callback_query(filters.regex(r"^(profile_|batch_|join_|zip_|panel_delete_all_)"))
async def advanced_features_callbacks_router(client: Client, query: CallbackQuery):
    try: await query.answer()
    except Exception: pass
    data = query.data
    if data.startswith("profile_"): await handle_profile_actions(client, query)
    elif data.startswith("batch_"): await handle_batch_actions(client, query)
    elif data.startswith("join_"): await handle_join_actions(client, query)
    elif data.startswith("zip_"): await handle_zip_actions(client, query)
    elif data.startswith("panel_delete_all_"): await handle_panel_delete_all(client, query)

async def open_task_menu_callback(client: Client, query: CallbackQuery):
    task_id = query.data.split("_")[2]
    await db_instance.set_user_state(query.from_user.id, "idle")
    await open_task_menu_from_p(client, query, task_id)

async def handle_task_actions(client: Client, query: CallbackQuery):
    parts = query.data.split("_"); action, task_id = parts[1], "_".join(parts[2:])
    if action == "queuesingle":
        await db_instance.update_task_field(task_id, "status", "queued")
        await query.message.edit_text("⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)
    elif action == "delete":
        await db_instance.delete_task_by_id(task_id)
        await query.message.edit_text("🗑️ Tarea cancelada.")
    await db_instance.set_user_state(query.from_user.id, "idle")

# [FIX DE FLUJO LÓGICO] - Función refactorizada para manejar la edición de mensajes de forma segura.
async def show_config_menu_and_set_state(client: Client, query: CallbackQuery):
    user_id, data = query.from_user.id, query.data
    parts = data.split("_"); menu_type, task_id = parts[1], "_".join(parts[2:])
    task = await db_instance.get_task(task_id)
    if not task: return await query.message.delete()
    config = task.get('processing_config', {})
    
    # Manejar acciones directas primero
    if menu_type == "extract_audio":
        await db_instance.update_task_config(task_id, "extract_audio", True)
        await db_instance.update_task_field(task_id, "status", "queued")
        return await query.message.edit_text("✅ Tarea de extracción enviada.\n⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)
    
    # Mapas para mostrar sub-menús (sin cambio de estado)
    keyboards = {"transcode": build_transcode_menu(task_id), "tracks": build_tracks_menu(task_id, config), "watermark": build_watermark_menu(task_id), "thumbnail": build_thumbnail_menu(task_id, config), "audiometadata": build_audio_metadata_menu(task_id)}
    menu_messages = {"transcode": "📉 Seleccione resolución:", "tracks": "📜 Gestione pistas:", "watermark": "💧 Añada marca de agua:", "thumbnail": "🖼️ Gestione miniatura:", "audiometadata": "📝 Elija qué metadato editar:"}
    if menu_type in keyboards:
        return await query.message.edit_text(text=menu_messages[menu_type], reply_markup=keyboards[menu_type])
    
    # Mapas para acciones que esperan una entrada (con cambio de estado)
    state_map = {"rename": "awaiting_rename", "trim": "awaiting_trim", "gif": "awaiting_gif", "addsubs": "awaiting_subs", "thumbnail_add": "awaiting_thumbnail_add", "replace_audio": "awaiting_replace_audio", "watermark_text": "awaiting_watermark_text", "watermark_image": "awaiting_watermark_image", "profile_save_request": "awaiting_profile_name", "audiotags": "awaiting_audiotags", "audiothumb": "awaiting_audiothumb"}
    menu_texts = {"rename": "✏️ Envíe el nuevo nombre (sin extensión).", "trim": "✂️ Envíe tiempos de corte:\n• <code>00:10-00:50</code> (inicio-fin)\n• <code>01:23</code> (corta hasta ahí)", "gif": "🎞️ Envíe duración y FPS (ej: <code>5 15</code>).", "addsubs": "➕ Envíe el archivo <code>.srt</code>.", "thumbnail_add": "🖼️ Envíe la nueva miniatura.", "replace_audio": "🎼 Envíe el nuevo audio.", "watermark_text": "💧 Envíe el texto para la marca de agua.", "watermark_image": "🖼️ Envíe la imagen para la marca.", "profile_save_request": "✏️ Envíe un nombre para el perfil.", "audiotags": "✍️ Envíe los metadatos:\n<code>Título: Mi Canción\nArtista: El Artista</code>", "audiothumb": "🖼️ Envíe la imagen de la carátula."}
    
    if menu_type in state_map:
        await db_instance.set_user_state(user_id, state_map[menu_type], data={"task_id": task_id, "source_message_id": query.message.id})
        back_callbacks = {"addsubs": "config_tracks_", "thumbnail_add": "config_thumbnail_", "replace_audio": "config_tracks_", "watermark_text": "config_watermark_", "watermark_image": "config_watermark_", "profile_save_request": "p_open_", "audiotags": "config_audiometadata_", "audiothumb": "config_audiometadata_"}
        back_button = build_back_button(f"{back_callbacks.get(menu_type, 'p_open_')}{task_id}")
        await query.message.edit_text(menu_texts[menu_type], reply_markup=back_button, parse_mode=ParseMode.HTML)

# ... (El resto del archivo, set_value_callback y demás, no necesitan cambios) ...
async def set_value_callback(client: Client, query: CallbackQuery):
    user_id, parts = query.from_user.id, query.data.split("_")
    config_type, task_id = parts[1], parts[2]
    task = await db_instance.get_task(task_id)
    if not task: return await query.message.delete()
    config = task.get('processing_config', {})
    if config_type == "watermark" and parts[3] == "position":
        await db_instance.update_task_config(task_id, "watermark.position", parts[4].replace('-', '_')); await db_instance.set_user_state(user_id, "idle")
    elif config_type == "transcode":
        value = "_".join(parts[3:])
        if value == "remove_all": await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.transcode": ""}})
        else: prop, val = value.split('_', 1); await db_instance.update_task_config(task_id, f"transcode.{prop}", val)
    elif config_type == "watermark" and parts[3] == "remove": await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.watermark": ""}})
    elif config_type == "thumb_op":
        op, current_val = parts[3], config.get(f"{op}_thumbnail", False)
        update_q = {"$set": {f"processing_config.{op}_thumbnail": not current_val}}
        if not current_val: other_op = 'remove' if op == 'extract' else 'extract'; update_q.update({"$unset": {f"processing_config.{other_op}_thumbnail": "", "processing_config.thumbnail_file_id": ""}})
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, update_q)
    elif config_type == "mute": await db_instance.update_task_config(task_id, 'mute_audio', not config.get('mute_audio', False))
    elif config_type == "trackopt": await db_instance.update_task_config(task_id, f"{parts[3]}", not config.get(parts[3], False))
    await open_task_menu_from_p(client, query, task_id)

async def handle_profile_actions(client: Client, query: CallbackQuery):
    user_id, parts, action = query.from_user.id, query.data.split("_"), query.data.split("_")[1]
    if action == "apply":
        task_id, preset_id = parts[2], parts[3]
        preset = await db_instance.get_preset_by_id(preset_id)
        if not preset: return
        await db_instance.update_task_field(task_id, "processing_config", preset['config_data']); await open_task_menu_from_p(client, query, task_id)
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
    if action == "cancel": return await query.message.delete()
    tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
    if not tasks: return await query.message.edit_text("❌ No hay tareas en el panel.")
    config_to_apply = {}
    if action == "apply" and len(parts) > 2:
        if preset := await db_instance.get_preset_by_id(parts[2]): config_to_apply = preset.get('config_data', {})
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
    action = query.data.split("_")[-1]
    if action == "confirm":
        deleted = await db_instance.delete_all_pending_tasks(query.from_user.id)
        await query.message.edit_text(f"💥 Panel limpiado. Se descartaron {deleted.deleted_count} tareas.")
    elif action == "cancel": await query.message.edit_text("Operación cancelada.")