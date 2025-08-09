import os
import time
import asyncio
from html import escape
from datetime import datetime, timedelta
from pyrogram.enums import ParseMode
import logging

logger = logging.getLogger(__name__)

try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    ADMIN_USER_ID = None 

def get_greeting(user_id: int) -> str:
    return "Jefe" if user_id == ADMIN_USER_ID else "Usuario"

def format_bytes(size_in_bytes) -> str:
    if size_in_bytes is None or not isinstance(size_in_bytes, (int, float)) or size_in_bytes <= 0:
        return "0 B"
    try:
        size = float(size_in_bytes)
        power = 1024; n = 0
        power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
        while size >= power and n < len(power_labels) - 1:
            size /= power; n += 1
        return f"{size:.2f} {power_labels[n]}"
    except (ValueError, TypeError):
        return "N/A"

def escape_html(text: str) -> str:
    if not isinstance(text, str): return ""
    return escape(text, quote=False)

def _create_text_bar(percentage: float, length: int = 12, fill_char: str = '■', empty_char: str = '□') -> str:
    """Crea una barra de progreso simple y robusta."""
    if not 0 <= percentage <= 100: percentage = 0
    filled_len = int(length * percentage / 100)
    return fill_char * filled_len + empty_char * (length - filled_len)

def format_time(seconds: float) -> str:
    if seconds is None or not isinstance(seconds, (int, float)) or seconds < 0 or seconds == float('inf'):
        return "∞"
    td = timedelta(seconds=int(seconds))
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if td.days > 0: return f"{td.days}d {hours:02}:{minutes:02}:{seconds:02}"
    if hours > 0: return f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"

def sanitize_filename(filename: str) -> str:
    if not isinstance(filename, str): return "archivo_invalido"
    invalid_chars = r'<>:"/\|?*' + '\x00-\x1f\x7f'
    sanitized = "".join(c if c not in invalid_chars else '_' for c in filename)
    return " ".join(sanitized.split())[:200]

def format_status_message(
    operation: str, filename: str, percentage: float,
    processed_bytes: float, total_bytes: float, speed: float, eta: float,
    elapsed_time: float, is_processing: bool = False
) -> str:
    short_filename = (filename[:50] + '...') if len(filename) > 53 else filename
    
    op_map = {"📥 Descargando": "#Downloading", "⚙️ Procesando": "#Processing", "⬆️ Subiendo": "#Uploading"}
    status_tag = op_map.get(operation.strip().replace("...", ""), "#Working")

    lines = [f"<b>{operation}</b>", f"<code>{escape_html(short_filename)}</code>\n"]

    if total_bytes > 0:
        bar = _create_text_bar(percentage)
        lines.append(f"[{bar}] {percentage:.2f}%")
    else:
        # Si el tamaño total es desconocido, no mostramos barra ni porcentaje.
        lines.append(f"[ <i>Calculando...</i> ]")

    if is_processing:
        processed_str = format_time(processed_bytes)
        total_str = format_time(total_bytes) if total_bytes > 0 else "??:??"
        speed_str = f"{speed:.2f}x"
        lines.append(f"┠ Procesado: {processed_str} de {total_str}")
    else:
        processed_str = format_bytes(processed_bytes)
        total_str = format_bytes(total_bytes) if total_bytes > 0 else "???"
        speed_str = f"{format_bytes(speed)}/s"
        lines.append(f"┠ Procesado: {processed_str} de {total_str}")

    lines.extend([
        f"┠ Estado: {status_tag}",
        f"┠ ETA: {format_time(eta)}",
        f"┠ Velocidad: {speed_str}",
        f"┠ Transcurrido: {int(elapsed_time)}s",
        f"┖ Motor: JefesMediaSuite"
    ])
    
    return "\n".join(lines)

def format_task_details_rich(task: dict, index: int) -> str:
    file_type = task.get('file_type', 'document')
    emoji_map = {'video': '🎬', 'audio': '🎵', 'document': '📄', 'join_operation': '🔗', 'zip_operation': '📦'}
    emoji = emoji_map.get(file_type, '📁')
    
    display_name = task.get('original_filename') or task.get('url', 'Tarea de URL')
    short_name = (display_name[:50] + '...') if len(display_name) > 53 else display_name
    
    config = task.get('processing_config', {})
    config_parts = []
    if config.get('transcode'): config_parts.append(f"📉 {config['transcode'].get('resolution', '...')}")
    if config.get('trim_times'): config_parts.append("✂️ Cortado")
    if config.get('gif_options'): config_parts.append("🎞️ GIF")
    if config.get('watermark'): config_parts.append("💧 Marca")
    if config.get('mute_audio'): config_parts.append("🔇 Mudo")
    if config.get('extract_audio'): config_parts.append(f"🎵 Audio")
    
    config_summary = ", ".join(config_parts) if config_parts else "<i>(Default)</i>"

    metadata = task.get('file_metadata', {})
    meta_parts = []
    if size := metadata.get('size'): meta_parts.append(f"📦 {format_bytes(size)}")
    if duration := metadata.get('duration'): meta_parts.append(f"⏱️ {format_time(duration)}")
    if resolution := metadata.get('resolution'): meta_parts.append(f"🖥️ {resolution}")
    meta_summary = " | ".join(meta_parts)

    lines = [f"<b>{index}.</b> {emoji} <code>{escape_html(short_name)}</code>", f"   └ ⚙️ {config_summary}"]
    if meta_summary: lines.append(f"   └ 📊 {meta_summary}")
    return "\n".join(lines)

def generate_summary_caption(task: dict, initial_size: int, final_size: int, final_filename: str) -> str:
    config = task.get('processing_config', {}); ops = []
    if config.get('final_filename'): ops.append(f"✍️ Renombrado")
    if config.get('transcode'): ops.append(f"📉 Transcodificado a {config['transcode'].get('resolution', 'N/A')}")
    if config.get('trim_times'): ops.append(f"✂️ Cortado")
    if config.get('split_criteria'): ops.append(f"🧩 Dividido en partes")
    if config.get('gif_options'): ops.append(f"🎞️ Convertido a GIF")
    if config.get('watermark'): ops.append(f"💧 Marca de agua añadida")
    if config.get('mute_audio'): ops.append(f"🔇 Audio silenciado")
    if config.get('extract_audio'): ops.append(f"🎵 Audio extraído")

    if task.get('file_type') == 'audio':
        if config.get('audio_format') or config.get('audio_bitrate'):
            ops.append(f"🔊 Convertido a {config.get('audio_format', 'mp3').upper()} ({config.get('audio_bitrate', '192k')})")
        if config.get('audio_tags'): ops.append(f"✍️ Metadatos editados")
        if config.get('thumbnail_file_id'): ops.append(f"🖼️ Carátula cambiada")

    caption_parts = [f"✅ <b>Proceso Completado</b>"]
    size_reduction_str = ""
    if final_size > 0 and initial_size > 0:
        diff = final_size - initial_size
        diff_str = f"+{format_bytes(abs(diff))}" if diff > 0 else f"-{format_bytes(abs(diff))}"
        size_reduction_str = f" ({diff_str})"
    
    caption_parts.append(f"📦 <code>{escape_html(final_filename)}</code>")
    caption_parts.append(f"💾 <b>Tamaño:</b> {format_bytes(initial_size)} ➞ {format_bytes(final_size)}{size_reduction_str}")

    if ops:
        caption_parts.append("\n<b>Operaciones Realizadas:</b>"); caption_parts.extend([f"  • {op}" for op in ops])
    return "\n".join(caption_parts)