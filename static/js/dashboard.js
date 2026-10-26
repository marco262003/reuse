(function () {
  const R = window.REUSE;
  const { esc, fmt } = R;
  const $ = (id) => document.getElementById(id);

  const thumb = (item) => `<span class="item-cell"><img src="${esc(item.image_url || R.placeholder)}" alt="">${esc(item.name)}</span>`;
  const table = (headers, rows) =>
    `<table class="data-table"><thead><tr>${headers.map((h) => `<th scope="col">${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;

  function renderRequests(list) {
    const box = $("recent-requests");
    if (!list.length) return R.state.empty(box, "You haven’t made any requests yet.", '<a class="btn btn-primary btn-sm" href="/browse/">Browse items</a>');
    box.innerHTML = table(
      ["Request ID", "Item", "Status", "Borrow Date", "Borrow Time", "Return Date", "Return Time", "Quantity", "Action"],
      list.map((r) => `<tr>
        <td>${esc(r.id)}</td><td>${thumb(r.item)}</td><td>${R.badge(r.status, r.status_label)}</td>
        <td>${fmt.date(r.borrow_at)}</td><td>${fmt.time(r.borrow_at)}</td>
        <td>${fmt.date(r.return_at)}</td><td>${fmt.time(r.return_at)}</td>
        <td class="num">${r.quantity}</td>
        <td><button type="button" class="link-btn" data-request="${esc(r.id)}">View</button></td></tr>`)
    );
  }

  function renderReturns(list) {
    const box = $("upcoming-returns");
    if (!list.length) return R.state.empty(box, "Nothing to return right now.");
    box.innerHTML = table(
      ["Item", "Borrowing ID", "Return Date", "Return Time", "Days left", "Action"],
      list.map((b) => `<tr>
        <td>${thumb(b.item)}</td><td>${esc(b.id)}</td>
        <td>Due ${fmt.date(b.due_at)}</td><td>${fmt.time(b.due_at)}</td>
        <td class="${b.status === "overdue" ? "is-overdue-text" : ""}">${esc(fmt.dueText(b.due_at))}</td>
        <td><button type="button" class="link-btn" data-borrowing="${esc(b.id)}">View</button></td></tr>`)
    );
  }

  function renderItems(list) {
    const box = $("recent-items");
    if (!list.length) return R.state.empty(box, "No items are available to request right now. Check back soon.");
    box.innerHTML = table(
      ["Item", "Qty Available", "Times Borrowed", "Condition", "Action"],
      list.map((i) => `<tr>
        <td>${thumb(i)}</td><td class="num">${i.available_qty}</td><td class="num">${i.times_borrowed}</td>
        <td>${esc(i.condition_label)}</td>
        <td><button type="button" class="link-btn" data-item="${i.id}">View</button></td></tr>`)
    );
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
    renderRequests(res.recent_requests);
    renderReturns(res.upcoming_returns);
    renderItems(res.recent_items);
  }

  document.querySelector(".main").addEventListener("click", (e) => {
    const t = e.target.closest("[data-request],[data-borrowing],[data-item]");
    if (!t) return;
    if (t.dataset.request) R.ItemUI.openRequestDetails(t.dataset.request);
    else if (t.dataset.borrowing) R.ItemUI.openBorrowing(t.dataset.borrowing);
    else R.ItemUI.openItem(t.dataset.item);
  });

  R.userReady.then((user) => { if (user) $("welcome-name").textContent = user.short_name; });
  load(false);
  R.onChanged(() => load(true));
  R.poll(() => load(true), 30000);
})();
