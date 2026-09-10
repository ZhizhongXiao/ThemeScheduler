"use strict";

(function exposeTimeMath(global) {
  const MINUTES_PER_DAY = 24 * 60;
  const MINUTE_STEP = 5;

  function normalizeMinutes(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      throw new TypeError("分钟数必须是有限数字");
    }
    return ((Math.round(numeric) % MINUTES_PER_DAY) + MINUTES_PER_DAY) % MINUTES_PER_DAY;
  }

  function parseTime(value) {
    const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(String(value));
    if (!match) {
      throw new TypeError("时间必须使用 24 小时制 HH:mm");
    }
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    return { hour, minute, totalMinutes: hour * 60 + minute };
  }

  function formatTime(value) {
    const total = normalizeMinutes(value);
    const hour = Math.floor(total / 60);
    const minute = total % 60;
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }

  function angleFor24Hour(value) {
    return normalizeMinutes(value) / MINUTES_PER_DAY * 360;
  }

  function hourHandAngle(value) {
    const total = normalizeMinutes(value);
    return (Math.floor(total / 60) % 12) * 30;
  }

  function minuteHandAngle(value) {
    return normalizeMinutes(value) % 60 * 6;
  }

  function clockAngleFromPoint(x, y, centerX, centerY) {
    const degrees = Math.atan2(x - centerX, centerY - y) * 180 / Math.PI;
    return (degrees + 360) % 360;
  }

  function shortestAngleDelta(previous, current) {
    let delta = current - previous;
    if (delta > 180) delta -= 360;
    if (delta < -180) delta += 360;
    return delta;
  }

  function applyHourDrag(initialTotalMinutes, accumulatedDegrees) {
    const initial = normalizeMinutes(initialTotalMinutes);
    const minute = initial % 60;
    const initialHour = Math.floor(initial / 60);
    const hourDelta = Math.round(accumulatedDegrees / 30);
    const targetHour = ((initialHour + hourDelta) % 24 + 24) % 24;
    return targetHour * 60 + minute;
  }

  function applyMinuteDrag(initialTotalMinutes, accumulatedDegrees) {
    const initial = normalizeMinutes(initialTotalMinutes);
    const hour = Math.floor(initial / 60);
    const initialMinute = initial % 60;
    const minuteDelta = Math.round(accumulatedDegrees / 30) * MINUTE_STEP;
    const targetMinute = ((initialMinute + minuteDelta) % 60 + 60) % 60;
    return hour * 60 + targetMinute;
  }

  function durationMinutes(start, end) {
    const from = normalizeMinutes(start);
    const to = normalizeMinutes(end);
    return (to - from + MINUTES_PER_DAY) % MINUTES_PER_DAY;
  }

  function isFiveMinuteValue(value) {
    return normalizeMinutes(value) % MINUTE_STEP === 0;
  }

  global.ThemeSchedulerTimeMath = Object.freeze({
    MINUTES_PER_DAY,
    MINUTE_STEP,
    normalizeMinutes,
    parseTime,
    formatTime,
    angleFor24Hour,
    hourHandAngle,
    minuteHandAngle,
    clockAngleFromPoint,
    shortestAngleDelta,
    applyHourDrag,
    applyMinuteDrag,
    durationMinutes,
    isFiveMinuteValue,
  });
}(window));
