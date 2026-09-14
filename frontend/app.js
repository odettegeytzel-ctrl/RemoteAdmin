let selectedDevice = null;

/* ==============================
AUTENTICACION
============================== */

const AUTH_TOKEN_KEY = "remoteadmin_token";

let appStarted = false;

function getAuthToken() {

try {
    return localStorage.getItem(AUTH_TOKEN_KEY);
} catch (error) {
    return null;
}

}

function setAuthToken(token) {

try {
    if (token) {
        localStorage.setItem(AUTH_TOKEN_KEY, token);
    } else {
        localStorage.removeItem(AUTH_TOKEN_KEY);
    }
} catch (error) {
    // Sin almacenamiento la sesión dura lo que la página
}

}

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

    setAuthToken(data.token);

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

const token =
    getAuthToken();

setAuthToken(null);

try {
    await fetch("/api/auth/logout", {
        method: "POST",
        headers: token
            ? { "Authorization": `Bearer ${token}` }
            : {}
    });
} catch (error) {
    // No importa si falla: el token ya se descartó en el cliente
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

const token =
    getAuthToken();

if (!token) {
    showLoginScreen();
    return;
}

try {

    const response =
        await fetch("/api/auth/me", {
            headers: { "Authorization": `Bearer ${token}` }
        });

    if (!response.ok) {
        setAuthToken(null);
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

async function loadDevices() {

const table =
    document.getElementById("devices-table");

try {

    const response =
        await fetch("/api/devices");

    if (!response.ok) {
        throw new Error("Error loading devices");
    }

    const devices =
        await response.json();

    updateCounters(devices);

    table.innerHTML = "";

    if (devices.length === 0) {

        table.innerHTML = `
            <tr>
                <td
                    colspan="5"
                    class="px-6 py-10 text-center text-slate-500"
                >
                    No hay dispositivos registrados
                </td>
            </tr>
        `;

        return;
    }

    devices.forEach(device => {

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

        row.innerHTML = `
            <td class="px-6 py-4">
                <div>
                    <p class="font-medium text-slate-900">
                        ${device.hostname}
                    </p>

                    <p class="text-xs text-slate-500 mt-1">
                        ${device.device_id}
                    </p>
                </div>
            </td>

            <td class="px-6 py-4 text-slate-600">
                ${device.operating_system || "-"}
            </td>

            <td class="px-6 py-4 text-slate-600">
                ${device.ip_address || "-"}
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

} catch (error) {

    console.error(
        "Error cargando dispositivos:",
        error
    );

    table.innerHTML = `
        <tr>
            <td
                colspan="5"
                class="px-6 py-10 text-center text-red-500"
            >
                Error al cargar dispositivos
            </td>
        </tr>
    `;
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

function showDeviceDetails(device) {

selectedDevice = device;

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

document.getElementById(
    "device-details"
).classList.remove("hidden");

loadInstalledSoftware(
    device.device_id
);

updateActionButtons();

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

if (!selectedDevice) {

    pingButton.disabled = true;
    systemInfoButton.disabled = true;

    return;
}

const isOnline =
    selectedDevice.status === "online";

pingButton.disabled =
    !isOnline;

systemInfoButton.disabled =
    !isOnline;

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

stats.classList.add("hidden");
devices.classList.add("hidden");
alerts.classList.add("hidden");
settings.classList.add("hidden");

if (view === "dashboard") {

    stats.classList.remove(
        "hidden"
    );

    devices.classList.remove(
        "hidden"
    );

} else if (view === "devices") {

    devices.classList.remove(
        "hidden"
    );

} else if (view === "alerts") {

    alerts.classList.remove(
        "hidden"
    );

    loadAlerts();

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

                const isOnline =
                    alert.type === "online";

                const dotColor =
                    isOnline
                        ? "bg-emerald-500"
                        : "bg-red-500";

                const unread =
                    alert.is_read
                        ? ""
                        : "bg-slate-50";

                return `
                    <div class="px-6 py-4 flex items-center gap-3 ${unread}">

                        <span class="w-2.5 h-2.5 rounded-full ${dotColor}"></span>

                        <div class="flex-1">

                            <p class="text-sm text-slate-900">
                                ${alert.message}
                            </p>

                            <p class="text-xs text-slate-500 mt-1">
                                ${formatDate(alert.created_at)}
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

} catch (error) {

    console.error(
        "Error cargando configuración:",
        error
    );
}

}

async function saveSettings() {

const status =
    document.getElementById(
        "settings-status"
    );

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
            : "false"
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
"remote-button"
)
.addEventListener(
"click",
startRemoteDesktop
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

await checkServer();

await loadDevices();

await refreshAlertsBadge();

const alertsSection =
    document.getElementById(
        "alerts-section"
    );

if (
    alertsSection &&
    !alertsSection.classList.contains(
        "hidden"
    )
) {
    await loadAlerts();
}

}

function startApp() {

// Solo arranca el dashboard una vez, tras autenticarse
if (appStarted) {
    return;
}

appStarted = true;

showView(
    "dashboard"
);

refreshDashboard();

setInterval(
    refreshDashboard,
    10000
);

}

initAuth();
