# Despliegue de RemoteAdmin en el servidor

El objetivo de estos archivos es que, una vez configurado, **nada dependa de
que alguien tenga una terminal, un SSH o un PowerShell abierto**. Todo corre
como servicio del sistema y vuelve solo despues de un reinicio.

```
Internet → Cloudflare → hostname estable → Cloudflare Tunnel
         → Nginx → RemoteAdmin/FastAPI → WebSocket → Agents Windows
```

Tres servicios, independientes entre si: `remoteadmin`, `nginx` y
`cloudflared`. Ninguno declara dependencia de los otros, a proposito: asi la
caida de uno no arrastra a los demas y cada uno se recupera por su cuenta.

## Archivos

| Archivo | Destino |
|---|---|
| `remoteadmin.service` | `/etc/systemd/system/` |
| `nginx-remoteadmin.conf` | `/etc/nginx/sites-available/remoteadmin` |
| `cloudflared-config.yml` | `/etc/cloudflared/config.yml` |

## 1. Backend

```bash
sudo cp deploy/remoteadmin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now remoteadmin
systemctl status remoteadmin
```

`enable` es lo que lo hace arrancar solo tras reiniciar el servidor.
`Restart=always` lo levanta si se cae.

Ajusta `User`, `WorkingDirectory` y las rutas si el despliegue no esta en
`/opt/remoteadmin`. Las credenciales van en
`/etc/remoteadmin/remoteadmin.env`, con permisos `0600`.

## 2. Nginx

```bash
sudo cp deploy/nginx-remoteadmin.conf /etc/nginx/sites-available/remoteadmin
sudo ln -s /etc/nginx/sites-available/remoteadmin /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Lo decisivo de este archivo son `proxy_http_version 1.1` y las cabeceras
`Upgrade`/`Connection`. Sin ellas el panel carga sin problemas y **ningun
Agent conecta jamas**, sin ningun error visible.

## 3. Cloudflare Tunnel

El Quick Tunnel (`cloudflared tunnel --url ...`, direcciones
`*.trycloudflare.com`) **no sirve para produccion**: cambia de direccion en
cada arranque, asi que un reinicio dejaria a todos los Agents apuntando a una
direccion que ya no existe.

Lo que se necesita es un **Named Tunnel**, y para eso hace falta un dominio en
Cloudflare. Ese es el unico punto que no se puede resolver desde el codigo.

### Lo que falta, exactamente

1. Un dominio en una cuenta de Cloudflare.
2. Elegir un hostname, por ejemplo `panel.tudominio.com`.

### Cuando lo haya, una sola vez

```bash
curl -L --output cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

cloudflared tunnel login                        # abre el navegador
cloudflared tunnel create remoteadmin           # imprime el ID del tunel
cloudflared tunnel route dns remoteadmin panel.tudominio.com
```

Copia `cloudflared-config.yml` a `/etc/cloudflared/config.yml` y rellena
`<ID-DEL-TUNEL>` y `<HOSTNAME>`. Despues:

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
systemctl status cloudflared
```

`cloudflared service install` crea la unidad de systemd. A partir de ahi
arranca con el servidor y se reinicia solo: no hay que volver a ejecutar
ningun comando a mano.

## 4. La direccion publica

Una vez exista el hostname, hay **un solo sitio** donde se configura:

```
REMOTEADMIN_PUBLIC_URL=https://panel.tudominio.com
```

en `/etc/remoteadmin/remoteadmin.env`. Luego `sudo systemctl restart
remoteadmin`.

De ahi la toma el panel para generar el instalador del Agent. No esta escrita
en ningun otro archivo.

Una organizacion concreta puede tener la suya propia (campo *Despliegue* de su
ficha), que tiene prioridad; es lo que corresponde en self-hosted, donde cada
cliente corre su propio servidor.

## 5. Cambiar la direccion mas adelante

Cambiar de `https://URL-A` a `https://URL-B` **no obliga a reinstalar ningun
Agent ni a volver a darlo de alta**. La identidad del equipo
(`identity.json`: `device_id` y token individual) no depende de la direccion
del servidor.

En el servidor:

1. Enruta el nuevo hostname al mismo tunel.
2. Cambia `REMOTEADMIN_PUBLIC_URL`.
3. `sudo systemctl restart remoteadmin`.

En cada equipo ya instalado, cambia una linea de
`C:\ProgramData\RemoteAdmin\.env`:

```
REMOTEADMIN_SERVER=https://URL-B
```

y reinicia la tarea. Conserva su `device_id`, su token individual, su
organizacion, sus grabaciones y su historial.

Lo mas comodo es mantener la direccion antigua apuntando al mismo sitio
mientras se hace el cambio: asi no hay prisa y ningun equipo queda sin
conexion.

## Comprobaciones

```bash
systemctl is-enabled remoteadmin nginx cloudflared   # los tres: enabled
systemctl is-active  remoteadmin nginx cloudflared   # los tres: active
curl -sI https://panel.tudominio.com/                # 200
journalctl -u remoteadmin -n 50 --no-pager
```

La prueba de verdad es reiniciar el servidor entero y comprobar que los tres
vuelven solos:

```bash
sudo reboot
# y al volver:
systemctl is-active remoteadmin nginx cloudflared
```


## 6. Actualizar a una version nueva

El servidor trabaja sobre `main`. Una version nueva llega fusionando
su pull request y trayendo los cambios con `git pull`.

Antes de empezar, dos datos que hacen falta para poder volver atras:

```bash
cd /opt/remoteadmin/RemoteAdmin

sudo -u remoteadmin git rev-parse HEAD      # anota esto
sudo -u remoteadmin git status --short      # debe estar limpio
```

Si `git status` no sale vacio, hay cambios hechos a mano en el
servidor. **Para y averigua que son antes de seguir**: un `git pull`
los pisaria.

### 6.1. Respaldo, primero

Sin respaldo verificado no se actualiza. El procedimiento completo
esta en `docs/respaldo-y-reversion.md`; el resumen:

```bash
cd /opt/remoteadmin/RemoteAdmin

sudo -u remoteadmin ./.venv/bin/python scripts/respaldo.py crear
sudo -u remoteadmin ./.venv/bin/python scripts/respaldo.py probar \
    respaldos/veyra-AAAAMMDD-HHMMSS
```

`probar` tiene que decir **"el respaldo SIRVE para restaurar"**. Si
dice otra cosa, no se actualiza.

Despues, sacar la copia del servidor:

```bash
# desde tu equipo
scp -r ubuntu@<servidor>:/opt/remoteadmin/RemoteAdmin/respaldos/veyra-... .
```

Un respaldo en el mismo disco que la base no protege del fallo mas
probable.

### 6.2. Traer la version

```bash
cd /opt/remoteadmin/RemoteAdmin

sudo -u remoteadmin git fetch origin
sudo -u remoteadmin git pull --ff-only origin main
sudo -u remoteadmin git rev-parse --short HEAD   # el commit esperado
```

`--ff-only` a proposito: si no puede avanzar en linea recta, falla en
vez de crear una fusion en el servidor.

El `sudo -u remoteadmin` tambien: ejecutar git como root deja los
archivos con otro dueño y la siguiente actualizacion falla con
"dubious ownership".

### 6.3. Dependencias, solo si cambiaron

```bash
sudo -u remoteadmin git diff --name-only <commit-anterior> HEAD \
    | grep requirements
```

Si no sale nada, no hay que instalar nada. Si sale, mirar **que**
cambio antes de instalar.

### 6.4. Reiniciar

```bash
sudo systemctl restart remoteadmin
systemctl is-active remoteadmin
```

Las migraciones de base corren solas al arrancar: `init_db()` añade
las columnas que falten. Son aditivas y no reescriben filas.

### 6.5. Comprobar

```bash
curl -s https://<url-publica>/api/health          # {"status":"ok",...}
curl -sI https://<url-publica>/            | head -1   # 200
curl -sI https://<url-publica>/panel       | head -1   # 200
curl -sI https://<url-publica>/frontend/index.html | head -1  # 200

journalctl -u remoteadmin -n 60 --no-pager
sudo tail -n 40 /var/log/nginx/error.log
```

Y que los datos siguen ahi, con los mismos recuentos que antes:

```bash
sudo -u remoteadmin sqlite3 \
  "file:/opt/remoteadmin/RemoteAdmin/remoteadmin.db?mode=ro" \
  "SELECT 'equipos', COUNT(*) FROM devices
   UNION ALL SELECT 'grabaciones', COUNT(*) FROM recordings
   UNION ALL SELECT 'usuarios', COUNT(*) FROM users
   UNION ALL SELECT 'auditoria', COUNT(*) FROM audit_log;"
```

Por ultimo, que los Agents vuelven solos: en unos minutos deberian
reconectar sin que nadie toque sus equipos.

```bash
journalctl -u remoteadmin --since "5 minutes ago" | grep -i "agent connected"
```

### 6.6. Volver atras

Revertir el codigo **no necesita restaurar la base**. Las columnas
nuevas le sobran a la version anterior, que las ignora.

```bash
sudo systemctl stop remoteadmin
sudo -u remoteadmin git -C /opt/remoteadmin/RemoteAdmin checkout <commit-anterior>
sudo systemctl start remoteadmin
curl -s https://<url-publica>/api/health
```

La base solo se restaura si los datos se han estropeado, que es otro
problema y tiene su propio procedimiento en
`docs/respaldo-y-reversion.md`.

Una consecuencia de revertir que conviene conocer: si se habia dado
de baja algun equipo, al volver atras **reaparece en el inventario y
vuelve a ocupar plaza del plan**, porque la version anterior no
entiende esa marca. Su credencial sigue revocada, asi que no puede
conectarse. Al volver a la version nueva, la baja reaparece: el dato
nunca se pierde.

### Que NO toca una actualizacion

Ni `/etc/remoteadmin/remoteadmin.env`, ni la base, ni
`server_recordings/`, ni los certificados, ni los respaldos
anteriores. `git pull` solo trae archivos versionados, y ninguno de
esos lo esta.
