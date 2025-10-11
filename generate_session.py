# --- START OF FILE generate_session.py (VERSIÓN DE EMERGENCIA) ---

import asyncio
import os

# Crear un bucle de eventos ANTES de importar Pyrogram para evitar el RuntimeError
try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# Ahora importar Pyrogram de forma segura
try:
    from pyrogram import Client
    from pyrogram.errors import (
        ApiIdInvalid, PhoneNumberInvalid, PhoneCodeInvalid,
        PhoneCodeExpired, SessionPasswordNeeded, PasswordHashInvalid
    )
except ImportError:
    print("\n[ERROR] Pyrogram no está instalado. Por favor, ejecute: pip install pyrogram")
    exit(1)


def clear_console():
    """Limpia la consola para una mejor legibilidad."""
    os.system('cls' if os.name == 'nt' else 'clear')


async def main():
    """Flujo principal 100% manual para generar la session string."""
    clear_console()
    print("=" * 50)
    print("🚀 Generador de Session String de Pyrogram (Modo Manual) 🚀")
    print("=" * 50)
    print("\nℹ️ Este script te pedirá todos los datos necesarios.")
    print("   Puedes obtener tu API_ID y API_HASH en https://my.telegram.org/apps")

    # --- Pide todo manualmente ---
    try:
        api_id = int(input("\n🔑 Introduce tu API_ID: ").strip())
        api_hash = input("🔑 Introduce tu API_HASH: ").strip()
    except (ValueError, KeyboardInterrupt, EOFError):
        print("\n\n🚫 Proceso cancelado o API_ID inválido.")
        return

    # --- CAMBIO CRUCIAL ---
    # En lugar de ":memory:", le damos un nombre de archivo simple y explícito.
    # El script creará un archivo llamado "temp_session.session" en esta carpeta.
    session_name = "temp_session"
    async with Client(session_name, api_id=api_id, api_hash=api_hash) as app:
        print("\n✅ Cliente listo. Ahora se pedirá tu información de sesión.")
        
        try:
            phone_number = input("📱 Introduce tu número de teléfono (formato internacional, ej: +1234567890): ").strip()
            sent_code_info = await app.send_code(phone_number)
            
            code = input(f"✉️  Introduce el código de verificación que recibiste ({sent_code_info.type.name}): ").strip()
            
            try:
                await app.sign_in(phone_number, sent_code_info.phone_code_hash, code)
            except SessionPasswordNeeded:
                password = input("🔒 Introduce tu contraseña de doble factor (2FA): ").strip()
                await app.check_password(password)

            session_string = await app.export_session_string()
            
            clear_console()
            print("=" * 50)
            print("🎉 ¡SESIÓN GENERADA CON ÉXITO! 🎉")
            print("=" * 50)
            print("\nCopia la siguiente línea y pégala en tu archivo .env del servidor:")
            print("-" * 50)
            print(f"USERBOT_SESSION_STRING={session_string}")
            print("-" * 50)
            print("\n⚠️  IMPORTANTE: ¡TRATA ESTA SESSION STRING COMO UNA CONTRASEÑA! ⚠️")

        except (ApiIdInvalid): print("\n❌ API_ID o API_HASH inválidos.")
        except (PhoneNumberInvalid): print("\n❌ El formato del número de teléfono es incorrecto.")
        except (PhoneCodeInvalid, PhoneCodeExpired): print("\n❌ El código de verificación es incorrecto o ha expirado.")
        except PasswordHashInvalid: print("\n❌ Contraseña de doble factor incorrecta.")
        except (KeyboardInterrupt, EOFError): print("\n\n🚫 Proceso cancelado por el usuario.")
        except Exception as e: print(f"\n❌ Ocurrió un error inesperado: {e}")

if __name__ == "__main__":
    try:
        loop.run_until_complete(main())
    except (KeyboardInterrupt, EOFError):
        print("\n\n🚫 Proceso cancelado.")
    finally:
        print("\n👋 El programa ha finalizado.")
        # Limpieza: eliminamos el archivo de sesión temporal que se creó.
        if os.path.exists("temp_session.session"):
            os.remove("temp_session.session")
        if os.path.exists("temp_session.session-journal"):
            os.remove("temp_session.session-journal")

# --- END OF FILE generate_session.py ---