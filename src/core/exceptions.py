class BaseBotException(Exception):
    """Clase base para todas las excepciones personalizadas del bot."""
    def __init__(self, message="Ocurrió un error inesperado en una operación del bot."):
        self.message = message
        super().__init__(self.message)

class NetworkError(BaseBotException):
    """
    Lanzada cuando hay un error de red transitorio, como un fallo de descarga o de API.
    Indica que la operación podría reintentarse.
    """
    def __init__(self, message="Error de red. No se pudo completar la operación. Por favor, inténtelo de nuevo más tarde."):
        super().__init__(message)

class DiskSpaceError(BaseBotException):
    """
    Lanzada por el ResourceManager cuando no hay suficiente espacio en disco
    para continuar con una operación que requiere almacenamiento.
    """
    def __init__(self, message="Espacio en disco insuficiente para procesar la tarea."):
        super().__init__(message)

class InvalidMediaError(BaseBotException):
    """
    Lanzada cuando un archivo de medios proporcionado está corrupto, no es compatible
    o no puede ser procesado por FFmpeg/ffprobe.
    """
    def __init__(self, message="El archivo proporcionado está corrupto o no es un formato de medios compatible."):
        super().__init__(message)

class FFmpegProcessingError(BaseBotException):
    """
    Lanzada cuando un proceso de FFmpeg falla con un código de salida distinto de cero.
    Contiene el log de errores de FFmpeg para depuración.
    """
    def __init__(self, message="Error irrecuperable durante el procesamiento con FFmpeg.", log: str = ""):
        self.log = log
        # Para el usuario, mostramos un mensaje limpio. El log completo se guarda en la DB.
        full_message = f"{message}\n\n--- Log de FFmpeg ---\n{log}"
        super().__init__(full_message)

class AuthenticationError(BaseBotException):
    """
    Lanzada cuando falla la autenticación con un servicio externo,
    principalmente debido a cookies de YouTube inválidas o caducadas.
    """
    def __init__(self, service_name: str, message: str = "Fallo de autenticación."):
        self.service_name = service_name
        full_message = f"Error de autenticación con {service_name}: {message}"
        super().__init__(full_message)