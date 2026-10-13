# Respaldo, restauración y reversión

Antes de este documento **no existía ningún procedimiento de
respaldo**: ni rutina en el código, ni nada en `deploy/README.md`, ni
orden en `platform_admin.py`.

La herramienta es `scripts/respaldo.py`. Todo lo que sigue está
probado sobre copias aisladas. **Nada se ha ejecutado en producción.**

---

## Qué hay que respaldar

| Qué | Dónde | Por qué |
|---|---|---|
| La base | `remoteadmin.db` | Organizaciones, usuarios, equipos, grabaciones, auditoría, ajustes |
| Auditoría pendiente | `auditoria_pendiente.jsonl` | Eventos que la base no aceptó. Puede no existir |
| Grabaciones | `server_recordings/` | Los archivos. En la instalación local, 12 MB |

Las rutas las decide `backend/database.py` y `backend/recordings.py`,
relativas a la raíz del proyecto. En Oracle:
`/opt/remoteadmin/RemoteAdmin/`.

### Qué NO entra, y es deliberado

Ni `.env`, ni `/etc/remoteadmin/remoteadmin.env`, ni certificados, ni
claves. **Un respaldo se copia, se envía y se guarda en sitios donde
un secreto no debería estar.**

El paquete lleva en su manifiesto la lista de lo que hay que reponer
a mano al restaurar, para que no se olvide.

---

## Por qué no se copia el archivo y ya

La base está abierta mientras el servicio corre. Copiarla con `cp`
puede dar un archivo a medias: SQLite escribe en varias páginas y
mantiene un diario aparte. **El resultado abre perfectamente y falla
meses después**, cuando hace falta de verdad.

`scripts/respaldo.py` usa `Connection.backup()`, el mecanismo de
respaldo de la propia SQLite, que se coordina con los escritores. Es
lo mismo que hace `.backup` en su consola.

La base original se abre en **modo solo lectura** (`mode=ro`): el
proceso de respaldo no puede escribir en producción ni por error.
Verificado comparando la huella SHA-256 de la base antes y después —
idéntica.

---

## Crear un respaldo

```bash
cd /opt/remoteadmin/RemoteAdmin
sudo -u remoteadmin ./.venv/bin/python scripts/respaldo.py crear
```

Deja un paquete en `respaldos/veyra-AAAAMMDD-HHMMSS/`:

```
remoteadmin.db              copia coherente
auditoria_pendiente.jsonl   si existe
server_recordings/          los archivos
manifiesto.json             huellas, recuentos e integridad
```

Con `--destino CARPETA` se elige dónde. Con `--sin-grabaciones` se
omiten los archivos: mucho más rápido y pequeño, útil para un
respaldo rápido **antes de un despliegue que no toca grabaciones**.

**Antes de desplegar basta con `--sin-grabaciones`**: ningún cambio
de este despliegue escribe en `server_recordings/`.

---

## Verificar

```bash
python scripts/respaldo.py verificar respaldos/veyra-...
```

Comprueba cuatro cosas:

- **`PRAGMA integrity_check`** — recorre la estructura entera
- **`PRAGMA foreign_key_check`** — referencias rotas
- **SHA-256** de la base y de cada grabación, contra el manifiesto
- **Recuentos** de diez tablas, contra los del momento del respaldo

Probado manipulando una copia: borrando **una sola fila** de
`recordings`, lo detecta por dos vías independientes —la huella y el
recuento— y dice «el paquete NO es fiable».

---

## Probar: la única forma de saber que sirve

**Intacto no es lo mismo que restaurable.**

```bash
python scripts/respaldo.py probar respaldos/veyra-...
```

Restaura el paquete en una carpeta temporal, abre la base, comprueba
su integridad, cuenta las tablas y **consulta datos de verdad**
—equipos y grabaciones— antes de borrar la carpeta.

Resultado sobre la instalación local:

```
integrity_check: ok
  organizations 1 · users 2 · devices 2 · recordings 11
  audit_log 40 · alerts 34
equipos legibles: 2
grabaciones: 11, 11.8 MB

RESULTADO: el respaldo SIRVE para restaurar
```

**Un respaldo que no se ha probado no es un respaldo.**

---

## Restaurar

```bash
python scripts/respaldo.py restaurar respaldos/veyra-... \
    --destino /tmp/recuperado
```

**Nunca escribe sobre una instalación existente.** Si la carpeta
destino ya tiene una base, se niega. Mover lo restaurado a su sitio
es un paso manual, deliberado y con el servicio parado:

```bash
sudo systemctl stop remoteadmin

# La base actual se APARTA, no se borra
sudo -u remoteadmin mv remoteadmin.db remoteadmin.db.antes-de-restaurar

sudo -u remoteadmin cp /tmp/recuperado/remoteadmin.db .
sudo -u remoteadmin cp -r /tmp/recuperado/server_recordings/* server_recordings/

sudo systemctl start remoteadmin
curl -s https://<url-publica>/api/health
```

Después hay que reponer lo que no viajaba en el paquete: el archivo
de entorno con la clave de sesión, SMTP y la URL pública.

---

## Revertir la aplicación a `f911f4e`

### La pregunta que importa: ¿funciona el código viejo con la base ya migrada?

El despliegue añade dos columnas a `devices`: `decommissioned_at` y
`succeeds_device_id`. Si el código anterior se atragantara con ellas,
revertir dejaría el servicio caído.

**Lo probé**, sacando el código de `f911f4e` con `git show` y
ejecutándolo contra una copia de la base ya migrada:

```
listar equipos ............. OK, 2 equipos
uso de la organización ..... OK, {'devices': 2, 'users': 2}
límites del plan ........... OK
ESCRITURA: alta de equipo .. OK
y se lee de vuelta ......... OK, 3 equipos

RESULTADO: el código anterior lee Y ESCRIBE sin problema
```

Las columnas le sobran, y SQLite no se queja de columnas que nadie
nombra. **Revertir el código sin revertir la base es seguro.**

### Una consecuencia que hay que conocer

Si se revierte **después de haber dado de baja algún equipo**:

| | Código nuevo | Tras revertir |
|---|---|---|
| El equipo aparece en el inventario | No | **Sí** |
| Cuenta para el plan | No | **Sí** |
| Su credencial funciona | No | **No** |

Comprobado. El equipo **reaparece y vuelve a ocupar plaza**, pero
**no puede conectarse**: la credencial sigue revocada. Es el lado
seguro del error —no se abre ningún acceso— pero la cuenta de equipos
cambia y conviene no sorprenderse.

Al volver a la versión nueva, la baja reaparece: el dato nunca se
perdió.

### El procedimiento

```bash
sudo systemctl stop remoteadmin
sudo -u remoteadmin git -C /opt/remoteadmin/RemoteAdmin checkout f911f4e
sudo systemctl start remoteadmin
curl -s https://<url-publica>/api/health
```

**No hace falta restaurar la base** para revertir el código. Solo se
restaura si los datos se han estropeado, que es otro problema.

---

## Probarlo sin tocar nada real

Todo lo anterior se puede ensayar sobre una copia:

```bash
mkdir -p /tmp/ensayo && cd /tmp/ensayo
cp /opt/remoteadmin/RemoteAdmin/remoteadmin.db .

python /opt/remoteadmin/RemoteAdmin/scripts/respaldo.py crear \
    --destino /tmp/ensayo/respaldos --sin-grabaciones

python .../scripts/respaldo.py probar /tmp/ensayo/respaldos/veyra-...
```

`crear` abre la base en solo lectura y `restaurar` se niega a
sobrescribir: ninguna de las dos puede dañar la instalación.

---

## Limitaciones y condiciones

**Espacio.** El paquete ocupa aproximadamente lo mismo que los datos:
la base (0,2 MB) más las grabaciones (12 MB en local; en Oracle puede
ser mucho más). Hay que comprobar que hay sitio **antes**, o el
respaldo quedará a medias. No he comprobado el espacio de Oracle.

**Permisos.** Hay que ejecutarlo como `remoteadmin`, el dueño de los
archivos, y no como root. Si no, el paquete queda con un dueño que
luego estorba.

**Archivos que cambian durante el respaldo.** La base está cubierta
por el mecanismo de SQLite. **Las grabaciones no**: si un Agent sube
una mientras se copia la carpeta, puede quedar a medias o no entrar.
No es grave —se vuelve a subir o se recupera del equipo— pero
significa que la copia de grabaciones es *casi* del momento, no
exactamente.

Para un respaldo exacto de grabaciones habría que parar el servicio.
Para un despliegue, no compensa.

**Qué hace válido un respaldo**, y las cuatro condiciones son
necesarias:

1. `verificar` dice que está intacto.
2. `probar` dice que sirve para restaurar.
3. Los recuentos cuadran con lo que esperabas.
4. Está **fuera** del servidor. Un respaldo en el mismo disco que la
   base no protege del fallo más probable.

**Lo que no cubre.** No es un respaldo continuo: es una foto. Entre
dos respaldos se pierde lo que haya pasado. Para un despliegue basta;
para una política de copias, no.

---

## Antes del despliegue de observación

```bash
# 1. Respaldo (sin grabaciones: el despliegue no las toca)
sudo -u remoteadmin ./.venv/bin/python scripts/respaldo.py crear \
    --sin-grabaciones

# 2. Probarlo. Si esto no dice que SIRVE, no se despliega.
sudo -u remoteadmin ./.venv/bin/python scripts/respaldo.py probar \
    respaldos/veyra-...

# 3. Sacarlo del servidor
scp -r <usuario>@<servidor>:/opt/remoteadmin/RemoteAdmin/respaldos/veyra-... .

# 4. Anotar a dónde se vuelve
sudo -u remoteadmin git -C /opt/remoteadmin/RemoteAdmin rev-parse HEAD
```

Con esos cuatro pasos hechos, revertir es parar el servicio, volver
al commit anotado y arrancar.
