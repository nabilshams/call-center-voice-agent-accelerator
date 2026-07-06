(function () {
  function initials(name) {
    const parts = String(name || "Guest")
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    if (parts.length === 0) return "G";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  function setIdentity(el, user) {
    const authenticated = Boolean(user && user.authenticated);
    const name = (user && user.display_name) || "Guest";
    const avatar = el.querySelector(".user-avatar");
    const label = el.querySelector(".user-kicker");
    const value = el.querySelector(".user-name");
    el.classList.toggle("guest", !authenticated);
    el.title = authenticated && user.email ? user.email : name;
    if (avatar) avatar.textContent = initials(name);
    if (label) label.textContent = authenticated ? "Signed in" : "Session";
    if (value) value.textContent = name;
  }

  async function hydrateIdentity() {
    const widgets = document.querySelectorAll("[data-user-identity]");
    if (widgets.length === 0) return;
    try {
      const response = await fetch("/api/me", { cache: "no-store" });
      if (!response.ok) throw new Error(`identity ${response.status}`);
      const user = await response.json();
      widgets.forEach((widget) => setIdentity(widget, user));
    } catch (err) {
      console.warn("Could not load user identity", err);
      widgets.forEach((widget) => setIdentity(widget, {
        authenticated: false,
        display_name: "Guest",
      }));
    }
  }

  window.WanderluxIdentity = { hydrate: hydrateIdentity };
  document.addEventListener("DOMContentLoaded", hydrateIdentity);
})();