"use strict";

(function exposeColorFields(global) {
  function channel(value, name) {
    const text = String(value).trim();
    if (!/^\d{1,3}$/.test(text)) {
      throw new TypeError(`${name} 必须是 0–255 的整数`);
    }
    const numeric = Number(text);
    if (!Number.isInteger(numeric) || numeric < 0 || numeric > 255) {
      throw new TypeError(`${name} 必须是 0–255 的整数`);
    }
    return numeric;
  }

  function fromHex(value) {
    const normalized = String(value).trim().toUpperCase();
    if (!/^#[0-9A-F]{6}$/.test(normalized)) {
      throw new TypeError("颜色必须使用 #RRGGBB");
    }
    return {
      hex: normalized,
      red: Number.parseInt(normalized.slice(1, 3), 16),
      green: Number.parseInt(normalized.slice(3, 5), 16),
      blue: Number.parseInt(normalized.slice(5, 7), 16),
    };
  }

  function fromHexInput(value) {
    const raw = String(value).trim();
    const digits = raw.startsWith("#") ? raw.slice(1) : raw;
    if (!/^[0-9A-Fa-f]{6}$/.test(digits)) {
      throw new TypeError("颜色必须使用 6 位 RRGGBB");
    }
    return fromHex(`#${digits}`);
  }

  function fromChannels(red, green, blue) {
    const color = {
      red: channel(red, "红色"),
      green: channel(green, "绿色"),
      blue: channel(blue, "蓝色"),
    };
    color.hex = `#${[color.red, color.green, color.blue]
      .map((value) => value.toString(16).padStart(2, "0"))
      .join("")
      .toUpperCase()}`;
    return color;
  }

  function sameColor(left, right) {
    return Boolean(
      left
      && right
      && left.red === right.red
      && left.green === right.green
      && left.blue === right.blue
    );
  }

  function cloneColor(color) {
    const valid = fromChannels(color.red, color.green, color.blue);
    if (color.hex && fromHex(color.hex).hex !== valid.hex) {
      throw new TypeError("十六进制与十进制 RGB 不一致");
    }
    return valid;
  }

  class ColorFields {
    constructor(root, options = {}) {
      if (!(root instanceof HTMLElement)) {
        throw new TypeError("ColorFields requires an HTML root");
      }
      this.root = root;
      this.onChange = typeof options.onChange === "function" ? options.onChange : () => {};
      this.hexInput = root.querySelector("[data-color-hex]");
      this.redInput = root.querySelector('[data-color-channel="red"]');
      this.greenInput = root.querySelector('[data-color-channel="green"]');
      this.blueInput = root.querySelector('[data-color-channel="blue"]');
      this.preview = root.querySelector("[data-color-preview]");
      this.error = root.querySelector("[data-color-error]");
      if (!this.hexInput || !this.redInput || !this.greenInput || !this.blueInput
          || !this.preview || !this.error) {
        throw new Error("ColorFields markup is incomplete");
      }
      this.value = null;
      this.validInput = false;
      this._bind();
    }

    _bind() {
      this.hexInput.addEventListener("input", () => {
        try {
          const color = fromHexInput(this.hexInput.value);
          this.hexInput.value = color.hex.slice(1);
          this.redInput.value = String(color.red);
          this.greenInput.value = String(color.green);
          this.blueInput.value = String(color.blue);
          this._accept(color, "hex");
        } catch (error) {
          this._reject(error);
        }
      });
      this.hexInput.addEventListener("blur", () => {
        if (this.value) this.hexInput.value = this.value.hex.slice(1);
      });
      for (const input of [this.redInput, this.greenInput, this.blueInput]) {
        input.addEventListener("input", () => {
          try {
            const color = fromChannels(
              this.redInput.value,
              this.greenInput.value,
              this.blueInput.value,
            );
            this.hexInput.value = color.hex.slice(1);
            this._accept(color, "rgb");
          } catch (error) {
            this._reject(error);
          }
        });
        input.addEventListener("blur", () => {
          if (this.value) this._renderInputs(this.value);
        });
      }
    }

    _renderInputs(color) {
      this.hexInput.value = color.hex.slice(1);
      this.redInput.value = String(color.red);
      this.greenInput.value = String(color.green);
      this.blueInput.value = String(color.blue);
    }

    _renderPreview(color) {
      this.preview.style.backgroundColor = color.hex;
      this.preview.setAttribute(
        "aria-label",
        `配置颜色预览 ${color.hex}，尚未应用到 Windows`,
      );
      this.preview.classList.remove("is-unavailable");
    }

    _accept(color, source) {
      const changed = !sameColor(this.value, color);
      this.value = color;
      this.validInput = true;
      this.error.textContent = "";
      this._renderPreview(color);
      if (changed) this.onChange(cloneColor(color), { source });
    }

    _reject(error) {
      this.validInput = false;
      this.error.textContent = String(error.message || error);
    }

    setColor(color) {
      const valid = cloneColor(color);
      this.value = valid;
      this.validInput = true;
      this._renderInputs(valid);
      this._renderPreview(valid);
      this.error.textContent = "";
      for (const input of [
        this.hexInput,
        this.redInput,
        this.greenInput,
        this.blueInput,
      ]) {
        input.disabled = false;
      }
    }

    setUnavailable(message) {
      this.value = null;
      this.validInput = false;
      this.hexInput.value = "";
      this.redInput.value = "";
      this.greenInput.value = "";
      this.blueInput.value = "";
      this.preview.style.backgroundColor = "";
      this.preview.setAttribute("aria-label", "颜色记录不可用");
      this.preview.classList.add("is-unavailable");
      this.error.textContent = message;
      for (const input of [
        this.hexInput,
        this.redInput,
        this.greenInput,
        this.blueInput,
      ]) {
        input.disabled = true;
      }
    }

    getColor() {
      return this.value ? cloneColor(this.value) : null;
    }

    isValid() {
      return this.validInput && Boolean(this.value);
    }
  }

  global.ThemeSchedulerColor = Object.freeze({
    fromHex,
    fromHexInput,
    fromChannels,
    sameColor,
    cloneColor,
    ColorFields,
  });
}(window));
