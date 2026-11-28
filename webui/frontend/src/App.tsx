// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, type Me } from "./api";
import { NAV, navByPath, type NavItem } from "./nav";
import { setTheme, useTheme } from "./theme";
import Overview from "./pages/Overview";
import { ReportEditor, ReportsList, ReportView } from "./pages/Reports";
import ColdData from "./pages/ColdData";
import Users from "./pages/Users";
import UserDetail from "./pages/UserDetail";
import Directory from "./pages/Directory";
import AuditLog from "./pages/AuditLog";
import Access from "./pages/Access";
import Browse from "./pages/Browse";
import Triage from "./pages/Triage";
import Policies from "./pages/Policies";
import Search from "./pages/Search";
import { ComingSoon, Forbidden, NotFound } from "./pages/States";

const MeContext = createContext<Me | null>(null);
export const useMe = () => useContext(MeContext)!;
// any of the permissions (one, or a list)
export const can = (me: Me, perm: string | string[]) =>
  (Array.isArray(perm) ? perm : [perm]).some((x) => me.permissions.includes(x));

function readCollapsed(): boolean {
  try { return localStorage.getItem("lamplighter-sidebar") === "collapsed"; } catch { return false; }
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.me().then(setMe).catch((e) => setError(String(e.message ?? e))); }, []);

  if (error) return <div className="empty"><h2>Lamplighter could not load</h2><p className="error-line">{error}</p></div>;
  if (!me) return null; // a brief blank while /me loads; no layout shift
  return (
    <MeContext.Provider value={me}>
      <Shell />
    </MeContext.Provider>
  );
}

function Shell() {
  const me = useMe();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    try { localStorage.setItem("lamplighter-sidebar", next ? "collapsed" : "expanded"); } catch { /* ignore */ }
  };
  const visible = NAV.filter((n) => can(me, n.perm));
  const groups = [...new Set(visible.map((n) => n.group))];

  return (
    <div className={"shell" + (collapsed ? " collapsed" : "")}>
      <aside className="sidebar" aria-label="Main navigation">
        <div className="brand"><span className="brand-mark" role="img" aria-label="Lamplighter logo" /><span className="brand-text">Lamplighter</span></div>
        <nav className="nav">
          {groups.map((g) => (
            <div className="nav-group" key={g}>
              <div className="nav-label">{g}</div>
              {visible.filter((n) => n.group === g).map((n) => (
                <NavLink key={n.path} to={n.path} end title={n.title}>
                  {n.icon}<span className="nav-text">{n.title}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <button className="collapse-btn" type="button" onClick={toggle} aria-pressed={collapsed}>
            <span aria-hidden="true">{collapsed ? "»" : "«"}</span><span className="nav-text"> Collapse</span>
          </button>
        </div>
      </aside>
      <div className="main">
        <TopBar />
        <main className="content" id="content">
          <Routes>
            {NAV.map((n) => (
              <Route key={n.path} path={n.path} element={<Page item={n} />} />
            ))}
            <Route path="/usage/user/:uid" element={can(me, "usage.view_aggregates") ? <UserDetail /> : <Forbidden perm="usage.view_aggregates" />} />
            <Route path="/reports/new" element={<ReportEditor />} />
            <Route path="/reports/:id" element={<ReportView />} />
            <Route path="/reports/:id/edit" element={<ReportEditor />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function Page({ item }: { item: NavItem }) {
  const me = useMe();
  useEffect(() => { document.title = `${item.title} · Lamplighter`; }, [item]);
  if (!can(me, item.perm)) return <Forbidden perm={[item.perm].flat().join(" or ")} />;
  if (item.path === "/") return <Overview />;
  if (item.path === "/cold") return <ColdData />;
  if (item.path === "/usage") return <Users />;
  if (item.path === "/admin/directory") return <Directory />;
  if (item.path === "/admin/audit") return <AuditLog />;
  if (item.path === "/admin/access") return <Access />;
  if (item.path === "/browse") return <Browse />;
  if (item.path === "/reports") return <ReportsList />;
  if (item.path === "/triage") return <Triage />;
  if (item.path === "/policies") return <Policies />;
  if (item.path === "/search") return <Search />;
  return <ComingSoon item={item} />;
}

function TopBar() {
  const { pathname } = useLocation();
  const item = navByPath(pathname);
  const userPage = pathname.match(/^\/usage\/user\/([^/]+)$/);
  const reportPage = pathname.match(/^\/reports\/(new|\d+)(\/edit)?$/);
  return (
    <header className="topbar">
      <div className="crumbs" aria-label="Breadcrumb">
        {userPage ? <><span>Monitor</span><span className="sep">›</span><NavLink to="/usage">Users & groups</NavLink>
                      <span className="sep">›</span><b>{decodeURIComponent(userPage[1])}</b></>
          : reportPage ? <><span>Data</span><span className="sep">›</span><NavLink to="/reports">Reports</NavLink>
                      <span className="sep">›</span><b>{reportPage[1] === "new" ? "New report" : reportPage[2] ? "Edit" : "Report"}</b></>
          : item ? <><span>{item.group}</span><span className="sep">›</span><b>{item.title}</b></> : <b>Not found</b>}
      </div>
      <div className="topbar-right"><UserMenu /></div>
    </header>
  );
}

function UserMenu() {
  const me = useMe();
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const initials = me.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  return (
    <div className="user-menu" ref={ref}>
      <button className="user-btn" type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="avatar" aria-hidden="true">{initials}</span>
        <span className="user-name">{me.name}</span>
      </button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-head">
            <b>{me.name}</b>
            <span className="muted">{me.uid}{me.mail ? ` · ${me.mail}` : ""}</span>
            <span style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
              {me.roles.map((r) => <span className="chip" key={r}>{r}</span>)}
            </span>
          </div>
          <button className="menu-item" role="menuitem" type="button"
                  onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            <span>{theme === "dark" ? "Light theme" : "Dark theme"}</span>
          </button>
          <form method="post" action="/auth/logout" style={{ margin: 0 }}>
            <button className="menu-item" role="menuitem" type="submit"><span>Sign out</span></button>
          </form>
        </div>
      )}
    </div>
  );
}
