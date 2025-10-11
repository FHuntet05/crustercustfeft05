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

# --- NO USAREMOS EL ID DEL .ENV, LO PONDREMOS MANUALMENTE ---

async def main():
    if not all([API_ID, API_HASH, SESSION_STRING]):
        print("❌ ERROR: Faltan una o más variables en el archivo .env (API_ID, API_HASH, USERBOT_SESSION_STRING)")
        return

    # --- FUNCIONES AUXILIARES ---
    async def test_channel_access(client, chat_identifier):
        try:
            chat = await client.get_chat(chat_identifier)
            print(f"✅ Acceso exitoso al canal '{chat.title}' (ID: {chat.id})")
            return chat.id
        except Exception as e:
            print(f"❌ Error al acceder al canal: {e}")
            return None

    async def download_media_from_restricted(client, message_id, chat_id, destination):
        try:
            message = await client.get_messages(chat_id, message_id)
            if not message:
                print(f"❌ No se encontró el mensaje con ID {message_id}")
                return None
            
            file_path = await client.download_media(message, file_name=destination)
            print(f"✅ Archivo descargado exitosamente: {file_path}")
            return file_path
        except Exception as e:
            print(f"❌ Error al descargar el medio: {e}")
            return None

    # --- TEST DE ACCESO ---
    chat_identifier = input("Ingrese el enlace o ID del canal a probar: ").strip()
    if not chat_identifier:
        print("❌ ERROR: Debe proporcionar un enlace o ID de canal.")
        return

    print("--- INICIANDO PRUEBA CON ENLACE DE INVITACIÓN ---")
    
    async with Client("test_link_session", api_id=int(API_ID), api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True) as app:
        try:
            me = await app.get_me()
            print(f"✅ Conectado exitosamente como: {me.username or me.first_name} (ID: {me.id})")
            
            print(f"▶️ Intentando acceder al canal con el enlace: {chat_identifier}...")
            chat = await app.get_chat(chat_identifier)
            print(f"✅ ¡ÉXITO! Se accedió al canal '{chat.title}' (ID: {chat.id}) correctamente.")
            print("\n--- DIAGNÓSTICO ---")
            print("La 'USERBOT_SESSION_STRING' es CORRECTA.")
            print(f"El ID numérico real del canal es: {chat.id}")
            print("El problema está en la resolución del ID numérico. Usa este nuevo ID en tu .env.")
            print("--------------------")
        
        except PeerIdInvalid:
            print(f"❌ FALLO: PeerIdInvalid. La cuenta '{me.username}' NO PUEDE ver el canal, incluso con el enlace de invitación.")
            print("   CONCLUSIÓN: La 'USERBOT_SESSION_STRING' que estás usando NO pertenece a una cuenta que sea miembro del canal.")
            print("--- PRUEBA FALLIDA. ---")
        except Exception as e:
            print(f"❌ FALLO: Ocurrió un error inesperado: {e}")
            print("--- PRUEBA FALLIDA. ---")

if __name__ == "__main__":
    asyncio.run(main())