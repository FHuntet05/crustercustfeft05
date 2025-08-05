import os
import time
from html import escape

try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    ADMIN_USER_ID = None 

def get_greeting(user_id):
    return "Jefe, " if user_id == ADMIN_USER_ID else ""

def format_bytes(size):
    if size is None: return "N/A"
    try:
        size = int(size)
        if size == 0: return "0 B"
        power = 1024; n = 0
        power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
        while size >= power and n < len(power_labels) - 1:
            size /= power
            n += 1
        return f"{size:.2f} {power_labels[n]}"
    except (ValueError, TypeError):
        return "Tamaño inválido"

def escape_html(text: str) -> str:
    if not isinstance(text, str): return ""
    return escape(text)

def create_progress_bar(percentage, length=10):
    if not 0 <= percentage <= 100: percentage = 0
    filled = int(length * percentage / 100)
    bar = '█' * filled + '░' * (length - filled)
    return f"[{bar}]"

def format_time(seconds):
    if seconds is None or seconds < 0: return "N/A"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"