// PoC EXPERIMENTAL (rama exp-go-engine): motor de audio en Go puro.
//
// Captura el loopback de un endpoint render (p. ej. "CABLE Input") con WASAPI
// event-driven, lo pasa por un ring buffer y lo renderiza a la salida fisica
// (por defecto en SILENCIO, para medir sin duplicar audio).
//
// Mide lo que importa para decidir el port: cortes (underruns del ring),
// huecos (frames de silencio insertados), desbordes del ring, jitter de los
// callbacks y coste de cada callback. NO es la app ni la sustituye.
package main

import (
	"flag"
	"fmt"
	"os"
	"os/signal"
	"runtime"
	"sort"
	"strings"
	"syscall"
	"time"
	"unsafe"

	"github.com/go-ole/go-ole"
	"github.com/moutend/go-wca/pkg/wca"
)

const (
	refTimesPerSec = 10000000.0
	flagSilent     = 0x00000002 // AUDCLNT_BUFFERFLAGS_SILENT
)

var (
	kernel32                   = syscall.NewLazyDLL("kernel32.dll")
	procWaitForMultipleObjects = kernel32.NewProc("WaitForMultipleObjects")
)

func waitForMultiple(handles []uintptr, timeoutMs uint32) (uint32, error) {
	if len(handles) == 0 {
		return 0, fmt.Errorf("sin handles")
	}
	r, _, err := procWaitForMultipleObjects.Call(
		uintptr(len(handles)), uintptr(unsafe.Pointer(&handles[0])), 0, uintptr(timeoutMs),
	)
	if r == 0xFFFFFFFF {
		return 0, err
	}
	return uint32(r), nil
}

// ---------- ring buffer (float32 intercalado) ----------

type ringBuf struct {
	data   []float32
	frames int
	rd     int
	wr     int
	count  int
}

func newRing(frames int) *ringBuf {
	return &ringBuf{data: make([]float32, frames*2), frames: frames}
}

func (r *ringBuf) free() int  { return r.frames - r.count }
func (r *ringBuf) avail() int { return r.count }

func (r *ringBuf) write(src []float32, n int) int {
	if n > r.free() {
		n = r.free()
	}
	first := n
	if r.wr+first > r.frames {
		first = r.frames - r.wr
	}
	if first > 0 {
		copy(r.data[r.wr*2:r.wr*2+first*2], src[:first*2])
	}
	if n-first > 0 {
		copy(r.data[:(n-first)*2], src[first*2:n*2])
	}
	r.wr = (r.wr + n) % r.frames
	r.count += n
	return n
}

func (r *ringBuf) read(dst []float32, n int) int {
	if n > r.count {
		n = r.count
	}
	first := n
	if r.rd+first > r.frames {
		first = r.frames - r.rd
	}
	if first > 0 {
		copy(dst[:first*2], r.data[r.rd*2:r.rd*2+first*2])
	}
	if n-first > 0 {
		copy(dst[first*2:n*2], r.data[:(n-first)*2])
	}
	r.rd = (r.rd + n) % r.frames
	r.count -= n
	return n
}

// ---------- utilidades WASAPI ----------

func friendlyName(mmd *wca.IMMDevice) (string, error) {
	var ps *wca.IPropertyStore
	if err := mmd.OpenPropertyStore(wca.STGM_READ, &ps); err != nil {
		return "", err
	}
	defer ps.Release()
	var pv wca.PROPVARIANT
	if err := ps.GetValue(&wca.PKEY_Device_FriendlyName, &pv); err != nil {
		return "", err
	}
	return pv.String(), nil
}

func findRenderDevice(mmde *wca.IMMDeviceEnumerator, substr string) (*wca.IMMDevice, string, error) {
	var coll *wca.IMMDeviceCollection
	if err := mmde.EnumAudioEndpoints(wca.ERender, wca.DEVICE_STATE_ACTIVE, &coll); err != nil {
		return nil, "", err
	}
	defer coll.Release()
	var n uint32
	if err := coll.GetCount(&n); err != nil {
		return nil, "", err
	}
	names := make([]string, 0, n)
	for i := uint32(0); i < n; i++ {
		var mmd *wca.IMMDevice
		if err := coll.Item(i, &mmd); err != nil {
			continue
		}
		name, _ := friendlyName(mmd)
		names = append(names, name)
		if substr == "" || strings.Contains(strings.ToLower(name), strings.ToLower(substr)) {
			return mmd, name, nil
		}
		mmd.Release()
	}
	return nil, "", fmt.Errorf("no encontrado %q; dispositivos: %s", substr, strings.Join(names, " | "))
}

func ppm(ms float64) float64 { return ms * 1000.0 }

func percentile(sorted []float64, p float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	idx := int(float64(len(sorted)-1) * p)
	if idx < 0 {
		idx = 0
	}
	if idx >= len(sorted) {
		idx = len(sorted) - 1
	}
	return sorted[idx]
}

// ---------- main ----------

func main() {
	runtime.LockOSThread()
	var err error
	if err = run(); err != nil {
		fmt.Fprintln(os.Stderr, "ERROR:", err)
		os.Exit(1)
	}
}

func run() error {
	sec := flag.Int("sec", 60, "duracion de la medida en segundos")
	capName := flag.String("cap", "CABLE Input", "subcadena del endpoint render a capturar (loopback)")
	renName := flag.String("render", "", "subcadena del endpoint de salida (vacio = predeterminado)")
	targetMs := flag.Int("target-ms", 250, "objetivo de llenado del ring (ms)")
	ringMs := flag.Int("ring-ms", 1000, "capacidad del ring (ms)")
	audible := flag.Bool("audible", false, "reproducir el audio (por defecto silencio)")
	flag.Parse()

	if err := ole.CoInitializeEx(0, ole.COINIT_APARTMENTTHREADED); err != nil {
		return err
	}
	defer ole.CoUninitialize()

	var mmde *wca.IMMDeviceEnumerator
	if err := wca.CoCreateInstance(wca.CLSID_MMDeviceEnumerator, 0, wca.CLSCTX_ALL, wca.IID_IMMDeviceEnumerator, &mmde); err != nil {
		return err
	}
	defer mmde.Release()

	mmdCap, capFriendly, err := findRenderDevice(mmde, *capName)
	if err != nil {
		return err
	}
	defer mmdCap.Release()

	var mmdRen *wca.IMMDevice
	var renFriendly string
	if *renName == "" {
		if err := mmde.GetDefaultAudioEndpoint(wca.ERender, wca.EConsole, &mmdRen); err != nil {
			return err
		}
		renFriendly, _ = friendlyName(mmdRen)
	} else {
		mmdRen, renFriendly, err = findRenderDevice(mmde, *renName)
		if err != nil {
			return err
		}
	}
	defer mmdRen.Release()

	var cac *wca.IAudioClient
	if err := mmdCap.Activate(wca.IID_IAudioClient, wca.CLSCTX_ALL, nil, &cac); err != nil {
		return err
	}
	defer cac.Release()

	var rac *wca.IAudioClient
	if err := mmdRen.Activate(wca.IID_IAudioClient, wca.CLSCTX_ALL, nil, &rac); err != nil {
		return err
	}
	defer rac.Release()

	var wfx *wca.WAVEFORMATEX
	if err := rac.GetMixFormat(&wfx); err != nil {
		return err
	}
	defer ole.CoTaskMemFree(uintptr(unsafe.Pointer(wfx)))

	fmt.Printf("Captura (loopback): %s\n", capFriendly)
	fmt.Printf("Salida:             %s\n", renFriendly)
	fmt.Printf("Formato mix:        %d Hz, %d ch, %d bits, tag=0x%04X\n",
		wfx.NSamplesPerSec, wfx.NChannels, wfx.WBitsPerSample, wfx.WFormatTag)
	if wfx.WBitsPerSample != 32 {
		return fmt.Errorf("se esperaba 32 bits float (mix format); encontrado %d bits", wfx.WBitsPerSample)
	}
	if !*audible {
		fmt.Println("Modo: SILENCIOSO (mide sin reproducir; usa -audible para oir)")
	}

	var defPeriod, minPeriod wca.REFERENCE_TIME
	if err := rac.GetDevicePeriod(&defPeriod, &minPeriod); err != nil {
		return err
	}
	periodMs := float64(defPeriod) / refTimesPerSec * 1000.0
	fmt.Printf("Periodo del dispositivo: %.2f ms (min %.2f ms)\n", periodMs,
		float64(minPeriod)/refTimesPerSec*1000.0)

	const (
		shareModeShared = wca.AUDCLNT_SHAREMODE_SHARED
		flagsCapture    = wca.AUDCLNT_STREAMFLAGS_EVENTCALLBACK | wca.AUDCLNT_STREAMFLAGS_LOOPBACK
		flagsRender     = wca.AUDCLNT_STREAMFLAGS_EVENTCALLBACK
	)
	if err := cac.Initialize(shareModeShared, flagsCapture, defPeriod, 0, wfx, nil); err != nil {
		return fmt.Errorf("Initialize captura: %w", err)
	}
	if err := rac.Initialize(shareModeShared, flagsRender, defPeriod, 0, wfx, nil); err != nil {
		return fmt.Errorf("Initialize salida: %w", err)
	}

	capEv := wca.CreateEventExA(0, 0, 0, wca.EVENT_MODIFY_STATE|wca.SYNCHRONIZE)
	defer wca.CloseHandle(capEv)
	renEv := wca.CreateEventExA(0, 0, 0, wca.EVENT_MODIFY_STATE|wca.SYNCHRONIZE)
	defer wca.CloseHandle(renEv)
	if err := cac.SetEventHandle(capEv); err != nil {
		return err
	}
	if err := rac.SetEventHandle(renEv); err != nil {
		return err
	}

	var capBufFrames uint32
	if err := cac.GetBufferSize(&capBufFrames); err != nil {
		return err
	}
	var renBufFrames uint32
	if err := rac.GetBufferSize(&renBufFrames); err != nil {
		return err
	}
	fmt.Printf("Buffers WASAPI: captura %d frames, salida %d frames\n", capBufFrames, renBufFrames)

	var acc *wca.IAudioCaptureClient
	if err := cac.GetService(wca.IID_IAudioCaptureClient, &acc); err != nil {
		return err
	}
	defer acc.Release()
	var arc *wca.IAudioRenderClient
	if err := rac.GetService(wca.IID_IAudioRenderClient, &arc); err != nil {
		return err
	}
	defer arc.Release()

	fs := int(wfx.NSamplesPerSec)
	ch := int(wfx.NChannels)
	if ch != 2 {
		return fmt.Errorf("se esperaban 2 canales; encontrado %d", ch)
	}
	ring := newRing(fs * *ringMs / 1000)
	targetFrames := fs * *targetMs / 1000

	if err := cac.Start(); err != nil {
		return err
	}
	defer cac.Stop()

	// Llenado inicial: esperar a que el ring llegue al objetivo (max 3 s).
	zero := make([]float32, 8192*ch)
	readChunk := func() (int, int, error) {
		total, dropped := 0, 0
		for {
			var pkt uint32
			if err := acc.GetNextPacketSize(&pkt); err != nil {
				return total, dropped, err
			}
			if pkt == 0 {
				return total, dropped, nil
			}
			var data *byte
			var frames, flags uint32
			var devPos, qpc uint64
			if err := acc.GetBuffer(&data, &frames, &flags, &devPos, &qpc); err != nil {
				return total, dropped, err
			}
			n := int(frames)
			if n > 0 {
				if flags&flagSilent != 0 || data == nil {
					if n*ch > len(zero) {
						zero = make([]float32, n*ch)
					}
					if w := ring.write(zero[:n*ch], n); w < n {
						dropped += n - w
					}
				} else {
					src := unsafe.Slice((*float32)(unsafe.Pointer(data)), n*ch)
					if w := ring.write(src, n); w < n {
						dropped += n - w
					}
				}
			}
			if err := acc.ReleaseBuffer(frames); err != nil {
				return total, dropped, err
			}
			total += n
		}
	}

	startupDeadline := time.Now().Add(3 * time.Second)
	for ring.avail() < targetFrames && time.Now().Before(startupDeadline) {
		if _, _, err := readChunk(); err != nil {
			return err
		}
		time.Sleep(5 * time.Millisecond)
	}
	fmt.Printf("Ring inicial: %d/%d frames (objetivo %d)\n", ring.avail(), ring.frames, targetFrames)

	if err := rac.Start(); err != nil {
		return err
	}
	defer rac.Stop()

	// Estado de medida (se reinicia tras el arranque).
	var (
		capEvents, renEvents            int64
		underruns, gapFrames, overflows int64
		peak, sumsq                     float64
		samples                         int64
		capIntervals, renIntervals      []float64
		capWork, renWork                []float64
		lastCap, lastRen                time.Time
		fillMin, fillMax                = ring.frames, 0
	)
	fmt.Println("Midiendo... (Ctrl-C para terminar antes)")
	start := time.Now()
	lastReport := start
	nextReport := start.Add(5 * time.Second)

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt)
	deadline := start.Add(time.Duration(*sec) * time.Second)

	renderDst := make([]float32, int(renBufFrames)*ch)

	for time.Now().Before(deadline) {
		select {
		case <-sig:
			fmt.Println("\nInterrumpido.")
			goto done
		default:
		}

		idx, err := waitForMultiple([]uintptr{capEv, renEv}, 2000)
		if err != nil {
			return err
		}
		now := time.Now()
		switch idx {
		case 0: // captura
			workStart := now
			if !lastCap.IsZero() {
				capIntervals = append(capIntervals, now.Sub(lastCap).Seconds()*1000.0)
			}
			lastCap = now
			capEvents++
			_, dropped, err := readChunk()
			if err != nil {
				return err
			}
			overflows += int64(dropped)
			capWork = append(capWork, time.Since(workStart).Seconds()*1e6)
		case 1: // render
			workStart := now
			if !lastRen.IsZero() {
				renIntervals = append(renIntervals, now.Sub(lastRen).Seconds()*1000.0)
			}
			lastRen = now
			renEvents++
			var pad uint32
			if err := rac.GetCurrentPadding(&pad); err != nil {
				continue
			}
			n := renBufFrames - pad
			if n == 0 {
				renWork = append(renWork, time.Since(workStart).Seconds()*1e6)
				continue
			}
			var data *byte
			if err := arc.GetBuffer(n, &data); err != nil {
				continue
			}
			dst := renderDst[:int(n)*ch]
			got := ring.read(dst, int(n))
			if got < int(n) {
				underruns++
				missing := int(n) - got
				gapFrames += int64(missing)
				for i := got * ch; i < int(n)*ch; i++ {
					dst[i] = 0
				}
			}
			// Estadistica de nivel de lo capturado (muestrea cada 64).
			for i := 0; i < got*ch; i += 64 {
				v := float64(dst[i])
				if v < 0 {
					v = -v
				}
				if v > peak {
					peak = v
				}
				sumsq += v * v
				samples++
			}
			if !*audible {
				for i := range dst {
					dst[i] = 0
				}
			}
			if err := arc.ReleaseBuffer(n, 0); err != nil {
				return err
			}
			renWork = append(renWork, time.Since(workStart).Seconds()*1e6)
		case 258: // timeout
			continue
		}
		if len(ring.data) > 0 {
			if av := ring.avail(); av < fillMin {
				fillMin = av
			}
		}
		if av := ring.avail(); av > fillMax {
			fillMax = av
		}
		if time.Now().After(nextReport) {
			sort.Float64s(renIntervals)
			rep := time.Since(lastReport).Seconds()
			fmt.Printf("t=%5.1fs cap=%5.1f/s rend=%5.1f/s fill=%4d/%4d (min %d max %d) under=%d gaps=%d over=%d jit_rend p50=%.2f p99=%.2f max=%.2f ms\n",
				time.Since(start).Seconds(),
				float64(capEvents)/rep,
				float64(renEvents)/rep,
				ring.avail()*1000/fs, ring.frames*1000/fs,
				fillMin*1000/fs, fillMax*1000/fs,
				underruns, gapFrames, overflows,
				percentile(renIntervals, 0.50)-periodMs,
				percentile(renIntervals, 0.99)-periodMs,
				func() float64 {
					if len(renIntervals) == 0 {
						return 0
					}
					return renIntervals[len(renIntervals)-1] - periodMs
				}(),
			)
			capEvents = 0
			renEvents = 0
			lastReport = time.Now()
			nextReport = lastReport.Add(5 * time.Second)
		}
	}

done:
	elapsed := time.Since(start).Seconds()
	sort.Float64s(capIntervals)
	sort.Float64s(renIntervals)
	sort.Float64s(capWork)
	sort.Float64s(renWork)

	rms := 0.0
	if samples > 0 {
		rms = sqrt(sumsq / float64(samples))
	}
	fmt.Println()
	fmt.Println("================ RESUMEN ================")
	fmt.Printf("Duracion medida:     %.1f s\n", elapsed)
	fmt.Printf("Callbacks:           captura %d, salida %d\n", int64(float64(len(capIntervals))), int64(float64(len(renIntervals))))
	fmt.Printf("Underruns (cortes):  %d\n", underruns)
	fmt.Printf("Huecos (frames):     %d (%.3f s)\n", gapFrames, float64(gapFrames)/float64(fs))
	fmt.Printf("Desbordes del ring:  %d\n", overflows)
	fmt.Printf("Fill (ms):           min %.1f  max %.1f  (objetivo %d)\n",
		float64(fillMin)*1000/float64(fs), float64(fillMax)*1000/float64(fs), *targetMs)
	if len(renIntervals) > 0 {
		fmt.Printf("Jitter salida (ms):  p50 %.3f  p99 %.3f  max %.3f  (periodo %.2f)\n",
			percentile(renIntervals, 0.50)-periodMs,
			percentile(renIntervals, 0.99)-periodMs,
			renIntervals[len(renIntervals)-1]-periodMs,
			periodMs)
	}
	if len(capIntervals) > 0 {
		fmt.Printf("Jitter captura (ms): p50 %.3f  p99 %.3f  max %.3f\n",
			percentile(capIntervals, 0.50)-periodMs,
			percentile(capIntervals, 0.99)-periodMs,
			capIntervals[len(capIntervals)-1]-periodMs)
	}
	if len(renWork) > 0 {
		fmt.Printf("Coste callback:      salida p99 %.1f us max %.1f us | captura p99 %.1f us max %.1f us\n",
			percentile(renWork, 0.99), renWork[len(renWork)-1],
			percentile(capWork, 0.99), func() float64 {
				if len(capWork) == 0 {
					return 0
				}
				return capWork[len(capWork)-1]
			}())
	}
	fmt.Printf("Nivel capturado:     pico %.4f  RMS %.4f\n", peak, rms)
	if peak < 1e-4 {
		fmt.Println("AVISO: no se detecto audio en el loopback (pon algo a sonar en el cable).")
	}
	fmt.Println("=========================================")
	return nil
}

func sqrt(x float64) float64 {
	if x <= 0 {
		return 0
	}
	z := x
	for i := 0; i < 20; i++ {
		z = 0.5 * (z + x/z)
	}
	return z
}
