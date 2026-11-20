(function () {
  const R = window.REUSE;
  const { esc, fmt } = R;
  const box = document.getElementById("requests-table");
  const search = document.getElementById("search");
  const category = document.getElementById("category");
  let seq = 0;

  // Category, requested-on date and purpose are in the View pop-up.
  const row = (r) => `<tr>
    <td><span class="item-cell"><img src="${esc(r.item.image_url || R.placeholder)}" alt=""><span>${esc(r.item.name)}<span class="cell-sub">${esc(r.id)} · Qty ${r.quantity}</span></span></span></td>
    <td>${R.badge(r.status, r.status_label)}</td>
    <td><span class="cell-stack">${fmt.date(r.borrow_at)}<span class="cell-sub">${fmt.time(r.borrow_at)}</span></span></td>
    <td><span class="cell-stack">${fmt.date(r.return_at)}<span class="cell-sub">${fmt.time(r.return_at)}</span></span></td>
    <td><button type="button" class="btn secondary btn-sm" data-request="${esc(r.id)}">View</button></td></tr>`;

  async function load(silent) {
    const mine = ++seq;
    if (!silent) R.state.loading(box, "Loading your requests…");
    const params = new URLSearchParams();
    if (search.value.trim()) params.set("q", search.value.trim());
    if (category.value) params.set("category", category.value);
    const res = await R.api.get(`/api/requests/?${params}`);
    if (mine !== seq) return;
    if (!res.ok) return R.state.error(box, res.message || "We couldn't load your requests.", () => load(false));
    if (!res.requests.length) {
      return params.toString()
        ? R.state.empty(box, "No requests match your search.")
        : R.state.empty(box, "You haven’t made any requests yet.", '<a class="btn btn-primary btn-sm" href="/browse/">Browse items</a>');
    }
    box.innerHTML = `<table class="data-table"><thead><tr>
      <th scope="col">Item</th><th scope="col">Status</th><th scope="col">Borrow</th>
      <th scope="col">Return</th><th scope="col">Action</th></tr></thead><tbody>${res.requests.map(row).join("")}</tbody></table>`;
    R.labelTables();
  }

  box.addEventListener("click", (e) => {
    const t = e.target.closest("[data-request]");
    if (t) R.ItemUI.openRequestDetails(t.dataset.request);
  });
  search.addEventListener("input", R.debounce(() => load(false), 300));
  category.addEventListener("change", () => load(false));

  const mic = document.getElementById("mic");
  if (mic) mic.hidden = true; // voice search lives on the Browse page

  load(false);
  R.onChanged(() => load(true));
  R.poll(() => load(true), 30000);

  const openId = new URLSearchParams(location.search).get("open");
  if (openId) R.ItemUI.openRequestDetails(openId);
})();
