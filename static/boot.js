(function () {
  const UI_REFRESH = "20260629hotpotworkbench1";
  const FULL_APP_STATE_SRC = `/app-state.js?v=${UI_REFRESH}`;
  const FULL_APP_CORE_SRC = `/app-core.js?v=${UI_REFRESH}`;
  const FULL_APP_FORMAT_SRC = `/app-format.js?v=${UI_REFRESH}`;
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

  function minimalHotpotSection(sectionId) {
    if (!sectionId) return null;
    return $(sectionId) || document.querySelector(`#hotpotQaView #${sectionId}`);
  }

  function syncMinimalChrome(viewId) {
    const isHotpotQaView = viewId === "hotpotQaView";
    const shell = document.querySelector(".app-shell");
    const sidebar = document.querySelector(".app-sidebar");
    const appContent = document.querySelector(".app-content");
    const topbarBrand = document.querySelector(".app-topbar-brand");
    const flowNav = $("locomoFlowNav");
    const overviewPanel = $("locomoOverviewPanel");
    const statusAction = document.querySelector(".workspace-action-status");

    if (shell) shell.style.gridTemplateColumns = isHotpotQaView ? "minmax(0,1fr)" : "";
    if (sidebar) sidebar.hidden = isHotpotQaView;
    if (appContent) appContent.style.width = isHotpotQaView ? "min(1480px,100vw)" : "";
    if (topbarBrand) {
      topbarBrand.hidden = isHotpotQaView;
      topbarBrand.style.pointerEvents = isHotpotQaView ? "none" : "";
    }
    if (flowNav) flowNav.hidden = isHotpotQaView;
    if (overviewPanel) overviewPanel.hidden = isHotpotQaView;
    if (statusAction) statusAction.hidden = isHotpotQaView;
  }

  function applyMinimalHotpotStage(options) {
    const panel = $("hotpotQaView");
    if (!panel) return;
    const stage = String(
      options?.benchmarkStage || options?.hotpotStage || panel.dataset.activeBenchmarkStage || "import",
    ).trim() || "import";
    panel.dataset.activeBenchmarkStage = stage;
    panel.querySelectorAll(".benchmark-stage-tab").forEach((button) => {
      const active = String(button.dataset.flowKey || button.dataset.hotpotStage || "").trim() === stage;
      button.classList.toggle("active", active);
      button.classList.toggle("is-selected", active);
      button.setAttribute("aria-current", active ? "step" : "false");
    });
    const section = minimalHotpotSection(String(options?.hotpotQaSection || "").trim());
    if (section) {
      window.setTimeout(() => {
        section.scrollIntoView({behavior: "smooth", block: "start"});
      }, 20);
    }
  }

  function resolveMinimalViewJump(button) {
    const targetView = String(button?.dataset?.viewJump || button?.dataset?.view || "").trim() || "openvikingView";
    const activeView = currentActiveView();
    const options = {};
    if (button?.dataset?.flowKey) options.benchmarkStage = button.dataset.flowKey;
    if (button?.dataset?.hotpotStage) options.hotpotStage = button.dataset.hotpotStage;
    if (button?.dataset?.hotpotSection) options.hotpotQaSection = button.dataset.hotpotSection;
    const hotpotTagged = Boolean(button?.dataset?.hotpotSection || button?.dataset?.hotpotStage);
    const hotpotScoped = Boolean(button?.closest?.("#hotpotQaView"));
    const fromHotpotQaView = activeView === "hotpotQaView" || hotpotTagged || hotpotScoped;
    if (fromHotpotQaView && ["openvikingView", "evalView", "judgeView", "runsView"].includes(targetView)) {
      const hint = [
        targetView,
        button?.dataset?.hotpotSection,
        button?.dataset?.hotpotStage,
        button?.id,
        button?.className,
        button?.textContent,
        button?.getAttribute?.("aria-label"),
        button?.getAttribute?.("title"),
      ].map((value) => String(value || "").trim().toLowerCase()).join(" ");
      const importJump = targetView === "openvikingView" || /写入准备|文档写入|记忆导入|启动前检查|dataset/.test(hint);
      const currentTaskJump = /运行状态|查看任务/.test(hint);
      const qaJump = targetView === "evalView" || /问答测试|运行 qa|运行问答|去问答测试|进入问答测试/.test(hint);
      if (!options.benchmarkStage && !options.hotpotStage) {
        options.benchmarkStage = importJump ? "import" : (currentTaskJump || qaJump ? "qa" : "report");
      }
      if (!options.hotpotQaSection) {
        options.hotpotQaSection = importJump
          ? "hotpotQaConfigSection"
          : currentTaskJump
          ? "hotpotQaCurrentSection"
          : (qaJump ? "hotpotQaQaSection" : "hotpotQaResultSection");
      }
      return {viewId: "hotpotQaView", options};
    }
    return {viewId: targetView, options};
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
    syncMinimalChrome(normalized);
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
    document.querySelectorAll(".locomo-flow-tab[data-view-jump], .flow-card[data-view-jump]").forEach((button) => {
      button.classList.toggle("active", button.dataset.viewJump === normalized);
      if (button.tagName === "A") {
        const url = currentUrl();
        url.searchParams.set("ui_refresh", UI_REFRESH);
        url.searchParams.set("view", button.dataset.viewJump || normalized);
        url.searchParams.delete("autoload");
        button.setAttribute("href", `/?${url.searchParams.toString()}`);
      }
    });
    const panel = $(normalized);
    if (panel) {
      const title = $("viewTitle");
      const subtitle = $("viewSubtitle");
      if (title) title.textContent = panel.dataset.title || "";
      if (subtitle) subtitle.textContent = panel.dataset.subtitle || "";
    }
    if (normalized === "hotpotQaView") {
      applyMinimalHotpotStage(options || {});
    }
    if (!options || !options.skipUrl) {
      const url = currentUrl();
      url.searchParams.set("ui_refresh", UI_REFRESH);
      url.searchParams.set("view", normalized);
      if (normalized === "hotpotQaView") {
        const stage = String(options?.benchmarkStage || options?.hotpotStage || "").trim();
        const section = String(options?.hotpotQaSection || "").trim();
        if (stage) url.searchParams.set("benchmark_stage", stage);
        else url.searchParams.delete("benchmark_stage");
        if (section) url.searchParams.set("hotpot_section", section);
        else url.searchParams.delete("hotpot_section");
      } else {
        url.searchParams.delete("benchmark_stage");
        url.searchParams.delete("hotpot_section");
      }
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
    const stateScript = document.createElement("script");
    stateScript.src = FULL_APP_STATE_SRC;
    stateScript.async = true;
    stateScript.onload = function () {
      const coreScript = document.createElement("script");
      coreScript.src = FULL_APP_CORE_SRC;
      coreScript.async = true;
      coreScript.onload = function () {
        const formatScript = document.createElement("script");
        formatScript.src = FULL_APP_FORMAT_SRC;
        formatScript.async = true;
        formatScript.onload = function () {
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
        };
        formatScript.onerror = function () {
          fullAppRequested = false;
          setStatus("完整控制台加载失败，请刷新重试");
        };
        document.body.appendChild(formatScript);
      };
      coreScript.onerror = function () {
        fullAppRequested = false;
        setStatus("完整控制台加载失败，请刷新重试");
      };
      document.body.appendChild(coreScript);
    };
    stateScript.onerror = function () {
      fullAppRequested = false;
      setStatus("完整控制台加载失败，请刷新重试");
    };
    document.body.appendChild(stateScript);
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
        const resolved = resolveMinimalViewJump(button);
        showView(resolved.viewId, resolved.options);
        if (!fullAppReady && resolved.viewId !== "openvikingView") {
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
        const resolved = targetNode.matches?.(".nav-item[data-view]")
          ? {viewId: targetNode.dataset.view || "", options: {}}
          : resolveMinimalViewJump(targetNode);
        const target = resolved.viewId || "";
        if (!target) return;
        if (targetNode.tagName === "A" && targetNode.getAttribute("href")) event.preventDefault();
        showView(target, resolved.options);
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
    const view = currentUrl().searchParams.get("view") || "openvikingView";
    return normalizeViewId(view) !== "openvikingView";
  }

  function boot() {
    const url = currentUrl();
    const view = url.searchParams.get("view") || "openvikingView";
    showView(view, {
      skipUrl: false,
      benchmarkStage: url.searchParams.get("benchmark_stage") || "",
      hotpotQaSection: url.searchParams.get("hotpot_section") || "",
    });
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
