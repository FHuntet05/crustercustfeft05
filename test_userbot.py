import asyncio
import os
from dotenv import load_dotenv
from pyrogram import Client
from pyrogram.errors import PeerIdInvalid

# Cargar variables de entorno desde .env
load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("USERBOT_SESSION_STRING")
FORWARD_CHAT_ID = os.getenv("FORWARD_CHAT_ID")

async def main():
    if not all([API_ID, API_HASH, SESSION_STRING, FORWARD_CHAT_ID]):
        print("❌ ERROR: Faltan una o más variables en el archivo .env (API_ID, API_HASH, USERBOT_SESSION_STRING, FORWARD_CHAT_ID)")
        return

    print("--- INICIANDO PRUEBA DE IDENTIDAD DEL USERBOT ---")
    
    try:
        chat_id_to_test = int(FORWARD_CHAT_ID.strip())
    except ValueError:
        print(f"❌ ERROR: El FORWARD_CHAT_ID '{FORWARD_CHAT_ID}' no es un número válido.")
        return

    # Usar almacenamiento en memoria para no interferir con la sesión del bot principal
    async with Client("test_session", api_id=int(API_ID), api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True) as app:
        try:
            me = await app.get_me()
            print(f"✅ Conectado exitosamente como: {me.username or me.first_name} (ID: {me.id})")
            
            print(f"▶️ Intentando acceder al canal con ID: {chat_id_to_test}...")
            chat = await app.get_chat(chat_id_to_test)
            print(f"✅ ¡ÉXITO! Se accedió al canal '{chat.title}' correctamente.")
            print("--- PRUEBA COMPLETADA. LA CONFIGURACIÓN ES CORRECTA. ---")
        
        except PeerIdInvalid:
            print(f"❌ FALLO: PeerIdInvalid. La cuenta '{me.username}' no puede ver el canal {chat_id_to_test}.")
            print("   MOTIVO MÁS PROBABLE: La USERBOT_SESSION_STRING es de una cuenta que no es miembro del canal.")
            print("--- PRUEBA FALLIDA. ---")
        except Exception as e:
            print(f"❌ FALLO: Ocurrió un error inesperado: {e}")
            print("--- PRUEBA FALLIDA. ---")

if __name__ == "__main__":
    asyncio.run(main())