(function () {
  const R = window.REUSE;
  const { esc, fmt } = R;
  const activeGrid = document.getElementById("active-grid");
  const pastGrid = document.getElementById("past-grid");
  const params = new URLSearchParams(location.search);

  const when = (iso) => iso ? `<span class="cell-stack">${fmt.date(iso)}<span class="cell-sub">${fmt.time(iso)}</span></span>` : "–";
  const resource = (rec) => `<span class="item-cell"><img src="${esc(rec.item.image_url || R.placeholder)}" alt=""><span><strong>${esc(rec.item.name)}</strong><span class="cell-sub">Qty ${rec.quantity} · ${esc(rec.return_area)}</span></span></span>`;
  const viewBtn = (rec) => `<button type="button" class="btn secondary btn-sm" data-kind="${esc(rec.kind)}" data-id="${esc(rec.id)}">View Details</button>`;
  const head = (cols) => `<thead><tr>${cols.map((c) => `<th scope="col">${c}</th>`).join("")}</tr></thead>`;

  // Active: reference, resource, borrowed on, due date, status (soonest due first, so overdue leads).
  const activeTable = (list) => `<table class="data-table">${head(["Reference", "Resource", "Borrow Date", "Due Date", "Status", "Action"])}<tbody>
      ${list.map((rec) => `<tr>
        <td><strong>${esc(rec.id)}</strong></td><td>${resource(rec)}</td>
        <td>${when(rec.picked_up_at)}</td>
        <td class="${rec.status === "overdue" ? "is-overdue-text" : ""}">${when(rec.due_at)}</td>
        <td>${R.dueBadge(rec.due_at)}</td><td>${viewBtn(rec)}</td></tr>`).join("")}</tbody></table>`;

  // History: returned borrowings and canceled reservations.
  const pastTable = (list) => `<table class="data-table">${head(["Reference", "Resource", "Borrowed", "Returned / Canceled", "Status", "Action"])}<tbody>
      ${list.map((rec) => `<tr>
        <td><strong>${esc(rec.id)}</strong><span class="cell-sub">${rec.kind === "borrowing" ? "Borrowing" : "Reservation"}</span></td>
        <td>${resource(rec)}</td><td>${when(rec.picked_up_at)}</td>
        <td>${when(rec.status === "canceled" ? rec.canceled_at : rec.returned_at)}</td>
        <td>${R.badge(rec.status, rec.status_label)}</td><td>${viewBtn(rec)}</td></tr>`).join("")}</tbody></table>`;

  async function load(silent) {
    if (!silent) { R.state.loading(activeGrid); R.state.loading(pastGrid); }
    const res = await R.api.get("/api/borrowings/");
    if (!res.ok) {
      R.state.error(activeGrid, res.message || "We couldn't load your borrowed items.", () => load(false));
      pastGrid.innerHTML = "";
      return;
    }
    document.getElementById("count-active").textContent = res.active.length;
    document.getElementById("count-history").textContent = res.past.length;
    if (res.active.length) activeGrid.innerHTML = activeTable(res.active);
    else R.state.empty(activeGrid, "You have no items borrowed right now. Once the admin confirms a pickup, it shows up here.");
    if (res.past.length) pastGrid.innerHTML = pastTable(res.past);
    else R.state.empty(pastGrid, "Returned and canceled items will appear here.");
  }

  // ---------------------------------------------------------------- tabs
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  // The sidebar has "My Borrowings" and "Borrowing History": keep it, the heading and the URL in step with the tab.
  const TAB_TEXT = {
    active: ["My Borrowings", "Monitor your active borrowing transactions."],
    history: ["Borrowing History", "View your previous borrowing transactions."],
  };
  function syncChrome(name) {
    const [title, sub] = TAB_TEXT[name];
    const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
    set("borrowed-title", title); set("borrowed-sub", sub); set("crumb", title);
    document.title = `${title} | REUSE`;
    document.querySelectorAll("[data-nav-tab]").forEach((a) => a.classList.toggle("active", a.dataset.navTab === name));
    const url = new URL(location.href);
    if (name === "history") url.searchParams.set("tab", "history"); else url.searchParams.delete("tab");
    history.replaceState(null, "", url);
  }
  function select(tab, focus) {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
    syncChrome(tab.id === "tab-history" ? "history" : "active");
    if (focus) tab.focus();
  }
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") select(tabs[(i + 1) % tabs.length], true);
      if (e.key === "ArrowLeft") select(tabs[(i - 1 + tabs.length) % tabs.length], true);
    });
  });
  if (params.get("tab") === "history") select(document.getElementById("tab-history"));

  document.querySelector(".main").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-id]");
    if (btn) R.ItemUI.openRecord(btn.dataset.kind, btn.dataset.id);
  });

  load(false);
  R.onChanged(() => load(true));
  R.poll(() => load(true), 30000);

  const openId = params.get("open");
  if (openId) R.ItemUI.openRecord(openId.startsWith("R") ? "reservation" : "borrowing", openId);
})();
