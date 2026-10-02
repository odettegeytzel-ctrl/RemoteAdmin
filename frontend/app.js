let selectedDevice = null;

/* ==============================
AUTENTICACION
============================== */

// La sesión vive en una cookie HttpOnly que emite y borra el backend
// (/api/auth/login y /api/auth/logout). El navegador la envía sola en cada
// petición del mismo origen: fetch, XHR, <img src>, <video src> y descargas.
// El token NO es accesible desde JavaScript, así que aquí no se guarda ni se
// lee en ninguna parte.

let appStarted = false;

function showLoginScreen() {

document.getElementById("login-screen").classList.remove("hidden");
document.getElementById("app-shell").classList.add("hidden");

}

function showAppShell(username) {

document.getElementById("login-screen").classList.add("hidden");
document.getElementById("app-shell").classList.remove("hidden");

if (username) {
    document.getElementById("session-username").textContent = username;
}

}

function setLoginError(message) {

const error =
    document.getElementById("login-error");

if (!message) {
    error.classList.add("hidden");
    return;
}

error.textContent = message;
error.classList.remove("hidden");

}

async function handleLogin(event) {

event.preventDefault();

const button =
    document.getElementById("login-button");

const username =
    document.getElementById("login-username").value.trim();

const password =
    document.getElementById("login-password").value;

setLoginError("");
button.disabled = true;

try {

    const response =
        await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password })
        });

    const data =
        await response.json();

    if (!response.ok || data.status !== "ok") {
        setLoginError(data.message || "No se pudo iniciar sesión");
        return;
    }

    // La cookie de sesión ya vino en la respuesta del backend

    document.getElementById("login-password").value = "";

    showAppShell(data.username || username);

    startApp();

} catch (error) {

    setLoginError("No se pudo conectar con el servidor");

} finally {

    button.disabled = false;
}

}

async function handleLogout() {

try {
    // El backend borra la cookie de sesión en su respuesta
    await fetch("/api/auth/logout", {
        method: "POST"
    });
} catch (error) {
    // Si falla, la recarga siguiente devolverá al login igualmente
}

// Recarga para dejar la app en un estado limpio y volver al login
window.location.reload();

}

async function initAuth() {

document
    .getElementById("login-form")
    .addEventListener("submit", handleLogin);

document
    .getElementById("logout-button")
    .addEventListener("click", handleLogout);

try {

    // Sin token que consultar: se pregunta al backend, que valida la cookie.
    const response =
        await fetch("/api/auth/me");

    if (!response.ok) {
        showLoginScreen();
        return;
    }

    const data =
        await response.json();

    showAppShell(data.username);

    startApp();

} catch (error) {

    showLoginScreen();
}

}

let lastMouseMoveTime = 0;
let isDragging = false;
let pressedMouseButton = "left";

let cursorPollingTimer = null;
let cursorRequestPending = false;

/* ==============================
SERVIDOR
============================== */

async function checkServer() {

const statusElement =
    document.getElementById("server-status");

try {

    const response =
        await fetch("/api/health");

    if (!response.ok) {
        throw new Error("Server error");
    }

    statusElement.textContent =
        "Servidor online";

    statusElement.className =
        "px-4 py-2 rounded-full bg-emerald-50 text-emerald-700 text-sm";

} catch (error) {

    statusElement.textContent =
        "Servidor offline";

    statusElement.className =
        "px-4 py-2 rounded-full bg-red-50 text-red-700 text-sm";
}

}

/* ==============================
DISPOSITIVOS
============================== */

// Última lista recibida del servidor. Búsqueda, filtro y orden se aplican
// sobre ella en el cliente: los datos ya vienen completos en /api/devices y
// así no hace falta volver a pedirlos al teclear.
let allDevices = [];

// device_id -> true si está grabando ahora mismo
let recordingDevices = {};

// Filtro de avisos activo (se enciende desde la tarjeta "Equipos con avisos")
let showOnlyWarnings = false;

// Criterio de orden activo
let deviceSortColumn = "hostname";
let deviceSortDirection = "asc";

// Número de columnas de la tabla (para el colspan de los mensajes)
const DEVICES_TABLE_COLUMNS = 8;


function getDevicesSearchTerm() {

const input =
    document.getElementById("devices-search");

return input ? input.value.trim().toLowerCase() : "";

}


function getDevicesStatusFilter() {

const select =
    document.getElementById("devices-status-filter");

return select ? select.value : "all";

}


function getStoragePercent(device) {

// El backend envía total y libre, no el porcentaje: se calcula aquí
if (!device.storage_total) {
    return null;
}

const used =
    device.storage_total - (device.storage_free || 0);

return used / device.storage_total * 100;

}


function filterDevices(devices) {

const term =
    getDevicesSearchTerm();

const status =
    getDevicesStatusFilter();

return devices.filter(device => {

    if (status !== "all" && device.status !== status) {
        return false;
    }

    if (showOnlyWarnings && !deviceHasWarning(device)) {
        return false;
    }

    if (!term) {
        return true;
    }

    // Busca en hostname, IP y usuario
    const campos = [
        device.hostname,
        device.ip_address,
        device.username
    ];

    return campos.some(
        valor => (valor || "").toLowerCase().includes(term)
    );

});

}


function sortDevices(devices) {

const factor =
    deviceSortDirection === "asc" ? 1 : -1;

// Copia para no reordenar el array original
return [...devices].sort((a, b) => {

    let valorA;
    let valorB;

    if (deviceSortColumn === "storage_percent") {
        valorA = getStoragePercent(a);
        valorB = getStoragePercent(b);
    } else {
        valorA = a[deviceSortColumn];
        valorB = b[deviceSortColumn];
    }

    // Los equipos sin dato van siempre al final, ordene como ordene
    const vacioA = valorA === null || valorA === undefined || valorA === "";
    const vacioB = valorB === null || valorB === undefined || valorB === "";

    if (vacioA && vacioB) {
        return 0;
    }

    if (vacioA) {
        return 1;
    }

    if (vacioB) {
        return -1;
    }

    if (typeof valorA === "number" && typeof valorB === "number") {
        return (valorA - valorB) * factor;
    }

    return String(valorA).localeCompare(
        String(valorB),
        "es",
        { sensitivity: "base" }
    ) * factor;

});

}


/* ==============================
UMBRALES DE SALUD (desde /api/settings)
============================== */

// Valores de respaldo: los mismos que usa el backend si la configuración no
// es válida. Solo se usan mientras no se hayan cargado los reales, o si la
// carga falla: el Dashboard debe seguir funcionando aunque /api/settings no
// responda.
const FALLBACK_THRESHOLDS = {
    ram: { warning: 75, critical: 90 },
    disk: { warning: 75, critical: 90 }
};

let healthThresholds = {
    ram: { ...FALLBACK_THRESHOLDS.ram },
    disk: { ...FALLBACK_THRESHOLDS.disk }
};


function applyHealthThresholds(settings) {
    /*
    Vuelca los umbrales de /api/settings en healthThresholds.

    Cada valor se valida por separado: si uno es inválido o el aviso queda
    por encima del crítico, ese recurso conserva sus valores de respaldo en
    lugar de quedar en un estado incoherente.
    */

    ["ram", "disk"].forEach(recurso => {

        const warning =
            Number(settings[`${recurso}_warning_percent`]);

        const critical =
            Number(settings[`${recurso}_critical_percent`]);

        const validos =
            Number.isFinite(warning) &&
            Number.isFinite(critical) &&
            warning < critical;

        healthThresholds[recurso] =
            validos
                ? { warning, critical }
                : { ...FALLBACK_THRESHOLDS[recurso] };

    });

}


async function loadHealthThresholds() {

try {

    const response =
        await fetch("/api/settings");

    if (!response.ok) {
        throw new Error("Error cargando umbrales");
    }

    applyHealthThresholds(
        await response.json()
    );

} catch (error) {

    // Sin configuración se siguen usando los de respaldo
    console.error(
        "No se pudieron cargar los umbrales, se usan los valores por defecto:",
        error
    );
}

}


function usageClass(percent, resource) {

// RAM y disco tienen umbrales propios: un 85% de RAM es normal en Windows,
// un 85% de disco no lo es.
const limites =
    healthThresholds[resource] || FALLBACK_THRESHOLDS.ram;

if (percent >= limites.critical) {
    return "usage-critical";
}

if (percent >= limites.warning) {
    return "usage-warn";
}

return "usage-ok";

}


function usageCell(percent, detalle, resource) {

// Sin dato: el Agent aún no ha enviado la información del sistema
if (percent === null || percent === undefined) {
    return `<span class="text-slate-400">-</span>`;
}

const redondeado =
    Math.round(percent);

return `
    <div class="flex items-center gap-2" title="${detalle}">
        <div class="usage-bar">
            <div
                class="usage-bar-fill ${usageClass(redondeado, resource)}"
                style="width: ${Math.min(100, Math.max(0, redondeado))}%"
            ></div>
        </div>
        <span class="text-xs text-slate-600 w-9">${redondeado}%</span>
    </div>
`;

}


function showDevicesMessage(mensaje, esError) {

const table =
    document.getElementById("devices-table");

const color =
    esError ? "text-red-500" : "text-slate-500";

table.innerHTML = `
    <tr>
        <td
            colspan="${DEVICES_TABLE_COLUMNS}"
            class="px-6 py-10 text-center ${color}"
        >
            ${mensaje}
        </td>
    </tr>
`;

}


function updateDevicesResultInfo(mostrados, total) {

const info =
    document.getElementById("devices-result-info");

if (!info) {
    return;
}

// Solo se informa cuando hay búsqueda o filtro activos
if (mostrados === total && !showOnlyWarnings) {
    info.classList.add("hidden");
    return;
}

info.textContent =
    showOnlyWarnings
        ? `Mostrando ${mostrados} de ${total} equipos · solo con avisos de RAM o disco`
        : `Mostrando ${mostrados} de ${total} equipos`;

info.classList.remove("hidden");

}


// Cada cuánto se repasa el estado de TODOS los equipos online. Antes era en
// cada ciclo (10 s), lo que suponía una petición por equipo conectado: con
// 20 equipos, 120 peticiones por minuto solo para los puntos rojos.
const RECORDING_SWEEP_MS = 60000;

// Momento del último barrido completo
let lastRecordingSweepAt = 0;


function setRecordingIndicator(deviceId, recording) {
    /*
    Actualiza el indicador de un equipo concreto y repinta la tabla.

    Lo usan los controles de grabación y el sondeo del panel abierto, para
    que el punto rojo aparezca o desaparezca al instante sin esperar al
    siguiente barrido.
    */

    if (!deviceId) {
        return;
    }

    const anterior =
        Boolean(recordingDevices[deviceId]);

    recordingDevices[deviceId] = Boolean(recording);

    // Solo se repinta si algo cambió, para no provocar parpadeo
    if (anterior !== Boolean(recording)) {
        renderDevices();
    }

}


async function fetchRecordingIndicator(deviceId) {

try {

    const response =
        await fetch(
            `/api/devices/${deviceId}/recording/status`
        );

    if (!response.ok) {
        return null;
    }

    const data =
        await response.json();

    return Boolean(data.recording);

} catch (error) {

    // Si falla, se conserva el último valor conocido
    return null;
}

}


async function refreshRecordingIndicators(devices, force) {
    /*
    Mantiene al día los puntos de "grabando" de la tabla con pocas peticiones.

    - Barrido completo (todos los equipos online) solo cada RECORDING_SWEEP_MS,
      o cuando se pide expresamente con force.
    - En los ciclos intermedios solo se consulta a los equipos que YA se sabe
      que están grabando: son pocos y son los únicos cuyo indicador puede
      apagarse sin que nos enteremos por otra vía.
    - Los equipos offline nunca se consultan.
    - Un equipo no consultado conserva su valor anterior en lugar de perderlo.
    */

    const online =
        devices.filter(device => device.status === "online");

    const tocaBarrido =
        force === true ||
        Date.now() - lastRecordingSweepAt >= RECORDING_SWEEP_MS;

    const aConsultar =
        tocaBarrido
            ? online
            : online.filter(
                device => recordingDevices[device.device_id]
            );

    if (tocaBarrido) {
        lastRecordingSweepAt = Date.now();
    }

    if (aConsultar.length === 0) {
        return;
    }

    const resultados = {};

    await Promise.all(
        aConsultar.map(async device => {

            const estado =
                await fetchRecordingIndicator(device.device_id);

            if (estado !== null) {
                resultados[device.device_id] = estado;
            }

        })
    );

    if (tocaBarrido) {

        // Tras un barrido, los equipos online no consultados con éxito y los
        // que ya no están online dejan de considerarse grabando.
        const nuevos = {};

        online.forEach(device => {

            const id = device.device_id;

            if (id in resultados) {
                nuevos[id] = resultados[id];
            } else if (recordingDevices[id]) {
                // Falló su consulta: se conserva lo último conocido
                nuevos[id] = true;
            }

        });

        recordingDevices = nuevos;

    } else {

        // Actualización parcial: no se toca el resto del mapa
        Object.assign(recordingDevices, resultados);
    }

}


function renderDevices() {

const table =
    document.getElementById("devices-table");

if (!table) {
    return;
}

if (allDevices.length === 0) {

    showDevicesMessage(
        "No hay dispositivos registrados",
        false
    );

    updateDevicesResultInfo(0, 0);

    return;
}

const visibles =
    sortDevices(filterDevices(allDevices));

updateDevicesResultInfo(visibles.length, allDevices.length);

if (visibles.length === 0) {

    showDevicesMessage(
        `No se encontraron equipos con los filtros actuales.
         <button
            onclick="clearDevicesFilters()"
            class="ml-2 text-slate-700 underline hover:text-slate-900"
         >Quitar filtros</button>`,
        false
    );

    return;
}

table.innerHTML = "";

    visibles.forEach(device => {

        const row =
            document.createElement("tr");

        row.className =
            "hover:bg-slate-50 cursor-pointer transition";

        const statusOnline =
            device.status === "online";

        const statusBadge =
            statusOnline
                ? `
                    <span class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-50 text-emerald-700 text-xs font-medium">
                        <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
                        Online
                    </span>
                `
                : `
                    <span class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-red-50 text-red-700 text-xs font-medium">
                        <span class="w-2 h-2 rounded-full bg-red-500"></span>
                        Offline
                    </span>
                `;

        // Punto rojo parpadeante mientras el equipo está grabando
        const recordingBadge =
            recordingDevices[device.device_id]
                ? `
                    <span
                        class="recording-dot ml-2 align-middle"
                        title="Grabación en curso"
                    ></span>
                `
                : "";

        row.innerHTML = `
            <td class="px-6 py-4">
                <div>
                    <p class="font-medium text-slate-900">
                        ${device.hostname}${recordingBadge}
                    </p>

                    <p class="text-xs text-slate-500 mt-1">
                        ${device.device_id}
                    </p>
                </div>
            </td>

            <td class="px-6 py-4 text-slate-600">
                ${device.username || "-"}
            </td>

            <td class="px-6 py-4 text-slate-600">
                ${device.operating_system || "-"}
            </td>

            <td class="px-6 py-4 text-slate-600">
                ${device.ip_address || "-"}
            </td>

            <td class="px-6 py-4">
                ${usageCell(
                    device.ram_percent,
                    formatRamTooltip(device),
                    "ram"
                )}
            </td>

            <td class="px-6 py-4">
                ${usageCell(
                    getStoragePercent(device),
                    formatStorageTooltip(device),
                    "disk"
                )}
            </td>

            <td class="px-6 py-4">
                ${statusBadge}
            </td>

            <td class="px-6 py-4 text-slate-500">
                ${formatDate(device.last_seen)}
            </td>
        `;

        row.addEventListener(
            "click",
            () => showDeviceDetails(device)
        );

        table.appendChild(row);
    });

}


function formatRamTooltip(device) {

if (!device.ram_total) {
    return "Sin datos de memoria";
}

return `${formatBytes(device.ram_used)} de ${formatBytes(device.ram_total)} en uso`;

}


function formatStorageTooltip(device) {

if (!device.storage_total) {
    return "Sin datos de disco";
}

return `${formatBytes(device.storage_free)} libres de ${formatBytes(device.storage_total)}`;

}


function clearDevicesFilters() {

const input =
    document.getElementById("devices-search");

const select =
    document.getElementById("devices-status-filter");

if (input) {
    input.value = "";
}

if (select) {
    select.value = "all";
}

showOnlyWarnings = false;

const tarjeta =
    document.getElementById("card-warnings");

if (tarjeta) {
    tarjeta.classList.remove("summary-card-active");
}

renderDevices();

}


function updateSortHeaders() {

document.querySelectorAll(".sortable-header").forEach(header => {

    if (header.dataset.sort === deviceSortColumn) {
        header.dataset.direction = deviceSortDirection;
    } else {
        delete header.dataset.direction;
    }

});

}


function sortDevicesBy(column) {

// Segundo clic en la misma columna: invierte el sentido
if (deviceSortColumn === column) {
    deviceSortDirection =
        deviceSortDirection === "asc" ? "desc" : "asc";
} else {
    deviceSortColumn = column;
    deviceSortDirection = "asc";
}

updateSortHeaders();

renderDevices();

}


function initDevicesTableControls() {

const input =
    document.getElementById("devices-search");

const select =
    document.getElementById("devices-status-filter");

if (input) {
    input.addEventListener("input", renderDevices);
}

if (select) {
    select.addEventListener("change", renderDevices);
}

document.querySelectorAll(".sortable-header").forEach(header => {

    header.addEventListener(
        "click",
        () => sortDevicesBy(header.dataset.sort)
    );

});

updateSortHeaders();

}


// Momento del último /api/devices correcto. Sirve para decir en el aviso
// desde cuándo están congelados los datos que se ven.
let lastDevicesOkAt = null;


function showDevicesStaleWarning() {
    /*
    Aviso de que el último refresco falló.

    La tabla NO se toca: un fallo pasajero de red no debe borrar los datos
    ni la selección. Se avisa de que lo que se ve puede estar desactualizado
    y desde cuándo.
    */

    const aviso =
        document.getElementById("devices-stale-warning");

    const texto =
        document.getElementById("devices-stale-text");

    if (!aviso || !texto) {
        return;
    }

    let desde =
        "No se pudo actualizar la lista de equipos. Los datos mostrados pueden estar desactualizados.";

    if (lastDevicesOkAt) {

        const segundos =
            Math.floor((Date.now() - lastDevicesOkAt) / 1000);

        const antiguedad =
            segundos < 60
                ? `${segundos} s`
                : `${Math.floor(segundos / 60)} min`;

        desde =
            "No se pudo actualizar la lista de equipos. " +
            `Mostrando los últimos datos correctos (hace ${antiguedad}).`;
    }

    texto.textContent = desde;

    aviso.classList.remove("hidden");

}


function hideDevicesStaleWarning() {

const aviso =
    document.getElementById("devices-stale-warning");

if (aviso) {
    aviso.classList.add("hidden");
}

}


async function loadDevices() {

try {

    const response =
        await fetch("/api/devices");

    if (!response.ok) {
        throw new Error("Error loading devices");
    }

    allDevices =
        await response.json();

    lastDevicesOkAt = Date.now();

    // Se recuperó: desaparece el aviso
    hideDevicesStaleWarning();

    updateCounters(allDevices);

    // Indicadores de grabación antes de pintar, para que salgan a la vez
    await refreshRecordingIndicators(allDevices);

    renderDevices();

    // La ficha abierta se pone al día con estos mismos datos
    syncSelectedDevice();

} catch (error) {

    console.error(
        "Error cargando dispositivos:",
        error
    );

    // Con datos previos: se conservan y solo se avisa. Sin datos (el primer
    // intento falló), sí se muestra el error en la tabla, porque una tabla
    // vacía sin explicación sería peor.
    if (allDevices.length > 0) {
        showDevicesStaleWarning();
    } else {
        showDevicesMessage(
            "Error al cargar dispositivos",
            true
        );
    }
}

}

function updateCounters(devices) {

const total =
    devices.length;

const online =
    devices.filter(
        device => device.status === "online"
    ).length;

const offline =
    total - online;

document.getElementById(
    "total-devices"
).textContent = total;

document.getElementById(
    "online-devices"
).textContent = online;

document.getElementById(
    "offline-devices"
).textContent = offline;

updateWarningsCard(devices);

}


/* ==============================
DASHBOARD: PANELES DE MONITOREO
==============================
Todos los paneles se alimentan de datos que el ciclo YA descarga
(allDevices, las alertas del globo, recordingDevices de E4). La única
petición añadida es /api/recordings, espaciada como los indicadores de E4
para no cargar el ciclo de 10 segundos. */

// Últimas alertas recibidas por refreshAlertsBadge, reutilizadas por el panel
let lastAlerts = [];

// Resumen de grabaciones y cada cuánto se refresca
let recordingsSummary = null;
const RECORDINGS_SUMMARY_MS = 60000;
let lastRecordingsSummaryAt = 0;


function prefersReducedMotion() {

return window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

}


function animateCount(element, valor) {
    /*
    Cuenta hasta el valor nuevo en vez de saltar de golpe.

    Solo anima si el número cambió y si el usuario no pidió menos movimiento;
    en cualquier otro caso escribe el valor directamente.
    */

    if (!element) {
        return;
    }

    const anterior =
        Number(element.dataset.valor || element.textContent) || 0;

    element.dataset.valor = String(valor);

    if (anterior === valor || prefersReducedMotion()) {
        element.textContent = valor;
        return;
    }

    const pasos = 18;
    const salto = (valor - anterior) / pasos;

    let paso = 0;

    const temporizador = setInterval(() => {

        paso += 1;

        if (paso >= pasos) {
            element.textContent = valor;
            clearInterval(temporizador);
            return;
        }

        element.textContent = Math.round(anterior + salto * paso);

    }, 22);

}


function renderOverviewPanel(devices) {

const total = devices.length;

const online =
    devices.filter(d => d.status === "online").length;

const offline = total - online;

animateCount(document.getElementById("overview-total"), total);
animateCount(document.getElementById("overview-online"), online);
animateCount(document.getElementById("overview-offline"), offline);

// Anillo SVG: la circunferencia de r=56 es 2*PI*56 ≈ 351.86
const circunferencia = 351.86;

const proporcion =
    total > 0 ? online / total : 0;

const anillo =
    document.getElementById("overview-ring");

if (anillo) {
    anillo.setAttribute(
        "stroke-dasharray",
        `${(circunferencia * proporcion).toFixed(1)} ${circunferencia}`
    );
}

const barra =
    document.getElementById("overview-bar");

if (barra) {
    barra.style.width = `${Math.round(proporcion * 100)}%`;
}

const resumen =
    document.getElementById("overview-summary");

if (resumen) {

    resumen.textContent =
        total === 0
            ? "Todavía no hay equipos registrados"
            : `${Math.round(proporcion * 100)}% de los equipos están conectados`;
}

}


function healthBreakdown(devices, recurso) {
    /*
    Reparte los equipos en normal / aviso / crítico / sin datos para un
    recurso. Usa usageClass, así que respeta los umbrales configurados.
    */

    const conteo = { ok: 0, warn: 0, critical: 0, nodata: 0 };

    devices.forEach(device => {

        const valor =
            recurso === "ram"
                ? device.ram_percent
                : getStoragePercent(device);

        if (valor === null || valor === undefined) {
            conteo.nodata += 1;
            return;
        }

        const clase = usageClass(valor, recurso);

        if (clase === "usage-critical") {
            conteo.critical += 1;
        } else if (clase === "usage-warn") {
            conteo.warn += 1;
        } else {
            conteo.ok += 1;
        }

    });

    return conteo;

}


function renderHealthGroup(contenedor, conteo) {

if (!contenedor) {
    return;
}

const total =
    conteo.ok + conteo.warn + conteo.critical + conteo.nodata;

const porcentaje =
    valor => (total > 0 ? (valor / total) * 100 : 0);

const leyenda = [
    ["Normal", conteo.ok, "text-emerald-600"],
    ["Aviso", conteo.warn, "text-amber-600"],
    ["Crítico", conteo.critical, "text-red-600"],
    ["Sin datos", conteo.nodata, "text-slate-400"]
];

contenedor.innerHTML = `
    <div class="health-bar">
        <span style="width:${porcentaje(conteo.ok)}%" class="bg-emerald-500"></span>
        <span style="width:${porcentaje(conteo.warn)}%" class="bg-amber-500"></span>
        <span style="width:${porcentaje(conteo.critical)}%" class="bg-red-500"></span>
        <span style="width:${porcentaje(conteo.nodata)}%" class="bg-slate-300"></span>
    </div>

    <div class="grid grid-cols-2 gap-x-4 gap-y-1 mt-2">
        ${leyenda.map(([etiqueta, valor, color]) => `
            <div class="flex items-center justify-between text-xs">
                <span class="text-slate-500">${etiqueta}</span>
                <strong class="${color}">${valor}</strong>
            </div>
        `).join("")}
    </div>
`;

}


function renderHealthPanel(devices) {

const ram = healthBreakdown(devices, "ram");
const disco = healthBreakdown(devices, "disk");

renderHealthGroup(document.getElementById("health-ram"), ram);
renderHealthGroup(document.getElementById("health-disk"), disco);

const nota =
    document.getElementById("health-note");

if (!nota) {
    return;
}

const sinDatos =
    devices.filter(
        d => (d.ram_percent === null || d.ram_percent === undefined) &&
             getStoragePercent(d) === null
    ).length;

// Se dice la verdad sobre la antigüedad: estas métricas solo se actualizan
// cuando alguien pulsa "Actualizar información" en la ficha del equipo.
const aviso =
    "Estos valores provienen del último inventario enviado por cada Agent; " +
    "no se actualizan de forma automática.";

nota.textContent =
    sinDatos > 0
        ? `${aviso} ${sinDatos} ${sinDatos === 1 ? "equipo no ha reportado" : "equipos no han reportado"} todavía.`
        : aviso;

}


function attentionReasons(device) {
    /*
    Motivos por los que un equipo requiere atención. Devuelve una lista de
    {texto, nivel} para poder pintar varios motivos por equipo.
    */

    const motivos = [];

    if (device.status !== "online") {
        motivos.push({ texto: "Desconectado", nivel: "critical" });
    }

    const comprobar = (valor, recurso, etiqueta, critico) => {

        if (valor === null || valor === undefined) {
            return;
        }

        const clase = usageClass(valor, recurso);

        if (clase === "usage-critical") {
            motivos.push({
                texto: `${etiqueta} ${critico} al ${Math.round(valor)}%`,
                nivel: "critical"
            });
        } else if (clase === "usage-warn") {
            motivos.push({
                texto: `${etiqueta} al ${Math.round(valor)}%`,
                nivel: "warn"
            });
        }

    };

    // El adjetivo concuerda con el recurso: "RAM crítica", "Disco crítico"
    comprobar(device.ram_percent, "ram", "RAM", "crítica");
    comprobar(getStoragePercent(device), "disk", "Disco", "crítico");

    return motivos;

}


function renderAttentionPanel(devices) {

const lista =
    document.getElementById("attention-list");

const contador =
    document.getElementById("attention-count");

if (!lista) {
    return;
}

const conMotivos =
    devices
        .map(device => ({ device, motivos: attentionReasons(device) }))
        .filter(item => item.motivos.length > 0);

// Primero los que tienen algo crítico
conMotivos.sort((a, b) => {

    const critico =
        item => item.motivos.some(m => m.nivel === "critical") ? 0 : 1;

    return critico(a) - critico(b);
});

if (contador) {
    contador.textContent =
        conMotivos.length === 0
            ? ""
            : `${conMotivos.length} de ${devices.length}`;
}

if (conMotivos.length === 0) {

    lista.innerHTML = `
        <div class="px-6 py-8 text-center text-sm text-slate-500">
            ✅ Ningún equipo requiere atención ahora mismo
        </div>
    `;

    return;
}

lista.innerHTML =
    conMotivos.map(({ device, motivos }) => {

        const critico =
            motivos.some(m => m.nivel === "critical");

        const punto =
            critico ? "bg-red-500" : "bg-amber-500";

        const etiquetas =
            motivos.map(m => `
                <span class="px-2 py-0.5 rounded-full text-xs ${
                    m.nivel === "critical"
                        ? "bg-red-50 text-red-700"
                        : "bg-amber-50 text-amber-700"
                }">${m.texto}</span>
            `).join("");

        return `
            <div class="dash-row-new attention-row px-6 py-3 flex items-center gap-3 cursor-pointer hover:bg-slate-50 transition"
                 data-device-id="${device.device_id}">

                <span class="w-2.5 h-2.5 rounded-full ${punto} shrink-0"></span>

                <div class="flex-1 min-w-0">

                    <p class="text-sm font-medium text-slate-900 truncate">
                        ${device.hostname || device.device_id}
                    </p>

                    <div class="flex flex-wrap gap-1.5 mt-1">${etiquetas}</div>

                </div>

                <span class="text-xs text-slate-400 shrink-0">Ver equipo →</span>

            </div>
        `;

    }).join("");

}


function renderAlertsPanel(alerts) {

const resumen =
    document.getElementById("dash-alerts-summary");

const tipos =
    document.getElementById("dash-alerts-types");

const recientes =
    document.getElementById("dash-alerts-recent");

if (!recientes) {
    return;
}

const pendientes =
    alerts.filter(a => !a.is_read).length;

if (resumen) {
    resumen.textContent =
        alerts.length === 0
            ? "Sin alertas"
            : `${pendientes} pendientes · ${alerts.length - pendientes} leídas`;
}

// Desglose por tipo, con los iconos y colores del Bloque D
if (tipos) {

    const porTipo = {};

    alerts.forEach(a => {
        porTipo[a.type] = (porTipo[a.type] || 0) + 1;
    });

    const entradas = Object.entries(porTipo);

    tipos.innerHTML =
        entradas.length === 0
            ? `<span class="text-xs text-slate-400">Sin alertas registradas</span>`
            : entradas.map(([tipo, n]) => {

                const apariencia = alertAppearance(tipo);

                return `
                    <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-50 border border-slate-200 text-xs">
                        <span class="w-2 h-2 rounded-full ${apariencia.dot}"></span>
                        ${apariencia.icon}
                        <span class="text-slate-600">${apariencia.label || tipo}</span>
                        <strong class="text-slate-900">${n}</strong>
                    </span>
                `;

            }).join("");
}

if (alerts.length === 0) {

    recientes.innerHTML = `
        <div class="px-6 py-6 text-center text-sm text-slate-500">
            No hay alertas
        </div>
    `;

    return;
}

recientes.innerHTML =
    alerts.slice(0, 5).map(alert => {

        const apariencia = alertAppearance(alert.type);

        return `
            <div class="dash-row-new px-6 py-3 flex items-center gap-3 ${alert.is_read ? "" : "bg-slate-50"}">

                <span class="w-2 h-2 rounded-full ${apariencia.dot} shrink-0"></span>

                ${apariencia.icon ? `<span class="text-sm shrink-0">${apariencia.icon}</span>` : ""}

                <div class="flex-1 min-w-0">
                    <p class="text-sm text-slate-900 truncate">${alert.message}</p>
                    <p class="text-xs text-slate-500 mt-0.5">${formatDate(alert.created_at)}</p>
                </div>

                ${alert.is_read ? "" : `<span class="text-xs font-medium text-amber-600 shrink-0">Pendiente</span>`}

            </div>
        `;

    }).join("");

}


function renderRecordingsPanel() {

const activos =
    document.getElementById("dash-rec-active");

const total =
    document.getElementById("dash-rec-total");

const tamano =
    document.getElementById("dash-rec-size");

const recientes =
    document.getElementById("dash-rec-recent");

// Equipos grabando: dato que E4 ya mantiene, sin pedir nada al servidor
const grabando =
    Object.keys(recordingDevices).filter(id => recordingDevices[id]).length;

if (activos) {

    activos.innerHTML =
        grabando === 0
            ? `<span class="text-slate-400">Ninguno grabando</span>`
            : `<span class="inline-flex items-center gap-2 text-red-600">
                   <span class="recording-dot"></span>
                   ${grabando} ${grabando === 1 ? "equipo grabando" : "equipos grabando"}
               </span>`;
}

if (!recordingsSummary) {

    if (recientes && !recientes.innerHTML) {
        recientes.innerHTML = `
            <div class="px-6 py-6 text-center text-sm text-slate-500">
                Cargando grabaciones...
            </div>
        `;
    }

    return;
}

const lista = recordingsSummary;

animateCount(total, lista.length);

if (tamano) {

    const bytes =
        lista.reduce((suma, r) => suma + (r.size_bytes || 0), 0);

    tamano.textContent = formatBytes(bytes);
}

if (!recientes) {
    return;
}

if (lista.length === 0) {

    recientes.innerHTML = `
        <div class="px-6 py-6 text-center text-sm text-slate-500">
            Todavía no hay grabaciones
        </div>
    `;

    return;
}

recientes.innerHTML =
    lista.slice(0, 5).map(rec => `
        <div class="px-6 py-2.5 flex items-center gap-3">

            <span class="text-slate-400 shrink-0">⏺</span>

            <div class="flex-1 min-w-0">
                <p class="text-sm text-slate-900 truncate">
                    ${rec.hostname || rec.device_id}
                </p>
                <p class="text-xs text-slate-500 mt-0.5">
                    ${formatRecordingDate(rec.started_at)} · ${rec.duration_sec || 0}s
                </p>
            </div>

            <span class="text-xs text-slate-500 shrink-0">${formatBytes(rec.size_bytes)}</span>

        </div>
    `).join("");

}


function renderActivityPanel(alerts) {
    /*
    Cronología con lo que el backend SÍ registra con fecha: alertas y
    grabaciones. No se inventan otros eventos.
    */

    const contenedor =
        document.getElementById("dash-activity");

    if (!contenedor) {
        return;
    }

    const eventos = [];

    alerts.forEach(alert => {

        const apariencia = alertAppearance(alert.type);

        eventos.push({
            fecha: alert.created_at,
            icono: apariencia.icon || "•",
            punto: apariencia.dot,
            texto: alert.message
        });

    });

    (recordingsSummary || []).forEach(rec => {

        eventos.push({
            fecha: rec.started_at,
            icono: "⏺",
            punto: "bg-slate-400",
            texto: `Grabación de ${rec.hostname || rec.device_id} (${rec.duration_sec || 0}s)`
        });

    });

    eventos.sort(
        (a, b) => String(b.fecha).localeCompare(String(a.fecha))
    );

    if (eventos.length === 0) {

        contenedor.innerHTML = `
            <p class="text-sm text-slate-500 text-center py-4">
                Todavía no hay actividad registrada
            </p>
        `;

        return;
    }

    contenedor.innerHTML = `
        <div class="relative pl-5">

            <span class="absolute left-1.5 top-2 bottom-2 w-px bg-slate-200"></span>

            ${eventos.slice(0, 8).map(evento => `
                <div class="relative py-2">
                    <span class="absolute -left-[13px] top-3.5 w-2.5 h-2.5 rounded-full ${evento.punto} ring-2 ring-white"></span>
                    <p class="text-sm text-slate-800">${evento.icono} ${evento.texto}</p>
                    <p class="text-xs text-slate-500 mt-0.5">${formatDate(evento.fecha)}</p>
                </div>
            `).join("")}

        </div>
    `;

}


async function refreshRecordingsSummary(force) {
    /*
    Trae el listado de grabaciones para el panel.

    Espaciado a un minuto, igual que el barrido de indicadores de E4: el
    ciclo de 10 segundos no debe cargarse con una consulta que cambia poco.
    */

    if (!force && Date.now() - lastRecordingsSummaryAt < RECORDINGS_SUMMARY_MS) {
        return;
    }

    lastRecordingsSummaryAt = Date.now();

    try {

        const response =
            await fetch("/api/recordings");

        if (!response.ok) {
            return;
        }

        const data =
            await response.json();

        recordingsSummary = data.recordings || [];

    } catch (error) {

        // Se conserva el último resumen conocido
        console.error("Error cargando el resumen de grabaciones:", error);
    }

}


function dashboardIsVisible() {

const panels =
    document.getElementById("dashboard-panels");

return panels && !panels.classList.contains("hidden");

}


function renderDashboardPanels() {
    /*
    Repinta los paneles con los datos que el ciclo ya tiene en memoria.
    No hace ninguna petición: quien las hace es refreshDashboard.
    */

    if (!dashboardIsVisible()) {
        return;
    }

    renderOverviewPanel(allDevices);
    renderHealthPanel(allDevices);
    renderAttentionPanel(allDevices);
    renderAlertsPanel(lastAlerts);
    renderRecordingsPanel();
    renderActivityPanel(lastAlerts);

}


function initDashboardPanels() {

// Un clic en un equipo con aviso abre su ficha en Dispositivos
const lista =
    document.getElementById("attention-list");

if (lista) {

    lista.addEventListener("click", evento => {

        const fila =
            evento.target.closest(".attention-row");

        if (!fila) {
            return;
        }

        openDeviceFromDashboard(fila.dataset.deviceId);

    });

}

}


function openDeviceFromDashboard(deviceId) {
    /*
    Lleva a Dispositivos y abre la ficha de ese equipo.

    Se busca en allDevices, que es la misma lista que usa la tabla, así que
    la ficha recibe exactamente los mismos datos que si se hubiera pulsado
    la fila.
    */

    showView("devices");

    const device =
        allDevices.find(d => d.device_id === deviceId);

    if (!device) {
        return;
    }

    renderDevices();

    showDeviceDetails(device);

    const ficha =
        document.getElementById("device-details");

    if (ficha) {
        ficha.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

}


/* ==============================
RESUMEN: TARJETAS DEL DASHBOARD
============================== */

function countResourceWarnings(devices) {
    /*
    Clasifica los equipos en tres grupos:
      - warnings: RAM o disco en nivel crítico (mismo umbral que las barras
        de la tabla, vía usageClass)
      - noData:   sin inventario de RAM ni de disco. Es informativo, NO es un
        aviso: no saber cómo está un equipo no es lo mismo que saber que está
        mal.
      - resto:    con datos y dentro de lo normal
    */

    let warnings = 0;
    let noData = 0;

    devices.forEach(device => {

        const ram =
            device.ram_percent;

        const disco =
            getStoragePercent(device);

        const sinRam =
            ram === null || ram === undefined;

        const sinDisco =
            disco === null || disco === undefined;

        if (sinRam && sinDisco) {
            noData += 1;
            return;
        }

        // Mismo criterio que la alerta del backend y que el color de la
        // barra: cuenta desde el umbral de AVISO, no solo desde el crítico.
        // Basta con que uno de los dos recursos lo supere.
        const conAviso =
            (!sinRam && usageClass(ram, "ram") !== "usage-ok") ||
            (!sinDisco && usageClass(disco, "disk") !== "usage-ok");

        if (conAviso) {
            warnings += 1;
        }

    });

    return { warnings, noData };

}


function updateWarningsCard(devices) {

const contador =
    document.getElementById("warning-devices");

const sinDatos =
    document.getElementById("nodata-devices");

if (!contador) {
    return;
}

const resumen =
    countResourceWarnings(devices);

contador.textContent =
    resumen.warnings;

contador.className =
    resumen.warnings > 0
        ? "block text-3xl font-semibold mt-3 text-red-600"
        : "block text-3xl font-semibold mt-3";

if (!sinDatos) {
    return;
}

if (resumen.noData === 0) {

    sinDatos.classList.add("hidden");

} else {

    sinDatos.textContent =
        resumen.noData === 1
            ? "1 equipo sin datos de RAM/disco"
            : `${resumen.noData} equipos sin datos de RAM/disco`;

    sinDatos.classList.remove("hidden");
}

}


function updatePendingAlertsCard(alerts) {

const contador =
    document.getElementById("pending-alerts");

if (!contador) {
    return;
}

const pendientes =
    alerts.filter(alert => !alert.is_read).length;

contador.textContent =
    pendientes;

contador.className =
    pendientes > 0
        ? "block text-3xl font-semibold mt-3 text-amber-600"
        : "block text-3xl font-semibold mt-3";

}


// Momento del último refresco correcto, para la etiqueta "hace X"
let lastRefreshAt = null;


function updateLastRefreshLabel() {

const etiqueta =
    document.getElementById("last-refresh");

if (!etiqueta) {
    return;
}

if (!lastRefreshAt) {
    etiqueta.textContent = "Actualizando...";
    return;
}

const segundos =
    Math.floor((Date.now() - lastRefreshAt) / 1000);

if (segundos < 5) {
    etiqueta.textContent = "Actualizado ahora mismo";
} else if (segundos < 60) {
    etiqueta.textContent = `Actualizado hace ${segundos} s`;
} else {

    const minutos =
        Math.floor(segundos / 60);

    etiqueta.textContent =
        minutos === 1
            ? "Actualizado hace 1 minuto"
            : `Actualizado hace ${minutos} minutos`;
}

}


function applyStatusFilterFromCard(status) {
    /*
    La tabla vive solo en Dispositivos, así que la tarjeta lleva allí con el
    filtro ya aplicado en lugar de filtrar algo que no está a la vista.
    */

    const select =
        document.getElementById("devices-status-filter");

    if (!select) {
        return;
    }

    // Reutiliza el filtro de la tabla del Bloque B
    select.value = status;

    showView("devices");

    renderDevices();

    document.getElementById(
        "devices-section"
    ).scrollIntoView({ behavior: "smooth", block: "nearest" });

}


function initSummaryCards() {

const acciones = [
    ["card-total", () => applyStatusFilterFromCard("all")],
    ["card-online", () => applyStatusFilterFromCard("online")],
    ["card-offline", () => applyStatusFilterFromCard("offline")],
    ["card-alerts", () => showView("alerts")],
    ["card-warnings", () => showWarningDevices()]
];

acciones.forEach(([id, accion]) => {

    const tarjeta =
        document.getElementById(id);

    if (tarjeta) {
        tarjeta.addEventListener("click", accion);
    }

});

const boton =
    document.getElementById("refresh-now-button");

if (boton) {

    boton.addEventListener("click", async () => {

        boton.disabled = true;
        boton.textContent = "Actualizando...";

        await refreshDashboard();

        boton.disabled = false;
        boton.textContent = "Actualizar ahora";

    });

}

// La etiqueta se recalcula sola para que el "hace X" no se quede congelado
setInterval(updateLastRefreshLabel, 5000);

}


function showWarningDevices() {

// Alterna el filtro de avisos: un segundo clic en la tarjeta lo quita.
showOnlyWarnings = !showOnlyWarnings;

// La tabla está en Dispositivos: se navega allí para poder verla
if (showOnlyWarnings) {
    showView("devices");
}

const select =
    document.getElementById("devices-status-filter");

if (select && showOnlyWarnings) {
    // El aviso puede darse en equipos online u offline
    select.value = "all";
}

const tarjeta =
    document.getElementById("card-warnings");

if (tarjeta) {
    tarjeta.classList.toggle("summary-card-active", showOnlyWarnings);
}

renderDevices();

document.getElementById(
    "devices-section"
).scrollIntoView({ behavior: "smooth", block: "nearest" });

}


function deviceHasWarning(device) {

const ram =
    device.ram_percent;

const disco =
    getStoragePercent(device);

// Aviso o crítico, igual que countResourceWarnings y que las alertas:
// si la barra no está verde, el equipo tiene algo que mirar.
const ramConAviso =
    ram !== null && ram !== undefined &&
    usageClass(ram, "ram") !== "usage-ok";

const discoConAviso =
    disco !== null && disco !== undefined &&
    usageClass(disco, "disk") !== "usage-ok";

return ramConAviso || discoConAviso;

}

/* ==============================
FORMATOS
============================== */

function formatDate(dateString) {

if (!dateString) {
    return "-";
}

const date =
    new Date(dateString);

return date.toLocaleString(
    "es-MX"
);

}

function formatBytes(bytes) {

if (!bytes) {
    return "Sin información";
}

const gigabytes =
    bytes / (1024 ** 3);

return `${gigabytes.toFixed(1)} GB`;

}

function formatInstallDate(date) {

if (!date) {
    return "Sin información";
}

if (
    date.length === 8 &&
    /^\d{8}$/.test(date)
) {
    return `${date.substring(6, 8)}/${date.substring(4, 6)}/${date.substring(0, 4)}`;
}

return date;

}

/* ==============================
DETALLES DEL DISPOSITIVO
============================== */

function syncSelectedDevice() {
    /*
    Mantiene la ficha abierta al día con los datos recién cargados.

    selectedDevice era una foto del momento del clic: cuando llegaba una
    actualización de /api/devices, la ficha seguía mostrando RAM, IP y sobre
    todo el estado online/offline antiguos, y los botones de acción se
    habilitaban según ese estado caducado.

    Solo se refrescan los campos y los botones. Alertas, grabaciones y
    software NO se recargan: son del mismo equipo y no han cambiado, así que
    volver a pedirlos provocaría parpadeo y peticiones de más.
    */

    if (!selectedDevice) {
        return;
    }

    const actualizado =
        allDevices.find(
            device => device.device_id === selectedDevice.device_id
        );

    // El equipo ya no está en la lista: se cierra la ficha en lugar de
    // dejarla mostrando datos de algo que ya no existe.
    if (!actualizado) {
        closeDeviceDetails();
        return;
    }

    const cambioEstado =
        actualizado.status !== selectedDevice.status;

    selectedDevice = actualizado;

    renderDeviceInfoFields(actualizado);

    // Las acciones que necesitan conexión se rehabilitan o bloquean según el
    // estado nuevo. Se llama siempre porque updateActionButtons lee
    // selectedDevice, que acaba de cambiar de objeto.
    updateActionButtons();

    if (cambioEstado && actualizado.status !== "online") {
        // Al desconectarse se detiene el escritorio remoto, que ya no puede
        // recibir nada del Agent.
        stopRemoteDesktop();
    }

}


function renderDeviceInfoFields(device) {
    /*
    Solo vuelca los datos del equipo en la ficha.

    Separado de showDeviceDetails para poder refrescar la información sin
    recargar alertas, grabaciones ni software, que no cambian por un simple
    ciclo de /api/devices.
    */

document.getElementById(
    "detail-hostname"
).textContent =
    device.hostname || "Sin información";

document.getElementById(
    "detail-username"
).textContent =
    device.username || "Sin información";

document.getElementById(
    "detail-os"
).textContent =
    device.operating_system || "Sin información";

document.getElementById(
    "detail-ip"
).textContent =
    device.ip_address || "Sin información";

document.getElementById(
    "detail-processor"
).textContent =
    device.processor || "Sin información";

document.getElementById(
    "detail-cpu"
).textContent =
    device.cpu_count !== null &&
    device.cpu_count !== undefined
        ? `${device.cpu_count} núcleos`
        : "Sin información";

document.getElementById(
    "detail-windows-version"
).textContent =
    device.windows_version || "Sin información";

document.getElementById(
    "detail-architecture"
).textContent =
    device.architecture || "Sin información";

document.getElementById(
    "detail-manufacturer"
).textContent =
    device.manufacturer || "Sin información";

document.getElementById(
    "detail-model"
).textContent =
    device.model || "Sin información";

document.getElementById(
    "detail-ram"
).textContent =
    formatBytes(device.ram_total);

document.getElementById(
    "detail-ram-usage"
).textContent =
    device.ram_percent !== null &&
    device.ram_percent !== undefined
        ? `${device.ram_percent}% utilizada`
        : "Sin información";

document.getElementById(
    "detail-storage"
).textContent =
    formatBytes(device.storage_total);

document.getElementById(
    "detail-storage-free"
).textContent =
    device.storage_free !== null &&
    device.storage_free !== undefined
        ? `${formatBytes(device.storage_free)} libres`
        : "Sin información";

document.getElementById(
    "detail-status"
).textContent =
    device.status || "Sin información";

document.getElementById(
    "detail-last-seen"
).textContent =
    formatDate(device.last_seen);

}


function showDeviceDetails(device) {

selectedDevice = device;

renderDeviceInfoFields(device);

document.getElementById(
    "device-details"
).classList.remove("hidden");

loadInstalledSoftware(
    device.device_id
);

// Alertas y grabaciones de ESTE equipo
loadDeviceAlerts(
    device.device_id
);

loadDeviceRecordings(
    device.device_id
);

updateActionButtons();

// Nuevo dispositivo: se fuerza reaplicar el estado de los botones
lastRecordingUIState = null;
lastContinuousEnabled = null;

refreshRecordingStatus();

refreshContinuousStatus();

startRecordingStatusPolling();

}

/* ==============================
APARIENCIA DE LAS ALERTAS
============================== */

// Cómo se representa cada tipo de alerta. El color sigue el mismo semáforo
// que las barras de la tabla (verde bien, ámbar aviso, rojo crítico), para
// que la lista de alertas y el estado de los equipos concuerden.
const ALERT_APPEARANCE = {
    online:         { dot: "bg-emerald-500", icon: "🟢", label: "Conexión" },
    offline:        { dot: "bg-red-500",     icon: "🔴", label: "Desconexión" },
    ram_warning:    { dot: "bg-amber-500",   icon: "⚠️", label: "RAM" },
    ram_critical:   { dot: "bg-red-500",     icon: "🔥", label: "RAM crítica" },
    disk_warning:   { dot: "bg-amber-500",   icon: "⚠️", label: "Disco" },
    disk_critical:  { dot: "bg-red-500",     icon: "🔥", label: "Disco crítico" },
    ram_recovered:  { dot: "bg-emerald-500", icon: "✅", label: "RAM recuperada" },
    disk_recovered: { dot: "bg-emerald-500", icon: "✅", label: "Disco recuperado" }
};

// Para un tipo no previsto: punto gris y sin icono. Así un tipo futuro no
// rompe la interfaz ni se disfraza de error.
const UNKNOWN_ALERT_APPEARANCE = {
    dot: "bg-slate-400",
    icon: "",
    label: ""
};


function alertAppearance(type) {

return ALERT_APPEARANCE[type] || UNKNOWN_ALERT_APPEARANCE;

}


/* ==============================
FICHA DEL EQUIPO: alertas y grabaciones propias
============================== */

// Cuántos elementos se muestran de cada bloque dentro de la ficha
const DEVICE_PANEL_LIMIT = 8;


function devicePanelMessage(text) {

return `
    <div class="px-5 py-6 text-center text-sm text-slate-500">
        ${text}
    </div>
`;

}


async function loadDeviceAlerts(deviceId) {

const list =
    document.getElementById("device-alerts-list");

const counter =
    document.getElementById("device-alerts-count");

if (!list) {
    return;
}

list.innerHTML =
    devicePanelMessage("Cargando alertas...");

try {

    // Mismo endpoint que la sección de Alertas; el filtrado por equipo se
    // hace aquí porque /api/alerts las devuelve todas.
    const response =
        await fetch("/api/alerts");

    if (!response.ok) {
        throw new Error("Error loading alerts");
    }

    const alerts =
        await response.json();

    // El equipo puede haber cambiado mientras llegaba la respuesta
    if (!selectedDevice || selectedDevice.device_id !== deviceId) {
        return;
    }

    const propias =
        alerts.filter(
            alert => alert.device_id === deviceId
        );

    // Más recientes primero
    propias.sort(
        (a, b) => String(b.created_at).localeCompare(String(a.created_at))
    );

    const pendientes =
        propias.filter(alert => !alert.is_read).length;

    if (counter) {
        counter.textContent =
            propias.length === 0
                ? ""
                : `${propias.length} en total · ${pendientes} sin leer`;
    }

    if (propias.length === 0) {

        list.innerHTML =
            devicePanelMessage("Este equipo no tiene alertas");

        return;
    }

    list.innerHTML =
        propias.slice(0, DEVICE_PANEL_LIMIT).map(alert => {

            const apariencia =
                alertAppearance(alert.type);

            const unread =
                alert.is_read ? "" : "bg-slate-50";

            const estado =
                alert.is_read
                    ? `<span class="text-xs text-slate-400">Leída</span>`
                    : `<span class="text-xs font-medium text-amber-600">Pendiente</span>`;

            // En la ficha se omite la etiqueta de categoría: el espacio es
            // estrecho y el icono ya distingue el tipo.
            const icono =
                apariencia.icon
                    ? `<span class="text-sm shrink-0" title="${apariencia.label}">${apariencia.icon}</span>`
                    : "";

            return `
                <div class="px-5 py-3 flex items-center gap-3 ${unread}">

                    <span class="w-2 h-2 rounded-full ${apariencia.dot} shrink-0"></span>

                    ${icono}

                    <div class="flex-1 min-w-0">

                        <p class="text-sm text-slate-900 truncate">
                            ${alert.message}
                        </p>

                        <p class="text-xs text-slate-500 mt-1">
                            ${formatDate(alert.created_at)}
                        </p>

                    </div>

                    ${estado}

                </div>
            `;

        }).join("");

} catch (error) {

    console.error(
        "Error cargando alertas del equipo:",
        error
    );

    list.innerHTML =
        devicePanelMessage("No se pudieron cargar las alertas");
}

}


async function loadDeviceRecordings(deviceId) {

const list =
    document.getElementById("device-recordings-list");

const counter =
    document.getElementById("device-recordings-count");

if (!list) {
    return;
}

list.innerHTML =
    devicePanelMessage("Cargando grabaciones...");

try {

    // /api/recordings ya admite filtrar por dispositivo
    const response =
        await fetch(
            `/api/recordings?device_id=${encodeURIComponent(deviceId)}`
        );

    if (!response.ok) {
        throw new Error("Error loading recordings");
    }

    const data =
        await response.json();

    if (!selectedDevice || selectedDevice.device_id !== deviceId) {
        return;
    }

    const recordings =
        (data.recordings || []).filter(
            rec => rec.device_id === deviceId
        );

    if (counter) {
        counter.textContent =
            recordings.length === 0
                ? ""
                : `${recordings.length} en total`;
    }

    if (recordings.length === 0) {

        list.innerHTML =
            devicePanelMessage("Este equipo no tiene grabaciones");

        return;
    }

    list.innerHTML =
        recordings.slice(0, DEVICE_PANEL_LIMIT).map(rec => {

            const conservada =
                rec.keep
                    ? `<span class="text-xs text-amber-600" title="Protegida de la retención">★</span>`
                    : "";

            return `
                <div class="px-5 py-3 flex items-center gap-3">

                    <div class="flex-1 min-w-0">

                        <p class="text-sm text-slate-900">
                            ${formatRecordingDate(rec.started_at)} ${conservada}
                        </p>

                        <p class="text-xs text-slate-500 mt-1">
                            ${rec.duration_sec || 0}s · ${formatBytes(rec.size_bytes)}
                        </p>

                    </div>

                    <button
                        onclick="openRecordingFromDevice(${rec.id})"
                        class="px-3 py-1 rounded-lg bg-slate-900 text-white text-xs hover:bg-slate-700 transition"
                    >
                        Ver
                    </button>

                    <a
                        href="/api/recordings/${rec.id}/download"
                        class="px-3 py-1 rounded-lg bg-slate-100 text-slate-700 text-xs hover:bg-slate-200 transition"
                    >
                        Descargar
                    </a>

                </div>
            `;

        }).join("");

} catch (error) {

    console.error(
        "Error cargando grabaciones del equipo:",
        error
    );

    list.innerHTML =
        devicePanelMessage("No se pudieron cargar las grabaciones");
}

}


function openRecordingFromDevice(id) {

// El reproductor vive en la sección de Grabaciones: hay que mostrarla antes
// de reproducir, o el vídeo se cargaría en un panel oculto.
showView("recordings");

playRecording(id);

}


/* Sondeo del estado mientras el panel del dispositivo está abierto:
   el Agent reporta de forma asíncrona (al conectar, al cerrar/subir un
   segmento), así que hay que refrescar para reflejar el estado en vivo. */
let recordingStatusPollTimer = null;

function startRecordingStatusPolling() {

stopRecordingStatusPolling();

recordingStatusPollTimer = setInterval(() => {
    if (!selectedDevice) {
        stopRecordingStatusPolling();
        return;
    }
    refreshContinuousStatus();
    refreshRecordingStatus();
}, 5000);

}

function stopRecordingStatusPolling() {

if (recordingStatusPollTimer) {
    clearInterval(recordingStatusPollTimer);
    recordingStatusPollTimer = null;
}

}

/* ==============================
GRABACIÓN CONTINUA (cámara de seguridad)
============================== */

// Cache del último flag aplicado a los botones de continua (evita parpadeo)
let lastContinuousEnabled = null;

function formatContinuousTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function refreshContinuousStatus() {

if (!selectedDevice) {
    return;
}

try {

    const response =
        await fetch(`/api/devices/${selectedDevice.device_id}/recording/continuous`);

    if (!response.ok) {
        return;
    }

    const data =
        await response.json();

    if (data.status !== "ok") {
        return;
    }

    const enabled = Boolean(data.continuous_recording_enabled);

    const stateEl = document.getElementById("continuous-state");
    const stateText = enabled ? "ACTIVADA" : "DESACTIVADA";

    // Solo escribe si cambió (evita reflow/parpadeo en cada ciclo del polling)
    if (stateEl.textContent !== stateText) {
        stateEl.textContent = stateText;
        stateEl.className =
            "font-semibold " + (enabled ? "text-emerald-600" : "text-slate-500");
    }

    // Estado: prioriza lo reportado por el Agent, con etiquetas en español
    const STATE_LABELS = {
        recording: "GRABANDO",
        uploading: "SUBIENDO",
        offline: "OFFLINE",
        error: "ERROR",
        idle: "INACTIVO"
    };

    let state;
    if (!data.online) {
        // El Agent no está conectado
        state = "OFFLINE";
    } else if (data.state) {
        state = STATE_LABELS[data.state] || String(data.state).toUpperCase();
    } else if (data.recording) {
        state = "GRABANDO";
    } else {
        // Conectado pero el Agent aún no ha reportado su estado
        state = "SIN DATOS";
    }

    // Textos de estado (spans, no botones): actualización idempotente
    setTextIfChanged("continuous-status", state);
    setTextIfChanged("continuous-last-segment", formatContinuousTime(data.last_segment));
    setTextIfChanged("continuous-last-upload", formatContinuousTime(data.last_upload));
    setTextIfChanged(
        "continuous-pending",
        (data.pending_uploads === undefined || data.pending_uploads === null)
            ? "—" : String(data.pending_uploads)
    );

    // Botones: solo se togglean cuando cambia el flag (no en cada ciclo)
    if (lastContinuousEnabled !== enabled) {
        lastContinuousEnabled = enabled;
        document.getElementById("continuous-enable-button")
            .classList.toggle("hidden", enabled);
        document.getElementById("continuous-disable-button")
            .classList.toggle("hidden", !enabled);
    }

} catch (error) {
    // Si falla, se deja lo mostrado
}

}

function setTextIfChanged(id, text) {
    const el = document.getElementById(id);
    if (el && el.textContent !== text) {
        el.textContent = text;
    }
}

async function setContinuous(enabled) {

if (!selectedDevice) {
    return;
}

const statusElement =
    document.getElementById("action-status");

statusElement.classList.remove("hidden");
statusElement.textContent =
    enabled ? "Activando grabación continua..." : "Desactivando grabación continua...";
statusElement.className =
    "px-4 py-3 rounded-lg text-sm bg-slate-100 text-slate-600";

try {

    const response =
        await fetch(`/api/devices/${selectedDevice.device_id}/recording/continuous`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled })
        });

    const result =
        await response.json();

    if (response.ok && result.status === "ok") {
        statusElement.textContent =
            enabled ? "Grabación continua activada" : "Grabación continua desactivada";
        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-emerald-50 text-emerald-700";
    } else {
        statusElement.textContent = result.message || "No fue posible cambiar la configuración";
        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
    }

    // Da tiempo al Agent a reportar y refresca
    setTimeout(refreshContinuousStatus, 1500);
    setTimeout(refreshRecordingStatus, 1500);

} catch (error) {

    statusElement.textContent = "Error al comunicarse con el servidor";
    statusElement.className =
        "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
}

}

/* ==============================
GRABACION DEL DISPOSITIVO
============================== */

// Cache del último estado aplicado a los botones: el polling NO reescribe el
// DOM de los controles si el estado no cambió (evita parpadeo e interferencia
// con el clic del usuario).
let lastRecordingUIState = null;

function setRecordingUI(recording) {

recording = Boolean(recording);

if (lastRecordingUIState === recording) {
    return;
}

const startButton =
    document.getElementById("start-recording-button");

const stopButton =
    document.getElementById("stop-recording-button");

const indicator =
    document.getElementById("recording-indicator");

if (!startButton || !stopButton || !indicator) {
    return;
}

lastRecordingUIState = recording;

startButton.classList.toggle("hidden", recording);
stopButton.classList.toggle("hidden", !recording);
indicator.classList.toggle("hidden", !recording);

}

async function refreshRecordingStatus() {

if (!selectedDevice) {
    return;
}

try {

    const response =
        await fetch(
            `/api/devices/${selectedDevice.device_id}/recording/status`
        );

    if (!response.ok) {
        return;
    }

    const data =
        await response.json();

    // Solo se actualizan los botones si el estado cambió (sin parpadeo)
    setRecordingUI(Boolean(data.recording));

    // La misma respuesta sirve para el punto de la tabla: el equipo abierto
    // queda siempre al día sin pedir nada adicional.
    setRecordingIndicator(
        selectedDevice.device_id,
        Boolean(data.recording)
    );

} catch (error) {

    // Si falla la consulta, se deja lo mostrado
}

}

async function startRecording() {

if (!selectedDevice) {
    return;
}

const deviceId =
    selectedDevice.device_id;

const statusElement =
    document.getElementById("action-status");

statusElement.classList.remove("hidden");

statusElement.textContent =
    "Iniciando grabación...";

statusElement.className =
    "px-4 py-3 rounded-lg text-sm bg-slate-100 text-slate-600";

try {

    const response =
        await fetch(
            `/api/devices/${deviceId}/recording/start`,
            { method: "POST" }
        );

    const result =
        await response.json();

    if (result.status === "sent") {

        statusElement.textContent =
            `Grabación iniciada en ${selectedDevice.hostname}`;

        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-emerald-50 text-emerald-700";

        setRecordingUI(true);

        // El punto de la tabla se enciende sin esperar al barrido
        setRecordingIndicator(selectedDevice.device_id, true);

        // Confirma el estado real reportado por el Agent
        setTimeout(refreshRecordingStatus, 1500);

    } else {

        statusElement.textContent =
            result.message ||
            "No fue posible iniciar la grabación";

        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
    }

} catch (error) {

    statusElement.textContent =
        "Error al comunicarse con el servidor";

    statusElement.className =
        "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
}

}

async function stopRecording() {

if (!selectedDevice) {
    return;
}

const deviceId =
    selectedDevice.device_id;

const statusElement =
    document.getElementById("action-status");

statusElement.classList.remove("hidden");

statusElement.textContent =
    "Deteniendo grabación...";

statusElement.className =
    "px-4 py-3 rounded-lg text-sm bg-slate-100 text-slate-600";

try {

    const response =
        await fetch(
            `/api/devices/${deviceId}/recording/stop`,
            { method: "POST" }
        );

    const result =
        await response.json();

    if (result.status === "sent") {

        statusElement.textContent =
            "Grabación detenida";

        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-emerald-50 text-emerald-700";

        setRecordingUI(false);

        // El punto de la tabla se apaga sin esperar al barrido
        setRecordingIndicator(selectedDevice.device_id, false);

        setTimeout(refreshRecordingStatus, 1500);

    } else {

        statusElement.textContent =
            result.message ||
            "No fue posible detener la grabación";

        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
    }

} catch (error) {

    statusElement.textContent =
        "Error al comunicarse con el servidor";

    statusElement.className =
        "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
}

}

/* ==============================
SOFTWARE
============================== */

async function loadInstalledSoftware(deviceId) {

const softwareTable =
    document.getElementById(
        "software-table"
    );

const softwareCount =
    document.getElementById(
        "software-count"
    );

if (!softwareTable || !softwareCount) {
    return;
}

softwareTable.innerHTML = `
    <tr>
        <td
            colspan="4"
            class="px-5 py-6 text-center text-slate-500"
        >
            Cargando software...
        </td>
    </tr>
`;

try {

    const response =
        await fetch(
            `/api/devices/${deviceId}/software`
        );

    if (!response.ok) {
        throw new Error(
            "No se pudo obtener el software"
        );
    }

    const software =
        await response.json();

    softwareCount.textContent =
        `${software.length} programas`;

    if (software.length === 0) {

        softwareTable.innerHTML = `
            <tr>
                <td
                    colspan="4"
                    class="px-5 py-6 text-center text-slate-500"
                >
                    No se encontró software instalado
                </td>
            </tr>
        `;

        return;
    }

    softwareTable.innerHTML =
        software.map(item => `
            <tr class="hover:bg-slate-50">

                <td class="px-5 py-3 font-medium text-slate-900">
                    ${item.name || "Sin información"}
                </td>

                <td class="px-5 py-3 text-slate-600">
                    ${item.version || "Sin información"}
                </td>

                <td class="px-5 py-3 text-slate-600">
                    ${item.publisher || "Sin información"}
                </td>

                <td class="px-5 py-3 text-slate-600">
                    ${formatInstallDate(item.install_date)}
                </td>

            </tr>
        `).join("");

} catch (error) {

    console.error(
        "Error cargando software:",
        error
    );

    softwareCount.textContent =
        "Error";

    softwareTable.innerHTML = `
        <tr>
            <td
                colspan="4"
                class="px-5 py-6 text-center text-red-500"
            >
                No se pudo cargar el software
            </td>
        </tr>
    `;
}

}

async function requestInstalledSoftware() {

if (!selectedDevice) {
    return;
}

const deviceId =
    selectedDevice.device_id;

const statusElement =
    document.getElementById(
        "action-status"
    );

statusElement.textContent =
    "Solicitando software instalado...";

statusElement.className =
    "px-4 py-3 rounded-lg text-sm bg-slate-100 text-slate-600";

try {

    const response =
        await fetch(
            `/api/devices/${deviceId}/software`,
            {
                method: "POST"
            }
        );

    if (!response.ok) {
        throw new Error(
            "Error al solicitar software"
        );
    }

    const result =
        await response.json();

    if (result.status !== "sent") {

        statusElement.textContent =
            result.message ||
            "No fue posible solicitar el software";

        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";

        return;
    }

    statusElement.textContent =
        "El equipo está enviando la información...";

    statusElement.className =
        "px-4 py-3 rounded-lg text-sm bg-blue-50 text-blue-700";

    setTimeout(
        async () => {

            try {

                await loadInstalledSoftware(
                    deviceId
                );

                const softwareResponse =
                    await fetch(
                        `/api/devices/${deviceId}/software`
                    );

                if (!softwareResponse.ok) {
                    throw new Error(
                        "No se pudo consultar el software actualizado"
                    );
                }

                const software =
                    await softwareResponse.json();

                statusElement.textContent =
                    `Software actualizado. ${software.length} programas encontrados.`;

                statusElement.className =
                    "px-4 py-3 rounded-lg text-sm bg-emerald-50 text-emerald-700";

            } catch (error) {

                console.error(
                    "Error actualizando software:",
                    error
                );

                statusElement.textContent =
                    "El software fue solicitado, pero no se pudo actualizar la tabla.";

                statusElement.className =
                    "px-4 py-3 rounded-lg text-sm bg-amber-50 text-amber-700";
            }

        },
        2000
    );

} catch (error) {

    console.error(
        "Error solicitando software:",
        error
    );

    statusElement.textContent =
        "Error al comunicarse con el servidor";

    statusElement.className =
        "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
}

}

/* ==============================
BOTONES
============================== */

function setConnectionActionsEnabled(ids, enabled) {

ids.forEach(id => {

    const button =
        document.getElementById(id);

    if (!button) {
        return;
    }

    button.disabled = !enabled;

    // Se marca visualmente sin tocar sus clases de color, que otras
    // funciones (setRecordingUI, refreshContinuousStatus) siguen gestionando.
    button.classList.toggle("action-disabled", !enabled);

    button.title =
        enabled
            ? ""
            : "El equipo está offline";

});

}


// ==============================
// INVENTARIO EN VIVO: PROCESOS Y SERVICIOS (solo lectura)
// ==============================

// Las columnas se declaran aqui y no se generan a partir de la respuesta:
// asi el panel muestra siempre lo mismo aunque el Agent envie campos de
// mas, y no hay forma de que un dato inesperado acabe pintado en pantalla.
const INVENTORY_VIEWS = {

    processes: {
        endpoint: "processes",
        listKey: "processes",
        label: "procesos",
        columns: [
            { title: "PID", value: row => row.pid },
            { title: "Proceso", value: row => row.name },
            { title: "Usuario", value: row => row.username || "-" },
            { title: "Memoria", value: row => formatProcessMemory(row.memory_bytes) },
            { title: "Estado", value: row => row.status || "-" }
        ]
    },

    services: {
        endpoint: "services",
        listKey: "services",
        label: "servicios",
        columns: [
            { title: "Servicio", value: row => row.name },
            { title: "Nombre visible", value: row => row.display_name || "-" },
            { title: "Estado", value: row => row.status || "-" },
            { title: "Inicio", value: row => row.start_type || "-" }
        ]
    }

};


// La formatBytes general de la pagina devuelve gigabytes: para la memoria
// de un proceso, todos saldrian como "0.0 GB". Esta escala sola.
function formatProcessMemory(bytes) {

    const valor = Number(bytes) || 0;

    if (valor < 1024) {
        return valor + " B";
    }

    if (valor < 1024 * 1024) {
        return (valor / 1024).toFixed(0) + " KB";
    }

    return (valor / (1024 * 1024)).toFixed(1) + " MB";
}


function setInventoryStatus(mensaje, tono) {

    const elemento =
        document.getElementById(
            "inventory-status"
        );

    if (!elemento) {
        return;
    }

    elemento.textContent = mensaje;

    elemento.className =
        "px-5 py-3 text-sm border-b border-slate-200 "
        + (tono === "error"
            ? "text-red-600 bg-red-50"
            : "text-slate-500");
}


function renderInventory(vista, filas) {

    const cabecera =
        document.getElementById(
            "inventory-head"
        );

    const cuerpo =
        document.getElementById(
            "inventory-table"
        );

    if (!cabecera || !cuerpo) {
        return;
    }

    cabecera.innerHTML = "";
    cuerpo.innerHTML = "";

    const filaCabecera = document.createElement("tr");

    vista.columns.forEach(columna => {

        const celda = document.createElement("th");

        celda.className =
            "text-left px-5 py-3 font-semibold text-slate-600";

        celda.textContent = columna.title;

        filaCabecera.appendChild(celda);
    });

    cabecera.appendChild(filaCabecera);

    if (!filas.length) {

        const fila = document.createElement("tr");
        const celda = document.createElement("td");

        celda.colSpan = vista.columns.length;
        celda.className = "px-5 py-6 text-center text-slate-500";
        celda.textContent = "El equipo no devolvio ningun resultado";

        fila.appendChild(celda);
        cuerpo.appendChild(fila);

        return;
    }

    filas.forEach(dato => {

        const fila = document.createElement("tr");

        vista.columns.forEach(columna => {

            const celda = document.createElement("td");

            celda.className = "px-5 py-2 text-slate-700";

            // textContent, no innerHTML: el nombre de un proceso viene del
            // equipo remoto y no debe poder inyectar nada en la pagina.
            celda.textContent = String(columna.value(dato));

            fila.appendChild(celda);
        });

        cuerpo.appendChild(fila);
    });
}


async function loadInventory(tipo) {

    const vista = INVENTORY_VIEWS[tipo];

    if (!vista || !selectedDevice) {
        return;
    }

    // El device_id sale del equipo ya seleccionado en la lista que envia el
    // servidor; el backend vuelve a comprobarlo de todos modos.
    const deviceId = selectedDevice.device_id;

    setInventoryStatus("Consultando " + vista.label + "...");

    try {

        const response =
            await fetch(
                `/api/devices/${deviceId}/${vista.endpoint}`
            );

        const data = await response.json();

        if (!response.ok || data.status !== "ok") {

            setInventoryStatus(
                data.message || "No se pudo completar la consulta",
                "error"
            );

            return;
        }

        const filas = data[vista.listKey] || [];

        renderInventory(vista, filas);

        let resumen = filas.length + " " + vista.label;

        if (data.truncated) {
            resumen += " (lista recortada)";
        }

        if (data.skipped_denied) {
            resumen += " \u00b7 " + data.skipped_denied
                + " sin permiso de lectura";
        }

        if (data.skipped_gone) {
            resumen += " \u00b7 " + data.skipped_gone
                + " terminaron durante la consulta";
        }

        if (data.unavailable) {
            resumen += " \u00b7 " + data.unavailable + " no consultables";
        }

        setInventoryStatus(resumen);

    } catch (error) {

        console.error("Error consultando " + vista.label + ":", error);

        setInventoryStatus(
            "No se pudo contactar con el servidor",
            "error"
        );
    }
}


// ==============================
// CAMBIO DE CONTRASENA DEL PANEL
// ==============================

// Mismo minimo que aplica el backend (backend/auth.py). Se valida aqui para
// avisar antes de enviar, pero la comprobacion que cuenta es la del
// servidor: esta se puede saltar con cualquier cliente.
const PASSWORD_MIN_LENGTH = 12;


function setPasswordStatus(mensaje, tono) {

    const elemento =
        document.getElementById(
            "password-status"
        );

    if (!elemento) {
        return;
    }

    elemento.textContent = mensaje;

    elemento.className =
        "text-sm "
        + (tono === "error"
            ? "text-red-600"
            : tono === "ok"
                ? "text-emerald-600"
                : "text-slate-500");
}


function limpiarCamposContrasena() {

    ["password-current", "password-new", "password-repeat"].forEach(id => {

        const campo = document.getElementById(id);

        if (campo) {
            campo.value = "";
        }
    });
}


async function changePassword() {

    const actual =
        document.getElementById("password-current").value;

    const nueva =
        document.getElementById("password-new").value;

    const repetida =
        document.getElementById("password-repeat").value;

    if (!actual || !nueva) {
        setPasswordStatus(
            "Rellena la contrase\u00f1a actual y la nueva",
            "error"
        );
        return;
    }

    if (nueva !== repetida) {
        setPasswordStatus(
            "La nueva contrase\u00f1a y su repetici\u00f3n no coinciden",
            "error"
        );
        return;
    }

    if (nueva.length < PASSWORD_MIN_LENGTH) {
        setPasswordStatus(
            `La contrase\u00f1a debe tener al menos ${PASSWORD_MIN_LENGTH} caracteres`,
            "error"
        );
        return;
    }

    if (nueva === actual) {
        setPasswordStatus(
            "La nueva contrase\u00f1a debe ser distinta de la actual",
            "error"
        );
        return;
    }

    setPasswordStatus("Cambiando contrase\u00f1a...");

    try {

        const response =
            await fetch(
                "/api/auth/password",
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        current_password: actual,
                        new_password: nueva
                    })
                }
            );

        const data = await response.json();

        if (!response.ok || data.status !== "ok") {

            setPasswordStatus(
                data.message || "No se pudo cambiar la contrase\u00f1a",
                "error"
            );

            return;
        }

        // El backend reemite la cookie de ESTA sesion, asi que se sigue
        // dentro. Las demas quedan cerradas.
        limpiarCamposContrasena();

        setPasswordStatus(
            data.message
                || "Contrase\u00f1a actualizada. Las dem\u00e1s sesiones se han cerrado.",
            "ok"
        );

    } catch (error) {

        console.error("Error cambiando la contrase\u00f1a:", error);

        setPasswordStatus(
            "No se pudo contactar con el servidor",
            "error"
        );
    }
}


function updateActionButtons() {

const pingButton =
    document.getElementById(
        "ping-device-button"
    );

const systemInfoButton =
    document.getElementById(
        "system-info-button"
    );

if (!pingButton || !systemInfoButton) {
    return;
}

// Acciones que necesitan que el Agent esté conectado. Se deshabilitan
// juntas para que un equipo offline no permita lanzar peticiones que
// fallarían sin explicación.
const connectionActions = [
    "software-button",
    "processes-button",
    "services-button",
    "remote-button",
    "start-recording-button",
    "stop-recording-button",
    "continuous-enable-button",
    "continuous-disable-button",
    "file-download-button"
];

if (!selectedDevice) {

    pingButton.disabled = true;
    systemInfoButton.disabled = true;

    setConnectionActionsEnabled(connectionActions, false);

    return;
}

const isOnline =
    selectedDevice.status === "online";

pingButton.disabled =
    !isOnline;

systemInfoButton.disabled =
    !isOnline;

setConnectionActionsEnabled(connectionActions, isOnline);

if (isOnline) {

    pingButton.className =
        "px-4 py-2 rounded-lg bg-slate-900 text-white text-sm hover:bg-slate-700 transition";

    systemInfoButton.className =
        "px-4 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-500 transition";

} else {

    pingButton.className =
        "px-4 py-2 rounded-lg bg-slate-200 text-slate-400 text-sm cursor-not-allowed";

    systemInfoButton.className =
        "px-4 py-2 rounded-lg bg-slate-200 text-slate-400 text-sm cursor-not-allowed";
}

}

async function pingDevice() {

if (!selectedDevice) {
    return;
}

const deviceId =
    selectedDevice.device_id;

const statusElement =
    document.getElementById(
        "action-status"
    );

statusElement.textContent =
    "Comprobando conexión...";

statusElement.className =
    "px-4 py-3 rounded-lg text-sm bg-slate-100 text-slate-600";

try {

    const response =
        await fetch(
            `/api/devices/${deviceId}/ping`,
            {
                method: "POST"
            }
        );

    const result =
        await response.json();

    if (result.status === "sent") {

        statusElement.textContent =
            `Ping enviado correctamente a ${selectedDevice.hostname}`;

        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-emerald-50 text-emerald-700";

    } else {

        statusElement.textContent =
            result.message ||
            "No fue posible enviar el ping";

        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
    }

} catch (error) {

    statusElement.textContent =
        "Error al comunicarse con el servidor";

    statusElement.className =
        "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
}

}

/* ==============================
SISTEMA
============================== */

async function requestSystemInfo() {

if (!selectedDevice) {
    return;
}

const deviceId =
    selectedDevice.device_id;

const statusElement =
    document.getElementById(
        "action-status"
    );

statusElement.textContent =
    "Solicitando información del equipo...";

statusElement.className =
    "px-4 py-3 rounded-lg text-sm bg-slate-100 text-slate-600";

try {

    const response =
        await fetch(
            `/api/devices/${deviceId}/system-info`,
            {
                method: "POST"
            }
        );

    const result =
        await response.json();

    if (result.status !== "sent") {

        statusElement.textContent =
            result.message ||
            "No fue posible solicitar la información";

        statusElement.className =
            "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";

        return;
    }

    statusElement.textContent =
        "Esperando respuesta del equipo...";

    statusElement.className =
        "px-4 py-3 rounded-lg text-sm bg-blue-50 text-blue-700";

    let attempts = 0;
    const maxAttempts = 10;

    const checkInformation = async () => {

        attempts++;

        try {

            const devicesResponse =
                await fetch(
                    "/api/devices"
                );

            const devices =
                await devicesResponse.json();

            const updatedDevice =
                devices.find(
                    device =>
                        device.device_id === deviceId
                );

            if (
                updatedDevice &&
                updatedDevice.username &&
                updatedDevice.processor &&
                updatedDevice.cpu_count !== null
            ) {

                selectedDevice =
                    updatedDevice;

                showDeviceDetails(
                    updatedDevice
                );

                statusElement.textContent =
                    "Información del equipo actualizada.";

                statusElement.className =
                    "px-4 py-3 rounded-lg text-sm bg-emerald-50 text-emerald-700";

                return;
            }

            if (attempts < maxAttempts) {

                setTimeout(
                    checkInformation,
                    1000
                );

                return;
            }

            statusElement.textContent =
                "El equipo no respondió a tiempo.";

            statusElement.className =
                "px-4 py-3 rounded-lg text-sm bg-amber-50 text-amber-700";

        } catch (error) {

            statusElement.textContent =
                "Error al consultar la información del equipo.";

            statusElement.className =
                "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
        }
    };

    setTimeout(
        checkInformation,
        1000
    );

} catch (error) {

    statusElement.textContent =
        "Error al comunicarse con el servidor";

    statusElement.className =
        "px-4 py-3 rounded-lg text-sm bg-red-50 text-red-700";
}

}

/* ==============================
CONTROL REMOTO
============================== */

async function startRemoteDesktop() {

if (!selectedDevice) {
    return;
}

if (selectedDevice.status !== "online") {
    return;
}

const remoteScreen =
    document.getElementById(
        "remote-screen"
    );

const image =
    document.getElementById(
        "remote-screen-image"
    );

if (!remoteScreen || !image) {
    return;
}

try {

    const response =
        await fetch(
            `/api/devices/${selectedDevice.device_id}/screen`,
            {
                method: "POST"
            }
        );

    if (!response.ok) {
        throw new Error(
            "No se pudo iniciar el remoto"
        );
    }

    remoteScreen.classList.remove(
        "hidden"
    );

    image.src =
        `/api/devices/${selectedDevice.device_id}/screen-stream`;

    enableRemoteKeyboard();

    image.focus();

    startCursorPolling();

} catch (error) {

    console.error(
        "Error iniciando escritorio remoto:",
        error
    );
}

}

async function stopRemoteDesktop() {

stopCursorPolling();

disableRemoteKeyboard();

if (!selectedDevice) {
    return;
}

try {

    await fetch(
        `/api/devices/${selectedDevice.device_id}/screen/stop`,
        {
            method: "POST"
        }
    );

} catch (error) {

    console.error(
        "Error deteniendo escritorio remoto:",
        error
    );
}

const remoteScreen =
    document.getElementById(
        "remote-screen"
    );

const image =
    document.getElementById(
        "remote-screen-image"
    );

if (remoteScreen) {
    remoteScreen.classList.add(
        "hidden"
    );
}

if (image) {
    image.src = "";
}

isDragging = false;
pressedMouseButton = "left";

}

/* ==============================
CURSOR REMOTO
============================== */

function startCursorPolling() {

stopCursorPolling();

cursorPollingTimer =
    setInterval(
        updateRemoteCursor,
        100
    );

}

function stopCursorPolling() {

if (cursorPollingTimer) {

    clearInterval(
        cursorPollingTimer
    );

    cursorPollingTimer = null;
}

const cursor =
    document.getElementById(
        "remote-cursor"
    );

if (cursor) {

    cursor.classList.add(
        "hidden"
    );
}

}

async function updateRemoteCursor() {

if (
    !selectedDevice ||
    cursorRequestPending
) {
    return;
}

cursorRequestPending = true;

try {

    const response =
        await fetch(
            `/api/devices/${selectedDevice.device_id}/cursor`
        );

    if (!response.ok) {
        return;
    }

    const data =
        await response.json();

    drawRemoteCursor(data);

} catch (error) {

    console.error(
        "Error obteniendo cursor remoto:",
        error
    );

} finally {

    cursorRequestPending = false;
}

}

function getRenderedImageRect(image) {

const rect =
    image.getBoundingClientRect();

const naturalWidth =
    image.naturalWidth;

const naturalHeight =
    image.naturalHeight;

if (
    !naturalWidth ||
    !naturalHeight ||
    !rect.width ||
    !rect.height
) {
    return null;
}

const scale =
    Math.min(
        rect.width / naturalWidth,
        rect.height / naturalHeight
    );

const drawnWidth =
    naturalWidth * scale;

const drawnHeight =
    naturalHeight * scale;

return {
    left:
        rect.left +
        (rect.width - drawnWidth) / 2,

    top:
        rect.top +
        (rect.height - drawnHeight) / 2,

    width:
        drawnWidth,

    height:
        drawnHeight,

    naturalWidth:
        naturalWidth,

    naturalHeight:
        naturalHeight
};

}

function drawRemoteCursor(data) {

const cursor =
    document.getElementById(
        "remote-cursor"
    );

const image =
    document.getElementById(
        "remote-screen-image"
    );

const container =
    document.getElementById(
        "remote-screen-container"
    );

if (
    !cursor ||
    !image ||
    !container
) {
    return;
}

if (data.status !== "ok") {

    cursor.classList.add(
        "hidden"
    );

    return;
}

const imageRect =
    getRenderedImageRect(
        image
    );

if (!imageRect) {
    return;
}

const sourceWidth =
    data.width ||
    imageRect.naturalWidth;

const sourceHeight =
    data.height ||
    imageRect.naturalHeight;

if (
    !sourceWidth ||
    !sourceHeight
) {
    return;
}

const containerRect =
    container.getBoundingClientRect();

const left =
    (
        imageRect.left -
        containerRect.left
    ) +
    data.x *
    (
        imageRect.width /
        sourceWidth
    );

const top =
    (
        imageRect.top -
        containerRect.top
    ) +
    data.y *
    (
        imageRect.height /
        sourceHeight
    );

const outside =
    data.x < 0 ||
    data.y < 0 ||
    data.x > sourceWidth ||
    data.y > sourceHeight;

const shape =
    applyRemoteCursorShape(
        cursor,
        image,
        data.cursor
    );

cursor.classList.toggle(
    "hidden",
    outside ||
    shape.hidden
);

cursor.style.transform =
    `translate(${left - shape.hotspotX}px, ${top - shape.hotspotY}px)`;

}

/* Formas del cursor de Windows: viewBox, tamaño, punto activo, path y cursor CSS */
const ARROW_PATH =
    "M1 1 L1 21 L6 16 L10 25 L13 23.5 L9 15 L16 15 Z";

const RESIZE_EW_PATH =
    "M1 12 L7 6 L7 10 L17 10 L17 6 L23 12 L17 18 L17 14 L7 14 L7 18 Z";

const RESIZE_NS_PATH =
    "M12 1 L18 7 L14 7 L14 17 L18 17 L12 23 L6 17 L10 17 L10 7 L6 7 Z";

const REMOTE_CURSOR_SHAPES = {
    arrow: {
        viewBox: "0 0 18 26", width: 18, height: 26,
        hotspotX: 0, hotspotY: 0,
        path: ARROW_PATH,
        css: "default"
    },
    ew: {
        viewBox: "0 0 24 24", width: 24, height: 24,
        hotspotX: 12, hotspotY: 12,
        path: RESIZE_EW_PATH,
        css: "ew-resize"
    },
    ns: {
        viewBox: "0 0 24 24", width: 24, height: 24,
        hotspotX: 12, hotspotY: 12,
        path: RESIZE_NS_PATH,
        css: "ns-resize"
    },
    nwse: {
        viewBox: "0 0 24 24", width: 24, height: 24,
        hotspotX: 12, hotspotY: 12,
        path: RESIZE_EW_PATH,
        rotate: 45,
        css: "nwse-resize"
    },
    nesw: {
        viewBox: "0 0 24 24", width: 24, height: 24,
        hotspotX: 12, hotspotY: 12,
        path: RESIZE_EW_PATH,
        rotate: -45,
        css: "nesw-resize"
    },
    move: {
        viewBox: "0 0 24 24", width: 24, height: 24,
        hotspotX: 12, hotspotY: 12,
        path:
            "M12 1 L16 5 L13 5 L13 11 L19 11 L19 8 L23 12 L19 16 L19 13 L13 13 " +
            "L13 19 L16 19 L12 23 L8 19 L11 19 L11 13 L5 13 L5 16 L1 12 L5 8 " +
            "L5 11 L11 11 L11 5 L8 5 Z",
        css: "move"
    },
    text: {
        viewBox: "0 0 24 24", width: 24, height: 24,
        hotspotX: 12, hotspotY: 12,
        path:
            "M8 2 L16 2 L16 4 L13 4 L13 20 L16 20 L16 22 L8 22 L8 20 L11 20 " +
            "L11 4 L8 4 Z",
        css: "text"
    },
    pointer: {
        viewBox: "0 0 24 26", width: 24, height: 26,
        hotspotX: 8, hotspotY: 1,
        path:
            "M7 2 Q9 0 11 2 L11 10 L17 11 Q21 12 20 16 L18 24 L9 24 L3 15 " +
            "Q2 12 5 13 L7 15 Z",
        css: "pointer"
    },
    wait: {
        viewBox: "0 0 24 24", width: 24, height: 24,
        hotspotX: 12, hotspotY: 12,
        path:
            "M12 2 A10 10 0 1 1 11.99 2 Z M12 6 A6 6 0 1 0 12.01 6 Z",
        css: "wait"
    },
    progress: {
        viewBox: "0 0 18 26", width: 18, height: 26,
        hotspotX: 0, hotspotY: 0,
        path:
            ARROW_PATH +
            " M13 17 A4 4 0 1 1 12.99 17 Z",
        css: "progress"
    },
    "not-allowed": {
        viewBox: "0 0 24 24", width: 24, height: 24,
        hotspotX: 12, hotspotY: 12,
        path:
            "M12 2 A10 10 0 1 1 11.99 2 Z M7 5.5 L18.5 17 A7 7 0 0 0 7 5.5 Z " +
            "M5.5 7 A7 7 0 0 0 17 18.5 Z",
        css: "not-allowed"
    },
    crosshair: {
        viewBox: "0 0 24 24", width: 24, height: 24,
        hotspotX: 12, hotspotY: 12,
        path:
            "M11 1 L13 1 L13 11 L23 11 L23 13 L13 13 L13 23 L11 23 L11 13 " +
            "L1 13 L1 11 L11 11 Z",
        css: "crosshair"
    },
    help: {
        viewBox: "0 0 26 26", width: 26, height: 26,
        hotspotX: 0, hotspotY: 0,
        path:
            ARROW_PATH +
            " M18 8 Q18 4 21.5 4 Q25 4 25 7.5 Q25 10 22.5 11 L22.5 13 L20.5 13 " +
            "L20.5 10 Q23 9 23 7.5 Q23 6 21.5 6 Q20 6 20 8 Z M20.5 15 L22.5 15 L22.5 17 L20.5 17 Z",
        css: "help"
    }
};

let currentRemoteCursorType = null;

function applyRemoteCursorShape(cursor, image, type) {

const isHidden =
    type === "hidden";

const shape =
    REMOTE_CURSOR_SHAPES[type] ||
    REMOTE_CURSOR_SHAPES.arrow;

const key =
    isHidden
        ? "hidden"
        : (REMOTE_CURSOR_SHAPES[type] ? type : "arrow");

if (key !== currentRemoteCursorType) {

    currentRemoteCursorType = key;

    image.style.cursor =
        isHidden
            ? "none"
            : shape.css;

    if (!isHidden) {

        cursor.setAttribute(
            "viewBox",
            shape.viewBox
        );

        cursor.style.width =
            `${shape.width}px`;

        cursor.style.height =
            `${shape.height}px`;

        const path =
            cursor.querySelector("path");

        path.setAttribute(
            "d",
            shape.path
        );

        path.setAttribute(
            "fill-rule",
            "evenodd"
        );

        if (shape.rotate) {
            path.setAttribute(
                "transform",
                `rotate(${shape.rotate} 12 12)`
            );
        } else {
            path.removeAttribute("transform");
        }
    }
}

return {
    hidden: isHidden,
    hotspotX: shape.hotspotX,
    hotspotY: shape.hotspotY
};

}

/* ==============================
COORDENADAS DEL MOUSE
============================== */

function getRemoteCoords(event) {

const image =
    document.getElementById(
        "remote-screen-image"
    );

const imageRect =
    getRenderedImageRect(
        image
    );

if (!imageRect) {
    return null;
}

let x =
    (
        event.clientX -
        imageRect.left
    ) *
    (
        imageRect.naturalWidth /
        imageRect.width
    );

let y =
    (
        event.clientY -
        imageRect.top
    ) *
    (
        imageRect.naturalHeight /
        imageRect.height
    );

x =
    Math.max(
        0,
        Math.min(
            imageRect.naturalWidth - 1,
            x
        )
    );

y =
    Math.max(
        0,
        Math.min(
            imageRect.naturalHeight - 1,
            y
        )
    );

return {
    x: Math.round(x),
    y: Math.round(y)
};

}

function mouseButtonName(event) {

if (event.button === 2) {
    return "right";
}

if (event.button === 1) {
    return "middle";
}

return "left";

}

/* ==============================
MOUSE REMOTO
============================== */

/* Cola ordenada: los eventos llegan al agente en el mismo orden
   en que ocurrieron (down -> move... -> up) */
let mouseQueue = Promise.resolve();
let pendingMove = null;
let moveTimer = null;
let activePointerId = null;

function postMouse(action, payload) {

if (!selectedDevice) {
    return mouseQueue;
}

const deviceId =
    selectedDevice.device_id;

mouseQueue = mouseQueue
    .then(() =>
        fetch(
            `/api/devices/${deviceId}/mouse/${action}`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            }
        )
    )
    .catch(error =>
        console.error(
            `Error enviando mouse ${action}:`,
            error
        )
    );

return mouseQueue;

}

function flushPendingMove() {

if (moveTimer) {
    clearTimeout(moveTimer);
    moveTimer = null;
}

if (pendingMove) {
    postMouse("move", pendingMove);
    pendingMove = null;
}

lastMouseMoveTime =
    Date.now();

}

function queueMouseMove(coords) {

pendingMove = coords;

const elapsed =
    Date.now() - lastMouseMoveTime;

// Mas frecuencia durante el arrastre para trazos suaves
const interval =
    isDragging ? 16 : 40;

if (elapsed >= interval) {
    flushPendingMove();
} else if (!moveTimer) {
    // Nunca se pierde el ultimo movimiento
    moveTimer = setTimeout(
        flushPendingMove,
        interval - elapsed
    );
}

}

function onPointerMove(event) {

if (!selectedDevice) {
    return;
}

if (
    isDragging &&
    event.pointerId !== activePointerId
) {
    return;
}

const events =
    event.getCoalescedEvents
        ? event.getCoalescedEvents()
        : [];

const last =
    events.length
        ? events[events.length - 1]
        : event;

const coords =
    getRemoteCoords(last);

if (coords) {
    queueMouseMove(coords);
}

}

function onPointerDown(event) {

if (!selectedDevice || isDragging) {
    return;
}

const image =
    event.currentTarget;

const coords =
    getRemoteCoords(event);

if (!coords) {
    return;
}

event.preventDefault();

image.focus();

// Recibe los eventos aunque el puntero salga de la imagen
image.setPointerCapture(
    event.pointerId
);

activePointerId =
    event.pointerId;

pressedMouseButton =
    mouseButtonName(event);

isDragging = true;

// Descarta movimientos previos y envia la posicion exacta del down
pendingMove = null;
flushPendingMove();

postMouse("down", {
    x: coords.x,
    y: coords.y,
    button: pressedMouseButton
});

}

function onPointerUp(event) {

if (
    !isDragging ||
    event.pointerId !== activePointerId
) {
    return;
}

event.preventDefault();

const image =
    event.currentTarget;

if (image.hasPointerCapture(event.pointerId)) {
    image.releasePointerCapture(
        event.pointerId
    );
}

finishDrag(
    getRemoteCoords(event)
);

}

function onPointerCancel() {

if (isDragging) {
    finishDrag(null);
}

}

function finishDrag(coords) {

if (coords) {
    pendingMove = coords;
}

flushPendingMove();

const payload = {
    button: pressedMouseButton
};

if (coords) {
    payload.x = coords.x;
    payload.y = coords.y;
}

postMouse("up", payload);

isDragging = false;
activePointerId = null;
pressedMouseButton = "left";

}

/* ==============================
TECLADO REMOTO
============================== */

/* event.code -> nombre de tecla de PyAutoGUI (independiente de la distribucion) */
const KEY_CODE_MAP = {
    Enter: "enter",
    NumpadEnter: "enter",
    Escape: "esc",
    Tab: "tab",
    Backspace: "backspace",
    Delete: "delete",
    Insert: "insert",
    Home: "home",
    End: "end",
    PageUp: "pageup",
    PageDown: "pagedown",
    ArrowUp: "up",
    ArrowDown: "down",
    ArrowLeft: "left",
    ArrowRight: "right",
    Space: "space",
    ControlLeft: "ctrlleft",
    ControlRight: "ctrlright",
    ShiftLeft: "shiftleft",
    ShiftRight: "shiftright",
    AltLeft: "altleft",
    AltRight: "altright",
    MetaLeft: "winleft",
    MetaRight: "winright",
    OSLeft: "winleft",
    OSRight: "winright",
    CapsLock: "capslock",
    NumLock: "numlock",
    ScrollLock: "scrolllock",
    PrintScreen: "printscreen",
    Pause: "pause",
    ContextMenu: "apps",
    NumpadAdd: "add",
    NumpadSubtract: "subtract",
    NumpadMultiply: "multiply",
    NumpadDivide: "divide",
    NumpadDecimal: "decimal"
};

let keyboardQueue = Promise.resolve();
let keyboardDeviceId = null;

// event.code -> tecla enviada en el keydown (el keyup suelta exactamente la misma)
const pressedRemoteKeys = new Map();

function postKeyboard(payload) {

const deviceId =
    keyboardDeviceId ||
    (selectedDevice && selectedDevice.device_id);

if (!deviceId) {
    return keyboardQueue;
}

keyboardQueue = keyboardQueue
    .then(() =>
        fetch(
            `/api/devices/${deviceId}/keyboard`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            }
        )
    )
    .catch(error =>
        console.error(
            "Error enviando teclado:",
            error
        )
    );

return keyboardQueue;

}

function remoteKeyFromEvent(event) {

const code =
    event.code;

if (KEY_CODE_MAP[code]) {
    return KEY_CODE_MAP[code];
}

let match =
    /^F([1-9]|1[0-9]|2[0-4])$/.exec(code);

if (match) {
    return `f${match[1]}`;
}

match =
    /^Key([A-Z])$/.exec(code);

if (match) {
    return match[1].toLowerCase();
}

match =
    /^(?:Digit|Numpad)([0-9])$/.exec(code);

if (match) {
    return code.startsWith("Numpad")
        ? `num${match[1]}`
        : match[1];
}

return null;

}

function onRemoteKeyDown(event) {

if (!selectedDevice) {
    return;
}

// Evita las acciones del navegador (Ctrl+C, F5, flechas, Tab...)
event.preventDefault();
event.stopPropagation();

if (event.isComposing) {
    return;
}

const key =
    remoteKeyFromEvent(event);

if (key) {

    if (
        pressedRemoteKeys.has(event.code) &&
        !event.repeat
    ) {
        // keydown duplicado sin keyup: no se reenvia
        return;
    }

    pressedRemoteKeys.set(
        event.code,
        key
    );

    // Tecla mantenida: cada repeticion se envia como keyDown (autorepeticion remota)
    postKeyboard({
        action: "down",
        key: key
    });

    return;
}

// Simbolos segun la distribucion local (p. ej. AltGr+2 = @)
if (
    event.key.length === 1 &&
    event.key !== "Dead"
) {

    postKeyboard({
        action: "press",
        key: event.key
    });
}

}

function onRemoteKeyUp(event) {

if (!selectedDevice) {
    return;
}

event.preventDefault();
event.stopPropagation();

const key =
    pressedRemoteKeys.get(event.code);

if (!key) {
    return;
}

pressedRemoteKeys.delete(
    event.code
);

postKeyboard({
    action: "up",
    key: key
});

}

function releaseAllRemoteKeys() {

pressedRemoteKeys.forEach(key => {

    postKeyboard({
        action: "up",
        key: key
    });
});

pressedRemoteKeys.clear();

// Seguridad extra: el Agent suelta todo lo que siga presionado
postKeyboard({
    action: "release_all"
});

}

function onRemoteKeyboardBlur() {

if (pressedRemoteKeys.size) {
    releaseAllRemoteKeys();
}

}

function enableRemoteKeyboard() {

const image =
    document.getElementById(
        "remote-screen-image"
    );

if (!image) {
    return;
}

disableRemoteKeyboard(false);

keyboardDeviceId =
    selectedDevice
        ? selectedDevice.device_id
        : null;

image.addEventListener("keydown", onRemoteKeyDown);
image.addEventListener("keyup", onRemoteKeyUp);
image.addEventListener("blur", onRemoteKeyboardBlur);
window.addEventListener("blur", onRemoteKeyboardBlur);

}

function disableRemoteKeyboard(releaseKeys = true) {

const image =
    document.getElementById(
        "remote-screen-image"
    );

if (image) {
    image.removeEventListener("keydown", onRemoteKeyDown);
    image.removeEventListener("keyup", onRemoteKeyUp);
    image.removeEventListener("blur", onRemoteKeyboardBlur);
}

window.removeEventListener("blur", onRemoteKeyboardBlur);

if (releaseKeys) {
    releaseAllRemoteKeys();
}

}

/* ==============================
TRANSFERENCIA DE ARCHIVOS
============================== */

const MAX_TRANSFER_SIZE =
    200 * 1024 * 1024;

let fileTransferInProgress = false;

function formatFileSize(bytes) {

const units = ["B", "KB", "MB", "GB"];

let value = bytes;
let unit = 0;

while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
}

return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;

}

function setFileTransferStatus(message, type) {

const status =
    document.getElementById(
        "file-transfer-status"
    );

if (!message) {
    status.classList.add("hidden");
    return;
}

const colors = {
    success: "text-emerald-400",
    error: "text-red-400",
    info: "text-slate-300"
};

status.className =
    `text-sm ${colors[type] || colors.info}`;

status.textContent =
    message;

}

function setFileTransferProgress(percent) {

document
    .getElementById("file-transfer-progress")
    .classList.remove("hidden");

document
    .getElementById("file-transfer-progress-bar")
    .style.width = `${percent}%`;

document
    .getElementById("file-transfer-percent")
    .textContent = `${Math.round(percent)}%`;

}

function onFileTransferSelected() {

const file =
    document.getElementById("file-transfer-input").files[0];

const sendButton =
    document.getElementById("file-transfer-send");

document
    .getElementById("file-transfer-progress")
    .classList.add("hidden");

setFileTransferStatus("");

document.getElementById("file-transfer-name").textContent =
    file ? file.name : "Ningún archivo seleccionado";

document.getElementById("file-transfer-size").textContent =
    file ? formatFileSize(file.size) : "";

if (file && file.size > MAX_TRANSFER_SIZE) {

    sendButton.disabled = true;

    setFileTransferStatus(
        `El archivo supera el límite de ${formatFileSize(MAX_TRANSFER_SIZE)}`,
        "error"
    );

    return;
}

sendButton.disabled =
    !file || fileTransferInProgress;

}

function sendSelectedFile() {

const input =
    document.getElementById("file-transfer-input");

const sendButton =
    document.getElementById("file-transfer-send");

const file =
    input.files[0];

if (!selectedDevice || !file || fileTransferInProgress) {
    return;
}

if (file.size > MAX_TRANSFER_SIZE) {
    onFileTransferSelected();
    return;
}

fileTransferInProgress = true;
sendButton.disabled = true;


setFileTransferProgress(0);
setFileTransferStatus("Enviando archivo...", "info");

const url =
    `/api/devices/${encodeURIComponent(selectedDevice.device_id)}/files/upload` +
    `?filename=${encodeURIComponent(file.name)}&size=${file.size}`;

// XMLHttpRequest para poder mostrar el progreso de subida
const xhr =
    new XMLHttpRequest();

xhr.open("POST", url);

xhr.setRequestHeader(
    "Content-Type",
    "application/octet-stream"
);

xhr.upload.onprogress = event => {

    if (event.lengthComputable) {
        setFileTransferProgress(
            event.loaded / event.total * 100
        );
    }
};

xhr.upload.onload = () => {

    setFileTransferStatus(
        "Esperando confirmación del equipo remoto...",
        "info"
    );
};

const finish = () => {

    fileTransferInProgress = false;
    sendButton.disabled = !input.files[0];
};

xhr.onload = () => {

    let data = {};

    try {
        data = JSON.parse(xhr.responseText);
    } catch (error) {
        data = {};
    }

    if (
        xhr.status >= 200 &&
        xhr.status < 300 &&
        data.status === "file_transfer_complete"
    ) {

        setFileTransferProgress(100);

        setFileTransferStatus(
            `Archivo recibido: ${data.file.saved_as} ` +
            `(${formatFileSize(data.file.size)}) en ${data.file.path}`,
            "success"
        );

    } else {

        setFileTransferStatus(
            data.message || `Error en la transferencia (${xhr.status})`,
            "error"
        );
    }

    finish();
};

xhr.onerror = () => {

    setFileTransferStatus(
        "No se pudo conectar con el servidor",
        "error"
    );

    finish();
};

xhr.send(file);

}

let fileDownloadInProgress = false;

function setFileDownloadStatus(message, type) {

const status =
    document.getElementById(
        "file-download-status"
    );

if (!message) {
    status.classList.add("hidden");
    return;
}

const colors = {
    success: "text-emerald-400",
    error: "text-red-400",
    info: "text-slate-300"
};

status.className =
    `text-sm ${colors[type] || colors.info}`;

status.textContent =
    message;

}

function setFileDownloadProgress(percent, indeterminate) {

document
    .getElementById("file-download-progress")
    .classList.remove("hidden");

document
    .getElementById("file-download-progress-bar")
    .style.width = `${percent}%`;

document
    .getElementById("file-download-percent")
    .textContent = indeterminate ? "..." : `${Math.round(percent)}%`;

}

function filenameFromResponse(response, fallback) {

const header =
    response.headers.get("Content-Disposition") || "";

const utf8 =
    /filename\*=UTF-8''([^;]+)/i.exec(header);

if (utf8) {
    try {
        return decodeURIComponent(utf8[1]);
    } catch (error) {
        return fallback;
    }
}

const plain =
    /filename="?([^";]+)"?/i.exec(header);

return plain ? plain[1] : fallback;

}

async function downloadRemoteFile() {

const input =
    document.getElementById("file-download-path");

const button =
    document.getElementById("file-download-button");

const path =
    input.value.trim();

if (!selectedDevice || !path || fileDownloadInProgress) {
    return;
}

// La ruta debe apuntar a un archivo, no a la carpeta
if (/[\\/]$/.test(path)) {
    setFileDownloadStatus("Indica la ruta completa de un archivo", "error");
    return;
}


fileDownloadInProgress = true;
button.disabled = true;

setFileDownloadProgress(0, true);
setFileDownloadStatus("Solicitando archivo al equipo remoto...", "info");

const url =
    `/api/devices/${encodeURIComponent(selectedDevice.device_id)}/files/download` +
    `?path=${encodeURIComponent(path)}`;

try {

    const response =
        await fetch(url);

    if (!response.ok) {

        let data = {};

        try {
            data = await response.json();
        } catch (error) {
            data = {};
        }


        setFileDownloadStatus(
            data.message || `Error en la descarga (${response.status})`,
            "error"
        );

        return;
    }

    const total =
        Number(response.headers.get("Content-Length")) || 0;

    const filename =
        filenameFromResponse(response, path.split(/[\\/]/).pop());

    const reader =
        response.body.getReader();

    const chunks = [];
    let received = 0;

    setFileDownloadStatus("Descargando archivo...", "info");

    while (true) {

        const { done, value } =
            await reader.read();

        if (done) {
            break;
        }

        chunks.push(value);
        received += value.length;

        if (total) {
            setFileDownloadProgress(received / total * 100, false);
        } else {
            setFileDownloadProgress(0, true);
        }
    }


    if (total && received !== total) {

        setFileDownloadStatus(
            "La descarga se interrumpió antes de terminar",
            "error"
        );

        return;
    }

    // Blob -> descarga real en el navegador (conserva nombre y extension)
    const blob =
        new Blob(chunks);

    const objectUrl =
        URL.createObjectURL(blob);

    const link =
        document.createElement("a");

    link.href = objectUrl;
    link.download = filename;

    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    URL.revokeObjectURL(objectUrl);

    setFileDownloadProgress(100, false);

    setFileDownloadStatus(
        `Archivo descargado: ${filename} (${formatFileSize(received)})`,
        "success"
    );

} catch (error) {


    setFileDownloadStatus(
        "No se pudo conectar con el servidor",
        "error"
    );

} finally {

    fileDownloadInProgress = false;
    button.disabled = false;
}

}

/* ==============================
CERRAR DETALLES
============================== */

function closeDeviceDetails() {

stopRemoteDesktop();

stopRecordingStatusPolling();

selectedDevice =
    null;

document
    .getElementById(
        "device-details"
    )
    .classList.add(
        "hidden"
    );

}

/* ==============================
NAVEGACION
============================== */

const VIEW_TITLES = {

dashboard: [
    "Dashboard",
    "Resumen de tus dispositivos"
],

devices: [
    "Dispositivos",
    "Equipos registrados en RemoteAdmin"
],

recordings: [
    "Grabaciones",
    "Grabaciones de pantalla almacenadas"
],

alerts: [
    "Alertas",
    "Conexiones y desconexiones de los equipos"
],

settings: [
    "Configuración",
    "Ajustes del servidor RemoteAdmin"
]

};

function showView(view) {

const stats =
    document.getElementById(
        "stats-section"
    );

const devices =
    document.getElementById(
        "devices-section"
    );

const alerts =
    document.getElementById(
        "alerts-section"
    );

const settings =
    document.getElementById(
        "settings-section"
    );

const recordings =
    document.getElementById(
        "recordings-section"
    );

const dashboardPanels =
    document.getElementById("dashboard-panels");

stats.classList.add("hidden");
devices.classList.add("hidden");
alerts.classList.add("hidden");
settings.classList.add("hidden");
recordings.classList.add("hidden");

if (dashboardPanels) {
    dashboardPanels.classList.add("hidden");
}

if (view === "dashboard") {

    stats.classList.remove(
        "hidden"
    );

    // El Dashboard NO muestra la tabla de equipos: eso vive solo en
    // Dispositivos. Aquí van los paneles de monitoreo.
    if (dashboardPanels) {
        dashboardPanels.classList.remove("hidden");
    }

    renderDashboardPanels();

} else if (view === "devices") {

    devices.classList.remove(
        "hidden"
    );

} else if (view === "alerts") {

    alerts.classList.remove(
        "hidden"
    );

    loadAlerts();

} else if (view === "recordings") {

    recordings.classList.remove(
        "hidden"
    );

    loadRecordings();

} else if (view === "settings") {

    settings.classList.remove(
        "hidden"
    );

    loadSettings();
}

document
    .querySelectorAll(".nav-button")
    .forEach(button => {

        const active =
            button.dataset.view === view;

        button.classList.toggle(
            "bg-slate-800",
            active
        );

        button.classList.toggle(
            "text-white",
            active
        );

        button.classList.toggle(
            "text-slate-400",
            !active
        );
    });

const titles =
    VIEW_TITLES[view] ||
    VIEW_TITLES.dashboard;

document.getElementById(
    "view-title"
).textContent =
    titles[0];

document.getElementById(
    "view-subtitle"
).textContent =
    titles[1];

}

/* ==============================
GRABACIONES
============================== */

function recordingsMessageRow(text) {
    return `
        <tr>
            <td colspan="9" class="px-6 py-10 text-center text-slate-500">
                ${text}
            </td>
        </tr>
    `;
}

/* ---------- Línea de tiempo del historial ---------- */

let recordingsById = {};

function timelineTime(date) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function computeTimelineAxis(recordings) {

const date =
    document.getElementById("recordings-date").value;

const startTime =
    document.getElementById("recordings-start").value;

const endTime =
    document.getElementById("recordings-end").value;

let axisStart;
let axisEnd;

if (date) {

    axisStart = new Date(`${date}T${startTime || "00:00"}:00`);
    axisEnd = new Date(`${date}T${endTime || "23:59"}:00`);

} else if (recordings.length) {

    const starts = recordings.map(r => new Date(r.started_at).getTime());
    const ends = recordings.map(r => new Date(r.ended_at || r.started_at).getTime());
    axisStart = new Date(Math.min(...starts));
    axisEnd = new Date(Math.max(...ends));

} else {
    return null;
}

if (isNaN(axisStart.getTime()) || isNaN(axisEnd.getTime())) {
    return null;
}

if (axisEnd <= axisStart) {
    axisEnd = new Date(axisStart.getTime() + 3600000);
}

return { axisStart, axisEnd };

}

let selectedTimelineId = null;
let lastTimelineRecordings = [];

// Marcas del eje calculadas dinámicamente según duración y ancho disponible,
// evitando que las etiquetas se encimen o se salgan del contenedor.
function buildTimelineTicks(axisStart, axisEnd, widthPx) {

const startMs = axisStart.getTime();
const endMs = axisEnd.getTime();
const span = endMs - startMs;
const spanMin = span / 60000;

// Cada etiqueta ("09:57 a.m.") ocupa ~92px; el ancho manda cuántas caben
const labelPx = 92;
const maxTicks = Math.max(2, Math.min(12, Math.floor((widthPx || 800) / labelPx)));

const positions = [];

const addTick = (t) => {
    const pct = ((t - startMs) / span) * 100;
    if (pct < -0.5 || pct > 100.5) {
        return;
    }
    const align = pct < 6 ? "left" : pct > 94 ? "right" : "center";
    positions.push({ t, pct: Math.max(0, Math.min(100, pct)), align });
};

if (spanMin <= 20) {

    // Rangos muy cortos: inicio / mitad / fin (o solo inicio y fin si es muy angosto)
    addTick(startMs);
    if (maxTicks >= 3) {
        addTick(startMs + span / 2);
    }
    addTick(endMs);

} else {

    // Escalón "redondo" según la duración; se agranda si no caben las etiquetas
    let step;
    if (spanMin <= 90) {
        step = 15 * 60000;
    } else if (spanMin <= 240) {
        step = 30 * 60000;
    } else if (spanMin <= 480) {
        step = 60 * 60000;
    } else if (spanMin <= 1440) {
        step = 120 * 60000;
    } else {
        step = 180 * 60000;
    }

    // Garantiza que el número de marcas no supere lo que cabe por ancho
    while (span / step > maxTicks - 1) {
        step *= 2;
    }

    // Marcas alineadas a horas "redondas" dentro del rango
    const first = Math.ceil(startMs / step) * step;
    for (let t = first; t <= endMs + 1; t += step) {
        addTick(t);
    }

    // Si por el redondeo quedó muy vacío en los extremos, añade inicio/fin
    if (positions.length === 0) {
        addTick(startMs);
        addTick(endMs);
    }
}

return positions.map(p => {

    const translate =
        p.align === "left" ? "translateX(0)"
        : p.align === "right" ? "translateX(-100%)"
        : "translateX(-50%)";

    return `<span class="absolute text-xs text-slate-500 whitespace-nowrap"
                style="left:${p.pct}%; transform:${translate}">${timelineTime(new Date(p.t))}</span>`;

}).join("");

}

function renderTimeline(recordings) {

lastTimelineRecordings = recordings;

const container =
    document.getElementById("recordings-timeline");

const body =
    document.getElementById("timeline-body");

const empty =
    document.getElementById("timeline-empty");

const detail =
    document.getElementById("timeline-detail");

const rangeLabel =
    document.getElementById("timeline-range-label");

if (!container || !body) {
    return;
}

container.classList.remove("hidden");
detail.classList.add("hidden");

if (!recordings.length) {
    body.innerHTML = "";
    rangeLabel.textContent = "";
    empty.classList.remove("hidden");
    return;
}

empty.classList.add("hidden");

const axis =
    computeTimelineAxis(recordings);

if (!axis) {
    body.innerHTML = "";
    return;
}

const total =
    axis.axisEnd.getTime() - axis.axisStart.getTime();

rangeLabel.textContent =
    `${timelineTime(axis.axisStart)} – ${timelineTime(axis.axisEnd)}`;

// Marcas horarias dinámicas (según duración y ancho disponible)
const bodyWidth =
    body.clientWidth || body.offsetWidth || 800;

const ticksHtml =
    buildTimelineTicks(axis.axisStart, axis.axisEnd, bodyWidth);

// Agrupar por dispositivo (una pista por equipo)
const groups = {};
recordings.forEach(r => {
    const key = r.device_id;
    if (!groups[key]) {
        groups[key] = { hostname: r.hostname || r.device_id, items: [] };
    }
    groups[key].items.push(r);
});

const lanes = Object.keys(groups).map(deviceId => {

    const group = groups[deviceId];

    const blocks = group.items.map(r => {

        const s = new Date(r.started_at).getTime();
        const e = new Date(r.ended_at || r.started_at).getTime();

        // Recorta al rango visible (caso de rango parcial)
        const vs = Math.max(s, axis.axisStart.getTime());
        const ve = Math.min(e, axis.axisEnd.getTime());

        if (ve <= vs) {
            return "";
        }

        const left = ((vs - axis.axisStart.getTime()) / total) * 100;
        const width = Math.max(0.6, ((ve - vs) / total) * 100);

        const kept = r.keep ? "ring-2 ring-amber-400" : "";

        return `
            <button
                type="button"
                data-timeline-id="${r.id}"
                onclick="selectTimelineSegment(${r.id})"
                title="${timelineTime(new Date(r.started_at))} - ${timelineTime(new Date(r.ended_at || r.started_at))}"
                class="timeline-block absolute top-0 h-8 rounded bg-emerald-500 hover:bg-emerald-400 ${kept}"
                style="left:${left}%; width:${width}%"
            ></button>
        `;

    }).join("");

    return `
        <div class="mb-4">
            <p class="text-xs font-medium text-slate-600 mb-1">${group.hostname}</p>
            <div class="relative h-8 bg-slate-100 rounded">
                ${blocks}
            </div>
        </div>
    `;

}).join("");

body.innerHTML = `
    <div class="relative h-5 mb-2">${ticksHtml}</div>
    ${lanes}
    <p class="text-xs text-slate-400 mt-2">
        Los bloques son segmentos grabados; los espacios en gris indican intervalos sin grabación.
    </p>
`;

// Reaplica la selección tras redibujar (p. ej. al cambiar el tamaño de ventana)
if (selectedTimelineId !== null && recordingsById[selectedTimelineId]) {
    showTimelineDetail(selectedTimelineId);
} else {
    detail.classList.add("hidden");
}

}

function highlightTimelineBlock(id) {

document.querySelectorAll(".timeline-block").forEach(b => {
    const active = id !== null && b.dataset.timelineId === String(id);
    b.classList.toggle("ring-2", active);
    b.classList.toggle("ring-slate-900", active);
});

}

function closeTimelineDetail() {

selectedTimelineId = null;

const detail =
    document.getElementById("timeline-detail");

if (detail) {
    detail.classList.add("hidden");
    detail.innerHTML = "";
}

highlightTimelineBlock(null);

}

function showTimelineDetail(id) {

const rec =
    recordingsById[id];

const detail =
    document.getElementById("timeline-detail");

if (!rec || !detail) {
    return;
}

selectedTimelineId = id;

highlightTimelineBlock(id);

const start = formatRecordingDate(rec.started_at);
const end = formatRecordingDate(rec.ended_at);
const pc = rec.hostname || rec.device_id || "-";

detail.innerHTML = `
    <div class="flex items-start justify-between gap-4 mb-3">
        <h5 class="font-semibold text-slate-900 text-sm">Segmento seleccionado</h5>
        <button onclick="closeTimelineDetail()"
            aria-label="Cerrar"
            class="w-7 h-7 flex items-center justify-center rounded-lg bg-slate-200 text-slate-600 hover:bg-slate-300 transition">
            ✕
        </button>
    </div>
    <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="grid grid-cols-2 sm:grid-cols-3 gap-4 text-sm">
            <div><p class="text-xs text-slate-500">Dispositivo</p><strong>${pc}</strong></div>
            <div><p class="text-xs text-slate-500">Fecha</p><strong>${start.date}</strong></div>
            <div><p class="text-xs text-slate-500">Estado</p><strong>${rec.status || "-"}</strong></div>
            <div><p class="text-xs text-slate-500">Hora inicio</p><strong>${start.time}</strong></div>
            <div><p class="text-xs text-slate-500">Hora fin</p><strong>${end.time || "-"}</strong></div>
            <div><p class="text-xs text-slate-500">Duración</p><strong>${formatDuration(rec.duration_sec)}</strong></div>
            <div><p class="text-xs text-slate-500">Conservada</p><strong>${rec.keep ? "⭐ Sí" : "No"}</strong></div>
        </div>
        <div class="flex flex-col gap-2">
            <button onclick="playRecording(${rec.id})"
                class="px-3 py-1.5 rounded-lg bg-slate-900 text-white text-xs hover:bg-slate-700 transition">
                Ver grabación
            </button>
            <a href="/api/recordings/${rec.id}/download"
                class="text-center px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs hover:bg-blue-500 transition">
                Descargar
            </a>
        </div>
    </div>
`;

detail.classList.remove("hidden");

}

function selectTimelineSegment(id) {

// Clic sobre el mismo segmento ya seleccionado -> se contrae
if (selectedTimelineId === id) {
    closeTimelineDetail();
    return;
}

showTimelineDetail(id);

}

async function toggleKeep(id, currentKeep) {

const nextKeep = currentKeep ? 0 : 1;

try {

    const response =
        await fetch(`/api/recordings/${id}/keep`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ keep: nextKeep })
        });

    if (!response.ok) {
        return;
    }

    // Refresca el listado para reflejar el nuevo estado
    loadRecordings();

} catch (error) {

    // Si falla, se deja el estado actual
}

}

function playRecording(id) {

const player =
    document.getElementById("recording-player");

const video =
    document.getElementById("recording-video");

const error =
    document.getElementById("recording-player-error");

const title =
    document.getElementById("recording-player-title");

if (!player || !video) {
    return;
}

error.classList.add("hidden");
title.textContent = `Grabación #${id}`;

// La sesión viaja en la cookie; el endpoint exige autenticación
video.src = `/api/recordings/${id}/video`;

player.classList.remove("hidden");
player.scrollIntoView({ behavior: "smooth", block: "nearest" });

video.onerror = () => {
    error.textContent = "No se pudo reproducir la grabación";
    error.classList.remove("hidden");
};

video.play().catch(() => {
    // Si el navegador bloquea el autoplay, el usuario pulsa play manualmente
});

}

function closeRecordingPlayer() {

const player =
    document.getElementById("recording-player");

const video =
    document.getElementById("recording-video");

if (video) {
    video.pause();
    video.removeAttribute("src");
    video.load();
}

if (player) {
    player.classList.add("hidden");
}

}

function formatDuration(seconds) {

const total = Math.max(0, Math.round(seconds || 0));
const m = Math.floor(total / 60);
const s = total % 60;

if (m === 0) {
    return `${s}s`;
}

return `${m}m ${String(s).padStart(2, "0")}s`;

}

function formatRecordingDate(iso) {

if (!iso) {
    return { date: "-", time: "-" };
}

const d = new Date(iso);

if (isNaN(d.getTime())) {
    return { date: iso, time: "" };
}

return {
    date: d.toLocaleDateString(),
    time: d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
};

}

async function populateRecordingsDeviceFilter() {

const select =
    document.getElementById("recordings-device");

if (!select) {
    return;
}

try {

    const response =
        await fetch("/api/devices");

    if (!response.ok) {
        return;
    }

    const data =
        await response.json();

    const devices =
        Array.isArray(data) ? data : (data.devices || []);

    const current =
        select.value;

    // Mantiene "Todos" y reconstruye el resto
    select.innerHTML =
        '<option value="">Todos</option>' +
        devices.map(d =>
            `<option value="${d.device_id}">${d.hostname || d.device_id}</option>`
        ).join("");

    select.value = current;

} catch (error) {
    // Si falla, el filtro queda solo con "Todos"
}

}

function buildRecordingsQuery() {

const deviceId =
    document.getElementById("recordings-device").value;

const date =
    document.getElementById("recordings-date").value;

const startTime =
    document.getElementById("recordings-start").value;

const endTime =
    document.getElementById("recordings-end").value;

const params =
    new URLSearchParams();

if (deviceId) {
    params.set("device_id", deviceId);
}

const rangeInfo =
    document.getElementById("recordings-range-info");

const rangeText =
    document.getElementById("recordings-range-text");

// El rango solo aplica si hay fecha
if (date) {

    // La selección es hora local; se envía en UTC (ISO) para comparar con lo almacenado
    if (startTime) {
        const startDate = new Date(`${date}T${startTime}`);
        if (!isNaN(startDate.getTime())) {
            params.set("start", startDate.toISOString());
        }
    }

    if (endTime) {
        const endDate = new Date(`${date}T${endTime}`);
        if (!isNaN(endDate.getTime())) {
            params.set("end", endDate.toISOString());
        }
    }
}

// Prepara/visualiza el rango seleccionado (para descarga posterior)
if (date && (startTime || endTime)) {
    rangeText.textContent =
        `Rango seleccionado: ${date} ${startTime || "00:00"} – ${endTime || "23:59"}`;
    rangeInfo.classList.remove("hidden");
} else {
    rangeInfo.classList.add("hidden");
}

return params.toString();

}

async function loadRecordings() {

const table =
    document.getElementById("recordings-table");

if (!table) {
    return;
}

// Nueva búsqueda: se limpia la selección previa de la línea de tiempo
selectedTimelineId = null;

populateRecordingsDeviceFilter();

table.innerHTML = recordingsMessageRow("Cargando...");

const query =
    buildRecordingsQuery();

try {

    const response =
        await fetch(
            query ? `/api/recordings?${query}` : "/api/recordings"
        );

    if (!response.ok) {
        table.innerHTML = recordingsMessageRow("Error al cargar las grabaciones");
        return;
    }

    const data =
        await response.json();

    const recordings =
        data.recordings || [];

    // Índice por id para la línea de tiempo
    recordingsById = {};
    recordings.forEach(r => { recordingsById[r.id] = r; });

    if (recordings.length === 0) {
        table.innerHTML = recordingsMessageRow("No hay grabaciones");
        renderTimeline([]);
        return;
    }

    table.innerHTML = recordings.map(rec => {

        const start = formatRecordingDate(rec.started_at);
        const end = formatRecordingDate(rec.ended_at);
        const pc = rec.hostname || rec.device_id || "-";

        const kept = Boolean(rec.keep);

        const keepCell = kept
            ? `<span class="inline-flex items-center gap-1 text-amber-600 font-medium">⭐ Conservada</span>
               <button onclick="toggleKeep(${rec.id}, 1)"
                   class="ml-2 px-2 py-1 rounded-lg bg-slate-100 text-slate-600 text-xs hover:bg-slate-200 transition">
                   Dejar de conservar
               </button>`
            : `<span class="text-slate-500">No</span>
               <button onclick="toggleKeep(${rec.id}, 0)"
                   class="ml-2 px-2 py-1 rounded-lg bg-amber-100 text-amber-700 text-xs hover:bg-amber-200 transition">
                   Conservar
               </button>`;

        return `
            <tr class="hover:bg-slate-50 transition ${kept ? "bg-amber-50/40" : ""}">
                <td class="px-6 py-4 font-medium text-slate-900">${pc}</td>
                <td class="px-6 py-4 text-slate-600">${start.date}</td>
                <td class="px-6 py-4 text-slate-600">${start.time}</td>
                <td class="px-6 py-4 text-slate-600">${end.time || "-"}</td>
                <td class="px-6 py-4 text-slate-600">${formatDuration(rec.duration_sec)}</td>
                <td class="px-6 py-4 text-slate-600">${formatFileSize(rec.size_bytes)}</td>
                <td class="px-6 py-4">
                    <span class="px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 text-xs">
                        ${rec.status || "-"}
                    </span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">${keepCell}</td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <button
                        onclick="playRecording(${rec.id})"
                        class="px-3 py-1.5 rounded-lg bg-slate-900 text-white text-xs hover:bg-slate-700 transition"
                    >
                        Ver grabación
                    </button>
                    <a
                        href="/api/recordings/${rec.id}/download"
                        class="ml-2 inline-block px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs hover:bg-blue-500 transition"
                    >
                        Descargar
                    </a>
                </td>
            </tr>
        `;

    }).join("");

    renderTimeline(recordings);

} catch (error) {

    table.innerHTML = recordingsMessageRow("Error al cargar las grabaciones");
}

}

/* ==============================
ALERTAS
============================== */

async function loadAlerts() {

const list =
    document.getElementById(
        "alerts-list"
    );

try {

    const response =
        await fetch(
            "/api/alerts"
        );

    if (!response.ok) {
        throw new Error(
            "Error cargando alertas"
        );
    }

    const alerts =
        await response.json();

    updateAlertsBadge(
        alerts
    );

    if (alerts.length === 0) {

        list.innerHTML = `
            <div class="px-6 py-10 text-center text-slate-500">
                No hay alertas
            </div>
        `;

        return;
    }

    list.innerHTML =
        alerts.map(
            alert => {

                const apariencia =
                    alertAppearance(alert.type);

                const unread =
                    alert.is_read
                        ? ""
                        : "bg-slate-50";

                const icono =
                    apariencia.icon
                        ? `<span class="text-base shrink-0">${apariencia.icon}</span>`
                        : "";

                // Etiqueta de categoría junto a la fecha
                const etiqueta =
                    apariencia.label
                        ? `<span class="px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">${apariencia.label}</span>`
                        : "";

                return `
                    <div class="px-6 py-4 flex items-center gap-3 ${unread}">

                        <span class="w-2.5 h-2.5 rounded-full ${apariencia.dot} shrink-0"></span>

                        ${icono}

                        <div class="flex-1 min-w-0">

                            <p class="text-sm text-slate-900">
                                ${alert.message}
                            </p>

                            <p class="text-xs text-slate-500 mt-1 flex items-center gap-2">
                                ${etiqueta}
                                <span>${formatDate(alert.created_at)}</span>
                            </p>

                        </div>

                        ${
                            alert.is_read
                                ? ""
                                : `
                                    <button
                                        onclick="markAlertRead(${alert.id})"
                                        class="text-xs text-slate-500 hover:text-slate-900"
                                    >
                                        Marcar leída
                                    </button>
                                `
                        }

                    </div>
                `;
            }
        ).join("");

} catch (error) {

    console.error(
        "Error cargando alertas:",
        error
    );

    list.innerHTML = `
        <div class="px-6 py-10 text-center text-red-500">
            No se pudieron cargar las alertas
        </div>
    `;
}

}

function updateAlertsBadge(alerts) {

const badge =
    document.getElementById(
        "alerts-badge"
    );

if (!badge) {
    return;
}

const unread =
    alerts.filter(
        alert =>
            !alert.is_read
    ).length;

if (unread === 0) {

    badge.classList.add(
        "hidden"
    );

    return;
}

badge.textContent =
    unread;

badge.classList.remove(
    "hidden"
);

}

async function markAlertRead(alertId) {

try {

    await fetch(
        `/api/alerts/${alertId}/read`,
        {
            method: "POST"
        }
    );

    await loadAlerts();

} catch (error) {

    console.error(
        "Error marcando alerta:",
        error
    );
}

}

async function markAllAlertsRead() {

try {

    await fetch(
        "/api/alerts/read-all",
        {
            method: "POST"
        }
    );

    await loadAlerts();

} catch (error) {

    console.error(
        "Error marcando alertas:",
        error
    );
}

}

async function refreshAlertsBadge() {

try {

    const response =
        await fetch(
            "/api/alerts"
        );

    if (!response.ok) {
        return;
    }

    const alerts =
        await response.json();

    updateAlertsBadge(
        alerts
    );

    // Misma respuesta para la tarjeta de resumen y para los paneles del
    // Dashboard: no se repite la petición
    updatePendingAlertsCard(
        alerts
    );

    lastAlerts = alerts;

} catch (error) {
}

}

/* ==============================
CONFIGURACION
============================== */

async function loadSettings() {

try {

    const response =
        await fetch(
            "/api/settings"
        );

    if (!response.ok) {
        throw new Error(
            "Error cargando configuración"
        );
    }

    const settings =
        await response.json();

    document.getElementById(
        "setting-server-name"
    ).value =
        settings.server_name || "";

    document.getElementById(
        "setting-offline-seconds"
    ).value =
        settings.offline_after_seconds || "30";

    document.getElementById(
        "setting-alerts-enabled"
    ).checked =
        settings.alerts_enabled === "true";

    // Umbrales de salud
    document.getElementById(
        "setting-ram-warning"
    ).value =
        settings.ram_warning_percent || "75";

    document.getElementById(
        "setting-ram-critical"
    ).value =
        settings.ram_critical_percent || "90";

    document.getElementById(
        "setting-disk-warning"
    ).value =
        settings.disk_warning_percent || "75";

    document.getElementById(
        "setting-disk-critical"
    ).value =
        settings.disk_critical_percent || "90";

    // Misma respuesta para el Dashboard: no se repite la petición
    applyHealthThresholds(settings);

} catch (error) {

    console.error(
        "Error cargando configuración:",
        error
    );
}

}


/* ==============================
UMBRALES DE SALUD: VALIDACIÓN
============================== */

function readThresholdField(id) {

const campo =
    document.getElementById(id);

if (!campo) {
    return null;
}

const texto =
    campo.value.trim();

if (texto === "") {
    return null;
}

const valor =
    Number(texto);

// Se rechazan decimales y cualquier cosa que no sea un número
if (!Number.isInteger(valor)) {
    return null;
}

return valor;

}


function validateThresholds() {
    /*
    Comprueba los cuatro umbrales antes de enviarlos.

    Devuelve { ok: true, values: {...} } o { ok: false, message: "..." }.
    La validación se hace aquí además de en los atributos min/max del HTML
    porque esos no impiden enviar el formulario desde el código.
    */

    const campos = [
        ["setting-ram-warning", "ram_warning_percent", "RAM — aviso"],
        ["setting-ram-critical", "ram_critical_percent", "RAM — crítico"],
        ["setting-disk-warning", "disk_warning_percent", "Disco — aviso"],
        ["setting-disk-critical", "disk_critical_percent", "Disco — crítico"]
    ];

    const values = {};

    for (const [id, clave, etiqueta] of campos) {

        const valor =
            readThresholdField(id);

        if (valor === null) {
            return {
                ok: false,
                message: `${etiqueta}: escribe un número entero de 0 a 100`
            };
        }

        if (valor < 0 || valor > 100) {
            return {
                ok: false,
                message: `${etiqueta}: el valor debe estar entre 0 y 100`
            };
        }

        values[clave] = String(valor);
    }

    // El aviso debe dispararse ANTES que el crítico, o el nivel de aviso
    // nunca llegaría a alcanzarse
    if (Number(values.ram_warning_percent) >= Number(values.ram_critical_percent)) {
        return {
            ok: false,
            message: "RAM: el umbral de aviso debe ser menor que el crítico"
        };
    }

    if (Number(values.disk_warning_percent) >= Number(values.disk_critical_percent)) {
        return {
            ok: false,
            message: "Disco: el umbral de aviso debe ser menor que el crítico"
        };
    }

    return { ok: true, values };

}

async function saveSettings() {

const status =
    document.getElementById(
        "settings-status"
    );

// Umbrales primero: si alguno no es válido no se guarda NADA, para no
// dejar la configuración a medias.
const umbrales =
    validateThresholds();

if (!umbrales.ok) {

    status.textContent = umbrales.message;
    status.className = "text-sm text-red-600";
    status.classList.remove("hidden");

    return;
}

const values = {

    server_name:
        document.getElementById(
            "setting-server-name"
        ).value,

    offline_after_seconds:
        document.getElementById(
            "setting-offline-seconds"
        ).value,

    alerts_enabled:
        document.getElementById(
            "setting-alerts-enabled"
        ).checked
            ? "true"
            : "false",

    ...umbrales.values
};

try {

    const response =
        await fetch(
            "/api/settings",
            {
                method: "POST",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body:
                    JSON.stringify(
                        values
                    )
            }
        );

    if (!response.ok) {
        throw new Error(
            "No se pudo guardar"
        );
    }

    // El Dashboard usa los umbrales nuevos sin esperar a recargar la página
    applyHealthThresholds(values);

    status.textContent =
        "Configuración guardada";

    status.className =
        "text-sm text-emerald-600";

    status.classList.remove(
        "hidden"
    );

} catch (error) {

    console.error(
        "Error guardando configuración:",
        error
    );

    status.textContent =
        "Error al guardar";

    status.className =
        "text-sm text-red-600";

    status.classList.remove(
        "hidden"
    );
}

}

/* ==============================
EVENTOS
============================== */

document
.getElementById(
"ping-device-button"
)
.addEventListener(
"click",
pingDevice
);

document
.getElementById(
"system-info-button"
)
.addEventListener(
"click",
requestSystemInfo
);

document
.getElementById(
"software-button"
)
.addEventListener(
"click",
requestInstalledSoftware
);

document
.getElementById(
"password-save-button"
)
.addEventListener(
"click",
changePassword
);

document
.getElementById(
"processes-button"
)
.addEventListener(
"click",
() => loadInventory("processes")
);

document
.getElementById(
"services-button"
)
.addEventListener(
"click",
() => loadInventory("services")
);

document
.getElementById(
"remote-button"
)
.addEventListener(
"click",
startRemoteDesktop
);

document
.getElementById(
"start-recording-button"
)
.addEventListener(
"click",
startRecording
);

document
.getElementById(
"continuous-enable-button"
)
.addEventListener(
"click",
() => setContinuous(true)
);

document
.getElementById(
"continuous-disable-button"
)
.addEventListener(
"click",
() => setContinuous(false)
);

document
.getElementById(
"stop-recording-button"
)
.addEventListener(
"click",
stopRecording
);

document
.getElementById(
"close-remote-button"
)
.addEventListener(
"click",
stopRemoteDesktop
);

document
.getElementById(
"file-transfer-select"
)
.addEventListener(
"click",
() =>
document.getElementById("file-transfer-input").click()
);

document
.getElementById(
"file-transfer-input"
)
.addEventListener(
"change",
onFileTransferSelected
);

document
.getElementById(
"file-transfer-send"
)
.addEventListener(
"click",
sendSelectedFile
);

document
.getElementById(
"file-download-button"
)
.addEventListener(
"click",
downloadRemoteFile
);

document
.getElementById(
"recordings-refresh-button"
)
.addEventListener(
"click",
loadRecordings
);

document
.getElementById(
"recording-player-close"
)
.addEventListener(
"click",
closeRecordingPlayer
);

document
.getElementById(
"recordings-search-button"
)
.addEventListener(
"click",
loadRecordings
);

// Eje responsive: redibuja la línea de tiempo al cambiar el tamaño de ventana
let _timelineResizeRAF = null;
window.addEventListener("resize", () => {
    if (_timelineResizeRAF) {
        cancelAnimationFrame(_timelineResizeRAF);
    }
    _timelineResizeRAF = requestAnimationFrame(() => {
        const container = document.getElementById("recordings-timeline");
        if (container && !container.classList.contains("hidden") && lastTimelineRecordings.length) {
            renderTimeline(lastTimelineRecordings);
        }
    });
});

document
.getElementById(
"recordings-clear-button"
)
.addEventListener(
"click",
() => {
    document.getElementById("recordings-device").value = "";
    document.getElementById("recordings-date").value = "";
    document.getElementById("recordings-start").value = "";
    document.getElementById("recordings-end").value = "";
    document.getElementById("recordings-range-info").classList.add("hidden");
    loadRecordings();
}
);

document
.getElementById(
"file-download-path"
)
.addEventListener(
"keydown",
event => {
    if (event.key === "Enter") {
        downloadRemoteFile();
    }
}
);

const remoteImage =
document.getElementById(
"remote-screen-image"
);

// Evita el arrastre nativo de la imagen y gestos tactiles del navegador
remoteImage.style.touchAction = "none";
remoteImage.style.userSelect = "none";

remoteImage.addEventListener(
"pointerdown",
onPointerDown
);

remoteImage.addEventListener(
"pointermove",
onPointerMove
);

remoteImage.addEventListener(
"pointerup",
onPointerUp
);

remoteImage.addEventListener(
"pointercancel",
onPointerCancel
);

remoteImage.addEventListener(
"lostpointercapture",
onPointerCancel
);

remoteImage.addEventListener(
"dragstart",
event =>
event.preventDefault()
);

document
.getElementById(
"remote-screen-image"
)
.addEventListener(
"contextmenu",
event =>
event.preventDefault()
);

document
.querySelectorAll(
".nav-button"
)
.forEach(button => {

    button.addEventListener(
        "click",
        () =>
            showView(
                button.dataset.view
            )
    );
});

document
.getElementById(
"alerts-read-all-button"
)
.addEventListener(
"click",
markAllAlertsRead
);

document
.getElementById(
"settings-save-button"
)
.addEventListener(
"click",
saveSettings
);

/* ==============================
DASHBOARD
============================== */

async function refreshDashboard() {
    /*
    Las cuatro cargas son independientes: ninguna usa el resultado de otra,
    y cada una escribe en su propia parte de la interfaz. Antes se esperaban
    en cadena, así que el ciclo duraba la SUMA de las cuatro; ahora arrancan
    a la vez y dura lo que la más lenta.

    Lo que sí es secuencial vive DENTRO de loadDevices(): primero la lista de
    equipos y después el estado de grabación de cada uno, que necesita saber
    cuáles hay. Ese orden no se toca.

    allSettled y no all: un fallo no debe impedir que las demás terminen.
    Cada función ya gestiona su propio error (el aviso de datos
    desactualizados de loadDevices, por ejemplo); esto solo garantiza que
    ninguna excepción inesperada corte el ciclo entero.
    */

    const alertsSection =
        document.getElementById(
            "alerts-section"
        );

    // Al invocarlas aquí, las peticiones ya salen en paralelo
    const tareas = [
        checkServer(),
        loadDevices(),
        refreshAlertsBadge()
    ];

    // La lista completa de alertas solo si esa sección está a la vista
    if (
        alertsSection &&
        !alertsSection.classList.contains(
            "hidden"
        )
    ) {
        tareas.push(loadAlerts());
    }

    // Resumen de grabaciones solo con el Dashboard visible, y espaciado a un
    // minuto por dentro: no añade carga al ciclo de 10 segundos.
    if (dashboardIsVisible()) {
        tareas.push(refreshRecordingsSummary());
    }

    await Promise.allSettled(tareas);

    // Los paneles se pintan con lo que las tareas dejaron en memoria
    renderDashboardPanels();

    lastRefreshAt = Date.now();

    updateLastRefreshLabel();

}

/* ==============================
REFRESCO AUTOMÁTICO
============================== */

// Frecuencia del ciclo automático (sin cambios respecto a antes)
const DASHBOARD_REFRESH_MS = 10000;

// Único temporizador del ciclo. Se guarda para poder pararlo y para que no
// se creen dos a la vez.
let dashboardRefreshTimer = null;


function startAutoRefresh() {

// Se detiene el anterior antes de crear otro: sin esto, cada vez que la
// pestaña volviera al primer plano quedaría un intervalo más corriendo.
stopAutoRefresh();

dashboardRefreshTimer =
    setInterval(
        refreshDashboard,
        DASHBOARD_REFRESH_MS
    );

}


function stopAutoRefresh() {

if (dashboardRefreshTimer) {
    clearInterval(dashboardRefreshTimer);
    dashboardRefreshTimer = null;
}

}


function initVisibilityRefresh() {
    /*
    Suspende el ciclo mientras la pestaña está en segundo plano.

    Con el panel abierto todo el día eran unas 8.600 rondas de peticiones
    diarias, cada una despertando la detección de alertas en el servidor,
    aunque nadie estuviera mirando.

    Al volver se refresca de inmediato, para no mostrar datos de hace rato
    mientras se espera al siguiente ciclo.
    */

    document.addEventListener("visibilitychange", () => {

        if (document.hidden) {
            stopAutoRefresh();
            return;
        }

        refreshDashboard();

        startAutoRefresh();

    });

}


function startApp() {

// Solo arranca el dashboard una vez, tras autenticarse
if (appStarted) {
    return;
}

appStarted = true;

// Búsqueda, filtro y ordenación de la tabla de PCs
initDevicesTableControls();

// Tarjetas de resumen: contadores, filtros rápidos y "actualizar ahora"
initSummaryCards();

// Paneles de monitoreo del Dashboard
initDashboardPanels();

showView(
    "dashboard"
);

// Los umbrales se cargan ANTES del primer pintado, para que las barras y la
// tarjeta de avisos usen los valores configurados desde el principio. Si la
// carga falla, se sigue con los de respaldo y el Dashboard igualmente pinta.
loadHealthThresholds().then(refreshDashboard);

startAutoRefresh();

initVisibilityRefresh();

}

initAuth();
