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
from src.helpers.keyboards import (build_back_button, build_processing_menu,
                                   build_audio_convert_menu, build_audio_effects_menu,
                                   build_watermark_menu, build_position_menu,
                                   build_audio_metadata_menu, build_tracks_menu,
                                   build_transcode_menu, build_thumbnail_menu,
                                   build_confirmation_keyboard, build_search_results_keyboard,
                                   build_detailed_format_menu, build_join_selection_keyboard,
                                   build_zip_selection_keyboard, build_batch_profiles_keyboard)
from src.helpers.utils import get_greeting, escape_html, sanitize_filename, format_time

logger = logging.getLogger(__name__)
TEMP_DIR = os.path.join(os.getcwd(), "temp_lyrics")

async def open_task_menu_from_p(client: Client, message_or_query, task_id: str):
    task = await db_instance.get_task(task_id)
    if not task:
        text = "❌ Error: La tarea ya no existe."
        if isinstance(message_or_query, CallbackQuery):
            return await message_or_query.answer(text, show_alert=True)
        else:
            return await message_or_query.reply(text)

    text_content = f"🛠️ Configurando Tarea:\n<code>{escape_html(task.get('original_filename', '...'))}</code>"
    markup = build_processing_menu(task_id, task['file_type'], task)
    
    try:
        if isinstance(message_or_query, CallbackQuery):
            await message_or_query.message.edit_text(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)
        else:
            await message_or_query.reply(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)
    except MessageNotModified:
        pass
    except Exception as e:
        logger.error(f"Error en open_task_menu_from_p: {e}")
        chat_id = message_or_query.chat.id if isinstance(message_or_query, Message) else message_or_query.message.chat.id
        await client.send_message(chat_id, text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)

# --- MANEJADORES DE ENTRADAS (TEXTO/MEDIA) ---

async def handle_text_input_for_state(client: Client, message: Message, user_state: dict):
    user_id = message.from_user.id
    user_input = message.text.strip()
    state, data = user_state['status'], user_state['data']
    task_id, source_message_id = data['task_id'], data['source_message_id']
    
    try:
        feedback = "✅ Configuración guardada."
        if state == "awaiting_rename":
            await db_instance.update_task_config(task_id, "final_filename", user_input)
            feedback = "✅ Nombre actualizado."
        elif state == "awaiting_trim":
            await db_instance.update_task_config(task_id, "trim_times", user_input)
            feedback = "✅ Tiempos de corte guardados."
        elif state == "awaiting_split":
            await db_instance.update_task_config(task_id, "split_criteria", user_input)
            feedback = "✅ Criterio de división guardado."
        elif state == "awaiting_gif":
            parts = user_input.split()
            if len(parts) != 2 or not parts[0].replace('.', '', 1).isdigit() or not parts[1].isdigit():
                raise ValueError("Formato inválido. Use: [segundos] [fps]")
            await db_instance.update_task_config(task_id, "gif_options", {"duration": float(parts[0]), "fps": int(parts[1])})
            feedback = "✅ GIF configurado."
        elif state == "awaiting_audiotags":
            tags_to_update = {}
            for line in user_input.split('\n'):
                if ':' in line:
                    key, value = [part.strip() for part in line.split(':', 1)]
                    key_map = {'título': 'title', 'titulo': 'title', 'artista': 'artist', 'artist': 'artist', 'álbum': 'album', 'album': 'album'}
                    if (db_key := key_map.get(key.lower())): tags_to_update[db_key] = value
            if not tags_to_update: raise ValueError("No se proporcionaron tags válidos.")
            for key, value in tags_to_update.items(): await db_instance.update_task_config(task_id, f"audio_tags.{key}", value)
            feedback = "✅ Tags de audio actualizados."
        elif state == "awaiting_watermark_text":
            await db_instance.update_task_config(task_id, "watermark", {"type": "text", "text": user_input})
            await message.delete()
            await db_instance.set_user_state(user_id, 'awaiting_watermark_position', data=data)
            return await client.edit_message_text(user_id, source_message_id, text="✅ Texto recibido. Elija la posición:", reply_markup=build_position_menu(task_id, "config_watermark"))
        else:
            return # No es un estado que espere texto

        # Volver al menú principal
        await message.delete()
        await db_instance.set_user_state(user_id, "idle")
        source_message = await client.get_messages(user_id, source_message_id)
        await open_task_menu_from_p(client, source_message, task_id)
        
    except Exception as e:
        logger.error(f"Error procesando entrada de config '{state}': {e}")
        await message.reply(f"❌ Formato incorrecto o error al guardar: `{e}`", quote=True)

async def handle_media_input_for_state(client: Client, message: Message, user_state: dict):
    user_id = message.from_user.id
    state, data = user_state['status'], user_state['data']
    task_id, source_message_id = data['task_id'], data['source_message_id']

    media = message.photo or message.document or message.audio
    if not media: return

    state_map = {
        "awaiting_audiothumb": ("thumbnail_file_id", "✅ Carátula de audio guardada."),
        "awaiting_subs": ("subs_file_id", "✅ Subtítulos guardados."),
        "awaiting_watermark_image": ("watermark", "✅ Imagen recibida. Ahora, elija la posición:"),
        "awaiting_thumbnail": ("thumbnail_file_id", "✅ Miniatura de video guardada."),
        "awaiting_replace_audio": ("replace_audio_file_id", "✅ Nuevo audio guardado.")
    }

    if state not in state_map: return

    if state in ["awaiting_audiothumb", "awaiting_watermark_image", "awaiting_thumbnail"] and not (message.photo or (hasattr(media, 'mime_type') and media.mime_type.startswith("image/"))):
        return await message.reply("❌ El archivo no es una imagen válida.")
    if state == "awaiting_replace_audio" and (not hasattr(media, 'mime_type') or not media.mime_type.startswith("audio/")):
        return await message.reply("❌ El archivo no es un audio válido.")
    if state == "awaiting_subs" and (not hasattr(media, 'mime_type') or not "srt" in media.mime_type):
         return await message.reply("❌ El archivo no parece ser un subtítulo .srt válido.")

    key, feedback = state_map[state]
    value_to_set = media.file_id
    if state == "awaiting_watermark_image":
        value_to_set = {"type": "image", "file_id": media.file_id}

    update_query = {"$set": {f"processing_config.{key}": value_to_set}}
    if state == "awaiting_thumbnail":
        update_query["$unset"] = {"processing_config.extract_thumbnail": "", "processing_config.remove_thumbnail": ""}

    await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, update_query)
    await message.delete()

    if state == "awaiting_watermark_image":
        await db_instance.set_user_state(user_id, 'awaiting_watermark_position', data=data)
        await client.edit_message_text(user_id, source_message_id, feedback, reply_markup=build_position_menu(task_id, "config_watermark"))
    else:
        await db_instance.set_user_state(user_id, "idle")
        source_message = await client.get_messages(user_id, source_message_id)
        await open_task_menu_from_p(client, source_message, task_id)

# --- MANEJADORES DE CALLBACKS ---

@Client.on_callback_query(filters.regex(r"^p_open_"))
async def open_task_menu_callback(client: Client, query: CallbackQuery):
    await query.answer()
    task_id = query.data.split("_")[2]
    await db_instance.set_user_state(query.from_user.id, "idle")
    await open_task_menu_from_p(client, query, task_id)

@Client.on_callback_query(filters.regex(r"^task_"))
async def handle_task_actions(client: Client, query: CallbackQuery):
    parts = query.data.split("_")
    action, task_id = parts[1], "_".join(parts[2:])

    if action == "queuesingle":
        await query.answer("Enviando a la forja...")
        status_message_ref = {"chat_id": query.message.chat.id, "message_id": query.message.id}
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"status": "queued", "status_message_ref": status_message_ref}})
        await query.message.edit_text("⏳ <b>En Cola...</b>\nSu tarea será procesada en breve.", parse_mode=ParseMode.HTML)
        await db_instance.set_user_state(query.from_user.id, "idle")
    elif action == "delete":
        await query.answer("Tarea eliminada.")
        if task_id == "url_selection": await query.message.delete()
        else:
            await db_instance.delete_task_by_id(task_id)
            await query.message.edit_text("🗑️ Tarea cancelada y eliminada del panel.")
        await db_instance.set_user_state(query.from_user.id, "idle")

@Client.on_callback_query(filters.regex(r"^config_"))
async def show_config_menu_and_set_state(client: Client, query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    parts, menu_type, task_id = query.data.split("_"), query.data.split("_")[1], "_".join(query.data.split("_")[2:])

    task = await db_instance.get_task(task_id)
    if not task: return await query.answer("❌ Error: Tarea no encontrada.", show_alert=True)
    config = task.get('processing_config', {})
    
    if menu_type == "extract_audio":
        status_message_ref = {"chat_id": query.message.chat.id, "message_id": query.message.id}
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"processing_config.extract_audio": True, "status": "queued", "status_message_ref": status_message_ref}})
        return await query.message.edit_text("✅ Tarea de extracción de audio enviada.\n⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)

    keyboards = { "transcode": build_transcode_menu(task_id), "tracks": build_tracks_menu(task_id, config), "audioconvert": build_audio_convert_menu(task_id),
                  "audioeffects": build_audio_effects_menu(task_id, config), "audiometadata": build_audio_metadata_menu(task_id), "watermark": build_watermark_menu(task_id),
                  "thumbnail": build_thumbnail_menu(task_id, config) }
    menu_messages = { "transcode": "📉 Seleccione una resolución:", "tracks": "📜 Gestione las pistas del video:", "audioconvert": "🔊 Configure la conversión de audio:",
                      "audioeffects": "🎧 Aplique efectos de audio:", "audiometadata": "🖼️ Elija qué metadatos editar:", "watermark": "💧 Elija un tipo de marca de agua:",
                      "thumbnail": "🖼️ Gestione la miniatura del video:" }
    
    if menu_type in keyboards: return await query.message.edit_text(text=menu_messages[menu_type], reply_markup=keyboards[menu_type])

    state_map = { "rename": "awaiting_rename", "trim": "awaiting_trim", "split": "awaiting_split", "gif": "awaiting_gif", "audiotags": "awaiting_audiotags", 
                  "audiothumb": "awaiting_audiothumb", "addsubs": "awaiting_subs", "thumbnail_add": "awaiting_thumbnail", "replace_audio": "awaiting_replace_audio",
                  "watermark_text": "awaiting_watermark_text", "watermark_image": "awaiting_watermark_image" }
    
    if menu_type not in state_map: return logger.warning(f"Tipo de menú de configuración no reconocido: {menu_type}")

    await db_instance.set_user_state(user_id, state_map[menu_type], data={"task_id": task_id, "source_message_id": query.message.id})
    
    greeting_prefix = get_greeting(user_id)
    menu_texts = { "rename": f"✏️ <b>Renombrar Archivo</b>\n\n{greeting_prefix}, envíeme el nuevo nombre para <code>{escape_html(task.get('original_filename', 'archivo'))}</code>.\n<i>No incluya la extensión.</i>",
                   "trim": f"✂️ <b>Cortar Video</b>\n\n{greeting_prefix}, envíeme el tiempo de inicio y fin.\nFormatos: <code>HH:MM:SS-HH:MM:SS</code> o <code>MM:SS-MM:SS</code>.\n\nPara múltiples cortes, separe los rangos con comas:\n<code>MM:SS-MM:SS<b>,</b>MM:SS-MM:SS</code>",
                   "split": f"🧩 <b>Dividir Video</b>\n\n{greeting_prefix}, envíeme el criterio de división por tiempo (ej. <code>300s</code>).", "gif": f"🎞️ <b>Crear GIF</b>\n\n{greeting_prefix}, envíeme la duración y los FPS.\nFormato: <code>[segundos] [fps]</code> (ej: <code>5 15</code>).",
                   "audiotags": f"✍️ <b>Editar Tags</b>\n\n{greeting_prefix}, envíeme los nuevos metadatos. Formato:\n\n<code>Título: [Nuevo Título]\nArtista: [Nuevo Artista]\nÁlbum: [Nuevo Álbum]</code>",
                   "audiothumb": f"🖼️ <b>Añadir Carátula (Audio)</b>\n\n{greeting_prefix}, envíeme la imagen para la carátula.", "addsubs": f"➕ <b>Añadir Subtítulos</b>\n\n{greeting_prefix}, envíeme el archivo de subtítulos (<code>.srt</code>).",
                   "thumbnail_add": f"🖼️ <b>Añadir Miniatura (Video)</b>\n\n{greeting_prefix}, envíeme la imagen que será la nueva miniatura.", "replace_audio": f"🎼 <b>Reemplazar Audio</b>\n\n{greeting_prefix}, envíeme el nuevo archivo de audio.",
                   "watermark_text": f"💧 <b>Texto de Marca de Agua</b>\n\n{greeting_prefix}, envíeme el texto que desea superponer.", "watermark_image": f"🖼️ <b>Imagen de Marca de Agua</b>\n\n{greeting_prefix}, envíeme la imagen que desea usar." }
    
    back_callbacks = { "audiotags": "config_audiometadata_", "audiothumb": "config_audiometadata_", "addsubs": "config_tracks_", "thumbnail_add": "config_thumbnail_",
                       "replace_audio": "config_tracks_", "watermark_text": "config_watermark_", "watermark_image": "config_watermark_" }
    back_button_cb = f"{back_callbacks.get(menu_type, 'p_open_')}{task_id}"
    
    await query.message.edit_text(menu_texts[menu_type], reply_markup=build_back_button(back_button_cb), parse_mode=ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^set_"))
async def set_value_callback(client: Client, query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    parts = query.data.split("_")
    config_type, task_id = parts[1], parts[2]
    
    task = await db_instance.get_task(task_id)
    if not task: return await query.message.delete()
    config = task.get('processing_config', {})
    
    if config_type == "watermark" and parts[3] == "position":
        await db_instance.update_task_config(task_id, "watermark.position", parts[4].replace('-', '_'))
        await db_instance.set_user_state(user_id, "idle")
    else:
        # Lógica para otras opciones de set
        if config_type == "transcode":
            value = "_".join(parts[3:])
            if value == "remove_all": await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.transcode": ""}})
            else: key, new_value = value.split("_", 1); await db_instance.update_task_config(task_id, f"transcode.{key}", new_value)
        elif config_type == "watermark" and parts[3] == "remove": await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.watermark": ""}})
        elif config_type == "thumb_op":
            op = parts[3]; current_value = config.get(f"{op}_thumbnail", False); update_query = {"$set": {f"processing_config.{op}_thumbnail": not current_value}}
            if not current_value:
                other_op = "remove" if op == "extract" else "extract"; update_query["$unset"] = {f"processing_config.{other_op}_thumbnail": "", "processing_config.thumbnail_file_id": ""}
            await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, update_query)
        elif config_type == "mute": await db_instance.update_task_config(task_id, 'mute_audio', not config.get('mute_audio', False))
        elif config_type == "audioprop": await db_instance.update_task_config(task_id, f"audio_{parts[3]}", parts[4])
        elif config_type == "audioeffect": await db_instance.update_task_config(task_id, parts[3], not config.get(parts[3], False))
        elif config_type == "trackopt": await db_instance.update_task_config(task_id, parts[3], not config.get(parts[3], False))

    await open_task_menu_from_p(client, query, task_id)

@Client.on_callback_query(filters.regex(r"^(song_select_|search_page_|cancel_search_)"))
async def handle_search_callbacks(client: Client, query: CallbackQuery):
    if query.data.startswith("song_select_"):
        await select_song_from_search(client, query)
    elif query.data.startswith("search_page_"):
        await handle_search_pagination(client, query)
    elif query.data.startswith("cancel_search_"):
        await cancel_search_session(client, query)

# El resto de funciones (select_song, etc.) se mantienen sin cambios significativos
# ...