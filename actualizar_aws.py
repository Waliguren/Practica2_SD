import os
import shutil
from pathlib import Path

def actualizar_credenciales():
    # 1. Definir los archivos usando Path (se adapta a Windows y Linux automáticamente)
    archivo_local = Path("aws_keys.txt")
    carpeta_aws = Path.home() / ".aws"
    destino_credenciales = carpeta_aws / "credentials"

    # 2. Comprobar si has creado el archivo con las claves
    if not archivo_local.exists():
        print("==========================================")
        print("❌ ERROR: No encuentro el archivo 'aws_keys.txt'.")
        print("Crea el archivo en esta carpeta, pega las claves de AWS Academy y vuelve a ejecutar.")
        print("==========================================")
        return

    try:
        # 3. Crear la carpeta .aws en tu perfil de usuario si no existe
        carpeta_aws.mkdir(parents=True, exist_ok=True)
        
        # 4. Copiar el archivo y sobreescribir las credenciales viejas
        shutil.copyfile(archivo_local, destino_credenciales)
        
        print("==========================================")
        print("✅ ¡Credenciales de AWS inyectadas con éxito!")
        print(f"📂 Guardadas en: {destino_credenciales}")
        print("🚀 Terraform ya tiene luz verde para hacer el apply.")
        print("==========================================")
        
    except Exception as e:
        print(f"❌ Error inesperado al copiar las claves: {e}")

if __name__ == "__main__":
    actualizar_credenciales()