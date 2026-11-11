/* Sends a school ID file straight to storage, then asks the server to read it. */
(function () {
  const R = window.REUSE;
  const limits = JSON.parse(document.getElementById("upload-limits").textContent);

  function fileProblem(file) {
    if (!file) return "Choose a photo of your school ID.";
    if (!limits.types.includes(file.type)) return "Upload a JPG, PNG or PDF.";
    if (!file.size) return "That file is empty.";
    if (file.size > limits.max_bytes) return `File must be ${limits.megabytes} MB or smaller.`;
    return "";
  }

  async function sendSide(side, file) {
    const start = await R.api.post("/api/registration/upload-id/", { side, content_type: file.type, size: file.size });
    if (!start.ok) return start;
    const form = new FormData();
    Object.entries(start.upload.fields).forEach(([key, value]) => form.append(key, value));
    form.append("file", file); // storage expects the file last
    try {
      // "no-cors" lets the upload work without any CORS set-up on the bucket.
      // The browser can't see the result, so the server checks that the file arrived.
      await fetch(start.upload.url, { method: "POST", body: form, mode: "no-cors" });
    } catch (err) {
      return { ok: false, errors: {}, message: "We couldn't upload the file. Check your connection and try again." };
    }
    return { ok: true };
  }

  R.idUpload = {
    fileProblem,
    // Upload the front, and the back if there is one, then return the server's reading of the ID.
    async send(front, back) {
      for (const [side, file] of [["front", front], ["back", back]]) {
        if (!file) continue;
        const sent = await sendSide(side, file);
        if (!sent.ok) return sent;
      }
      return R.api.post("/api/registration/extract-id/", {});
    },
  };
})();
