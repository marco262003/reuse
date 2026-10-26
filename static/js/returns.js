(function () {
  const R = window.REUSE;
  const { esc, fmt } = R;
  const grid = document.getElementById("returns-grid");
  let records = [];

  // Status comes from the due date and the current time, so it changes while the page stays open.
  const statusOf = (r) => (new Date(r.due_at) < new Date() ? ["overdue", "Overdue"] : ["due_soon", "Due Soon"]);

  function render() {
    if (!records.length) return R.state.empty(grid, "You have nothing to return. Items you pick up will show here until the admin confirms the return.");
    const sorted = [...records].sort((a, b) => new Date(a.due_at) - new Date(b.due_at)); // overdue sorts first
    grid.innerHTML = sorted.map((r) => {
      const [status, label] = statusOf(r);
      return `<article class="record-card ${status === "overdue" ? "is-overdue" : ""}">
        <img src="${esc(r.item.image_url || R.placeholder)}" alt="">
        <div class="record-info">
          <strong>${esc(r.item.name)}</strong>
          <span>Borrowing ID: ${esc(r.id)}</span>
          <span>Quantity: ${r.quantity}</span>
          <span>Due on: ${fmt.dateTime(r.due_at)}</span>
          <span>Return to: ${esc(r.return_area)}</span>
          <span>Status: ${R.badge(status, label)}</span>
        </div>
        <button type="button" class="btn btn-primary" data-id="${esc(r.id)}">View Details</button>
      </article>`;
    }).join("");
  }

  async function load(silent) {
    if (!silent) R.state.loading(grid);
    const res = await R.api.get("/api/returns/");
    if (!res.ok) return R.state.error(grid, res.message || "We couldn't load your returns.", () => load(false));
    records = res.returns;
    render();
  }

  grid.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-id]");
    if (btn) R.ItemUI.openBorrowing(btn.dataset.id);
  });

  load(false);
  R.onChanged(() => load(true));
  R.poll(() => load(true), 60000);
  setInterval(() => { if (records.length && !document.hidden && !document.querySelector(".modal-overlay")) render(); }, 30000);

  const openId = new URLSearchParams(location.search).get("open");
  if (openId) R.ItemUI.openBorrowing(openId);
})();
