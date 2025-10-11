from urllib.parse import quote_plus
import os

# Leer la URL actual de MongoDB
mongo_uri = os.getenv('MONGO_URI', '')

if mongo_uri:
    # Separar la URL en sus componentes
    if '@' in mongo_uri:
        auth_part = mongo_uri.split('@')[0]
        rest_part = mongo_uri.split('@')[1]
        
        # Separar usuario y contraseña
        if ':' in auth_part:
            username = auth_part.split(':')[0].replace('mongodb://', '')
            password = auth_part.split(':')[1]
            
            # Codificar usuario y contraseña
            encoded_username = quote_plus(username)
            encoded_password = quote_plus(password)
            
            # Reconstruir la URL
            new_mongo_uri = f"mongodb://{encoded_username}:{encoded_password}@{rest_part}"
            print(f"Nueva URL de MongoDB (codificada correctamente):")
            print(new_mongo_uri)
        else:
            print("No se encontró el separador ':' en la parte de autenticación")
    else:
        print("No se encontró el separador '@' en la URL")
else:
    print("MONGO_URI no está definida")