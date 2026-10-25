// Власні силуети цілей (64×64, ніс угору — поворот робить карта за курсом).
"use strict";
const TYPE_META = {
  uav: { label: "БпЛА", color: "#f0820e" },
  fpv: { label: "FPV", color: "#f0820e" },
  missile: { label: "Ракети", color: "#e2384f" },
  ballistic: { label: "Балістика", color: "#b21e6b" },
  kab: { label: "КАБ", color: "#d9531e" },
  mig31k: { label: "Авіація", color: "#7c4dff" },
  recon: { label: "Розвідка", color: "#1f9bd8" },
  unknown: { label: "Невідомі", color: "#6b7790" },
};
const TYPE_TITLE = {
  uav: "БпЛА", fpv: "FPV-дрон", missile: "Крилата ракета", ballistic: "Балістична ракета", kab: "КАБ",
  mig31k: "Авіація (МіГ-31К)", recon: "Розвідувальний БпЛА", unknown: "Невідома ціль",
};

const SHAPES = {
  // шахед: обрізана дельта з кілями на кінцях крил
  uav: "M32 5 L35.5 15 L35.5 24 L56 46 L57 53 L52 53 L50 50 L36 46 L35 56 L39 60 L25 60 L29 56 L28 46 L14 50 L12 53 L7 53 L8 46 L28.5 24 L28.5 15 Z",
  // FPV: квадрокоптер
  fpv: "M27 27 L19 19 A6 6 0 1 1 23 15 L31 23 H33 L41 15 A6 6 0 1 1 45 19 L37 27 V37 L45 45 A6 6 0 1 1 41 49 L33 41 H31 L23 49 A6 6 0 1 1 19 45 L27 37 Z",
  // крилата ракета: корпус, крила посередині, хвостове оперення
  missile: "M32 3 L35.5 12 L35.5 26 L47 34 L47 38 L35.5 35 L35.5 50 L42 57 L42 61 L32 58 L22 61 L22 57 L28.5 50 L28.5 35 L17 38 L17 34 L28.5 26 L28.5 12 Z",
  // балістика: товстий корпус, гострий ніс, чотири стабілізатори
  ballistic: "M32 2 L37.5 14 L37.5 46 L44 54 L44 61 L37.5 57 L26.5 57 L20 61 L20 54 L26.5 46 L26.5 14 Z",
  // КАБ: краплеподібна бомба з хрестоподібним хвостом
  kab: "M32 6 C38 6 40 14 40 24 L40 42 L46 50 L46 57 L36 53 L32 60 L28 53 L18 57 L18 50 L24 42 L24 24 C24 14 26 6 32 6 Z",
  // літак: стрілоподібні крила, два кілі
  mig31k: "M32 3 L35 12 L35 24 L56 38 L56 43 L35 38 L35 48 L44 56 L44 60 L34 58 L32 61 L30 58 L20 60 L20 56 L29 48 L29 38 L8 43 L8 38 L29 24 L29 12 Z",
  // розвідник: пряме довге крило, хвостове оперення
  recon: "M32 8 L34.5 13 L34.5 24 L60 26 L60 31 L34.5 31 L34.5 50 L43 53 L43 57 L32 56 L21 57 L21 53 L29.5 50 L29.5 31 L4 31 L4 26 L29.5 24 L29.5 13 Z",
  unknown: "M32 10 A22 22 0 1 1 31.9 10 Z",
};

function shapeSvg(type, { fill = "#fff", stroke = "#0b1220", sw = 4 } = {}) {
  const d = SHAPES[type] || SHAPES.unknown;
  const extra = type === "unknown"
    ? `<text x="32" y="42" text-anchor="middle" font-size="28" font-weight="700" font-family="Arial" fill="${stroke}">?</text>` : "";
  return `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">` +
    `<path d="${d}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}" stroke-linejoin="round" paint-order="stroke"/>${extra}</svg>`;
}

function svgUrl(svg) { return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg); }

// Плитка для списку: білий силует на кольоровому тлі
function tileHtml(type) {
  const c = (TYPE_META[type] || TYPE_META.unknown).color;
  const img = svgUrl(shapeSvg(type, { fill: "#fff", stroke: "none", sw: 0 }));
  return `<span class="tile" style="background:${c}"><img alt="" src="${img}"></span>`;
}

// Зображення для MapLibre (з темним контуром, як у NEPTUN)
function loadMapIcons(map) {
  const jobs = Object.keys(SHAPES).map((type) => new Promise((resolve) => {
    const img = new Image(64, 64);
    img.onload = () => { if (!map.hasImage("ti-" + type)) map.addImage("ti-" + type, img, { pixelRatio: 2 }); resolve(); };
    img.onerror = resolve;
    img.src = svgUrl(shapeSvg(type));
  }));
  // шеврон курсу
  jobs.push(new Promise((resolve) => {
    const img = new Image(32, 32);
    img.onload = () => { if (!map.hasImage("ti-course")) map.addImage("ti-course", img, { pixelRatio: 2 }); resolve(); };
    img.src = svgUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">` +
      `<path d="M8 20 L16 11 L24 20" fill="none" stroke="#0b1220" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>` +
      `<path d="M8 20 L16 11 L24 20" fill="none" stroke="#fff" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`);
  }));
  return Promise.all(jobs);
}
