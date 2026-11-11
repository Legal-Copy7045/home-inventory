import { useState } from "react";
import { Download, FileSpreadsheet, Upload } from "lucide-react";
import { api, url } from "../api";
import { Sheet, useToast } from "./ui";

interface ImportResult {
  rows: number;
  errors: { row: number; message: string }[];
  to_create: string[];
  create_count: number;
  existing_count: number;
  update_count: number;
  applied: boolean;
  created?: number;
  updated?: number;
}

/** Bulk-create locations from an Excel (or CSV) sheet: download template →
 * fill in → upload → preview → confirm. */
export function LocationImportSheet({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(f: File, confirm: boolean) {
    const fd = new FormData();
    fd.append("file", f, f.name);
    fd.append("confirm", confirm ? "true" : "false");
    return api.upload<ImportResult>("locations/import", fd);
  }

  async function choose(f: File | undefined) {
    if (!f) return;
    setFile(f);
    setPreview(null);
    setError(null);
    setBusy(true);
    try {
      setPreview(await send(f, false));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!file) return;
    setBusy(true);
    try {
      const r = await send(file, true);
      toast(`${r.created} location${r.created === 1 ? "" : "s"} created${r.updated ? `, ${r.updated} updated` : ""}`);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet title="Import locations from Excel" onClose={onClose}>
      <div className="stack">
        <p style={{ margin: 0 }}>
          One row per place, written as its path: <b>Level 1</b> Property, <b>Level 2</b> Shed, <b>Level 3</b> Rear Right Shelving, <b>Level 4</b> Middle Shelf.
          Parents are created automatically, and places you already have are reused.
        </p>
        <div className="row wrap">
          <a className="btn" href={url("api/v1/locations/template.xlsx")} download>
            <Download size={18} /> Blank template
          </a>
          <a className="btn" href={url("api/v1/locations/template.xlsx?existing=true")} download>
            <FileSpreadsheet size={18} /> Template with my locations
          </a>
        </div>
        <label className="btn primary big block">
          {busy ? <div className="spinner" /> : <Upload size={20} />} {file ? `Choose another file (${file.name})` : "Upload filled-in sheet"}
          <input type="file" accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; choose(f); }} />
        </label>
        {error && <div className="error">{error}</div>}
        {preview && (
          <div className="card" style={{ marginBottom: 0 }}>
            <h3 style={{ marginBottom: 6 }}>Preview</h3>
            <p style={{ margin: "0 0 8px" }}>
              <b>{preview.create_count}</b> new location{preview.create_count === 1 ? "" : "s"} · {preview.existing_count} already exist
              {preview.update_count ? ` · ${preview.update_count} get a kind or description` : ""}
            </p>
            {preview.errors.length > 0 && (
              <div className="notice">
                {preview.errors.length} row{preview.errors.length === 1 ? "" : "s"} will be skipped:
                <ul style={{ margin: "4px 0 0", paddingLeft: 20 }}>
                  {preview.errors.slice(0, 10).map((e) => <li key={e.row}>Row {e.row}: {e.message}</li>)}
                </ul>
              </div>
            )}
            {preview.to_create.length > 0 && (
              <ul style={{ margin: 0, paddingLeft: 20, maxHeight: 260, overflowY: "auto", overflowWrap: "anywhere" }}>
                {preview.to_create.slice(0, 200).map((p) => <li key={p}>{p}</li>)}
                {preview.to_create.length > 200 && <li>… and {preview.to_create.length - 200} more</li>}
              </ul>
            )}
          </div>
        )}
        {preview && (
          <div className="sheet-actions">
            <button className="btn primary block big" disabled={busy || (!preview.create_count && !preview.update_count)} onClick={apply}>
              {preview.create_count || preview.update_count ? `Create ${preview.create_count} location${preview.create_count === 1 ? "" : "s"}` : "Nothing new to import"}
            </button>
          </div>
        )}
      </div>
    </Sheet>
  );
}
