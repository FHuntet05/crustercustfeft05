# --- START OF FILE src/helpers/utils.py ---

import os
import time
import asyncio
from html import escape
from datetime import timedelta
import re
from typing import Dict, Union, Optional

from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified, FloodWait
import logging

logger = logging.getLogger(__name__)

try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    ADMIN_USER_ID = 0

async def _edit_status_message(user_id: int, text: str, progress_tracker: dict):
    """Edita un mensaje de estado de forma segura, evitando spam y manejando errores."""
    ctx = progress_tracker.get(user_id)
    if not ctx or not ctx.message or text == ctx.last_update_text: return
    ctx.last_update_text = text
    try:
        await ctx.bot.edit_message_text(
            chat_id=ctx.message.chat.id, message_id=ctx.message.id,
            text=text, parse_mode=ParseMode.HTML
        )
    except MessageNotModified: pass
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
    except Exception as e:
        logger.error(f"Error al editar mensaje de estado para {user_id}: {e}")

def get_greeting(user_id: int) -> str:
    """Devuelve un saludo personalizado."""
    return "Jefe" if user_id == ADMIN_USER_ID else "Usuario"

def format_bytes(size_in_bytes: Union[int, float]) -> str:
    """Formatea bytes a un formato legible (KB, MB, GB)."""
    if not isinstance(size_in_bytes, (int, float)) or size_in_bytes <= 0: return "0 B"
    power, n = 1024, 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size_in_bytes >= power and n < len(power_labels) - 1:
        size_in_bytes /= power
        n += 1
    return f"{size_in_bytes:.2f} {power_labels[n]}"

def escape_html(text: str) -> str:
    """Escapa texto para ser usado de forma segura en mensajes HTML de Telegram."""
    if not isinstance(text, str): return ""
    return escape(text, quote=False)

def _create_text_bar(percentage: float, length: int = 12, fill_char: str = '■', empty_char: str = '□') -> str:
    """Crea una barra de progreso de texto."""
    if not 0 <= percentage <= 100: percentage = 0
    filled_len = int(length * percentage / 100)
    return fill_char * filled_len + empty_char * (length - filled_len)

def format_time(seconds: Union[int, float]) -> str:
    """Formatea segundos a un formato de tiempo legible (ej. 01:23, 1d 04:15:30)."""
    if not isinstance(seconds, (int, float)) or seconds < 0 or seconds == float('inf'): return "∞"
    seconds = int(seconds)
    td = timedelta(seconds=seconds)
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    if days > 0: return f"{days}d {hours:02d}:{minutes:02d}:{seconds_part:02d}"
    if hours > 0: return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"
    return f"{minutes:02d}:{seconds_part:02d}"

def sanitize_filename(filename: str) -> str:
    """Limpia un string para que sea un nombre de archivo válido y seguro."""
    if not isinstance(filename, str): return "archivo_invalido"
    name_base = os.path.splitext(filename)[0]
    sanitized_base = re.sub(r'[^\w\s.-]', '', name_base, flags=re.UNICODE)
    sanitized_base = re.sub(r'[\s_]+', ' ', sanitized_base).strip()
    if not sanitized_base: sanitized_base = "archivo_procesado"
    return sanitized_base[:240]

def format_status_message(
    operation_title: str, percentage: float, processed_bytes: float, total_bytes: float,
    speed: float, eta: float, elapsed: float, status_tag: str,
    engine: str, user_id: int, file_info: Optional[str] = None
) -> str:
    """Construye el mensaje de estado/progreso completo."""
    bar = _create_text_bar(percentage)
    lines = [f"<b>{operation_title}</b>", f"<code>[{bar}] {percentage:.2f}%</code>"]
    
    is_processing = "Process" in operation_title
    processed_str = format_time(processed_bytes) if is_processing else format_bytes(processed_bytes)
    total_str = format_time(total_bytes) if is_processing and total_bytes > 0 else format_bytes(total_bytes)

    details = [
        f"Progreso: {processed_str} de {total_str}",
        f"Estado: {status_tag}",
        f"ETA: {format_time(eta)}"
    ]

    if is_processing:
        details.append(f"Velocidad: {speed:.2f}x")
    else:
        details.append(f"Velocidad: {format_bytes(speed)}/s")

    details.append(f"Transcurrido: {format_time(elapsed)}")
    details.append(f"Motor: {engine} | ID: {user_id}")

    lines.extend([f"├ {detail}" for detail in details])
    return "\n".join(lines)

def generate_summary_caption(task: Dict, initial_size: int, final_size: int, final_filename: str) -> str:
    """Genera el caption para el archivo final, resumiendo las operaciones realizadas."""
    config = task.get('processing_config', {})
    ops = []

    # [MEJORA] Lista de operaciones más completa y dinámica.
    if sanitize_filename(final_filename) != sanitize_filename(task.get('original_filename', '')):
        ops.append("✍️ Renombrado")
    if config.get('transcode'):
        ops.append(f"📉 Transcodificado a {config['transcode'].get('resolution', 'N/A')}")
    if config.get('trim_times'):
        ops.append("✂️ Cortado")
    if config.get('gif_options'):
        ops.append("🎞️ GIF Creado")
    if config.get('watermark'):
        ops.append("💧 Marca de agua añadida")
    if config.get('mute_audio'):
        ops.append("🔇 Audio silenciado")
    
    caption_parts = [f"✅ <b>Proceso Completado</b>", f"📦 <code>{escape_html(final_filename)}</code>"]
    
    size_change_str = ""
    if final_size > 0 and initial_size > 0:
        diff = final_size - initial_size
        sign = "+" if diff > 0 else "-"
        size_change_str = f" ({sign}{format_bytes(abs(diff))})"
    
    caption_parts.append(f"💾 <b>Tamaño:</b> {format_bytes(initial_size)} → {format_bytes(final_size)}{size_change_str}")

    if ops:
        caption_parts.append("\n<b>Operaciones Realizadas:</b>")
        caption_parts.extend([f"  • {op}" for op in ops])
        
    return "\n".join(caption_parts)

def format_task_details_rich(task: Dict, index: int) -> str:
    """Formatea la descripción de una tarea para el comando /panel."""
    file_type = task.get('file_type', 'document')
    emoji_map = {'video': '🎬', 'audio': '🎵', 'document': '📄'}
    emoji = emoji_map.get(file_type, '📁')
    
    display_name = task.get('original_filename') or task.get('url', 'Tarea sin nombre')
    short_name = (display_name[:50] + '...') if len(display_name) > 53 else display_name
    
    config = task.get('processing_config', {})
    
    # [MEJORA] Resumen de configuración dinámico y detallado para el panel.
    config_tags = []
    if config.get('final_filename', sanitize_filename(task.get('original_filename', ''))) != sanitize_filename(task.get('original_filename', '')):
        config_tags.append("✏️ Ren.")
    if res := config.get('transcode', {}).get('resolution'):
        config_tags.append(f"📉 {res}")
    if config.get('trim_times'):
        config_tags.append("✂️ Cortado")
    if config.get('gif_options'):
        config_tags.append("🎞️ GIF")
    if config.get('watermark'):
        config_tags.append("💧 Marca")
    if config.get('mute_audio'):
        config_tags.append("🔇 Mudo")

    config_summary = " ".join(config_tags) if config_tags else "<i>(Sin cambios)</i>"

    metadata = task.get('file_metadata', {})
    meta_parts = []
    if size := metadata.get('size'): meta_parts.append(f"📦 {format_bytes(size)}")
    if duration := metadata.get('duration'): meta_parts.append(f"⏱️ {format_time(duration)}")
    if resolution := metadata.get('resolution'): meta_parts.append(f"🖥️ {resolution}")
    meta_summary = " | ".join(meta_parts) if meta_parts else ""

    lines = [f"<b>{index}.</b> {emoji} <code>{escape_html(short_name)}</code>", f"   └ ⚙️ {config_summary}"]
    if meta_summary:
        lines.append(f"   └ 📊 {meta_summary}")
    return "\n".join(lines)