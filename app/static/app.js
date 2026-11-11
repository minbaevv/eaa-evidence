const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;

/* ---------- Motion engine (degrades silently, no-JS / reduced-motion safe) ---------- */

const animatable = !reducedMotion && "IntersectionObserver" in window;

if (animatable) {
  document.documentElement.classList.add("animations-on");
}

const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

function countUp(el) {
  const target = Number(el.dataset.count) || 0;
  if (reducedMotion) {
    return;
  }
  const duration = 1100;
  const start = performance.now();
  const step = (now) => {
    const p = Math.min((now - start) / duration, 1);
    el.textContent = String(Math.round(target * easeOutCubic(p)));
    if (p < 1) {
      requestAnimationFrame(step);
    }
  };
  requestAnimationFrame(step);
}

function animateRing(wrapper) {
  if (reducedMotion) {
    return;
  }
  const ring = wrapper.querySelector(".ring-value");
  if (!ring) {
    return;
  }
  const score = Math.max(0, Math.min(100, Number(wrapper.dataset.score) || 0));
  const circumference = 326.73;
  const duration = 1400;
  const start = performance.now();
  const step = (now) => {
    const p = Math.min((now - start) / duration, 1);
    ring.style.strokeDashoffset = String(circumference * (1 - (score / 100) * easeOutCubic(p)));
    if (p < 1) {
      requestAnimationFrame(step);
    }
  };
  requestAnimationFrame(step);
}

function initReveal() {
  if (!animatable) {
    return;
  }

  // Stagger delays for siblings sharing a parent.
  document.querySelectorAll("[data-reveal]").forEach((el) => {
    const parent = el.parentElement;
    if (!parent) {
      return;
    }
    const siblings = [...parent.querySelectorAll(":scope > [data-reveal]")];
    const index = siblings.indexOf(el);
    if (index > 0) {
      el.style.setProperty("--reveal-delay", `${Math.min(index * 0.08, 0.48)}s`);
    }
  });

  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) {
          continue;
        }
        const el = entry.target;
        observer.unobserve(el);
        el.classList.add("revealed");
        el.querySelectorAll("[data-count]").forEach(countUp);
        if (el.hasAttribute("data-score")) {
          animateRing(el);
        }
      }
    },
    { threshold: 0.12, rootMargin: "0px 0px -6% 0px" },
  );

  document.querySelectorAll("[data-reveal]").forEach((el) => observer.observe(el));
}

/* ---------- 3D tilt + pointer glare ---------- */
function initTilt() {
  if (reducedMotion || !finePointer) {
    return;
  }
  const maxTilt = 7;
  document.querySelectorAll("[data-tilt]").forEach((el) => {
    el.addEventListener("pointermove", (event) => {
      const rect = el.getBoundingClientRect();
      const px = (event.clientX - rect.left) / rect.width;
      const py = (event.clientY - rect.top) / rect.height;
      el.classList.add("is-tilting");
      el.style.setProperty("--rx", `${((py - 0.5) * -2 * maxTilt).toFixed(2)}deg`);
      el.style.setProperty("--ry", `${((px - 0.5) * 2 * maxTilt).toFixed(2)}deg`);
      el.style.setProperty("--mx", `${(px * 100).toFixed(1)}%`);
      el.style.setProperty("--my", `${(py * 100).toFixed(1)}%`);
    });
    el.addEventListener("pointerleave", () => {
      el.classList.remove("is-tilting");
      el.style.setProperty("--rx", "0deg");
      el.style.setProperty("--ry", "0deg");
    });
  });
}

/* ---------- Header progress + hero parallax ---------- */

function initScrollChrome() {
  const header = document.querySelector(".site-header");
  const hero = document.querySelector(".hero");
  const bar = document.getElementById("scroll-progress");
  let ticking = false;

  const update = () => {
    ticking = false;
    const doc = document.documentElement;
    const max = doc.scrollHeight - doc.clientHeight;
    const ratio = max > 0 ? doc.scrollTop / max : 0;
    if (bar) {
      bar.style.width = `${(ratio * 100).toFixed(1)}%`;
    }
    if (header) {
      header.classList.toggle("scrolled", doc.scrollTop > 12);
    }
    if (hero && !reducedMotion) {
      hero.style.transform = `translate3d(0, ${(doc.scrollTop * 0.08).toFixed(1)}px, 0)`;
    }
  };

  window.addEventListener(
    "scroll",
    () => {
      if (!ticking) {
        ticking = true;
        requestAnimationFrame(update);
      }
    },
    { passive: true },
  );
  update();
}

/* ---------- Existing behavior: print + scan polling ---------- */

document.addEventListener("DOMContentLoaded", () => {
  initReveal();
  initTilt();
  initScrollChrome();

  const printButton = document.getElementById("print-statement");
  if (printButton) {
    printButton.addEventListener("click", () => window.print());
  }

  const root = document.querySelector("[data-scan-status]");
  if (!root) {
    return;
  }

  const scanId = root.dataset.scanId;
  const statusUrl = root.dataset.statusUrl;
  const msg = root.dataset;
  const statusEl = document.getElementById("scan-status");
  const messageEl = document.getElementById("scan-message");
  const errorEl = document.getElementById("scan-error");
  let stopped = false;

  const showError = (message) => {
    if (!errorEl) {
      return;
    }
    errorEl.hidden = false;
    errorEl.textContent = message;
  };

  const poll = async () => {
    if (stopped) {
      return;
    }

    try {
      const response = await fetch(statusUrl, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(
          (msg.msgStatusFailed || "Status request failed ({code}).").replace(
            "{code}",
            String(response.status),
          ),
        );
      }

      const data = await response.json();

      if (data.status === "done") {
        stopped = true;
        if (statusEl) {
          statusEl.textContent = msg.msgComplete || "Scan complete. Opening the report…";
        }
        window.location.assign(`/scans/${scanId}/report`);
        return;
      }

      if (data.status === "failed") {
        stopped = true;
        if (statusEl) {
          statusEl.textContent = msg.msgFailed || "Scan failed.";
        }
        showError(data.error || msg.msgFailedDefault || "The scan could not be completed.");
        return;
      }

      if (statusEl && data.status === "running") {
        statusEl.textContent = msg.msgRunning || "Scanning pages and running axe-core…";
      }
      if (messageEl) {
        messageEl.textContent = msg.msgKeep || "This usually takes 10–40 seconds. Keep this tab open.";
      }

      window.setTimeout(poll, 2000);
    } catch (error) {
      showError(error.message || msg.msgStatusError || "Could not check scan status.");
      window.setTimeout(poll, 4000);
    }
  };

  poll();
});
