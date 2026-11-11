(function () {
  const R = window.REUSE;
  const { esc, fmt } = R;
  const $ = (id) => document.getElementById(id);

  // One activity row in the design's list style: thumbnail, name + detail line, status, View.
  const row = (item, sub, badge, data) => `<div class="suggestion">
      <img class="suggestionthumb" src="${esc(item.image_url || R.placeholder)}" alt="">
      <div class="grow"><b>${esc(item.name)}</b>${sub}</div>
      ${badge}
      <button type="button" class="btn secondary btn-sm" ${data}>View</button></div>`;
  const table = (headers, rows) =>
    `<table class="data-table"><thead><tr>${headers.map((h) => `<th scope="col">${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;

  // Overdue or due within 24 hours: the things to act on first.
  const isUrgent = (b) => new Date(b.due_at) - Date.now() < 86400000;

  function renderAttention(returns, pickups) {
    const urgent = returns.filter(isUrgent);
    $("attention").hidden = !urgent.length && !pickups.length;
    const line = (item, sub, badge, data) => row(item, sub, badge, data);
    $("attention-list").innerHTML =
      urgent.map((b) => line(b.item, `Return to ${esc(b.return_area)} by ${fmt.dateTime(b.due_at)}`,
        R.dueBadge(b.due_at), `data-borrowing="${esc(b.id)}"`)).join("") +
      pickups.map((r) => line(r.item, `Reserved for ${fmt.dateTime(r.borrow_at)}`,
        R.badge("approved", "Ready for pickup"), `data-request="${esc(r.id)}"`)).join("");
  }

  function renderRequests(list) {
    const box = $("recent-requests");
    if (!list.length) return R.state.empty(box, "You haven’t made any requests yet.", '<a class="btn btn-primary btn-sm" href="/browse/">Browse items</a>');
    box.innerHTML = table(
      ["Item", "Status", "Borrow", "Return", "Action"],
      list.map((r) => `<tr>
        <td><span class="item-cell"><img src="${esc(r.item.image_url || R.placeholder)}" alt=""><span>${esc(r.item.name)}<span class="cell-sub">${esc(r.id)} · Qty ${r.quantity}</span></span></span></td>
        <td>${R.badge(r.status, r.status_label)}</td>
        <td><span class="cell-stack">${fmt.date(r.borrow_at)}<span class="cell-sub">${fmt.time(r.borrow_at)}</span></span></td>
        <td><span class="cell-stack">${fmt.date(r.return_at)}<span class="cell-sub">${fmt.time(r.return_at)}</span></span></td>
        <td><button type="button" class="btn secondary btn-sm" data-request="${esc(r.id)}">View</button></td></tr>`)
    );
  }

  function renderReturns(list) {
    const box = $("upcoming-returns");
    const later = list.filter((b) => !isUrgent(b)); // urgent ones are already listed under Needs Attention
    if (!later.length) return R.state.empty(box, list.length ? "No other returns coming up." : "Nothing to return right now.");
    box.innerHTML = later.map((b) => row(b.item,
      `${esc(b.id)} · Return by ${fmt.date(b.due_at)}, ${fmt.time(b.due_at)}`,
      R.dueBadge(b.due_at), `data-borrowing="${esc(b.id)}"`)).join("");
  }

  function renderItems(list) {
    const box = $("recent-items");
    if (!list.length) return R.state.empty(box, "No items are available to request right now. Check back soon.");
    box.innerHTML = list.map((i) => row(i,
      `${i.available_qty} available · ${esc(i.condition_label)} · Borrowed ${i.times_borrowed} time${i.times_borrowed === 1 ? "" : "s"}`,
      "", `data-item="${i.id}"`)).join("");
  }

  async function load(silent) {
    if (!silent) ["recent-requests", "upcoming-returns", "recent-items"].forEach((id) => R.state.loading($(id)));
    const res = await R.api.get("/api/dashboard/");
    if (!res.ok) {
      ["recent-requests", "upcoming-returns", "recent-items"].forEach((id) =>
        R.state.error($(id), res.message || "We couldn't load your dashboard.", () => load(false)));
      return;
    }
    $("stat-available").textContent = res.counts.available_items;
    $("stat-requests").textContent = res.counts.my_requests;
    $("stat-borrowings").textContent = res.counts.active_borrowings;
    $("stat-alerts").textContent = res.counts.alerts;
    renderAttention(res.upcoming_returns, res.ready_for_pickup);
    renderRequests(res.recent_requests);
    renderReturns(res.upcoming_returns);
    renderItems(res.recent_items);
    R.labelTables();
  }

  document.querySelector(".main").addEventListener("click", (e) => {
    const t = e.target.closest("[data-request],[data-borrowing],[data-item]");
    if (!t) return;
    if (t.dataset.request) R.ItemUI.openRequestDetails(t.dataset.request);
    else if (t.dataset.borrowing) R.ItemUI.openBorrowing(t.dataset.borrowing);
    else R.ItemUI.openItem(t.dataset.item);
  });

  R.userReady.then((user) => { if (user && !$("welcome-name").textContent.trim()) $("welcome-name").textContent = user.full_name; });
  load(false);
  R.onChanged(() => load(true));
  R.poll(() => load(true), 30000);
})();
