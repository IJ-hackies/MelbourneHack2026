export const themeInitScript = `
(function () {
  try {
    var stored = localStorage.getItem("hr-theme");
    if (stored === "light" || stored === "dark") {
      document.documentElement.setAttribute("data-theme", stored);
    }
  } catch (e) {}
})();
`;
