from urllib.parse import quote_plus

# Credenciales de MongoDB
username = quote_plus("teratrinin2")
password = quote_plus("hpK6PQPCn9b6ZaQh")

# Crear la URL de MongoDB correctamente codificada
mongo_uri = f"mongodb+srv://{username}:{password}@cluster0.gtvuxtd.mongodb.net/JefesMedia?retryWrites=true&w=majority"

# Contenido del archivo .env
env_content = f"""# Credenciales para el Userbot
API_ID=27026389
API_HASH=158b014213c39d3b342a8792e495a5dc
BOT_USERNAME=@theporfessionalfeft05_bot
USERBOT_ID=1601545124

# MongoDB Configuration
MONGO_URI="{mongo_uri}"
MONGO_DB_NAME=JefesMedia

# Admin Configuration
ADMIN_IDS=1601545124
ADMIN_USER_ID=1601545124

# Bot Configuration
SESSION_NAME=JefesMediaSuiteBot
BOT_USERNAME=@theporfessionalfeft05_bot

# Processing Configuration
WORKER_POLL_INTERVAL=5
MAX_DISK_USAGE_PERCENTAGE=95
DOWNLOAD_DIR=downloads

# Debug Configuration
DEBUG_MODE=false
ENABLE_COPY_TO_DESTINATION=true"""

# Escribir el archivo .env
with open('.env', 'w', encoding='utf-8') as f:
    f.write(env_content)

print("Archivo .env creado exitosamente")
print("\nURL de MongoDB codificada:")
print(mongo_uri)