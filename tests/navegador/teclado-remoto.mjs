/*
El teclado fisico de la ventana remota, en Chrome de verdad.

Lo que comprueba:

  - que al abrir la sesion el foco esta en el marco;
  - que teclear produce envios al equipo (se intercepta postKeyboard);
  - que pulsar un boton de la barra NO deja el teclado muerto, que
    era el fallo: el foco se iba al boton y escribir dejaba de llegar
    sin ninguna pista de por que;
  - que al perder el foco se SUELTAN las teclas pulsadas, para no
    dejar un Ctrl o un Shift atascado en el equipo remoto;
  - y que el aviso de "a donde va el teclado" dice la verdad.

Se usan eventos de teclado reales por el protocolo de Chrome, no
llamadas a funciones.
*/

const PUERTO_WEB = Number(process.argv[2] || 8790);
const PUERTO = Number(process.argv[3] || 9346);

const URL_PAGINA =
    `http://127.0.0.1:${PUERTO_WEB}/frontend/index.html`;

async function endpoint() {
    for (let i = 0; i < 40; i++) {
        try {
            const r = await fetch(`http://127.0.0.1:${PUERTO}/json/version`);
            const j = await r.json();
            if (j.webSocketDebuggerUrl) return j.webSocketDebuggerUrl;
        } catch { /* todavia no */ }
        await new Promise(r => setTimeout(r, 300));
    }
    throw new Error("Chrome no abrio su puerto");
}

class Sesion {
    constructor(ws) { this.ws = ws; this.n = 0; this.pend = new Map(); }

    static async abrir(url) {
        const ws = new WebSocket(url);
        await new Promise((ok, mal) => {
            ws.onopen = ok;
            ws.onerror = () => mal(new Error("sin conexion"));
        });
        const s = new Sesion(ws);
        ws.onmessage = (e) => {
            const m = JSON.parse(e.data);
            if (m.id && s.pend.has(m.id)) {
                const { ok, mal } = s.pend.get(m.id);
                s.pend.delete(m.id);
                m.error ? mal(new Error(JSON.stringify(m.error))) : ok(m.result);
            }
        };
        return s;
    }

    enviar(method, params = {}, sessionId) {
        const id = ++this.n;
        const carga = { id, method, params };
        if (sessionId) carga.sessionId = sessionId;
        this.ws.send(JSON.stringify(carga));
        return new Promise((ok, mal) => this.pend.set(id, { ok, mal }));
    }
}

const ws = await endpoint();
const raiz = await Sesion.abrir(ws);

const { targetId } = await raiz.enviar("Target.createTarget",
                                       { url: "about:blank" });
const { sessionId } = await raiz.enviar("Target.attachToTarget",
                                        { targetId, flatten: true });

await raiz.enviar("Page.enable", {}, sessionId);
await raiz.enviar("Runtime.enable", {}, sessionId);

await raiz.enviar("Emulation.setDeviceMetricsOverride", {
    width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false
}, sessionId);

await raiz.enviar("Page.navigate", { url: URL_PAGINA }, sessionId);
await new Promise(r => setTimeout(r, 2000));

async function evaluar(expresion) {
    const { result, exceptionDetails } = await raiz.enviar(
        "Runtime.evaluate",
        { expression: expresion, returnByValue: true, awaitPromise: true },
        sessionId
    );
    if (exceptionDetails) {
        throw new Error(exceptionDetails.text + " :: " +
                        (exceptionDetails.exception?.description || ""));
    }
    return result.value;
}

// Se destapa la ventana remota y se intercepta el envio de teclas:
// aqui no hay Agent, interesa QUE se habria mandado.
await evaluar(`(() => {
    document.getElementById("login-screen")?.classList.add("hidden");
    document.getElementById("app-shell")?.classList.remove("hidden");

    const caja = document.getElementById("remote-screen");
    caja.classList.remove("hidden");

    let nodo = caja.parentElement;
    while (nodo && nodo !== document.body) {
        nodo.classList.remove("hidden");
        nodo = nodo.parentElement;
    }

    const img = document.getElementById("remote-screen-image");
    img.src = "data:image/svg+xml;base64," + btoa(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
        + '<rect width="1280" height="720" fill="#246"/></svg>'
    );

    // Asignacion DIRECTA, no via window: selectedDevice se declara
    // con 'let', asi que no es una propiedad de window y ponerla ahi
    // no cambiaba nada. El manejador salia por la puerta de atras en
    // su primera linea y no se enviaba ninguna tecla.
    selectedDevice = { device_id: "eq-prueba", status: "online" };
    keyboardDeviceId = "eq-prueba";

    window.__enviadas = [];
    postKeyboard = (carga) => {
        window.__enviadas.push(carga);
        return Promise.resolve();
    };

    colocarTiradoresRemotos();
    enableRemoteKeyboard();
    document.getElementById("remote-screen-container")
        .scrollIntoView({ block: "center" });

    return true;
})()`);

await new Promise(r => setTimeout(r, 400));

async function tecla(tipo, texto, codigo, llave, modificadores) {
    await raiz.enviar("Input.dispatchKeyEvent", {
        type: tipo,
        text: texto,
        code: codigo,
        key: llave,
        windowsVirtualKeyCode: llave && llave.length === 1
            ? llave.toUpperCase().charCodeAt(0) : undefined,
        modifiers: modificadores || 0
    }, sessionId);
    await new Promise(r => setTimeout(r, 40));
}

async function raton(tipo, x, y) {
    await raiz.enviar("Input.dispatchMouseEvent", {
        type: tipo, x, y, button: "left",
        buttons: tipo === "mouseReleased" ? 0 : 1,
        clickCount: 1, pointerType: "mouse"
    }, sessionId);
    await new Promise(r => setTimeout(r, 80));
}

const ESTADO = `(() => {
    const marco = document.getElementById("remote-screen-container");
    const aviso = document.getElementById("remote-foco");
    return {
        enfocado: document.activeElement === marco
                  || marco.contains(document.activeElement),
        activo: document.activeElement
                ? (document.activeElement.id
                   || document.activeElement.tagName) : null,
        aviso: aviso ? aviso.textContent.trim() : null,
        enviadas: window.__enviadas.length,
        ultimas: window.__enviadas.slice(-4),
        pulsadas: typeof pressedRemoteKeys !== "undefined"
                  ? pressedRemoteKeys.size : null
    };
})()`;

const pasos = {};

await evaluar(`document.getElementById("remote-screen-container").focus(); true`);
await new Promise(r => setTimeout(r, 200));

pasos.alAbrir = await evaluar(ESTADO);

// --- escribir texto normal ---
await evaluar("window.__enviadas = []");
for (const c of "hola") {
    await tecla("keyDown", c, "Key" + c.toUpperCase(), c);
    await tecla("keyUp", "", "Key" + c.toUpperCase(), c);
}
pasos.texto = await evaluar(ESTADO);

// --- una combinacion con Ctrl ---
await evaluar("window.__enviadas = []");
await tecla("rawKeyDown", "", "ControlLeft", "Control", 2);
await tecla("rawKeyDown", "", "KeyC", "c", 2);
await tecla("keyUp", "", "KeyC", "c", 2);
await tecla("keyUp", "", "ControlLeft", "Control", 0);
pasos.combinacion = await evaluar(ESTADO);

// --- pulsar un boton de la barra y volver a escribir ---
const puntoBoton = await evaluar(`(() => {
    const b = document.querySelector('.vy-tecla-remota[data-teclas="win"]');
    const r = b.getBoundingClientRect();
    return { x: Math.round(r.left + r.width/2),
             y: Math.round(r.top + r.height/2) };
})()`);

await raton("mousePressed", puntoBoton.x, puntoBoton.y);
await raton("mouseReleased", puntoBoton.x, puntoBoton.y);
await new Promise(r => setTimeout(r, 400));

pasos.trasBoton = await evaluar(ESTADO);

await evaluar("window.__enviadas = []");
await tecla("keyDown", "z", "KeyZ", "z");
await tecla("keyUp", "", "KeyZ", "z");

pasos.escribirTrasBoton = await evaluar(ESTADO);

// --- teclas atascadas: se dejan pulsadas y se pierde el foco ---
await evaluar("window.__enviadas = []");
await tecla("rawKeyDown", "", "ShiftLeft", "Shift", 8);
await tecla("rawKeyDown", "", "ControlLeft", "Control", 10);

pasos.conTeclasPulsadas = await evaluar(ESTADO);

await evaluar(`document.getElementById("remote-screen-container").blur(); true`);
await new Promise(r => setTimeout(r, 300));

pasos.trasPerderFoco = await evaluar(ESTADO);

console.log(JSON.stringify(pasos, null, 1));
process.exit(0);
