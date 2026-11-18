const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const boards = {
  pico2: {name: "Pico 2", sha: "b69a7172973fd20b65cf71e2a02149d665e0566617db655d41852a9a46c29c20"},
  pico2_w: {name: "Pico 2 W", sha: "51478e767dcc0956a6ebefcd88304dda9a55d01c030ffab9464cddf3daecb6b8"}
};
const stages = $$(".panel");
const links = $$(".steps a");
const nextLabels = ["Continue to BOOTSEL", "Continue to installation", "Continue to verification", "Continue to card inspection", "Continue to glitch research", "Return to board selection"];
let stage = 0;
let board = "";
let bootSerial = "";
let downloading = false;

function showStep(index, moveFocus) {
  stage = index;
  stages.forEach((panel, i) => {
    panel.hidden = i !== index;
    if (i === index) links[i].setAttribute("aria-current", "step");
    else links[i].removeAttribute("aria-current");
  });
  $("#next").textContent = nextLabels[index];
  $("#next").href = "#" + stages[(index + 1) % stages.length].id;
  $("#back").disabled = index === 0;
  if (moveFocus) {
    stages[index].querySelector("h3").focus({preventScroll: true});
    $(".work-heading").scrollIntoView();
  }
}

function route() {
  const index = stages.findIndex(panel => "#" + panel.id === location.hash);
  if (index >= 0) showStep(index, true);
}
showStep(0, false);
route();
window.addEventListener("hashchange", route);
$("#back").addEventListener("click", () => { if (stage > 0) location.hash = stages[stage - 1].id; });
$("#diagram-button").addEventListener("click", () => $("#diagram").showModal());
$("#close-diagram").addEventListener("click", () => $("#diagram").close());

function updateDownload() {
  $("#download").disabled = !board || !$("#confirm-image").checked || downloading;
}
for (const radio of $$("input[name=board]")) {
  radio.checked = false;
  radio.addEventListener("change", () => {
    board = radio.value;
    bootSerial = "";
    if (usbAvailable) {
      $("#rom-result").textContent = "Waiting for a bootloader.";
      $("#runtime-result").textContent = "Waiting for a runtime device.";
      $("#console-result").textContent = "Inspect the device to check it against your board selection.";
      $("#console-next").hidden = true;
    }
    $("#image-label").textContent = boards[board].name + " diagnostic image";
    $("#checksum").textContent = boards[board].sha;
    $("#expected").textContent = "ga100ctl/" + board + "/1.0.0";
    $("#confirm-image").checked = false;
    $("#download-result").textContent = "";
    updateDownload();
  });
}
$("#confirm-image").checked = false;
$("#confirm-image").addEventListener("change", updateDownload);
updateDownload();

$("#download").addEventListener("click", async () => {
  const selected = board;
  if (!selected || !$("#confirm-image").checked || downloading) return;
  downloading = true;
  updateDownload();
  const output = $("#download-result");
  output.textContent = "Checking image…";
  try {
    const filename = selected + "-diagnostic-1.0.0.uf2";
    const response = await fetch("downloads/" + filename);
    if (!response.ok) throw new Error("Image download failed.");
    const buffer = await response.arrayBuffer();
    if (!crypto.subtle) throw new Error("Use HTTPS or localhost to check the image digest.");
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", buffer));
    const hex = [...digest].map(value => value.toString(16).padStart(2, "0")).join("");
    if (hex !== boards[selected].sha) throw new Error("Image checksum mismatch. Download stopped.");
    if (board !== selected || !$("#confirm-image").checked) return;
    const url = URL.createObjectURL(new Blob([buffer], {type: "application/octet-stream"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    output.textContent = "Checksum matched. Copy " + filename + " from your downloads to RP2350.";
  } catch (error) {
    output.textContent = error.message || "Could not retrieve the image. Retry the download.";
  } finally {
    downloading = false;
    updateDownload();
  }
});

const vendorId = 0x2e8a;
const romPid = 0x000f;
const runtimePid = 0x0009;
const readButtons = $$("[data-read]");
const outputs = {rom: $("#rom-result"), runtime: $("#runtime-result"), either: $("#console-result")};
const hexId = value => value.toString(16).padStart(4, "0");

function interpretDevice(device) {
  if (device.productId === romPid) {
    return {message: "BOOTSEL is active. The Pico is ready to accept a firmware file. Check the printed board model before choosing an image.", step: board ? "install" : "board"};
  }
  const target = Object.keys(boards).find(key => device.productName === "ga100ctl/" + key + "/1.0.0");
  if (!target) {
    return {message: "Unrecognized firmware. The device appeared over USB, but its product string does not identify a supported diagnostic image. Check the printed board model and firmware version.", step: "verify"};
  }
  if (board && board !== target) {
    return {message: "Board mismatch. The firmware reports " + boards[target].name + ", but you selected " + boards[board].name + ". Check the model printed on your board.", step: "board"};
  }
  const ready = boards[target].name + " diagnostics are running (version 1.0.0).";
  if (bootSerial && device.serialNumber) {
    if (bootSerial.toLowerCase() !== device.serialNumber.toLowerCase()) {
      return {message: "Serial mismatch. " + ready + " Select the same Pico you inspected in BOOTSEL.", step: "verify"};
    }
    return {message: (board ? "Verified. " : "Serial matched. ") + ready + " The serial matches the Pico seen in BOOTSEL. Continue to GPU inspection.", step: "card"};
  }
  return {message: ready + " If the printed model matches, continue to GPU inspection. The serial has not been matched across BOOTSEL and runtime.", step: "card"};
}

async function inspect(mode) {
  const output = outputs[mode];
  const ids = mode === "either" ? [romPid, runtimePid] : [mode === "rom" ? romPid : runtimePid];
  readButtons.forEach(button => { button.disabled = true; });
  try {
    const device = await navigator.usb.requestDevice({filters: ids.map(productId => ({vendorId, productId}))});
    if (device.vendorId !== vendorId || !ids.includes(device.productId)) throw new Error("The selected device does not match this step.");
    const summary = [hexId(device.vendorId) + ":" + hexId(device.productId), device.productName || "Unknown product", "Serial " + (device.serialNumber || "unavailable")].join(" · ");
    if (device.productId === romPid) {
      bootSerial = device.serialNumber || "";
      outputs.runtime.textContent = "Waiting for a runtime device.";
    }
    const {message, step} = interpretDevice(device);
    output.textContent = message + "\n" + summary;
    outputs.either.textContent = output.textContent;
    const next = $("#console-next");
    next.href = "#" + step;
    next.textContent = $(`#${step} h3`).textContent;
    next.hidden = false;
  } catch (error) {
    if (mode === "either") $("#console-next").hidden = true;
    output.textContent = error.name === "NotFoundError" ? "No device selected. Check the cable and mode, then reopen the picker." :
      "USB read failed. Use the host checks in the guide. " + (error.message || "");
  } finally {
    readButtons.forEach(button => { button.disabled = false; });
  }
}

const usbAvailable = window.isSecureContext && Boolean(navigator.usb?.requestDevice);
for (const button of readButtons) {
  if (usbAvailable) button.addEventListener("click", () => inspect(button.dataset.read));
  else {
    button.disabled = true;
    outputs[button.dataset.read].textContent = "WebUSB is unavailable here. Use desktop Chrome or Edge on HTTPS, or follow the file-manager and Ubuntu checks in the guide.";
  }
}
