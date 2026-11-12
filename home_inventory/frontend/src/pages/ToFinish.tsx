import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { api, type Item } from "../api";
import { SelectableItems } from "../components/BulkActions";
import { Empty, Spinner, Thumb, useApi } from "../components/ui";

/** Items added with Quick photos that still need their details. */
export default function ToFinish() {
  const list = useApi(() => api.get<{ total: number; items: Item[] }>("items", { needs_details: true, sort: "oldest", limit: 500 }), []);
  return (
    <div className="page">
      <h1>To finish</h1>
      <p className="muted">Items added with Quick photos. Open one, check the details (AI suggestions are pre-filled where available) and save — then carry on to the next. Use <b>Select</b> to move or delete several at once.</p>
      {list.loading && !list.data ? <Spinner /> : (
        <SelectableItems
          items={list.data?.items || []}
          total={list.data?.total}
          onChanged={list.reload}
          empty={<Empty>Nothing to finish. Use <b>Quick photos</b> to photograph several items in a row and add their details later.</Empty>}
          renderRow={(i) => (
            <Link to={`/items/${i.id}?finish=1`}>
              <Thumb src={i.photo?.thumb_url} />
              <div className="grow">
                <div className="t">
                  {i.capture?.suggestion ? <><Sparkles size={14} /> {i.capture.suggestion.name}?</> : <span className="muted">Not identified yet</span>}
                </div>
                <div className="s">{i.placement.path}</div>
                {i.capture?.ai_status === "queued" && <span className="badge">AI is looking…</span>}
              </div>
            </Link>
          )}
        />
      )}
    </div>
  );
}
