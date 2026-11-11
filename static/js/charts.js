/* Draws the dashboard's line chart and donut (REUSE design) from values already rendered in the page. */
(function () {
  // Request statuses arrive in model order: pending, approved, rejected, canceled, picked up.
  const COLORS = ["#eae6e9", "#800e30", "#a99da3", "#5e5560", "#c98b9c"];
  const NS = "http://www.w3.org/2000/svg";

  // Line chart: <div data-linechart><ol class="chartdata"><li data-label data-value>…</li></ol></div>
  document.querySelectorAll("[data-linechart]").forEach((box) => {
    const points = [...box.querySelectorAll("li[data-value]")].map((li) => ({ label: li.dataset.label, value: Number(li.dataset.value) || 0 }));
    if (!points.length) return;
    const W = 500, H = 145, top = 14, bottom = 8;
    const max = Math.max(...points.map((p) => p.value), 1) * 1.25; // headroom above the peak
    const x = (i) => (points.length === 1 ? W / 2 : (i / (points.length - 1)) * W);
    const y = (v) => H - bottom - (v / max) * (H - top - bottom);
    const coords = points.length === 1
      ? [[0, y(points[0].value)], [W, y(points[0].value)]]
      : points.map((p, i) => [x(i), y(p.value)]);
    const line = coords.map(([cx, cy], i) => `${i ? "L" : "M"}${cx.toFixed(1)} ${cy.toFixed(1)}`).join("");

    const chart = document.createElement("div");
    chart.className = "chart";
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label", `Pickups per day: ${points.map((p) => `${p.label} ${p.value}`).join(", ")}`);
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.innerHTML =
      `<path d="${line}L${W} ${H}L0 ${H}Z" fill="#f0e7eb" stroke="none"/>` +
      `<path d="${line}" fill="none" stroke="#800e30" stroke-width="3" vector-effect="non-scaling-stroke"/>`;
    chart.appendChild(svg);

    // Up to six evenly spaced day labels under the chart.
    const axis = document.createElement("div");
    axis.className = "axis";
    const step = Math.max(1, Math.ceil(points.length / 6));
    points.forEach((p, i) => {
      if (i % step && i !== points.length - 1) return;
      const span = document.createElement("span");
      span.textContent = `${p.label} (${p.value})`;
      axis.appendChild(span);
    });
    box.append(chart, axis);
  });

  // Donut: <div data-donut> with <span data-donut-total> and legend <li data-value>.
  document.querySelectorAll("[data-donut]").forEach((box) => {
    const items = [...box.querySelectorAll("li[data-value]")];
    const values = items.map((li) => Number(li.dataset.value) || 0);
    const total = values.reduce((a, b) => a + b, 0);
    const totalEl = box.querySelector("[data-donut-total]");
    if (totalEl) totalEl.textContent = String(total);
    let start = 0;
    const stops = [];
    items.forEach((li, i) => {
      const color = COLORS[i % COLORS.length];
      const dot = li.querySelector(".dot");
      if (dot) dot.style.background = color;
      const pct = total ? (values[i] / total) * 100 : 0;
      const b = li.querySelector("b");
      if (b) b.textContent = `${values[i]} · ${Math.round(pct)}%`;
      if (pct > 0) { stops.push(`${color} ${start}% ${start + pct}%`); start += pct; }
    });
    const donut = box.querySelector(".donut");
    if (donut && stops.length) donut.style.background = `conic-gradient(${stops.join(",")})`;
  });
})();
