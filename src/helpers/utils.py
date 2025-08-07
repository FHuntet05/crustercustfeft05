# --- START OF FILE src/helpers/utils.py ---

import os
import time
import asyncio
from html import escape
from datetime import timedelta
from pyrogram.enums import ParseMode

# Cargar el ID del admin desde las variables de entorno de forma segura
try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    ADMIN_USER_ID = None 

def get_greeting(user_id: int) -> str:
    """Devuelve un saludo personalizado si el usuario es el administrador."""
    return "Jefe, " if user_id == ADMIN_USER_ID else ""

def format_bytes(size_in_bytes) -> str:
    """Formatea un tamaño en bytes a un formato legible (KB, MB, GB)."""
    if size_in_bytes is None: return "N/A"
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

def escape_html(text: str) -> str:
    """Escapa caracteres HTML de un texto para evitar problemas de parseo en Telegram."""
    if not isinstance(text, str): 
        return ""
    return escape(text, quote=False)

def _create_text_bar(percentage: float, length: int = 10, fill_char: str = '█', empty_char: str = '░') -> str:
    """Crea una barra de progreso de texto simple."""
    if not 0 <= percentage <= 100: 
        percentage = max(0, min(100, percentage))
    filled_len = int(length * percentage / 100)
    bar = fill_char * filled_len + empty_char * (length - filled_len)
    return bar

def format_time(seconds: float) -> str:
    """Formatea segundos a un formato HH:MM:SS."""
    if seconds is None or not isinstance(seconds, (int, float)) or seconds < 0 or seconds == float('inf'):
        return "∞"
    return str(timedelta(seconds=int(seconds)))

def sanitize_filename(filename: str) -> str:
    """Elimina caracteres inválidos de un nombre de archivo para compatibilidad con sistemas de archivos."""
    if not isinstance(filename, str):
        return "archivo_invalido"
    
    invalid_chars = r'<>:"/\|?*' + '\x00-\x1f\x7f'
    sanitized = "".join(c if c not in invalid_chars else '_' for c in filename)
    sanitized = " ".join(sanitized.split())
    return sanitized[:200]

async def _edit_status_message(user_id: int, text: str, progress_tracker: dict):
    """Edita el mensaje de estado con un throttle para evitar spam de API."""
    if user_id not in progress_tracker: return
    ctx = progress_tracker[user_id]
    
    if text == ctx.last_update_text: return
    ctx.last_update_text = text
    
    current_time = time.time()
    if current_time - ctx.last_edit_time > 1.5:  # --- OPTIMIZACIÓN DE VISUALIZACIÓN ---
        try:
            await ctx.bot.edit_message_text(
                chat_id=ctx.message.chat.id, 
                message_id=ctx.message.id, 
                text=text, 
                parse_mode=ParseMode.HTML
            )
            ctx.last_edit_time = current_time
        except Exception: 
            # Si la edición falla (ej. mensaje eliminado), no hacer nada.
            pass

def _progress_hook_yt_dlp(d, progress_tracker: dict):
    """Hook llamado por yt-dlp durante la descarga."""
    user_id = d.get('user_id')
    if not user_id or user_id not in progress_tracker: return

    ctx = progress_tracker[user_id]
    
    try:
        if d['status'] == 'downloading':
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded_bytes = d.get('downloaded_bytes', 0)
            
            if total_bytes > 0:
                speed = d.get('speed', 0)
                eta = d.get('eta', 0)
                percentage = (downloaded_bytes / total_bytes) * 100
                
                user_mention = "Usuario"
                if hasattr(ctx.message, 'from_user') and ctx.message.from_user:
                    user_mention = ctx.message.from_user.mention

                text = format_status_message(
                    operation="📥 Descargando...",
                    filename=ctx.task.get('original_filename', 'archivo'),
                    percentage=percentage, 
                    processed_bytes=downloaded_bytes, 
                    total_bytes=total_bytes,
                    speed=speed, 
                    eta=eta, 
                    engine="yt-dlp", 
                    user_id=user_id,
                    user_mention=user_mention
                )
                # Enviar la corutina al bucle de eventos principal de forma segura desde un hilo.
                asyncio.run_coroutine_threadsafe(_edit_status_message(user_id, text, progress_tracker), ctx.loop)
    except Exception as e:
        # --- OPTIMIZACIÓN DE VISUALIZACIÓN: Evitar que el hook de progreso falle ---
        # A veces yt-dlp puede enviar un diccionario incompleto, esto lo previene.
        logger.warning(f"Error menor en el hook de progreso de yt-dlp (ignorado): {e}")

def format_status_message(
    operation: str, filename: str, percentage: float, 
    processed_bytes: float, total_bytes: float, speed: float, 
    eta: float, engine: str, user_id: int, user_mention: str
) -> str:
    """
    Construye el mensaje de estado con un formato visual mejorado y detallado.
    """
    # --- OPTIMIZACIÓN DE VISUALIZACIÓN: Formato de mensaje mejorado ---
    bar = _create_text_bar(percentage, 10)
    short_filename = (filename[:45] + '…') if len(filename) > 48 else filename

    is_processing = "Codificando" in operation
    processed_text = format_time(processed_bytes) if is_processing else format_bytes(processed_bytes)
    total_text = format_time(total_bytes) if is_processing else format_bytes(total_bytes)
    speed_text = f"{speed:.2f}x" if is_processing else f"{format_bytes(speed)}/s"

    greeting = get_greeting(user_id).replace(', ', '')
    title = f"<b>{greeting} {operation}</b>"
    
    lines = [
        f"┏ <b>ꜰɪʟᴇ</b>: <code>{escape_html(short_filename)}</code>",
        f"┠ <b>ᴘʀᴏɢʀᴇss</b>: [{bar}] {percentage:.1f}%",
        f"┠ <b>sɪᴢᴇ</b>: {processed_text} / {total_text}",
        f"┠ <b>sᴘᴇᴇᴅ</b>: {speed_text}",
        f"┠ <b>ᴇᴛᴀ</b>: {format_time(eta)}",
        f"┗ <b>ᴇɴɢɪɴᴇ</b>: {engine}",
    ]
    
    return f"{title}\n" + "\n".join(lines)
# --- END OF FILE src/helpers/utils.py ---