# --- INICIO DEL ARCHIVO src/plugins/processing_handler.py ---

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_processing_menu, build_transcode_menu, build_tracks_menu, build_watermark_menu, build_position_menu, build_thumbnail_menu, build_audio_metadata_menu, build_back_button
from src.helpers.utils import sanitize_filename, escape_html, get_media_info

logger = logging.getLogger(__name__)

# --- [NUEVA LÓGICA] Punto de Entrada Universal para Menús ---

async def _update_and_redisplay_menu(client: Client, query: CallbackQuery, task_id: str, answer_text: str = ""):
    """
    Función central para redibujar el menú de procesamiento.
    Obtiene los datos más recientes de la tarea y edita el mensaje del panel.
    """
    try:
        task = await db_instance.get_task(task_id)
        if not task:
            await query.answer("❌ La tarea ya no existe.", show_alert=True)
            return await query.message.delete()

        text_content = f"🛠️ <b>Configurando Tarea:</b>\n<code>{escape_html(task.get('original_filename', '...'))}</code>\n\n"
        text_content += "<i>Modifica las opciones y presiona 'Procesar' cuando termines.</i>"
        
        markup = build_processing_menu(task_id, task.get('file_type', 'video'), task)
        
        await query.message.edit_text(
            text=text_content,
            reply_markup=markup,
            parse_mode=ParseMode.HTML
        )
        if answer_text:
            await query.answer(answer_text)
    except MessageNotModified:
        await query.answer() # Responde al callback aunque no haya cambios
    except Exception as e:
        logger.error(f"Error al actualizar el menú: {e}", exc_info=True)
        await query.answer("❌ Error al actualizar la interfaz.", show_alert=True)

# --- [REFACTORIZADO] Router de Callbacks Único y Centralizado ---

@Client.on_callback_query(filters.regex(r"^(p_open_|task_|config_|set_)"))
async def main_processing_router(client: Client, query: CallbackQuery):
    """
    Manejador único para todas las acciones de configuración de tareas.
    """
    user_id = query.from_user.id
    data = query.data
    
    try:
        if data.startswith("p_open_"):
            task_id = data.split("_")[2]
            await db_instance.set_user_state(user_id, "idle")
            await _update_and_redisplay_menu(client, query, task_id)

        elif data.startswith("task_"):
            action, task_id = data.split("_")[1], data.split("_")[2]
            if action == "queuesingle":
                await db_instance.update_task_field(task_id, "status", "queued")
                await query.message.edit_text("✅ Tarea enviada a la cola.\nRecibirás el archivo cuando finalice.", parse_mode=ParseMode.HTML)
            elif action == "delete":
                await db_instance.delete_task_by_id(task_id)
                await query.message.edit_text("🗑️ Tarea cancelada y eliminada.")
            await db_instance.set_user_state(user_id, "idle")

        elif data.startswith("config_"):
            await handle_config_selection(client, query)

        elif data.startswith("set_"):
            await handle_set_value(client, query)

    except Exception as e:
        logger.error(f"Error en el router de procesamiento: {e}", exc_info=True)
        await query.answer("❌ Ocurrió un error inesperado.", show_alert=True)

# --- [REFACTORIZADO] Manejadores de Lógica Específica ---

async def handle_config_selection(client: Client, query: CallbackQuery):
    """Maneja la selección de un submenú o la solicitud de entrada de texto/media."""
    user_id, data = query.from_user.id, query.data
    parts = data.split("_")
    menu_type, task_id = parts[1], parts[2]

    task = await db_instance.get_task(task_id)
    if not task: return await query.message.delete()
    
    config = task.get('processing_config', {})
    
    # Menús que no requieren entrada del usuario
    keyboards = {
        "transcode": build_transcode_menu(task_id),
        "tracks": build_tracks_menu(task_id, config),
        "watermark": build_watermark_menu(task_id),
        "thumbnail": build_thumbnail_menu(task_id, config),
        "audiometadata": build_audio_metadata_menu(task_id)
    }
    menu_messages = {
        "transcode": "📉 Elige la nueva resolución. El video será recomprimido.",
        "tracks": "📜 Gestiona las pistas de audio y subtítulos.",
        "watermark": "💧 Elige el tipo de marca de agua a aplicar.",
        "thumbnail": "🖼️ Añade o elimina la miniatura del video.",
        "audiometadata": "📝 Edita los metadatos del archivo de audio."
    }
    
    if menu_type in keyboards:
        await query.message.edit_text(text=menu_messages[menu_type], reply_markup=keyboards[menu_type], parse_mode=ParseMode.HTML)
        return

    # Estados que requieren entrada del usuario
    state_map = {
        "rename": "awaiting_rename", "trim": "awaiting_trim", "gif": "awaiting_gif",
        "addsubs": "awaiting_subs", "thumbnail_add": "awaiting_thumbnail_add",
        "replace_audio": "awaiting_replace_audio", "watermark_text": "awaiting_watermark_text",
        "watermark_image": "awaiting_watermark_image", "audiotags": "awaiting_audiotags",
        "audiothumb": "awaiting_audiothumb"
    }
    prompt_messages = {
        "rename": "✏️ Envíame el <b>nuevo nombre</b> para el archivo (sin extensión).",
        "trim": "✂️ Envíame los tiempos de corte.\nFormatos: <code>HH:MM:SS-HH:MM:SS</code> (rango) o <code>HH:MM:SS</code> (corta hasta ese punto).",
        "gif": "🎞️ Envíame la <b>duración</b> y los <b>FPS</b> separados por un espacio (ej: <code>5 15</code>).",
        "addsubs": "➕ Envíame el archivo de subtítulos en formato <code>.srt</code>.",
        "thumbnail_add": "🖼️ Envíame la imagen que quieres usar como <b>miniatura</b>.",
        "replace_audio": "🎼 Envíame el nuevo archivo de <b>audio</b> que reemplazará al original.",
        "watermark_text": "💧 Envíame el <b>texto</b> que quieres usar como marca de agua.",
        "watermark_image": "🖼️ Envíame la <b>imagen</b> que quieres usar como marca de agua.",
        "audiotags": "✍️ Envíame los metadatos con el formato:\n<code>Título: Mi Canción\nArtista: El Artista</code>",
        "audiothumb": "🖼️ Envíame la imagen de la <b>carátula</b>."
    }

    if menu_type in state_map:
        await db_instance.set_user_state(user_id, state_map[menu_type], data={"task_id": task_id})
        back_button = build_back_button(f"p_open_{task_id}")
        await query.message.edit_text(prompt_messages[menu_type], reply_markup=back_button, parse_mode=ParseMode.HTML)


async def handle_set_value(client: Client, query: CallbackQuery):
    """Maneja cambios de configuración que no requieren entrada adicional (toggles, selección de menú)."""
    user_id, data = query.from_user.id, query.data
    parts = data.split("_")
    config_type, task_id = parts[1], parts[2]
    
    await db_instance.set_user_state(user_id, "idle")
    task = await db_instance.get_task(task_id)
    if not task: return await query.message.delete()
    
    config = task.get('processing_config', {})
    answer_text = "✅ Configuración actualizada"

    if config_type == "transcode":
        value = parts[-1]
        if value == "remove":
            await db_instance.unset_task_config_key(task_id, "quality")
            answer_text = "✅ Calidad restaurada a la original."
        else:
            await db_instance.update_task_config(task_id, "quality", value)
            answer_text = f"✅ Calidad establecida a {value}."
            
    elif config_type == "watermark":
        action = parts[3]
        if action == "remove":
            await db_instance.unset_task_config_key(task_id, "watermark")
            answer_text = "✅ Marca de agua eliminada."
        elif action == "position":
            position = parts[4].replace('-', '_')
            await db_instance.update_task_config(task_id, "watermark.position", position)
            answer_text = f"✅ Posición de marca de agua establecida."

    elif config_type == "mute":
        new_value = not config.get('mute_audio', False)
        await db_instance.update_task_config(task_id, 'mute_audio', new_value)
        answer_text = "🔇 Audio silenciado." if new_value else "🔊 Audio restaurado."
        
    elif config_type == "trackopt":
        key = "remove_subtitles"
        new_value = not config.get(key, False)
        await db_instance.update_task_config(task_id, key, new_value)
        answer_text = "✅ Subtítulos eliminados." if new_value else "✅ Subtítulos conservados."

    await _update_and_redisplay_menu(client, query, task_id, answer_text)

# --- [REFACTORIZADO] Manejadores de Entrada de Usuario (Texto y Media) ---

async def handle_text_input_for_state(client: Client, message: Message, user_state: dict):
    """Maneja la entrada de texto del usuario y actualiza el menú principal."""
    user_id, user_input = message.from_user.id, message.text.strip()
    state, data = user_state['status'], user_state['data']
    task_id = data.get('task_id')
    
    if not task_id:
        await db_instance.set_user_state(user_id, "idle")
        return await message.reply("❌ Error de sesión. Por favor, abre el panel de nuevo.")

    success = False
    if state == "awaiting_rename":
        if user_input:
            await db_instance.update_task_config(task_id, "final_filename", sanitize_filename(user_input))
            success = True
    elif state == "awaiting_trim":
        # Validación simple de formato de tiempo
        if re.match(r'^[\d:.-]+$', user_input):
            await db_instance.update_task_config(task_id, "trim_times", user_input)
            success = True
    elif state == "awaiting_watermark_text":
        if user_input:
            await db_instance.update_task_config(task_id, "watermark", {"type": "text", "text": user_input, "position": "bottom_right"})
            success = True
            
    # [Añadir más lógica para otros estados aquí]
    
    if success:
        await db_instance.set_user_state(user_id, "idle")
        # Simula un CallbackQuery para usar la función de actualización
        class FakeQuery:
            def __init__(self, msg, uid): self.message = msg; self.from_user = type('user', (), {'id':uid})
            async def answer(self, *args, **kwargs): pass
        
        original_message = getattr(message, "reply_to_message", None)
        if original_message is None or getattr(original_message, "id", None) is None:
            await client.send_message(
                user_id,
                "⚠️ No pude localizar el panel original. Por favor vuelve a abrir el menú usando /start.",
                parse_mode=ParseMode.HTML
            )
            return

        original_panel_message = await client.get_messages(user_id, original_message.id)
        await _update_and_redisplay_menu(client, FakeQuery(original_panel_message, user_id), task_id, "✅ Configuración guardada.")
        await message.delete() # Borra el mensaje de entrada del usuario
    else:
        await message.reply("❌ Entrada inválida. Por favor, inténtalo de nuevo.")


async def handle_media_input_for_state(client: Client, message: Message, user_state: dict):
    """Maneja la entrada de media del usuario y actualiza el menú principal."""
    user_id, state, data = message.from_user.id, user_state['status'], user_state['data']
    task_id = data.get('task_id')
    media = message.photo or message.document
    
    if not task_id or not media:
        await db_instance.set_user_state(user_id, "idle")
        return await message.reply("❌ Error de sesión o archivo no válido.")

    success = False
    if state == "awaiting_watermark_image":
        if message.photo or (hasattr(media, 'mime_type') and media.mime_type.startswith("image/")):
            await db_instance.update_task_config(task_id, "watermark", {"type": "image", "file_id": media.file_id, "position": "bottom_right"})
            success = True
    
    # [Añadir más lógica para otros estados aquí]

    if success:
        await db_instance.set_user_state(user_id, "idle")
        class FakeQuery:
            def __init__(self, msg, uid): self.message = msg; self.from_user = type('user', (), {'id':uid})
            async def answer(self, *args, **kwargs): pass
        
        original_panel_message = await client.get_messages(user_id, message.reply_to_message.id)
        await _update_and_redisplay_menu(client, FakeQuery(original_panel_message, user_id), task_id, "✅ Archivo recibido y guardado.")
        await message.delete()
    else:
        await message.reply("❌ Archivo no válido para esta operación.")

# Add a function to handle direct video processing
async def handle_direct_video(client: Client, message: Message):
    """Handles videos sent directly to the bot."""
    user_id = message.from_user.id
    video_info = await get_media_info(message)

    if not video_info["file_name"]:
        video_info["file_name"] = f"video_{int(time.time())}.mp4"

    task_data = {
        "user_id": user_id,
        "file_id": message.video.file_id,
        "original_filename": video_info["file_name"],
        "file_type": "video",
        "file_metadata": {
            "size": video_info["file_size"],
            "duration": video_info["duration"],
            "width": video_info["width"],
            "height": video_info["height"],
            "mime_type": video_info["mime_type"]
        },
        "status": "pending_processing",
        "created_at": datetime.utcnow(),
        "processing_config": {
            "quality": "1080p",
            "content_type": "default"
        }
    }

    task_id = await db_instance.create_task(task_data)
    if task_id:
        await _update_and_redisplay_menu(client, message, task_id, "Task created successfully.")
    else:
        await message.reply("Failed to create task.")

@Client.on_callback_query(filters.regex(r"^compress_video_"))
async def handle_compress_video(client: Client, query: CallbackQuery):
    """Handles the 'Compress Video' button click."""
    task_id = query.data.split("_")[2]
    task = await db_instance.get_task(task_id)

    if not task:
        await query.answer("Task not found.", show_alert=True)
        return

    video_name = task.get("original_filename", "Unknown Video")
    compression_menu = InlineKeyboardMarkup([
        [InlineKeyboardButton("1080p", callback_data=f"set_compression_{task_id}_1080p"),
         InlineKeyboardButton("720p", callback_data=f"set_compression_{task_id}_720p")],
        [InlineKeyboardButton("480p", callback_data=f"set_compression_{task_id}_480p"),
         InlineKeyboardButton("Cancel", callback_data=f"cancel_task_{task_id}")]
    ])

    await query.message.edit_text(
        f"\ud83c\udfa5 <b>Compressing Video:</b>\n<code>{escape_html(video_name)}</code>\n\nSelect a quality:",
        reply_markup=compression_menu,
        parse_mode=ParseMode.HTML
    )

async def handle_ffmpeg_errors(input_file: str, output_file: str) -> str:
    """
    Maneja errores comunes de FFmpeg y aplica soluciones.

    Args:
        input_file (str): Ruta del archivo de entrada.
        output_file (str): Ruta del archivo de salida.

    Returns:
        str: Mensaje de estado indicando éxito o fallo.
    """
    try:
        # Validar el archivo antes de procesarlo
        validate_command = ["ffmpeg", "-v", "error", "-i", input_file, "-f", "null", "-"]
        validate_process = await asyncio.create_subprocess_exec(
            *validate_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, validate_stderr = await validate_process.communicate()

        if validate_process.returncode != 0:
            logger.error(f"Error validando archivo: {validate_stderr.decode()}")
            return "❌ Error: Archivo inválido o corrupto."

        # Intentar reparar el archivo si el error es "moov atom not found"
        repair_command = [
            "ffmpeg", "-i", input_file, "-c", "copy", output_file
        ]
        repair_process = await asyncio.create_subprocess_exec(
            *repair_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, repair_stderr = await repair_process.communicate()

        if repair_process.returncode == 0:
            return f"✅ Archivo reparado exitosamente: {output_file}"
        elif b"moov atom not found" in repair_stderr:
            logger.error(f"Error reparando archivo: {repair_stderr.decode()}")
            return "❌ Error: Archivo corrupto. No se pudo reparar."

        # Intentar conversión segura si el error es "Conversion failed"
        conversion_command = [
            "ffmpeg", "-i", input_file, "-preset", "ultrafast", "-c:v", "libx264", "-c:a", "aac", output_file
        ]
        conversion_process = await asyncio.create_subprocess_exec(
            *conversion_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, conversion_stderr = await conversion_process.communicate()

        if conversion_process.returncode == 0:
            return f"✅ Conversión completada: {output_file}"
        elif b"Conversion failed" in conversion_stderr:
            logger.error(f"Error en conversión: {conversion_stderr.decode()}")
            return "❌ Error: Fallo en la conversión. Parámetros incompatibles."

        return "❌ Error desconocido durante el procesamiento con FFmpeg."

    except Exception as e:
        logger.error(f"Error manejando FFmpeg: {e}")
        return f"❌ Error inesperado: {str(e)}"