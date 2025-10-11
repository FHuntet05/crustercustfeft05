from urllib.parse import quote_plus
import os

# La URL original de MongoDB
mongo_uri = "mongodb+srv://enco:Cuba230405?@cluster0.gtvuxtd.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"

# Extraer y codificar usuario y contraseña
username = quote_plus("enco")
password = quote_plus("Cuba230405?")

# Construir la nueva URL
new_mongo_uri = f"mongodb+srv://{username}:{password}@cluster0.gtvuxtd.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"

# Crear el contenido del archivo .env
env_content = f"""# Telegram API
API_ID=22088113
API_HASH=e2d24b4fe087c65780d5875b5a7216a0
TELEGRAM_TOKEN=6855045588:AAFCUXtgKmxhIDNmZGVsQ3hh8pgZtpxtci4

# Admin Settings
ADMIN_IDS=1601545124
ADMIN_USER_ID=1601545124

# MongoDB
MONGO_URI={new_mongo_uri}
MONGO_DB_NAME=JefesMedia

# Bot Settings
SESSION_NAME=JefesMediaSuiteBot
BOT_USERNAME=@theporfessionalfeft05_bot

# Processing Settings
WORKER_POLL_INTERVAL=5
MAX_DISK_USAGE_PERCENTAGE=95
DOWNLOAD_DIR=downloads
"""

# Guardar el archivo
with open('.env', 'w', encoding='utf-8') as f:
    f.write(env_content)

print("Archivo .env creado con éxito con la URL de MongoDB codificada correctamente")