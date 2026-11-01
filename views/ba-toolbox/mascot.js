(() => {
  "use strict";

  const host = document.getElementById("mascot-host");
  const image = document.getElementById("mascot-image");
  if (!host || !image) return;

  const characters = {
    arona: { label: "阿洛娜", src: "./assets/mascots/arona.webp" },
    plana: { label: "普拉娜", src: "./assets/mascots/plana.webp" },
  };
  let requestId = 0;

  function loadCharacter(character) {
    const config = characters[character];
    if (!config) return;
    if (
      image.dataset.character === character &&
      host.dataset.modelState !== "error"
    ) {
      return;
    }

    const currentRequestId = ++requestId;
    image.dataset.character = character;
    host.dataset.modelState = "loading";
    host.title = `${config.label}动画加载中`;
    image.onload = () => {
      if (currentRequestId !== requestId) return;
      host.dataset.modelState = "ready";
      host.title = `${config.label}动画`;
    };
    image.onerror = () => {
      if (currentRequestId !== requestId) return;
      host.dataset.modelState = "error";
      host.title = `${config.label}动画加载失败`;
      console.error(`[BA Toolbox mascot] 加载角色动画失败：${config.src}`);
    };
    image.src = config.src;
    if (image.complete && image.naturalWidth > 0) image.onload();
  }

  document.addEventListener("ba-toolbox:character-change", (event) => {
    loadCharacter(event.detail?.character);
  });
  loadCharacter(host.dataset.character || "arona");
})();
