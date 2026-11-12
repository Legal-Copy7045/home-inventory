import { useEffect, useState } from "react";
import { HashRouter, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Activity, ArrowLeft, Box, Handshake, Home, ListChecks, Map as MapIcon, MapPin, Menu, Package, Plus, Printer, ScanLine, Search, Settings, Users, Wrench, ClipboardList, Boxes, Nfc, ShoppingCart, Camera, Images } from "lucide-react";
import { api, appBaseUrl, labelRef, type Me, type QrTarget } from "./api";
import { MeContext, Sheet, Spinner, ToastHost, useToast } from "./components/ui";
import AddItem from "./pages/AddItem";
import { DashboardPage, ItemsPage, LocationDetailPage, LocationsPage, SearchBox, SearchPage } from "./pages/Browse";
import { ContainerDetail, ContainersPage } from "./pages/Containers";
import ItemDetail from "./pages/ItemDetail";
import { MapView, MapsPage } from "./pages/Maps";
import { ActivityPage, LabelsPage, LoansPage, SettingsPage } from "./pages/Other";
import { CustomerDetailPage, CustomersPage, RepairJobPage, RepairsPage, WorkOrderPage } from "./pages/Customers";
import { KitPage, KitsPage } from "./pages/Kits";
import { ShoppingPage } from "./pages/Shopping";
import { TagScanPage, TagsPage } from "./pages/Tags";
import { MaintenancePage } from "./pages/Maintenance";
import QuickCapture from "./pages/QuickCapture";
import ScanPage from "./pages/ScanPage";
import SortPhotos from "./pages/SortPhotos";
import { RecentlyDeleted } from "./components/BulkActions";
import ToFinish from "./pages/ToFinish";

const NAV = [
  { to: "/", label: "Dashboard", icon: Home, end: true },
  { to: "/search", label: "Search", icon: Search },
  { to: "/add", label: "Add Item", icon: Plus, edit: true },
  { to: "/scan", label: "Scan", icon: ScanLine },
  { to: "/items", label: "Items", icon: Package },
  { to: "/capture", label: "Quick photos", icon: Camera, edit: true },
  { to: "/sort", label: "To sort", icon: Images },
  { to: "/to-finish", label: "To finish", icon: ListChecks },
  { to: "/maintenance", label: "Maintenance", icon: Wrench },
  { to: "/shopping", label: "Restock", icon: ShoppingCart },
  { to: "/repairs", label: "Repairs", icon: ClipboardList, edit: true },
  { to: "/customers", label: "Customers", icon: Users, edit: true },
  { to: "/containers", label: "Containers", icon: Box },
  { to: "/kits", label: "Kits", icon: Boxes },
  { to: "/locations", label: "Locations", icon: MapPin },
  { to: "/maps", label: "Visual Maps", icon: MapIcon },
  { to: "/labels", label: "Print Labels", icon: Printer },
  { to: "/tags", label: "NFC Tags", icon: Nfc, edit: true },
  { to: "/activity", label: "Activity History", icon: Activity },
  { to: "/loans", label: "Loans & Temporary", icon: Handshake },
  { to: "/settings", label: "Settings", icon: Settings },
];

const TITLES: [RegExp, string][] = [
  [/^\/$/, "Home Inventory"], [/^\/search/, "Search"], [/^\/add/, "Add item"], [/^\/scan/, "Scan"], [/^\/items\/\d+/, "Item"], [/^\/items/, "Items"],
  [/^\/containers\/\d+/, "Container"], [/^\/containers/, "Containers"], [/^\/locations/, "Locations"], [/^\/maps/, "Visual maps"], [/^\/labels/, "Labels"],
  [/^\/capture/, "Quick photos"], [/^\/to-finish/, "To finish"], [/^\/sort/, "To sort"], [/^\/deleted/, "Recently deleted"], [/^\/maintenance/, "Maintenance"], [/^\/kits\/\d+/, "Kit"], [/^\/tags/, "NFC tags"], [/^\/shopping/, "Restock & shopping"], [/^\/tag\//, "Tag"], [/^\/kits/, "Kits"], [/^\/customers\/\d+/, "Customer"], [/^\/customers/, "Customers"], [/^\/repairs\/\d+\/print/, "Work order"], [/^\/repairs\/\d+/, "Repair job"], [/^\/repairs/, "Repairs"], [/^\/activity/, "Activity"], [/^\/loans/, "Loans & temporary"], [/^\/settings/, "Settings"],
];

/** QR labels can encode `<base>?c=CODE`; open the container when launched that way. */
/**
 * Open the container or location a QR label points at.
 *
 * Labels encode https://<home assistant>/<app panel>/c/<code> (or /l/ for
 * locations).  Home Assistant opens the App's panel and passes the
 * "/c/<code>" tail to the App via its iframe properties message; the page
 * is same-origin, so the top-level address is also checked directly.
 */
function DeepLink() {
  const nav = useNavigate();
  const toast = useToast();
  useEffect(() => {
    let handled = "";
    const open = async (source: string, fromTop: boolean) => {
      const ref = labelRef(source);
      if (!ref || handled === `${ref.kind}${ref.code}`) return;
      handled = `${ref.kind}${ref.code}`;
      if (ref.kind === "N") {
        // NFC tag: the tag page resolves it (login still required) and offers actions.
        nav(`/tag/${ref.code}`, { replace: true });
        const base = appBaseUrl();
        if (fromTop && base) window.parent.postMessage({ type: "home-assistant/navigate", path: new URL(base).pathname, options: { replace: true } }, "*");
        return;
      }
      try {
        const r = await api.post<QrTarget>("qr/resolve", { text: `HINV:${ref.kind}:${ref.code}` });
        nav(r.type === "location" ? `/locations/${r.id}` : `/containers/${r.id}`, { replace: true });
        // Tidy Home Assistant's address bar so a reload doesn't jump back here.
        const base = appBaseUrl();
        if (fromTop && base) {
          window.parent.postMessage({ type: "home-assistant/navigate", path: new URL(base).pathname, options: { replace: true } }, "*");
        }
      } catch (e) {
        toast((e as Error).message, true);
      }
    };
    open(window.location.pathname + window.location.search, false);
    try {
      open(window.top!.location.pathname + window.top!.location.search, true);
    } catch {
      /* not same-origin */
    }
    const onMessage = (ev: MessageEvent) => {
      if (ev.source !== window.parent || ev.data?.type !== "home-assistant/properties") return;
      const route = ev.data.route as { path?: string } | undefined;
      if (route?.path) open(route.path, true);
    };
    window.addEventListener("message", onMessage);
    if (window.parent !== window) window.parent.postMessage({ type: "home-assistant/subscribe-properties" }, "*");
    return () => window.removeEventListener("message", onMessage);
  }, [nav, toast]);
  return null;
}

/** First run with no administrator: claim the role with the code from the App log. */
function AdminSetup() {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const claim = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("setup/claim-admin", { code });
      window.location.reload();
    } catch (err) {
      toast((err as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page" style={{ paddingBottom: 0 }}>
      <div className="notice row between wrap">
        <span>This app has no administrator yet.</span>
        <button className="btn small" onClick={() => setOpen(true)}>Set up administrator</button>
      </div>
      {open && (
        <Sheet title="Become the administrator" onClose={() => setOpen(false)}>
          <form className="stack" onSubmit={claim}>
            <p style={{ marginTop: 0 }}>
              In Home Assistant, open <b>Settings → Apps → Home Inventory → Log</b> and copy the setup code shown there.
              Only Home Assistant administrators can see that log. Alternatively, add your username under
              <b> admin_users</b> on the App's Configuration tab.
            </p>
            <label className="field">
              Setup code
              <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="XXXX-XXXX-XXXX" autoCapitalize="characters" autoComplete="off" required />
            </label>
            <button className="btn primary block" disabled={busy || code.length < 4}>Become administrator</button>
          </form>
        </Sheet>
      )}
    </div>
  );
}

function Shell({ me }: { me: Me }) {
  const loc = useLocation();
  const nav = useNavigate();
  const [more, setMore] = useState(false);
  useEffect(() => { setMore(false); window.scrollTo(0, 0); }, [loc.pathname]);
  const title = TITLES.find(([re]) => re.test(loc.pathname))?.[1] || "Home Inventory";
  const isRoot = ["/", "/search", "/scan", "/items", "/capture", "/sort", "/to-finish", "/maintenance", "/kits", "/tags", "/shopping", "/repairs", "/customers", "/containers", "/locations", "/maps", "/labels", "/activity", "/loans", "/settings"].includes(loc.pathname);
  const items = NAV.filter((n) => !n.edit || me.can_edit);
  return (
    <div className="app">
      <nav className="sidebar" aria-label="Main">
        <div className="brand"><img src="./favicon.svg" alt="" width={30} height={30} /> Home Inventory</div>
        {items.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}><n.icon size={20} /> {n.label}</NavLink>
        ))}
      </nav>
      <div className="main">
        <header className="topbar">
          {!isRoot && <button className="iconbtn" onClick={() => nav(-1)} aria-label="Back"><ArrowLeft size={22} /></button>}
          <div className="title">{title}</div>
          {loc.pathname !== "/" && loc.pathname !== "/search" && <div style={{ maxWidth: 320, flex: 1 }} className="hide-sm"><SearchBox /></div>}
        </header>
        <DeepLink />
        {me.admin_setup && <AdminSetup />}
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/add" element={<AddItem />} />
          <Route path="/scan" element={<ScanPage />} />
          <Route path="/capture" element={<QuickCapture />} />
          <Route path="/sort" element={<SortPhotos />} />
          <Route path="/deleted" element={<RecentlyDeleted />} />
          <Route path="/to-finish" element={<ToFinish />} />
          <Route path="/maintenance" element={<MaintenancePage />} />
          <Route path="/kits" element={<KitsPage />} />
          <Route path="/tags" element={<TagsPage />} />
          <Route path="/shopping" element={<ShoppingPage />} />
          <Route path="/tag/:ref" element={<TagScanPage />} />
          <Route path="/kits/:id" element={<KitPage />} />
          <Route path="/customers" element={<CustomersPage />} />
          <Route path="/customers/:id" element={<CustomerDetailPage />} />
          <Route path="/repairs" element={<RepairsPage />} />
          <Route path="/repairs/:id" element={<RepairJobPage />} />
          <Route path="/repairs/:id/print" element={<WorkOrderPage />} />
          <Route path="/items" element={<ItemsPage />} />
          <Route path="/items/:id" element={<ItemDetail />} />
          <Route path="/containers" element={<ContainersPage />} />
          <Route path="/containers/:id" element={<ContainerDetail />} />
          <Route path="/locations" element={<LocationsPage />} />
          <Route path="/locations/:id" element={<LocationDetailPage />} />
          <Route path="/maps" element={<MapsPage />} />
          <Route path="/maps/:id" element={<MapView />} />
          <Route path="/labels" element={<LabelsPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="/loans" element={<LoansPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<div className="page"><div className="empty">Page not found.</div></div>} />
        </Routes>
      </div>
      <nav className="bottomnav" aria-label="Quick">
        <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}><Home size={22} />Home</NavLink>
        <NavLink to="/search" className={({ isActive }) => (isActive ? "active" : "")}><Search size={22} />Search</NavLink>
        {me.can_edit ? <NavLink to="/add" aria-label="Add item"><span className="fab"><Plus size={28} /></span></NavLink> : <NavLink to="/items"><Package size={22} />Items</NavLink>}
        <NavLink to="/scan" className={({ isActive }) => (isActive ? "active" : "")}><ScanLine size={22} />Scan</NavLink>
        <button onClick={() => setMore(true)}><Menu size={22} />More</button>
      </nav>
      {more && (
        <Sheet title="Menu" onClose={() => setMore(false)}>
          <div className="quick">
            {items.filter((n) => !["/", "/search", "/add", "/scan"].includes(n.to)).map((n) => (
              <NavLink key={n.to} to={n.to}><span className="ico"><n.icon size={20} /></span>{n.label}</NavLink>
            ))}
          </div>
        </Sheet>
      )}
    </div>
  );
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.get<Me>("me").then(setMe).catch((e) => setError(e.message));
  }, []);
  if (error) return <div className="page"><div className="error">Could not start Home Inventory: {error}</div><p>Open the app from the Home Assistant sidebar.</p></div>;
  if (!me) return <Spinner />;
  return (
    <MeContext.Provider value={me}>
      <ToastHost>
        <HashRouter>
          <Shell me={me} />
        </HashRouter>
      </ToastHost>
    </MeContext.Provider>
  );
}
