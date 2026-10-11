# Plan: cable/driver virtual propio — "AudioEnhancer Virtual Cable"

- **Fecha**: 2026-10-11
- **Rama**: `feat-driver-cable` (base `c9d4aec`, sobre `feat-enhancer-fx`)
- **Estado**: plan afinado + validación de mecanismos en curso (F0 preparación)
- **Restricciones del proyecto**:
  1. **Todo código abierto** (driver incluido).
  2. El cable debe ser **gratuito** para el usuario final y **hecho por nosotros**.
  3. Se permite **reverse engineering solo para validar cómo funcionan** las
     soluciones existentes (entender mecanismos; no copiar código cerrado).

## 0. Problema → causa → solución

- **Problema**: la app depende de VB-Cable (tercero, cerrado) para interceptar el
  audio del sistema; condiciona instalación, licencias, soporte y evolución.
- **Causa**: el DSP vive en Python y necesita un *endpoint* donde las apps
  reproduzcan y del que la app capture. Hoy lo aporta el driver de VB-Audio; la
  app solo captura su loopback WASAPI y renderiza a la salida física.
- **Solución**: un **VAD (Virtual Audio Device) propio de kernel, render-only**,
  sobre el que la app captura por **loopback del motor de audio de Windows**
  (audiodg) — el mismo camino que ya usa con VB-Cable, **sin cambios en la app**.

---

## 1. Validación de mecanismos (RE)

### 1.1 Evidencia local (esta máquina, 2026-10-11)

| Elemento | VB-Cable | FxSound (fxvad) |
|---|---|---|
| Versión driver | 3.3.1.7 (2024-10-07) | 14.1.0.0 (2021-03-17) |
| INF | `vbmmecable64_win10.inf` (oem35) | `fxvad.inf` (oem27) |
| Servicio | `VBAudioVACMME` (demand, Running) | `FXVAD` (demand, Running) |
| .sys | `vbaudio_cable64_win10.sys` (140.760 B), cargado desde DriverStore (`DefaultDestDir=13`) | `fxvad.sys` (326.656 B), en `system32\drivers` |
| Firma | `.cat` firmado (12.862 B) | `fxvadNTAMD64.cat` (10.590 B) |
| Endpoints | render `CABLE Input` + capture `CABLE Output` + render `CABLE In 16ch` (3 vía MediaCategories) | **solo render** `FxSound Speakers` (KSCATEGORY_CAPTURE comentada en INF) |
| Arquitecturas | NTamd64 **y NTARM64** (incluye `vbaudio_cable64arm_win10.sys`) | NTx86/NTamd64 (sin ARM64) |
| Enumeración | root-enumerado (`%HardwareId%`) | root-enumerado (`Root\FXVAD`) |

- Endpoints WASAPI en la máquina: `CABLE Input [Loopback]` 2ch/48k (el que usa la
  app), `FxSound Speakers [Loopback]` 2ch/48k, `CABLE In 16ch [Loopback]` 2ch/48k.
  En MME/DirectSound los cables aparecen a 44.1 kHz/16ch (legacy).
- **MediaCategories** (registro, escritas por los INF):
  - `{6518B5E9-772E-48E2-B110-1752F65934E8}` = "FxSound Speakers"
  - `{B961F7FC-…}` = "CABLE Output"; `{B961F7FD-…}` = "CABLE Input"; `{B961F7FD-…B75}` = "CABLE In 16 Ch"
- **Coexistencia**: hay 3 VADs en la máquina (VB-Cable, fxvad, SteelSeries Sonar
  VAD) y conviven sin conflicto.

### 1.2 fxvad por dentro (fuentes abiertas: `github.com/fxsound2/fxsound-driver`)

- Es el **sample MSVAD (WDK 8.1)** sin modernizar: `adapter.cpp`
  (`PcInitializeAdapterDriver`, MAX_MINIPORTS=3), `basewave.cpp`/`basedma.cpp` →
  **WaveCyclic** (no WaveRT), `basetopo.cpp` (topología PortCls), `hw.cpp`
  (hardware simulado por timer), `pcmex/` (`minwave`, `mintopo`, `wavtable`).
- Licencia real del repo: **AGPL-3.0 + MS-LPL 1.1** (no MS-PL).
- INF: `Class=MEDIA`, `PnpLockDown=1`, root-enum `Root\FXVAD`, servicio demand
  (`ServiceType=1, StartType=3`), `AssociatedFilters="wdmaud,swmidi,redbook"` +
  mapeo legacy wdmaud (wave/midi/mixer), entrada MediaCategories con el nombre
  del endpoint, `EP\0` con `Association=KSNODETYPE_SPEAKER`; el `OEMFormat`
  (48k/16-bit/2ch) está comentado → el formato mix lo determina el rango PCM de
  `wavtable.h` (en runtime: 2ch/48k).
- **Qué hace**: exponer endpoint render + topology y descartar el audio escrito
  (sumidero simulado). **Qué NO hace**: captura, loopback propio, DSP,
  conversión de formato, exclusividad/low-latency. El DSP de FxSound vive en la
  **app**; el `[Loopback]` lo provee el **motor de audio de Windows**.

### 1.3 VB-Cable (cerrado) — según la evidencia local

- Cable completo: render + capture con puente interno en el driver; además el
  motor expone el `[Loopback]` del render.
- Para nosotros el loopback del motor **basta** y evita implementar el puente.

### 1.4 Sysvad (base elegida) — referencia

- WaveRT (+offload), **KMDF+PortCls**, paquete componentizado `.inx` (obligatorio
  desde Win10 1809), interfaces modernas: wave = `AUDIO+RENDER+REALTIME`;
  topo = `AUDIO+TOPOLOGY`; `EP\0` con `Supports_EventDriven_Mode`; servicio
  demand con binario en `%13%` (DriverStore); `AssociatedFilters` +
  MediaCategories; APOs opcionales. Su "loopback" de muestra es un tono
  simulado (se elimina; el loopback real lo da el motor).

### 1.5 Qué replicar y qué simplificar

| Replicamos (probado) | Simplificamos (fuera de Sysvad) |
|---|---|
| root-enum MEDIA, render+topology, servicio demand, wdmaud legacy para MME, MediaCategories, cero DSP en kernel, captura por `[Loopback]` | offload, tono de loopback, endpoints HDMI/SPDIF/mics/BT/USB, keyword detector, APOs, INFs de extensión; un solo endpoint y formato fijo |

---

## 2. Plan afinado

### 2.1 Base elegida + licencia

| | Sysvad | fxvad | Virtual-Audio-Driver (VirtualDrivers) |
|---|---|---|---|
| Licencia | **MS-PL** | AGPL-3.0 + MS-LPL 1.1 | MIT (propio) + MS-PL (derivado) |
| Audio | WaveRT (+offload) | WaveCyclic (año 2000) | WaveRT (derivado de Sysvad) |
| ARM64 | Configurable con WDK (validar en F0) | No | Declara x64 + ARM64 |
| Mantenimiento | Sample oficial vigente | 13 commits | Beta, 1 mantenedor |
| Esfuerzo v1 | Medio (recortar) | Medio (legacy + licencia) | Bajo (ya recortado) |
| Veredicto | **Base elegida** | Solo RE | Referencia / préstamo MIT |

- **Elegida: Sysvad (MS-PL)**. Los archivos derivados conservan cabecera y
  `LICENSE-MS-PL`; el código nuevo va **MIT**; se publica
  `THIRD_PARTY_NOTICES.md`. Los binarios pueden distribuirse (MS-PL 3(D)); no se
  usan marcas de Microsoft (3(A)). Sin AGPL en el repo.
- **Rechazo de fxvad como base**: AGPL + MS-LPL sobre sample de 1997-2000,
  WaveCyclic sin ARM64. Su valor: validación de mecanismo e INF mínimo.

### 2.2 Diseño v1

- **Dispositivo**: "AudioEnhancer Virtual Cable" (render), root-enum
  `Root\AudioEnhancerVirtualCable`; servicio `AECable` demand.
- **Formato**: 48 kHz / 2 ch; PCM 16/24/32 + float32; default/mix **32f** (sin
  SRC en kernel). Multichannel fuera de v1 (la app procesa estéreo).
- **INF** (patrón Sysvad componentizado): `Class=MEDIA`, `PnpLockDown=1`,
  `[Manufacturer] NT$ARCH$.10.0...22621` → `Root\…`; Include ks+wdmaudio;
  `CopyList=13` (DriverStore); `AssociatedFilters=wdmaud`; Interfaces: wave
  `AUDIO+RENDER+REALTIME`, topo `AUDIO+TOPOLOGY`, **sin CAPTURE**; `EP\0`:
  `Association=KSNODETYPE_SPEAKER` + `Supports_EventDriven_Mode=1`; nombre en
  MediaCategories; servicio `ServiceType=1/StartType=3` + sección KMDF;
  branding opcional (como fxvad).
- **Captura**: `[Loopback]` del render (OS) → sirve a la app y a OBS.
- **La app no cambia**: `CABLE_KEYWORDS` incluye "cable" y
  `VIRTUAL_CABLE_KEYWORDS` "virtual" (`constants.py:84-85`), y
  `pick_capture_source` prefiere el loopback del mismo dispositivo de salida
  (`device_utils.py:91-122`).
- **No** se cambia el dispositivo por defecto de Windows en v1.
- **Repo nuevo separado** `audioenhancer-driver`:
  `driver/` (derivado Sysvad), `tools/` (build / sign-test / install /
  uninstall), `docs/`, `LICENSE` (MIT), `LICENSE-MS-PL`,
  `THIRD_PARTY_NOTICES.md`.

### 2.3 Firma e instalación (proyecto gratuito y abierto)

Estado (abr-2026): la **Windows Driver Policy** (KB5083631) retiró la confianza
por defecto a drivers cross-signed; solo valen WHCP (Hardware Dev Center) o
allowlist. Azure Trusted Signing es user-mode y **no** sustituye al EV para HDC.
SignPath Foundation firma gratis OSS pero en user-mode: sirve para la app y el
instalador, **no para el `.sys`**.

**(a) Tu máquina hoy — test signing (gratis):**

1. VS2022 + WDK11 + SDK; compilar x64.
2. `bcdedit /set testsigning on` → si responde "protected by Secure Boot
   policy": desactivar Secure Boot en UEFI (suspender BitLocker) y reiniciar;
   aparece marca "Test Mode".
3. Firma de prueba (VS "Test Sign" o cert propio + Inf2Cat/signtool).
4. `devcon install <aecable.inf> Root\AudioEnhancerVirtualCable` (devcon crea el
   device root); reiniciar `audiosrv` si hace falta.
5. Verificar en Panel de sonido + loopback WASAPI + app E2E.
6. Desinstalar: `devcon remove Root\…` +
   `pnputil /delete-driver oemXX.inf /uninstall /force`.

Implicaciones: Secure Boot OFF, watermark, no producción. Alternativa con
Secure Boot ON: firma **preproducción WHCP** (HDC) + `EnableUefiSbTest`
(Win11 22H2+) — requiere ya el EV.

**(b) Distribución pública gratis para el usuario:**

| Vía | Coste | Carga en Windows stock (Secure Boot ON) | Notas |
|---|---|---|---|
| BYO-build: fuentes + scripts; cada usuario compila y test-signa | 0 | No (Secure Boot off, Test Mode) | Siempre disponible; UX pobre |
| **Attestation signing (HDC) con EV cert** | EV ~250-600 USD/año (donaciones/comunidad) | **Sí** en Win10/11 client | Firmado por Microsoft, sin HLK; la vía del release |
| WHQL (HLK) | EV + esfuerzo HLK | Sí | Solo para Windows Update/certificación; fuera de v1 |

**(c) Lo que NO se puede gratis**: que un `.sys` sin firma HDC cargue en Windows
de fábrica con Secure Boot activo. "Custom kernel signers" es enterprise y
Trusted Signing/SignPath no cubren kernel. No hay atajo legítimo.

### 2.4 Fases F0-F4

| Fase | Entregable | Criterio de salida | Esfuerzo | Riesgo principal |
|---|---|---|---|---|
| F0 PoC | Sysvad compilado y recortado a render+topology ("v0"); test-sign/install en PC dev; app apuntada al cable | Audio E2E 30 min sin dropouts; device+loopback visibles; sin fallos en Device Manager; rollback documentado | 1-2 sem | Secure Boot/BitLocker en la máquina dev |
| F1 Producto dev | Renombrado (INF/servicio/HWID/MediaCategories), formato 48k/2ch 32f, scripts install/uninstall, coexistencia VB-Cable/fxvad/Sonar | Scripts idempotentes en VM limpia (test-sign); app autoselecciona; nombre correcto en Panel | 2-3 sem | Nombres truncados en MME; restos de device |
| F2 Robustez | Sleep/resume, reinicio de audiosrv, formatos 16/24/32f, exclusivo best-effort, Driver Verifier, burn-in 72 h, comparativa de latencia vs VB-Cable | Sin fallos de verifier; latencia documentada; sin crashes | 2-3 sem | WaveRT en 24H2/25H2; pull mode |
| F3 Firma | EV + Partner Center; attestation; decisión ARM64; paquete firmado por MS | Instala en VM limpia Win11 Secure Boot ON sin Test Mode; Event log limpio; uninstall limpio | 3-5 sem (lead time cert) | Coste/financiación EV; particularidades HDC (root-enum, atributos DRM) |
| F4 Release | Repo público + CI (build+test-sign en Actions), docs (BUILD/INSTALL/FAQ firma), assets, avisos de licencia | Un tercero compila y firma siguiendo docs; release firmada probada en 3 VMs; VB-Cable como fallback permanente | 1-2 sem | Mantenimiento continuo del driver |

Total ~9-15 semanas part-time; F3 es el cuello (cert + cola de HDC).

### 2.5 Preguntas abiertas (solo bloqueantes)

1. **Nombre final y proveedor** ("AudioEnhancer Virtual Cable" / Provider
   "AudioEnhancer") — entra en INF, `.cat` y cert; decidir antes de F1.
2. **EV cert: ¿quién lo financia y a nombre de quién se emite?** — bloquea F3;
   plan B sin fondos: release con instrucciones de test-signing o mantener
   VB-Cable.
3. ¿Objetivo Windows Update/WHQL? (default: no; no bloquea v1).

Defaults ya decididos: x64 v1 (ARM64 opcional en F3), estéreo, sin capture
endpoint, sin cambio de dispositivo por defecto, coexistencia, repo aparte.

---

## 3. Registro de validación

### 3.1 Hecho (2026-10-11)

| # | Validación | Resultado |
|---|---|---|
| 1 | Inspección local de drivers (versiones, servicios, paquetes, INF, endpoints) | ✅ Tabla §1.1 (VB-Cable 3.3.1.7 y fxvad 14.1.0.0; ambos root-enum MEDIA, servicio demand) |
| 2 | MediaCategories + endpoints render (registro MMDevices) | ✅ GUIDs y nombres confirmados (§1.1); 3 endpoints activos |
| 3 | Entorno F0 (VS2022 + WDK + herramientas de firma) | ❌ **Pendiente instalar**: VS2022 y WDK ausentes; devcon/signtool no en PATH. Descargas gratis (VS Community + WDK) |
| 4 | **Loopback de VAD render-only con nuestro stack** (`tools/validacion/loopback_vad.py`) | ✅ **OK**: 20,4 s capturados del `[Loopback]` de "FxSound Speakers" (VAD render-only, sin app FxSound corriendo): 937 chunks, **0 silencios**, RMS medio 0.3534 (teórico 0.3536), pico FFT 421.9 Hz (bin de 46.9 Hz → 440 Hz correcto) |

**Conclusión de la validación #4**: un endpoint **render-only** + loopback del
motor entrega audio continuo y correcto a `pyaudiowpatch` — el mecanismo exacto
del diseño v1 queda confirmado sin escribir una línea de driver.

### 3.2 Próximos pasos de validación (pendientes)

1. Instalar VS2022 Community + WDK11 (gratis) → compilar Sysvad tal cual (sin
   recortar) y validar que el entorno de build funciona.
2. F0: recortar Sysvad a render+topology, renombrar a v0, test-sign + instalar
   en esta máquina (con Secure Boot OFF o `EnableUefiSbTest`).
3. Apuntar la app (copia de config) al loopback del cable v0 y medir E2E:
   huecos, latencia, CPU — comparar contra VB-Cable y contra el loopback de
   FxSound Speakers.
4. Medir latencia añadida del VAD (impulso loopback→captura) y comparar
   WaveRT vs VB-Cable.
5. Verificar hot-unplug/reinstalación y coexistencia con los 3 VADs actuales.
