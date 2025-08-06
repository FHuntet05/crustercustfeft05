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

    # --- ACCIÓN REQUERIDA ---
    # Pega aquí el enlace de invitación privado de tu canal
    chat_identifier = "https://t.me/+rTJhHhkSH3tiMmQ5"

    if "https://t.me/+rTJhHhkSH3tiMmQ5" in chat_identifier:
        print("❌ ERROR: Por favor, edita el archivo 'test_userbot.py' y reemplaza 'https://t.me/+rTJhHhkSH3tiMmQ5' con el enlace de invitación real de tu canal.")
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