# Xiaomi Scooter BLE para Home Assistant

Integración local para patinetes eléctricos Xiaomi de la serie 5 (probada en el
**Electric Scooter 5 Max**) por Bluetooth, sin la nube de Xiaomi una vez configurada.

* **En rango**, **encendido** y **cargando**.
* Batería, voltaje, corriente, potencia, autonomía, kilómetros, temperaturas, ciclos,
  salud de la batería, energía total y datos del trayecto.
* **Control de carga**: apaga el enchufe inteligente del cargador al llegar al límite que
  elijas (por ejemplo, el 80 %).
* Ajustes del patinete como interruptores: luz trasera fija, luz automática, luz ambiental,
  control de crucero, control de tracción, bajada inteligente y freno en cuesta.

Todo lo aprendido sobre el patinete está en [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Requisitos

* Home Assistant 2024.12 o posterior.
* Un **adaptador Bluetooth con conexión activa** al alcance del patinete: el Bluetooth
  del propio servidor o un **proxy ESPHome con `active: true`**
  ([plantilla](esphome/scooter-bt-proxy.yaml)). Los proxies Shelly solo sirven para
  saber si el patinete está en rango, no para leerlo.
* El patinete vinculado a tu cuenta de **Mi Home** (para obtener la clave).

## Instalación

### Manual (repositorio privado)

HACS no admite repositorios privados, así que mientras este lo sea:

1. Copia la carpeta `custom_components/mi_scooter_ble` en `/config/custom_components/`
   de Home Assistant, por ejemplo con el complemento **Samba share**, **Studio Code Server**
   o **Terminal & SSH**.
2. Reinicia Home Assistant.
3. Ajustes → Dispositivos y servicios: el patinete debería aparecer como **descubierto**.
   Si no, **Añadir integración → Xiaomi Scooter BLE**.

### HACS (solo si el repositorio es público)

HACS → ⋮ → **Repositorios personalizados** → URL de este repositorio, tipo
**Integración** → instalar **Xiaomi Scooter BLE** → reiniciar.

## Configuración

1. **Clave del patinete**: QR de Mi Home + PIN, o clave manual. Ver
   [docs/OBTENER_CLAVE.md](docs/OBTENER_CLAVE.md).
2. **Opciones** (botón «Configurar» de la integración):
   * **Enchufe del cargador**: por ejemplo `switch.shelly_patinete`.
   * **Sensor de potencia del enchufe** (opcional): permite saber si carga con el patinete apagado.
   * **Umbral de reposo** del cargador (W).
   * **Intervalo de comprobación** con el patinete apagado.
   * **Mantener la conexión mientras está encendido**.
3. Activa **Control de carga** y ajusta **Límite de carga**.

### Flujo de carga recomendado

1. Enchufa el cargador y **enciende el patinete**. Encendido y con el cargador puesto, no
   se apaga solo; la integración mantiene la conexión y lee cada 10 s.
2. Al llegar al límite, la integración **apaga el enchufe** y lanza el evento
   `mi_scooter_ble_charge_stopped`, que puedes usar en automatizaciones (por ejemplo, para
   enviarte una notificación).
3. Si el patinete está apagado, la batería no se puede leer (se queda congelada). Con un
   sensor de potencia configurado, el enchufe se corta cuando el cargador lleva 5 minutos
   por debajo del umbral, es decir, cuando ha terminado.

## Cómo funciona

| Estado | Qué hace la integración |
|---|---|
| Fuera de rango | Mantiene los últimos valores; «En rango» = apagado |
| En rango, apagado | Se conecta cada *N* minutos, comprueba si los valores se mueven y se desconecta |
| Encendido y cargando | Conexión permanente, lectura cada 10 s, control de carga |
| Encendido sin cargar | Lee y se desconecta (o se queda conectado si lo activas en opciones) |

Mientras la integración está conectada, la app Mi Home no puede conectarse al patinete.

## Herramientas

* `tools/scooter_cli.py`: lee, observa o cambia ajustes desde un PC (`pip install bleak cryptography`).
* `tests/`: tests del protocolo (`pytest`).

## Licencia

MIT. El inicio de sesión en Mi Cloud sigue el esquema público de
[Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor)
(MIT, © Piotr Machowski). Proyecto no oficial, sin relación con Xiaomi.
