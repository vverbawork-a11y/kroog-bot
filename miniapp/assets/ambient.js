"use strict";
function startBackground() {
  const backdrop = document.getElementById("ambientBg");
  const video = document.getElementById("ambientVideo");
  if (!backdrop || !video || backdrop.dataset.started) return;
  backdrop.dataset.started = "true";
  const motion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const fallback = () => backdrop.classList.remove("is-playing");
  let pending = false;
  const play = () => {
    if (motion?.matches || navigator.connection?.saveData || document.hidden || pending) return;
    video.muted = true;
    video.defaultMuted = true;
    video.playsInline = true;
    if (!video.src) video.src = video.dataset.src;
    pending = true;
    try {
      Promise.resolve(video.play()).catch(fallback).finally(() => { pending = false; });
    } catch (_) { pending = false; fallback(); }
  };
  video.addEventListener("playing", () => {
    if (!video.paused && !motion?.matches && !document.hidden) backdrop.classList.add("is-playing");
  });
  for (const event of ["pause", "ended", "error", "waiting", "stalled"]) video.addEventListener(event, fallback);
  // Retry in a real gesture, and when returning to the Telegram mini app.
  document.addEventListener("pointerdown", () => { if (video.paused) play(); }, {passive:true});
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { video.pause(); fallback(); } else play();
  });
  window.addEventListener("pageshow", play);
  motion?.addEventListener?.("change", () => {
    if (motion.matches) { video.pause(); fallback(); } else play();
  });
  play();
}
