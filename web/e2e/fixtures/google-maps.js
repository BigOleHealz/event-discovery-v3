(() => {
  window.google = window.google || {};
  window.google.maps = window.google.maps || {};
  const ready = window.google.maps.__ib__;

  class FixtureLatLng {
    constructor(latitude, longitude) {
      if (typeof latitude === "object") {
        this.latitude = typeof latitude.lat === "function" ? latitude.lat() : latitude.lat;
        this.longitude = typeof latitude.lng === "function" ? latitude.lng() : latitude.lng;
      } else {
        this.latitude = latitude;
        this.longitude = longitude;
      }
    }

    lat() {
      return this.latitude;
    }

    lng() {
      return this.longitude;
    }
  }

  class FixtureLatLngBounds {
    constructor(southWest, northEast) {
      this.south = southWest?.lat() ?? 90;
      this.west = southWest?.lng() ?? 180;
      this.north = northEast?.lat() ?? -90;
      this.east = northEast?.lng() ?? -180;
    }

    extend(position) {
      this.south = Math.min(this.south, position.lat());
      this.west = Math.min(this.west, position.lng());
      this.north = Math.max(this.north, position.lat());
      this.east = Math.max(this.east, position.lng());
      return this;
    }

    contains(position) {
      return (
        position.lat() >= this.south &&
        position.lat() <= this.north &&
        position.lng() >= this.west &&
        position.lng() <= this.east
      );
    }

    getCenter() {
      return new FixtureLatLng((this.south + this.north) / 2, (this.west + this.east) / 2);
    }

    getNorthEast() {
      return new FixtureLatLng(this.north, this.east);
    }

    getSouthWest() {
      return new FixtureLatLng(this.south, this.west);
    }
  }

  const fixtureProjection = {
    fromLatLngToDivPixel: (position) => ({ x: position.lng() * 100, y: -position.lat() * 100 }),
    fromDivPixelToLatLng: (point) => new FixtureLatLng(-point.y / 100, point.x / 100),
  };

  function FixtureOverlayView() {}

  FixtureOverlayView.prototype.setMap = function setMap(map) {
    if (this.fixtureMap === map) {
      return;
    }
    if (this.fixtureMap && this.onRemove) {
      this.onRemove();
    }
    this.fixtureMap = map;
    if (map && this.onAdd) {
      this.onAdd();
    }
  };

  FixtureOverlayView.prototype.getMap = function getMap() {
    return this.fixtureMap ?? null;
  };

  FixtureOverlayView.prototype.getProjection = function getProjection() {
    return this.fixtureMap?.getProjection();
  };

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
      return new FixtureLatLngBounds(
        new FixtureLatLng(39.8, -75.3),
        new FixtureLatLng(40.15, -74.95),
      );
    }

    getMapCapabilities() {
      return { isAdvancedMarkersAvailable: true };
    }

    getProjection() {
      return fixtureProjection;
    }

    getZoom() {
      return this.options.zoom;
    }

    fitBounds() {
      this.options.zoom += 1;
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
      if (options.content) {
        this.append(options.content);
      }
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

  class FixtureLegacyMarker {}
  FixtureLegacyMarker.MAX_ZINDEX = 1000000;

  window.google.maps.Map = FixtureMap;
  window.google.maps.Marker = FixtureLegacyMarker;
  window.google.maps.OverlayView = FixtureOverlayView;
  window.google.maps.LatLng = FixtureLatLng;
  window.google.maps.LatLngBounds = FixtureLatLngBounds;
  window.google.maps.marker = { AdvancedMarkerElement, PinElement };
  window.google.maps.event = {
    removeListener: (listener) => listener.remove(),
    trigger: () => {},
  };

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
