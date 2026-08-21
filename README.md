# Audio Enhancer FxStyle

Real-time audio enhancer for Windows inspired by FxSound. Captures rendered audio via **WASAPI loopback**, applies real-time DSP processing, and plays it back on a physical output device you select.

> Open-source experimental project. Latency, stability, and compatibility depend on your hardware, drivers, and chosen audio route.

## Features

- System audio capture via PyAudioWPatch / WASAPI loopback.
- 9-band equalizer.
- Bass and treble boost.
- Volume control with smooth ramps.
- RMS compressor and soft limiter.
- Spectrum analyzer.
- Low-latency ring buffer with clock-drift compensation.
- Anti-click crossfades on underruns.
- Built-in and custom presets.
- English / Spanish interface with A/B mode to compare processed vs. raw audio.

## Interface Language

The app detects your Windows language automatically on startup:

- Windows in English → English interface.
- Windows in Spanish → Spanish interface.
- Any other language → English as fallback (default).

No configuration needed. If you change your Windows language, the app will pick it up on the next launch.

---

## Quick Start (No Coding Required)

You don't need to install Python or open a terminal. Just follow these steps:

### Step 1 — Download Audio Enhancer

Go to the **Releases** section on GitHub and download the `.exe` file from the latest version:

👉 [Download Audio Enhancer from Releases](../../releases/latest)

> If GitHub shows several files, pick the `.exe` file (not the "Source code" zip).

> **Windows SmartScreen warning**: the executable is not digitally signed, so Windows may show a "Windows protected your PC" warning. Click **More info** → **Run anyway** to proceed.

### Step 2 — Install VB-CABLE (Virtual Audio Cable)

Audio Enhancer needs a "virtual cable" to receive the sound from your system, process it, and send it to your real speakers or headphones. That virtual cable is **VB-CABLE**, a free and safe driver made by VB-Audio.

#### 2a — Download the right version for your PC

| Your Windows version | File to download | Direct link |
|---|---|---|
| **Windows 11** (any edition) | `VBCABLE_Driver_Pack45.zip` (1.3 MB) | [Download](https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip) |
| **Windows 10 — 64-bit** | `VBCABLE_Driver_Pack45.zip` (1.3 MB) | [Download](https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip) |
| **Windows 10 — 32-bit** | `VBCABLE_Driver_Pack45.zip` (1.3 MB) | [Download](https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip) |
| **Windows 8 / 7 / Vista / XP** | `VBCABLE_Driver_Pack43.zip` (1.1 MB) | [Download](https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack43.zip) |

> Not sure if your Windows is 32-bit or 64-bit? Press **Win + I** to open Settings → **System** → **About**. Look for **System type** — it will say "64-bit operating system" or "32-bit operating system". The vast majority of modern PCs are 64-bit, so **Pack 45** is the right choice for most users.

You can also visit the official page: [vb-audio.com/Cable](https://vb-audio.com/Cable/)

#### 2b — Install VB-CABLE

1. Open the `.zip` file you just downloaded (no special software needed — just double-click it).
2. **Extract** all files to a folder (e.g., your Desktop).
3. Inside the extracted folder you will find a file called **`VBCABLE_Setup_x64.exe`** (for 64-bit) or **`VBCABLE_Setup.exe`** (for 32-bit). **Right-click** it and select **"Run as administrator"**.
4. Accept the Windows permission prompt (User Account Control).
5. Click through the installer — there are no complicated options, just **Next → Install → Finish**.
6. **Restart your computer** when the installer asks you to. This is important — VB-CABLE won't appear in your sound devices until you reboot.

#### 2c — Verify VB-CABLE is installed

After restarting:

1. Right-click the **speaker icon** in your Windows taskbar (bottom-right corner).
2. Click **Sound settings** (or "Open volume mixer" → then the gear icon).
3. Scroll down to **Advanced** → click **More sound settings** (or "Sound control panel").
4. Go to the **Playback** tab — you should see **`CABLE Input`**.
5. Go to the **Recording** tab — you should also see **`CABLE Input (VB-Audio Virtual Cable)`**.

If you see it in both tabs, VB-CABLE is installed correctly.

### Step 3 — Configure your audio routing

Now you need to tell Windows to send all its sound through the virtual cable, so Audio Enhancer can process it:

1. Right-click the **speaker icon** in the taskbar → **Sound settings**.
2. In the **Output** section (or "Choose your output device"), select **`CABLE Input`**.
3. All audio from your apps (browser, games, music, etc.) will now go through the virtual cable instead of directly to your speakers. Don't worry — you won't hear anything yet; that's normal. Audio Enhancer will be the bridge.

### Step 4 — Launch Audio Enhancer and start processing

1. Double-click the **`AudioEnhancerFxStyle.exe`** file you downloaded in Step 1.
2. In the app, find the **Capture (loopback)** dropdown and select the device that contains **`CABLE Input`**.
3. Find the **Output (physical)** dropdown and select your **real speakers or headphones** (not CABLE Input).
4. Click **Start system audio**.
5. Adjust the EQ and effects to your liking. You should now hear enhanced audio!

### What does VB-CABLE actually do?

Think of VB-CABLE as an invisible audio wire inside your computer:

```
Your apps (browser, games, etc.)
        ↓
   CABLE Input  ← virtual cable (VB-CABLE)
        ↓
  Audio Enhancer FxStyle  ← EQ, compressor, limiter
        ↓
  Speakers / Headphones  ← real output device
```

Without the virtual cable, Audio Enhancer has no way to intercept the sound coming from your apps. VB-CABLE simply creates a pair of virtual audio devices (an input and an output) that act as a bridge. It is **not** malware, not a player, and not a spyware — it's a well-known, widely-used open audio driver trusted by musicians and audio engineers worldwide.

> **Important**: Never select `CABLE Input` as the **physical output** inside Audio Enhancer. The output must always be your real speakers or headphones. Selecting the same device for both capture and output would cause echo or feedback (loud screeching noise).

---

## Installing from Source

If you're a developer and want to run from source code:

```bat
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python AudioEnhancer_FxStyle.py
```

You can also use `AudioEnhancer_instalar_seguro.bat`, which creates the virtual environment and installs dependencies for you.

## Recommended Audio Route

1. Install and configure VB-CABLE if you need to capture all system audio.
2. Set `CABLE Input` as the default output device in Windows.
3. In the app, select the `CABLE Input` loopback as capture.
4. Select real speakers or headphones as output.
5. Do not select the same device for capture and output — that would cause echo or feedback.
6. Disable FxSound or other duplicate audio processors during testing.

## Building an Executable

```bat
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller.exe --clean --noconfirm --onefile --windowed ^
  --name AudioEnhancerFxStyle ^
  --icon assets\app.ico ^
  --add-data "assets;assets" ^
  AudioEnhancer_FxStyle.py
```

The executable will be generated at `dist\AudioEnhancerFxStyle.exe`. Binaries, virtual environments, and build artifacts are not part of the source repository.

## Quick Validation

```bat
python -m py_compile AudioEnhancer_FxStyle.py
python -c "import numpy, scipy, PySide6, pyaudiowpatch; print('Dependencies OK')"
```

## Known Limitations

- This is a user-mode application, not an audio driver or a global Windows APO.
- The loopback → processing → output path adds latency.
- Capture and output may use different hardware clocks; the ring buffer attempts to compensate for drift.
- Bluetooth, USB, and drivers with large buffers may increase latency.
- Test at low volume first to avoid unexpected peaks.
- Actual audio processing depends on installed devices and cannot be guaranteed by a synthetic test alone.

## Support / Donate

This project is developed and maintained in my free time, for free. If Audio Enhancer FxStyle has been useful to you and you'd like to support its development, you can make a donation. Any amount helps and is greatly appreciated:

👉 [Donate via PayPal](https://paypal.me/BLACWARG)

Thank you for your support!

---

## Contributing

Contributions are welcome. When opening an issue, please include:

- Windows version.
- Capture and output devices.
- Sample rate.
- Whether you use VB-CABLE, FxSound, Bluetooth, or USB.
- Exact steps to reproduce the issue.
- Full error messages without personal data.

See `LICENSE` for terms of use.
