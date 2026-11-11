(function () {
  const R = window.REUSE;
  const { esc } = R;
  const grid = document.getElementById("items-grid");
  const search = document.getElementById("search");
  const category = document.getElementById("category");
  let seq = 0;

  const card = (i) => `
    <article class="resourcecard ${i.is_available ? "" : "is-unavailable"}" data-item="${i.id}">
      <div class="resourcevisual"><img src="${esc(i.image_url || R.placeholder)}" alt="" loading="lazy"></div>
      <h3>${esc(i.name)}</h3>
      <p>${esc(i.category_label)} · ${esc(i.condition_label)}${i.is_available ? "" : ` · ${esc(i.unavailable_reason)}`}</p>
      <div class="resourcefoot">
        ${i.is_available
          ? `<span class="status">${i.available_qty} Available</span>`
          : '<span class="status declined">Unavailable</span>'}
        <button type="button" class="btn secondary" data-item="${i.id}">View details</button>
      </div>
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
