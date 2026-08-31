(function () {
  var root = document.getElementById("order-live-track");
  if (!root) return;

  var apiUrl = root.getAttribute("data-api-url");
  var isTerminal = root.getAttribute("data-terminal") === "1";
  var autoOpen = root.getAttribute("data-auto-open") === "1";
  var toggle = document.getElementById("track-toggle");
  var panel = document.getElementById("track-panel");
  var hint = document.getElementById("track-status-hint");
  var timeline = document.getElementById("track-timeline");
  var mapWrap = document.getElementById("track-map-wrap");
  var updatedEl = document.getElementById("track-updated");
  var courierCard = document.getElementById("track-courier-card");
  var progressBar = document.getElementById("track-progress-bar");
  var statEta = document.getElementById("track-stat-eta");
  var statDist = document.getElementById("track-stat-dist");
  var statPhase = document.getElementById("track-stat-phase");
  var pollTimer = null;
  var map = null;
  var markers = {};
  var routeLine = null;
  var leafletLoaded = false;
  var lastStepHash = "";

  function loadLeaflet(cb) {
    if (window.L) {
      cb();
      return;
    }
    if (leafletLoaded) {
      var wait = setInterval(function () {
        if (window.L) {
          clearInterval(wait);
          cb();
        }
      }, 50);
      return;
    }
    leafletLoaded = true;
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

  function courierIcon() {
    return L.divIcon({
      className: "otl-courier-marker",
      html:
        '<div style="width:14px;height:14px;background:#6C3AED;border:3px solid #fff;border-radius:50%;box-shadow:0 2px 8px rgba(108,58,237,.5)"></div>',
      iconSize: [14, 14],
      iconAnchor: [7, 7],
    });
  }

  function stepDotHtml(state, index) {
    if (state === "done") {
      return (
        '<span class="track-step-dot h-7 w-7 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 bg-[#6C3AED] text-white">' +
        '<span class="material-symbols-rounded text-sm">check</span></span>'
      );
    }
    if (state === "active") {
      return (
        '<span class="track-step-dot h-7 w-7 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 bg-amber-500 text-white ring-4 ring-amber-100">' +
        index +
        "</span>"
      );
    }
    return (
      '<span class="track-step-dot h-7 w-7 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 bg-[#F3F4F6] text-[#9CA3AF]">' +
      index +
      "</span>"
    );
  }

  function renderTimeline(steps) {
    if (!timeline || !steps) return;
    var hash = JSON.stringify(steps);
    var changed = hash !== lastStepHash;
    lastStepHash = hash;
    timeline.innerHTML = steps
      .map(function (step, i) {
        var detail = step.detail
          ? '<p class="text-xs text-[#6B7280] mt-0.5 track-step-detail">' + step.detail + "</p>"
          : '<p class="text-xs text-[#6B7280] mt-0.5 track-step-detail hidden"></p>';
        return (
          '<li class="flex gap-3 items-start track-step' +
          (step.state === "active" ? " track-step--updated" : "") +
          '" data-step-id="' +
          step.id +
          '">' +
          stepDotHtml(step.state, i + 1) +
          '<div class="flex-1 min-w-0 pt-0.5"><p class="font-bold text-sm text-[#374151] track-step-label">' +
          step.label +
          "</p>" +
          detail +
          "</div></li>"
        );
      })
      .join("");
    if (changed) {
      timeline.querySelectorAll(".track-step--updated").forEach(function (el) {
        setTimeout(function () {
          el.classList.remove("track-step--updated");
        }, 500);
      });
    }
  }

  function setMarker(key, point, icon) {
    if (!map || !point || point.lat == null || point.lng == null) return;
    var latlng = [point.lat, point.lng];
    if (markers[key]) {
      markers[key].setLatLng(latlng);
    } else {
      markers[key] = L.marker(latlng, icon ? { icon: icon } : undefined)
        .addTo(map)
        .bindPopup(point.label || key);
    }
  }

  function updateRoute(mapData) {
    if (!map || !mapData) return;
    var pts = [];
    ["pharmacy", "courier", "destination"].forEach(function (k) {
      var p = mapData[k];
      if (p && p.lat != null) pts.push([p.lat, p.lng]);
    });
    if (pts.length < 2) {
      if (routeLine) {
        map.removeLayer(routeLine);
        routeLine = null;
      }
      return;
    }
    if (routeLine) {
      routeLine.setLatLngs(pts);
    } else {
      routeLine = L.polyline(pts, {
        color: "#6C3AED",
        weight: 4,
        opacity: 0.55,
        dashArray: "8 10",
      }).addTo(map);
    }
  }

  function updateMap(mapData) {
    if (!mapData) return;
    var courier = mapData.courier;
    var pharmacy = mapData.pharmacy;
    var dest = mapData.destination;
    var hasCourier = courier && courier.lat != null;
    var hasAny =
      hasCourier ||
      (pharmacy && pharmacy.lat != null) ||
      (dest && dest.lat != null);
    if (!hasAny) {
      if (mapWrap) mapWrap.classList.add("hidden");
      return;
    }
    if (mapWrap) mapWrap.classList.remove("hidden");
    loadLeaflet(function () {
      if (!map) {
        var center = hasCourier
          ? [courier.lat, courier.lng]
          : pharmacy && pharmacy.lat != null
            ? [pharmacy.lat, pharmacy.lng]
            : [dest.lat, dest.lng];
        map = L.map("track-map", { zoomControl: true }).setView(center, 14);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: "&copy; OSM",
        }).addTo(map);
      }
      if (pharmacy) setMarker("pharmacy", pharmacy);
      if (dest) setMarker("dest", dest);
      if (courier) setMarker("courier", courier, courierIcon());
      updateRoute(mapData);
      var bounds = [];
      ["pharmacy", "dest", "courier"].forEach(function (k) {
        var p = mapData[k];
        if (p && p.lat != null) bounds.push([p.lat, p.lng]);
      });
      if (bounds.length > 1) {
        map.fitBounds(bounds, { padding: [28, 28], maxZoom: 15 });
      } else if (bounds.length === 1) {
        map.setView(bounds[0], 14);
      }
      setTimeout(function () {
        map.invalidateSize();
      }, 120);
    });
  }

  function phaseLabel(phase) {
    var labels = {
      preparing: "Préparation",
      pickup: "Retrait pharma",
      transit: "En route",
      delivered: "Livré",
    };
    return labels[phase] || "Suivi";
  }

  function renderCourierCard(courier, metrics) {
    if (!courierCard) return;
    if (!courier || !courier.name) {
      courierCard.classList.add("hidden");
      return;
    }
    courierCard.classList.remove("hidden");
    var avatar = courierCard.querySelector(".otl-courier-avatar");
    var nameEl = courierCard.querySelector("[data-courier-name]");
    var metaEl = courierCard.querySelector("[data-courier-meta]");
    var statusEl = courierCard.querySelector("[data-courier-status]");
    var phoneLink = courierCard.querySelector("[data-courier-phone]");
    if (avatar) {
      avatar.textContent = courier.initials || "?";
      avatar.classList.toggle(
        "otl-courier-avatar--transit",
        metrics && metrics.phase === "transit"
      );
    }
    if (nameEl) nameEl.textContent = courier.name;
    if (statusEl) statusEl.textContent = courier.status_label || "";
    if (metaEl) {
      var parts = [];
      if (courier.rating) parts.push("★ " + Number(courier.rating).toFixed(1));
      if (courier.total_deliveries)
        parts.push(courier.total_deliveries + " courses");
      if (courier.vehicle_type) parts.push(courier.vehicle_type);
      metaEl.textContent = parts.join(" · ");
    }
    if (phoneLink) {
      if (courier.phone) {
        phoneLink.href = "tel:" + courier.phone;
        phoneLink.classList.remove("hidden");
      } else {
        phoneLink.classList.add("hidden");
      }
    }
  }

  function renderMetrics(metrics) {
    if (!metrics) return;
    if (progressBar) {
      progressBar.style.width = (metrics.progress_percent || 0) + "%";
    }
    if (statEta) {
      statEta.textContent =
        metrics.eta_minutes != null ? "~" + metrics.eta_minutes + " min" : "—";
    }
    if (statDist) {
      statDist.textContent = metrics.distance_remaining_label || "—";
    }
    if (statPhase) {
      statPhase.textContent = phaseLabel(metrics.phase);
    }
  }

  function applyPayload(data) {
    if (!data || !data.ok) return;
    if (hint) hint.textContent = data.current_hint || data.status_label || "";
    renderTimeline(data.timeline);
    renderCourierCard(data.courier, data.metrics);
    renderMetrics(data.metrics);
    updateMap(data.map);
    isTerminal = !!data.is_terminal;
    root.setAttribute("data-terminal", isTerminal ? "1" : "0");
    if (updatedEl && data.updated_at) {
      try {
        var d = new Date(data.updated_at);
        updatedEl.textContent =
          "Dernière mise à jour : " +
          d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
      } catch (e) {}
    }
    if (isTerminal && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function refresh() {
    if (!apiUrl) return;
    fetch(apiUrl, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        return r.json();
      })
      .then(applyPayload)
      .catch(function () {});
  }

  function startPolling() {
    if (pollTimer || isTerminal) return;
    refresh();
    pollTimer = setInterval(refresh, 10000);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function openPanel() {
    if (!panel || !toggle) return;
    panel.classList.remove("hidden");
    toggle.setAttribute("aria-expanded", "true");
    startPolling();
    setTimeout(function () {
      if (map) map.invalidateSize();
    }, 200);
  }

  if (toggle && panel) {
    toggle.addEventListener("click", function () {
      var willOpen = panel.classList.contains("hidden");
      panel.classList.toggle("hidden");
      toggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
      if (willOpen) startPolling();
      else stopPolling();
    });
  }

  if (autoOpen && !isTerminal) {
    openPanel();
  }
})();
