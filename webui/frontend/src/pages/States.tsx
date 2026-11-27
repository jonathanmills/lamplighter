// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Designed empty and error states.
import { Link } from "react-router-dom";
import type { NavItem } from "../nav";

export function ComingSoon({ item }: { item: NavItem }) {
  return (
    <>
      <div className="page-head"><h1>{item.title}</h1></div>
      <section className="panel">
        <div className="empty">
          <span className="phase">Planned for {item.phase}</span>
          <h2>Not built yet</h2>
          <p className="muted">{item.blurb}</p>
          <p className="muted">The plan is in <code>doc/webui-design.md</code>.</p>
        </div>
      </section>
    </>
  );
}

export function Forbidden({ perm }: { perm: string }) {
  return (
    <section className="panel">
      <div className="empty">
        <h2>You don't have access to this page</h2>
        <p className="muted">It needs the <code>{perm}</code> permission. Ask a Lamplighter administrator to add you to a group that grants it.</p>
        <Link to="/">Back to the overview</Link>
      </div>
    </section>
  );
}

export function NotFound() {
  return (
    <section className="panel">
      <div className="empty">
        <h2>Page not found</h2>
        <p className="muted">No page lives at this address.</p>
        <Link to="/">Back to the overview</Link>
      </div>
    </section>
  );
}
