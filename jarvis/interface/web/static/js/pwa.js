class JarvisPWA {
  constructor() {
    this.deferredPrompt = null;
    this.isInstalled = false;
  }

  init() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker
        .register("/sw.js")
        .then((reg) => {
          console.log("[JARVIS PWA] Service worker registered:", reg.scope);
          this.requestNotificationPermission();
        })
        .catch((err) => console.warn("[JARVIS PWA] SW registration failed:", err));
    }

    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      this.deferredPrompt = e;
      this.showInstallButton();
    });

    if (window.matchMedia("(display-mode: standalone)").matches) {
      this.isInstalled = true;
      document.body.classList.add("pwa-installed");
    }

    const params = new URLSearchParams(window.location.search);
    if (params.get("action") === "new_chat") window.jarvisUI?.clearHistory();
    if (params.get("action") === "screenshot") window.jarvisUI?.sendMessage("take a screenshot");
  }

  showInstallButton() {
    const header = document.querySelector(".hud-header");
    if (!header || document.getElementById("pwa-install-btn")) return;
    const btn = document.createElement("button");
    btn.id = "pwa-install-btn";
    btn.textContent = "Install";
    btn.style.cssText =
      "background:var(--cyan);color:#000;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px;font-family:inherit";
    btn.onclick = () => this.installApp();
    header.appendChild(btn);
  }

  async installApp() {
    if (!this.deferredPrompt) return;
    this.deferredPrompt.prompt();
    const result = await this.deferredPrompt.userChoice;
    console.log("[JARVIS PWA] Install result:", result.outcome);
    this.deferredPrompt = null;
    document.getElementById("pwa-install-btn")?.remove();
  }

  async requestNotificationPermission() {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default") {
      const permission = await Notification.requestPermission();
      console.log("[JARVIS PWA] Notification permission:", permission);
    }
  }

  showLocalNotification(title, body) {
    if (Notification.permission === "granted") {
      new Notification(title, {
        body,
        icon: "/static/icons/icon-192.png",
        badge: "/static/icons/icon-192.png",
      });
    }
  }
}

window.jarvisPWA = new JarvisPWA();
document.addEventListener("DOMContentLoaded", () => window.jarvisPWA.init());
