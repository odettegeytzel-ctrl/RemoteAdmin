"""
Pruebas de la configuración de límites del bloque F2.

Comprueba que REMOTEADMIN_LOCAL_STORAGE_MB y REMOTEADMIN_MIN_FREE_MB cambian
de verdad el comportamiento, que los valores por defecto son los acordados
(5 GB y 2 GB) y que un valor inválido nunca deja el Agent sin protección.

Uso, desde la raíz del proyecto:

    python tests/test_f2_config.py
"""

import importlib
import os
import shutil
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "agent"))

import storage  # noqa: E402


MB = 1024 * 1024

resultados = []


def comprobar(descripcion, condicion):
    resultados.append((descripcion, bool(condicion)))
    print(f"  [{'OK  ' if condicion else 'FALLA'}] {descripcion}")


def recargar_con(entorno):
    """Recarga storage.py con unas variables de entorno concretas."""

    for clave in ("REMOTEADMIN_LOCAL_STORAGE_MB", "REMOTEADMIN_MIN_FREE_MB"):
        os.environ.pop(clave, None)

    os.environ.update(entorno)

    importlib.reload(storage)

    return storage


def main():

    previo = {
        clave: os.environ.get(clave)
        for clave in ("REMOTEADMIN_LOCAL_STORAGE_MB", "REMOTEADMIN_MIN_FREE_MB")
    }

    temporal = tempfile.mkdtemp(prefix="remoteadmin_f2cfg_")
    grabaciones = os.path.join(temporal, "recordings")
    os.makedirs(grabaciones, exist_ok=True)

    try:

        print("\n=== 1. Valores por defecto (sin variables) ===")
        s = recargar_con({})
        comprobar("almacenamiento local = 5 GB",
                  s.STORAGE_LIMIT_BYTES == 5120 * MB)
        comprobar("mínimo libre = 2 GB",
                  s.MIN_FREE_BYTES == 2048 * MB)
        print(f"        {s.STORAGE_LIMIT_BYTES // MB} MB de tope, "
              f"{s.MIN_FREE_BYTES // MB} MB libres mínimos")

        print("\n=== 2. REMOTEADMIN_LOCAL_STORAGE_MB cambia el tope ===")
        s = recargar_con({"REMOTEADMIN_LOCAL_STORAGE_MB": "100"})
        comprobar("el tope pasa a 100 MB", s.STORAGE_LIMIT_BYTES == 100 * MB)
        comprobar("el mínimo libre conserva su valor por defecto",
                  s.MIN_FREE_BYTES == 2048 * MB)

        print("\n=== 3. ...y cambia el comportamiento, no solo el número ===")
        s.get_recordings_dir = lambda: grabaciones
        s.STORAGE_LIMIT_BYTES = 4096          # tope diminuto para la prueba
        s.MIN_FREE_BYTES = 1                  # el disco libre no interfiere

        comprobar("con la carpeta vacía se puede grabar",
                  s.can_start_new_segment())

        with open(os.path.join(grabaciones, "rec_1.mp4"), "wb") as handle:
            handle.write(b"x" * 5000)

        comprobar("superado el tope, se bloquea",
                  not s.can_start_new_segment())

        s.STORAGE_LIMIT_BYTES = 1024 * 1024   # se sube el tope
        comprobar("subiendo el tope, se vuelve a permitir",
                  s.can_start_new_segment())

        print("\n=== 4. REMOTEADMIN_MIN_FREE_MB cambia el mínimo libre ===")
        s = recargar_con({"REMOTEADMIN_MIN_FREE_MB": "50"})
        comprobar("el mínimo libre pasa a 50 MB", s.MIN_FREE_BYTES == 50 * MB)
        comprobar("el tope conserva su valor por defecto",
                  s.STORAGE_LIMIT_BYTES == 5120 * MB)

        print("\n=== 5. ...y también cambia el comportamiento ===")
        s.get_recordings_dir = lambda: grabaciones
        libre_real = s.free_space_bytes()
        print(f"        espacio libre real del disco: {libre_real // MB} MB")

        s.MIN_FREE_BYTES = 1
        comprobar("exigiendo casi nada libre: se puede grabar",
                  s.can_start_new_segment())

        s.MIN_FREE_BYTES = libre_real + 100 * MB
        comprobar("exigiendo más de lo que hay: se bloquea",
                  not s.can_start_new_segment())
        comprobar("y el motivo menciona el espacio libre",
                  "libres" in (s.storage_blocked_reason() or ""))

        print("\n=== 6. Las dos variables a la vez ===")
        s = recargar_con({
            "REMOTEADMIN_LOCAL_STORAGE_MB": "256",
            "REMOTEADMIN_MIN_FREE_MB": "128"
        })
        comprobar("tope = 256 MB", s.STORAGE_LIMIT_BYTES == 256 * MB)
        comprobar("mínimo libre = 128 MB", s.MIN_FREE_BYTES == 128 * MB)

        print("\n=== 7. Valores inválidos: nunca dejan sin protección ===")

        casos = [
            ("texto", "abc"),
            ("vacío", ""),
            ("negativo", "-500"),
            ("cero", "0"),
            ("espacios", "   "),
            ("con unidad", "5GB"),
            ("desbordado", "99999999999999999999999999")
        ]

        for etiqueta, valor in casos:

            s = recargar_con({"REMOTEADMIN_LOCAL_STORAGE_MB": valor})

            # El único que debe aceptarse es el desbordado, que sí es un
            # número; el resto tienen que caer al valor por defecto.
            if etiqueta == "desbordado":
                seguro = s.STORAGE_LIMIT_BYTES > 0
            else:
                seguro = s.STORAGE_LIMIT_BYTES == 5120 * MB

            comprobar(
                f"{etiqueta:12} -> límite seguro "
                f"({s.STORAGE_LIMIT_BYTES // MB} MB)",
                seguro
            )

        print("\n=== 8. Un decimal se acepta y se trunca ===")
        s = recargar_con({"REMOTEADMIN_LOCAL_STORAGE_MB": "512.9"})
        comprobar("512.9 -> 512 MB", s.STORAGE_LIMIT_BYTES == 512 * MB)

        print("\n=== 9. Tras un valor inválido el mecanismo sigue funcionando ===")
        s = recargar_con({"REMOTEADMIN_LOCAL_STORAGE_MB": "no-es-un-numero"})
        s.get_recordings_dir = lambda: grabaciones
        comprobar("can_start_new_segment responde sin fallar",
                  isinstance(s.can_start_new_segment(), bool))
        comprobar("y el borrado seguro sigue rechazando lo ajeno",
                  s.delete_recording_file(
                      os.path.join(temporal, "algo.txt")
                  ) is False)

    finally:

        shutil.rmtree(temporal, ignore_errors=True)

        for clave, valor in previo.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor

        importlib.reload(storage)

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
