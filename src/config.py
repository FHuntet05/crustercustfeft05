# --- START OF FILE src/config.py ---

import os

class Config:
    """
    Clase que centraliza todas las configuraciones de la aplicación.
    Lee las variables de entorno cargadas previamente por `dotenv` en `bot.py`.
    """
    # --- Telegram API ---
    API_ID = os.getenv("API_ID")
    API_HASH = os.getenv("API_HASH")
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    SESSION_NAME = os.getenv("SESSION_NAME", "JefesMediaSuiteBot")

    # --- Bot Behavior ---
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))
    USERBOT_ID = int(os.getenv("USERBOT_ID", 0))
    FORWARD_CHAT_ID = int(os.getenv("FORWARD_CHAT_ID", 0))
    BOT_USERNAME = os.getenv("BOT_USERNAME")
    
    # --- Database ---
    MONGO_URI = os.getenv("MONGO_URI")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "JefesMedia")

    # --- External APIs ---
    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

    # --- Worker & Processing ---
    # Convertimos a entero, con un valor por defecto si no se encuentra.
    WORKER_POLL_INTERVAL = int(os.getenv("WORKER_POLL_INTERVAL", 5)) 
    MAX_DISK_USAGE_PERCENTAGE = int(os.getenv("MAX_DISK_USAGE_PERCENTAGE", 95))
    DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
    
    # --- LA VARIABLE CLAVE PARA SOLUCIONAR EL ERROR DE COPIA ---
    DEFAULT_DESTINATION_PATH = os.getenv("DEFAULT_DESTINATION_PATH")

    # --- Settings de Debug y Control ---
    # Usamos .lower() in ('true', '1', 't') para una evaluación booleana robusta
    DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').lower() in ('true', '1', 't')
    ENABLE_COPY_TO_DESTINATION = os.getenv('ENABLE_COPY_TO_DESTINATION', 'true').lower() in ('true', '1', 't')

# --- END OF FILE src/config.py ---