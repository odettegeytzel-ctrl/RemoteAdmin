"""
Pruebas del bloque F2: limpieza y protección del almacenamiento local.

Se ejecutan con archivos REALES en una carpeta temporal, nunca sobre
C:\\ProgramData\\RemoteAdmin ni sobre las grabaciones del equipo.

Uso, desde la raíz del proyecto:

    python tests/test_f2_local_storage.py

No requiere pytest ni dependencias nuevas: el Agent importa wmi, pyautogui y
mss, que no se pueden cargar en cualquier entorno, así que estas pruebas
atacan agent/storage.py, que está aislado a propósito.
"""

import os
import shutil
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "agent"))

import storage  # noqa: E402


resultados = []


def comprobar(descripcion, condicion):

    resultados.append((descripcion, bool(condicion)))

    print(f"  [{'OK  ' if condicion else 'FALLA'}] {descripcion}")


def crear_mp4(carpeta, nombre, bytes_=1024):
    """Crea un archivo real con contenido, no un marcador vacío."""

    os.makedirs(carpeta, exist_ok=True)

    ruta = os.path.join(carpeta, nombre)

    with open(ruta, "wb") as handle:
        handle.write(b"\x00\x00\x00\x18ftypmp42" + b"x" * (bytes_ - 12))

    return ruta


def main():

    temporal = tempfile.mkdtemp(prefix="remoteadmin_f2_")

    grabaciones = os.path.join(temporal, "recordings")
    os.makedirs(grabaciones, exist_ok=True)

    # storage.py pregunta la carpeta a paths.get_recordings_dir; se redirige
    # a la temporal para no tocar las grabaciones reales
    storage.get_recordings_dir = lambda: grabaciones

    try:

        print("\n=== 1. Subida correcta -> el archivo local se elimina ===")
        subido = crear_mp4(grabaciones, "rec_1000000001.mp4")
        comprobar("el archivo existe antes", os.path.exists(subido))
        borrado = storage.delete_recording_file(subido)
        comprobar("delete_recording_file devuelve True", borrado)
        comprobar("el archivo ya no está", not os.path.exists(subido))

        print("\n=== 2. Subida fallida -> el archivo se conserva ===")
        # Una subida fallida sencillamente NO llama al borrado. Se comprueba
        # que nada más lo elimine por su cuenta.
        pendiente = crear_mp4(grabaciones, "rec_1000000002.mp4")
        storage.recordings_size_bytes()
        storage.can_start_new_segment()
        comprobar("el archivo pendiente sigue ahí", os.path.exists(pendiente))

        print("\n=== 3. Reintento correcto -> se elimina tras confirmar ===")
        storage.delete_recording_file(pendiente)
        comprobar("eliminado tras el reintento", not os.path.exists(pendiente))

        print("\n=== 4. Reinicio con subida pendiente -> el archivo sigue ===")
        # El reinicio no borra nada por sí mismo: solo se borra en el camino
        # de confirmación. Se simula recargando el módulo.
        tras_reinicio = crear_mp4(grabaciones, "rec_1000000003.mp4")
        import importlib
        importlib.reload(storage)
        storage.get_recordings_dir = lambda: grabaciones
        comprobar("sobrevive al reinicio del proceso", os.path.exists(tras_reinicio))

        print("\n=== 5. Archivo NO gestionado -> nunca se borra ===")
        fuera = os.path.join(temporal, "documento_importante.txt")
        with open(fuera, "w", encoding="utf-8") as handle:
            handle.write("no tocar")

        comprobar(
            "fuera de la carpeta de grabaciones: rechazado",
            storage.delete_recording_file(fuera) is False
        )
        comprobar("el archivo externo sigue intacto", os.path.exists(fuera))

        ajeno = crear_mp4(grabaciones, "vacaciones.mp4")
        comprobar(
            "nombre que no sigue el patrón: rechazado",
            storage.delete_recording_file(ajeno) is False
        )
        comprobar("el MP4 ajeno sigue intacto", os.path.exists(ajeno))

        travesia = os.path.join(grabaciones, "..", "documento_importante.txt")
        comprobar(
            "ruta con '..' que se sale: rechazada",
            storage.delete_recording_file(travesia) is False
        )
        comprobar("y el archivo apuntado sigue ahí", os.path.exists(fuera))

        print("\n=== 6. Un archivo protegido no se elimina ===")
        en_curso = crear_mp4(grabaciones, "rec_1000000004.mp4")
        comprobar(
            "marcado como en uso: no se borra",
            storage.delete_recording_file(
                en_curso,
                is_protected=lambda ruta: True
            ) is False
        )
        comprobar("la grabación en curso sigue ahí", os.path.exists(en_curso))
        comprobar(
            "sin protección sí se borra",
            storage.delete_recording_file(en_curso, is_protected=lambda r: False)
        )

        print("\n=== 7. Límite de almacenamiento -> se bloquea ===")
        limite_original = storage.STORAGE_LIMIT_BYTES
        storage.STORAGE_LIMIT_BYTES = 4096

        comprobar("con la carpeta casi vacía se puede grabar",
                  storage.can_start_new_segment())

        crear_mp4(grabaciones, "rec_1000000005.mp4", 5000)

        comprobar("superado el límite: NO se puede grabar",
                  not storage.can_start_new_segment())

        motivo = storage.storage_blocked_reason()
        comprobar("el motivo explica el límite con cifras",
                  motivo is not None and "límite" in motivo)
        print(f"        motivo: {motivo}")

        print("\n=== 8. Recuperación al liberar espacio ===")
        os.remove(os.path.join(grabaciones, "rec_1000000005.mp4"))
        comprobar("liberado el espacio, se puede grabar de nuevo",
                  storage.can_start_new_segment())
        comprobar("y ya no hay motivo de bloqueo",
                  storage.storage_blocked_reason() is None)
        storage.STORAGE_LIMIT_BYTES = limite_original

        print("\n=== 9. Poco espacio libre en disco -> se bloquea ===")
        libre_original = storage.MIN_FREE_BYTES
        # Se exige más espacio libre del que puede haber en cualquier disco
        storage.MIN_FREE_BYTES = 10 ** 18

        comprobar("sin espacio libre suficiente: NO se puede grabar",
                  not storage.can_start_new_segment())

        motivo = storage.storage_blocked_reason()
        comprobar("el motivo habla del espacio libre",
                  motivo is not None and "libres" in motivo)
        print(f"        motivo: {motivo}")

        storage.MIN_FREE_BYTES = libre_original
        comprobar("restaurado el mínimo, se puede grabar",
                  storage.can_start_new_segment())

        print("\n=== 10. El recuento de tamaño es real ===")
        for f in os.listdir(grabaciones):
            os.remove(os.path.join(grabaciones, f))

        comprobar("carpeta vacía -> 0 bytes", storage.recordings_size_bytes() == 0)
        crear_mp4(grabaciones, "rec_1000000006.mp4", 3000)
        crear_mp4(os.path.join(grabaciones, "2026", "09", "26"),
                  "rec_1000000007.mp4", 2000)
        comprobar("suma también las subcarpetas por fecha",
                  storage.recordings_size_bytes() == 5000)

        print("\n=== 11. Un archivo ya borrado no es un error ===")
        inexistente = os.path.join(grabaciones, "rec_1000000099.mp4")
        comprobar("borrar algo que ya no está devuelve True",
                  storage.delete_recording_file(inexistente))

    finally:
        shutil.rmtree(temporal, ignore_errors=True)

    fallos = [d for d, ok in resultados if not ok]

    print("\n" + "=" * 60)
    print(f"  {len(resultados) - len(fallos)} de {len(resultados)} comprobaciones correctas")

    if fallos:
        print("  FALLOS:")
        for f in fallos:
            print("   -", f)

    print("=" * 60)

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
