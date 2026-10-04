// Small page behaviour that needs no knowledge of the application: a popover menu (<details class="menu">) closes when
// something outside it is used, when Escape is pressed, or when one of its commands has been chosen. Only one is open.
(function () {
  "use strict";
  const menus = () => Array.from(document.querySelectorAll("details.menu[open]"));

  document.addEventListener("click", event => {
    for (const menu of menus()) {
      const inside = menu.contains(event.target);
      const chose = inside && event.target.closest(".menu-pop button") && !event.target.closest("select, label");
      if (!inside || chose) menu.open = false;
    }
  });

  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    for (const menu of menus()) {
      menu.open = false;
      const summary = menu.querySelector("summary");
      if (summary) summary.focus();
    }
  });

  document.addEventListener("toggle", event => {
    if (!event.target.matches || !event.target.matches("details.menu") || !event.target.open) return;
    for (const other of menus()) if (other !== event.target) other.open = false;
  }, true);
})();
