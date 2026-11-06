# Cómo se obtienen la MAC y la clave del patinete

El patinete solo acepta conexiones de quien conoce su **LTMK**: una clave de 32 bytes
propia de cada unidad, que Xiaomi guarda en la cuenta de Mi Home a la que está vinculado.

## Opción A — Desde Home Assistant (recomendada)

Al añadir la integración, elige **«Obtenerla de Mi Home (código QR + PIN)»**:

1. Home Assistant muestra un **código QR** de Xiaomi.
2. En el móvil: **Mi Home → Perfil → icono de escanear** (arriba a la derecha) → escanea
   el código → **confirma** el inicio de sesión. No hace falta escribir tu contraseña en
   ningún sitio.
3. La integración busca el patinete en tu cuenta (en todos los servidores de Xiaomi; el
   nuestro estaba en `de`) y pide su clave (`/share/askbluetoothkey`).
4. Si en Mi Home protegiste el patinete con un **PIN**, Xiaomi devuelve la clave cifrada
   y la integración te pide ese PIN. Se descifra en local:
   `AES-128-CBC(clave = MD5(PIN), iv = 7aa4c68c590d4031b980d98b41023800)`.
5. Antes de guardar, si el patinete está al alcance, se prueba la clave conectándose a
   él: si el patinete responde `22`, el PIN es incorrecto y se vuelve a pedir.

La sesión de Mi Cloud no se guarda: solo se usa durante este asistente.

**¿No recuerdas el PIN?** En Mi Home → patinete → ⋮ → «Contraseña» (o similar) se puede
cambiar. La app no lo muestra, pero sí deja poner uno nuevo.

## Opción B — Clave manual

Si ya tienes la LTMK (64 caracteres hexadecimales), elige **«Ya tengo la clave»** y pégala.

Para sacarla desde un ordenador, sin Home Assistant, se puede usar la misma lógica con
[Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor)
para el inicio de sesión por QR y después la petición `/share/askbluetoothkey`
(`{"type":"own","did":"<did>","keyid":0}`) más el paso del PIN descrito arriba.

## La MAC

* La integración detecta el patinete sola: anuncia un nombre como `xiaomi.scooter.5max`
  con el servicio `FE95`.
* También aparece en la lista de dispositivos de Mi Cloud y en Mi Home → patinete → ⋮ →
  Información.

## Cuidado

* **La LTMK da acceso total al patinete**, incluida la escritura de ajustes. No la
  compartas ni la subas a ningún repositorio (`.gitignore` ya excluye `*.hex` y `secrets/`).
* Si desvinculas el patinete de Mi Home y lo vuelves a vincular, la clave cambia: reconfigura la integración.
