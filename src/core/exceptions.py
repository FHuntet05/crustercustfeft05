# --- START OF FILE src/core/exceptions.py (CORREGIDO Y COMPLETO) ---

class BaseBotError(Exception):
    """Clase base para todas las excepciones personalizadas del bot."""
    def __init__(self, message="Ocurrió un error en el bot."):
        self.message = message
        super().__init__(self.message)

class FfmpegError(BaseBotError):
    """Excepción para errores ocurridos durante el procesamiento con FFmpeg."""
    def __init__(self, message="Error en FFmpeg."):
        super().__init__(message)

class NetworkError(BaseBotError):
    """Excepción para errores de red, como fallos de descarga."""
    def __init__(self, message="Error de red."):
        super().__init__(message)

class AuthenticationError(BaseBotError):
    """Excepción para problemas de autenticación o acceso (ej. cookies de YouTube)."""
    def __init__(self, service_name: str, details: str):
        message = f"Error de autenticación con {service_name}: {details}"
        super().__init__(message)

class DatabaseError(BaseBotError):
    """Excepción para errores relacionados con la interacción con la base de datos."""
    def __init__(self, message="Error en la base de datos."):
        super().__init__(message)

# --- END OF FILE src/core/exceptions.py ---