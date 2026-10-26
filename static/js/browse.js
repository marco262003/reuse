(function () {
  const R = window.REUSE;
  const { esc } = R;
  const grid = document.getElementById("items-grid");
  const search = document.getElementById("search");
  const category = document.getElementById("category");
  let seq = 0;

  const card = (i) => `
    <article class="item-card ${i.is_available ? "" : "is-unavailable"}" data-item="${i.id}">
      <img src="${esc(i.image_url || R.placeholder)}" alt="" loading="lazy">
      <dl class="item-meta">
        <div><dt>Item:</dt><dd>${esc(i.name)}</dd></div>
        <div><dt>Qty Available:</dt><dd>${i.available_qty}</dd></div>
        <div><dt>Times Borrowed:</dt><dd>${i.times_borrowed}</dd></div>
        <div><dt>Condition:</dt><dd>${esc(i.condition_label)}</dd></div>
      </dl>
      <p class="avail ${i.is_available ? "avail-yes" : "avail-no"}">${i.is_available ? "AVAILABLE" : "UNAVAILABLE"}${i.is_available ? "" : `<span class="avail-reason">${esc(i.unavailable_reason)}</span>`}</p>
      <button type="button" class="btn btn-primary" data-item="${i.id}">View</button>
    </article>`;

  async function load(silent) {
    const mine = ++seq;
    if (!silent) R.state.loading(grid, "Loading items…");
    const params = new URLSearchParams();
    if (search.value.trim()) params.set("q", search.value.trim());
    if (category.value) params.set("category", category.value);
    const res = await R.api.get(`/api/items/?${params}`);
    if (mine !== seq) return; // a newer search is already running
    if (!res.ok) return R.state.error(grid, res.message || "We couldn't load the items.", () => load(false));
    if (!res.items.length) {
      const filtered = params.toString();
      return R.state.empty(grid, filtered ? "No items match your search. Try another name or category." : "No items have been listed yet.");
    }
    grid.innerHTML = res.items.map(card).join("");
  }

  grid.addEventListener("click", (e) => {
    const target = e.target.closest("[data-item]");
    if (target) R.ItemUI.openItem(target.dataset.item);
  });
  search.addEventListener("input", R.debounce(() => load(false), 300));
  category.addEventListener("change", () => load(false));

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
