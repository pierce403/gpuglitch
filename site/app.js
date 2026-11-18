const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const boards = {
  pico2: {name: "Pico 2", sha: "b69a7172973fd20b65cf71e2a02149d665e0566617db655d41852a9a46c29c20"},
  pico2_w: {name: "Pico 2 W", sha: "51478e767dcc0956a6ebefcd88304dda9a55d01c030ffab9464cddf3daecb6b8"}
};
const stages = $$(".panel");
const links = $$(".steps a");
const nextLabels = ["Continue to BOOTSEL", "Continue to installation", "Continue to verification", "Continue to card inspection", "Return to board selection"];
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

function compareRuntime(device) {
  if (!board) return "Choose the printed board model in step 1 before verifying.";
  if (device.productName !== "ga100ctl/" + board + "/1.0.0") {
    return "Unexpected product string for " + boards[board].name + ". Check the board label and selected image.";
  }
  if (!bootSerial || !device.serialNumber) return "Board target and version match. Same-device verification requires a serial from both USB modes.";
  if (bootSerial.toLowerCase() !== device.serialNumber.toLowerCase()) return "Serial mismatch. This device differs from the one selected in BOOTSEL.";
  return "Verified. The BOOTSEL serial, board target and firmware version match.";
}

async function inspect(mode) {
  const output = outputs[mode];
  const ids = mode === "either" ? [romPid, runtimePid] : [mode === "rom" ? romPid : runtimePid];
  readButtons.forEach(button => { button.disabled = true; });
  try {
    const device = await navigator.usb.requestDevice({filters: ids.map(productId => ({vendorId, productId}))});
    if (device.vendorId !== vendorId || !ids.includes(device.productId)) throw new Error("The selected device does not match this step.");
    const summary = [hexId(device.vendorId) + ":" + hexId(device.productId), device.productName || "Unknown product", "Serial " + (device.serialNumber || "unavailable")].join(" · ");
    let message = summary;
    if (mode === "rom") {
      bootSerial = device.serialNumber || "";
      outputs.runtime.textContent = "Waiting for a runtime device.";
      message = "Bootloader observed. " + summary;
    } else if (mode === "runtime") {
      message = compareRuntime(device) + "\n" + summary;
    }
    output.textContent = message;
    if (mode !== "either") outputs.either.textContent = "Last observation\n" + summary;
  } catch (error) {
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
