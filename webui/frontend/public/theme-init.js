// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Apply the saved theme before the first paint. Dark is the default.
(function () {
  var t = "dark";
  try { if (localStorage.getItem("rbh-theme") === "light") t = "light"; } catch (e) {}
  document.documentElement.setAttribute("data-theme", t);
})();
