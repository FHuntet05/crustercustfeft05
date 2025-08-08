import logging
import asyncio
import re
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from src.core import downloader
from src.helpers.keyboards import (build_back_button, build_processing_menu,
                                   build_detailed_format_menu, build_audio_convert_menu,
                                   build_audio_effects_menu, build_watermark_menu,
                                   build_position_menu, build_audio_metadata_menu,
                                   build_tracks_menu, build_transcode_menu,
                                   build_thumbnail_menu, build_confirmation_keyboard,
                                   build_search_results_keyboard)
from src.helpers.utils import get_greeting, escape_html, sanitize_filename, format_time

logger = logging.getLogger(__name__)

async def open_task_menu_from_p(client: Client, message: Message, task_id: str):
    task = await db_instance.get_task(task_id)
    if not task:
        return await message.reply("❌ Error: La tarea ya no existe.")

    text_content = f"🛠️ Configurando Tarea:\n<code>{escape_html(task.get('original_filename', '...'))}</code>"
    markup = build_processing_menu(task_id, task['file_type'], task)
    
    if hasattr(message, 'photo') and message.photo:
        await message.delete()
        await client.send_message(
            chat_id=message.chat.id,
            text=text_content,
            reply_markup=markup,
            parse_mode=ParseMode.HTML
        )
    else:
        try:
            if message.from_user.is_bot:
                 await message.edit_text(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)
            else:
                 await message.reply(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
             await message.reply(text=text_content, reply_markup=markup, parse_mode=ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^p_open_"))
async def open_task_menu_callback(client: Client, query: CallbackQuery):
    await query.answer()
    task_id = query.data.split("_")[2]
    await open_task_menu_from_p(client, query.message, task_id)

@Client.on_callback_query(filters.regex(r"^task_"))
async def handle_task_actions(client: Client, query: CallbackQuery):
    parts = query.data.split("_")
    action, task_id = parts[1], "_".join(parts[2:])

    if action == "queuesingle":
        await query.answer("Enviando a la forja...")
        status_message_ref = {"chat_id": query.message.chat.id, "message_id": query.message.id}
        await db_instance.tasks.update_one(
            {"_id": ObjectId(task_id)},
            {"$set": {"status": "queued", "status_message_ref": status_message_ref}}
        )
        await query.message.edit_text("⏳ <b>En Cola...</b>\nSu tarea será procesada en breve.", parse_mode=ParseMode.HTML)

    elif action == "delete":
        if task_id == "url_selection":
            await query.answer("Operación cancelada.")
            await query.message.delete()
            await db_instance.set_user_state(query.from_user.id, "idle")
            return

        await query.answer("Tarea eliminada.")
        await db_instance.delete_task_by_id(task_id)
        await query.message.edit_text("🗑️ Tarea cancelada y eliminada del panel.")

@Client.on_callback_query(filters.regex(r"^(profile_|panel_delete_all_)"))
async def handle_profile_and_panel_actions(client: Client, query: CallbackQuery):
    user = query.from_user
    parts = query.data.split("_")
    action_type = parts[0]

    if action_type == "profile":
        sub_action = parts[1]
        if sub_action == "apply":
            await query.answer()
            task_id, preset_id = parts[2], parts[3]
            preset = await db_instance.get_preset_by_id(preset_id)
            if not preset:
                return await query.message.edit_text("❌ El perfil seleccionado ya no existe.")
            
            status_message_ref = {"chat_id": query.message.chat.id, "message_id": query.message.id}
            await db_instance.tasks.update_one(
                {"_id": ObjectId(task_id)},
                {"$set": {
                    "processing_config": preset.get('config_data', {}), 
                    "status": "queued",
                    "status_message_ref": status_message_ref
                }}
            )
            await query.message.edit_text(f"✅ Perfil '<b>{preset['preset_name'].capitalize()}</b>' aplicado.\n⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)
        
        elif sub_action == "save" and parts[2] == "request":
            await query.answer()
            task_id = parts[3]
            if not hasattr(client, 'user_data'): client.user_data = {}
            client.user_data[user.id] = {"active_config": {"task_id": task_id, "menu_type": "profile_save", "source_message_id": query.message.id}}
            await query.message.edit_text("💾 Escriba un nombre para este perfil:", reply_markup=build_back_button(f"p_open_{task_id}"))
        
        elif sub_action == "delete":
            await query.answer()
            confirm_or_cancel, preset_name = parts[2], parts[3]
            if confirm_or_cancel == "confirm":
                result = await db_instance.user_presets.delete_one({"user_id": user.id, "preset_name": preset_name.lower()})
                await query.message.edit_text(f"🗑️ Perfil '<b>{escape_html(preset_name.capitalize())}</b>' eliminado." if result.deleted_count > 0 else "❌ Perfil no encontrado.", parse_mode=ParseMode.HTML)
            elif confirm_or_cancel == "cancel":
                await query.message.delete()
    
    elif action_type == "panel" and parts[1] == "delete" and parts[2] == "all":
        await query.answer()
        confirm_or_cancel = parts[3]
        if confirm_or_cancel == "confirm":
            deleted = await db_instance.delete_all_pending_tasks(user.id)
            await query.message.edit_text(f"💥 Panel limpiado. Se descartaron {deleted.deleted_count} tareas.")
        elif confirm_or_cancel == "cancel":
            await query.message.edit_text("Operación cancelada.")

@Client.on_callback_query(filters.regex(r"^config_"))
async def show_config_menu(client: Client, query: CallbackQuery):
    await query.answer()
    parts, menu_type = query.data.split("_"), query.data.split("_")[1]
    task_id = "_".join(parts[2:])

    task = await db_instance.get_task(task_id)
    if not task:
        return await query.message.edit_text("❌ Error: Tarea no encontrada.")

    config = task.get('processing_config', {})
    
    if menu_type == "extract_audio":
        status_message_ref = {"chat_id": query.message.chat.id, "message_id": query.message.id}
        await db_instance.tasks.update_one(
            {"_id": ObjectId(task_id)},
            {"$set": { "processing_config.extract_audio": True, "status": "queued", "status_message_ref": status_message_ref }}
        )
        return await query.message.edit_text("✅ Tarea de extracción de audio enviada.\n⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)

    keyboards = { "audioconvert": build_audio_convert_menu(task_id), "audioeffects": build_audio_effects_menu(task_id, config),
                  "audiometadata": build_audio_metadata_menu(task_id), "watermark": build_watermark_menu(task_id),
                  "tracks": build_tracks_menu(task_id, config), "transcode": build_transcode_menu(task_id),
                  "thumbnail": build_thumbnail_menu(task_id, config) }
    
    menu_messages = { "audioconvert": "🔊 Configure la conversión de audio:", "audioeffects": "🎧 Aplique efectos de audio:",
                      "audiometadata": "🖼️ Elija qué metadatos editar:", "watermark": "💧 Elija un tipo de marca de agua:",
                      "tracks": "📜 Gestione las pistas del video:", "transcode": "📉 Seleccione una resolución:",
                      "thumbnail": "🖼️ Gestione la miniatura del video:" }
    
    if menu_type in keyboards:
        if query.message.photo: await query.message.delete()
        target_func = client.send_message if query.message.photo else query.message.edit_text
        await target_func(
            chat_id=query.message.chat.id,
            text=menu_messages[menu_type],
            reply_markup=keyboards[menu_type]
        )
        return

    if not hasattr(client, 'user_data'): client.user_data = {}
    client.user_data[query.from_user.id] = {"active_config": {"task_id": task_id, "menu_type": menu_type, "source_message_id": query.message.id}}

    greeting_prefix = get_greeting(query.from_user.id)
    menu_texts = {
        "rename": f"✏️ <b>Renombrar Archivo</b>\n\n{greeting_prefix}, envíeme el nuevo nombre para <code>{escape_html(task.get('original_filename', 'archivo'))}</code>.\n<i>No incluya la extensión.</i>",
        "trim": f"✂️ <b>Cortar Video</b>\n\n{greeting_prefix}, envíeme el tiempo de inicio y fin.\nFormatos: <code>HH:MM:SS-HH:MM:SS</code> o <code>MM:SS-MM:SS</code>.\n\nPara múltiples cortes, separe los rangos con comas:\n<code>MM:SS-MM:SS<b>,</b>MM:SS-MM:SS</code>",
        "split": f"🧩 <b>Dividir Video</b>\n\n{greeting_prefix}, envíeme el criterio de división por tiempo (ej. <code>300s</code>).",
        "gif": f"🎞️ <b>Crear GIF</b>\n\n{greeting_prefix}, envíeme la duración y los FPS.\nFormato: <code>[segundos] [fps]</code> (ej: <code>5 15</code>).",
        "audiotags": f"✍️ <b>Editar Tags</b>\n\n{greeting_prefix}, envíeme los nuevos metadatos. Formato:\n\n<code>Título: [Nuevo Título]\nArtista: [Nuevo Artista]\nÁlbum: [Nuevo Álbum]</code>",
        "audiothumb": f"🖼️ <b>Añadir Carátula (Audio)</b>\n\n{greeting_prefix}, envíeme la imagen para la carátula.",
        "addsubs": f"➕ <b>Añadir Subtítulos</b>\n\n{greeting_prefix}, envíeme el archivo de subtítulos (<code>.srt</code>).",
        "thumbnail_add": f"🖼️ <b>Añadir Miniatura (Video)</b>\n\n{greeting_prefix}, envíeme la imagen que será la nueva miniatura.",
        "replace_audio": f"🎼 <b>Reemplazar Audio</b>\n\n{greeting_prefix}, envíeme el nuevo archivo de audio.",
        "watermark_text": f"💧 <b>Texto de Marca de Agua</b>\n\n{greeting_prefix}, envíeme el texto que desea superponer."
    }
    
    text = menu_texts.get(menu_type, "Configuración no reconocida.")
    back_callbacks = { "audiotags": "config_audiometadata_", "audiothumb": "config_audiometadata_", "addsubs": "config_tracks_",
                       "thumbnail_add": "config_thumbnail_", "replace_audio": "config_tracks_", "watermark_text": "config_watermark_"}
    back_button_cb = f"{back_callbacks.get(menu_type, 'p_open_')}{task_id}"
    await query.message.edit_text(text, reply_markup=build_back_button(back_button_cb), parse_mode=ParseMode.HTML)

@Client.on_message((filters.photo | filters.document | filters.audio) & filters.private, group=0)
async def handle_media_input(client: Client, message: Message):
    user_id = message.from_user.id
    if not hasattr(client, 'user_data') or not (active_config := client.user_data.get(user_id, {}).get("active_config")):
        return

    task_id, menu_type, source_message_id = active_config.get("task_id"), active_config.get("menu_type"), active_config.get("source_message_id")
    if not all([task_id, menu_type, source_message_id]): return

    media = message.photo or message.document or message.audio
    
    handler_map = { "audiothumb": ("thumbnail_file_id", "✅ Carátula de audio guardada."),
                    "addsubs": ("subs_file_id", "✅ Subtítulos guardados."),
                    "watermark_image": ("watermark", "✅ Imagen recibida. Ahora, elija la posición:"),
                    "thumbnail_add": ("thumbnail_file_id", "✅ Miniatura de video guardada."),
                    "replace_audio": ("replace_audio_file_id", "✅ Nuevo audio guardado.") }

    if menu_type not in handler_map: return

    if menu_type in ["audiothumb", "watermark_image", "thumbnail_add"] and not (message.photo or (hasattr(media, 'mime_type') and media.mime_type.startswith("image/"))):
        return await message.reply("❌ El archivo no es una imagen válida.")
    if menu_type == "replace_audio" and (not hasattr(media, 'mime_type') or not media.mime_type.startswith("audio/")):
        return await message.reply("❌ El archivo no es un audio válido.")
    
    key, feedback = handler_map[menu_type]
    value_to_set = media.file_id
    if menu_type == "watermark_image": value_to_set = {"type": "image", "file_id": media.file_id}
    
    update_query = {"$set": {f"processing_config.{key}": value_to_set}}
    if menu_type == "thumbnail_add":
        update_query["$unset"] = {"processing_config.extract_thumbnail": "", "processing_config.remove_thumbnail": ""}

    await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, update_query)
    
    del client.user_data[user_id]["active_config"]
    await message.delete()
    
    task = await db_instance.get_task(task_id)
    if not task:
        return await client.edit_message_text(user_id, source_message_id, "❌ Error: La tarea asociada desapareció.")

    try:
        if menu_type == "watermark_image":
            await client.edit_message_text(user_id, source_message_id, feedback, reply_markup=build_position_menu(task_id, "config_watermark"))
        else:
            await client.edit_message_text(user_id, source_message_id, f"{feedback}\n\nVolviendo al menú...", reply_markup=build_processing_menu(task_id, task['file_type'], task))
    except MessageNotModified:
        pass

async def handle_text_input(client: Client, message: Message):
    user_id, user_input = message.from_user.id, message.text.strip()
    
    active_config = client.user_data.get(user_id, {}).get('active_config')
    if not active_config: return

    task_id, menu_type, source_message_id = active_config['task_id'], active_config['menu_type'], active_config.get('source_message_id')
    
    try:
        feedback = "✅ Configuración guardada."
        if menu_type == "profile_save":
            task = await db_instance.get_task(task_id)
            if not task: raise ValueError("Tarea no encontrada")
            await db_instance.add_preset(user_id, user_input, task.get('processing_config', {}))
            feedback = f"✅ Perfil '<b>{escape_html(user_input.capitalize())}</b>' guardado."
        elif menu_type == "rename":
            await db_instance.update_task_config(task_id, "final_filename", user_input); feedback = "✅ Nombre actualizado."
        elif menu_type == "trim":
            await db_instance.update_task_config(task_id, "trim_times", user_input); feedback = "✅ Tiempos de corte guardados."
        elif menu_type == "split":
            await db_instance.update_task_config(task_id, "split_criteria", user_input); feedback = "✅ Criterio de división guardado."
        elif menu_type == "gif":
            parts = user_input.split(); await db_instance.update_task_config(task_id, "gif_options", {"duration": float(parts[0]), "fps": int(parts[1])}); feedback = "✅ GIF configurado."
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
            await message.delete()
            del client.user_data[user_id]['active_config']
            await client.edit_message_text(user_id, source_message_id, text="✅ Texto recibido. Elija la posición:", reply_markup=build_position_menu(task_id, "config_watermark"))
            return
        else:
            return
        
        del client.user_data[user_id]['active_config']
        await message.delete()
        
        if task := await db_instance.get_task(task_id):
            await client.edit_message_text(user_id, source_message_id, text=f"{feedback}\n\nVolviendo al menú...",
                                           reply_markup=build_processing_menu(task_id, task['file_type'], task), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error procesando entrada de config '{menu_type}': {e}")
        await message.reply(f"❌ Formato incorrecto o error al guardar: `{e}`", quote=True)

@Client.on_callback_query(filters.regex(r"^set_"))
async def set_value_callback(client: Client, query: CallbackQuery):
    await query.answer()
    parts, config_type, task_id = query.data.split("_"), query.data.split("_")[1], query.data.split("_")[2]
    
    task = await db_instance.get_task(task_id)
    if not task: return await query.message.delete()
    
    config = task.get('processing_config', {})
    
    if config_type == "transcode":
        value = "_".join(parts[3:])
        if value == "remove_all": await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.transcode": ""}})
        else: key, new_value = value.split("_", 1); await db_instance.update_task_config(task_id, f"transcode.{key}", new_value)
    elif config_type == "watermark":
        action = parts[3]
        if action == "remove": await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.watermark": ""}})
        elif action == "position": await db_instance.update_task_config(task_id, "watermark.position", parts[4].replace('-', '_'))
    elif config_type == "thumb_op":
        op = parts[3]
        current_value = config.get(f"{op}_thumbnail", False)
        update_query = {"$set": {f"processing_config.{op}_thumbnail": not current_value}}
        if not current_value:
            other_op = "remove" if op == "extract" else "extract"
            update_query["$unset"] = {f"processing_config.{other_op}_thumbnail": "", "processing_config.thumbnail_file_id": ""}
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, update_query)
    elif config_type == "mute": await db_instance.update_task_config(task_id, 'mute_audio', not config.get('mute_audio', False))
    elif config_type == "audioprop": await db_instance.update_task_config(task_id, f"audio_{parts[3]}", parts[4])
    elif config_type == "audioeffect": await db_instance.update_task_config(task_id, parts[3], not config.get(parts[3], False))
    elif config_type == "trackopt": await db_instance.update_task_config(task_id, parts[3], not config.get(parts[3], False))

    task = await db_instance.get_task(task_id)
    config = task.get('processing_config', {})
    
    message_text, keyboard = "🛠️ Configuración actualizada.", None
    if config_type == "watermark" and len(parts) > 3 and parts[3] == "position":
        message_text, keyboard = "✅ Posición guardada. Volviendo al menú...", build_processing_menu(task_id, task['file_type'], task)
    else:
        menu_map = {"transcode": build_transcode_menu(task_id), "watermark": build_watermark_menu(task_id),
                    "audioeffect": build_audio_effects_menu(task_id, config), "trackopt": build_tracks_menu(task_id, config),
                    "thumb_op": build_thumbnail_menu(task_id, config)}
        keyboard = menu_map.get(config_type, build_processing_menu(task_id, task['file_type'], task))

    try: await query.message.edit_text(message_text, reply_markup=keyboard)
    except MessageNotModified: pass

@Client.on_callback_query(filters.regex(r"^set_dlformat_"))
async def set_download_format(client: Client, query: CallbackQuery):
    await query.answer()
    user, format_id_selected = query.from_user, "_".join(query.data.split("_")[3:])

    user_state = await db_instance.get_user_state(user.id)
    if user_state.get("status") != "awaiting_quality_selection":
        return await query.message.edit_text("❌ Esta selección ha expirado. Por favor, envíe el enlace de nuevo.")

    info = user_state.get("data", {}).get("url_info")
    if not info: return await query.message.edit_text("❌ Error crítico: No se encontró la info del video.")
    
    await query.message.delete()
    status_message = await client.send_message(user.id, "✅ Calidad seleccionada. Creando tarea en el panel...")

    final_format_id, processing_config, file_type = format_id_selected, {}, 'video' if info['is_video'] else 'audio'

    if format_id_selected == "bestaudio": final_format_id, file_type = downloader.get_best_audio_format_id(info.get('formats', [])), 'audio'
    elif format_id_selected == "bestvideo": final_format_id = downloader.get_best_video_format_id(info.get('formats', []))
    elif format_id_selected == "mp3": final_format_id, processing_config["audio_format"], file_type = downloader.get_best_audio_format_id(info.get('formats', [])), "mp3", 'audio'
    elif file_type == 'video' and (best_audio_id := downloader.get_best_audio_format_id(info.get('formats', []))):
        final_format_id = f"{format_id_selected}+{best_audio_id}"

    processing_config["download_format_id"] = final_format_id

    task_id = await db_instance.add_task(
        user_id=user.id, file_type=file_type, url=info['url'], file_name=sanitize_filename(info['title']),
        url_info=info, processing_config=processing_config, status="queued",
        custom_fields={"status_message_ref": {"chat_id": status_message.chat.id, "message_id": status_message.id}}
    )
    
    if not task_id: return await status_message.edit_text("❌ Error crítico al crear la tarea.")
    await db_instance.set_user_state(user.id, "idle")
    await status_message.edit_text(f"✅ Tarea para '<b>{escape_html(info['title'])}</b>' creada.\n⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)

async def handle_join_actions(client: Client, query: CallbackQuery):
    await query.answer()
    user = query.from_user
    
    if not hasattr(client, 'user_data') or not (join_data := client.user_data.get(user.id, {}).get("join_mode")):
        return await query.message.edit_text("❌ El modo de unión ha expirado. Inicie de nuevo con /join.")

    parts, action = query.data.split("_"), query.data.split("_")[1]

    if action == "cancel":
        del client.user_data[user.id]["join_mode"]
        return await query.message.edit_text("Operación de unión cancelada.")

    if action == "select":
        task_id, selected_ids = parts[2], join_data["selected_ids"]
        if task_id in selected_ids: selected_ids.remove(task_id)
        else: selected_ids.append(task_id)
        keyboard = build_join_selection_keyboard(join_data["available_tasks"], selected_ids)
        await query.message.edit_text(f"🎬 <b>Modo de Unión de Videos</b>\n\nSeleccionados: <b>{len(selected_ids)} video(s)</b>.",
                                      reply_markup=keyboard, parse_mode=ParseMode.HTML)
    elif action == "confirm":
        selected_ids = join_data["selected_ids"]
        if len(selected_ids) < 2: return await query.message.edit_text("❌ Necesita seleccionar al menos 2 videos para unir.")
        status_message_ref = {"chat_id": query.message.chat.id, "message_id": query.message.id}
        await db_instance.add_task(user_id=user.id, file_type='join_operation', file_name=f"Union de {len(selected_ids)} videos.mp4",
                                   processing_config={"source_task_ids": [ObjectId(tid) for tid in selected_ids]}, status='queued',
                                   custom_fields={"status_message_ref": status_message_ref})
        await db_instance.tasks.delete_many({"_id": {"$in": [ObjectId(tid) for tid in selected_ids]}})
        del client.user_data[user.id]["join_mode"]
        await query.message.edit_text(f"✅ Tarea para unir <b>{len(selected_ids)}</b> videos enviada.\n⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)

async def handle_zip_actions(client: Client, query: CallbackQuery):
    await query.answer()
    user = query.from_user
    
    if not hasattr(client, 'user_data') or not (zip_data := client.user_data.get(user.id, {}).get("zip_mode")):
        return await query.message.edit_text("❌ El modo de compresión ha expirado. Inicie de nuevo con /zip.")

    parts, action = query.data.split("_"), query.data.split("_")[1]

    if action == "cancel": del client.user_data[user.id]["zip_mode"]; return await query.message.edit_text("Operación de compresión cancelada.")
    if action == "select":
        task_id, selected_ids = parts[2], zip_data["selected_ids"]
        if task_id in selected_ids: selected_ids.remove(task_id)
        else: selected_ids.append(task_id)
        keyboard = build_zip_selection_keyboard(zip_data["available_tasks"], selected_ids)
        await query.message.edit_text(f"📦 <b>Modo de Compresión ZIP</b>\n\nSeleccionados: <b>{len(selected_ids)} tarea(s)</b>.",
                                      reply_markup=keyboard, parse_mode=ParseMode.HTML)
    elif action == "confirm":
        selected_ids = zip_data["selected_ids"]
        if not selected_ids: return await query.message.edit_text("❌ No ha seleccionado ninguna tarea para comprimir.")
        status_message_ref = {"chat_id": query.message.chat.id, "message_id": query.message.id}
        await db_instance.add_task(user_id=user.id, file_type='zip_operation', file_name=f"Compresion_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.zip",
                                   processing_config={"source_task_ids": [ObjectId(tid) for tid in selected_ids]}, status='queued',
                                   custom_fields={"status_message_ref": status_message_ref})
        await db_instance.tasks.delete_many({"_id": {"$in": [ObjectId(tid) for tid in selected_ids]}})
        del client.user_data[user.id]["zip_mode"]
        await query.message.edit_text(f"✅ Tarea para comprimir <b>{len(selected_ids)}</b> archivos enviada.\n⏳ <b>En Cola...</b>", parse_mode=ParseMode.HTML)

async def handle_batch_actions(client: Client, query: CallbackQuery):
    await query.answer()
    user, parts, action = query.from_user, query.data.split("_"), query.data.split("_")[1]

    if action == "cancel": return await query.message.edit_text("Operación en lote cancelada.")
    if action == "apply":
        profile_id, profile_name = parts[2], "Default"
        if profile_id != "default":
            profile = await db_instance.get_preset_by_id(profile_id)
            if not profile: return await query.message.edit_text("❌ El perfil ya no existe.")
            profile_name = profile.get('preset_name', 'N/A').capitalize()
        count = await db_instance.tasks.count_documents({"user_id": user.id, "status": "pending_processing"})
        await query.message.edit_text(f"¿Seguro que desea procesar las <b>{count}</b> tareas pendientes con el perfil '<b>{profile_name}</b>'?",
                                      reply_markup=build_confirmation_keyboard(f"batch_confirm_{profile_id}", "batch_cancel"), parse_mode=ParseMode.HTML)
    elif action == "confirm":
        profile_id, profile_config = parts[2], {}
        if profile_id != "default":
            profile = await db_instance.get_preset_by_id(profile_id)
            if not profile: return await query.message.edit_text("❌ El perfil ya no existe.")
            profile_config = profile.get('config_data', {})
        update_result = await db_instance.tasks.update_many({"user_id": user.id, "status": "pending_processing"},
                                                            {"$set": {"status": "queued", "processing_config": profile_config}})
        await query.message.edit_text(f"✅ ¡Hecho! <b>{update_result.modified_count}</b> tareas han sido enviadas a la forja.", parse_mode=ParseMode.HTML)

async def select_song_from_search(client: Client, query: CallbackQuery):
    await query.answer()
    user, res_id = query.from_user, query.data.split("_")[2]
    await query.message.delete()
    status_message = await client.send_message(user.id, "🔎 Obteniendo información de la canción...")
    try:
        search_result = await db_instance.search_results.find_one({"_id": ObjectId(res_id)})
        if not search_result: return await status_message.edit_text("❌ Error: Resultado de búsqueda ha expirado.")
        url = search_result.get('url') or f"ytsearch:{search_result.get('search_term')}"
        info = await asyncio.to_thread(downloader.get_url_info, url)
        if not info or not info.get('formats'): return await status_message.edit_text("❌ No pude obtener información de ese enlace.")
        best_audio_id = downloader.get_best_audio_format_id(info.get('formats', []))
        processing_config = {"download_format_id": best_audio_id, "audio_format": "mp3"}
        if not await db_instance.add_task(user_id=user.id, file_type='audio', url=info['url'], file_name=sanitize_filename(info['title']),
                                          url_info=info, processing_config=processing_config, status="queued"):
            return await status_message.edit_text("❌ Error al crear la tarea.")
        await status_message.edit_text(f"✅ Canción '<b>{escape_html(info['title'])}</b>' enviada a la forja.", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error en select_song_from_search: {e}", exc_info=True)
        await status_message.edit_text(f"❌ Ocurrió un error inesperado: <code>{escape_html(str(e))}</code>")

async def handle_search_pagination(client: Client, query: CallbackQuery):
    await query.answer()
    parts, search_id, page = query.data.split("_"), query.data.split("_")[2], int(query.data.split("_")[3])
    all_results = await db_instance.search_results.find({"search_id": search_id}).to_list(length=100)
    if not all_results: return await query.message.edit_text("❌ La sesión de búsqueda ha expirado.")
    keyboard = build_search_results_keyboard(all_results, search_id, page=page)
    try: await query.message.edit_reply_markup(reply_markup=keyboard)
    except MessageNotModified: pass

async def cancel_search_session(client: Client, query: CallbackQuery):
    await query.answer("Búsqueda cancelada.")
    await query.message.delete()

async def handle_playlist_action(client: Client, query: CallbackQuery):
    await query.answer()
    user = query.from_user
    parts = query.data.split("_")
    action, playlist_id = parts[2], parts[3]

    user_state = await db_instance.get_user_state(user.id)
    if user_state.get("status") != "awaiting_playlist_action":
        return await query.message.edit_text("❌ Esta selección de playlist ha expirado.")
    
    playlist_info = user_state.get("data", {}).get("playlist_info", {})
    if playlist_info.get("id") != playlist_id:
        return await query.message.edit_text("❌ Error: La información de la playlist no coincide.")

    if action == "process" and parts[3] == "first":
        await query.message.delete()
        first_video_info = playlist_info.get("entries", [{}])[0]
        if not first_video_info:
            return await client.send_message(user.id, "❌ Error: La playlist parece estar vacía.")

        await db_instance.set_user_state(user.id, "awaiting_quality_selection", {"url_info": first_video_info})
        
        caption = (f"<b>📝 Nombre:</b> {escape_html(first_video_info['title'])}\n"
                   f"<b>🕓 Duración:</b> {format_time(first_video_info.get('duration'))}\n\n"
                   "Elija la calidad para la descarga:")
        keyboard = build_detailed_format_menu("url_selection", first_video_info.get('formats', []))
        
        if first_video_info.get('thumbnail'):
            await client.send_photo(user.id, photo=first_video_info['thumbnail'], caption=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            await client.send_message(user.id, text=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)