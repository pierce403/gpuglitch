const panels = [...document.querySelectorAll(".panel")];
const links = [...document.querySelectorAll(".steps a")];
const next = document.querySelector("#next");
const back = document.querySelector("#back");

function showStep() {
  const index = Math.max(0, panels.findIndex(panel => "#" + panel.id === location.hash));
  panels.forEach((panel, current) => {
    panel.hidden = current !== index;
    if (current === index) links[current].setAttribute("aria-current", "step");
    else links[current].removeAttribute("aria-current");
  });
  back.disabled = index === 0;
  const following = panels[(index + 1) % panels.length];
  next.href = "#" + following.id;
  next.textContent = index === panels.length - 1
    ? "Return to identification"
    : "Continue to " + links[index + 1].querySelector("span").textContent.toLowerCase();
}
showStep();
window.addEventListener("hashchange", showStep);
back.addEventListener("click", () => {
  const index = panels.findIndex(panel => !panel.hidden);
  if (index > 0) location.hash = panels[index - 1].id;
});
