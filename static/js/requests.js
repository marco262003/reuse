(function () {
  const R = window.REUSE;
  const { esc, fmt } = R;
  const box = document.getElementById("requests-table");
  const search = document.getElementById("search");
  const category = document.getElementById("category");
  let seq = 0;

  const row = (r) => `<tr>
    <td>${esc(r.id)}</td>
    <td><span class="item-cell"><img src="${esc(r.item.image_url || R.placeholder)}" alt="">${esc(r.item.name)}</span></td>
    <td>${R.badge(r.status, r.status_label)}</td>
    <td>${esc(r.item.category_label)}</td>
    <td>${fmt.date(r.created_at)}</td>
    <td>${fmt.date(r.borrow_at)}</td><td>${fmt.time(r.borrow_at)}</td>
    <td>${fmt.date(r.return_at)}</td><td>${fmt.time(r.return_at)}</td>
    <td class="num">${r.quantity}</td>
    <td><span class="cell-clip" title="${esc(r.purpose)}">${esc(r.purpose)}</span></td>
    <td><button type="button" class="link-btn" data-request="${esc(r.id)}">View</button></td></tr>`;

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
      <th scope="col">Request ID</th><th scope="col">Item</th><th scope="col">Status</th><th scope="col">Category</th>
      <th scope="col">Requested On</th><th scope="col">Borrow Date</th><th scope="col">Borrow Time</th>
      <th scope="col">Return Date</th><th scope="col">Return Time</th><th scope="col">Quantity</th>
      <th scope="col">Purpose</th><th scope="col">Action</th></tr></thead><tbody>${res.requests.map(row).join("")}</tbody></table>`;
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
