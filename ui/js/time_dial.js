"use strict";

(function exposeTimeDials(global) {
  const math = global.ThemeSchedulerTimeMath;
  if (!math) throw new Error("ThemeSchedulerTimeMath must load before time_dial.js");

  const SVG_NS = "http://www.w3.org/2000/svg";

  function svgNode(name, attributes = {}) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }

  function pointAt(angle, radius, center = 150) {
    const radians = (angle - 90) * Math.PI / 180;
    return {
      x: center + radius * Math.cos(radians),
      y: center + radius * Math.sin(radians),
    };
  }

  function arcPath(startAngle, endAngle, radius) {
    const start = pointAt(startAngle, radius);
    const end = pointAt(endAngle, radius);
    const sweep = ((endAngle - startAngle) % 360 + 360) % 360;
    const largeArc = sweep > 180 ? 1 : 0;
    return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y}`;
  }

  function addTitle(node, text) {
    const title = svgNode("title");
    title.textContent = text;
    node.appendChild(title);
  }

  class InteractiveTimeDial {
    constructor(root, options = {}) {
      if (!(root instanceof HTMLElement)) {
        throw new TypeError("InteractiveTimeDial requires an HTML root");
      }
      this.root = root;
      this.onChange = typeof options.onChange === "function" ? options.onChange : () => {};
      this.value = math.parseTime(options.value || "06:15").totalMinutes;
      this.selectedHand = "minute";
      this.drag = null;
      this._build();
      this.render();
    }

    _build() {
      this.svg = svgNode("svg", {
        class: "interactive-clock",
        viewBox: "0 0 300 300",
        role: "group",
        "aria-label": "12 小时时间编辑表盘",
      });
      this.svg.appendChild(svgNode("circle", {
        class: "clock-face",
        cx: 150,
        cy: 150,
        r: 132,
      }));

      for (let index = 0; index < 60; index += 1) {
        const angle = index * 6;
        const major = index % 5 === 0;
        const outer = pointAt(angle, 125);
        const inner = pointAt(angle, major ? 113 : 119);
        this.svg.appendChild(svgNode("line", {
          class: major ? "clock-tick is-major" : "clock-tick",
          x1: inner.x,
          y1: inner.y,
          x2: outer.x,
          y2: outer.y,
        }));
      }

      for (let hour = 1; hour <= 12; hour += 1) {
        const position = pointAt(hour * 30, 96);
        const label = svgNode("text", {
          class: "clock-number",
          x: position.x,
          y: position.y,
          "text-anchor": "middle",
          "dominant-baseline": "central",
        });
        label.textContent = String(hour);
        this.svg.appendChild(label);
      }

      this.hourGroup = this._hand("hour", 70, "hour-hand");
      this.minuteGroup = this._hand("minute", 103, "minute-hand");
      this.svg.appendChild(this.hourGroup);
      this.svg.appendChild(this.minuteGroup);
      this.svg.appendChild(svgNode("circle", {
        class: "clock-pin",
        cx: 150,
        cy: 150,
        r: 7,
      }));
      this.root.replaceChildren(this.svg);

      this.svg.addEventListener("pointermove", (event) => this._move(event));
      this.svg.addEventListener("pointerup", (event) => this._end(event));
      this.svg.addEventListener("pointercancel", (event) => this._end(event));
    }

    _hand(kind, length, className) {
      const group = svgNode("g", {
        class: `clock-hand-group ${className}`,
        role: "button",
        tabindex: "0",
        "aria-label": kind === "hour" ? "调整小时" : "调整分钟",
      });
      group.dataset.hand = kind;
      group.appendChild(svgNode("line", {
        class: "clock-hand-hit",
        x1: 150,
        y1: 160,
        x2: 150,
        y2: 150 - length,
      }));
      group.appendChild(svgNode("line", {
        class: "clock-hand-visible",
        x1: 150,
        y1: 160,
        x2: 150,
        y2: 150 - length,
      }));
      group.addEventListener("pointerdown", (event) => this._start(event, kind));
      group.addEventListener("click", () => this.selectHand(kind));
      group.addEventListener("keydown", (event) => this._key(event, kind));
      return group;
    }

    _eventAngle(event) {
      const bounds = this.svg.getBoundingClientRect();
      const x = (event.clientX - bounds.left) / bounds.width * 300;
      const y = (event.clientY - bounds.top) / bounds.height * 300;
      return math.clockAngleFromPoint(x, y, 150, 150);
    }

    _start(event, kind) {
      event.preventDefault();
      this.selectHand(kind);
      this.drag = {
        pointerId: event.pointerId,
        hand: kind,
        previousAngle: this._eventAngle(event),
        accumulated: 0,
        initialValue: this.value,
        changed: false,
      };
      this.svg.setPointerCapture(event.pointerId);
    }

    _move(event) {
      if (!this.drag || this.drag.pointerId !== event.pointerId) return;
      const angle = this._eventAngle(event);
      this.drag.accumulated += math.shortestAngleDelta(this.drag.previousAngle, angle);
      this.drag.previousAngle = angle;
      const next = this.drag.hand === "hour"
        ? math.applyHourDrag(this.drag.initialValue, this.drag.accumulated)
        : math.applyMinuteDrag(this.drag.initialValue, this.drag.accumulated);
      const before = this.value;
      this._commit(next, "pointer");
      if (this.value !== before) this.drag.changed = true;
    }

    _end(event) {
      if (!this.drag || this.drag.pointerId !== event.pointerId) return;
      const completed = this.drag;
      if (this.svg.hasPointerCapture(event.pointerId)) {
        this.svg.releasePointerCapture(event.pointerId);
      }
      this.drag = null;
      this.root.dispatchEvent(new CustomEvent("dialdragend", {
        detail: {
          selectedHand: completed.hand,
          changed: completed.changed,
          value: this.getTime(),
        },
      }));
    }

    _key(event, kind) {
      const direction = ["ArrowRight", "ArrowUp"].includes(event.key)
        ? 1
        : ["ArrowLeft", "ArrowDown"].includes(event.key)
          ? -1
          : 0;
      if (!direction) return;
      event.preventDefault();
      this.selectHand(kind);
      if (kind === "hour") {
        this._commit(this.value + direction * 60, "keyboard");
      } else {
        const hour = Math.floor(this.value / 60);
        const minute = ((this.value % 60 + direction * 5) % 60 + 60) % 60;
        this._commit(hour * 60 + minute, "keyboard");
      }
    }

    _commit(value, source) {
      const normalized = math.normalizeMinutes(value);
      if (normalized === this.value) return;
      this.value = normalized;
      this.render();
      this.onChange(math.formatTime(this.value), {
        selectedHand: this.selectedHand,
        source,
      });
    }

    selectHand(kind) {
      if (!["hour", "minute"].includes(kind)) return;
      this.selectedHand = kind;
      this.render();
      this.root.dispatchEvent(new CustomEvent("dialhandchange", {
        detail: { selectedHand: kind },
      }));
    }

    shiftHalfDay(direction) {
      const step = direction >= 0 ? 12 * 60 : -12 * 60;
      this.selectHand("hour");
      this._commit(this.value + step, "half-day");
    }

    setMeridiem(value) {
      if (!["am", "pm"].includes(value)) return;
      const currentHour = Math.floor(this.value / 60);
      const isPm = currentHour >= 12;
      if ((value === "pm") !== isPm) this.shiftHalfDay(value === "pm" ? 1 : -1);
    }

    setTime(value, options = {}) {
      const parsed = typeof value === "string" ? math.parseTime(value).totalMinutes : value;
      this.value = math.normalizeMinutes(parsed);
      if (options.selectedHand) this.selectedHand = options.selectedHand;
      this.render();
    }

    getTime() {
      return math.formatTime(this.value);
    }

    render() {
      this.hourGroup.setAttribute("transform", `rotate(${math.hourHandAngle(this.value)} 150 150)`);
      this.minuteGroup.setAttribute("transform", `rotate(${math.minuteHandAngle(this.value)} 150 150)`);
      this.hourGroup.classList.toggle("is-selected", this.selectedHand === "hour");
      this.minuteGroup.classList.toggle("is-selected", this.selectedHand === "minute");
      const formatted = math.formatTime(this.value);
      const [hour, minute] = formatted.split(":");
      this.hourGroup.setAttribute("aria-valuetext", `${hour} 时`);
      this.minuteGroup.setAttribute("aria-valuetext", `${minute} 分`);
    }
  }

  class ScheduleSummaryDial {
    constructor(root) {
      if (!(root instanceof HTMLElement)) {
        throw new TypeError("ScheduleSummaryDial requires an HTML root");
      }
      this.root = root;
      this.day = math.parseTime("06:15").totalMinutes;
      this.night = math.parseTime("23:45").totalMinutes;
      this._build();
      this.render();
    }

    _build() {
      this.svg = svgNode("svg", {
        class: "summary-clock",
        viewBox: "-12 -12 324 324",
        role: "img",
        "aria-label": "24 小时昼夜计划总览",
      });
      this.svg.appendChild(svgNode("circle", {
        class: "summary-clock-face",
        cx: 150,
        cy: 150,
        r: 118,
      }));
      this.dayArc = svgNode("path", { class: "schedule-arc day-arc" });
      this.nightArc = svgNode("path", { class: "schedule-arc night-arc" });
      this.svg.appendChild(this.dayArc);
      this.svg.appendChild(this.nightArc);
      for (let index = 0; index < 96; index += 1) {
        const angle = index * 3.75;
        const hourTick = index % 4 === 0;
        const outer = pointAt(angle, 116);
        const inner = pointAt(angle, hourTick ? 107 : 112);
        this.svg.appendChild(svgNode("line", {
          class: hourTick ? "summary-tick is-hour" : "summary-tick",
          x1: inner.x,
          y1: inner.y,
          x2: outer.x,
          y2: outer.y,
        }));
      }
      for (const hour of [0, 6, 12, 18]) {
        const position = pointAt(hour * 15, 146);
        const label = svgNode("text", {
          class: "summary-hour is-major",
          x: position.x,
          y: position.y,
          "text-anchor": "middle",
          "dominant-baseline": "central",
        });
        label.textContent = String(hour);
        this.svg.appendChild(label);
      }

      this.dayPointer = this._pointer("day");
      this.nightPointer = this._pointer("night");
      this.svg.appendChild(this.dayPointer);
      this.svg.appendChild(this.nightPointer);
      this.svg.appendChild(svgNode("circle", {
        class: "summary-pin",
        cx: 150,
        cy: 150,
        r: 6,
      }));
      this.root.replaceChildren(this.svg);
    }

    _pointer(kind) {
      const isDay = kind === "day";
      const length = isDay ? 124 : 102;
      const group = svgNode("g", { class: `summary-pointer ${kind}-pointer` });
      group.appendChild(svgNode("line", {
        x1: 150,
        y1: 150,
        x2: 150,
        y2: 150 - length,
      }));
      if (isDay) {
        group.appendChild(svgNode("circle", {
          class: "pointer-end",
          cx: 150,
          cy: 150 - length,
          r: 7,
        }));
      } else {
        const y = 150 - length;
        group.appendChild(svgNode("path", {
          class: "pointer-end",
          d: `M 150 ${y - 8} L 158 ${y} L 150 ${y + 8} L 142 ${y} Z`,
        }));
      }
      return group;
    }

    setTimes(day, night) {
      this.day = math.parseTime(day).totalMinutes;
      this.night = math.parseTime(night).totalMinutes;
      this.render();
    }

    _setArc(node, start, duration, label) {
      while (node.firstChild) node.removeChild(node.firstChild);
      if (duration === math.MINUTES_PER_DAY) {
        node.setAttribute("d", `${arcPath(0, 180, 128)} ${arcPath(180, 360, 128)}`);
      } else if (duration === 0) {
        node.setAttribute("d", "");
      } else {
        const startAngle = math.angleFor24Hour(start);
        const endAngle = startAngle + duration / math.MINUTES_PER_DAY * 360;
        node.setAttribute("d", arcPath(startAngle, endAngle, 128));
      }
      addTitle(node, label);
    }

    render() {
      const dayDuration = math.durationMinutes(this.day, this.night);
      const nightDuration = math.MINUTES_PER_DAY - dayDuration;
      const dayText = math.formatTime(this.day);
      const nightText = math.formatTime(this.night);
      const formatDuration = (value) => `${Math.floor(value / 60)} 小时 ${value % 60} 分钟`;

      this._setArc(
        this.dayArc,
        this.day,
        dayDuration,
        `昼间模式：${dayText}–${nightText}，持续 ${formatDuration(dayDuration)}`,
      );
      this._setArc(
        this.nightArc,
        this.night,
        nightDuration,
        `夜间模式：${nightText}–${dayText}，持续 ${formatDuration(nightDuration)}`,
      );
      this.dayPointer.setAttribute("transform", `rotate(${math.angleFor24Hour(this.day)} 150 150)`);
      this.nightPointer.setAttribute("transform", `rotate(${math.angleFor24Hour(this.night)} 150 150)`);
      while (this.dayPointer.lastChild?.tagName === "title") this.dayPointer.lastChild.remove();
      while (this.nightPointer.lastChild?.tagName === "title") this.nightPointer.lastChild.remove();
      addTitle(this.dayPointer, `昼间开始 ${dayText}`);
      addTitle(this.nightPointer, `夜间开始 ${nightText}`);
      this.svg.setAttribute(
        "aria-label",
        `24 小时计划：昼间 ${dayText} 开始，夜间 ${nightText} 开始`,
      );
    }
  }

  global.ThemeSchedulerTimeDials = Object.freeze({
    InteractiveTimeDial,
    ScheduleSummaryDial,
  });
}(window));
