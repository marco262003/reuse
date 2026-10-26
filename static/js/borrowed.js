(function () {
  const R = window.REUSE;
  const { esc, fmt } = R;
  const activeGrid = document.getElementById("active-grid");
  const pastGrid = document.getElementById("past-grid");

  function card(rec) {
    const past = rec.status === "returned" || rec.status === "canceled";
    // Outstanding items read "Active" here; Overdue still shows so nothing is hidden.
    const label = rec.status === "due_soon" ? "Active" : rec.status_label;
    let dateLine;
    if (rec.status === "returned") dateLine = `Returned on: ${fmt.dateTime(rec.returned_at)}`;
    else if (rec.status === "canceled") dateLine = `Canceled on: ${fmt.dateTime(rec.canceled_at)}`;
    else dateLine = `Due on: ${fmt.dateTime(rec.due_at)}`;
    return `<article class="record-card ${rec.status === "overdue" ? "is-overdue" : ""}">
      <img src="${esc(rec.item.image_url || R.placeholder)}" alt="">
      <div class="record-info">
        <strong>${esc(rec.item.name)}</strong>
        <span>${esc(rec.kind === "borrowing" ? "Borrowing ID" : "Request ID")}: ${esc(rec.id)}</span>
        <span>Quantity: ${rec.quantity}</span>
        <span>${dateLine}</span>
        <span>${past ? "Returned to" : "Return to"}: ${esc(rec.return_area)}</span>
        <span>Status: ${esc(label)}</span>
      </div>
      <button type="button" class="btn btn-primary" data-kind="${esc(rec.kind)}" data-id="${esc(rec.id)}">View Details</button>
    </article>`;
  }

  async function load(silent) {
    if (!silent) { R.state.loading(activeGrid); R.state.loading(pastGrid); }
    const res = await R.api.get("/api/borrowings/");
    if (!res.ok) {
      R.state.error(activeGrid, res.message || "We couldn't load your borrowed items.", () => load(false));
      pastGrid.innerHTML = "";
      return;
    }
    if (res.active.length) activeGrid.innerHTML = res.active.map(card).join("");
    else R.state.empty(activeGrid, "You have no items borrowed right now. Once the admin confirms a pickup, it shows up here.");
    if (res.past.length) pastGrid.innerHTML = res.past.map(card).join("");
    else R.state.empty(pastGrid, "Returned and canceled items will appear here.");
  }

  document.querySelector(".main").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-id]");
    if (btn) R.ItemUI.openRecord(btn.dataset.kind, btn.dataset.id);
  });

  load(false);
  R.onChanged(() => load(true));
  R.poll(() => load(true), 30000);

  const openId = new URLSearchParams(location.search).get("open");
  if (openId) R.ItemUI.openRecord(openId.startsWith("R") ? "reservation" : "borrowing", openId);
})();
