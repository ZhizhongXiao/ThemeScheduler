"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.resolve(__dirname, "..", "..", "ui", "js", "color_fields.js"),
  "utf8",
);
const context = { window: {} };
vm.createContext(context);
vm.runInContext(source, context, { filename: "color_fields.js" });
const color = context.window.ThemeSchedulerColor;

assert.deepEqual(
  { ...color.fromHex("#ffb900") },
  { hex: "#FFB900", red: 255, green: 185, blue: 0 },
);
assert.deepEqual(
  { ...color.fromHexInput("ffb900") },
  { hex: "#FFB900", red: 255, green: 185, blue: 0 },
);
assert.deepEqual(
  { ...color.fromHexInput("#744da9") },
  { hex: "#744DA9", red: 116, green: 77, blue: 169 },
);
assert.deepEqual(
  { ...color.fromChannels("116", 77, 169) },
  { hex: "#744DA9", red: 116, green: 77, blue: 169 },
);
assert.equal(
  color.sameColor(
    color.fromHex("#744DA9"),
    color.fromChannels(116, 77, 169),
  ),
  true,
);
assert.throws(() => color.fromHex("FFB900"), /#RRGGBB/);
assert.throws(() => color.fromHexInput("FFB90"), /6/);
assert.throws(() => color.fromChannels(256, 0, 0), /0–255/);
assert.throws(
  () => color.cloneColor({
    hex: "#744DA9",
    red: 117,
    green: 77,
    blue: 169,
  }),
  /不一致/,
);

process.stdout.write("stage12 color fields: ok\n");
