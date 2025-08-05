# generate_session.py
import asyncio
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

async def main():
    print("🚀 Generador de String Session de Telethon 🚀")
    print("--------------------------------------------")
    print("Ingresa tus credenciales de Telegram para generar la sesión.")
    print("Estos datos solo se usan para la autenticación y no se guardan aquí.\n")

    # Pide las credenciales de forma segura
    try:
        api_id = int(input("🔑 Ingresa tu API_ID: "))
        api_hash = input("🔒 Ingresa tu API_HASH: ")
    except ValueError:
        print("\n❌ Error: El API_ID debe ser un número entero.")
        return

    # Usamos una sesión en memoria para no crear archivos
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        # El cliente se conectará y te pedirá tu número, código y contraseña 2FA si la tienes.
        # Esto sucede de forma interactiva en la terminal.
        
        session_string = client.session.save()
        
        print("\n✅ ¡Sesión generada con éxito!")
        print("--------------------------------------------")
        print("Copia la siguiente línea completa. Esta es tu SESSION_STRING:")
        print("\n" + session_string + "\n")
        print("⚠️  Guarda esta string de forma segura. Quien la tenga puede acceder a tu cuenta.")
        print("     Añádela a tus variables de entorno en tu servidor (Railway, etc.).")

if __name__ == "__main__":
    # En Windows, puede que necesites esta línea si hay problemas con el event loop
    # asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())