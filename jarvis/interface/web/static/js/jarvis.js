/**
 * J.A.R.V.I.S web UI — Socket.IO client and HUD logic.
 * Console logs prefixed with [JARVIS] for debugging.
 */

(function () {
  "use strict";

  function log() {
    var args = Array.prototype.slice.call(arguments);
    args.unshift("[JARVIS]");
    console.log.apply(console, args);
  }

  function esc(s) {
    if (!s) return "";
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function playClickSound() {
    try {
      var ctx = new (window.AudioContext || window.webkitAudioContext)();
      var o = ctx.createOscillator();
      var g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = 880;
      g.gain.value = 0.04;
      o.connect(g);
      g.connect(ctx.destination);
      o.start();
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.08);
      o.stop(ctx.currentTime + 0.09);
      ctx.resume();
    } catch (e) {
      log("click sound skipped:", e);
    }
  }

  function JarvisUI(opts) {
    this.voiceConfigEnabled = !!(opts && opts.voiceConfigEnabled);
    this.smarthomeEnabled = !!(opts && opts.smarthomeEnabled);
    this.smarthomeHaReady = !!(opts && opts.smarthomeHaReady);
    this.smarthomeRegistry = null;
    this.smarthomeRoom = null;
    this.sessionId = "s-" + Math.random().toString(36).slice(2, 12);
    this.socket = null;
    this.typingEl = null;
    this.uptimeLocalStart = Date.now();
    this.recentCmds = [];
    this.maxRecent = 6;
  }

  JarvisUI.prototype.init = function () {
    var self = this;
    log("init UI, session", this.sessionId);

    this.bindDom();
    this.startClock();
    this.startUptimeCounter();

    // Polling only: WSGI servers (e.g. Waitress) do not support WebSocket upgrade.
    this.socket = io(window.location.origin, {
      transports: ["polling"],
      upgrade: false,
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionAttempts: Infinity,
    });

    this.socket.on("connect", function () {
      log("socket connected");
      self.setConnected(true);
      self.socket.emit("get_history");
      self.socket.emit("system_stats");
    });

    this.socket.on("disconnect", function () {
      log("socket disconnected");
      self.setConnected(false);
      self.showReconnectBanner(true);
    });

    this.socket.on("connect_error", function (err) {
      log("connect_error", err);
      self.showReconnectBanner(true);
    });

    this.socket.on("reconnect", function () {
      log("reconnected");
      self.setConnected(true);
      self.showReconnectBanner(false);
      self.socket.emit("get_history");
    });

    this.socket.on("jarvis_status", function (data) {
      log("jarvis_status", data);
      self.applyJarvisStatus(data);
    });

    this.socket.on("jarvis_response", function (data) {
      self.receiveMessage(data || {});
    });

    this.socket.on("conversation_history", function (data) {
      self.applyHistory((data && data.history) || []);
    });

    this.socket.on("system_stats", function (data) {
      self.updateStats(data || {});
    });

    this.socket.on("memory_cleared", function (data) {
      log("memory_cleared", data);
      if (data && data.ok) {
        var box = document.getElementById("messages");
        if (box) box.innerHTML = "";
      }
    });

    this.socket.on("voice_toggle_ack", function (data) {
      log("voice_toggle_ack", data);
      self.applyVoiceAck(data || {});
    });

    this.socket.on("device_update", function (data) {
      self.applyDeviceUpdate(data || {});
    });
  };

  JarvisUI.prototype.bindDom = function () {
    var self = this;
    var input = document.getElementById("msg-input");
    var send = document.getElementById("btn-send");
    var mic = document.getElementById("btn-mic");
    var counter = document.getElementById("char-count");

    if (send) {
      send.addEventListener("click", function () {
        self.sendFromInput();
      });
    }
    if (input) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          self.sendFromInput();
        }
      });
      input.addEventListener("input", function () {
        if (counter) counter.textContent = String((input.value || "").length);
      });
    }

    if (mic) {
      mic.addEventListener("click", function () {
        self.toggleVoice();
      });
    }

    document.getElementById("btn-search") &&
      document.getElementById("btn-search").addEventListener("click", function () {
        var q = (document.getElementById("search-input") || {}).value || "";
        self.runSearch(q.trim());
      });

    document.getElementById("btn-sysinfo") &&
      document.getElementById("btn-sysinfo").addEventListener("click", function () {
        if (self.socket) self.socket.emit("system_stats");
      });

    document.getElementById("btn-clear") &&
      document.getElementById("btn-clear").addEventListener("click", function () {
        self.clearHistory();
      });

    document.getElementById("btn-list-files") &&
      document.getElementById("btn-list-files").addEventListener("click", function () {
        var p = (document.getElementById("path-input") || {}).value || ".";
        self.listFiles(p);
      });

    document.getElementById("btn-run-cmd") &&
      document.getElementById("btn-run-cmd").addEventListener("click", function () {
        var c = (document.getElementById("cmd-input") || {}).value || "";
        self.runCommand(c.trim());
      });

    var sw = document.getElementById("voice-switch");
    if (sw) {
      sw.addEventListener("click", function () {
        var on = !sw.classList.contains("on");
        self.toggleVoiceServer(on);
      });
    }
    this.updateMicButtonLabel();

    document.getElementById("toggle-left") &&
      document.getElementById("toggle-left").addEventListener("click", function () {
        document.getElementById("panel-left").classList.toggle("open");
      });
    document.getElementById("toggle-right") &&
      document.getElementById("toggle-right").addEventListener("click", function () {
        document.getElementById("panel-right").classList.toggle("open");
      });

    var btnHm = document.getElementById("toggle-smarthome");
    var panHm = document.getElementById("panel-smarthome");
    if (btnHm && panHm) {
      btnHm.addEventListener("click", function () {
        panHm.classList.toggle("open");
        if (panHm.classList.contains("open") && self.smarthomeHaReady) {
          self.refreshSmarthomeDevices();
        }
      });
    }

    var rBtns = document.querySelectorAll(".sm-routine");
    if (rBtns && rBtns.length) {
      rBtns.forEach(function (b) {
        b.addEventListener("click", function () {
          var n = b.getAttribute("data-routine");
          if (n) self.runSmarthomeRoutine(n);
        });
      });
    }
  };

  JarvisUI.prototype.startClock = function () {
    var el = document.getElementById("clock");
    function tick() {
      if (!el) return;
      var d = new Date();
      el.textContent = d.toLocaleTimeString(undefined, { hour12: false });
    }
    tick();
    setInterval(tick, 1000);
  };

  JarvisUI.prototype.startUptimeCounter = function () {
    var el = document.getElementById("uptime-local");
    var self = this;
    function fmt(ms) {
      var s = Math.floor(ms / 1000);
      var h = Math.floor(s / 3600);
      var m = Math.floor((s % 3600) / 60);
      var sec = s % 60;
      return (
        String(h).padStart(2, "0") +
        ":" +
        String(m).padStart(2, "0") +
        ":" +
        String(sec).padStart(2, "0")
      );
    }
    setInterval(function () {
      if (el) el.textContent = fmt(Date.now() - self.uptimeLocalStart);
    }, 1000);
    if (el) el.textContent = "00:00:00";
  };

  JarvisUI.prototype.setConnected = function (ok) {
    var dot = document.getElementById("conn-dot");
    if (!dot) return;
    dot.classList.remove("ok", "bad");
    dot.classList.add(ok ? "ok" : "bad");
    if (ok) this.showReconnectBanner(false);
  };

  JarvisUI.prototype.showReconnectBanner = function (show) {
    var b = document.getElementById("banner-lost");
    if (!b) return;
    if (show) b.classList.add("show");
    else b.classList.remove("show");
  };

  JarvisUI.prototype.applyJarvisStatus = function (data) {
    if (data.provider) this.updateProvider(data.provider);
    if (data.skills) this.renderSkillsList(data.skills);
    var v = data.voice || {};
    var sw = document.getElementById("voice-switch");
    if (sw && typeof v.web_output_enabled === "boolean") {
      sw.classList.toggle("on", v.web_output_enabled);
    }
    this.updateMicButtonLabel();
  };

  JarvisUI.prototype.renderSkillsList = function (skills) {
    var ul = document.getElementById("skills-list");
    if (!ul || !Array.isArray(skills)) return;
    ul.innerHTML = "";
    skills.forEach(function (s) {
      var li = document.createElement("li");
      var dot = document.createElement("span");
      dot.className = "skill-dot " + (s.enabled ? "on" : "off");
      li.appendChild(dot);
      li.appendChild(document.createTextNode(s.label || s.id || "?"));
      ul.appendChild(li);
    });
  };

  JarvisUI.prototype.updateProvider = function (name) {
    var el = document.getElementById("provider-badge");
    if (el) el.textContent = name || "—";
    var hdr = document.getElementById("header-provider");
    if (hdr) hdr.textContent = name || "—";
  };

  JarvisUI.prototype.updateStats = function (data) {
    if (data.error) {
      log("stats error", data.error);
      return;
    }
    var cpu = parseFloat(data.cpu_percent);
    var ram = parseFloat(data.ram_percent);
    var disk = parseFloat(data.disk_percent);
    if (!isNaN(cpu)) this.setBar("cpu-bar", cpu);
    if (!isNaN(ram)) this.setBar("ram-bar", ram);
    if (!isNaN(disk)) this.setBar("disk-bar", disk);
    var battEl = document.getElementById("battery-val");
    if (battEl && data.battery) battEl.textContent = data.battery;
    var upEl = document.getElementById("uptime-sys");
    if (upEl && data.uptime_seconds != null) {
      var sec = parseInt(data.uptime_seconds, 10) || 0;
      var h = Math.floor(sec / 3600);
      var m = Math.floor((sec % 3600) / 60);
      var s = sec % 60;
      upEl.textContent =
        String(h).padStart(2, "0") +
        ":" +
        String(m).padStart(2, "0") +
        ":" +
        String(s).padStart(2, "0");
    }
  };

  JarvisUI.prototype.setBar = function (id, pct) {
    var el = document.getElementById(id);
    if (!el) return;
    var v = Math.max(0, Math.min(100, pct));
    el.style.width = v + "%";
    var label = document.getElementById(id.replace("-bar", "-pct"));
    if (label) label.textContent = Math.round(v) + "%";
  };

  JarvisUI.prototype.sendFromInput = function () {
    var input = document.getElementById("msg-input");
    var text = (input && input.value || "").trim();
    if (!text) return;
    playClickSound();
    this.sendMessage(text);
    if (input) input.value = "";
    var c = document.getElementById("char-count");
    if (c) c.textContent = "0";
  };

  JarvisUI.prototype.sendMessage = function (text) {
    if (!this.socket || !this.socket.connected) {
      log("cannot send — disconnected");
      return;
    }
    this.appendUserBubble(text);
    this.showTypingIndicator();
    this.socket.emit("user_message", { message: text, session_id: this.sessionId });
  };

  JarvisUI.prototype.appendUserBubble = function (text) {
    var box = document.getElementById("messages");
    if (!box) return;
    var div = document.createElement("div");
    div.className = "msg user";
    var ts = new Date().toLocaleTimeString(undefined, { hour12: false });
    div.innerHTML =
      "<div>" +
      esc(text) +
      '</div><div class="msg-meta">' +
      esc(ts) +
      "</div>";
    box.appendChild(div);
    this.scrollChat();
  };

  JarvisUI.prototype.showTypingIndicator = function () {
    this.removeTypingIndicator();
    var box = document.getElementById("messages");
    if (!box) return;
    var div = document.createElement("div");
    div.className = "msg jarvis typing-row";
    div.id = "jarvis-typing";
    div.innerHTML =
      '<div class="jarvis-label">[JARVIS]<span class="typing-dots"><span></span><span></span><span></span></span></div>';
    box.appendChild(div);
    this.typingEl = div;
    this.scrollChat();
  };

  JarvisUI.prototype.removeTypingIndicator = function () {
    var t = document.getElementById("jarvis-typing");
    if (t) t.remove();
    this.typingEl = null;
  };

  JarvisUI.prototype.receiveMessage = function (data) {
    this.removeTypingIndicator();
    var text = data.response || "";
    var tool = data.tool_used || "";
    var ts = new Date().toLocaleTimeString(undefined, { hour12: false });
    var box = document.getElementById("messages");
    if (!box) return;
    var div = document.createElement("div");
    div.className = "msg jarvis";
    var badge =
      tool && tool !== "chat" && tool !== "direct" && tool !== "error"
        ? '<div class="msg-badge">🔧 ' +
          esc(tool) +
          "</div>"
        : "";
    if (tool === "error" && data.error) {
      badge = '<div class="msg-badge">⚠ error</div>';
    }
    div.innerHTML =
      '<div class="jarvis-label">[JARVIS]</div><div>' +
      esc(text) +
      "</div>" +
      badge +
      '<div class="msg-meta">' +
      esc(ts) +
      "</div>";
    box.appendChild(div);
    this.scrollChat();
  };

  JarvisUI.prototype.scrollChat = function () {
    var box = document.getElementById("messages");
    if (!box) return;
    box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
  };

  JarvisUI.prototype.applyHistory = function (hist) {
    var box = document.getElementById("messages");
    if (!box || !Array.isArray(hist)) return;
    box.innerHTML = "";
    var self = this;
    hist.forEach(function (m) {
      if (!m || !m.role || !m.content) return;
      if (m.role === "user") self.appendUserBubble(m.content);
      else if (m.role === "assistant")
        self.receiveMessage({ response: m.content, tool_used: "" });
    });
    self.removeTypingIndicator();
  };

  JarvisUI.prototype.toggleVoice = function () {
    if (!this.voiceConfigEnabled) {
      log("voice not enabled in config");
      alert("Voice not enabled in server config.");
      return;
    }
    this.startBrowserStt();
  };

  JarvisUI.prototype.toggleVoiceServer = function (enabled) {
    if (!this.socket) return;
    this.socket.emit("voice_toggle", { enabled: !!enabled });
  };

  JarvisUI.prototype.applyVoiceAck = function (data) {
    var sw = document.getElementById("voice-switch");
    if (data.ok && sw) sw.classList.toggle("on", !!data.enabled);
    this.updateMicButtonLabel();
  };

  JarvisUI.prototype.updateMicButtonLabel = function () {
    var mic = document.getElementById("btn-mic");
    if (!mic) return;
    if (!this.voiceConfigEnabled) {
      mic.title = "Voice not enabled";
      mic.textContent = "🎤";
      return;
    }
    var sw = document.getElementById("voice-switch");
    var on = sw && sw.classList.contains("on");
    mic.title = on
      ? "Browser speech-to-text (click to dictate)"
      : "Enable voice output switch first, or use mic for browser STT only";
    mic.textContent = "🎤";
  };

  JarvisUI.prototype.startBrowserStt = function () {
    if (!this.voiceConfigEnabled) return;
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      log("Web Speech API unavailable");
      return;
    }
    var rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    var self = this;
    rec.onresult = function (ev) {
      var t = ev.results[0] && ev.results[0][0] && ev.results[0][0].transcript;
      if (t) {
        var input = document.getElementById("msg-input");
        if (input) input.value = (input.value || "") + t;
        self.sendFromInput();
      }
    };
    rec.onerror = function (e) {
      log("STT error", e);
    };
    try {
      rec.start();
    } catch (e) {
      log("STT start failed", e);
    }
  };

  JarvisUI.prototype.runSearch = function (query) {
    if (!query) return;
    var self = this;
    fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: query }),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (res) {
        var box = document.getElementById("search-results");
        if (!box) return;
        var data = res.data;
        if (!res.ok || data.error) {
          box.innerHTML = "<p>" + esc((data && data.error) || "Search failed") + "</p>";
          return;
        }
        var items = data.results || [];
        if (!items.length) {
          box.innerHTML = "<p>(no results)</p>";
          return;
        }
        function safeUrl(u) {
          u = String(u || "").trim();
          return /^https?:\/\//i.test(u) ? u : "#";
        }
        box.innerHTML = items
          .map(function (it) {
            var href = safeUrl(it.url);
            return (
              "<div style='margin-bottom:0.5rem;border-bottom:1px solid rgba(0,212,255,0.15);padding-bottom:0.35rem'>" +
              "<strong>" +
              esc(it.title) +
              '</strong><br/><a href="' +
              href +
              '" target="_blank" rel="noopener noreferrer">' +
              esc(it.url) +
              "</a><br/>" +
              esc(it.body) +
              "</div>"
            );
          })
          .join("");
      })
      .catch(function (e) {
        log("search failed", e);
      });
  };

  JarvisUI.prototype.runCommand = function (cmd) {
    if (!cmd) return;
    var self = this;
    this.recentCmds.unshift(cmd);
    this.recentCmds = this.recentCmds.slice(0, this.maxRecent);
    this.renderRecentCmds();
    fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: cmd }),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (res) {
        var box = document.getElementById("term-out");
        if (!box) return;
        var data = res.data;
        if (!res.ok && data.error) {
          box.textContent = data.error;
          return;
        }
        if (data.error) {
          box.textContent = data.error;
          return;
        }
        box.textContent =
          (data.stdout || "") + (data.stderr ? "\n[stderr]\n" + data.stderr : "") ||
          "(empty output)";
      })
      .catch(function (e) {
        log("run failed", e);
      });
  };

  JarvisUI.prototype.renderRecentCmds = function () {
    var el = document.getElementById("recent-cmds");
    if (!el) return;
    el.innerHTML = this.recentCmds.map(function (c) {
      return "<div>" + esc(c) + "</div>";
    }).join("");
  };

  JarvisUI.prototype.listFiles = function (path) {
    var url = "/api/files?path=" + encodeURIComponent(path || ".");
    fetch(url)
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var box = document.getElementById("file-list");
        if (!box) return;
        if (data.error) {
          box.innerHTML = "<p>" + esc(data.error) + "</p>";
          return;
        }
        var entries = data.entries || [];
        box.innerHTML = entries
          .map(function (e) {
            return (
              "<div>" +
              esc(e.kind || "") +
              " " +
              esc(e.name || "") +
              " <span style='opacity:0.6'>" +
              esc(e.size || "") +
              "</span></div>"
            );
          })
          .join("");
      })
      .catch(function (e) {
        log("files failed", e);
      });
  };

  JarvisUI.prototype.clearHistory = function () {
    if (!confirm("Clear all conversation memory?")) return;
    if (this.socket) this.socket.emit("clear_memory");
  };

  JarvisUI.prototype.refreshSmarthomeDevices = function () {
    var self = this;
    if (!this.smarthomeHaReady) return;
    fetch("/api/smarthome/devices")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.registry) return;
        self.smarthomeRegistry = data.registry;
        self.renderSmarthomePanel();
      })
      .catch(function (e) {
        log("smarthome devices failed", e);
      });
  };

  JarvisUI.prototype.applyDeviceUpdate = function (data) {
    if (!this.smarthomeHaReady || !this.smarthomeRegistry || !this.smarthomeRegistry.entities) return;
    var states = data.states || {};
    var ents = this.smarthomeRegistry.entities;
    for (var i = 0; i < ents.length; i++) {
      var e = ents[i];
      var id = e.entity_id;
      if (!id || !states[id]) continue;
      e.state = states[id].state;
      if (states[id].attributes) e.attributes = states[id].attributes;
    }
    var pan = document.getElementById("panel-smarthome");
    if (pan && pan.classList.contains("open")) {
      this.renderSmarthomePanel();
    }
  };

  JarvisUI.prototype.renderSmarthomePanel = function () {
    var reg = this.smarthomeRegistry;
    var tabs = document.getElementById("smarthome-room-tabs");
    var list = document.getElementById("smarthome-device-list");
    if (!reg || !tabs || !list) return;
    var byRoom = reg.by_room || {};
    var rooms = Object.keys(byRoom).sort();
    if (!rooms.length) {
      tabs.innerHTML = "";
      list.innerHTML = "<p style='opacity:0.7;font-size:0.85rem'>No devices discovered yet.</p>";
      return;
    }
    if (!this.smarthomeRoom || rooms.indexOf(this.smarthomeRoom) < 0) {
      this.smarthomeRoom = rooms[0];
    }
    var self = this;
    tabs.innerHTML = rooms
      .map(function (rid) {
        var label = rid.replace(/_/g, " ");
        var on = rid === self.smarthomeRoom ? " on" : "";
        return (
          "<button type='button' class='sm-tab" +
          on +
          "' data-room='" +
          esc(rid) +
          "'>" +
          esc(label) +
          "</button>"
        );
      })
      .join("");
    tabs.querySelectorAll(".sm-tab").forEach(function (t) {
      t.addEventListener("click", function () {
        self.smarthomeRoom = t.getAttribute("data-room");
        self.renderSmarthomePanel();
      });
    });

    var devices = byRoom[this.smarthomeRoom] || [];
    list.innerHTML = devices.map(function (d) {
      return self.renderSmarthomeDeviceRow(d);
    }).join("");
    list.querySelectorAll("[data-sm-toggle]").forEach(function (el) {
      el.addEventListener("click", function () {
        var eid = el.getAttribute("data-eid");
        var want = el.getAttribute("data-sm-toggle");
        self.smarthomeControlEntity(eid, { state: want });
      });
    });
    list.querySelectorAll(".sm-bright").forEach(function (el) {
      el.addEventListener("change", function () {
        var eid = el.getAttribute("data-eid");
        var v = parseInt(el.value, 10);
        if (!eid || isNaN(v)) return;
        var bri = Math.max(0, Math.min(255, Math.round((v * 255) / 100)));
        self.smarthomeControlEntity(eid, { brightness: bri });
      });
    });
    list.querySelectorAll(".sm-temp-apply").forEach(function (el) {
      el.addEventListener("click", function () {
        var eid = el.getAttribute("data-eid");
        var inp = list.querySelector(".sm-temp-inp[data-eid='" + eid + "']");
        if (!inp || !eid) return;
        var t = parseFloat(inp.value);
        if (isNaN(t)) return;
        self.smarthomeControlEntity(eid, { temperature: t });
      });
    });
  };

  JarvisUI.prototype.brightnessPct = function (d) {
    var a = d.attributes || {};
    if (typeof a.brightness === "number") {
      return Math.round((a.brightness * 100) / 255);
    }
    if (typeof a.brightness_pct === "number") return Math.round(a.brightness_pct);
    return d.state === "on" ? 100 : 0;
  };

  JarvisUI.prototype.renderSmarthomeDeviceRow = function (d) {
    var dom = d.domain || (d.entity_id || "").split(".")[0];
    var name = esc(d.friendly_name || d.entity_id || "");
    var st = esc(String(d.state != null ? d.state : "—"));
    var eid = d.entity_id || "";
    var head =
      "<div class='sm-device-head'><div><div class='sm-device-name'>" +
      name +
      "</div><div class='sm-device-meta'>" +
      esc(dom) +
      " · " +
      st +
      "</div></div>";
    if (dom === "light" || dom === "switch") {
      var on = String(d.state).toLowerCase() === "on";
      head +=
        "<button type='button' class='btn' data-sm-toggle='" +
        (on ? "off" : "on") +
        "' data-eid='" +
        esc(eid) +
        "'>" +
        (on ? "Off" : "On") +
        "</button>";
    }
    head += "</div>";
    var body = "";
    if (dom === "light") {
      var pct = this.brightnessPct(d);
      body +=
        "<div class='sm-slider-row'><label style='font-size:0.7rem;opacity:0.8'>Brightness</label><input type='range' class='sm-bright' min='0' max='100' value='" +
        pct +
        "' data-eid='" +
        esc(eid) +
        "'/></div>";
    }
    if (dom === "climate") {
      var cur = d.attributes && d.attributes.temperature != null ? d.attributes.temperature : "";
      body +=
        "<div class='sm-temp-row'><label style='font-size:0.72rem'>Target °C</label><input type='number' step='0.5' class='sm-temp-inp' data-eid='" +
        esc(eid) +
        "' value='" +
        esc(String(cur)) +
        "'/><button type='button' class='btn sm-temp-apply' data-eid='" +
        esc(eid) +
        "'>Set</button></div>";
    }
    return "<div class='sm-device' data-eid='" + esc(eid) + "'>" + head + body + "</div>";
  };

  JarvisUI.prototype.smarthomeControlEntity = function (eid, payload) {
    var body = Object.assign({ entity_id: eid }, payload);
    fetch("/api/smarthome/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (res) {
        if (!res.ok) log("smarthome control", res.data);
        else log("smarthome control ok", eid);
      })
      .catch(function (e) {
        log("smarthome control failed", e);
      });
  };

  JarvisUI.prototype.runSmarthomeRoutine = function (name) {
    var self = this;
    fetch("/api/smarthome/routine", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        log("routine", name, data);
        if (data && data.ok) {
          window.setTimeout(function () {
            self.refreshSmarthomeDevices();
          }, 800);
        }
      })
      .catch(function (e) {
        log("routine failed", e);
      });
  };

  window.JarvisUI = JarvisUI;
})();
