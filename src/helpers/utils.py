import os
import time

# Leemos el ADMIN_USER_ID aquí para que esté disponible globalmente en los helpers
try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    ADMIN_USER_ID = None 

def get_greeting(user_id):
    """Devuelve un saludo personalizado si el usuario es el admin."""
    return "Jefe, " if user_id == ADMIN_USER_ID else ""

def format_bytes(size):
    """Formatea bytes a un formato legible (KB, MB, GB)."""
    if size is None: return "N/A"
    try:
        size = int(size)
        power = 1024; n = 0
        power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
        while size >= power and n < len(power_labels) - 1:
            size /= power
            n += 1
        return f"{size:.2f} {power_labels[n]}"
    except (ValueError, TypeError):
        return "Tamaño inválido"

def escape_html(text: str) -> str:
    """Escapa caracteres HTML para evitar errores de formato en Telegram."""
    if not isinstance(text, str): return ""
    return text.replace("&", "&").replace("<", "<").replace(">", ">")

# --- NUEVA FUNCIÓN DE PROGRESO ---
def create_progress_bar(percentage, length=10):
    """Genera una barra de progreso visual: [█████░░░░░]"""
    if not 0 <= percentage <= 100: percentage = 0
    filled = int(length * percentage / 100)
    bar = '█' * filled + '░' * (length - filled)
    return f"[{bar}]"

# --- NUEVA FUNCIÓN DE TIEMPO ---
def format_time(seconds):
    """Formatea segundos a un formato legible (HH:MM:SS)."""
    if seconds is None: return "N/A"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"