(function () {
  "use strict";

  var geoJsonCache = null;
  var selectedLayer = null;

  function readMapData() {
    var el = document.getElementById("authority-map-data");
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return null;
    }
  }

  function levelLabel(level) {
    var labels = {
      excellent: "Couverture élevée",
      bon: "Bonne couverture",
      moyen: "Couverture moyenne",
      faible: "Couverture faible",
      critique: "Zone à risque",
    };
    return labels[level] || "Couverture";
  }

  function formatInt(n) {
    return (n || 0).toLocaleString("fr-FR");
  }

  function normalizeShapeName(name) {
    return (name || "").replace(/\s+/g, " ").trim();
  }

  function regionBySlug(data, slug) {
    return (data.regions || []).find(function (r) {
      return r.slug === slug;
    });
  }

  function slugFromFeature(data, feature) {
    var shapeName = normalizeShapeName(feature.properties && feature.properties.shapeName);
    var found = (data.regions || []).find(function (r) {
      return normalizeShapeName(r.name) === shapeName;
    });
    return found ? found.slug : "";
  }

  function loadGeoJson(url) {
    if (geoJsonCache) return Promise.resolve(geoJsonCache);
    return fetch(url)
      .then(function (res) {
        return res.json();
      })
      .then(function (json) {
        geoJsonCache = json;
        return json;
      });
  }

  function drawSparkline(container, values) {
    if (!container || !values || !values.length) return;
    var w = 280;
    var h = 56;
    var pad = 4;
    var max = Math.max.apply(null, values.concat([1]));
    var pts = values
      .map(function (v, i) {
        var x = pad + (i / Math.max(values.length - 1, 1)) * (w - pad * 2);
        var y = h - pad - (v / max) * (h - pad * 2);
        return x + "," + y;
      })
      .join(" ");
    container.innerHTML =
      '<svg viewBox="0 0 ' +
      w +
      " " +
      h +
      '" class="auth-sparkline" preserveAspectRatio="none">' +
      '<polyline fill="none" stroke="#228545" stroke-width="2.5" points="' +
      pts +
      '"/>' +
      '<polyline fill="url(#sparkGrad)" stroke="none" points="' +
      pts +
      " " +
      (w - pad) +
      "," +
      (h - pad) +
      " " +
      pad +
      "," +
      (h - pad) +
      '"/>' +
      "<defs><linearGradient id='sparkGrad' x1='0' y1='0' x2='0' y2='1'>" +
      "<stop offset='0%' stop-color='#34d399' stop-opacity='0.35'/>" +
      "<stop offset='100%' stop-color='#34d399' stop-opacity='0'/>" +
      "</linearGradient></defs></svg>";
  }

  function updateDetail(region) {
    var panel = document.getElementById("auth-map-detail");
    if (!panel || !region) return;

    var nameEl = panel.querySelector("[data-field=name]");
    if (nameEl) nameEl.textContent = region.name;

    var status = panel.querySelector("[data-field=status]");
    if (status) {
      status.textContent = levelLabel(region.level);
      status.className = "auth-map-detail__status auth-map-detail__status--" + region.level;
    }

    var rateEl = panel.querySelector("[data-field=rate]");
    if (rateEl) rateEl.textContent = region.availability_rate + "%";

    var popEl = panel.querySelector("[data-field=population]");
    if (popEl) popEl.textContent = formatInt(region.population);

    var estEl = panel.querySelector("[data-field=establishments]");
    if (estEl) estEl.textContent = formatInt(region.establishments);

    var phEl = panel.querySelector("[data-field=pharmacies]");
    if (phEl) phEl.textContent = formatInt(region.pharmacies);

    var ordEl = panel.querySelector("[data-field=orders]");
    if (ordEl) ordEl.textContent = formatInt(region.orders_7d);

    var ruptEl = panel.querySelector("[data-field=ruptures]");
    if (ruptEl) ruptEl.textContent = formatInt(region.ruptures);

    var alertEl = panel.querySelector("[data-field=alerts]");
    if (alertEl) alertEl.textContent = formatInt(region.alerts);

    var ruptWrap = panel.querySelector("[data-field=ruptures-wrap]");
    var alertWrap = panel.querySelector("[data-field=alerts-wrap]");
    if (ruptWrap) ruptWrap.classList.toggle("is-danger", region.ruptures > 0);
    if (alertWrap) alertWrap.classList.toggle("is-warn", region.alerts > 0);

    drawSparkline(panel.querySelector("[data-field=sparkline]"), region.coverage_sparkline);

    document.querySelectorAll("[data-region-row]").forEach(function (row) {
      row.classList.toggle("is-active", row.getAttribute("data-region-row") === region.slug);
    });
  }

  function highlightLayer(layer, region) {
    if (selectedLayer && selectedLayer !== layer) {
      selectedLayer.setStyle({ weight: 1.2, opacity: 0.9 });
    }
    selectedLayer = layer;
    layer.setStyle({ weight: 3, opacity: 1, color: "#111827" });
    layer.bringToFront();
    updateDetail(region);
  }

  function initChoropleth(containerId, options) {
    options = options || {};
    var data = readMapData();
    if (!data || !data.regions || typeof L === "undefined") return null;

    var host = document.getElementById(containerId);
    if (!host) return null;

    var geoUrl = data.geojson_url || "/static/geo/gabon-provinces.geojson";
    var isMini = options.mini === true;

    var map = L.map(host, {
      scrollWheelZoom: !isMini,
      zoomControl: !isMini,
      attributionControl: false,
      dragging: !isMini,
      touchZoom: !isMini,
      doubleClickZoom: !isMini,
      boxZoom: !isMini,
      keyboard: !isMini,
    });

    var layersBySlug = {};
    var api = { map: map, layers: layersBySlug, data: data };

    loadGeoJson(geoUrl)
      .then(function (geojson) {
        L.geoJSON(geojson, {
          style: function (feature) {
            var slug = slugFromFeature(data, feature);
            var region = regionBySlug(data, slug);
            var fill = region ? region.fill_color : "#e5e7eb";
            var border = region ? region.border_color : "#d1d5db";
            return {
              fillColor: fill,
              weight: 1.2,
              opacity: 0.95,
              color: border,
              fillOpacity: 0.82,
            };
          },
          onEachFeature: function (feature, layer) {
            var slug = slugFromFeature(data, feature);
            var region = regionBySlug(data, slug);
            if (!region) return;

            layersBySlug[slug] = layer;

            var label =
              region.incidence_rate != null && data.map_mode === "trends"
                ? region.name + " — " + region.incidence_rate + " /100k"
                : region.name + " — " + region.availability_rate + "%";
            layer.bindTooltip(label, {
              permanent: !isMini,
              direction: "center",
              className: isMini ? "auth-map-tooltip auth-map-tooltip--mini" : "auth-map-tooltip",
            });

            if (!isMini) {
              layer.on({
                mouseover: function (e) {
                  e.target.setStyle({ weight: 2.5, fillOpacity: 0.92 });
                },
                mouseout: function (e) {
                  if (selectedLayer !== e.target) {
                    e.target.setStyle({ weight: 1.2, fillOpacity: 0.82 });
                  }
                },
                click: function () {
                  highlightLayer(layer, region);
                  if (typeof options.onSelect === "function") {
                    options.onSelect(region, layer);
                  }
                },
              });
            }
          },
        }).addTo(map);

        map.fitBounds(L.geoJSON(geojson).getBounds(), { padding: isMini ? [8, 8] : [20, 20] });

        if (data.markers && data.markers.length) {
          data.markers.forEach(function (m) {
            if (!m.lat || !m.lng) return;
            var level = m.level || "low";
            var colors = { high: "#dc2626", medium: "#ea580c", low: "#2563eb" };
            var bg = colors[level] || "#6b7280";
            var html =
              m.count > 1
                ? '<span class="auth-alert-pin__count">' + m.count + "</span>"
                : '<span class="material-symbols-rounded">warning</span>';
            var icon = L.divIcon({
              className: "auth-alert-pin auth-alert-pin--" + level,
              html: html,
              iconSize: [30, 30],
              iconAnchor: [15, 15],
            });
            L.marker([m.lat, m.lng], { icon: icon, zIndexOffset: 500 })
              .addTo(map)
              .bindTooltip(m.label || "", {
                direction: "top",
                className: "auth-map-tooltip auth-map-tooltip--mini",
              });
          });
        }

        if (!isMini && data.regions.length) {
          var first = data.regions[0];
          var firstLayer = layersBySlug[first.slug];
          if (firstLayer) highlightLayer(firstLayer, first);
        }

        setTimeout(function () {
          map.invalidateSize();
        }, 150);
      })
      .catch(function (err) {
        console.error("Carte Gabon:", err);
        host.innerHTML =
          '<p class="text-sm text-rose-600 p-4">Impossible de charger la carte du Gabon. Vérifiez votre connexion ou rechargez la page.</p>';
      });

    return api;
  }

  function initDeliveriesMap(containerId) {
    var data = readMapData();
    if (!data || typeof L === "undefined") return null;

    var host = document.getElementById(containerId);
    if (!host) return null;

    var center = data.center || { lat: 0.4162, lng: 9.4673 };
    var zoom = data.zoom || 11;

    var map = L.map(host, {
      scrollWheelZoom: false,
      zoomControl: true,
      attributionControl: false,
    }).setView([center.lat, center.lng], zoom);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap",
    }).addTo(map);

    var icons = {
      active: "moped",
      done: "check",
      pharmacy: "local_pharmacy",
      pending: "schedule",
      cancelled: "close",
    };

    (data.markers || []).forEach(function (m) {
      if (!m.lat || !m.lng) return;
      var status = m.status || "pending";
      var iconName = icons[status] || "local_shipping";
      var html =
        '<div class="auth-delivery-marker auth-delivery-marker--' +
        status +
        '"><span class="material-symbols-rounded" style="font-size:16px">' +
        iconName +
        "</span></div>";
      var icon = L.divIcon({
        className: "",
        html: html,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
      });
      L.marker([m.lat, m.lng], { icon: icon, zIndexOffset: status === "pharmacy" ? 100 : 400 })
        .addTo(map)
        .bindTooltip(m.label || "", {
          direction: "top",
          className: "auth-map-tooltip auth-map-tooltip--mini",
        });
    });

    setTimeout(function () {
      map.invalidateSize();
    }, 150);

    return { map: map, data: data };
  }

  document.addEventListener("DOMContentLoaded", function () {
    var fullHost = document.getElementById("auth-map-full");
    var api = null;

    if (fullHost) {
      api = initChoropleth("auth-map-full", {
        onSelect: function (region) {
          updateDetail(region);
        },
      });

      document.querySelectorAll("[data-region-row]").forEach(function (row) {
        row.addEventListener("click", function () {
          var slug = row.getAttribute("data-region-row");
          if (!api) return;
          var region = regionBySlug(api.data, slug);
          var layer = api.layers[slug];
          if (region && layer) {
            highlightLayer(layer, region);
            api.map.fitBounds(layer.getBounds(), { padding: [30, 30], maxZoom: 8 });
          }
        });
      });

      var fsBtn = document.getElementById("auth-map-fullscreen");
      if (fsBtn) {
        fsBtn.addEventListener("click", function () {
          var wrap = fullHost.closest(".auth-map-panel__map");
          if (wrap && wrap.requestFullscreen) wrap.requestFullscreen();
        });
      }
    }

    var miniHost = document.getElementById("auth-map-mini");
    if (miniHost) {
      initChoropleth("auth-map-mini", { mini: true });
    }

    var stocksHost = document.getElementById("auth-map-stocks");
    if (stocksHost) {
      initChoropleth("auth-map-stocks", { mini: true });
    }

    var alertsHost = document.getElementById("auth-map-alerts");
    if (alertsHost) {
      initChoropleth("auth-map-alerts", { mini: true });
    }

    var trendsHost = document.getElementById("auth-map-trends");
    if (trendsHost) {
      initChoropleth("auth-map-trends", { mini: true });
    }

    var patientsHost = document.getElementById("auth-map-patients");
    if (patientsHost) {
      initChoropleth("auth-map-patients", { mini: true });
    }

    var pharmaHost = document.getElementById("auth-map-pharmacies");
    if (pharmaHost) {
      initChoropleth("auth-map-pharmacies", { mini: true });
    }

    var deliveriesHost = document.getElementById("auth-map-deliveries");
    if (deliveriesHost) {
      initDeliveriesMap("auth-map-deliveries");
    }

    var disputesHost = document.getElementById("auth-map-disputes");
    if (disputesHost) {
      initChoropleth("auth-map-disputes", { mini: true });
    }

    var campaignsHost = document.getElementById("auth-map-campaigns");
    if (campaignsHost) {
      initChoropleth("auth-map-campaigns", { mini: true });
    }
  });
})();
