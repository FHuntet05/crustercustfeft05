# src/helpers/utils.py

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
    """Devuelve un saludo personalizado si el usuario es el administrador."""
    return "Jefe" if user_id == ADMIN_USER_ID else "Usuario"

def format_bytes(size_in_bytes) -> str:
    """Formatea un tamaño en bytes a un formato legible (KB, MB, GB)."""
    if size_in_bytes is None or not isinstance(size_in_bytes, (int, float)): return "N/A"
    try:
        size = float(size_in_bytes)
        if size < 0: return "N/A"
        if size == 0: return "0 B"
        power = 1024
        n = 0
        power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
        while size >= power and n < len(power_labels) - 1:
            size /= power
            n += 1
        return f"{size:.2f} {power_labels[n]}"
    except (ValueError, TypeError):
        return "Tamaño inválido"

def format_view_count(count) -> str:
    if count is None: return "N/A"
    try:
        num = float(count)
        if num < 1000: return str(int(num))
        if num < 1_000_000: return f"{num/1000:.1f}K"
        return f"{num/1_000_000:.1f}M"
    except (ValueError, TypeError): return "N/A"

def format_upload_date(date_str) -> str:
    if date_str is None or len(date_str) != 8: return "N/A"
    try: return datetime.strptime(date_str, "%Y%m%d").strftime("%d-%m-%Y")
    except ValueError: return "N/A"

def escape_html(text: str) -> str:
    if not isinstance(text, str): return ""
    return escape(text, quote=False)

def _create_text_bar(percentage: float, length: int = 20, fill_char: str = '▓', empty_char: str = '░') -> str:
    if not 0 <= percentage <= 100: percentage = max(0, min(100, percentage))
    filled_len = int(length * percentage / 100)
    return fill_char * filled_len + empty_char * (length - filled_len)

def format_time(seconds: float) -> str:
    if seconds is None or not isinstance(seconds, (int, float)) or seconds < 0 or seconds == float('inf'):
        return "∞"
    return str(timedelta(seconds=int(seconds)))

def sanitize_filename(filename: str) -> str:
    if not isinstance(filename, str): return "archivo_invalido"
    invalid_chars = r'<>:"/\|?*' + '\x00-\x1f\x7f'
    sanitized = "".join(c if c not in invalid_chars else '_' for c in filename)
    return " ".join(sanitized.split())[:200]

async def _edit_status_message(user_id: int, text: str, progress_tracker: dict):
    if user_id not in progress_tracker: return
    ctx = progress_tracker[user_id]
    
    if text == ctx.last_update_text: return
    ctx.last_update_text = text
    
    current_time = time.time()
    if current_time - ctx.last_edit_time > 1.5:
        try:
            await ctx.bot.edit_message_text(chat_id=ctx.message.chat.id, message_id=ctx.message.id, text=text, parse_mode=ParseMode.HTML)
            ctx.last_edit_time = current_time
        except Exception as e:
            # --- LÓGICA CORREGIDA ---
            # Registrar el error en lugar de ignorarlo silenciosamente.
            logger.warning(f"No se pudo editar el mensaje de estado {ctx.message.id} para el usuario {user_id}: {e}")

def _progress_hook_yt_dlp(d, progress_tracker: dict):
    user_id = d.get('user_id')
    if not user_id or user_id not in progress_tracker: return
    ctx = progress_tracker[user_id]
    
    try:
        if d['status'] == 'downloading':
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            if total_bytes > 0:
                downloaded_bytes = d.get('downloaded_bytes', 0)
                speed, eta, percentage = d.get('speed', 0), d.get('eta', 0), (downloaded_bytes / total_bytes) * 100
                user_mention = ctx.message.from_user.mention if hasattr(ctx.message, 'from_user') and ctx.message.from_user else "Usuario"
                text = format_status_message(operation="📥 Descargando...", filename=ctx.task.get('original_filename', 'archivo'), percentage=percentage, 
                                           processed_bytes=downloaded_bytes, total_bytes=total_bytes, speed=speed, eta=eta, 
                                           engine="yt-dlp", user_id=user_id, user_mention=user_mention)
                asyncio.run_coroutine_threadsafe(_edit_status_message(user_id, text, progress_tracker), ctx.loop)
    except Exception as e:
        logger.warning(f"Error menor en el hook de progreso de yt-dlp (ignorado): {e}")

def format_status_message(
    operation: str, filename: str, percentage: float, 
    processed_bytes: float, total_bytes: float, speed: float, 
    eta: float, engine: str, user_id: int, user_mention: str
) -> str:
    """
    Construye el mensaje de estado con el formato visual solicitado.
    """
    bar = _create_text_bar(percentage, 20)
    short_filename = (filename[:35] + '…') if len(filename) > 38 else filename
    greeting = get_greeting(user_id)
    
    op_text = operation.replace('...', '').strip()
    header = f"╭─( <b>{greeting}</b> ⚙️ {op_text} )─"

    is_processing = "Procesando" in operation
    
    if is_processing:
        processed_text = format_time(processed_bytes)
        total_text = format_time(total_bytes)
        speed_text = f"{speed:.2f}x" if speed > 0 else "N/A"
    else: # Descargando o Subiendo
        processed_text = format_bytes(processed_bytes)
        total_text = format_bytes(total_bytes)
        speed_text = f"{format_bytes(speed)}/s" if speed > 0 else "N/A"

    lines = [
        header,
        f"┣❯ <b>Archivo:</b> <code>{escape_html(short_filename)}</code>",
        f"┣❯ <b>Progreso:</b> [{bar}] {percentage:.1f}%",
        f"┣❯ <b>Tamaño:</b> {processed_text} de {total_text}",
        f"┣❯ <b>Velocidad:</b> {speed_text}",
        f"┣❯ <b>ETA:</b> {format_time(eta)}",
        f"╰───────────> motor: {engine}"
    ]
    
    return "\n".join(lines)

def generate_summary_caption(task: dict, initial_size: int, final_size: int, final_filename: str) -> str:
    """Genera un resumen detallado de las operaciones realizadas en una tarea."""
    config = task.get('processing_config', {})
    ops = []

    # Detección de operaciones
    if config.get('final_filename'): ops.append(f"✍️ Renombrado")
    if config.get('transcode'): ops.append(f"📉 Transcodificado a {config['transcode'].get('resolution', 'N/A')}")
    if config.get('trim_times'): ops.append(f"✂️ Cortado")
    if config.get('split_criteria'): ops.append(f"🧩 Dividido en partes")
    if config.get('gif_options'): ops.append(f"🎞️ Convertido a GIF")
    if config.get('watermark'): ops.append(f"💧 Marca de agua añadida")
    if config.get('mute_audio'): ops.append(f"🔇 Audio silenciado")
    if config.get('remove_subtitles'): ops.append(f"📜 Subtítulos eliminados")
    if config.get('subs_file_id'): ops.append(f"📜 Subtítulos añadidos")
    if config.get('remove_thumbnail'): ops.append(f"🖼️ Miniatura eliminada")
    if config.get('thumbnail_file_id') and task.get('file_type') == 'video': ops.append(f"🖼️ Miniatura cambiada")
    if config.get('extract_audio'): ops.append(f"🎵 Audio extraído")
    if config.get('replace_audio_file_id'): ops.append(f"🎼 Audio reemplazado")

    if task.get('file_type') == 'audio':
        if config.get('audio_format') or config.get('audio_bitrate'):
            fmt = config.get('audio_format', 'mp3').upper()
            br = config.get('audio_bitrate', '192k')
            ops.append(f"🔊 Convertido a {fmt} ({br})")
        if config.get('audio_tags'): ops.append(f"✍️ Metadatos editados")
        if config.get('thumbnail_file_id'): ops.append(f"🖼️ Carátula cambiada")

    # Construcción del caption
    caption_parts = [f"✅ <b>Proceso Completado</b>"]
    
    size_reduction_str = ""
    if final_size > 0 and initial_size > 0:
        diff = final_size - initial_size
        diff_str = f"+{format_bytes(abs(diff))}" if diff > 0 else f"-{format_bytes(abs(diff))}"
        size_reduction_str = f" ({diff_str})"
    
    caption_parts.append(f"📦 <code>{escape_html(final_filename)}</code>")
    caption_parts.append(f"💾 <b>Tamaño:</b> {format_bytes(initial_size)} ➞ {format_bytes(final_size)}{size_reduction_str}")

    if ops:
        caption_parts.append("\n<b>Operaciones Realizadas:</b>")
        for op in ops:
            caption_parts.append(f"  • {op}")
            
    return "\n".join(caption_parts)