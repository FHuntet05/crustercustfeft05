# --- START OF FILE src/helpers/utils.py ---

import os
import time
import asyncio
from html import escape
from datetime import datetime, timedelta
from pyrogram.enums import ParseMode
import logging
import re
from typing import Dict, Optional
from pyrogram.errors import MessageNotModified, FloodWait

logger = logging.getLogger(__name__)

try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    ADMIN_USER_ID = 0

async def _edit_status_message(user_id: int, text: str, progress_tracker: dict):
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
    return "Jefe" if user_id == ADMIN_USER_ID else "Usuario"

def format_bytes(size_in_bytes) -> str:
    if not isinstance(size_in_bytes, (int, float)) or size_in_bytes <= 0: return "0 B"
    size, n, power = float(size_in_bytes), 0, 1024
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size >= power and n < len(power_labels) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"

def escape_html(text: str) -> str:
    if not isinstance(text, str): return ""
    return escape(text, quote=False)

def _create_text_bar(percentage: float, length: int = 12, fill_char: str = '■', empty_char: str = '□') -> str:
    if not 0 <= percentage <= 100: percentage = 0
    filled_len = int(length * percentage / 100)
    return fill_char * filled_len + empty_char * (length - filled_len)

def format_time(seconds: float) -> str:
    if not isinstance(seconds, (int, float)) or seconds < 0 or seconds == float('inf'): return "∞"
    seconds = int(seconds)
    if seconds < 60: return f"{seconds}s"
    td = timedelta(seconds=seconds)
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    if td.days > 0: return f"{td.days}d {hours:02}:{minutes:02}:{seconds_part:02}"
    if hours > 0: return f"{hours:02}:{minutes:02}:{seconds_part:02}"
    return f"{minutes:02}:{seconds_part:02}"

def sanitize_filename(filename: str) -> str:
    if not isinstance(filename, str): return "archivo_invalido"
    name_base = os.path.splitext(filename)[0]
    sanitized_base = re.sub(r'[^\w\s-]', '', name_base, flags=re.UNICODE)
    sanitized_base = re.sub(r'[\s-]+', ' ', sanitized_base).strip()
    if not sanitized_base: sanitized_base = "archivo_procesado"
    return sanitized_base[:240]

# [DEFINITIVE FIX - TypeError]
# La lógica ha sido reescrita para evitar la reutilización de variables con tipos diferentes.
# Se usan variables separadas para los valores numéricos y sus representaciones en string.
def format_status_message(
    operation_title: str, percentage: float, processed_bytes: float, total_bytes: float,
    speed: float, eta: float, elapsed: float, status_tag: str,
    engine: str, user_id: int, file_info: Optional[str] = None
) -> str:
    bar = _create_text_bar(percentage)
    details = []
    
    if "Process" in operation_title:
        # Usar variables separadas para el string formateado
        processed_str = format_time(processed_bytes)
        total_str = format_time(total_bytes) if total_bytes > 0 else "??:??"
        details.append(f"Processed: {processed_str} de {total_str}")
    else:
        # Usar variables separadas para el string formateado
        processed_str = format_bytes(processed_bytes)
        total_str = format_bytes(total_bytes) if total_bytes > 0 else "0 B"
        details.append(f"Processed: {processed_str} of {total_str}")

    if file_info: details.append(f"File: {file_info}")
    details.append(f"Status: {status_tag}")
    details.append(f"ETA: {format_time(eta)}")

    if "Process" in operation_title: 
        details.append(f"Speed: {speed:.2f}x")
    else: 
        details.append(f"Speed: {format_bytes(speed)}/s")

    details.append(f"Elapsed: {format_time(elapsed)}")
    details.append(f"Engine: {engine}")
    details.append(f"ID: {user_id}")

    lines = [f"<b>{operation_title}</b>", f"<code>[{bar}] {percentage:.2f}%</code>"]
    for i, detail in enumerate(details):
        prefix = '┖' if i == len(details) - 1 else '┠'
        lines.append(f"{prefix} {detail}")
        
    return "\n".join(lines)

def generate_summary_caption(task: Dict, initial_size: int, final_size: int, final_filename: str) -> str:
    config = task.get('processing_config', {})
    ops = []

    original_base = sanitize_filename(task.get('original_filename', ''))
    final_base = sanitize_filename(final_filename)

    if final_base != original_base:
        ops.append("✍️ Renombrado")
    if config.get('transcode'): ops.append(f"📉 Transcodificado a {config['transcode'].get('resolution', 'N/A')}")
    if config.get('watermark'): ops.append("💧 Marca de agua añadida")
    if config.get('mute_audio'): ops.append("🔇 Audio silenciado")
    
    caption_parts = [f"✅ <b>Proceso Completado</b>", f"📦 <code>{escape_html(final_filename)}</code>"]
    
    size_reduction_str = ""
    if final_size > 0 and initial_size > 0:
        diff = final_size - initial_size
        diff_str = f"+{format_bytes(abs(diff))}" if diff > 0 else f"-{format_bytes(abs(diff))}"
        size_reduction_str = f" ({diff_str})"
    
    caption_parts.append(f"💾 <b>Tamaño:</b> {format_bytes(initial_size)} ➞ {format_bytes(final_size)}{size_reduction_str}")

    if ops:
        caption_parts.append("\n<b>Operaciones Realizadas:</b>")
        caption_parts.extend([f"  • {op}" for op in ops])
        
    return "\n".join(caption_parts)

def format_task_details_rich(task: Dict, index: int) -> str:
    file_type = task.get('file_type', 'document')
    emoji_map = {'video': '🎬', 'audio': '🎵', 'document': '📄'}
    emoji = emoji_map.get(file_type, '📁')
    
    display_name = task.get('original_filename') or task.get('url', 'Tarea')
    short_name = (display_name[:50] + '...') if len(display_name) > 53 else display_name
    
    config = task.get('processing_config', {})
    config_parts = []
    
    if sanitize_filename(config.get('final_filename', '')) != sanitize_filename(task.get('original_filename', '')):
        config_parts.append("✏️ Renombrado")

    config_summary = ", ".join(config_parts) if config_parts else "<i>(Sin cambios)</i>"

    metadata = task.get('file_metadata', {})
    meta_parts = []
    if size := metadata.get('size'): meta_parts.append(f"📦 {format_bytes(size)}")
    if duration := metadata.get('duration'): meta_parts.append(f"⏱️ {format_time(duration)}")
    if resolution := metadata.get('resolution'): meta_parts.append(f"🖥️ {resolution}")
    meta_summary = " | ".join(meta_parts)

    lines = [f"<b>{index}.</b> {emoji} <code>{escape_html(short_name)}</code>", f"   └ ⚙️ {config_summary}"]
    if meta_summary:
        lines.append(f"   └ 📊 {meta_summary}")
    return "\n".join(lines)