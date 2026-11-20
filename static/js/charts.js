/* Draws the dashboard's line chart and donut (REUSE design) from values already rendered in the page. */
(function () {
  // Request statuses arrive in model order: pending, approved, rejected, canceled, picked up.
  const COLORS = ["#eae6e9", "#800e30", "#a99da3", "#5e5560", "#c98b9c"];
  const NS = "http://www.w3.org/2000/svg";

  // Line chart: <div data-linechart><ol class="chartdata"><li data-label data-value [data-forecast data-low data-high]>…</li></ol></div>
  // Actual weeks are a solid line with a soft area; forecast weeks continue as a dashed line
  // inside a shaded "likely range" band. Hovering a point shows its value.
  const path = (pts) => pts.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)} ${py.toFixed(1)}`).join("");
  const round = (v) => (Number.isInteger(v) ? String(v) : String(Math.round(v)));

  document.querySelectorAll("[data-linechart]").forEach((box) => {
    const points = [...box.querySelectorAll("li[data-value]")].map((li) => ({
      label: li.dataset.label,
      value: Number(li.dataset.value) || 0,
      forecast: li.hasAttribute("data-forecast"),
      low: Number(li.dataset.low) || 0,
      high: Number(li.dataset.high) || 0,
    }));
    const actual = points.filter((p) => !p.forecast);
    if (!actual.length || !actual.some((p) => p.value > 0)) return;

    const W = 500, H = 160, top = 14, bottom = 8;
    const max = Math.max(...points.map((p) => (p.forecast ? p.high : p.value)), 1) * 1.15;
    const x = (i) => (points.length === 1 ? W / 2 : (i / (points.length - 1)) * W);
    const y = (v) => H - bottom - (v / max) * (H - top - bottom);
    const lastActual = actual.length - 1;

    // A single week is drawn as a flat line across the chart.
    const actualPts = points.length === 1 ? [[0, y(actual[0].value)], [W, y(actual[0].value)]] : actual.map((p, i) => [x(i), y(p.value)]);
    const line = path(actualPts);
    let svgInner =
      `<path d="${line}L${actualPts[actualPts.length - 1][0].toFixed(1)} ${H}L${actualPts[0][0].toFixed(1)} ${H}Z" fill="#f0e7eb" stroke="none"/>` +
      `<path d="${line}" fill="none" stroke="#800e30" stroke-width="2.5" vector-effect="non-scaling-stroke"/>`;

    const fc = points.map((p, i) => ({ ...p, i })).filter((p) => p.forecast);
    if (fc.length) {
      const anchor = [x(lastActual), y(actual[lastActual].value)];
      const upper = [anchor, ...fc.map((p) => [x(p.i), y(p.high)])];
      const lower = [anchor, ...fc.map((p) => [x(p.i), y(p.low)])].reverse();
      svgInner +=
        `<path d="${path([...upper, ...lower])}Z" fill="#800e30" fill-opacity=".09" stroke="none"/>` +
        `<path d="${path([anchor, ...fc.map((p) => [x(p.i), y(p.value)])])}" fill="none" stroke="#800e30" stroke-width="2" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"/>`;
    }

    const chart = document.createElement("div");
    chart.className = "chart";
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label",
      `Pickups per week: ${actual.map((p) => `week of ${p.label} ${p.value}`).join(", ")}` +
      (fc.length ? `. Forecast: ${fc.map((p) => `week of ${p.label} about ${round(p.value)}, likely ${round(p.low)} to ${round(p.high)}`).join(", ")}` : ""));
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = svgInner;
    chart.appendChild(svg);

    // Points as HTML so they stay round when the SVG stretches; each shows its value on hover.
    points.forEach((p, i) => {
      const dot = document.createElement("span");
      dot.className = `chartdot${p.forecast ? " is-forecast" : ""}`;
      dot.style.left = `${(x(i) / W) * 100}%`;
      dot.style.top = `${(y(p.value) / H) * 100}%`;
      dot.dataset.tip = p.forecast
        ? `Week of ${p.label}: ~${round(p.value)} forecast (${round(p.low)}–${round(p.high)})`
        : `Week of ${p.label}: ${p.value} pickup${p.value === 1 ? "" : "s"}`;
      if (i > points.length / 2) dot.classList.add("tip-left");
      chart.appendChild(dot);
    });

    // Up to seven evenly spaced week labels; forecast weeks are styled differently.
    const axis = document.createElement("div");
    axis.className = "axis";
    const step = Math.max(1, Math.ceil(points.length / 7));
    points.forEach((p, i) => {
      if (i % step && i !== points.length - 1) return;
      const span = document.createElement("span");
      span.textContent = p.label;
      if (p.forecast) span.className = "is-forecast";
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
