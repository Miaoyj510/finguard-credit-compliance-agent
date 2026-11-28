/* FinGuard 界面交互（原生 JS，无依赖） */
(function () {
  "use strict";

  /* ---------- 轻提示 ---------- */
  var toastEl = null;
  function toast(msg) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "toast";
      toastEl.setAttribute("role", "status");
      toastEl.setAttribute("aria-live", "polite");
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    window.clearTimeout(toast._t);
    toast._t = window.setTimeout(function () { toastEl.classList.remove("show"); }, 2600);
  }

  /* ---------- 闸门倒计时 ---------- */
  function tickTimers() {
    var nodes = document.querySelectorAll("[data-deadline]");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var end = new Date(el.getAttribute("data-deadline")).getTime();
      var now = new Date(el.getAttribute("data-now")).getTime();
      var left = Math.floor((end - now) / 3600000);
      el.textContent = (left > 0 ? "剩余 " + left + " 小时" : "已超时 " + Math.abs(left) + " 小时");
      el.classList.toggle("overdue", left <= 0);
    }
  }

  /* ---------- 预警等级筛选 ---------- */
  function initFilter() {
    var seg = document.querySelector("[data-filter-level]");
    if (!seg) return;
    var rows = document.querySelectorAll("[data-level]");
    seg.addEventListener("click", function (ev) {
      var btn = ev.target.closest("button[data-level-target]");
      if (!btn) return;
      var level = btn.getAttribute("data-level-target");
      for (var i = 0; i < seg.querySelectorAll("button").length; i++) {
        seg.querySelectorAll("button")[i].setAttribute("aria-pressed", "false");
      }
      btn.setAttribute("aria-pressed", "true");
      for (var j = 0; j < rows.length; j++) {
        var ok = level === "all" || rows[j].getAttribute("data-level") === level;
        rows[j].style.display = ok ? "" : "none";
      }
      var live = document.getElementById("filter-status");
      if (live) {
        var n = document.querySelectorAll('[data-level]:not([style*="display: none"])').length;
        live.textContent = (level === "all" ? "全部" : level) + "级别预警 " + n + " 条";
      }
    });
  }

  /* ---------- 签核 / 处置动作（原型：本地反馈，不落库） ---------- */
  function initActions() {
    document.addEventListener("click", function (ev) {
      var sign = ev.target.closest("[data-action='sign']");
      if (sign) {
        var gate = sign.getAttribute("data-gate") || "GATE-A";
        var bar = sign.closest(".gate-bar");
        if (bar) {
          var badge = bar.querySelector("[data-gate-state]");
          if (badge) {
            badge.className = "badge badge-success";
            badge.textContent = "已签核";
          }
          var btn = bar.querySelector("[data-action='sign']");
          if (btn) { btn.disabled = true; btn.textContent = "已签核"; }
        }
        toast(gate + " 已签核（原型演示，未落库）");
        return;
      }
      var amend = ev.target.closest("[data-action='amend']");
      if (amend) {
        toast("进入「改写并留痕」——原始意见与修改后意见将双版本入卷宗");
        return;
      }
      var dismiss = ev.target.closest("[data-action='dismiss']");
      if (dismiss) {
        toast("已发起驳回补件：该条意见转为「未判定」，需补件后重跑");
        return;
      }
      var disp = ev.target.closest("[data-action='dispose']");
      if (disp) {
        var label = disp.getAttribute("data-disposition");
        var id = disp.getAttribute("data-alert");
        var wrap = disp.closest("[data-disposition-group]");
        if (wrap) {
          var btns = wrap.querySelectorAll("button");
          for (var i = 0; i < btns.length; i++) {
            btns[i].setAttribute("aria-pressed", "false");
            btns[i].classList.remove("btn-primary");
          }
          disp.setAttribute("aria-pressed", "true");
          disp.classList.add("btn-primary");
        }
        toast("预警 " + id + " 拟处置为「" + label + "」，须经 GATE-C 签核后方可生效");
        return;
      }
    });
  }

  /* ---------- 裁决表单：理由必填（就近报错） ---------- */
  function initAdjudication() {
    var form = document.querySelector("[data-adjudication]");
    if (!form) return;
    var reason = form.querySelector("textarea");
    var err = form.querySelector(".err");
    var submit = form.querySelector("[data-action='adjudicate']");
    function validate() {
      var ok = reason.value.trim().length > 0;
      if (err) err.classList.toggle("show", !ok);
      if (submit) submit.disabled = !ok;
      return ok;
    }
    reason.addEventListener("input", validate);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (!validate()) { reason.focus(); return; }
      var picked = form.querySelector("input[name='value']:checked");
      toast("裁决已提交：" + (picked ? picked.value : "") + "（原型演示）");
    });
    validate();
  }

  /* ---------- 时间轴：闸门节点不可跳过 ---------- */
  function initTimeline() {
    var nodes = document.querySelectorAll(".tl-node");
    for (var i = 0; i < nodes.length; i++) {
      (function (node) {
        node.addEventListener("click", function () {
          if (node.getAttribute("aria-disabled") === "true") {
            toast("人工闸门：不可绕过，请查看该闸门的签核与升级记录");
            return;
          }
          var all = document.querySelectorAll(".tl-node");
          for (var j = 0; j < all.length; j++) { all[j].setAttribute("aria-current", "false"); }
          node.setAttribute("aria-current", "true");
          var panel = document.getElementById("snapshot-title");
          if (panel) panel.textContent = node.getAttribute("data-label") || node.textContent.trim();
        });
      })(nodes[i]);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    tickTimers();
    initFilter();
    initActions();
    initAdjudication();
    initTimeline();
  });
})();