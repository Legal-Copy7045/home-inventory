import { useEffect, useRef, useState } from "react";
import { Camera, Keyboard, SwitchCamera } from "lucide-react";
import { BrowserMultiFormatReader, type IScannerControls } from "@zxing/browser";
import { BarcodeFormat, DecodeHintType } from "@zxing/library";

export interface Detection {
  text: string;
  format: string; // qr_code | ean_13 | ean_8 | upc_a | upc_e | code_128 | itf | ...
}

const ZX_FORMATS = [
  BarcodeFormat.QR_CODE,
  BarcodeFormat.EAN_13,
  BarcodeFormat.EAN_8,
  BarcodeFormat.UPC_A,
  BarcodeFormat.UPC_E,
  BarcodeFormat.CODE_128,
  BarcodeFormat.ITF,
];
const NATIVE_FORMATS = ["qr_code", "ean_13", "ean_8", "upc_a", "upc_e", "code_128", "itf"];

function zxReader() {
  const hints = new Map();
  hints.set(DecodeHintType.POSSIBLE_FORMATS, ZX_FORMATS);
  hints.set(DecodeHintType.TRY_HARDER, true);
  return new BrowserMultiFormatReader(hints, { delayBetweenScanAttempts: 120 });
}

function zxFormatName(f: BarcodeFormat): string {
  return (BarcodeFormat[f] || "unknown").toLowerCase();
}

type NativeDetector = { detect: (src: CanvasImageSource) => Promise<{ rawValue: string; format: string }[]> };

async function nativeDetector(): Promise<NativeDetector | null> {
  const BD = (window as unknown as { BarcodeDetector?: { new (o: unknown): NativeDetector; getSupportedFormats(): Promise<string[]> } }).BarcodeDetector;
  if (!BD) return null;
  try {
    const supported = await BD.getSupportedFormats();
    const formats = NATIVE_FORMATS.filter((f) => supported.includes(f));
    if (!formats.includes("qr_code") || !formats.includes("ean_13")) return null;
    return new BD({ formats });
  } catch {
    return null;
  }
}

/** Decode a still photo (fallback when live camera is unavailable). */
export async function decodeImageFile(file: File): Promise<Detection | null> {
  const bmpUrl = URL.createObjectURL(file);
  try {
    const native = await nativeDetector();
    if (native) {
      const img = await createImageBitmap(file);
      const r = await native.detect(img);
      if (r.length) return { text: r[0].rawValue, format: r[0].format };
    }
    const res = await zxReader().decodeFromImageUrl(bmpUrl);
    return { text: res.getText(), format: zxFormatName(res.getBarcodeFormat()) };
  } catch {
    return null;
  } finally {
    URL.revokeObjectURL(bmpUrl);
  }
}

export function cameraAvailable(): boolean {
  return !!(window.isSecureContext && navigator.mediaDevices?.getUserMedia);
}

/**
 * Continuous scanner for QR labels and product barcodes.  Repeated reads of
 * the same code are suppressed for a few seconds so a label held in view
 * doesn't trigger again and again.
 */
export function Scanner({ onDetect, paused = false, hint = "Point at a QR label or barcode" }: { onDetect: (d: Detection) => void; paused?: boolean; hint?: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [facing, setFacing] = useState<"environment" | "user">("environment");
  const [manual, setManual] = useState("");
  const last = useRef<{ text: string; at: number }>({ text: "", at: 0 });
  const cb = useRef(onDetect);
  cb.current = onDetect;
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const live = cameraAvailable();

  function emit(d: Detection) {
    if (pausedRef.current) return;
    const now = Date.now();
    if (d.text === last.current.text && now - last.current.at < 3000) return;
    last.current = { text: d.text, at: now };
    if (navigator.vibrate) navigator.vibrate(40);
    cb.current(d);
  }

  useEffect(() => {
    if (!live) return;
    let stopped = false;
    let stream: MediaStream | null = null;
    let controls: IScannerControls | null = null;
    let raf = 0;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        });
        if (stopped) return;
        const video = videoRef.current!;
        video.srcObject = stream;
        video.setAttribute("playsinline", "true");
        await video.play();
        const native = await nativeDetector();
        if (native) {
          const tick = async () => {
            if (stopped) return;
            if (!pausedRef.current && video.readyState >= 2) {
              try {
                const r = await native.detect(video);
                if (r.length) emit({ text: r[0].rawValue, format: r[0].format });
              } catch {
                /* ignore frame errors */
              }
            }
            raf = window.setTimeout(tick, 150) as unknown as number;
          };
          tick();
        } else {
          controls = await zxReader().decodeFromVideoElement(video, (res) => {
            if (res) emit({ text: res.getText(), format: zxFormatName(res.getBarcodeFormat()) });
          });
        }
        setError(null);
      } catch (e) {
        setError((e as Error).name === "NotAllowedError" ? "Camera permission was denied. Allow camera access for Home Assistant, or take a photo instead." : `Camera unavailable: ${(e as Error).message}`);
      }
    })();
    return () => {
      stopped = true;
      window.clearTimeout(raf);
      controls?.stop();
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [facing, live]);

  async function onPhoto(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    const d = await decodeImageFile(f);
    if (d) emit({ ...d, text: d.text });
    else setError("No barcode or QR code found in that photo. Try again closer, with good light.");
  }

  return (
    <div className="stack">
      {live ? (
        <div className="camera">
          <video ref={videoRef} muted playsInline />
          <div className="reticle" />
          <div className="hint">{paused ? "Paused" : hint}</div>
        </div>
      ) : (
        <div className="notice">
          Live camera scanning needs a secure (https) connection to Home Assistant. You can still take a photo of the code below.
        </div>
      )}
      {error && <div className="error">{error}</div>}
      <div className="row wrap">
        <label className="btn" style={{ flex: 1 }}>
          <Camera size={18} /> Photo of code
          <input type="file" accept="image/*" hidden onChange={onPhoto} />
        </label>
        {live && (
          <button className="btn" onClick={() => setFacing(facing === "environment" ? "user" : "environment")} aria-label="Switch camera">
            <SwitchCamera size={18} />
          </button>
        )}
      </div>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          if (manual.trim()) {
            last.current = { text: "", at: 0 };
            emit({ text: manual.trim(), format: /^\d+$/.test(manual.trim()) ? "manual" : "qr_code" });
            setManual("");
          }
        }}
      >
        <input inputMode="numeric" placeholder="Type a barcode or label code" value={manual} onChange={(e) => setManual(e.target.value)} />
        <button className="btn" type="submit" aria-label="Look up">
          <Keyboard size={18} />
        </button>
      </form>
    </div>
  );
}
