# Publicar VEYRA 1.3.0

**Nada de esto se ha ejecutado.** Es el procedimiento, con lo que ya
está comprobado y lo que falta por decidir.

Son **tres artefactos** y van por caminos distintos:

| artefacto | qué es | cómo llega al servidor |
|---|---|---|
| `agent-1.3.0.zip` | el paquete de actualización | se firma aquí y se copia a `releases/` |
| `manifest.json` | versión, hash, tamaño y firma | lo produce el firmador |
| `VEYRA-Instalador.exe` | la instalación nueva | se construye aquí y se copia |

Los dos primeros los recogen los Agents ya instalados. El tercero lo
descargan las personas desde el panel. **`releases/` está ignorado por
git**: nada de esto viaja en un `push`.

---

## Lo que ya está comprobado

Ejecutado sobre el código actual:

```
version declarada : 1.3.0
manifiesto        : {'version': '1.3.0',
                     'sha256': '9be921dc1041c5995b1d639260f68fd3781de34af2988b1f2477d6649430b476',
                     'size': 87685}
sha256 recalculado: 9be921dc1041c599...  coincide: True
el paquete declara: 1.3.0
```

El actualizador **rechaza** lo que debe:

```
firma que no corresponde     SignatureError
firma manipulada             SignatureError
paquete cambiado por otro    ReleaseError
tamano que no cuadra         ReleaseError
descarga a medias            ReleaseError
version mal formada          UpdateError
```

Y las versiones:

```
desde 1.1.0  -> (True,  'hay version nueva: 1.1.0 -> 1.3.0')
desde 1.2.0  -> (True,  'hay version nueva: 1.2.0 -> 1.3.0')
desde 1.3.0  -> (False, 'ya esta en la version 1.3.0')
desde 2.0.0  -> (False, 'la version publicada es anterior: no se instala')
```

La **1.2.0 publicada sigue válida**, con su firma y su hash. No se
toca hasta que la 1.3.0 esté comprobada en un equipo.

---

## Paso 1. Construir, en este equipo

```powershell
cd C:\Users\odett\RemoteAdmin

# El interprete embebido, solo si falta installer\runtime\python.exe
.\.venv\Scripts\python installer\build-runtime.py

powershell -File installer\build-installer.ps1 -Limpiar
```

Anota el SHA-256 que imprime. **Ese es el número con el que se
comprueba todo lo demás.**

```powershell
.\.venv\Scripts\python tests\test_paquete_completo.py
.\.venv\Scripts\python tests\test_actualizacion.py
.\.venv\Scripts\python tests\test_instalador_exe.py
```

Si `test_paquete_completo` falla, **para**: significa que al paquete
le falta un módulo y el Agent no arrancaría tras actualizarse.

## Paso 2. Firmar el paquete

La clave privada Ed25519 **no está aquí y no debe estarlo**.

```powershell
.\.venv\Scripts\python tools\sign_agent_release.py --firmar `
    --clave <tu_ruta>\agent-signing.pem
```

Tiene que decir **«Firma comprobada contra la clave publica del
Agent»**. Si no, la clave no es la pareja de la que llevan los Agents
instalados y publicarlo no serviría de nada.

Deja `releases\agent-1.3.0.zip` y `releases\manifest.json`, y debe
coincidir con:

```
"version": "1.3.0"
"sha256":  "9be921dc1041c5995b1d639260f68fd3781de34af2988b1f2477d6649430b476"
"size":    87685
```

**Si el sha256 no coincide**, el código cambió desde esta medición:
vuelve al paso 1.

## Paso 3. Firmar el instalador — *pendiente de decisión*

Ver [firma-de-codigo.md](firma-de-codigo.md). Hoy **no se puede**: no
hay certificado y `signtool` no está instalado.

Se puede publicar sin firmar, asumiendo el aviso de SmartScreen. Es
una decisión tuya.

## Paso 4. Copiar al servidor

```powershell
scp -i <tu_clave> `
    releases\agent-1.3.0.zip `
    releases\manifest.json `
    installer\dist\VEYRA-Instalador.exe `
    ubuntu@159.54.156.221:/tmp/
```

**El orden importa en el siguiente paso, no aquí.**

```bash
cd /opt/remoteadmin/RemoteAdmin

# 1. Guardar lo que hay, por si hay que volver
sudo cp releases/manifest.json releases/manifest.json.1.2.0
sudo cp releases/VEYRA-Instalador.exe releases/VEYRA-Instalador.exe.anterior

# 2. El paquete y el instalador PRIMERO
sudo mv /tmp/agent-1.3.0.zip /tmp/VEYRA-Instalador.exe releases/

# 3. El manifiesto AL FINAL
sudo mv /tmp/manifest.json releases/

sudo chown remoteadmin:remoteadmin releases/*
```

El manifiesto va **el último** a propósito: es lo que dispara la
actualización. Si llegara antes que el ZIP, un Agent que compruebe en
ese hueco pediría un paquete que todavía no está. No es grave —
reintenta — pero deja un error en el registro sin motivo.

**No hace falta reiniciar `remoteadmin`:** los archivos se leen de
disco en cada petición.

---

## Verificaciones después de publicar

### En el servidor

```bash
cd /opt/remoteadmin/RemoteAdmin

sha256sum releases/agent-1.3.0.zip releases/VEYRA-Instalador.exe
cat releases/manifest.json
ls -l releases/
```

| qué | debe dar |
|---|---|
| `agent-1.3.0.zip` | `9be921dc1041c599…` |
| `VEYRA-Instalador.exe` | el SHA del paso 1 |
| `manifest.json` | `"version": "1.3.0"` y el mismo sha256 del ZIP |
| dueño | `remoteadmin:remoteadmin` |

**Si un hash no coincide, el archivo llegó dañado.** Repón el
respaldo del paso 4 y vuelve a copiarlo.

### Desde el panel

1. Entrar como propietario → **Instalación**.
2. Debe decir **«Versión 1.3.0»** y su tamaño.
3. Descargar y comprobar la huella:

```powershell
Get-FileHash $env:USERPROFILE\Downloads\VEYRA-Instalador.exe -Algorithm SHA256
```

4. Entrar con un subadministrador: **no debe poder descargarlo**
   (solo el propietario).
5. Sin sesión, `…/api/agent-installer` debe dar **401**.

### Actualizar un Agent de prueba desde 1.2.0

Esta es la que de verdad decide, y **no puede hacerse desde aquí**.

En un equipo con el Agent en 1.2.0:

| # | qué mirar | dónde |
|---|---|---|
| 1 | Anotar su `device_id` | `C:\ProgramData\RemoteAdmin\config\identity.json` |
| 2 | Que detecta la versión nueva | `logs\agent.log`, línea `[update]` |
| 3 | Que **se descarga y verifica la firma** antes de instalar | `logs\agent.log` |
| 4 | Que el panel informa **1.3.0** | ficha del equipo |
| 5 | Que el `device_id` **no cambió** | `identity.json` |
| 6 | Que **no aparece un equipo duplicado** | inventario |
| 7 | Que las grabaciones locales siguen | `recordings\` |
| 8 | Que el Agent **reconecta** | el panel lo marca en línea |

**El punto 5 es el que miraría primero.** Si cambiara, la
actualización habría tocado `identity.json`, y eso no debería poder
pasar — el actualizador lo preserva — pero es el fallo más caro.

### Volver atrás

```bash
cd /opt/remoteadmin/RemoteAdmin
sudo mv releases/manifest.json.1.2.0 releases/manifest.json
sudo chown remoteadmin:remoteadmin releases/manifest.json
```

Con eso los Agents vuelven a ver la 1.2.0. Los que ya se actualizaran
**se quedan en 1.3.0**: el actualizador no instala versiones
anteriores, a propósito. Para bajarlos haría falta reinstalarlos a
mano.

---

## Lo que necesita tu autorización

1. **Firmar el paquete 1.3.0.** Solo tú tienes la clave.
2. **Publicar sin firma Authenticode**, asumiendo el aviso de
   SmartScreen, o esperar al certificado.
3. **Qué equipo se usa de prueba.** No publicaría el `manifest.json`
   —que es lo que dispara la actualización en *todos*— antes de ver
   un equipo pasar de 1.2.0 a 1.3.0 sin perder su identidad.
4. **El `scp` sigue sin resolverse**: tu `~/.ssh/` no tiene ninguna
   clave y el último intento falló con `Permission denied (publickey)`.
