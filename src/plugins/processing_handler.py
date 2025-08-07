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
                                   build_watermark_menu, build_position_menu, 
                                   build_audio_metadata_menu, build_tracks_menu, 
                                   build_transcode_menu, build_confirmation_keyboard)
from src.helpers.utils import get_greeting, escape_html, sanitize_filename

logger = logging.getLogger(__name__)

@Client.on_callback_query(filters.regex(r"^p_open_"))
async def open_task_menu_from_p(client: Client, query: CallbackQuery):
    """Manejador para volver al menú principal de una tarea desde un submenú."""
    await query.answer()
    task_id = query.data.split("_")[2]
    task = await db_instance.get_task(task_id)
    if not task:
        return await query.message.edit_text("❌ Error: La tarea ya no existe.")

    await query.message.edit_text(
        f"🛠️ Configurando Tarea:\n<code>{escape_html(task.get('original_filename', '...'))}</code>",
        reply_markup=build_processing_menu(task_id, task['file_type'], task),
        parse_mode=ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"^task_manual_config_"))
async def task_manual_config(client: Client, query: CallbackQuery):
    """Manejador para el botón 'Configuración Manual'."""
    await query.answer()
    task_id = query.data.split("_")[3]
    task = await db_instance.get_task(task_id)
    if not task: return await query.message.edit_text("❌ Tarea no encontrada.")

    await query.message.edit_text(f"✅ Tarea añadida al panel. Use `/p {await db_instance.tasks.count_documents({'user_id': query.from_user.id, 'status': 'pending_processing'})}` para configurarla.")

@Client.on_callback_query(filters.regex(r"^(profile_|panel_delete_all_)"))
async def handle_profile_and_panel_actions(client: Client, query: CallbackQuery):
    await query.answer()
    user = query.from_user
    parts = query.data.split("_")
    action_type, action = parts[0], parts[1]

    if action_type == "profile":
        if action == "apply":
            task_id, preset_id = parts[2], parts[3]
            if not (preset := await db_instance.get_preset_by_id(preset_id)):
                return await query.message.edit_text("❌ El perfil seleccionado ya no existe.")
            
            await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": {"processing_config": preset.get('config_data', {}), "status": "queued"}})
            await query.message.edit_text(f"✅ Perfil '<b>{preset['preset_name'].capitalize()}</b>' aplicado. La tarea ha sido enviada a la forja.", parse_mode=ParseMode.HTML)
        
        elif action == "save" and parts[2] == "request":
            task_id = parts[3]
            if not hasattr(client, 'user_data'): client.user_data = {}
            client.user_data[user.id] = {"active_config": {"task_id": task_id, "menu_type": "profile_save"}}
            await query.message.edit_text("💾 Escriba un nombre para este perfil:", reply_markup=build_back_button(f"p_open_{task_id}"))
        
        elif action == "delete":
            preset_name = parts[3] if len(parts) > 3 else parts[2]
            if parts[2] == "confirm":
                result = await db_instance.user_presets.delete_one({"user_id": user.id, "preset_name": preset_name.lower()})
                await query.message.edit_text(f"🗑️ Perfil '<b>{escape_html(preset_name.capitalize())}</b>' eliminado." if result.deleted_count > 0 else "❌ Perfil no encontrado.")
            elif parts[2] == "cancel":
                await query.message.delete()
    
    elif action_type == "panel" and action == "delete" and parts[2] == "all":
        if parts[3] == "confirm":
            deleted = await db_instance.delete_all_pending_tasks(user.id)
            await query.message.edit_text(f"💥 Panel limpiado. Se descartaron {deleted.deleted_count} tareas.")
        elif parts[3] == "cancel":
            await query.message.edit_text("Operación cancelada.")

@Client.on_callback_query(filters.regex(r"^config_"))
async def show_config_menu(client: Client, query: CallbackQuery):
    await query.answer()
    parts = query.data.split("_"); menu_type, task_id = parts[1], "_".join(parts[2:])
    if not (task := await db_instance.get_task(task_id)):
        return await query.message.edit_text("❌ Error: Tarea no encontrada.")

    keyboards = { "dlquality": build_detailed_format_menu(task_id, task.get('url_info', {}).get('formats', [])), "audioconvert": build_audio_convert_menu(task_id), "audioeffects": build_audio_effects_menu(task_id, task.get('processing_config', {})), "audiometadata": build_audio_metadata_menu(task_id), "watermark": build_watermark_menu(task_id), "tracks": build_tracks_menu(task_id, task.get('processing_config', {})), "transcode": build_transcode_menu(task_id) }
    menu_messages = { "dlquality": "💿 Seleccione la calidad a descargar:", "audioconvert": "🔊 Configure la conversión de audio:", "audioeffects": "🎧 Aplique efectos de audio:", "audiometadata": "🖼️ Elija qué metadatos editar:", "watermark": "💧 Elija un tipo de marca de agua:", "tracks": "📜 Gestione las pistas del video:", "transcode": "📉 Seleccione una resolución para reducir el tamaño:" }
    
    if menu_type in keyboards:
        if menu_type in ["audioeffects", "tracks"]:
            return await query.message.edit_text(menu_messages[menu_type], reply_markup=keyboards[menu_type])
        return await query.message.edit_text(menu_messages[menu_type], reply_markup=keyboards[menu_type])

    if not hasattr(client, 'user_data'): client.user_data = {}
    client.user_data[query.from_user.id] = {"active_config": {"task_id": task_id, "menu_type": menu_type}}

    greeting_prefix = get_greeting(query.from_user.id)
    menu_texts = { "rename": f"✏️ <b>Renombrar Archivo</b>\n\n{greeting_prefix}envíeme el nuevo nombre para <code>{escape_html(task.get('original_filename', 'archivo'))}</code>.\n<i>No incluya la extensión.</i>", "trim": f"✂️ <b>Cortar</b>\n\n{greeting_prefix}envíeme el tiempo de inicio y fin.\nFormatos: <code>HH:MM:SS-HH:MM:SS</code> o <code>MM:SS-MM:SS</code>.", "split": f"🧩 <b>Dividir Video</b>\n\n{greeting_prefix}envíeme el criterio de división por tiempo (ej. <code>300s</code>).", "gif": f"🎞️ <b>Crear GIF</b>\n\n{greeting_prefix}envíeme la duración y los FPS.\nFormato: <code>[segundos] [fps]</code> (ej: <code>5 15</code>).", "audiotags": "🖼️ <b>Editar Tags</b>\n\n{greeting_prefix}envíeme los nuevos metadatos. Formato (omita los que no quiera cambiar):\n\n<code>Título: [Nuevo Título]\nArtista: [Nuevo Artista]\nÁlbum: [Nuevo Álbum]</code>", "audiothumb": f"🖼️ <b>Añadir Carátula</b>\n\n{greeting_prefix}envíeme la imagen para la carátula.", "addsubs": f"➕ <b>Añadir Subtítulos</b>\n\n{greeting_prefix}envíeme el archivo de subtítulos (<code>.srt</code>)." }
    
    text = menu_texts.get(menu_type, "Configuración no reconocida.")
    back_callbacks = {"audiotags": "config_audiometadata_", "audiothumb": "config_audiometadata_", "addsubs": "config_tracks_"}
    back_button_cb = f"{back_callbacks.get(menu_type, 'p_open_')}{task_id}"
    await query.message.edit_text(text, reply_markup=build_back_button(back_button_cb), parse_mode=ParseMode.HTML)

@Client.on_message((filters.photo | filters.document) & filters.reply)
async def handle_media_input(client: Client, message: Message):
    user_id = message.from_user.id
    if not hasattr(client, 'user_data') or not (active_config := client.user_data.get(user_id, {}).get("active_config")): return

    task_id, menu_type = active_config["task_id"], active_config.get("menu_type")
    media = message.photo or message.document
    
    handler_map = { "audiothumb": ("thumbnail_file_id", "✅ Carátula guardada.", f'p_open_{task_id}'), "addsubs": ("subs_file_id", "✅ Subtítulos guardados.", f'config_tracks_{task_id}'), "watermark_image": ("watermark", "✅ Imagen recibida. Ahora, elija la posición:", None) }
    if menu_type not in handler_map: return
    if menu_type in ["audiothumb", "watermark_image"] and hasattr(media, 'mime_type') and not media.mime_type.startswith("image/"): return await message.reply("❌ El archivo no es una imagen.")
    
    key, feedback, next_menu_cb = handler_map[menu_type]
    value = {"type": "image", "file_id": media.file_id} if menu_type == "watermark_image" else media.file_id
    await db_instance.update_task_config(task_id, key, value)
    del client.user_data[user_id]["active_config"]
    
    keyboard = build_position_menu(task_id, "config_watermark") if next_menu_cb is None else build_back_button(next_menu_cb)
    await message.reply(feedback, reply_markup=keyboard)

@Client.on_callback_query(filters.regex(r"^set_"))
async def set_value_callback(client: Client, query: CallbackQuery):
    await query.answer()
    parts = query.data.split("_"); config_type = parts[1]
    task_id, value = parts[2], "_".join(parts[3:])
    
    if not (task := await db_instance.get_task(task_id)): return await query.message.delete()

    config_updates = { "mute": ("mute_audio", not task.get('processing_config', {}).get('mute_audio', False)), "audioprop": (f"audio_{parts[3]}", parts[4]), "audioeffect": (parts[3], not task.get('processing_config', {}).get(parts[3], False)), "trackopt": (parts[3], not task.get('processing_config', {}).get(parts[3], False)), "transcode": (parts[3], parts[4]) }
    
    if config_type == "transcode" and value == "remove_all":
        await db_instance.tasks.update_one({"_id": ObjectId(task_id)}, {"$unset": {"processing_config.transcode": ""}})
    elif config_type in config_updates:
        key, new_value = config_updates[config_type]
        await db_instance.update_task_config(task_id, f"{config_type}.{key}", new_value)
    
    task = await db_instance.get_task(task_id)
    config = task.get('processing_config', {})
    
    # --- CORRECCIÓN ---
    # Llamar a cada constructor de teclado con los argumentos correctos
    if config_type == "audioeffect":
        keyboard = build_audio_effects_menu(task_id, config)
    elif config_type == "trackopt":
        keyboard = build_tracks_menu(task_id, config)
    elif config_type == "transcode":
        keyboard = build_transcode_menu(task_id) # Esta era la línea del error
    else:
        keyboard = build_processing_menu(task_id, task['file_type'], task)
        
    await query.message.edit_text("🛠️ Configuración actualizada.", reply_markup=keyboard)

async def handle_text_input_for_config(client: Client, message: Message):
    user_id, user_input = message.from_user.id, message.text.strip()
    if not (active_config := client.user_data.get(user_id, {}).get('active_config')): return
    
    task_id, menu_type = active_config['task_id'], active_config['menu_type']
    del client.user_data[user_id]['active_config']
    
    try:
        if menu_type == "profile_save":
            task = await db_instance.get_task(task_id)
            if not task: raise ValueError("La tarea original ya no existe.")
            await db_instance.add_preset(user_id, user_input, task.get('processing_config', {}))
            feedback = f"✅ Perfil '<b>{escape_html(user_input.capitalize())}</b>' guardado."
        elif menu_type == "rename": await db_instance.update_task_config(task_id, "final_filename", user_input); feedback = f"✅ Nombre actualizado."
        elif menu_type == "trim":
            if not re.match(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*$", user_input): raise ValueError("Formato de tiempo inválido.")
            await db_instance.update_task_config(task_id, "trim_times", user_input); feedback = f"✅ Tiempos de corte guardados."
        elif menu_type == "split": await db_instance.update_task_config(task_id, "split_criteria", user_input); feedback = f"✅ Criterio de división guardado."
        elif menu_type == "gif": parts = user_input.split(); await db_instance.update_task_config(task_id, "gif_options", {"duration": float(parts[0]), "fps": int(parts[1])}); feedback = f"✅ GIF configurado."
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
            await message.reply("✅ Texto recibido. Ahora, elija la posición:", reply_markup=build_position_menu(task_id, "config_watermark"))
            return
        
        await message.reply(feedback, parse_mode=ParseMode.HTML, quote=True)
    except Exception as e:
        logger.error(f"Error procesando entrada de config '{menu_type}': {e}")
        await message.reply("❌ Formato incorrecto o error al guardar.", quote=True)

    if task := await db_instance.get_task(task_id):
        await message.reply("Volviendo al menú principal...", reply_markup=build_processing_menu(task_id, task['file_type'], task))