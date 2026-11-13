import { Suspense, lazy, useEffect, useState, type ComponentType } from "react";
import { HashRouter, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Activity, ArrowLeft, Box, Handshake, Home, ListChecks, Map as MapIcon, MapPin, Menu, Package, Plus, Printer, ScanLine, Search, Settings, Users, Wrench, ClipboardList, Boxes, Nfc, ShoppingCart, Camera, Images, FileText } from "lucide-react";
import { api, appBaseUrl, labelRef, type Me, type QrTarget } from "./api";
import { MeContext, Sheet, Spinner, ToastHost, useToast } from "./components/ui";
import { DashboardPage, ItemsPage, LocationDetailPage, LocationsPage, SearchBox, SearchPage } from "./pages/Browse";
// Quick photos stays in the main bundle: it has to open offline.
import QuickCapture from "./pages/QuickCapture";
import { startQueue, subscribe as subscribeUploads, waitingCount } from "./uploadQueue";

// The other pages load when first opened, so the app starts faster.  Once
// it's idle they're fetched in the background (and kept by the service
// worker), so they still open offline and without a wait later.
type Loader = () => Promise<Record<string, unknown>>;
const loaders: Loader[] = [];
function page<K extends string>(load: () => Promise<Record<K, ComponentType>>, name: K) {
  loaders.push(load as Loader);
  return lazy(() => load().then((m) => ({ default: m[name] })));
}
function preloadPages() {
  const idle = (window as unknown as { requestIdleCallback?: (f: () => void) => void }).requestIdleCallback || ((f: () => void) => setTimeout(f, 2000));
  idle(() => { loaders.forEach((l) => l().catch(() => undefined)); });
}
loaders.push(() => import("./components/Scanner").then((m) => m.loadZxing()).then(() => ({})));
const AddItem = page(() => import("./pages/AddItem"), "default");
const ContainersPage = page(() => import("./pages/Containers"), "ContainersPage");
const ContainerDetail = page(() => import("./pages/Containers"), "ContainerDetail");
const ItemDetail = page(() => import("./pages/ItemDetail"), "default");
const MapsPage = page(() => import("./pages/Maps"), "MapsPage");
const MapView = page(() => import("./pages/Maps"), "MapView");
const ActivityPage = page(() => import("./pages/Other"), "ActivityPage");
const LabelsPage = page(() => import("./pages/Other"), "LabelsPage");
const LoansPage = page(() => import("./pages/Other"), "LoansPage");
const SettingsPage = page(() => import("./pages/Other"), "SettingsPage");
const CustomersPage = page(() => import("./pages/Customers"), "CustomersPage");
const CustomerDetailPage = page(() => import("./pages/Customers"), "CustomerDetailPage");
const RepairsPage = page(() => import("./pages/Customers"), "RepairsPage");
const RepairJobPage = page(() => import("./pages/Customers"), "RepairJobPage");
const WorkOrderPage = page(() => import("./pages/Customers"), "WorkOrderPage");
const KitsPage = page(() => import("./pages/Kits"), "KitsPage");
const KitPage = page(() => import("./pages/Kits"), "KitPage");
const ShoppingPage = page(() => import("./pages/Shopping"), "ShoppingPage");
const TagsPage = page(() => import("./pages/Tags"), "TagsPage");
const TagScanPage = page(() => import("./pages/Tags"), "TagScanPage");
const MaintenancePage = page(() => import("./pages/Maintenance"), "MaintenancePage");
const ScanPage = page(() => import("./pages/ScanPage"), "default");
const SortPhotos = page(() => import("./pages/SortPhotos"), "default");
const ReportPage = page(() => import("./pages/Report"), "default");
const DuplicatesPage = page(() => import("./pages/Duplicates"), "default");
const RecentlyDeleted = page(() => import("./components/BulkActions"), "RecentlyDeleted");
const ToFinish = page(() => import("./pages/ToFinish"), "default");

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
  { to: "/report", label: "Inventory Report", icon: FileText },
  { to: "/tags", label: "NFC Tags", icon: Nfc, edit: true },
  { to: "/activity", label: "Activity History", icon: Activity },
  { to: "/loans", label: "Loans & Temporary", icon: Handshake },
  { to: "/settings", label: "Settings", icon: Settings },
];

const TITLES: [RegExp, string][] = [
  [/^\/$/, "Home Inventory"], [/^\/search/, "Search"], [/^\/add/, "Add item"], [/^\/scan/, "Scan"], [/^\/items\/\d+/, "Item"], [/^\/items/, "Items"],
  [/^\/containers\/\d+/, "Container"], [/^\/containers/, "Containers"], [/^\/locations/, "Locations"], [/^\/maps/, "Visual maps"], [/^\/labels/, "Labels"],
  [/^\/capture/, "Quick photos"], [/^\/to-finish/, "To finish"], [/^\/sort/, "To sort"], [/^\/deleted/, "Recently deleted"], [/^\/report/, "Inventory report"], [/^\/duplicates/, "Possible duplicates"], [/^\/maintenance/, "Maintenance"], [/^\/kits\/\d+/, "Kit"], [/^\/tags/, "NFC tags"], [/^\/shopping/, "Restock & shopping"], [/^\/tag\//, "Tag"], [/^\/kits/, "Kits"], [/^\/customers\/\d+/, "Customer"], [/^\/customers/, "Customers"], [/^\/repairs\/\d+\/print/, "Work order"], [/^\/repairs\/\d+/, "Repair job"], [/^\/repairs/, "Repairs"], [/^\/activity/, "Activity"], [/^\/loans/, "Loans & temporary"], [/^\/settings/, "Settings"],
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

/** "3 photos waiting to upload": quick photos saved on this phone but not on the server yet. */
function UploadIndicator() {
  const [n, setN] = useState(0);
  const loc = useLocation();
  useEffect(() => {
    startQueue();
    preloadPages();
    const refresh = () => void waitingCount().then(setN);
    refresh();
    return subscribeUploads(refresh);
  }, []);
  if (!n || loc.pathname.startsWith("/capture")) return null;
  return (
    <NavLink to="/capture" className="upload-indicator" aria-live="polite">
      {n} photo{n === 1 ? "" : "s"} waiting to upload
    </NavLink>
  );
}

function Shell({ me }: { me: Me }) {
  const loc = useLocation();
  const nav = useNavigate();
  const [more, setMore] = useState(false);
  useEffect(() => { setMore(false); window.scrollTo(0, 0); }, [loc.pathname]);
  const title = TITLES.find(([re]) => re.test(loc.pathname))?.[1] || "Home Inventory";
  const isRoot = ["/", "/search", "/scan", "/items", "/capture", "/sort", "/report", "/to-finish", "/maintenance", "/kits", "/tags", "/shopping", "/repairs", "/customers", "/containers", "/locations", "/maps", "/labels", "/activity", "/loans", "/settings"].includes(loc.pathname);
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
        <UploadIndicator />
        {me.admin_setup && <AdminSetup />}
        <Suspense fallback={<Spinner />}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/add" element={<AddItem />} />
          <Route path="/scan" element={<ScanPage />} />
          <Route path="/capture" element={<QuickCapture />} />
          <Route path="/sort" element={<SortPhotos />} />
          <Route path="/deleted" element={<RecentlyDeleted />} />
          <Route path="/report" element={<ReportPage />} />
          <Route path="/duplicates" element={<DuplicatesPage />} />
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
        </Suspense>
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
