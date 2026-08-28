(() => {
  window.google = window.google || {};
  window.google.maps = window.google.maps || {};
  const ready = window.google.maps.__ib__;

  class FixtureMap {
    constructor(element, options) {
      this.element = element;
      this.options = options;
      this.listeners = new Map();
      element.dataset.googleMapReady = "true";
    }

    addListener(eventName, handler) {
      this.listeners.set(eventName, handler);
      if (eventName === "idle") {
        queueMicrotask(handler);
      }
      return {
        remove: () => this.listeners.delete(eventName),
      };
    }

    getBounds() {
      return {
        getNorthEast: () => ({ lat: () => 40.15, lng: () => -74.95 }),
        getSouthWest: () => ({ lat: () => 39.8, lng: () => -75.3 }),
      };
    }
  }

  class FixtureAdvancedMarkerElement extends HTMLElement {
    constructor(options = {}) {
      super();
      this._map = null;
      this.position = options.position;
      this.title = options.title || "";
      this.style.display = "block";
      this.style.width = "2rem";
      this.style.height = "2.5rem";
      this.map = options.map || null;
    }

    get map() {
      return this._map;
    }

    set map(value) {
      this.remove();
      this._map = value;
      if (value && value.element) {
        value.element.append(this);
      }
    }

    addListener(eventName, handler) {
      this.addEventListener(eventName, handler);
      return {
        remove: () => this.removeEventListener(eventName, handler),
      };
    }
  }

  class FixturePinElement extends HTMLElement {}

  if (!customElements.get("gmp-advanced-marker")) {
    customElements.define("gmp-advanced-marker", FixtureAdvancedMarkerElement);
  }
  if (!customElements.get("gmp-pin")) {
    customElements.define("gmp-pin", FixturePinElement);
  }

  const AdvancedMarkerElement = customElements.get("gmp-advanced-marker");
  const PinElement = customElements.get("gmp-pin");

  window.google.maps.importLibrary = async (name) => {
    if (name === "maps") {
      return { Map: FixtureMap };
    }
    if (name === "marker") {
      return { AdvancedMarkerElement, PinElement };
    }
    throw new Error(`Unexpected fixture library: ${name}`);
  };

  ready();
})();
