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

async def open_task_menu_from_p(client: Client, message_or_query, task_id: str):
    task = await db_instance.get_task(task_id)
    if not task:
        text = "❌ Error: La tarea ya no existe."
        if isinstance(message_or_query, CallbackQuery): return await message_or_query.answer(text, show_alert=True)
        else: return await message_or_query.reply(text)

    text_content = f"🛠️ <b>Configurando Tarea:</b>\n<code>{escape_html(task.get('original_filename', '...'))}</code>"
    markup = build_processing_menu(task_id, task['file_type'], task)
    
    try:
        if isinstance(message_or_query, CallbackQuery):
            await message_or_query.message.edit_text(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)
        else:
            await message_or_query.reply(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)
    except MessageNotModified:
        pass

async def handle_text_input_for_state(client: Client, message: Message, user_state: dict):
    user_id = message.from_user.id
    user_input = message.text.strip()
    state, data = user_state['status'], user_state['data']
    task_id, source_message_id = data.get('task_id'), data.get('source_message_id')
    
    if not source_message_id: return
    try:
        if state == "awaiting_profile_name":
            task = await db_instance.get_task(task_id)
            if not task: return
            await db_instance.add_preset(user_id, user_input, task.get('processing_config', {}))
            await message.reply(f"✅ Perfil '<b>{escape_html(user_input)}</b>' guardado.", parse_mode=ParseMode.HTML, quote=True)
        elif state == "awaiting_rename": await db_instance.update_task_config(task_id, "final_filename", sanitize_filename(user_input))
        elif state == "awaiting_trim": await db_instance.update_task_config(task_id, "trim_times", user_input)
        elif state == "awaiting_split": await db_instance.update_task_config(task_id, "split_criteria", user_input)
        elif state == "awaiting_gif":
            parts = user_input.split(); await db_instance.update_task_config(task_id, "gif_options", {"duration": float(parts[0]), "fps": int(parts[1])})
        elif state == "awaiting_audiotags":
            tags_to_update = {}; [tags_to_update.update({{'título': 'title', 'titulo': 'title', 'artista': 'artist', 'artist': 'artist', 'álbum': 'album', 'album': 'album'}.get(k.lower()): v}) for k, v in (line.split(':', 1) for line in user_input.split('\n') if ':' in line)]; await db_instance.update_task_config(task_id, "audio_tags", tags_to_update)
        elif state == "awaiting_watermark_text":
            await db_instance.update_task_config(task_id, "watermark", {"type": "text", "text": user_input}); await message.delete(); await db_instance.set_user_state(user_id, 'awaiting_watermark_position', data=data); return await client.edit_message_text(user_id, source_message_id, text="✅ Texto recibido. Elija la posición:", reply_markup=build_position_menu(task_id, "config_watermark"))
        else: return
        await message.delete(); await db_instance.set_user_state(user_id, "idle"); source_message = await client.get_messages(user_id, source_message_id); await open_task_menu_from_p(client, source_message, task_id)
    except Exception as e:
        logger.error(f"Error procesando entrada de config '{state}': {e}"); await message.reply(f"❌ Error: `{e}`", quote=True)

async def handle_media_input_for_state(client: Client, message: Message, user_state: dict):
    user_id = message.from_user.id
    state, data = user_state['status'], user_state['data']
    task_id, source_message_id = data.get('task_id'), data.get('source_message_id')
    if not source_message_id: return
    media = message.photo or message.document or message.audio
    if not media: return
    state_map = {"awaiting_audiothumb": "thumbnail_file_id", "awaiting_subs": "subs_file_id", "awaiting_watermark_image": "watermark", "awaiting_thumbnail_add": "thumbnail_file_id", "awaiting_replace_audio": "replace_audio_file_id"}
    if state not in state_map: return
    if state in ["awaiting_audiothumb", "awaiting_watermark_image", "awaiting_thumbnail_add"] and not (message.photo or (hasattr(media, 'mime_type') and media.mime_type.startswith("image/"))): return await message.reply("❌ No es una imagen válida.")
    key_to_update = state_map[state]
    value_to_set = {"type": "image", "file_id": media.file_id} if state == "awaiting_watermark_image" else media.file_id
    update_query = {"$set": {f"processing_config.{key_to_update}": value_to_set}}
    if state == "awaiting_thumbnail_add": update_query["$unset"] = {"processing_config.extract_thumbnail": "", "processing_config.remove_thumbnail": ""}
    await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, update_query); await message.delete()
    if state == "awaiting_watermark_image":
        await db_instance.set_user_state(user_id, 'awaiting_watermark_position', data=data); await client.edit_message_text(user_id, source_message_id, "✅ Imagen recibida. Elija la posición:", reply_markup=build_position_menu(task_id, "config_watermark"))
    else:
        await db_instance.set_user_state(user_id, "idle"); source_message = await client.get_messages(user_id, source_message_id); await open_task_menu_from_p(client, source_message, task_id)

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

@Client.on_callback_query(filters.regex(r"^(song_select_|search_page_|cancel_search_)"))
async def search_callbacks_router(client: Client, query: CallbackQuery):
    try:
        data = query.data
        if data.startswith("song_select_"): await select_song_from_search(client, query)
        elif data.startswith("search_page_"): await handle_search_pagination(client, query)
        elif data.startswith("cancel_search_"): await cancel_search_session(client, query)
    except MessageNotModified: await query.answer("Nada que cambiar.")
    except Exception as e: logger.error(f"Error en search_callbacks_router: {e}", exc_info=True)

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

async def open_task_menu_callback(client: Client, query: CallbackQuery):
    await query.answer(); task_id = query.data.split("_")[2]; await db_instance.set_user_state(query.from_user.id, "idle"); await open_task_menu_from_p(client, query, task_id)

async def handle_task_actions(client: Client, query: CallbackQuery):
    parts = query.data.split("_"); action, task_id = parts[1], "_".join(parts[2:])
    if action == "queuesingle":
        await query.answer("Enviando a la forja..."); ref = {"chat_id": query.message.chat.id, "message_id": query.message.id}; await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"status": "queued", "status_message_ref": ref}}); await query.message.edit_text("⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)
    elif action == "delete":
        await query.answer("Tarea eliminada."); await db_instance.delete_task_by_id(task_id); await query.message.edit_text("🗑️ Tarea cancelada.")
    await db_instance.set_user_state(query.from_user.id, "idle")

async def show_config_menu_and_set_state(client: Client, query: CallbackQuery):
    await query.answer(); user_id = query.from_user.id; parts = query.data.split("_"); menu_type, task_id = parts[1], "_".join(parts[2:])
    task = await db_instance.get_task(task_id)
    if not task: return
    config = task.get('processing_config', {})
    if menu_type == "extract_audio":
        ref = {"chat_id": query.message.chat.id, "message_id": query.message.id}; await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"processing_config.extract_audio": True, "status": "queued", "status_message_ref": ref}}); return await query.message.edit_text("✅ Tarea de extracción de audio enviada.\n⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)
    keyboards = {"transcode": build_transcode_menu(task_id), "tracks": build_tracks_menu(task_id, config), "audioconvert": build_audio_convert_menu(task_id), "audioeffects": build_audio_effects_menu(task_id, config), "audiometadata": build_audio_metadata_menu(task_id), "watermark": build_watermark_menu(task_id), "thumbnail": build_thumbnail_menu(task_id, config)}
    menu_messages = {"transcode": "📉 Seleccione resolución:", "tracks": "📜 Gestione pistas:", "audioconvert": "🔊 Convierta audio:", "audioeffects": "🎧 Aplique efectos:", "audiometadata": "🖼️ Edite metadatos:", "watermark": "💧 Añada marca de agua:", "thumbnail": "🖼️ Gestione miniatura:"}
    if menu_type in keyboards: return await query.message.edit_text(text=menu_messages[menu_type], reply_markup=keyboards[menu_type])
    state_map = {"rename": "awaiting_rename", "trim": "awaiting_trim", "split": "awaiting_split", "gif": "awaiting_gif", "audiotags": "awaiting_audiotags", "audiothumb": "awaiting_audiothumb", "addsubs": "awaiting_subs", "thumbnail_add": "awaiting_thumbnail_add", "replace_audio": "awaiting_replace_audio", "watermark_text": "awaiting_watermark_text", "watermark_image": "awaiting_watermark_image", "profile_save_request": "awaiting_profile_name"}
    if menu_type not in state_map: return
    await db_instance.set_user_state(user_id, state_map[menu_type], data={"task_id": task_id, "source_message_id": query.message.id})
    menu_texts = {"rename": "✏️ Envíe el nuevo nombre.", "trim": "✂️ Envíe tiempos de corte (<code>INICIO-FIN</code>).", "split": "🧩 Envíe criterio de división (<code>300s</code>).", "gif": "🎞️ Envíe duración y FPS (<code>5 15</code>).", "audiotags": "✍️ Envíe nuevos metadatos (<code>Título: X</code>).", "audiothumb": "🖼️ Envíe la carátula.", "addsubs": "➕ Envíe el archivo <code>.srt</code>.", "thumbnail_add": "🖼️ Envíe la nueva miniatura.", "replace_audio": "🎼 Envíe el nuevo audio.", "watermark_text": "💧 Envíe el texto de la marca.", "watermark_image": "🖼️ Envíe la imagen de la marca.", "profile_save_request": "✏️ Envíe un nombre para este perfil."}
    back_callbacks = {"audiotags": "config_audiometadata_", "audiothumb": "config_audiometadata_", "addsubs": "config_tracks_", "thumbnail_add": "config_thumbnail_", "replace_audio": "config_tracks_", "watermark_text": "config_watermark_", "watermark_image": "config_watermark_", "profile_save_request": "p_open_"}
    await query.message.edit_text(menu_texts[menu_type], reply_markup=build_back_button(f"{back_callbacks.get(menu_type, 'p_open_')}{task_id}"), parse_mode=ParseMode.HTML)

async def set_value_callback(client: Client, query: CallbackQuery):
    await query.answer(); user_id, parts = query.from_user.id, query.data.split("_"); config_type, task_id = parts[1], parts[2]
    task = await db_instance.get_task(task_id)
    if not task: return await query.message.delete()
    config = task.get('processing_config', {})
    if config_type == "watermark" and parts[3] == "position": await db_instance.update_task_config(task_id, "watermark.position", parts[4].replace('-', '_')); await db_instance.set_user_state(user_id, "idle")
    else:
        if config_type == "transcode": value = "_".join(parts[3:]); await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.transcode": ""}}) if value == "remove_all" else await db_instance.update_task_config(task_id, f"transcode.{value.split('_', 1)[0]}", value.split('_', 1)[1])
        elif config_type == "watermark" and parts[3] == "remove": await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.watermark": ""}})
        elif config_type == "thumb_op": op, current_val = parts[3], config.get(f"{op}_thumbnail", False); update_q = {"$set": {f"processing_config.{op}_thumbnail": not current_val}}; (not current_val) and update_q.update({"$unset": {f"processing_config.{'remove' if op == 'extract' else 'extract'}_thumbnail": "", "processing_config.thumbnail_file_id": ""}}); await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, update_q)
        elif config_type == "mute": await db_instance.update_task_config(task_id, 'mute_audio', not config.get('mute_audio', False))
        elif config_type == "audioprop": await db_instance.update_task_config(task_id, f"audio_{parts[3]}", parts[4])
        elif config_type == "audioeffect": await db_instance.update_task_config(task_id, f"{parts[3]}", not config.get(parts[3], False))
        elif config_type == "trackopt": await db_instance.update_task_config(task_id, f"{parts[3]}", not config.get(parts[3], False))
    await open_task_menu_from_p(client, query, task_id)

async def handle_profile_actions(client: Client, query: CallbackQuery):
    user_id = query.from_user.id; action, parts = query.data.split("_")[1], query.data.split("_")
    if action == "apply":
        task_id, preset_id = parts[2], parts[3]; preset = await db_instance.get_preset_by_id(preset_id)
        if not preset: return await query.answer("❌ Perfil no encontrado.", show_alert=True)
        await db_instance.update_task_field(task_id, "processing_config", preset['config_data']); await query.answer("✅ Perfil aplicado.", show_alert=True); await open_task_menu_from_p(client, query, task_id)
    elif action == "delete" and parts[2] == "req":
        preset_id = parts[3]; await query.message.edit_text("¿Seguro que desea eliminar este perfil?", reply_markup=build_profile_delete_confirmation_keyboard(preset_id))
    elif action == "delete" and parts[2] == "confirm":
        preset_id = parts[3]; await db_instance.delete_preset_by_id(preset_id); presets = await db_instance.get_user_presets(user_id); await query.message.edit_text("✅ Perfil eliminado.", reply_markup=build_profiles_management_keyboard(presets))
    elif action == "open" and parts[2] == "main": presets = await db_instance.get_user_presets(user_id); await query.message.edit_text("<b>Gestión de Perfiles:</b>", reply_markup=build_profiles_management_keyboard(presets), parse_mode=ParseMode.HTML)
    elif action == "close": await query.message.delete()

async def handle_batch_actions(client: Client, query: CallbackQuery):
    user_id = query.from_user.id; action, parts = query.data.split("_")[1], query.data.split("_")
    if action == "cancel": return await query.message.delete()
    config_to_apply = {}; profile_id = "default"
    if action == "apply": profile_id = parts[2]
    tasks = await db_instance.get_pending_tasks(user_id)
    if not tasks: return await query.message.edit_text("No hay tareas en el panel.")
    if profile_id != "default": preset = await db_instance.get_preset_by_id(profile_id); config_to_apply = preset.get('config_data', {}) if preset else {}
    await query.message.edit_text(f"🔥 Procesando en lote {len(tasks)} tareas...")
    for task in tasks: tid = str(task['_id']); await db_instance.update_task_field(tid, "processing_config", config_to_apply); await db_instance.update_task_field(tid, "status", "queued")
    await query.message.edit_text(f"✅ ¡Listo! {len(tasks)} tareas enviadas a la cola.")

async def handle_join_actions(client: Client, query: CallbackQuery):
    user_id = query.from_user.id; action, parts = query.data.split("_")[1], query.data.split("_"); state = await db_instance.get_user_state(user_id); selected_ids = state.get("data", {}).get("selected_ids", [])
    if action == "select":
        task_id = parts[2]; selected_ids.remove(task_id) if task_id in selected_ids else selected_ids.append(task_id); await db_instance.set_user_state(user_id, "selecting_join_files", data={"selected_ids": selected_ids}); tasks = await db_instance.get_pending_tasks(user_id, file_type_filter="video"); await query.message.edit_text("Seleccione los videos para unir:", reply_markup=build_join_selection_keyboard(tasks, selected_ids))
    elif action == "confirm":
        if len(selected_ids) < 2: return await query.answer("❌ Debe seleccionar al menos 2 videos.", show_alert=True)
        await db_instance.add_task(user_id, 'join_operation', file_name=f"Join_{len(selected_ids)}videos.mp4", status="queued", custom_fields={"source_task_ids": [ObjectId(tid) for tid in selected_ids]}); await query.message.edit_text(f"✅ Tarea de unión para {len(selected_ids)} videos enviada a la cola."); await db_instance.set_user_state(user_id, "idle")
    elif action == "cancel": await db_instance.set_user_state(user_id, "idle"); await query.message.delete()

async def handle_zip_actions(client: Client, query: CallbackQuery):
    user_id = query.from_user.id; action, parts = query.data.split("_")[1], query.data.split("_"); state = await db_instance.get_user_state(user_id); selected_ids = state.get("data", {}).get("selected_ids", [])
    if action == "select":
        task_id = parts[2]; selected_ids.remove(task_id) if task_id in selected_ids else selected_ids.append(task_id); await db_instance.set_user_state(user_id, "selecting_zip_files", data={"selected_ids": selected_ids}); tasks = await db_instance.get_pending_tasks(user_id); await query.message.edit_text("Seleccione archivos para comprimir:", reply_markup=build_zip_selection_keyboard(tasks, selected_ids))
    elif action == "confirm":
        if not selected_ids: return await query.answer("❌ No ha seleccionado archivos.", show_alert=True)
        await db_instance.add_task(user_id, 'zip_operation', file_name=f"Zip_{len(selected_ids)}files.zip", status="queued", custom_fields={"source_task_ids": [ObjectId(tid) for tid in selected_ids]}); await query.message.edit_text(f"✅ Tarea de compresión para {len(selected_ids)} archivos enviada a la cola."); await db_instance.set_user_state(user_id, "idle")
    elif action == "cancel": await db_instance.set_user_state(user_id, "idle"); await query.message.delete()

async def handle_panel_delete_all(client: Client, query: CallbackQuery):
    action = query.data.split("_")[-1]
    if action == "confirm": deleted = await db_instance.delete_all_pending_tasks(query.from_user.id); await query.message.edit_text(f"💥 Panel limpiado. Se descartaron {deleted.deleted_count} tareas.")
    elif action == "cancel": await query.message.edit_text("Operación cancelada.")

async def select_song_from_search(client: Client, query: CallbackQuery):
    await query.answer("Preparando descarga...", show_alert=False)
    result_id = query.data.split("_")[2]
    search_result = await db_instance.search_results.find_one({"_id": ObjectId(result_id)})
    if not search_result: return await query.message.edit_text("❌ Este resultado de búsqueda ha expirado.")
    search_term = search_result.get('search_term', f"{search_result['artist']} {search_result['title']}")
    await query.message.edit_text(f"🔎 Buscando audio para:\n<code>{escape_html(search_term)}</code>", parse_mode=ParseMode.HTML)
    url_info = await asyncio.to_thread(downloader.get_url_info, f"ytsearch1:{search_term}")
    if not url_info or not url_info.get('url'): return await query.message.edit_text("❌ No pude encontrar una fuente de audio descargable.")
    task_id = await db_instance.add_task(user_id=query.from_user.id, file_type='audio', file_name=f"{search_result['artist']} - {search_result['title']}", url=url_info['url'], status="queued", processing_config={"download_format_id": downloader.get_best_audio_format_id(url_info.get('formats', [])), "audio_tags": {'title': search_result['title'], 'artist': search_result['artist'], 'album': search_result.get('album')}}, url_info=url_info)
    if thumbnail_url := search_result.get('thumbnail'):
        thumb_path = os.path.join(DOWNLOAD_DIR, f"thumb_{task_id}.jpg")
        if await asyncio.to_thread(downloader.download_thumbnail, thumbnail_url, thumb_path): await db_instance.update_task_config(task_id, "thumbnail_path", thumb_path)
    await query.message.edit_text(f"✅ <b>¡Enviado a la cola!</b>\n🎧 <code>{escape_html(search_result['title'])}</code>", parse_mode=ParseMode.HTML)

async def handle_search_pagination(client: Client, query: CallbackQuery):
    await query.answer(); _, search_id, page_str = query.data.split("_"); page = int(page_str)
    results = await db_instance.search_results.find({"search_id": search_id}).to_list(length=100)
    if not results: return await query.message.edit_text("❌ La sesión de búsqueda ha expirado.")
    await query.message.edit_reply_markup(reply_markup=build_search_results_keyboard(results, search_id, page))

async def cancel_search_session(client: Client, query: CallbackQuery):
    await query.answer("Búsqueda cancelada."); await query.message.delete()