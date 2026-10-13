# Instalar VEYRA en una computadora

Para quien instala. No hace falta saber de informática ni escribir
comandos.

## Lo que necesitas antes de empezar

1. **La carpeta de VEYRA**, descargada del panel y descomprimida en la
   computadora. Dentro verás `VEYRA-Instalador.exe` y dos carpetas,
   `agent` y `installer`.
2. **La credencial de instalación**, un texto que empieza por `rae_`.
   La genera quien administra el panel, en *Instalar equipo*.
3. **Que la computadora tenga internet.**

## Los pasos

1. Haz doble clic en **`VEYRA-Instalador.exe`**.
2. Comprueba que la dirección del servidor aparece ya escrita. Si no,
   pégala.
3. Pulsa **Comprobar conexión**. Debe decir que el servidor responde.
   Si falla, no sigas: revisa internet y la dirección.
4. Pega la **credencial** en su campo.
5. Pulsa **Instalar**.
6. Windows pedirá permiso con su aviso de siempre. **Acepta.** Sin eso
   no se puede instalar.
7. Espera. Tarda unos minutos, porque instala lo que necesita.
8. Cuando termine, dirá que el equipo ya aparece en el panel.

No hay que hacer nada más. VEYRA arranca solo cada vez que se enciende
la computadora.

## Si la computadora ya tenía VEYRA

El instalador lo detecta y lo dice, mostrando el identificador del
equipo. En ese caso:

- **no pide credencial**, porque no hace falta;
- el botón dice **Actualizar** en vez de Instalar;
- el equipo **sigue siendo el mismo** en el panel: conserva su
  identificador, sus grabaciones y su historial.

## Qué hacer si algo falla

**«No se pudo encontrar el servidor».** Sin internet o la dirección
está mal escrita. Comprueba que puedes abrir cualquier página web.

**«El certificado del servidor no se pudo validar».** No continúes y
avisa a quien administra el panel. Significa que la conexión no es de
fiar: puede que la dirección no sea la correcta o que algo se esté
interponiendo.

**«Esa no parece una credencial de instalación».** Debe empezar por
`rae_`. Vuelve a copiarla del panel, completa.

**Windows no muestra el aviso de permisos, o se rechaza.** Sin permisos
de administrador no se puede instalar. Hay que entrar con una cuenta
que los tenga.

**Termina pero el equipo no aparece en el panel.** Suele tardar menos
de un minuto. Si no aparece, el motivo está en este archivo, que puedes
enviar a quien administra:

```
C:\ProgramData\RemoteAdmin\logs\agent-service.log
```

## Para quien administra

### Preparar el paquete que se entrega

1. Descarga el instalador del Agent desde el panel, en *Instalar
   equipo*. Trae el Agent y la dirección del servidor ya configurada.
2. Construye el ejecutable gráfico:

   ```powershell
   .\installer\build-installer.ps1
   ```

3. Copia `installer\dist\VEYRA-Instalador.exe` dentro de la carpeta
   descomprimida del paso 1, junto a `agent\` e `installer\`.
4. Comprime todo y eso es lo que se entrega.

El `.exe` **no lleva el Agent dentro**, a propósito: ejecuta el
`installer\install-agent.ps1` de la carpeta donde esté. Así se instala
siempre el Agent del paquete firmado, y no una copia congelada dentro
del ejecutable que envejecería por su cuenta.

### La credencial

Genérala en el panel con caducidad corta y **revócala cuando el equipo
aparezca**. El equipo seguirá funcionando: a partir del alta usa su
token individual, no la credencial.

Una credencial sirve para dar de alta varios equipos, así que no la
reenvíes más de lo necesario.

### Por qué el instalador pide permisos de administrador

Registra una tarea que arranca como SYSTEM al encender la computadora
—así VEYRA funciona sin que nadie inicie sesión— y escribe la
configuración con permisos restringidos. Las dos cosas requieren
administrador.

El instalador **no elude** el aviso de Windows: lo pide con el diálogo
del sistema, y si se rechaza, no instala nada. Saltárselo sería
exactamente lo que hace el software que no debería estar en una
computadora.

### Qué viaja y qué no

La credencial no está dentro del ejecutable. Se teclea al instalar,
pasa al proceso con permisos en un archivo temporal que se sobrescribe
y se borra al leerlo —nunca como argumento, que quedaría a la vista en
la lista de procesos de toda la computadora— y después solo vive en
`C:\ProgramData\RemoteAdmin\.env`, legible únicamente por
administradores.

El token individual del equipo no lo ve el instalador en ningún
momento: lo asigna el servidor en el alta y queda en
`config\identity.json`.

### Sobre los nombres

Las carpetas, tareas y servicios siguen llamándose `RemoteAdmin`. Son
identificadores técnicos y cambiarlos rompería las computadoras ya
instaladas: dejarían de encontrar su identidad y aparecerían como
equipos nuevos. El nombre comercial VEYRA está en lo que se ve.
