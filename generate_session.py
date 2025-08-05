# generate_session.py
import asyncio
from pyrogram import Client

async def main():
    print("--- Generador de Session String de Pyrogram ---")
    print("Necesitará su API_ID y API_HASH de my.telegram.org")
    
    try:
        api_id = int(input("Por favor, introduzca su API_ID: "))
        api_hash = input("Por favor, introduzca su API_HASH: ")
    except ValueError:
        print("\nERROR: API_ID debe ser un número entero. Por favor, reinicie el script.")
        return

    async with Client(':memory:', api_id=api_id, api_hash=api_hash) as app:
        print("\nEl cliente de Telegram se iniciará ahora.")
        print("Se le pedirá su número, código y contraseña 2FA si la tiene.")
        
        session_string = await app.export_session_string()
        
        with open("session_pyrogram.txt", "w") as f:
            f.write(session_string)
        print("\n\n--- ¡ÉXITO! ---")
        print("Su Session String HA SIDO GUARDADA en el archivo 'session_pyrogram.txt'.")
        print("Esto evita errores al copiar. Use el contenido de ese archivo.")

if __name__ == "__main__":
    asyncio.run(main())