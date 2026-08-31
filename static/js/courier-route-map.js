(function () {
  var maps = {};
  var LIBREVILLE = [0.4162, 9.4673];

  function loadLeaflet(cb) {
    if (window.L) {
      cb();
      return;
    }
    if (!document.querySelector('link[href*="leaflet"]')) {
      var link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
      link.crossOrigin = "";
      document.head.appendChild(link);
    }
    var script = document.createElement("script");
    script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    script.crossOrigin = "";
    script.onload = cb;
    document.body.appendChild(script);
  }

  function readTracking(scriptId) {
    var el = document.getElementById(scriptId);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return null;
    }
  }

  function markerIcon(color, glyph) {
    return L.divIcon({
      className: "",
      html:
        '<div style="width:28px;height:28px;border-radius:50%;background:' +
        color +
        ';border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.2);display:flex;align-items:center;justify-content:center;color:#fff;font-size:14px;">' +
        (glyph || "") +
        "</div>",
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });
  }

  function courierPulseIcon() {
    return L.divIcon({
      className: "cm-courier-pulse-marker",
      html:
        '<div style="width:16px;height:16px;background:#6C3AED;border:3px solid #fff;border-radius:50%;box-shadow:0 0 0 6px rgba(108,58,237,.25)"></div>',
      iconSize: [16, 16],
      iconAnchor: [8, 8],
    });
  }

  function initMap(el) {
    var mapId = el.getAttribute("data-courier-map");
    var scriptId = el.getAttribute("data-tracking-script");
    var data = readTracking(scriptId) || {};
    var mapData = data.map || {};
    var center = LIBREVILLE;
    var points = [];

    loadLeaflet(function () {
      if (maps[mapId]) return;
      var map = L.map(el, { zoomControl: false }).setView(center, 13);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap",
      }).addTo(map);
      maps[mapId] = { map: map, markers: {}, route: null };

      function addPoint(key, point, icon) {
        if (!point || point.lat == null || point.lng == null) return;
        var latlng = [point.lat, point.lng];
        points.push(latlng);
        maps[mapId].markers[key] = L.marker(latlng, { icon: icon })
          .addTo(map)
          .bindPopup(point.label || key);
      }

      addPoint(
        "pharmacy",
        mapData.pharmacy,
        markerIcon("#16A34A", "+")
      );
      addPoint(
        "destination",
        mapData.destination,
        markerIcon("#EF4444", "⌂")
      );
      if (mapData.courier) {
        addPoint("courier", mapData.courier, courierPulseIcon());
      }

      if (points.length >= 2) {
        maps[mapId].route = L.polyline(points, {
          color: "#2563EB",
          weight: 5,
          opacity: 0.85,
        }).addTo(map);
        map.fitBounds(L.latLngBounds(points), { padding: [36, 36] });
      } else if (points.length === 1) {
        map.setView(points[0], 14);
      }

      setTimeout(function () {
        map.invalidateSize();
      }, 200);
    });
  }

  document.querySelectorAll("[data-courier-map]").forEach(initMap);

  document.querySelectorAll(".courier-map-center").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var mapId = btn.getAttribute("data-map-id");
      var entry = maps[mapId];
      if (!entry) return;
      var bounds = [];
      Object.keys(entry.markers).forEach(function (key) {
        bounds.push(entry.markers[key].getLatLng());
      });
      if (bounds.length) {
        entry.map.fitBounds(L.latLngBounds(bounds), { padding: [36, 36] });
      }
    });
  });
})();
