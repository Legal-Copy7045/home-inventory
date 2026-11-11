import { useState } from "react";
import { Link2 } from "lucide-react";
import { api, type LinkImport as LinkImportResult } from "../api";

/**
 * Paste a shop or manufacturer link; the App reads the page and returns
 * product details (and the main photo) for the user to review.
 */
export function LinkImport({
  initialUrl = "",
  downloadImage = true,
  buttonLabel = "Get details",
  onImported,
}: {
  initialUrl?: string;
  downloadImage?: boolean;
  buttonLabel?: string;
  onImported: (r: LinkImportResult) => void;
}) {
  const [link, setLink] = useState(initialUrl);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function run() {
    if (!link.trim()) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const r = await api.post<LinkImportResult>("link/import", { url: link.trim(), download_image: downloadImage });
      const from = r.details.source.includes("ai") ? "read with AI from the page" : "read from the page";
      setNote(`Details ${from}${r.details.retailer ? ` (${r.details.retailer})` : ""}. Check them before saving.${r.image_error ? ` Photo not added: ${r.image_error}` : ""}`);
      onImported(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <form className="row" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <input type="url" inputMode="url" placeholder="Paste a product link (https://…)" value={link} onChange={(e) => setLink(e.target.value)} aria-label="Product link" />
        <button className="btn" type="submit" disabled={busy || !link.trim()}>
          {busy ? <div className="spinner" /> : <Link2 size={18} />} {buttonLabel}
        </button>
      </form>
      {note && <p className="muted" style={{ margin: 0 }}>{note}</p>}
      {error && <div className="notice" style={{ margin: 0 }}>{error}</div>}
    </div>
  );
}
