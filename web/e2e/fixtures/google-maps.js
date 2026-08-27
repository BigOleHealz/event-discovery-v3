(() => {
  window.google = window.google || {};
  window.google.maps = window.google.maps || {};
  const ready = window.google.maps.__ib__;

  class FixtureMap {
    constructor(element, options) {
      this.element = element;
      this.options = options;
      element.dataset.googleMapReady = "true";
    }
  }

  class FixtureAdvancedMarkerElement extends HTMLElement {
    constructor(options = {}) {
      super();
      this._map = null;
      this.position = options.position;
      this.title = options.title || "";
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
