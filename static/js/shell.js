/* App shell shared by the user and admin sides: sidebar collapse, and links that open a collapsible form. */
(function () {
  const KEY = "reuse.sidebar";
  const sidebar = document.getElementById("sidebar");
  const toggle = document.getElementById("collapse");

  function setCollapsed(on) {
    if (!sidebar || !toggle) return;
    sidebar.classList.toggle("collapsed", on);
    toggle.textContent = on ? "»" : "«";
    toggle.setAttribute("aria-label", on ? "Expand sidebar" : "Collapse sidebar");
    toggle.setAttribute("aria-expanded", String(!on));
    toggle.title = on ? "Expand sidebar" : "Collapse sidebar";
  }

  if (sidebar && toggle) {
    let saved = false;
    try { saved = localStorage.getItem(KEY) === "collapsed"; } catch (e) { /* storage unavailable */ }
    setCollapsed(saved);
    toggle.addEventListener("click", () => {
      const on = !sidebar.classList.contains("collapsed");
      setCollapsed(on);
      try { localStorage.setItem(KEY, on ? "collapsed" : "open"); } catch (e) { /* storage unavailable */ }
    });
  }

  // <a href="#id" data-open-details> opens the <details id="id"> it points at (also on page load via the hash).
  function openDetails(id) {
    const el = id && document.getElementById(id);
    if (el && el.tagName === "DETAILS") {
      el.open = true;
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      const first = el.querySelector("input:not([type=hidden]), select, textarea");
      if (first) first.focus({ preventScroll: true });
    }
  }
  document.addEventListener("click", (e) => {
    const link = e.target.closest("[data-open-details]");
    if (!link) return;
    const id = (link.getAttribute("href") || "").split("#")[1];
    if (id && document.getElementById(id)) { e.preventDefault(); openDetails(id); history.replaceState(null, "", `#${id}`); }
  });
  if (location.hash) openDetails(location.hash.slice(1));
})();
