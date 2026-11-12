import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ExternalLink, Package, Star, X } from "lucide-react";
import { url, type Attachment } from "../api";

/**
 * Item photos: the whole picture, centred (no cropping), thumbnails that
 * switch the main photo, and a full-screen viewer with swipe / arrows /
 * pinch-zoom - all inside the app (no jumping out to the raw file).
 */
export function PhotoViewer({ photos, mainId, onMakeMain }: { photos: Attachment[]; mainId?: number | null; onMakeMain?: (id: number) => void }) {
  const ordered = [...photos].sort((a, b) => (a.id === mainId ? -1 : b.id === mainId ? 1 : a.id - b.id));
  const [index, setIndex] = useState(0);
  const [full, setFull] = useState(false);
  useEffect(() => setIndex(0), [mainId, photos.length]);
  const current = ordered[Math.min(index, ordered.length - 1)];

  if (!current) {
    return <div className="photo-main empty" aria-label="No photo"><Package size={40} /></div>;
  }
  return (
    <>
      <button type="button" className="photo-main" onClick={() => setFull(true)} aria-label="View photo full screen">
        <img src={url(current.url)} alt="" draggable={false} />
        {ordered.length > 1 && <span className="photo-count">{index + 1} / {ordered.length}</span>}
      </button>
      {ordered.length > 1 && (
        <div className="photo-strip" role="list">
          {ordered.map((p, i) => (
            <div key={p.id} className={`photo-thumb ${i === index ? "active" : ""}`} role="listitem">
              <button type="button" onClick={() => setIndex(i)} aria-label={`Show photo ${i + 1}`} aria-current={i === index}>
                <img src={url(p.thumb_url || p.url)} alt="" draggable={false} />
              </button>
              {p.id === mainId ? (
                <span className="badge blue photo-tag">Main</span>
              ) : onMakeMain ? (
                <button type="button" className="photo-star" onClick={() => onMakeMain(p.id)} aria-label="Make main photo"><Star size={14} /></button>
              ) : null}
            </div>
          ))}
        </div>
      )}
      {full && <Lightbox photos={ordered} index={index} onIndex={setIndex} onClose={() => setFull(false)} />}
    </>
  );
}

function Lightbox({ photos, index, onIndex, onClose }: { photos: Attachment[]; index: number; onIndex: (i: number) => void; onClose: () => void }) {
  const start = useRef<{ x: number; y: number; touches: number } | null>(null);
  const n = photos.length;
  const go = (d: number) => onIndex((index + d + n) % n);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft" && n > 1) go(-1);
      if (e.key === "ArrowRight" && n > 1) go(1);
    };
    window.addEventListener("keydown", key);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", key); document.body.style.overflow = prev; };
  });
  const p = photos[index];
  return (
    <div
      className="lightbox"
      role="dialog"
      aria-modal="true"
      aria-label="Photo"
      onTouchStart={(e) => { start.current = { x: e.touches[0].clientX, y: e.touches[0].clientY, touches: e.touches.length }; }}
      onTouchEnd={(e) => {
        const s = start.current;
        start.current = null;
        if (!s || s.touches > 1 || n < 2) return; // pinch-zoom, not a swipe
        const dx = e.changedTouches[0].clientX - s.x;
        const dy = e.changedTouches[0].clientY - s.y;
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) go(dx < 0 ? 1 : -1);
        else if (dy > 90 && Math.abs(dy) > Math.abs(dx) * 1.5) onClose(); // swipe down to close
      }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <img src={url(p.url)} alt="" draggable={false} />
      <div className="lightbox-bar">
        <span>{n > 1 ? `${index + 1} / ${n}` : ""}</span>
        <span className="row" style={{ gap: 6 }}>
          <a className="lightbox-btn" href={url(p.url)} target="_blank" rel="noreferrer" aria-label="Open original"><ExternalLink size={20} /></a>
          <button type="button" className="lightbox-btn" onClick={onClose} aria-label="Close"><X size={24} /></button>
        </span>
      </div>
      {n > 1 && (
        <>
          <button type="button" className="lightbox-btn lightbox-prev" onClick={() => go(-1)} aria-label="Previous photo"><ChevronLeft size={28} /></button>
          <button type="button" className="lightbox-btn lightbox-next" onClick={() => go(1)} aria-label="Next photo"><ChevronRight size={28} /></button>
        </>
      )}
    </div>
  );
}
