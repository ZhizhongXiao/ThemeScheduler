"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.resolve(__dirname, "..", "..", "ui", "js", "time_math.js"),
  "utf8",
);
const context = { window: {} };
vm.createContext(context);
vm.runInContext(source, context, { filename: "time_math.js" });
const math = context.window.ThemeSchedulerTimeMath;

assert.equal(math.formatTime(math.parseTime("06:25").totalMinutes), "06:25");
assert.equal(math.angleFor24Hour(math.parseTime("06:25").totalMinutes), 96.25);
assert.equal(math.angleFor24Hour(math.parseTime("07:45").totalMinutes), 116.25);
assert.equal(math.hourHandAngle(math.parseTime("06:25").totalMinutes), 180);
assert.equal(math.hourHandAngle(math.parseTime("18:25").totalMinutes), 180);

const beforeNoon = math.parseTime("11:55").totalMinutes;
assert.equal(math.formatTime(math.applyHourDrag(beforeNoon, 60)), "13:55");
assert.equal(math.formatTime(math.applyHourDrag(beforeNoon, 360)), "23:55");
assert.equal(math.formatTime(math.applyHourDrag(beforeNoon, -360)), "23:55");

const minuteCrossing = math.parseTime("06:55").totalMinutes;
assert.equal(math.formatTime(math.applyMinuteDrag(minuteCrossing, 30)), "06:00");
assert.equal(math.formatTime(math.applyMinuteDrag(minuteCrossing, 360)), "06:55");
assert.equal(math.formatTime(math.applyMinuteDrag(minuteCrossing, -360)), "06:55");

assert.equal(
  math.durationMinutes(
    math.parseTime("20:00").totalMinutes,
    math.parseTime("06:00").totalMinutes,
  ),
  600,
);
assert.equal(math.isFiveMinuteValue(math.parseTime("06:25").totalMinutes), true);
assert.equal(math.isFiveMinuteValue(math.parseTime("06:27").totalMinutes), false);

process.stdout.write("stage12 time math: ok\n");
