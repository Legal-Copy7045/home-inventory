import { useEffect, useState } from "react";
import { HashRouter, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Activity, ArrowLeft, Box, Handshake, Home, LayoutGrid, Map as MapIcon, MapPin, Menu, Package, Plus, Printer, ScanLine, Search, Settings } from "lucide-react";
import { api, type Me } from "./api";
import { MeContext, Sheet, Spinner, ToastHost } from "./components/ui";
import AddItem from "./pages/AddItem";
import { DashboardPage, ItemsPage, LocationDetailPage, LocationsPage, SearchBox, SearchPage } from "./pages/Browse";
import { ContainerDetail, ContainersPage } from "./pages/Containers";
import ItemDetail from "./pages/ItemDetail";
import { MapView, MapsPage } from "./pages/Maps";
import { ActivityPage, LabelsPage, LoansPage, SettingsPage } from "./pages/Other";
import ScanPage from "./pages/ScanPage";

const NAV = [
  { to: "/", label: "Dashboard", icon: Home, end: true },
  { to: "/search", label: "Search", icon: Search },
  { to: "/add", label: "Add Item", icon: Plus, edit: true },
  { to: "/scan", label: "Scan", icon: ScanLine },
  { to: "/items", label: "Items", icon: Package },
  { to: "/containers", label: "Containers", icon: Box },
  { to: "/locations", label: "Locations", icon: MapPin },
  { to: "/maps", label: "Visual Maps", icon: MapIcon },
  { to: "/labels", label: "Print Labels", icon: Printer },
  { to: "/activity", label: "Activity History", icon: Activity },
  { to: "/loans", label: "Loans & Temporary", icon: Handshake },
  { to: "/settings", label: "Settings", icon: Settings },
];

const TITLES: [RegExp, string][] = [
  [/^\/$/, "Home Inventory"], [/^\/search/, "Search"], [/^\/add/, "Add item"], [/^\/scan/, "Scan"], [/^\/items\/\d+/, "Item"], [/^\/items/, "Items"],
  [/^\/containers\/\d+/, "Container"], [/^\/containers/, "Containers"], [/^\/locations/, "Locations"], [/^\/maps/, "Visual maps"], [/^\/labels/, "Labels"],
  [/^\/activity/, "Activity"], [/^\/loans/, "Loans & temporary"], [/^\/settings/, "Settings"],
];

/** QR labels can encode `<base>?c=CODE`; open the container when launched that way. */
function DeepLink() {
  const nav = useNavigate();
  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get("c");
    if (!code) return;
    api.post<{ container_id: number }>("qr/resolve", { text: `HINV:C:${code}` })
      .then((r) => nav(`/containers/${r.container_id}`, { replace: true }))
      .catch(() => undefined);
  }, [nav]);
  return null;
}

function Shell({ me }: { me: Me }) {
  const loc = useLocation();
  const nav = useNavigate();
  const [more, setMore] = useState(false);
  useEffect(() => { setMore(false); window.scrollTo(0, 0); }, [loc.pathname]);
  const title = TITLES.find(([re]) => re.test(loc.pathname))?.[1] || "Home Inventory";
  const isRoot = ["/", "/search", "/scan", "/items", "/containers", "/locations", "/maps", "/labels", "/activity", "/loans", "/settings"].includes(loc.pathname);
  const items = NAV.filter((n) => !n.edit || me.can_edit);
  return (
    <div className="app">
      <nav className="sidebar" aria-label="Main">
        <div className="brand"><LayoutGrid size={22} color="var(--primary)" /> Home Inventory</div>
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
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/add" element={<AddItem />} />
          <Route path="/scan" element={<ScanPage />} />
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
