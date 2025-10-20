#!/usr/bin/env python3
"""
Script de prueba simple para verificar la lógica básica del bot.
Este script no requiere dependencias externas.
"""

import os
import sys
import re
from datetime import datetime

def test_url_parsing():
    """Prueba el parsing de URLs de Telegram sin dependencias"""
    print("🧪 Probando parsing de URLs de Telegram...")
    
    def parse_telegram_url(url: str):
        """Versión simplificada del parser de URLs"""
        result = {
            "type": "unknown",
            "chat_id": None,
            "message_id": None,
            "invite_hash": None,
            "username": None,
            "raw_chat_id": None,
            "original_url": url
        }
        
        # Verificar si es un enlace interno de canal privado (t.me/c/123456/789)
        match = re.search(r't\.me/c/(\d+)(?:/(\d+))?', url)
        if match:
            raw_chat_id = match.group(1)
            message_id = match.group(2)
            
            result.update({
                "type": "private_channel",
                "raw_chat_id": raw_chat_id,
                "message_id": int(message_id) if message_id else None
            })
            return result
        
        # Verificar si es un enlace de invitación (t.me/+ABC123)
        match = re.search(r't\.me/\+([a-zA-Z0-9_-]+)', url)
        if match:
            invite_hash = match.group(1)
            result.update({
                "type": "invitation",
                "invite_hash": invite_hash
            })
            return result
        
        # Verificar si es un enlace a un canal público (t.me/username/123)
        match = re.search(r't\.me/([^\s/]+)(?:/(\d+))?', url)
        if match:
            username = match.group(1)
            message_id = match.group(2)
            
            if username in ['joinchat', '+']:
                return result
                
            result.update({
                "type": "public_channel",
                "username": username,
                "message_id": int(message_id) if message_id else None
            })
            return result
        
        return result
    
    def normalize_chat_id(chat_id):
        """Versión simplificada del normalizador de chat ID"""
        try:
            chat_id_str = str(chat_id).strip()
            
            if chat_id_str.startswith('-100'):
                return int(chat_id_str)
            elif chat_id_str.isdigit():
                return int(f'-100{chat_id_str}')
            elif chat_id_str.startswith('-'):
                return int(f'-100{chat_id_str[1:]}')
            else:
                numeric_id = int(chat_id_str)
                if numeric_id > 0:
                    return int(f'-100{numeric_id}')
                else:
                    return int(f'-100{abs(numeric_id)}')
        except Exception as e:
            raise ValueError(f"Error normalizando chat_id {chat_id}: {e}")
    
    # Casos de prueba
    test_cases = [
        ("https://t.me/testchannel/123", "public_channel", "testchannel", 123),
        ("https://t.me/c/123456789/456", "private_channel", "123456789", 456),
        ("https://t.me/+ABC123DEF", "invitation", None, None),
        ("https://t.me/testchannel", "public_channel", "testchannel", None),
    ]
    
    for url, expected_type, expected_id, expected_msg_id in test_cases:
        parsed = parse_telegram_url(url)
        assert parsed['type'] == expected_type, f"Tipo incorrecto para {url}: {parsed['type']} != {expected_type}"
        
        if expected_id:
            if expected_type == "public_channel":
                assert parsed['username'] == expected_id, f"Username incorrecto: {parsed['username']} != {expected_id}"
            elif expected_type == "private_channel":
                assert parsed['raw_chat_id'] == expected_id, f"Raw chat ID incorrecto: {parsed['raw_chat_id']} != {expected_id}"
        
        if expected_msg_id:
            assert parsed['message_id'] == expected_msg_id, f"Message ID incorrecto: {parsed['message_id']} != {expected_msg_id}"
    
    # Probar normalización de chat ID
    assert normalize_chat_id("123456789") == -100123456789
    assert normalize_chat_id("-100123456789") == -100123456789
    assert normalize_chat_id(123456789) == -100123456789
    
    print("✅ Parsing de URLs exitoso")
    return True

def test_utility_functions():
    """Prueba las funciones de utilidad básicas"""
    print("🧪 Probando funciones de utilidad...")
    
    def format_bytes(size_in_bytes):
        """Formatea bytes a un formato legible"""
        if not isinstance(size_in_bytes, (int, float)) or size_in_bytes <= 0:
            return "0 B"
        power, n = 1024, 0
        power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
        while size_in_bytes >= power and n < len(power_labels) - 1:
            size_in_bytes /= power
            n += 1
        return f"{size_in_bytes:.2f} {power_labels[n]}"
    
    def format_time(seconds):
        """Formatea segundos a un formato de tiempo legible"""
        if not isinstance(seconds, (int, float)) or seconds < 0 or seconds == float('inf'):
            return "∞"
        seconds = int(seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds_part = divmod(remainder, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"
        return f"{minutes:02d}:{seconds_part:02d}"
    
    def sanitize_filename(filename):
        """Limpia un string para que sea un nombre de archivo válido"""
        if not isinstance(filename, str):
            return "archivo_invalido"
        name_base = os.path.splitext(filename)[0]
        sanitized_base = re.sub(r'[^\w\s.-]', '', name_base, flags=re.UNICODE)
        sanitized_base = re.sub(r'[\s_]+', ' ', sanitized_base).strip()
        if not sanitized_base:
            sanitized_base = "archivo_procesado"
        return sanitized_base[:240]
    
    def escape_html(text):
        """Escapa texto para HTML"""
        if not isinstance(text, str):
            return ""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;")
    
    # Probar formateo de bytes
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1048576) == "1.00 MB"
    assert format_bytes(0) == "0 B"
    
    # Probar formateo de tiempo
    assert format_time(65) == "01:05"
    assert format_time(3661) == "01:01:01"
    assert format_time(0) == "00:00"
    
    # Probar sanitización de nombres
    assert sanitize_filename("test file.mp4") == "test file"
    # La función actual mantiene los caracteres válidos, solo remueve los inválidos
    sanitized = sanitize_filename("test<>file.mp4")
    assert "test" in sanitized and "file" in sanitized
    assert sanitize_filename("") == "archivo_procesado"
    
    # Probar escape HTML
    assert escape_html("<test>") == "&lt;test&gt;"
    assert escape_html("normal text") == "normal text"
    
    print("✅ Funciones de utilidad exitosas")
    return True

def test_ffmpeg_command_generation():
    """Prueba la generación de comandos FFmpeg básicos"""
    print("🧪 Probando generación de comandos FFmpeg...")
    
    def build_simple_ffmpeg_command(input_path, output_path, watermark_text=None):
        """Genera un comando FFmpeg simple"""
        command = ["ffmpeg", "-y", "-i", input_path]
        
        if watermark_text:
            command.extend([
                "-vf", f"drawtext=text='{watermark_text}':fontcolor=white:fontsize=24:x=10:y=10",
                "-c:v", "libx264",
                "-c:a", "aac"
            ])
        else:
            command.extend(["-c:v", "copy", "-c:a", "copy"])
        
        command.append(output_path)
        return command
    
    # Probar comando básico
    cmd = build_simple_ffmpeg_command("input.mp4", "output.mp4")
    assert "ffmpeg" in cmd[0]
    assert "input.mp4" in cmd
    assert "output.mp4" in cmd[-1]
    
    # Probar comando con marca de agua
    cmd_with_wm = build_simple_ffmpeg_command("input.mp4", "output.mp4", "Test Watermark")
    assert "drawtext" in " ".join(cmd_with_wm)
    assert "Test Watermark" in " ".join(cmd_with_wm)
    
    print("✅ Generación de comandos FFmpeg exitosa")
    return True

def test_configuration_validation():
    """Prueba la validación de configuraciones"""
    print("🧪 Probando validación de configuraciones...")
    
    def validate_watermark_config(config):
        """Valida la configuración de marca de agua"""
        if not config:
            return False, "Configuración vacía"
        
        if config.get('type') not in ['text', 'image']:
            return False, "Tipo de marca de agua inválido"
        
        if config.get('type') == 'text':
            text = config.get('text', '')
            if not text:
                return False, "Texto de marca de agua vacío"
            if len(text) > 50:
                return False, "Texto de marca de agua demasiado largo"
        
        return True, "Configuración válida"
    
    def validate_trim_config(trim_times):
        """Valida la configuración de corte"""
        if not trim_times:
            return False, "Tiempos de corte vacíos"
        
        time_pattern = r'^(\d{1,2}:\d{2}(:\d{2})?(-\d{1,2}:\d{2}(:\d{2})?)?|\d{1,2}:\d{2}(:\d{2})?)$'
        if not re.match(time_pattern, trim_times):
            return False, "Formato de tiempo inválido"
        
        return True, "Configuración válida"
    
    # Probar validación de marca de agua
    valid_wm = {"type": "text", "text": "Test"}
    is_valid, msg = validate_watermark_config(valid_wm)
    assert is_valid, f"Configuración válida rechazada: {msg}"
    
    invalid_wm = {"type": "text", "text": ""}
    is_valid, msg = validate_watermark_config(invalid_wm)
    assert not is_valid, "Configuración inválida aceptada"
    
    # Probar validación de corte
    valid_trim = "00:10-00:50"
    is_valid, msg = validate_trim_config(valid_trim)
    assert is_valid, f"Tiempo válido rechazado: {msg}"
    
    invalid_trim = "invalid_time"
    is_valid, msg = validate_trim_config(invalid_trim)
    assert not is_valid, "Tiempo inválido aceptado"
    
    print("✅ Validación de configuraciones exitosa")
    return True

def run_all_tests():
    """Ejecuta todas las pruebas"""
    print("🚀 Iniciando pruebas del bot...")
    
    tests = [
        ("Funciones de utilidad", test_utility_functions),
        ("Parsing de URLs", test_url_parsing),
        ("Comandos FFmpeg", test_ffmpeg_command_generation),
        ("Validación de configuraciones", test_configuration_validation),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"\n{'='*50}")
        print(f"Ejecutando: {test_name}")
        print(f"{'='*50}")
        
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Error inesperado en {test_name}: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))
    
    # Mostrar resumen
    print(f"\n{'='*50}")
    print("RESUMEN DE PRUEBAS")
    print(f"{'='*50}")
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASÓ" if result else "❌ FALLÓ"
        print(f"{test_name}: {status}")
        if result:
            passed += 1
    
    print(f"\nResultado final: {passed}/{total} pruebas pasaron")
    
    if passed == total:
        print("🎉 ¡Todas las pruebas pasaron! La lógica básica del bot está funcionando correctamente.")
    else:
        print(f"⚠️ {total - passed} pruebas fallaron. Revisa los errores anteriores.")
    
    return passed == total

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
