# Recuperación de contraseña por correo

El flujo ya está implementado: el usuario pide un enlace desde la pantalla de
acceso, recibe un correo, abre el enlace y establece una contraseña nueva.

Lo que **no** viene configurado es el envío. Hasta que se configure, VEYRA
acepta la solicitud, responde con un mensaje de éxito y **descarta el correo
en silencio**.

Esto no es un descuido del flujo, es deliberado: la respuesta es siempre la
misma —exista o no la dirección— para no revelar qué correos están dados de
alta. El efecto secundario es que nada en el panel delata que el envío no
está configurado. Por eso existe el diagnóstico.

## Comprueba primero en qué estado estás

```bash
python platform_admin.py correo-diagnostico
```

Dice si el envío funcionaría de verdad y, si no, qué falta. No imprime
contraseñas ni usuarios de SMTP: solo si están definidos.

## Los tres requisitos

### 1. Cada usuario necesita una dirección

Sin correo, un usuario **no puede recuperar su contraseña**, y al pedirlo verá
el mismo mensaje de éxito que los demás.

```bash
python platform_admin.py listar                       # ver quién tiene correo
python platform_admin.py correo <usuario> <dirección>
```

La dirección queda *sin verificar* a propósito: cambiarla no hereda la
verificación de la anterior, porque entonces bastaría con apuntarla a otro
sitio para heredar la confianza.

### 2. Un servidor de correo

En `/etc/remoteadmin/remoteadmin.env`:

```
REMOTEADMIN_MAIL_TRANSPORT=smtp
REMOTEADMIN_SMTP_HOST=smtp.ejemplo.com
REMOTEADMIN_SMTP_PORT=587
REMOTEADMIN_SMTP_USER=avisos@tudominio.com
REMOTEADMIN_SMTP_PASSWORD=...
REMOTEADMIN_SMTP_TLS=1
REMOTEADMIN_MAIL_FROM=VEYRA <avisos@tudominio.com>
```

`REMOTEADMIN_MAIL_TRANSPORT` es la que de verdad cambia el comportamiento.
Mientras valga `mock` —y es el valor por defecto— no se envía nada, aunque el
resto esté puesto.

De las demás, solo `REMOTEADMIN_SMTP_HOST` es obligatoria. `PORT` asume 587,
`TLS` asume activado, y sin `USER`/`PASSWORD` se intenta un envío sin
autenticar, que casi ningún proveedor acepta.

Después:

```bash
sudo systemctl restart remoteadmin
python platform_admin.py correo-diagnostico
```

### 3. Una dirección pública alcanzable

El enlace del correo se construye con `REMOTEADMIN_PUBLIC_URL`. Si apunta a
`localhost`, el enlace no se podrá abrir desde fuera y el diagnóstico lo
señala.

**Nunca se usa la cabecera `Host` de la petición** para esto. Si se usara,
bastaría con pedir la recuperación falsificando esa cabecera para que el
enlace apuntara al sitio de quien ataca, y quien recibiera el correo
entregaría su contraseña nueva ahí.

## Qué proveedor usar

Cualquiera que dé SMTP. Tres caminos habituales:

**Un servicio de envío** (Brevo, Mailgun, Resend, SendGrid). Tienen capa
gratuita suficiente para esto —se envían unos pocos correos al mes— y es lo
que mejor llega a la bandeja de entrada, porque gestionan reputación, SPF y
DKIM.

**Gmail con contraseña de aplicación.** Funciona y es rápido de montar, pero
exige verificación en dos pasos y crear una contraseña de aplicación; la
normal no sirve. Tiene límites de envío y Google puede bloquear el acceso si
le parece inusual.

**El SMTP de tu proveedor de correo**, si ya tienes dominio con buzones.

No monto un servidor de correo propio en Oracle: hacer que sus mensajes no
acaben en spam exige SPF, DKIM, DMARC, IP con buena reputación y DNS inverso,
que es bastante trabajo para unos pocos correos al mes.

## Qué no se registra nunca

Ni el cuerpo del mensaje ni el enlace aparecen en los logs. El enlace vale
tanto como la contraseña mientras no caduque, así que registrarlo sería
guardar la contraseña en texto plano con otro nombre.

En la auditoría queda que *se pidió* una recuperación y si el envío falló,
nunca a qué dirección ni con qué enlace.

## Comprobar que funciona de verdad

El diagnóstico revisa la configuración, no el envío. La única prueba
concluyente:

1. Asigna un correo tuyo a un usuario de prueba.
2. Pide el enlace desde la pantalla de acceso.
3. Comprueba que llega, que el enlace abre y que la contraseña nueva funciona.
4. Mira en la auditoría que quedó registrado.

Si no llega, revisa la bandeja de spam y después:

```bash
sudo journalctl -u remoteadmin --since "10 min ago" | grep -i mail
```
