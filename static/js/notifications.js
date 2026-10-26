(function () {
  const R = window.REUSE;
  const { esc, fmt } = R;
  const list = document.getElementById("notes");
  const markAll = document.getElementById("mark-all");
  let notes = [];

  function render() {
    markAll.disabled = !notes.some((n) => !n.is_read);
    if (!notes.length) {
      list.innerHTML = '<li class="state"><p>You have no notifications yet. Updates about your requests and returns will show up here.</p></li>';
      return;
    }
    list.innerHTML = notes.map((n) => `
      <li class="note ${n.is_read ? "is-read" : ""}" data-id="${n.id}">
        ${n.link
          ? `<button type="button" class="note-text" data-open="${n.id}">${esc(n.message)}</button>`
          : `<p class="note-text">${esc(n.message)}</p>`}
        <time datetime="${esc(n.created_at)}">${esc(fmt.ago(n.created_at))}</time>
        ${n.is_read ? "" : `<button type="button" class="btn btn-primary btn-sm" data-read="${n.id}">Mark as read</button>`}
      </li>`).join("");
  }

  async function load() {
    R.state.loading(list);
    const res = await R.api.get("/api/notifications/");
    if (!res.ok) return R.state.error(list, res.message || "We couldn't load your notifications.", load);
    notes = res.notifications;
    render();
  }

  async function markRead(id) {
    const note = notes.find((n) => n.id === Number(id));
    if (!note || note.is_read) return true;
    const res = await R.api.post(`/api/notifications/${id}/read/`);
    if (!res.ok) { R.toast(res.message || "We couldn't update this notification.", "error"); return false; }
    note.is_read = true;
    render();
    R.refreshBadge();
    return true;
  }

  list.addEventListener("click", async (e) => {
    const read = e.target.closest("[data-read]");
    if (read) return markRead(read.dataset.read);
    const open = e.target.closest("[data-open]");
    if (open) {
      const note = notes.find((n) => n.id === Number(open.dataset.open));
      await markRead(note.id);
      window.location.href = note.link;
    }
  });

  markAll.addEventListener("click", async () => {
    markAll.disabled = true;
    const res = await R.api.post("/api/notifications/read-all/");
    if (!res.ok) { markAll.disabled = false; return R.toast(res.message || "We couldn't update your notifications.", "error"); }
    notes.forEach((n) => { n.is_read = true; });
    render();
    R.refreshBadge();
  });

  load();
  R.poll(async () => { // pick up new notifications while the page is open
    const res = await R.api.get("/api/notifications/");
    if (res.ok) { notes = res.notifications; render(); }
  }, 30000);
})();
