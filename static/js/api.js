/* Small fetch wrapper: JSON in/out, CSRF header, friendly network errors. */
(function () {
  const R = (window.REUSE = window.REUSE || {});

  function getCookie(name) {
    const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[1]) : null;
  }

  async function request(method, url, body) {
    const options = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    if (method !== "GET") options.headers["X-CSRFToken"] = getCookie("csrftoken") || "";

    let response;
    try {
      response = await fetch(url, options);
    } catch (err) {
      return { ok: false, status: 0, errors: {}, message: "We couldn't reach the server. Check your connection and try again." };
    }

    let data = {};
    try { data = await response.json(); } catch (err) { /* empty or non-JSON body */ }

    // Signed-in pages: an expired session sends the person back to the login page.
    if (response.status === 401 && R.requireAuth) {
      window.location.href = "/login/";
    }
    return Object.assign({ errors: {} }, data, {
      ok: response.ok && data.ok !== false,
      status: response.status,
    });
  }

  R.api = {
    get: (url) => request("GET", url),
    post: (url, body) => request("POST", url, body || {}),
    patch: (url, body) => request("PATCH", url, body || {}),
  };
})();
