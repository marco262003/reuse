/* The camera side of the face scan: open the camera, show the head-turn prompts, collect frames. */
(function () {
  const R = window.REUSE;
  const PROMPTS = {
    center: "Look straight at the camera",
    left: "Slowly turn your head to your left",
    right: "Slowly turn your head to your right",
  };
  const FRAME_WIDTH = 480;
  const LEAD_IN_MS = 900;   // time to read the prompt and start moving
  const FRAME_GAP_MS = 300;
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let stream = null;

  function cameraErrorText(err) {
    if (!navigator.mediaDevices) return "This browser can't open the camera here. Use a current browser over HTTPS.";
    if (err && err.name === "NotAllowedError") return "Camera access was blocked. Allow the camera in your browser settings, then try again.";
    if (err && (err.name === "NotFoundError" || err.name === "OverconstrainedError")) return "We couldn't find a camera on this device.";
    if (err && err.name === "NotReadableError") return "Your camera is being used by another app. Close it and try again.";
    return "We couldn't open your camera. Please try again.";
  }

  async function open(video) {
    if (!navigator.mediaDevices) throw new Error("no camera support");
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 } }, audio: false });
    video.srcObject = stream;
    video.hidden = false;
    for (let i = 0; i < 50 && !video.videoWidth; i++) await wait(100); // until the first picture arrives
    if (!video.videoWidth) throw new Error("camera gave no picture");
  }

  function close(video) {
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
    video.srcObject = null;
    video.hidden = true;
  }

  // One camera picture as a JPEG. The preview is mirrored on screen; the picture itself is not flipped.
  function grab(video, canvas) {
    const scale = Math.min(1, FRAME_WIDTH / video.videoWidth);
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.8);
  }

  // Walk through the challenge, showing each prompt and collecting a few pictures for it.
  async function capture(video, canvas, challenge, onPrompt) {
    const frames = [];
    for (let step = 0; step < challenge.steps.length; step++) {
      onPrompt(PROMPTS[challenge.steps[step]] || "", step, challenge.steps.length);
      await wait(LEAD_IN_MS);
      for (let i = 0; i < challenge.frames_per_step; i++) {
        frames.push({ step, image: grab(video, canvas) });
        await wait(FRAME_GAP_MS);
      }
    }
    return frames;
  }

  R.faceScan = { open, close, capture, cameraErrorText };
})();
