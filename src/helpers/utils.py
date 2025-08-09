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

logger = logging.getLogger(__name__)

try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    ADMIN_USER_ID = None 

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

# [FINAL FIX - CORRECT SANITIZATION]
# Esta es la versión final y correcta de la función.
# 1. Separa el nombre base y la extensión.
# 2. Sanea SOLO el nombre base, eliminando caracteres problemáticos.
# 3. Vuelve a unir el nombre saneado con su extensión original.
# Esto previene los errores de 'Invalid Argument' en FFmpeg de forma definitiva.
def sanitize_filename(filename: str) -> str:
    """
    Limpia un nombre de archivo de forma segura, preservando su extensión.
    """
    if not isinstance(filename, str):
        return "archivo_invalido"

    # Separar el nombre base de la extensión
    name_base, extension = os.path.splitext(filename)

    # Sanear SOLO el nombre base
    # Eliminar cualquier carácter que NO sea alfanumérico (Unicode), espacio, guion o guion bajo.
    sanitized_base = re.sub(r'[^\w\s-]', '', name_base, flags=re.UNICODE)
    
    # Reemplazar múltiples espacios/guiones por un solo espacio y limpiar extremos.
    sanitized_base = re.sub(r'[\s-]+', ' ', sanitized_base).strip()
    
    # Si el nombre base queda vacío, usar un nombre por defecto.
    if not sanitized_base:
        sanitized_base = "archivo_procesado"

    # Limitar la longitud del nombre base
    sanitized_base = sanitized_base[:240]

    # Devolvemos el nombre base saneado. La extensión se manejará por separado en el worker.
    # El propósito de esta función es preparar el NOMBRE, no el nombre.extension completo.
    return sanitized_base


def format_status_message(operation: str, filename: str, percentage: float,
                          processed_bytes: float, total_bytes: float, speed: float, eta: float,
                          elapsed_time: float, is_processing: bool = False) -> str:
    """Genera el mensaje de estado completo para descargas, subidas y procesamiento."""
    short_filename = (filename[:45] + '...') if len(filename) > 48 else filename
    
    op_map = {"📥 Descargando": "#Downloading", "⚙️ Procesando": "#Processing", "⬆️ Subiendo": "#Uploading"}
    status_tag = op_map.get(operation.strip().replace("...", ""), "#Working")

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
        lines.append(f"┠ 🎞️ {processed_str} de {total_str}")
    else:
        processed_str = format_bytes(processed_bytes)
        total_str = format_bytes(total_bytes) if total_bytes > 0 else "---"
        speed_str = f"{format_bytes(speed)}/s" if speed else "N/A"
        lines.append(f"┠ 📦 {processed_str} de {total_str}")

    lines.extend([
        f"┠ 🚀 Velocidad: {speed_str}",
        f"┠ ⏳ ETA: {format_time(eta)}",
        f"┖ ⏱️ Transcurrido: {int(elapsed_time)}s"
    ])
    
    return "\n".join(lines)

def format_task_details_rich(task: Dict, index: int) -> str:
    """Genera una descripción detallada y rica de una tarea para el /panel."""
    file_type = task.get('file_type', 'document')
    emoji_map = {'video': '🎬', 'audio': '🎵', 'document': '📄', 'join_operation': '🔗', 'zip_operation': '📦'}
    emoji = emoji_map.get(file_type, '📁')
    
    display_name = task.get('original_filename') or task.get('url', 'Tarea de URL')
    short_name = (display_name[:50] + '...') if len(display_name) > 53 else display_name
    
    config = task.get('processing_config', {})
    config_parts = []
    
    if rn := config.get('final_filename'):
        original_name_base = os.path.splitext(task.get('original_filename', ''))[0]
        # La sanitización puede alterar el nombre, así que comparamos versiones saneadas
        if sanitize_filename(rn) != sanitize_filename(original_name_base):
            config_parts.append("✏️ Renombrado")
    if config.get('transcode'): config_parts.append(f"📉 {config['transcode'].get('resolution', '...')}")
    if config.get('trim_times'): config_parts.append("✂️ Cortado")
    if config.get('gif_options'): config_parts.append("🎞️ GIF")
    if config.get('watermark'): config_parts.append("💧 Marca Agua")
    if config.get('mute_audio'): config_parts.append("🔇 Silenciado")
    if config.get('extract_audio'): config_parts.append("🎵 Extraer Audio")
    
    if config.get('audio_format') or config.get('audio_bitrate'):
        config_parts.append(f"🔊 Convertido ({config.get('audio_format','mp3')})")
    if config.get('slowed') or config.get('reverb'): config_parts.append("🎧 Efectos")
    if config.get('audio_tags'): config_parts.append("📝 Metadatos")
    if config.get('thumbnail_file_id') or config.get('thumbnail_url'): config_parts.append("🖼️ Carátula")

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

def generate_summary_caption(task: Dict, initial_size: int, final_size: int, final_filename: str) -> str:
    """Genera el caption final para el archivo procesado."""
    config = task.get('processing_config', {})
    ops = []

    original_name_base = os.path.splitext(task.get('original_filename', ''))[0]
    if sanitize_filename(config.get('final_filename', original_name_base)) != sanitize_filename(original_name_base):
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