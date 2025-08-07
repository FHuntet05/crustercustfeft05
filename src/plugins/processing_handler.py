# --- START OF FILE src/plugins/processing_handler.py ---

import logging
import asyncio
import re
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ParseMode
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from src.core import downloader
from src.helpers.keyboards import (build_back_button, build_processing_menu, 
                                   build_detailed_format_menu, build_audio_convert_menu, 
                                   build_audio_effects_menu, build_search_results_keyboard, 
                                   build_panel_keyboard, build_watermark_menu, 
                                   build_position_menu, build_audio_metadata_menu,
                                   build_tracks_menu)
from src.helpers.utils import get_greeting, escape_html, sanitize_filename

logger = logging.getLogger(__name__)

# --- MANEJADORES DE CALLBACKS DE BOTONES ---

@Client.on_callback_query(filters.regex(r"^panel_"))
async def on_panel_action(client: Client, query: CallbackQuery):
    await query.answer()
    user, action = query.from_user, query.data.split("_")[1]
    if action == "delete_all":
        count = (await db_instance.tasks.delete_many({"user_id": user.id, "status": "pending_processing"})).deleted_count
        await query.message.edit_text(f"💥 Limpieza completada. Se descartaron {count} tareas.")
    elif action == "show":
        greeting_prefix = get_greeting(user.id)
        pending_tasks = await db_instance.get_pending_tasks(user.id)
        text = f"✅ ¡{greeting_prefix}Su mesa de trabajo está vacía!" if not pending_tasks else f"📋 <b>{greeting_prefix}Su mesa de trabajo actual:</b>"
        await query.message.edit_text(text, reply_markup=build_panel_keyboard(pending_tasks), parse_mode=ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^task_"))
async def on_task_action(client: Client, query: CallbackQuery):
    await query.answer()
    parts = query.data.split("_"); action, task_id = parts[1], "_".join(parts[2:])
    if not (task := await db_instance.get_task(task_id)):
        return await query.message.edit_text("❌ Error: La tarea ya no existe.", reply_markup=None)

    if action == "process":
        filename = task.get('original_filename', '...')
        keyboard = build_processing_menu(task_id, task['file_type'], task, filename)
        await query.message.edit_text(f"🛠️ ¿Qué desea hacer con:\n<code>{escape_html(filename)}</code>?", reply_markup=keyboard, parse_mode=ParseMode.HTML)
    elif action == "queuesingle":
        await db_instance.update_task(task_id, "status", "queued")
        await query.message.edit_text("🔥 Tarea enviada a la forja. El procesamiento comenzará en breve.")
    elif action == "delete":
        await db_instance.tasks.delete_one({"_id": ObjectId(task_id)})
        await query.message.edit_text("🗑️ Tarea cancelada y eliminada.")

@Client.on_callback_query(filters.regex(r"^config_"))
async def show_config_menu(client: Client, query: CallbackQuery):
    await query.answer()
    parts = query.data.split("_"); menu_type, task_id = parts[1], "_".join(parts[2:])
    if not (task := await db_instance.get_task(task_id)):
        return await query.message.edit_text("❌ Error: Tarea no encontrada.")

    keyboards = {
        "dlquality": build_detailed_format_menu(task_id, task.get('url_info', {}).get('formats', [])),
        "audioconvert": build_audio_convert_menu(task_id),
        "audioeffects": build_audio_effects_menu(task_id, task.get('processing_config', {})),
        "audiometadata": build_audio_metadata_menu(task_id),
        "watermark": build_watermark_menu(task_id),
        "tracks": build_tracks_menu(task_id, task.get('processing_config', {})),
    }
    menu_messages = {
        "dlquality": "💿 Seleccione la calidad a descargar:",
        "audioconvert": "🔊 Configure la conversión de audio:",
        "audioeffects": "🎧 Aplique efectos de audio:",
        "audiometadata": "🖼️ Elija qué metadatos editar:",
        "watermark": "💧 Elija un tipo de marca de agua:",
        "tracks": "📜 Gestione las pistas del video:",
    }
    if menu_type in keyboards:
        return await query.message.edit_text(menu_messages[menu_type], reply_markup=keyboards[menu_type])

    if not hasattr(client, 'user_data'): client.user_data = {}
    client.user_data[query.from_user.id] = {"active_config": {"task_id": task_id, "menu_type": menu_type}}

    greeting_prefix = get_greeting(query.from_user.id)
    menu_texts = {
        "rename": f"✏️ <b>Renombrar Archivo</b>\n\n{greeting_prefix}envíeme el nuevo nombre para <code>{escape_html(task.get('original_filename', 'archivo'))}</code>.\n<i>No incluya la extensión.</i>",
        "trim": f"✂️ <b>Cortar</b>\n\n{greeting_prefix}envíeme el tiempo de inicio y fin.\nFormatos: <code>HH:MM:SS-HH:MM:SS</code> o <code>MM:SS-MM:SS</code>.",
        "split": f"🧩 <b>Dividir Video</b>\n\n{greeting_prefix}envíeme el criterio de división por tiempo (ej. <code>300s</code>).",
        "gif": f"🎞️ <b>Crear GIF</b>\n\n{greeting_prefix}envíeme la duración y los FPS.\nFormato: <code>[segundos] [fps]</code> (ej: <code>5 15</code>).",
        "audiotags": "🖼️ <b>Editar Tags</b>\n\n{greeting_prefix}envíeme los nuevos metadatos. Formato (omita los que no quiera cambiar):\n\n<code>Título: [Nuevo Título]\nArtista: [Nuevo Artista]\nÁlbum: [Nuevo Álbum]</code>",
        "audiothumb": f"🖼️ <b>Añadir Carátula</b>\n\n{greeting_prefix}envíeme la imagen para la carátula.",
        "addsubs": f"➕ <b>Añadir Subtítulos</b>\n\n{greeting_prefix}envíeme el archivo de subtítulos (<code>.srt</code>).",
    }
    
    text = menu_texts.get(menu_type, "Configuración no reconocida.")
    back_callbacks = {"audiotags": "config_audiometadata_", "audiothumb": "config_audiometadata_", "addsubs": "config_tracks_"}
    back_button_cb = f"{back_callbacks.get(menu_type, 'task_process_')}{task_id}"
    await query.message.edit_text(text, reply_markup=build_back_button(back_button_cb), parse_mode=ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^set_watermark_"))
async def set_watermark_handler(client: Client, query: CallbackQuery):
    await query.answer()
    parts = query.data.split("_")
    action, task_id = parts[2], "_".join(parts[3:])
    
    if action == "remove":
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.watermark": ""}})
    elif action == "position":
        position = "_".join(parts[4:])
        await db_instance.update_task_config(task_id, "watermark.position", position)
    else: # image o text
        if not hasattr(client, 'user_data'): client.user_data = {}
        client.user_data[query.from_user.id] = {"active_config": {"task_id": task_id, "menu_type": f"watermark_{action}"}}
        prompt = "🖼️ Por favor, envíeme la imagen para la marca de agua." if action == "image" else "✏️ Por favor, envíeme el texto para la marca de agua."
        return await query.message.edit_text(prompt, reply_markup=build_back_button(f"config_watermark_{task_id}"))
    
    if not (task := await db_instance.get_task(task_id)): return await query.message.edit_text("❌ Tarea no encontrada.")
    await query.message.edit_text("✅ Configuración de marca de agua guardada.", reply_markup=build_processing_menu(task_id, task['file_type'], task, task.get('original_filename')))

@Client.on_message((filters.photo | filters.document) & filters.reply)
async def handle_media_input(client: Client, message: Message):
    user_id = message.from_user.id
    if not hasattr(client, 'user_data') or not (active_config := client.user_data.get(user_id, {}).get("active_config")): return

    task_id, menu_type = active_config["task_id"], active_config.get("menu_type")
    media = message.photo or message.document
    
    handler_map = {
        "audiothumb": ("thumbnail_file_id", "✅ Carátula guardada.", "config_audiometadata_"),
        "addsubs": ("subs_file_id", "✅ Subtítulos guardados.", "config_tracks_"),
        "watermark_image": ("watermark", "✅ Imagen de marca de agua recibida. Ahora, elija la posición:", None),
    }

    if menu_type not in handler_map: return
    
    is_image_required = menu_type in ["audiothumb", "watermark_image"]
    if is_image_required and hasattr(media, 'mime_type') and not media.mime_type.startswith("image/"):
        return await message.reply("❌ El archivo enviado no es una imagen.")
    
    key, feedback, next_menu_cb = handler_map[menu_type]
    value = {"type": "image", "file_id": media.file_id} if menu_type == "watermark_image" else media.file_id
    
    await db_instance.update_task_config(task_id, key, value)
    del client.user_data[user_id]["active_config"]
    
    if next_menu_cb:
        task = await db_instance.get_task(task_id)
        keyboard = build_processing_menu(task_id, task['file_type'], task, task.get('original_filename')) if next_menu_cb == 'task_process_' else build_tracks_menu(task_id, task.get('processing_config', {}))
    else: # Watermark position
        keyboard = build_position_menu(task_id)

    await message.reply(feedback, reply_markup=keyboard)

@Client.on_callback_query(filters.regex(r"^set_"))
async def set_value_callback(client: Client, query: CallbackQuery):
    parts = query.data.split("_")
    config_type = parts[1]
    
    if config_type == "watermark": return await set_watermark_handler(client, query)

    task_id, value = parts[2], "_".join(parts[3:])
    
    if not (task := await db_instance.get_task(task_id)):
        await query.answer("❌ Tarea no encontrada.", show_alert=True); return await query.message.delete()
    
    if config_type == "dlformat":
        final_format_id = downloader.get_best_audio_format_id(task['url_info']['formats']) if value in ["mp3", "bestaudio"] else ("bestvideo+bestaudio/best" if value == "bestvideo" else value)
        if value.isdigit() and next((f for f in task['url_info']['formats'] if f.get('format_id') == value), {}).get('acodec') in ['none', None]:
            final_format_id = f"{value}+bestaudio/best"
        if value in ["mp3", "bestaudio"]: await db_instance.update_task(task_id, "file_type", "audio")
        if value == "mp3": await db_instance.update_task_config(task_id, "audio_format", "mp3")
        
        info = task.get('url_info', {})
        if info.get('artist') or info.get('title') or info.get('album'):
            await db_instance.update_task_config(task_id, 'audio_tags', {'artist': info.get('artist'), 'title': info.get('title'), 'album': info.get('album')})
        
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"processing_config.download_format_id": final_format_id, "status": "queued"}})
        await query.message.edit_text("✅ Formato seleccionado.\n\n🔥 Tarea enviada a la forja.", reply_markup=None); return await query.answer()

    await query.answer()
    
    config_updates = {
        "mute": ("mute_audio", not task.get('processing_config', {}).get('mute_audio', False)),
        "audioprop": (f"audio_{parts[3]}", parts[4]),
        "audioeffect": (parts[3], not task.get('processing_config', {}).get(parts[3], False)),
        "trackopt": (parts[3], not task.get('processing_config', {}).get(parts[3], False)),
    }
    if config_type in config_updates:
        key, new_value = config_updates[config_type]
        await db_instance.update_task_config(task_id, key, new_value)

    task = await db_instance.get_task(task_id)
    
    next_keyboards = {
        "audioeffect": build_audio_effects_menu(task_id, task.get('processing_config', {})),
        "trackopt": build_tracks_menu(task_id, task.get('processing_config', {})),
    }
    if config_type in next_keyboards:
        await query.message.edit_text("🛠️ Configuración actualizada.", reply_markup=next_keyboards[config_type])
    else:
        await query.message.edit_text("🛠️ Configuración actualizada.", reply_markup=build_processing_menu(task_id, task['file_type'], task, task.get('original_filename', '')))

@Client.on_callback_query(filters.regex(r"^(song_select_|search_page_|cancel_search_|noop)"))
async def on_search_actions(client: Client, query: CallbackQuery):
    await query.answer()
    action = query.data.split("_")[0]

    if action == "song_select":
        result_id = query.data.split("_")[2]
        if not (search_result := await db_instance.search_results.find_one_and_delete({"_id": ObjectId(result_id), "user_id": query.from_user.id})):
            return await query.message.edit_text("❌ Esta búsqueda ha expirado.")
        if sid := search_result.get('search_id'):
            await db_instance.search_results.delete_many({"search_id": sid}); await db_instance.search_sessions.delete_one({"_id": ObjectId(sid)})
        
        status_msg = await query.message.edit_text(f"🔥 Procesando: <b>{escape_html(search_result.get('title'))}</b>...", parse_mode=ParseMode.HTML)
        info = await asyncio.to_thread(downloader.get_url_info, search_result.get('url') or f"ytsearch1:{search_result.get('search_term')}")
        if not info or not info.get('formats'): return await status_msg.edit("❌ No se pudo obtener información del video.")

        await db_instance.add_task(user_id=query.from_user.id, file_type='audio', url=info['url'], file_name=sanitize_filename(info['title']), 
                                   processing_config={"download_format_id": downloader.get_best_audio_format_id(info['formats']), "audio_tags": {'title': info.get('title'), 'artist': info.get('artist'), 'album': info.get('album')}, "thumbnail_url": info.get('thumbnail')},
                                   status="queued", url_info=info)

    elif action == "search_page":
        search_id, page = query.data.split("_")[2], int(query.data.split("_")[3])
        if not (session := await db_instance.search_sessions.find_one({"_id": ObjectId(search_id)})): return await query.message.edit_text("❌ Sesión de búsqueda expirada.")
        all_results = await db_instance.search_results.find({"search_id": search_id}).sort("created_at", 1).to_list(length=100)
        await query.message.edit_text(f"✅ Resultados para: <b>{escape_html(session['query'])}</b>", reply_markup=build_search_results_keyboard(all_results, search_id, page), parse_mode=ParseMode.HTML)
    
    elif action == "cancel_search":
        search_id = query.data.split("_")[2]
        await db_instance.search_results.delete_many({"search_id": search_id}); await db_instance.search_sessions.delete_one({"_id": ObjectId(search_id)})
        await query.message.edit_text("✅ Búsqueda cancelada.")

async def handle_text_input_for_config(client: Client, message: Message):
    user_id, user_input = message.from_user.id, message.text.strip()
    if not (active_config := client.user_data.get(user_id, {}).get('active_config')): return
    
    task_id, menu_type = active_config['task_id'], active_config['menu_type']
    del client.user_data[user_id]['active_config']

    try:
        if menu_type == "rename":
            await db_instance.update_task_config(task_id, "final_filename", user_input)
            feedback = f"✅ Nombre actualizado a <code>{escape_html(user_input)}</code>."
        elif menu_type == "trim":
            if not re.match(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*$", user_input):
                raise ValueError("Formato de tiempo inválido.")
            await db_instance.update_task_config(task_id, "trim_times", user_input)
            feedback = f"✅ Tiempos de corte guardados: <code>{escape_html(user_input)}</code>."
        elif menu_type == "split":
            await db_instance.update_task_config(task_id, "split_criteria", user_input)
            feedback = f"✅ Criterio de división guardado: <code>{escape_html(user_input)}</code>."
        elif menu_type == "gif":
            parts = user_input.split(); duration, fps = float(parts[0]), int(parts[1])
            await db_instance.update_task_config(task_id, "gif_options", {"duration": duration, "fps": fps})
            feedback = f"✅ GIF se creará con {duration}s a {fps}fps."
        elif menu_type == "audiotags":
            tags_to_update = {}
            for line in user_input.split('\n'):
                if ':' in line:
                    key, value = [part.strip() for part in line.split(':', 1)]
                    key_map = {'título': 'title', 'titulo': 'title', 'artista': 'artist', 'artist': 'artist', 'álbum': 'album', 'album': 'album'}
                    if (db_key := key_map.get(key.lower())): tags_to_update[db_key] = value
            if not tags_to_update: raise ValueError("No se proporcionaron tags válidos.")
            for key, value in tags_to_update.items(): await db_instance.update_task_config(task_id, f"audio_tags.{key}", value)
            feedback = "✅ Tags de audio actualizados."
        elif menu_type == "watermark_text":
            await db_instance.update_task_config(task_id, "watermark", {"type": "text", "text": user_input})
            await message.reply("✅ Texto recibido. Ahora, elija la posición:", reply_markup=build_position_menu(task_id))
            return # Evita el doble mensaje
        
        await message.reply(feedback, parse_mode=ParseMode.HTML, quote=True)
    except Exception as e:
        logger.error(f"Error procesando entrada de config '{menu_type}': {e}")
        await message.reply("❌ Formato incorrecto o error al guardar.", quote=True)

    if task := await db_instance.get_task(task_id):
        await message.reply("¿Algo más?", reply_markup=build_processing_menu(task_id, task['file_type'], task, task.get('original_filename', '')))
# --- END OF FILE src/plugins/processing_handler.py ---