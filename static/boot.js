(function () {
  const UI_REFRESH = "20260620redesign3";
  const FULL_APP_SRC = `/app.js?v=${UI_REFRESH}`;
  let fullAppRequested = false;
  let fullAppReady = false;

  function $(id) {
    return document.getElementById(id);
  }

  function activeParentView(viewId) {
    if (["datasetView", "workbenchView", "openvikingView", "evalView", "judgeView", "memoryView", "runsView"].includes(viewId)) {
      return "openvikingView";
    }
    return viewId;
  }

  function normalizeViewId(viewId) {
    if (viewId === "workbenchView") return "openvikingView";
    if (viewId === "genericBenchmarkView") return "evolvingEventsView";
    return viewId || "openvikingView";
  }

  function currentUrl() {
    return new URL(window.location.href);
  }

  function setStatus(text) {
    const node = $("connectionStatus");
    if (!node) return;
    node.textContent = "";
    node.hidden = true;
  }

  function showView(viewId, options) {
    if (fullAppReady) return;
    const target = viewId || "openvikingView";
    const normalized = normalizeViewId(target);
    document.body.dataset.activeView = normalized;
    document.querySelectorAll(".view-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === normalized);
    });
    var navTarget = activeParentView(normalized);
    document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
      var isMatch = button.dataset.view === navTarget;
      // When multiple items share same data-view, only activate the clicked one
      if (isMatch && lastClickedNavItem && lastClickedNavItem.dataset.view === navTarget) {
        button.classList.toggle("active", button === lastClickedNavItem);
      } else {
        button.classList.toggle("active", isMatch && !button.dataset.benchmarkSwitch);
      }
    });
    document.querySelectorAll(".flow-card[data-view-jump]").forEach((button) => {
      button.classList.toggle("active", button.dataset.viewJump === normalized);
      if (button.tagName === "A") {
        const url = currentUrl();
        url.searchParams.set("ui_refresh", UI_REFRESH);
        url.searchParams.set("view", button.dataset.viewJump || normalized);
        url.searchParams.delete("autoload");
        button.setAttribute("href", `/full.html?${url.searchParams.toString()}`);
      }
    });
    const panel = $(normalized);
    if (panel) {
      const title = $("viewTitle");
      const subtitle = $("viewSubtitle");
      if (title) title.textContent = panel.dataset.title || "";
      if (subtitle) subtitle.textContent = panel.dataset.subtitle || "";
    }
    if (!options || !options.skipUrl) {
      const url = currentUrl();
      url.searchParams.set("ui_refresh", UI_REFRESH);
      url.searchParams.set("view", normalized);
      window.history.replaceState({}, "", url);
    }
  }

  function requestFullApp(reason) {
    if (fullAppRequested || fullAppReady) return;
    fullAppRequested = true;
    setStatus(reason === "idle" ? "正在加载完整控制台..." : "正在启用完整功能...");
    const style = document.createElement("link");
    style.rel = "stylesheet";
    style.href = `/styles.css?v=${UI_REFRESH}`;
    style.media = "print";
    style.onload = () => { style.media = "all"; };
    document.head.appendChild(style);
    const script = document.createElement("script");
    script.src = FULL_APP_SRC;
    script.async = true;
    script.onload = function () {
      fullAppReady = true;
      setStatus("");
    };
    script.onerror = function () {
      fullAppRequested = false;
      setStatus("完整控制台加载失败，请刷新重试");
    };
    document.body.appendChild(script);
  }

  function currentActiveView() {
    return document.body.dataset.activeView || "openvikingView";
  }

  function shouldActivateFullAppForNode(node) {
    if (!node || fullAppReady) return false;
    const activeView = currentActiveView();
    if (activeView === "openvikingView") return false;
    if (node.closest && node.closest("[data-view-jump], .nav-item[data-view]")) return false;
    return Boolean(
      (node.closest && node.closest(
        "#evalView select, #evalView input, #evalView textarea, #evalView button, " +
        "#judgeView select, #judgeView input, #judgeView textarea, #judgeView button, " +
        "#runsView select, #runsView input, #runsView textarea, #runsView button, " +
        "#memoryView select, #memoryView input, #memoryView textarea, #memoryView button",
      ))
    );
  }

  function queueFullAppFromInteraction() {
    if (fullAppReady) return;
    setTimeout(function () {
      requestFullApp("interaction");
    }, 32);
  }

  var lastClickedNavItem = null;

  function bindMinimalNav() {
    document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
      if (fullAppReady) return;
      button.addEventListener("click", function () {
        if (fullAppReady) return;
        lastClickedNavItem = button;
        const target = button.dataset.view || "openvikingView";
        showView(target);
        if (!fullAppReady && target !== "openvikingView") {
          setStatus("加载中...");
          requestFullApp("interaction");
        }
      });
    });
    document.querySelectorAll("[data-view-jump]").forEach((button) => {
      button.addEventListener("click", function (event) {
        if (fullAppReady) return;
        if (button.tagName === "A") event.preventDefault();
        const target = button.dataset.viewJump || "openvikingView";
        showView(target);
        if (!fullAppReady && target !== "openvikingView") {
          setStatus("加载中...");
          requestFullApp("interaction");
        }
      });
    });
    document.addEventListener(
      "click",
      function (event) {
        if (fullAppReady) return;
        const targetNode = event.target && typeof event.target.closest === "function"
          ? event.target.closest("[data-view-jump], .nav-item[data-view]")
          : null;
        if (!targetNode) return;
        const target = targetNode.dataset.viewJump || targetNode.dataset.view || "";
        if (!target) return;
        if (targetNode.tagName === "A" && targetNode.getAttribute("href")) event.preventDefault();
        showView(target);
        if (!fullAppReady && target !== "openvikingView") {
          requestFullApp("interaction");
        }
      },
      true,
    );
    document.addEventListener(
      "click",
      function (event) {
        if (fullAppReady) return;
        if (shouldActivateFullAppForNode(event.target)) {
          queueFullAppFromInteraction();
        }
      },
      true,
    );
    document.addEventListener(
      "change",
      function (event) {
        if (fullAppReady) return;
        if (shouldActivateFullAppForNode(event.target)) {
          queueFullAppFromInteraction();
        }
      },
      true,
    );
    document.addEventListener(
      "focusin",
      function (event) {
        if (shouldActivateFullAppForNode(event.target)) {
          queueFullAppFromInteraction();
        }
      },
      true,
    );
    const loadButton = $("loadFullAppButton");
    if (loadButton) {
      loadButton.addEventListener("click", function () {
        if (fullAppReady) return;
        requestFullApp("interaction");
      });
    }
  }

  function shouldAutoLoadFullApp() {
    return false;
  }

  function boot() {
    const url = currentUrl();
    const view = url.searchParams.get("view") || "openvikingView";
    showView(view, {skipUrl: false});
    bindMinimalNav();
    setStatus("");
    if (shouldAutoLoadFullApp()) {
      setTimeout(() => requestFullApp("idle"), 120);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, {once: true});
  } else {
    boot();
  }
})();
