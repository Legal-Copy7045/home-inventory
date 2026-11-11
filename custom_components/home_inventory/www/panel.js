// Sidebar panel for Home Assistant Container/Core installs: frames the
// Home Inventory UI through the integration's authenticated proxy.
class HomeInventoryPanel extends HTMLElement {
  constructor() {
    super();
    this._root = this.attachShadow({ mode: "open" });
    this._root.innerHTML = `
      <style>
        :host { display: flex; flex-direction: column; height: 100%; background: var(--primary-background-color); }
        header { display: none; align-items: center; gap: 8px; height: 56px; padding: 0 12px;
                 background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); font: 500 20px/1 var(--paper-font-body1_-_font-family, sans-serif); }
        header button { background: none; border: 0; color: inherit; width: 40px; height: 40px; cursor: pointer; }
        :host([narrow]) header { display: flex; }
        iframe { flex: 1; border: 0; width: 100%; display: block; }
        .msg { padding: 24px; color: var(--primary-text-color); }
      </style>
      <header><button aria-label="Menu">&#9776;</button><span>Inventory</span></header>
      <div class="msg">Loading Home Inventory…</div>`;
    this._root.querySelector("button").addEventListener("click", () =>
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
  }
  set narrow(v) { this.toggleAttribute("narrow", !!v); }
  set hass(hass) {
    this._hass = hass;
    if (!this._started) { this._started = true; this._open(); }
  }
  async _open() {
    try {
      const r = await this._hass.callWS({ type: "home_inventory/panel_url" });
      const frame = document.createElement("iframe");
      frame.setAttribute("allow", "camera; clipboard-write; fullscreen");
      frame.src = r.url;
      this._root.querySelector(".msg").replaceWith(frame);
    } catch (e) {
      this._root.querySelector(".msg").textContent = "Home Inventory is not reachable: " + (e.message || e.code || e);
    }
  }
}
customElements.define("home-inventory-panel", HomeInventoryPanel);
