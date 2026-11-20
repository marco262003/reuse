(function () {
  const R = window.REUSE;
  const { esc } = R;
  const grid = document.getElementById("items-grid");
  const count = document.getElementById("result-count");
  const search = document.getElementById("search");
  const category = document.getElementById("category");
  const sort = document.getElementById("sort");
  const availableOnly = document.getElementById("available-only");
  let seq = 0;
  let items = new Map();

  // Sort and "available only" are remembered per browser (a convenience; it works without storage).
  const PREF = "reuse.browse";
  try {
    const saved = JSON.parse(localStorage.getItem(PREF) || "{}");
    if (saved.sort && [...sort.options].some((o) => o.value === saved.sort)) sort.value = saved.sort;
    availableOnly.checked = Boolean(saved.availableOnly);
  } catch (e) { /* storage blocked: use the defaults */ }
  const savePrefs = () => {
    try { localStorage.setItem(PREF, JSON.stringify({ sort: sort.value, availableOnly: availableOnly.checked })); } catch (e) { /* ignore */ }
  };

  // The main button on a card does the obvious next step for that item.
  function primaryAction(i) {
    if (i.is_available) {
      return i.pending_request_id
        ? `<button type="button" class="btn btn-pending" data-act="pending" title="You already have a pending request for this item">Pending</button>`
        : `<button type="button" class="btn" data-act="request">Request</button>`;
    }
    return i.subscribed
      ? '<button type="button" class="btn secondary" disabled title="You will be notified when it is available">Notifying you</button>'
      : '<button type="button" class="btn" data-act="notify">Notify me</button>';
  }

  const card = (i) => {
    const back = R.backText(i);
    const tag = i.is_available
      ? `<span class="cardtag">${i.available_qty} of ${i.total_qty} available</span>`
      : `<span class="cardtag is-out">${esc(i.unavailable_reason)}</span>`;
    return `
    <article class="resourcecard ${i.is_available ? "" : "is-unavailable"}" data-item="${i.id}">
      <div class="resourcevisual"><img src="${esc(i.image_url || R.placeholder)}" alt="" loading="lazy">${tag}</div>
      <h3>${esc(i.name)}</h3>
      <p>${esc(i.category_label)} · ${esc(i.condition_label)}</p>
      ${back ? `<p class="cardback ${back.none ? "is-none" : ""}">${esc(back.text)}</p>` : ""}
      <div class="resourcefoot">
        <button type="button" class="btn secondary" data-act="details" aria-label="Details for ${esc(i.name)}">Details</button>
        ${primaryAction(i)}
      </div>
    </article>`;
  };

  function renderCount(list, filtered) {
    const avail = list.filter((i) => i.is_available).length;
    count.textContent = list.length
      ? `${list.length} item${list.length === 1 ? "" : "s"}${filtered ? " found" : ""} · ${avail} available now`
      : "";
  }

  async function load(silent) {
    const mine = ++seq;
    if (!silent) R.state.loading(grid, "Loading items…");
    const params = new URLSearchParams();
    if (search.value.trim()) params.set("q", search.value.trim());
    if (category.value) params.set("category", category.value);
    if (availableOnly.checked) params.set("available", "1");
    params.set("sort", sort.value);
    const res = await R.api.get(`/api/items/?${params}`);
    if (mine !== seq) return; // a newer search is already running
    if (!res.ok) { count.textContent = ""; return R.state.error(grid, res.message || "We couldn't load the items.", () => load(false)); }
    items = new Map(res.items.map((i) => [String(i.id), i]));
    const filtered = Boolean(search.value.trim() || category.value || availableOnly.checked);
    renderCount(res.items, filtered);
    if (!res.items.length) {
      const reset = filtered ? '<button type="button" class="btn btn-outline btn-sm" data-act="clear">Clear filters</button>' : "";
      return R.state.empty(grid, filtered ? "No items match. Try another name or category, or show unavailable items too." : "No items have been listed yet.", reset);
    }
    grid.innerHTML = res.items.map(card).join("");
  }

  async function notify(item, btn) {
    btn.disabled = true;
    const res = await R.api.post(`/api/items/${item.id}/subscribe/`);
    if (!res.ok) {
      btn.disabled = false;
      return R.toast(res.message || "We couldn't turn on notifications.", "error");
    }
    R.toast(`We'll let you know when “${item.name}” is available.`, "success");
    load(true);
  }

  grid.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (btn && btn.dataset.act === "clear") {
      search.value = ""; category.value = ""; availableOnly.checked = false; savePrefs();
      return load(false);
    }
    const cardEl = e.target.closest("[data-item]");
    if (!cardEl) return;
    const item = items.get(cardEl.dataset.item);
    const act = btn ? btn.dataset.act : "details";
    if (act === "request" && item) R.ItemUI.openRequestForm(item);
    else if (act === "pending" && item) R.ItemUI.openRequestDetails(item.pending_request_id);
    else if (act === "notify" && item) notify(item, btn);
    else R.ItemUI.openItem(cardEl.dataset.item);
  });
  search.addEventListener("input", R.debounce(() => load(false), 300));
  category.addEventListener("change", () => load(false));
  sort.addEventListener("change", () => { savePrefs(); load(false); });
  availableOnly.addEventListener("change", () => { savePrefs(); load(false); });

  // Voice search where the browser supports it.
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mic = document.getElementById("mic");
  if (Recognition && mic) {
    mic.hidden = false;
    mic.addEventListener("click", () => {
      const rec = new Recognition();
      rec.lang = "en-PH";
      mic.classList.add("is-listening");
      rec.onresult = (e) => { search.value = e.results[0][0].transcript.replace(/[.]$/, ""); load(false); };
      rec.onend = () => mic.classList.remove("is-listening");
      rec.onerror = () => mic.classList.remove("is-listening");
      rec.start();
    });
  }

  load(false);
  R.onChanged(() => load(true));
  R.poll(() => load(true), 30000); // quantities update on their own

  const openId = new URLSearchParams(location.search).get("item");
  if (openId) R.ItemUI.openItem(openId);
})();
