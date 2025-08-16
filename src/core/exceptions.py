# --- START OF FILE src/core/exceptions.py ---

class BaseBotException(Exception):
    """Clase base para todas las excepciones personalizadas del bot."""
    def __init__(self, message="Ocurrió un error inesperado en una operación del bot."):
        self.message = message
        super().__init__(self.message)

class NetworkError(BaseBotException):
    """Lanzada cuando hay un error de red, como un fallo de descarga o de API."""
    def __init__(self, message="Error de red. No se pudo completar la operación. El enlace puede estar roto o ser privado."):
        super().__init__(message)

class DiskSpaceError(BaseBotException):
    """Lanzada por ResourceManager cuando no hay suficiente espacio en disco."""
    def __init__(self, message="Espacio en disco insuficiente para procesar la tarea."):
        super().__init__(message)

class InvalidMediaError(BaseBotException):
    """Lanzada cuando un archivo de medios está corrupto o no es compatible."""
    def __init__(self, message="El archivo proporcionado está corrupto o no es un formato de medios compatible."):
        super().__init__(message)

class FFmpegProcessingError(BaseBotException):
    """Lanzada cuando un proceso de FFmpeg falla."""
    def __init__(self, message="Error irrecuperable durante el procesamiento con FFmpeg.", log: str = ""):
        self.log = log
        full_message = f"{message}\n\n--- Log de FFmpeg ---\n{log}"
        super().__init__(full_message)

class AuthenticationError(BaseBotException):
    """Lanzada cuando falla la autenticación con un servicio externo (ej. cookies de YouTube)."""
    def __init__(self, service_name: str, message: str = "Fallo de autenticación."):
        self.service_name = service_name
        full_message = f"Error de autenticación con {service_name}: {message}"
        super().__init__(full_message)