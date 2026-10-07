# Publicar una version del Agent

Los equipos administrados se actualizan solos. Este documento explica como
se publica una version nueva para que la recojan.

## Lo que hay que entender antes

El Agent **no instala nada que no venga firmado**. La firma no la hace el
servidor: la hace quien publica, en su propio equipo, con una clave privada
que no esta ni en el repositorio ni en el servidor.

Esa separacion es el motivo de todo lo demas. Si el servidor pudiera firmar,
quien se hiciera con el podria instalar cualquier cosa en todos los equipos
administrados a la vez. Al no poder, lo peor que consigue es dejar de servir
actualizaciones.

```
Quien publica            Servidor               Equipo administrado
  clave privada    →   solo guarda y sirve  →   comprueba la firma
                                                 con la clave publica
```

## Preparacion, una sola vez

### 1. Generar el par de claves, fuera del proyecto

La ruta es obligatoria y tiene que estar **fuera** del repositorio. La
herramienta se niega a escribir la clave dentro, y tambien a usar una que
este dentro: ahi acabaria en un commit, en el ZIP del Agent o en una copia
al servidor.

```bash
python tools/sign_agent_release.py --generar-clave \
    --clave D:\llaves\agent-signing.pem
```

Escribe la clave privada en esa ruta e imprime **solo la publica**. La
privada no se muestra nunca por pantalla: lo que se imprime acaba en el
historial de la terminal y en los registros de sesion.

Guardala donde guardas lo que no puede perderse, y haz copia.

### 2. Incorporar la clave publica al Agent

Pega la clave publica que imprimio el comando en
[agent/release.py](../agent/release.py), en `AGENT_UPDATE_PUBLIC_KEY`:

```python
AGENT_UPDATE_PUBLIC_KEY = "LWxp9l/J8EhHVWpECV6m1NAPXo5bmQHRY2VI..."
```

La publica **si** va en el codigo: no es secreta, solo sirve para comprobar
firmas. Es la configuracion de confianza del Agent, y viaja con el en el
paquete de instalacion.

Confirma ese cambio y distribuye esa version del Agent.

### 3. Para no repetir la ruta cada vez

```bash
set REMOTEADMIN_SIGNING_KEY=D:\llaves\agent-signing.pem
```

La variable lleva **la ruta**, nunca la clave.

Mientras `AGENT_UPDATE_PUBLIC_KEY` este vacio, ningun Agent se actualiza: sin
clave no hay forma de comprobar la firma, y quedarse en la version actual es
preferible a instalar algo sin comprobar.

### Si se pierde la clave privada

Hay que generar un par nuevo y actualizar **a mano** el Agent de cada equipo
ya instalado, porque dejarian de aceptar cualquier version nueva. De ahi la
insistencia en la copia de seguridad.

## Publicar una version

1. Sube el numero en [agent/version.py](../agent/version.py):

   ```python
   AGENT_VERSION = "1.2.0"
   ```

   Tiene que ser **posterior** a la que corre en los equipos. Una version
   igual no se instala y una anterior se rechaza: volver atras reintroduce
   fallos ya corregidos.

2. Empaqueta y firma:

   ```bash
   python tools/sign_agent_release.py --firmar \
       --clave D:\llaves\agent-signing.pem
   ```

   Deja en `releases/` el paquete y el manifiesto firmado. La clave se lee
   donde este y se usa en memoria: **no se copia al proyecto** ni se escribe
   en ningun otro sitio.

   La herramienta comprueba la firma recien hecha contra la clave publica
   del Agent y avisa si no se corresponden, antes de que publiques algo que
   nadie podria instalar.

3. Copia los dos archivos a `releases/` en el servidor:

   ```bash
   scp releases/manifest.json releases/agent-1.2.0.zip servidor:/opt/remoteadmin/releases/
   ```

No hace falta reiniciar el servidor: los archivos se leen en cada peticion.

### Que llega al servidor, y que no

Al servidor se copian **dos archivos y nada mas**: el paquete y el
manifiesto firmado. Los dos son publicos; no hay nada que proteger en
ellos mas alla de que no se alteren, y si se alteran los Agents lo
detectan.

El servidor **no tiene ninguna clave** y no la necesita: no firma, no
verifica y no sabe de criptografia. Solo guarda y entrega.

Eso es lo que hace que un servidor comprometido no pueda instalar codigo
en los equipos administrados. Lo peor que consigue es dejar de servir
actualizaciones, o servir una antigua, que los Agents rechazan por ser
anterior a la instalada.

Si alguna vez te ves copiando la clave privada al servidor, algo se ha
entendido al reves: ahi no hace nada y anula el sentido de firmar.

## Que pasa en los equipos

Cada seis horas, el servicio de fondo pregunta por la version publicada. Si
hay una posterior y la firma es valida, la descarga, comprueba que coincide
con lo firmado, respalda la version actual, instala y reinicia su tarea.

Si algo falla en cualquier punto, vuelve a la version anterior y sigue
funcionando. Una actualizacion a medias dejaria un Agent que no arranca, y un
equipo remoto que no arranca es un equipo perdido hasta que alguien vaya
fisicamente.

Lo que **nunca** se toca: `config\identity.json`, las grabaciones, los
registros y el `.env`. Por eso una actualizacion no cambia el `device_id` ni
vuelve a pedir la credencial de instalacion.

El seguimiento esta en `C:\ProgramData\RemoteAdmin\logs\agent-service.log`,
en las lineas que empiezan por `[update]`.

## Comprobar que se publico bien

```bash
curl -s https://TU-DOMINIO/api/agent/version -H "X-Agent-Token: <token de un equipo>"
```

Devuelve el manifiesto y su firma. Con `"manifest": null` no hay nada
publicado.

## Volver atras una publicacion

No se puede "despublicar" de los equipos que ya actualizaron: el Agent no
acepta versiones anteriores, a proposito.

Lo que se hace es publicar una version **posterior** con la correccion. Si
`1.2.0` salio mal, se corrige y se publica `1.2.1`.
