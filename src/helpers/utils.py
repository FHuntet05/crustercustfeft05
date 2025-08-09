# --- START OF FILE src/helpers/utils.py ---

import os
import time
import asyncio
from html import escape
from datetime import datetime, timedelta
from pyrogram.enums import ParseMode
import logging
import re
from typing import Dict
from pyrogram.errors import MessageNotModified, FloodWait

logger = logging.getLogger(__name__)

try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    ADMIN_USER_ID = 0

# [COMPATIBILITY FIX]
# Esta función es requerida por el nuevo worker.py
async def _edit_status_message(user_id: int, text: str, progress_tracker: dict):
    """Edita el mensaje de estado de un usuario de forma segura."""
    ctx = progress_tracker.get(user_id)
    if not ctx or not ctx.message:
        return

    # Evitar editar si el texto es el mismo
    if text == ctx.last_update_text:
        return
    ctx.last_update_text = text
    
    try:
        await ctx.bot.edit_message_text(
            chat_id=ctx.message.chat.id,
            message_id=ctx.message.id,
            text=text,
            parse_mode=ParseMode.HTML
        )
    except MessageNotModified:
        pass
    except FloodWait as e:
        logger.warning(f"FloodWait de {e.value} segundos al editar mensaje para el usuario {user_id}.")
        await asyncio.sleep(e.value + 1)
    except Exception as e:
        logger.error(f"Error al editar mensaje de estado para el usuario {user_id}: {e}")


def get_greeting(user_id: int) -> str:
    """Devuelve un saludo personalizado para el admin."""
    return "Jefe" if user_id == ADMIN_USER_ID else "Usuario"

def format_bytes(size_in_bytes) -> str:
    """Convierte bytes a un formato legible (KB, MB, GB)."""
    if not isinstance(size_in_bytes, (int, float)) or size_in_bytes <= 0:
        return "0 B"
    try:
        size = float(size_in_bytes)
        power = 1024
        n = 0
        power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
        while size >= power and n < len(power_labels) - 1:
            size /= power
            n += 1
        return f"{size:.2f} {power_labels[n]}"
    except (ValueError, TypeError):
        return "N/A"

def escape_html(text: str) -> str:
    """Escapa texto para usarlo de forma segura en mensajes HTML de Telegram."""
    if not isinstance(text, str):
        return ""
    return escape(text, quote=False)

def _create_text_bar(percentage: float, length: int = 10, fill_char: str = '█', empty_char: str = '░') -> str:
    """Crea una barra de progreso de texto simple y robusta."""
    if not 0 <= percentage <= 100:
        percentage = 0
    filled_len = int(length * percentage / 100)
    return fill_char * filled_len + empty_char * (length - filled_len)

def format_time(seconds: float) -> str:
    """Convierte segundos a un formato HH:MM:SS o MM:SS."""
    if not isinstance(seconds, (int, float)) or seconds < 0 or seconds == float('inf'):
        return "∞"
    td = timedelta(seconds=int(seconds))
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    if td.days > 0:
        return f"{td.days}d {hours:02}:{minutes:02}:{seconds_part:02}"
    if hours > 0:
        return f"{hours:02}:{minutes:02}:{seconds_part:02}"
    return f"{minutes:02}:{seconds_part:02}"

def sanitize_filename(filename: str) -> str:
    """
    Limpia un nombre de archivo de forma segura, preservando su extensión.
    """
    if not isinstance(filename, str):
        return "archivo_invalido"
    name_base, extension = os.path.splitext(filename)
    sanitized_base = re.sub(r'[^\w\s-]', '', name_base, flags=re.UNICODE)
    sanitized_base = re.sub(r'[\s-]+', ' ', sanitized_base).strip()
    if not sanitized_base:
        sanitized_base = "archivo_procesado"
    return f"{sanitized_base[:240]}{extension}"

# Esta es la función de formateo requerida por el nuevo worker.py
def format_status_message(operation: str, filename: str, percentage: float,
                          processed_bytes: float, total_bytes: float, speed: float, eta: float,
                          engine: str, user_id: int, user_mention: str, is_processing: bool = False, file_size: int = 0) -> str:
    """Genera el mensaje de estado completo para descargas, subidas y procesamiento."""
    short_filename = (filename[:35] + '...') if len(filename) > 38 else filename
    
    lines = [f"<b>{operation}</b>", f"<code>{escape_html(short_filename)}</code>\n"]
    
    if total_bytes > 0:
        bar = _create_text_bar(percentage)
        lines.append(f"Progreso: [{bar}] {percentage:.1f}%")
    else:
        lines.append(f"Progreso: [ <i>Calculando...</i> ]")

    if is_processing:
        processed_str = format_time(processed_bytes)
        total_str = format_time(total_bytes) if total_bytes > 0 else "??:??"
        speed_str = f"{speed:.2f}x" if speed else "N/A"
        size_str = f"({format_bytes(file_size)})" if file_size > 0 else ""
        lines.append(f"┠ 🎞️ {processed_str} de {total_str} {size_str}")
    else:
        processed_str = format_bytes(processed_bytes)
        total_str = format_bytes(total_bytes) if total_bytes > 0 else "---"
        speed_str = f"{format_bytes(speed)}/s" if speed else "N/A"
        lines.append(f"┠ 📦 {processed_str} de {total_str}")

    lines.extend([
        f"┠ 🚀 Velocidad: {speed_str}",
        f"┠ ⏳ ETA: {format_time(eta)}",
        f"┖ ⏱️ Transcurrido: {int(time.time() - (progress_tracker.get(user_id).start_time if user_id in progress_tracker else time.time()))}s"
    ])
    
    return "\n".join(lines)


# Esta es la función de resumen requerida por el nuevo worker.py
def generate_summary_caption(task: Dict, initial_size: int, final_size: int, final_filename: str) -> str:
    """Genera el caption final para el archivo procesado."""
    config = task.get('processing_config', {})
    ops = []

    # Se sanean ambos nombres antes de comparar para asegurar una comparación justa.
    sanitized_original = sanitize_filename(os.path.splitext(task.get('original_filename', ''))[0])
    sanitized_final = sanitize_filename(os.path.splitext(final_filename)[0])

    if sanitized_final != sanitized_original:
        ops.append("✍️ Renombrado")
    if config.get('transcode'): ops.append(f"📉 Transcodificado a {config['transcode'].get('resolution', 'N/A')}")
    if config.get('trim_times'): ops.append("✂️ Cortado")
    if config.get('gif_options'): ops.append("🎞️ Convertido a GIF")
    if config.get('watermark'): ops.append("💧 Marca de agua añadida")
    if config.get('mute_audio'): ops.append("🔇 Audio silenciado")
    if config.get('extract_audio'): ops.append("🎵 Audio extraído")
    
    if task.get('file_type') == 'audio' or config.get('extract_audio'):
        if config.get('audio_format') or config.get('audio_bitrate'):
            ops.append(f"🔊 Convertido a {config.get('audio_format', 'mp3').upper()} ({config.get('audio_bitrate', '192k')})")
        if config.get('slowed'): ops.append("🐌 Efecto Slowed aplicado")
        if config.get('reverb'): ops.append("🌌 Efecto Reverb aplicado")
        if config.get('audio_tags'): ops.append("📝 Metadatos actualizados")
        if config.get('thumbnail_file_id') or config.get('thumbnail_url'): ops.append("🖼️ Carátula actualizada")

    caption_parts = [f"✅ <b>Proceso Completado</b>"]
    
    size_reduction_str = ""
    if final_size > 0 and initial_size > 0:
        diff = final_size - initial_size
        diff_str = f"+{format_bytes(abs(diff))}" if diff > 0 else f"-{format_bytes(abs(diff))}"
        size_reduction_str = f" ({diff_str})"
    
    caption_parts.append(f"📦 <code>{escape_html(final_filename)}</code>")
    if initial_size > 0:
        caption_parts.append(f"💾 <b>Tamaño:</b> {format_bytes(initial_size)} ➞ {format_bytes(final_size)}{size_reduction_str}")
    else:
        caption_parts.append(f"💾 <b>Tamaño Final:</b> {format_bytes(final_size)}")

    if ops:
        caption_parts.append("\n<b>Operaciones Realizadas:</b>")
        caption_parts.extend([f"  • {op}" for op in ops])
        
    return "\n".join(caption_parts)