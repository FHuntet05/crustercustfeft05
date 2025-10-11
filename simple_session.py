import asyncio
from pyrogram import Client

async def main():
    print("\n=== Generador de Session String Simple ===")
    print("Por favor, introduce tus credenciales:")
    
    api_id = input("API ID: ").strip()
    api_hash = input("API Hash: ").strip()
    
    async with Client(
        name="my_account",
        api_id=api_id,
        api_hash=api_hash,
        in_memory=True
    ) as app:
        print("\nGenerando session string...")
        session_string = await app.export_session_string()
        print("\n¡Session string generada exitosamente!")
        print("\nTu session string es:")
        print(f"\n{session_string}\n")
        
        with open("session_string.txt", "w") as f:
            f.write(session_string)
        print("La session string también se ha guardado en 'session_string.txt'")

if __name__ == "__main__":
    asyncio.run(main())