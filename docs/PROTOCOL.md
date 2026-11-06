# Lo que sabemos del patinete (protocolo BLE)

Todo lo de este documento se ha comprobado sobre un **Xiaomi Electric Scooter 5 Max**
(`xiaomi.scooter.5max`, firmware `2.7.0_0015.0016`, BMS `0108`). Otros modelos de la
familia (5 / 5 Pro / 5 Max) probablemente se comporten igual, pero no está verificado.

Referencia: el trabajo de ingeniería inversa de
[KuziaMother/SCOOTER_5_PRO](https://github.com/KuziaMother/SCOOTER_5_PRO) (5 Pro). Ese
repositorio no tiene licencia, así que este proyecto **no reutiliza su código**: solo los
hechos sobre el protocolo, reimplementados desde cero.

## 1. Dos "cerebros"

* **Chip Bluetooth** (Realtek RTL8762C): anuncia el servicio Xiaomi `FE95`, gestiona el
  inicio de sesión cifrado y las propiedades MIoT.
* **Controlador (MCU)**: motor y batería. Habla con el chip Bluetooth por UART.

## 2. Comportamiento observado (lo importante para Home Assistant)

| Situación | Anuncia por BLE | Acepta conexión | Datos |
|---|---|---|---|
| Encendido | Sí | Sí | **En vivo** |
| Encendido + cargando | Sí | Sí | En vivo (la pantalla se apaga sola, el patinete no) |
| Apagado (horas, días) | **Sí, siempre** | Sí | **Foto congelada** del momento en que se apagó |
| Apagado + cargando | Sí | Sí | Foto congelada (`cargando` sigue a 0, el voltaje no se mueve) |
| Conectado al móvil (Mi Home) | **No** | No | — |

Consecuencias:

* "En rango" se detecta escuchando anuncios (sirve hasta un proxy pasivo, por ejemplo un Shelly).
* Para **leer** hace falta una conexión activa: adaptador Bluetooth del host o un **proxy
  ESPHome con `active: true`**. Los proxies Shelly no valen.
* **Encendido / apagado** no lo dice ninguna propiedad: se deduce de si los valores
  rápidos (corriente, potencia, voltaje) **cambian** entre lecturas. Dos lecturas a 20 s
  pueden coincidir aun estando encendido; hay que muestrear hasta ~45-60 s.
* Al **encender** el patinete se corta cualquier conexión BLE abierta.
* Con el cargador puesto y el patinete encendido, **nunca se apaga solo** (observado más
  de 12 h). Sin cargador, se apaga por inactividad (pendiente de medir si una conexión
  abierta lo evita).
* La lectura **no despierta** al patinete (la pantalla no se enciende).
* Enchufar o desenchufar el cargador con el patinete apagado **no** actualiza la foto.

## 3. GATT (servicio `0000fe95-0000-1000-8000-00805f9b34fb`)

| Característica | Uso |
|---|---|
| `0x0010` | Control: `A4` (saludo), `20 00 00` (inicio de login), resultado `21` (ok) / `22` (fallo) |
| `0x0016` | Canal con tramas para el login |
| `0x001A` | Canal con tramas app → patinete (peticiones MIoT) |
| `0x001B` | Canal con tramas patinete → app (respuestas MIoT) |

### Tramas (little-endian)

Cada notificación empieza por un `u16` de secuencia. `seq = 0` es un paquete de control:

```
CTR       00 00 | 00 | canal | nº_fragmentos u16
ACK       00 00 | 01 | estado | [seq u16 ...]   estado: 01 listo, 00 recibido, 05 reenvía estos
SINGLE    00 00 | 02 | canal | datos
MNG       00 00 | 04 | subtipo | max_paquetes | max_dmtu   (se responde con 05 y los mismos bytes)
DATOS     seq u16 (>= 1) | fragmento
```

Envío: `CTR` → esperar `ACK 01` → fragmentos → esperar `ACK 00` (o reenviar los de `ACK 05`).
Recepción: llega `CTR` → responder `ACK 01` → recoger fragmentos → responder `ACK 00`.
En el canal MIoT los fragmentos son de **18 bytes**; en el login, de `max_dmtu − 2`.

## 4. Inicio de sesión ("security chip" de Mijia)

1. Escribir `A4` en `0x0010`; el patinete manda `MNG` en `0x0016` → responder `MNG_ACK`.
2. Escribir `20 00 00` en `0x0010`.
3. Enviar la clave pública P-256 de la app (64 bytes X‖Y) por el canal **3** de `0x0016`.
4. Recibir la clave pública del patinete (64 bytes).
5. `sk = HKDF-SHA256(ECDH(x) ‖ LTMK, salt="smartcfg-login-salt", info="smartcfg-login-info", 64 bytes)`.
6. Enviar por el canal **5**: `AES-CCM(clave=sk[16:32], nonce=10 11 … 1b, tag 4)` de
   `CRC32(clave pública del patinete)` en little-endian.
7. El patinete responde en `0x0010`: `21` = correcto, `22` = clave incorrecta.

La **LTMK** (32 bytes) es la clave de cada patinete. Está en la cuenta de Mi Home; ver
[OBTENER_CLAVE.md](OBTENER_CLAVE.md).

## 5. Propiedades MIoT

Petición (antes de cifrar): `[u16 longitud|0x2000][u16 id][u8 op][u8 nº=1][siid u8][piid u16][u16 (tipo<<12)|longitud][valor]`
con `op = 2` para leer y `op = 0` para escribir.

Cifrado de cada mensaje: `[contador u16][AES-CCM tag 4]`, nonce = `iv ‖ 00000000 ‖ contador u32`.

* app → patinete: clave `sk[16:32]`, iv `sk[36:40]`
* patinete → app: clave `sk[0:16]`, iv `sk[32:36]`

Respuesta: objeto correcto `[siid][piid][estado=0][tipo|longitud][valor]`; con error o al
escribir, solo `[siid][piid][estado]` (5 bytes).

**El patinete solo responde al primer objeto de cada petición**: hay que leer de una en una.

Tipos: 0 bool, 1 uint8, 2 int8, 3 uint16, 4 int16, 5 uint32, 6 int32, 9 float32, 10 texto.

### Tabla (escalas verificadas en el 5 Max)

| siid.piid | Nombre | Escala / unidad | Escribible |
|---|---|---|---|
| 1.1 | Modo de conducción | — | |
| 1.2 | Batería | % | |
| 1.3 | Capacidad restante | mAh (≈ 9700 al 100 %) | |
| 1.4 | Voltaje | ×0,01 V | |
| 1.5 | Corriente | ×0,01 A (negativa cargando) | |
| 1.6 | Potencia | ×0,01 W | |
| 1.7 | Autonomía | ×0,01 km | |
| 1.8 | Código de error | — | |
| 1.9 | Distancia del trayecto | ×0,01 km | |
| 2.1 | Velocidad media | ×0,01 km/h | |
| 2.2 | Bloqueado | bool | (sí, pero la integración no lo expone) |
| 2.3 | Control de crucero | bool | sí |
| 2.4 | Luz trasera fija | bool | sí |
| 2.5 | Frenada regenerativa | 30/60/90 | sí (uint8) |
| 2.6 | Kilómetros totales | ×0,01 km | |
| 2.7 | En marcha | — | |
| 2.8 | Tiempo de trayecto | s | |
| 2.9 | Velocidad máxima | ×0,01 km/h | |
| 2.10 | ASR | bool | |
| 2.11 | Algoritmo de autonomía | bool | |
| 2.12 | Luz automática | bool | sí |
| 2.13 | Control de tracción | bool | sí |
| 2.14 | Bajada inteligente | bool | sí |
| 2.15 | Freno en cuesta | bool | sí |
| 2.16 | Luz ambiental | 0/1/2 | sí (uint8) |
| 2.17 | Visible por Bluetooth | bool | (no expuesto: podría dejarlo invisible) |
| 2.18 | "Apagado falso" | bool | siempre 0 en nuestras pruebas |
| 3.1 | Estado de la batería | — | |
| 3.2 / 3.3 | Temperatura batería / patinete | °C | |
| 3.4 | Aviso de bloqueo | bool | |
| 3.5 | Unidad (1 = km) | — | |
| 3.6 | Código de vinculación | **secreto** | |
| 3.7 | Mantenimiento de ruedas | texto `EIIIRRR` | |
| 3.8 | Fecha de activación | texto | |
| 3.10 | Cargando | — | |
| 3.11 | Ciclos de carga | — | |
| 3.12 | Salud de la batería (SOH) | % | |
| 4.1 | Fecha de fabricación | texto | |
| 4.2 / 4.4 | Nº de serie batería / patinete | texto | |
| 4.3 / 4.5 | Firmware BMS / patinete | texto | |
| 4.6 | Restablecer ajustes | **no tocar** | |
| 4.7 | Batería extra | hex: energía total (Wh, 3 bytes), ?, descargas profundas | |
| 4.8 | Batería extra 2 | hex: fecha de temperatura extrema + segundos de carga | |
| 6.1–6.5 | Últimos 10 trayectos | 2 por propiedad, 16 dígitos: duración (×0,1 min), distancia (×0,1 km), media (×0,1 km/h), ¿? | |

Notas:

* En los trayectos, el cuarto campo **no es** la velocidad máxima (a veces es menor que la media o 0).
* Un ajuste de luz a 0 **no apaga** el faro: el faro se maneja con el botón. "Luz trasera
  fija" a 0 solo quita el modo "trasera siempre encendida".
