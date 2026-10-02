"""
Pruebas de la clasificación de errores de subida del Agent (bloque F3).

Antes, cualquier fallo se trataba como reintentable: un 401 o un 413 se
reintentaban cada 30 segundos para siempre. Ahora el Agent distingue qué
merece otro intento y qué no, pero en NINGÚN caso borra el archivo local:
perder una grabación es peor que ocupar disco.

Uso, desde la raíz del proyecto:

    python tests/test_f3_agent_errors.py
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "agent"))

resultados = []


def comprobar(descripcion, condicion):
    resultados.append((descripcion, bool(condicion)))
    print(f"  [{'OK  ' if condicion else 'FALLA'}] {descripcion}")


def cargar_clasificador():
    """
    Extrae is_retryable_status de agent.py sin importar el módulo entero:
    agent.py carga wmi, pyautogui y mss, que no están en cualquier entorno.
    """

    ruta = os.path.join(RAIZ, "agent", "agent.py")

    with open(ruta, encoding="utf-8") as handle:
        codigo = handle.read()

    inicio = codigo.index("RETRYABLE_STATUS = {")
    fin = codigo.index("def _upload_one(")

    import threading
    ambito = {"threading": threading}
    exec(codigo[inicio:fin], ambito)

    return ambito["is_retryable_status"], ambito["RETRYABLE_STATUS"]


def main():

    is_retryable, conjunto = cargar_clasificador()

    print("\n=== 1. Errores del servidor (5xx): SE REINTENTAN ===")
    for codigo in (500, 502, 503, 504, 507, 509, 599):
        comprobar(f"{codigo} es reintentable", is_retryable(codigo))

    print("\n=== 2. Sobrecarga y espera: SE REINTENTAN ===")
    for codigo in (408, 425, 429):
        comprobar(f"{codigo} es reintentable", is_retryable(codigo))

    print("\n=== 3. Errores permanentes del cliente: NO se reintentan ===")
    for codigo, motivo in [
        (400, "petición mal formada"),
        (401, "token inválido"),
        (403, "token de otro equipo"),
        (404, "dispositivo inexistente"),
        (413, "archivo demasiado grande"),
        (415, "tipo no admitido"),
        (422, "datos no procesables")
    ]:
        comprobar(f"{codigo} ({motivo}) NO es reintentable",
                  not is_retryable(codigo))

    print("\n=== 4. Un 4xx nuevo no se reintenta por descuido ===")
    comprobar("418 NO es reintentable", not is_retryable(418))
    comprobar("451 NO es reintentable", not is_retryable(451))

    print("\n=== 5. Un 5xx desconocido SÍ se reintenta ===")
    comprobar("521 es reintentable", is_retryable(521))
    comprobar("598 es reintentable", is_retryable(598))

    print("\n=== 6. El archivo local NUNCA se borra por un error ===")
    ruta = os.path.join(RAIZ, "agent", "agent.py")

    with open(ruta, encoding="utf-8") as handle:
        codigo = handle.read()

    inicio = codigo.index("def _upload_one(")
    fin = codigo.index("def _process_pending(")
    cuerpo = codigo[inicio:fin]

    comprobar("_upload_one no borra archivos",
              "os.remove" not in cuerpo and "unlink" not in cuerpo)
    comprobar("un error permanente devuelve False (sigue en la cola)",
              cuerpo.count("return False") >= 2)
    comprobar("el error permanente se registra en el log",
              "ERROR PERMANENTE" in cuerpo)
    comprobar("y avisa de que el archivo se conserva",
              "se conserva" in cuerpo)

    print(chr(10) + "=== 7. La copia local se conserva tras subir ===")
    inicio = codigo.index("def _process_pending(")
    fin = codigo.index("def on_segment_complete(")
    proceso = codigo[inicio:fin]

    comprobar("solo se retira de la cola si _upload_one devolvio True",
              "if _upload_one(segment):" in proceso)

    # Cambio de politica: antes, confirmar la subida borraba el archivo.
    # Ahora el equipo conserva su copia y solo la retencion la retira.
    comprobar("una subida correcta ya NO borra el archivo local",
              "_delete_uploaded_recording" not in proceso
              and "delete_recording_file" not in proceso)

    comprobar("lo unico que ocurre es salir de la cola de pendientes",
              "_remove_pending(segment" in proceso)

    comprobar("el borrado automatico vive solo en la retencion",
              "apply_local_retention" in codigo
              and "_is_pending_upload" in codigo)

    fallos = [d for d, ok in resultados if not ok]

    print("\n" + "=" * 62)
    print(f"  {len(resultados) - len(fallos)} de {len(resultados)} comprobaciones correctas")

    if fallos:
        print("  FALLOS:")
        for f in fallos:
            print("   -", f)

    print("=" * 62)

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
