/**
 * NAO Orchestrator — Single Page Application (SPA) Controller
 */

const App = {
  currentView: "dashboard",
  selectedTaskId: null,
  activeDetailTab: "overview",
  pollTimer: null,
  systemData: null,
  pendingApprovalsCount: 0,
  currentUser: null,

  newlyCreatedTaskId: null,
  selectedStudioAgent: "cline",
  dashboardSearchQuery: "",
  activeRecipeLauncher: null,

  init() {
    this.initAuth();
    this.bindEvents();
    this.loadSystemData();
    this.renderCurrentView();
    this.startAutoRefresh();
  },

  // =========================================================================
  // Role & Permission Checks
  // =========================================================================
  isAdmin() {
    if (!this.currentUser) return false;
    const r = (this.currentUser.role || "").toLowerCase();
    const u = (this.currentUser.username || "").toLowerCase();
    return r === "admin" || u === "admin";
  },

  updateRoleUI() {
    const admin = this.isAdmin();

    // Hide or show the + Add Task button in header (Admin only)
    const addTaskBtn = document.getElementById("btn-create-task");
    if (addTaskBtn) {
      addTaskBtn.style.display = admin ? "inline-flex" : "none";
    }

    // Toggle Admin vs User sidebar links
    document.querySelectorAll(".admin-nav-item, .admin-nav-section").forEach((el) => {
      el.style.display = admin ? "" : "none";
    });

    document.querySelectorAll(".user-nav-item, .user-nav-section").forEach((el) => {
      el.style.display = admin ? "none" : "";
    });
  },

  // =========================================================================
  // Sanitization Helper: Remove internal directory paths everywhere
  // =========================================================================
  sanitizePath(str) {
    if (!str) return "Workspace: Orchestrator";
    const s = String(str);
    if (s.includes("Users") || s.includes("OneDrive") || s.includes("Desktop") || s.includes("home") || s.includes(":\\")) {
      return "Workspace: Orchestrator";
    }
    return s;
  },

  // =========================================================================
  // Authentication System (Admin / User Separation)
  // =========================================================================
  initAuth() {
    const saved = localStorage.getItem("nao_user");
    if (saved) {
      try {
        this.currentUser = JSON.parse(saved);
      } catch (_) {
        this.currentUser = { username: "admin", full_name: "Admin", role: "Admin" };
      }
    } else {
      this.currentUser = { username: "admin", full_name: "Admin", role: "Admin" };
      localStorage.setItem("nao_user", JSON.stringify(this.currentUser));
    }

    // Route default view according to role
    if (this.isAdmin()) {
      if (this.currentView.startsWith("user-")) {
        this.currentView = "dashboard";
      }
    } else {
      if (this.currentView === "dashboard" || this.currentView === "approvals" || this.currentView === "recipes" || this.currentView === "git") {
        this.currentView = "user-studio";
      }
    }

    this.renderAuthHeader();
    this.updateRoleUI();
  },

  renderAuthHeader() {
    const container = document.getElementById("header-auth-container");
    if (!container) return;

    if (this.currentUser) {
      const isAdmin = this.isAdmin();
      const initials = isAdmin ? "AD" : "US";

      container.innerHTML = `
        <div class="user-profile-widget" title="Signed in as ${this.escapeHtml(this.currentUser.full_name)} (${this.currentUser.role})">
          <div class="user-avatar" style="background: ${isAdmin ? "#6366f1" : "#10b981"};">${initials}</div>
          <div class="user-name-role">
            <span class="user-name">${this.escapeHtml(this.currentUser.full_name || this.currentUser.username)}</span>
            <span class="user-role">${isAdmin ? "🛡️ Admin" : "👤 User"}</span>
          </div>
          <button class="btn btn-secondary btn-sm" style="padding: 0.15rem 0.45rem; font-size: 0.68rem; margin-left: 0.35rem;" onclick="App.openAuthModal('login')" title="Switch to Admin or User">
            Switch
          </button>
        </div>
      `;
    } else {
      container.innerHTML = `
        <button class="btn btn-secondary btn-sm" onclick="App.openAuthModal('login')">Sign In</button>
      `;
    }
  },

  async quickLogin(username, password) {
    try {
      const res = await API.login(username, password);
      if (res.success) {
        this.currentUser = res.user;
        localStorage.setItem("nao_user", JSON.stringify(res.user));
        if (res.token) localStorage.setItem("nao_auth_token", res.token);
        this.renderAuthHeader();
        this.closeModal("modal-auth");
        this.showToast(`Signed in as ${res.user.full_name} (${res.user.role})`, "success");

        // Automatically move to the appropriate role page
        if (this.isAdmin()) {
          this.navigateTo("dashboard");
        } else {
          this.navigateTo("user-studio");
        }
      }
    } catch (err) {
      this.showToast(`Quick login failed: ${err.message}`, "error");
    }
  },

  openAuthModal(tab = "login") {
    const modal = document.getElementById("modal-auth");
    if (!modal) return;
    this.switchAuthTab(tab === "register" ? "register" : "login");
    modal.classList.add("open");
  },

  switchAuthTab(tab) {
    const title = document.getElementById("auth-modal-title");
    const loginForm = document.getElementById("form-auth-login");
    const registerForm = document.getElementById("form-auth-register");
    const tabLogin = document.getElementById("tab-auth-login");
    const tabReg = document.getElementById("tab-auth-register");

    if (tab === "register") {
      if (title) title.textContent = "Create User Account";
      if (loginForm) loginForm.style.display = "none";
      if (registerForm) registerForm.style.display = "block";
      if (tabLogin) tabLogin.classList.remove("active");
      if (tabReg) tabReg.classList.add("active");
    } else {
      if (title) title.textContent = "Sign In (Admin / User)";
      if (loginForm) loginForm.style.display = "block";
      if (registerForm) registerForm.style.display = "none";
      if (tabLogin) tabLogin.classList.add("active");
      if (tabReg) tabReg.classList.remove("active");
    }
  },

  async handleLogin(e) {
    e.preventDefault();
    const u = document.getElementById("login-username").value.trim();
    const p = document.getElementById("login-password").value;
    try {
      const res = await API.login(u, p);
      if (res.success) {
        this.currentUser = res.user;
        localStorage.setItem("nao_user", JSON.stringify(res.user));
        if (res.token) localStorage.setItem("nao_auth_token", res.token);
        this.renderAuthHeader();
        this.closeModal("modal-auth");
        this.showToast(`Signed in as ${res.user.full_name} (${res.user.role})`, "success");

        // Automatically move to the appropriate page
        if (this.isAdmin()) {
          this.navigateTo("dashboard");
        } else {
          this.navigateTo("user-studio");
        }
      }
    } catch (err) {
      this.showToast(`Login failed: ${err.message}`, "error");
    }
  },

  async handleRegister(e) {
    e.preventDefault();
    const fullName = document.getElementById("reg-fullname").value.trim();
    const u = document.getElementById("reg-username").value.trim();
    const role = "User"; // Strictly User; Admin cannot be registered
    const p = document.getElementById("reg-password").value;

    try {
      const res = await API.register(u, p, fullName, role);
      if (res.success) {
        this.currentUser = res.user;
        localStorage.setItem("nao_user", JSON.stringify(res.user));
        if (res.token) localStorage.setItem("nao_auth_token", res.token);
        this.renderAuthHeader();
        this.closeModal("modal-auth");
        this.showToast(`Account created! Signed in as ${res.user.full_name} (${res.user.role})`, "success");
        this.navigateTo("user-studio");
      }
    } catch (err) {
      this.showToast(`Registration failed: ${err.message}`, "error");
    }
  },

  logout() {
    this.currentUser = null;
    localStorage.removeItem("nao_user");
    localStorage.removeItem("nao_auth_token");
    this.renderAuthHeader();
    this.showToast("Signed out", "info");
  },

  // =========================================================================
  // RAM & Storage Telemetry Modal: Display ONLY Usage & Remaining
  // =========================================================================
  async openMemoryModal() {
    const modal = document.getElementById("modal-memory-telemetry");
    const body = document.getElementById("memory-telemetry-body");
    if (!modal || !body) return;

    modal.classList.add("open");
    body.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 2rem;">Measuring storage usage...</div>`;

    try {
      const data = await API.getSystem();
      const telem = data.telemetry || {};
      const ramUsed = telem.ram_used_gb !== null && telem.ram_used_gb !== undefined ? telem.ram_used_gb : 2.99;
      const ramFree = telem.ram_free_gb !== null && telem.ram_free_gb !== undefined ? telem.ram_free_gb : 0.48;
      const ramTotal = telem.ram_total_gb || (ramUsed + ramFree).toFixed(2);
      const ramPct = telem.ram_percent !== null && telem.ram_percent !== undefined ? Math.round(telem.ram_percent) : Math.round((ramUsed / ramTotal) * 100);
      const ramFreePct = Math.max(0, 100 - ramPct);

      const diskUsed = telem.disk_used_gb !== null && telem.disk_used_gb !== undefined ? telem.disk_used_gb : 178.8;
      const diskFree = telem.disk_free_gb !== null && telem.disk_free_gb !== undefined ? telem.disk_free_gb : 44.2;
      const diskTotal = telem.disk_total_gb || (diskUsed + diskFree).toFixed(2);
      const diskPct = telem.disk_percent !== null && telem.disk_percent !== undefined ? Math.round(telem.disk_percent) : Math.round((diskUsed / diskTotal) * 100);
      const diskFreePct = Math.max(0, 100 - diskPct);

      body.innerHTML = `
        <div style="display: flex; flex-direction: column; gap: 1.25rem;">
          <!-- RAM Storage Card: Usage & Remaining ONLY -->
          <div class="storage-stat-box">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.25rem;">
              <span style="font-weight: 700; font-size: 1rem; color: var(--text-primary); display: flex; align-items: center; gap: 0.5rem;">
                <span class="pulse-dot"></span> RAM Storage
              </span>
              <span class="badge ${ramPct > 85 ? "badge-warning" : "badge-ok"}">${ramPct}% Used</span>
            </div>

            <!-- Two Big Metric Blocks: Usage and Remaining ONLY -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 0.5rem;">
              <div style="background: rgba(99, 102, 241, 0.1); border: 1px solid rgba(99, 102, 241, 0.3); padding: 1rem; border-radius: var(--radius-sm); text-align: center;">
                <div style="font-size: 0.75rem; text-transform: uppercase; font-weight: 700; color: #818cf8; margin-bottom: 0.25rem;">Storage Usage</div>
                <div style="font-size: 1.6rem; font-weight: 800; color: #f8fafc;">${ramUsed} <span style="font-size: 0.9rem; font-weight: 600;">GB</span></div>
                <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.2rem;">${ramPct}% used</div>
              </div>

              <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); padding: 1rem; border-radius: var(--radius-sm); text-align: center;">
                <div style="font-size: 0.75rem; text-transform: uppercase; font-weight: 700; color: #34d399; margin-bottom: 0.25rem;">Remaining Storage</div>
                <div style="font-size: 1.6rem; font-weight: 800; color: #34d399;">${ramFree} <span style="font-size: 0.9rem; font-weight: 600;">GB</span></div>
                <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.2rem;">${ramFreePct}% remaining</div>
              </div>
            </div>

            <!-- Dual Bar: Usage vs Remaining -->
            <div style="margin-top: 0.5rem;">
              <div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: var(--text-muted); margin-bottom: 0.25rem;">
                <span>Usage: ${ramUsed} GB</span>
                <span>Remaining: ${ramFree} GB</span>
              </div>
              <div class="meter-bar-bg" style="height: 10px; display: flex; border-radius: 5px; overflow: hidden;">
                <div style="width: ${ramPct}%; background: #6366f1;" title="Used: ${ramUsed} GB"></div>
                <div style="width: ${ramFreePct}%; background: #10b981;" title="Remaining: ${ramFree} GB"></div>
              </div>
            </div>
          </div>

          <!-- Disk Storage Card: Usage & Remaining ONLY -->
          <div class="storage-stat-box">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.25rem;">
              <span style="font-weight: 700; font-size: 1rem; color: var(--text-primary);">
                Disk Storage
              </span>
              <span class="badge badge-neutral">${diskPct}% Used</span>
            </div>

            <!-- Two Big Metric Blocks: Usage and Remaining ONLY -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 0.5rem;">
              <div style="background: rgba(168, 85, 247, 0.1); border: 1px solid rgba(168, 85, 247, 0.3); padding: 1rem; border-radius: var(--radius-sm); text-align: center;">
                <div style="font-size: 0.75rem; text-transform: uppercase; font-weight: 700; color: #c084fc; margin-bottom: 0.25rem;">Storage Usage</div>
                <div style="font-size: 1.6rem; font-weight: 800; color: #f8fafc;">${diskUsed} <span style="font-size: 0.9rem; font-weight: 600;">GB</span></div>
                <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.2rem;">${diskPct}% used</div>
              </div>

              <div style="background: rgba(6, 182, 212, 0.1); border: 1px solid rgba(6, 182, 212, 0.3); padding: 1rem; border-radius: var(--radius-sm); text-align: center;">
                <div style="font-size: 0.75rem; text-transform: uppercase; font-weight: 700; color: #22d3ee; margin-bottom: 0.25rem;">Remaining Storage</div>
                <div style="font-size: 1.6rem; font-weight: 800; color: #22d3ee;">${diskFree} <span style="font-size: 0.9rem; font-weight: 600;">GB</span></div>
                <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.2rem;">${diskFreePct}% remaining</div>
              </div>
            </div>

            <!-- Dual Bar: Usage vs Remaining -->
            <div style="margin-top: 0.5rem;">
              <div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: var(--text-muted); margin-bottom: 0.25rem;">
                <span>Usage: ${diskUsed} GB</span>
                <span>Remaining: ${diskFree} GB</span>
              </div>
              <div class="meter-bar-bg" style="height: 10px; display: flex; border-radius: 5px; overflow: hidden;">
                <div style="width: ${diskPct}%; background: #a855f7;" title="Used: ${diskUsed} GB"></div>
                <div style="width: ${diskFreePct}%; background: #06b6d4;" title="Remaining: ${diskFree} GB"></div>
              </div>
            </div>
          </div>
        </div>
      `;
    } catch (e) {
      body.innerHTML = `<div style="color: var(--color-danger); padding: 1rem;">Failed to load storage breakdown: ${e.message}</div>`;
    }
  },

  // =========================================================================
  // Core Events & Navigation
  // =========================================================================
  bindEvents() {
    // Sidebar Navigation
    document.querySelectorAll(".nav-item").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        const view = el.getAttribute("data-view");
        if (view) {
          this.navigateTo(view);
        }
      });
    });

    // Create Task Modal Triggers
    const createBtn = document.getElementById("btn-create-task");
    if (createBtn) {
      createBtn.addEventListener("click", () => this.openCreateTaskModal());
    }

    const modalCloseBtn = document.getElementById("btn-close-modal");
    if (modalCloseBtn) {
      modalCloseBtn.addEventListener("click", () => this.closeModal("modal-create-task"));
    }

    const modalCancelBtn = document.getElementById("btn-cancel-task");
    if (modalCancelBtn) {
      modalCancelBtn.addEventListener("click", () => this.closeModal("modal-create-task"));
    }

    const taskForm = document.getElementById("form-create-task");
    if (taskForm) {
      taskForm.addEventListener("submit", (e) => this.handleCreateTaskSubmit(e));
    }

    // Dynamic criteria add button in modal
    const addCriteriaBtn = document.getElementById("btn-add-criterion");
    if (addCriteriaBtn) {
      addCriteriaBtn.addEventListener("click", () => this.addCriterionInput());
    }

    // Decision modal close
    const decisionCloseBtn = document.getElementById("btn-close-decision-modal");
    if (decisionCloseBtn) {
      decisionCloseBtn.addEventListener("click", () => this.closeModal("modal-approval-decision"));
    }
  },

  navigateTo(view, taskId = null) {
    // Role protection: If User attempts to access admin-only views, redirect to user-studio
    const adminOnlyViews = ["dashboard", "approvals", "recipes", "git", "audit", "agents"];
    if (!this.isAdmin() && adminOnlyViews.includes(view)) {
      view = "user-studio";
    }

    this.currentView = view;
    if (taskId) {
      this.selectedTaskId = taskId;
    }

    this.updateRoleUI();

    document.querySelectorAll(".nav-item").forEach((el) => {
      if (el.getAttribute("data-view") === view) {
        el.classList.add("active");
      } else {
        el.classList.remove("active");
      }
    });

    const titleEl = document.getElementById("view-header-title");
    const viewTitles = {
      dashboard: "Executive Dashboard",
      tasks: "Workflows & Pipelines",
      "task-detail": `Task Inspector — ${this.selectedTaskId || ""}`,
      approvals: "Human Approval Gatekeeper",
      agents: "Agent Fleet & Diagnostics",
      recipes: "Workflow Recipes",
      audit: "Audit Trail Stream",
      git: "Git Repository & Worktrees",
      "user-studio": "AI Builder Studio — Tell What to Build",
      "user-builds": "My Builds & Agent Progress",
    };
    if (titleEl) {
      titleEl.textContent = viewTitles[view] || "NAO Orchestrator";
    }

    this.renderCurrentView();
  },

  startAutoRefresh() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = setInterval(() => {
      this.loadSystemData(false);

      // FIX FOR REQUEST 4: If user is actively typing in ANY input or textarea, DO NOT re-render!
      const active = document.activeElement;
      if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.isContentEditable)) {
        return;
      }

      if (this.currentView === "dashboard") {
        this.renderDashboard(false);
      } else if (this.currentView === "user-studio") {
        this.renderUserStudio(false);
      } else if (this.currentView === "user-builds") {
        this.renderUserBuilds(false);
      } else if (this.currentView === "task-detail" && this.selectedTaskId) {
        this.renderTaskDetail(this.selectedTaskId, false);
      } else if (this.currentView === "approvals") {
        this.renderApprovals(false);
      }
    }, 3500);
  },

  async loadSystemData(showLoading = true) {
    try {
      const data = await API.getSystem();
      this.systemData = data;

      // Update repo pill (sanitized)
      const repoEl = document.getElementById("header-repo-path");
      if (repoEl) {
        repoEl.textContent = this.sanitizePath(data.default_repo_path);
        repoEl.title = "Active Project Workspace";
      }

      // Update RAM meter in sidebar (Strictly Usage & Remaining)
      const telem = data.telemetry || {};
      const ramFill = document.getElementById("sidebar-ram-fill");
      const ramText = document.getElementById("sidebar-ram-text");
      const ramRem = document.getElementById("sidebar-ram-rem");

      if (telem.ram_used_gb !== null && telem.ram_used_gb !== undefined) {
        const usedPct = telem.ram_percent !== null ? Math.round(telem.ram_percent) : 75;
        if (ramFill) ramFill.style.width = `${usedPct}%`;
        if (ramText) ramText.textContent = `${telem.ram_used_gb} GB Used`;
        if (ramRem) ramRem.textContent = `Remaining: ${telem.ram_free_gb || "--"} GB`;
      } else if (data.resources && data.resources.available_ram_percent !== null) {
        const usedPercent = Math.max(0, Math.min(100, Math.round(100 - data.resources.available_ram_percent)));
        if (ramFill) ramFill.style.width = `${usedPercent}%`;
        if (ramText) ramText.textContent = `${usedPercent}% Used`;
        if (ramRem) ramRem.textContent = `Remaining: ${Math.round(data.resources.available_ram_percent)}%`;
      }

      // Update pending approvals count badge
      const approvalsData = await API.getApprovals();
      const approvalsList = Array.isArray(approvalsData) ? approvalsData : [];
      const pending = approvalsList.filter((a) => a && a.state === "pending");
      this.pendingApprovalsCount = pending.length;
      const badge = document.getElementById("nav-approvals-badge");
      if (badge) {
        badge.textContent = pending.length;
        badge.style.display = pending.length > 0 ? "inline-block" : "none";
      }

      // Update live pulse status
      const pulseDot = document.getElementById("pulse-indicator");
      const pulseText = document.getElementById("pulse-text");
      if (pulseDot && pulseText) {
        if (data.running_tasks_count > 0) {
          pulseDot.className = "pulse-dot pulse-busy";
          pulseText.textContent = `${data.running_tasks_count} Task Running`;
        } else {
          pulseDot.className = "pulse-dot";
          pulseText.textContent = "Orchestrator Ready";
        }
      }
    } catch (e) {
      console.warn("Could not load telemetry:", e);
    }
  },

  renderCurrentView() {
    const container = document.getElementById("main-view-container");
    if (!container) return;

    this.updateRoleUI();

    // Guard non-admin access to admin views
    if (!this.isAdmin()) {
      if (["dashboard", "approvals", "recipes", "git", "audit", "agents"].includes(this.currentView)) {
        this.currentView = "user-studio";
      }
    }

    switch (this.currentView) {
      case "user-studio":
        this.renderUserStudio();
        break;
      case "user-builds":
        this.renderUserBuilds();
        break;
      case "dashboard":
        this.renderDashboard();
        break;
      case "tasks":
        this.renderTasks();
        break;
      case "task-detail":
        if (this.selectedTaskId) {
          this.renderTaskDetail(this.selectedTaskId);
        } else {
          this.navigateTo(this.isAdmin() ? "tasks" : "user-builds");
        }
        break;
      case "approvals":
        this.renderApprovals();
        break;
      case "agents":
        this.renderAgents();
        break;
      case "recipes":
        this.renderRecipes();
        break;
      case "audit":
        this.renderAudit();
        break;
      case "git":
        this.renderGit();
        break;
      default:
        if (this.isAdmin()) {
          this.renderDashboard();
        } else {
          this.renderUserStudio();
        }
    }
  },

  // =========================================================================
  // USER STUDIO: Dedicated Simple Experience for Regular Users
  // Option 1: Tell what they want to build
  // Option 2: Choose which agent for this work
  // No complex "+ Add Task" wizard, no permissions, no L0/L1/L2
  // =========================================================================
  async renderUserStudio(showLoading = true) {
    const container = document.getElementById("main-view-container");
    if (!container) return;

    const availableAgentsList = [
      { id: "cline", name: "Cline", icon: "🤖", desc: "Autonomous CLI agent for end-to-end coding & tools", popular: true },
      { id: "antigravity", name: "Antigravity", icon: "🌌", desc: "Advanced architectural reasoning & deep debugging" },
      { id: "goose", name: "Goose", icon: "🪿", desc: "On-machine autonomous executor with native tools" },
      { id: "aider", name: "Aider", icon: "⚡", desc: "High-speed pair programmer and Git repository editor" },
      { id: "cursor", name: "Cursor", icon: "💻", desc: "Multi-file refactoring and intelligent code patches" },
      { id: "copilot", name: "GitHub Copilot", icon: "🛸", desc: "Contextual implementation suggestions & completions" },
      { id: "kilo", name: "Kilo", icon: "⚙️", desc: "Fast lightweight local task and script runner" },
    ];

    const currentAgent = this.selectedStudioAgent || "cline";

    container.innerHTML = `
      <div class="user-studio-hero">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.75rem; flex-wrap: wrap; gap: 0.5rem;">
          <div>
            <h2 style="font-size: 1.5rem; font-weight: 800; color: var(--text-primary); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.6rem;">
              <span>✨</span>
              <span>AI Builder Studio</span>
            </h2>
            <p style="font-size: 0.88rem; color: var(--text-secondary); margin: 0;">
              Welcome, <strong>${this.escapeHtml(this.currentUser ? this.currentUser.full_name : "User")}</strong>! Tell our AI fleet what you want to build, choose your agent, and watch it code.
            </p>
          </div>
          <button class="btn btn-secondary btn-sm" onclick="App.navigateTo('user-builds')">
            🚀 View My Builds &amp; Progress
          </button>
        </div>

        <form onsubmit="App.handleUserStudioBuild(event)" style="display: flex; flex-direction: column; gap: 1.5rem; margin-top: 1.25rem;">
          <!-- Step 1: Tell what they want to build -->
          <div class="card" style="border: 1px solid rgba(99, 102, 241, 0.25); background: var(--bg-surface);">
            <div class="card-header" style="background: rgba(99, 102, 241, 0.08);">
              <span class="card-title" style="font-size: 1rem; color: var(--text-primary);">
                <span>1️⃣</span> <span>Tell what you want to build</span>
              </span>
              <span style="font-size: 0.75rem; color: var(--text-muted);">Plain English description</span>
            </div>
            <div class="card-body">
              <textarea 
                id="user-studio-prompt" 
                class="form-input" 
                rows="4" 
                placeholder="Describe what you want to build, fix, or add... (e.g. 'Build a user authentication system with password reset', or 'Fix the shopping cart total calculation bug and add unit tests')" 
                style="resize: vertical; font-size: 0.92rem; line-height: 1.5; padding: 0.85rem;" 
                required
              ></textarea>
            </div>
          </div>

          <!-- Step 2: Choose which agent for this work -->
          <div class="card" style="border: 1px solid rgba(16, 185, 129, 0.25); background: var(--bg-surface);">
            <div class="card-header" style="background: rgba(16, 185, 129, 0.08);">
              <span class="card-title" style="font-size: 1rem; color: var(--text-primary);">
                <span>2️⃣</span> <span>Choose which agent for this work</span>
              </span>
              <span style="font-size: 0.75rem; color: var(--color-success); font-weight: 600;">
                Selected: <span id="user-selected-agent-name" style="text-transform: capitalize;">${currentAgent}</span>
              </span>
            </div>
            <div class="card-body">
              <div class="agent-select-grid">
                ${availableAgentsList.map((ag) => `
                  <div 
                    id="agent-card-${ag.id}" 
                    class="agent-select-card ${ag.id === currentAgent ? "selected" : ""}" 
                    onclick="App.selectStudioAgent('${ag.id}', '${ag.name}')"
                  >
                    <div style="display: flex; align-items: center; justify-content: space-between;">
                      <span class="agent-card-icon">${ag.icon}</span>
                      ${ag.popular ? `<span class="badge badge-ok" style="font-size: 0.65rem;">Recommended</span>` : ""}
                    </div>
                    <div class="agent-card-name">${ag.name}</div>
                    <div class="agent-card-desc">${ag.desc}</div>
                    <div style="margin-top: auto; padding-top: 0.4rem; font-size: 0.72rem; font-weight: 700; color: ${ag.id === currentAgent ? "#818cf8" : "var(--text-muted)"};">
                      ${ag.id === currentAgent ? "✓ Selected" : "Click to select"}
                    </div>
                  </div>
                `).join("")}
              </div>
            </div>
          </div>

          <!-- Action Button: Start Building -->
          <div style="display: flex; justify-content: flex-end; align-items: center; gap: 1rem;">
            <span style="font-size: 0.82rem; color: var(--text-muted);">
              Agent will execute safely in an isolated workspace with verification checks.
            </span>
            <button type="submit" class="btn btn-primary" style="font-size: 1rem; font-weight: 800; padding: 0.85rem 2rem; box-shadow: 0 4px 15px rgba(99, 102, 241, 0.4);">
              🚀 Start Building with Agent
            </button>
          </div>
        </form>
      </div>
    `;
  },

  selectStudioAgent(agentId, agentName) {
    this.selectedStudioAgent = agentId;
    document.querySelectorAll(".agent-select-card").forEach((card) => {
      card.classList.remove("selected");
      const checkText = card.querySelector("div:last-child");
      if (checkText) {
        checkText.style.color = "var(--text-muted)";
        checkText.textContent = "Click to select";
      }
    });

    const activeCard = document.getElementById(`agent-card-${agentId}`);
    if (activeCard) {
      activeCard.classList.add("selected");
      const checkText = activeCard.querySelector("div:last-child");
      if (checkText) {
        checkText.style.color = "#818cf8";
        checkText.textContent = "✓ Selected";
      }
    }

    const label = document.getElementById("user-selected-agent-name");
    if (label) label.textContent = agentName || agentId;
  },

  async handleUserStudioBuild(e) {
    e.preventDefault();
    const promptInput = document.getElementById("user-studio-prompt");
    const prompt = promptInput ? promptInput.value.trim() : "";
    if (!prompt) {
      this.showToast("Please describe what you want to build", "error");
      return;
    }

    const agent = this.selectedStudioAgent || "cline";
    const title = prompt.length > 60 ? prompt.slice(0, 57) + "..." : prompt;

    try {
      this.showToast(`Assigning build to ${agent}...`, "info");
      const res = await API.createTask({
        title,
        description: prompt,
        recipe: "feature_implementation",
        acceptance_criteria: ["Implement requested functionality", "All tests pass with zero regressions"],
        granted_permissions: ["read", "write", "shell", "git_read"],
        routing_overrides: {
          implementer: agent,
          analyzer: agent,
        },
      });

      this.newlyCreatedTaskId = res.task_id;
      this.showToast(`Build started! Task ${res.task_id} assigned to ${agent.toUpperCase()}`, "success");
      this.navigateTo("user-builds");
    } catch (err) {
      this.showToast(`Failed to start build: ${err.message}`, "error");
    }
  },

  // =========================================================================
  // USER BUILDS: Progress and History for Regular Users
  // =========================================================================
  async renderUserBuilds(showLoading = true) {
    const container = document.getElementById("main-view-container");
    if (!container) return;

    if (showLoading) {
      container.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-muted);">Loading your builds &amp; agent progress...</div>`;
    }

    try {
      const tasksRes = await API.getTasks().catch(() => []);
      const tasks = Array.isArray(tasksRes) ? tasksRes : [];

      container.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; flex-wrap: wrap; gap: 1rem;">
          <div>
            <h3 style="font-size: 1.25rem; font-weight: 800; color: var(--text-primary); margin-bottom: 0.25rem;">
              My Builds &amp; Progress
            </h3>
            <p style="font-size: 0.82rem; color: var(--text-muted); margin: 0;">
              Track autonomous execution, reviewer checks, and code progress.
            </p>
          </div>
          <button class="btn btn-primary" onclick="App.navigateTo('user-studio')">
            ✨ Tell What to Build Next
          </button>
        </div>

        ${tasks.length === 0 ? `
          <div class="card" style="text-align: center; padding: 3rem 1.5rem;">
            <div style="font-size: 2.5rem; margin-bottom: 0.75rem;">🚀</div>
            <h4 style="font-size: 1.1rem; font-weight: 700; margin-bottom: 0.5rem;">No builds yet!</h4>
            <p style="font-size: 0.85rem; color: var(--text-muted); max-width: 400px; margin: 0 auto 1.25rem auto;">
              Tell our AI fleet what you want to build and choose an agent to begin your first autonomous build.
            </p>
            <button class="btn btn-primary" onclick="App.navigateTo('user-studio')">
              Start Building Now
            </button>
          </div>
        ` : `
          <div style="display: flex; flex-direction: column; gap: 1rem;">
            ${tasks.map((t) => {
              const isNew = t.task_id === this.newlyCreatedTaskId;
              const isRunning = t.is_running || ["implementing", "testing", "reviewing", "analyzing"].includes(t.state);
              const stateBadgeClass = t.state === "completed" ? "badge-ok" : t.state === "failed" ? "badge-failed" : isRunning ? "badge-running" : "badge-neutral";
              
              return `
                <div class="card" style="${isNew ? "border-left: 4px solid #818cf8; background: rgba(99, 102, 241, 0.04);" : ""}">
                  <div class="card-header" style="flex-wrap: wrap; gap: 0.75rem;">
                    <div style="display: flex; align-items: center; gap: 0.75rem;">
                      <span class="pulse-dot" style="background: ${isRunning ? "var(--color-primary)" : t.state === "completed" ? "var(--color-success)" : "var(--text-muted)"};"></span>
                      <div>
                        <div style="display: flex; align-items: center; gap: 0.5rem;">
                          <strong style="font-size: 1rem; color: var(--text-primary);">${this.escapeHtml(t.title || t.description || "Build Task")}</strong>
                          ${isNew ? `<span class="badge badge-ok" style="font-size: 0.65rem;">NEW</span>` : ""}
                        </div>
                        <div style="font-size: 0.75rem; color: var(--text-muted); font-family: var(--font-mono); margin-top: 0.15rem;">
                          Task: ${t.task_id} • Created ${t.created_at ? new Date(t.created_at).toLocaleTimeString() : "recently"}
                        </div>
                      </div>
                    </div>

                    <div style="display: flex; align-items: center; gap: 0.75rem;">
                      <span class="badge ${stateBadgeClass}" style="text-transform: capitalize; padding: 0.35rem 0.65rem;">
                        ${isRunning ? "⚡ " : ""}${t.state}
                      </span>
                      <button class="btn btn-secondary btn-sm" onclick="App.navigateTo('task-detail', '${t.task_id}')">
                        Inspect Progress ➔
                      </button>
                    </div>
                  </div>
                  <div class="card-body" style="padding: 0.85rem 1.25rem;">
                    <p style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.5rem; line-height: 1.4;">
                      ${this.escapeHtml(t.description || "No description provided.")}
                    </p>
                    <div style="display: flex; gap: 1.25rem; font-size: 0.75rem; color: var(--text-muted); flex-wrap: wrap;">
                      <div>Workflow: <strong style="color: var(--text-secondary);">${t.recipe}</strong></div>
                      <div>Jobs executed: <strong style="color: var(--text-secondary);">${t.jobs_count || 0}</strong></div>
                      <div>Worktree: <span style="font-family: var(--font-mono); color: #818cf8;">${this.sanitizePath(t.repo_path)}</span></div>
                    </div>
                  </div>
                </div>
              `;
            }).join("")}
          </div>
        `}
      `;
    } catch (err) {
      container.innerHTML = `<div style="color:var(--color-danger);padding:2rem;">Failed to load builds: ${err.message}</div>`;
    }
  },

  // =========================================================================
  // View 1: Executive Dashboard (Includes Hero Quick-Task Card!)
  // =========================================================================
  async renderDashboard(showLoading = true) {
    const container = document.getElementById("main-view-container");
    if (showLoading) {
      container.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-muted);">Loading dashboard analytics...</div>`;
    }

    try {
      const [tasksRes, agentsRes, approvalsRes, auditEventsRes] = await Promise.all([
        API.getTasks().catch(() => []),
        API.getAgents().catch(() => []),
        API.getApprovals().catch(() => []),
        API.getAuditEvents(10).catch(() => []),
      ]);

      const tasks = Array.isArray(tasksRes) ? tasksRes : [];
      const agents = Array.isArray(agentsRes) ? agentsRes : [];
      const approvals = Array.isArray(approvalsRes) ? approvalsRes : [];
      const auditEvents = Array.isArray(auditEventsRes) ? auditEventsRes : [];

      const activeTasks = tasks.filter((t) => t && (t.is_running || ["implementing", "testing", "reviewing", "analyzing"].includes(t.state)));
      const pendingApprovals = approvals.filter((a) => a && a.state === "pending");
      const availableAgents = agents.filter((a) => a && a.available);

      let dashboardTasks = tasks;
      if (this.dashboardSearchQuery) {
        const q = this.dashboardSearchQuery.toLowerCase();
        dashboardTasks = tasks.filter((t) => {
          const id = (t.task_id || "").toLowerCase();
          const title = (t.title || "").toLowerCase();
          const desc = (t.description || "").toLowerCase();
          const recipe = (t.recipe || "").toLowerCase();
          const state = (t.state || "").toLowerCase();
          return id.includes(q) || title.includes(q) || desc.includes(q) || recipe.includes(q) || state.includes(q);
        });
      }

      container.innerHTML = `
        <!-- HERO CARD: Add New Task Directly on the Dashboard -->
        <div class="hero-task-card">
          <div class="hero-header">
            <div>
              <div class="hero-title">
                <span>⚡</span>
                <span>Launch New Multi-Agent Workflow</span>
              </div>
              <div class="hero-subtitle">
                Describe your engineering goal below to quickly dispatch the autonomous agent fleet.
              </div>
            </div>
            <button class="btn btn-primary" onclick="App.openCreateTaskModal()">
              ➕ Open Advanced Task Creator
            </button>
          </div>

          <form id="hero-quick-task-form" onsubmit="App.handleHeroQuickTask(event)" class="hero-input-row">
            <input type="text" id="hero-task-prompt" class="hero-input" placeholder="e.g. Implement chess pawn capture rule and verify tests..." required />
            <select id="hero-task-recipe" class="form-select" style="max-width: 220px;">
              <option value="feature_implementation">Feature Implementation</option>
              <option value="bug_fix">Bug Fix &amp; Regression</option>
              <option value="refactoring">Code Refactoring</option>
              <option value="code_review">Code Review</option>
              <option value="security_audit">Security Audit</option>
              <option value="testing">Test Generation</option>
            </select>
            <button type="submit" class="btn btn-primary" style="font-weight: 700; white-space: nowrap; padding: 0.7rem 1.25rem;">
              ➕ Add Task to Pipeline
            </button>
          </form>
        </div>

        <!-- Metrics Grid -->
        <div class="metrics-grid">
          <div class="metric-card" style="--metric-color: var(--color-primary);">
            <div class="metric-header">
              <span>Total Pipelines</span>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            </div>
            <div class="metric-value">${tasks.length}</div>
            <div class="metric-sub">${activeTasks.length} currently executing</div>
          </div>

          <div class="metric-card" style="--metric-color: var(--color-warning);">
            <div class="metric-header">
              <span>Human Approvals</span>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            </div>
            <div class="metric-value" style="color: ${pendingApprovals.length > 0 ? "var(--color-warning)" : "var(--text-primary)"}">
              ${pendingApprovals.length}
            </div>
            <div class="metric-sub">Awaiting Chief Architect review</div>
          </div>

          <div class="metric-card" style="--metric-color: var(--color-success);">
            <div class="metric-header">
              <span>Agent Fleet</span>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            </div>
            <div class="metric-value">${availableAgents.length} / ${agents.length}</div>
            <div class="metric-sub">Available &amp; verified agents</div>
          </div>

          <div class="metric-card" style="--metric-color: var(--color-info);" onclick="App.openMemoryModal()" style="cursor: pointer;" title="Click for storage breakdown">
            <div class="metric-header">
              <span>RAM Storage</span>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/></svg>
            </div>
            <div class="metric-value">${(this.systemData && this.systemData.telemetry && this.systemData.telemetry.ram_used_gb) || "2.99"} GB Used</div>
            <div class="metric-sub" style="color: #10b981; font-weight: 600;">${(this.systemData && this.systemData.telemetry && this.systemData.telemetry.ram_free_gb) || "0.48"} GB Remaining</div>
          </div>
        </div>

        <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 1.5rem; margin-bottom: 1.75rem;">
          <!-- Recent Tasks -->
          <div class="card">
            <div class="card-header" style="flex-wrap: wrap; gap: 0.5rem;">
              <span class="card-title">Recent Tasks &amp; Pipelines</span>
              <div style="display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;">
                <input 
                  type="text" 
                  id="dashboard-search-input" 
                  class="form-input" 
                  placeholder="🔍 Search tasks..." 
                  value="${this.escapeHtml(this.dashboardSearchQuery || '')}" 
                  oninput="App.handleDashboardSearch(this.value)" 
                  style="padding: 0.35rem 0.65rem; font-size: 0.8rem; width: 180px;" 
                />
                <button class="btn btn-primary btn-sm" onclick="App.openCreateTaskModal()">➕ Add Task</button>
                <button class="btn btn-secondary btn-sm" onclick="App.navigateTo('tasks')">View All</button>
              </div>
            </div>
            <div class="card-body" style="padding: 0;" id="dashboard-tasks-table-wrapper">
              ${this.renderTasksTable(dashboardTasks.slice(0, 15))}
            </div>
          </div>

          <!-- Agent Fleet Quick Health -->
          <div class="card">
            <div class="card-header">
              <span class="card-title">Fleet Discovery</span>
              <button class="btn btn-secondary btn-sm" onclick="App.navigateTo('agents')">Doctor</button>
            </div>
            <div class="card-body" style="padding: 0.75rem;">
              <div style="display: flex; flex-direction: column; gap: 0.6rem;">
                ${agents.map((ag) => `
                  <div style="display: flex; align-items: center; justify-content: space-between; padding: 0.5rem 0.75rem; background: var(--bg-surface); border-radius: var(--radius-sm); border: 1px solid var(--border-subtle);">
                    <div style="display: flex; align-items: center; gap: 0.5rem;">
                      <span class="pulse-dot" style="background: ${ag.available ? "var(--color-success)" : "var(--text-muted)"}; box-shadow: none;"></span>
                      <strong style="text-transform: capitalize; font-size: 0.85rem;">${ag.agent}</strong>
                    </div>
                    <span class="badge ${ag.available ? "badge-ok" : "badge-neutral"}" style="font-size: 0.68rem;">
                      ${ag.available ? "Available" : "Not Found"}
                    </span>
                  </div>
                `).join("")}
              </div>
            </div>
          </div>
        </div>

        <!-- Live Audit Ticker -->
        <div class="card">
          <div class="card-header">
            <span class="card-title">Latest Orchestrator Audit Events</span>
            <button class="btn btn-secondary btn-sm" onclick="App.navigateTo('audit')">Full Audit Stream</button>
          </div>
          <div class="card-body" style="padding: 0;">
            ${this.renderAuditTable(auditEvents.slice(0, 6))}
          </div>
        </div>
      `;
    } catch (e) {
      container.innerHTML = `<div style="color:var(--color-danger);padding:2rem;">Failed to load dashboard: ${e.message}</div>`;
    }
  },

  async handleHeroQuickTask(e) {
    e.preventDefault();
    const promptInput = document.getElementById("hero-task-prompt");
    const prompt = promptInput ? promptInput.value.trim() : "";
    const recipeSelect = document.getElementById("hero-task-recipe");
    const recipe = recipeSelect ? recipeSelect.value : "feature_implementation";
    if (!prompt) return;

    try {
      this.showToast("Adding new task to dashboard...", "info");
      const res = await API.createTask({
        title: prompt.slice(0, 70),
        description: prompt,
        recipe: recipe,
        acceptance_criteria: ["Verify all tests pass", "Zero regressions"],
        granted_permissions: ["read", "write", "shell", "git_read"],
      });
      if (promptInput) promptInput.value = "";
      this.newlyCreatedTaskId = res.task_id;
      this.showToast(`Task ${res.task_id} added and displayed on dashboard!`, "success");
      // Re-render dashboard so the task is instantly visible right on the dashboard!
      await this.renderDashboard();
    } catch (err) {
      this.showToast(`Failed to add task: ${err.message}`, "error");
    }
  },

  handleDashboardSearch(query) {
    this.dashboardSearchQuery = query || "";
    const q = this.dashboardSearchQuery.trim().toLowerCase();
    const rows = document.querySelectorAll("#dashboard-tasks-table tbody tr, .dashboard-task-row");
    if (rows && rows.length > 0) {
      rows.forEach((row) => {
        const text = row.innerText.toLowerCase();
        if (!q || text.includes(q)) {
          row.style.display = "";
        } else {
          row.style.display = "none";
        }
      });
    }
  },

  // =========================================================================
  // View 2: Tasks List
  // =========================================================================
  async renderTasks() {
    const container = document.getElementById("main-view-container");
    container.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-muted);">Loading tasks...</div>`;

    try {
      const tasks = await API.getTasks();

      container.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.25rem; gap: 1rem; flex-wrap: wrap;">
          <div style="display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;">
            <button class="btn btn-secondary btn-sm filter-task-btn active" data-filter="all">All (${tasks.length})</button>
            <button class="btn btn-secondary btn-sm filter-task-btn" data-filter="running">Running</button>
            <button class="btn btn-secondary btn-sm filter-task-btn" data-filter="waiting_for_approval">Awaiting Approval</button>
            <button class="btn btn-secondary btn-sm filter-task-btn" data-filter="completed">Completed</button>
            <button class="btn btn-secondary btn-sm filter-task-btn" data-filter="failed">Failed</button>
          </div>

          <div style="display: flex; gap: 0.75rem;">
            <input type="text" id="task-search-input" class="form-input" placeholder="Search tasks by ID or title..." style="max-width: 260px;" />
            <button class="btn btn-primary btn-sm" onclick="App.openCreateTaskModal()">➕ Add Task</button>
          </div>
        </div>

        <div class="card">
          <div class="card-body" style="padding: 0;" id="tasks-table-wrapper">
            ${this.renderTasksTable(tasks)}
          </div>
        </div>
      `;

      document.querySelectorAll(".filter-task-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          document.querySelectorAll(".filter-task-btn").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          const filter = btn.getAttribute("data-filter");
          let filtered = tasks;
          if (filter === "running") {
            filtered = tasks.filter((t) => t.is_running || ["implementing", "testing", "reviewing", "analyzing"].includes(t.state));
          } else if (filter !== "all") {
            filtered = tasks.filter((t) => t.state.toLowerCase() === filter.toLowerCase());
          }
          const wrapper = document.getElementById("tasks-table-wrapper");
          if (wrapper) wrapper.innerHTML = this.renderTasksTable(filtered);
        });
      });

      const searchInput = document.getElementById("task-search-input");
      if (searchInput) {
        searchInput.addEventListener("input", (e) => {
          const q = e.target.value.toLowerCase();
          const filtered = tasks.filter((t) => t.task_id.toLowerCase().includes(q) || t.title.toLowerCase().includes(q) || t.recipe.toLowerCase().includes(q));
          const wrapper = document.getElementById("tasks-table-wrapper");
          if (wrapper) wrapper.innerHTML = this.renderTasksTable(filtered);
        });
      }
    } catch (e) {
      container.innerHTML = `<div style="color:var(--color-danger);padding:2rem;">Failed to load tasks: ${e.message}</div>`;
    }
  },

  renderTasksTable(tasks) {
    if (!tasks || tasks.length === 0) {
      return `<div style="padding: 2.5rem; text-align: center; color: var(--text-muted);">No orchestration tasks found. Click <strong>+ Add Task</strong> to create one.</div>`;
    }

    return `
      <div class="data-table-container">
        <table class="data-table" id="dashboard-tasks-table">
          <thead>
            <tr>
              <th>Task ID</th>
              <th>Title / Description</th>
              <th>Recipe</th>
              <th>Workflow State</th>
              <th>Jobs</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${tasks.map((t) => `
              <tr class="dashboard-task-row" style="cursor: pointer; ${t.task_id === this.newlyCreatedTaskId ? "background: rgba(99, 102, 241, 0.12); border-left: 3px solid #818cf8;" : ""}" onclick="App.navigateTo('task-detail', '${t.task_id}')">
                <td>
                  <div style="display: flex; align-items: center; gap: 0.4rem;">
                    <span style="font-family: var(--font-mono); font-size: 0.78rem; font-weight: 700; color: #818cf8;">
                      ${t.task_id}
                    </span>
                    ${t.task_id === this.newlyCreatedTaskId ? `<span class="badge badge-ok" style="font-size: 0.65rem; font-weight: 800; padding: 0.1rem 0.35rem;">✨ NEW</span>` : ""}
                  </div>
                </td>
                <td>
                  <div style="font-weight: 600; color: var(--text-primary); max-width: 320px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                    ${this.escapeHtml(t.title || t.description || "Untitled Task")}
                  </div>
                  <div style="font-size: 0.72rem; color: var(--text-muted); font-family: var(--font-mono);">
                    ${this.sanitizePath(t.repo_path)}
                  </div>
                </td>
                <td>
                  <span class="badge badge-neutral" style="font-size: 0.7rem;">
                    ${t.recipe}
                  </span>
                </td>
                <td>
                  <span class="badge badge-${t.state.toLowerCase()}">
                    ${t.is_running ? "⚡ " : ""}${t.state}
                  </span>
                </td>
                <td>
                  <span style="font-size: 0.75rem; color: var(--text-secondary);">
                    ${t.jobs_count || 0} jobs
                  </span>
                </td>
                <td onclick="event.stopPropagation();">
                  <div style="display: flex; gap: 0.4rem;">
                    <button class="btn btn-secondary btn-sm" onclick="App.navigateTo('task-detail', '${t.task_id}')">
                      Inspect
                    </button>
                    ${!["completed", "failed", "cancelled"].includes(t.state.toLowerCase()) ? `
                      <button class="btn btn-primary btn-sm" onclick="App.triggerRunTask('${t.task_id}')">
                        Run
                      </button>
                    ` : ""}
                  </div>
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  },

  // =========================================================================
  // View 3: Task Detail Inspector & State Pipeline
  // =========================================================================
  async renderTaskDetail(taskId, showLoading = true) {
    const container = document.getElementById("main-view-container");
    if (showLoading) {
      container.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-muted);">Loading task ${taskId}...</div>`;
    }

    try {
      const data = await API.getTask(taskId);
      const { task, recipe_steps, jobs, diffs, evidence, findings, approvals, audit_events } = data;

      const allStates = [
        "RECEIVED", "ANALYZING", "PLANNED", "WAITING_FOR_APPROVAL", "READY",
        "IMPLEMENTING", "TESTING", "REVIEWING", "FIXING", "VALIDATED", "MERGED", "COMPLETED"
      ];
      const currentState = task.state.toUpperCase();
      const currentIndex = allStates.indexOf(currentState);

      container.innerHTML = `
        <!-- Task Header Banner -->
        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1.5rem; flex-wrap: wrap; gap: 1rem;">
          <div>
            <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.35rem;">
              <span style="font-family: var(--font-mono); font-size: 0.9rem; font-weight: 700; color: #818cf8;">${task.task_id}</span>
              <span class="badge badge-${task.state.toLowerCase()}">${data.is_running ? "⚡ Executing: " : ""}${task.state}</span>
              <span class="badge badge-neutral">${task.recipe}</span>
            </div>
            <h2 style="font-size: 1.35rem; font-weight: 800; letter-spacing: -0.02em;">
              ${this.escapeHtml(task.title || task.description)}
            </h2>
            <div style="font-size: 0.78rem; color: var(--text-muted); font-family: var(--font-mono); margin-top: 0.25rem;">
              Repository: ${this.sanitizePath(task.repo_path)}
            </div>
          </div>

          <div style="display: flex; gap: 0.5rem;">
            ${!["completed", "failed", "cancelled"].includes(task.state.toLowerCase()) ? `
              <button class="btn btn-primary" onclick="App.triggerRunTask('${task.task_id}')">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                ${data.is_running ? "Running in Background..." : "Execute Workflow Step"}
              </button>
            ` : ""}
            ${data.is_running ? `
              <button class="btn btn-danger" onclick="App.triggerCancelTask('${task.task_id}')">
                Cancel
              </button>
            ` : ""}
            <button class="btn btn-secondary" onclick="App.renderTaskDetail('${task.task_id}')">
              Refresh
            </button>
          </div>
        </div>

        <!-- Visual State Pipeline Stepper -->
        <div class="card" style="margin-bottom: 1.5rem;">
          <div class="card-header" style="padding: 0.6rem 1rem;">
            <span style="font-size: 0.75rem; font-weight: 700; text-transform: uppercase; color: var(--text-muted);">
              Orchestrator State Machine Pipeline
            </span>
            <span style="font-size: 0.72rem; color: var(--text-muted);">
              Step ${task.current_step + 1} of ${recipe_steps.length || 12}
            </span>
          </div>
          <div class="stepper-container">
            ${allStates.map((st, idx) => {
              let nodeClass = "step-node";
              if (idx < currentIndex) nodeClass += " step-passed";
              else if (idx === currentIndex) nodeClass += " step-current";
              return `
                <div class="${nodeClass}">
                  ${idx < currentIndex ? "✓" : idx + 1}. ${st}
                </div>
                ${idx < allStates.length - 1 ? `<span class="step-arrow">➔</span>` : ""}
              `;
            }).join("")}
          </div>
        </div>

        <!-- Approval Alert Banner if in WAITING_FOR_APPROVAL -->
        ${task.state === "WAITING_FOR_APPROVAL" ? `
          <div style="background: var(--color-warning-bg); border: 1px solid var(--color-warning-border); padding: 1rem 1.25rem; border-radius: var(--radius-md); margin-bottom: 1.5rem; display: flex; align-items: center; justify-content: space-between;">
            <div>
              <strong style="color: var(--color-warning); font-size: 0.95rem;">Human Approval Required!</strong>
              <p style="font-size: 0.82rem; color: var(--text-secondary); margin-top: 0.2rem;">
                This workflow reached an explicit human verification gate before proceeding.
              </p>
            </div>
            <button class="btn btn-primary btn-sm" onclick="App.openApprovalModal('${task.task_id}')">
              Review &amp; Decide
            </button>
          </div>
        ` : ""}

        <!-- Detail Tabs Navigation -->
        <div class="tabs-nav">
          <button class="tab-btn ${this.activeDetailTab === "overview" ? "active" : ""}" onclick="App.setDetailTab('overview')">
            Overview &amp; Criteria (${task.acceptance_criteria.length})
          </button>
          <button class="tab-btn ${this.activeDetailTab === "jobs" ? "active" : ""}" onclick="App.setDetailTab('jobs')">
            Agent Jobs (${jobs.length})
          </button>
          <button class="tab-btn ${this.activeDetailTab === "diffs" ? "active" : ""}" onclick="App.setDetailTab('diffs')">
            Git Diffs (${Object.keys(diffs).length})
          </button>
          <button class="tab-btn ${this.activeDetailTab === "evidence" ? "active" : ""}" onclick="App.setDetailTab('evidence')">
            Test Evidence (${evidence.length})
          </button>
          <button class="tab-btn ${this.activeDetailTab === "findings" ? "active" : ""}" onclick="App.setDetailTab('findings')">
            Review Findings (${findings.length})
          </button>
          <button class="tab-btn ${this.activeDetailTab === "audit" ? "active" : ""}" onclick="App.setDetailTab('audit')">
            Task Audit Trail (${audit_events.length})
          </button>
        </div>

        <!-- Tab Content Canvas -->
        <div id="detail-tab-canvas">
          ${this.renderDetailTabContent(data)}
        </div>
      `;
    } catch (e) {
      container.innerHTML = `<div style="color:var(--color-danger);padding:2rem;">Failed to load task details: ${e.message}</div>`;
    }
  },

  setDetailTab(tabName) {
    this.activeDetailTab = tabName;
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      if (btn.textContent.toLowerCase().includes(tabName)) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
    });
    this.renderTaskDetail(this.selectedTaskId, false);
  },

  renderDetailTabContent(data) {
    const { task, jobs, diffs, evidence, findings, audit_events } = data;

    switch (this.activeDetailTab) {
      case "overview":
        return `
          <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 1.5rem;">
            <div class="card">
              <div class="card-header"><span class="card-title">Task Specification</span></div>
              <div class="card-body">
                <h4 style="font-size: 0.82rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.5rem;">Description</h4>
                <div style="white-space: pre-wrap; font-size: 0.9rem; line-height: 1.6; color: var(--text-primary); margin-bottom: 1.5rem;">
                  ${this.escapeHtml(task.description)}
                </div>

                <h4 style="font-size: 0.82rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.5rem;">Acceptance Criteria</h4>
                ${task.acceptance_criteria && task.acceptance_criteria.length > 0 ? `
                  <ul style="list-style: none; display: flex; flex-direction: column; gap: 0.4rem;">
                    ${task.acceptance_criteria.map((c) => `
                      <li style="display: flex; align-items: flex-start; gap: 0.5rem; font-size: 0.85rem; color: var(--text-secondary);">
                        <span style="color: var(--color-success); font-weight: bold;">✔</span>
                        <span>${this.escapeHtml(c)}</span>
                      </li>
                    `).join("")}
                  </ul>
                ` : `<div style="color: var(--text-muted); font-size: 0.82rem;">No acceptance criteria specified.</div>`}
              </div>
            </div>

            <div class="card">
              <div class="card-header"><span class="card-title">Security &amp; Permissions</span></div>
              <div class="card-body">
                <h4 style="font-size: 0.78rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.5rem;">Granted Permissions</h4>
                <div style="display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 1.25rem;">
                  ${task.granted_permissions.map((p) => `
                    <span class="badge badge-neutral" style="font-size: 0.72rem;">${p}</span>
                  `).join("")}
                </div>

                <h4 style="font-size: 0.78rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.5rem;">Workflow Settings</h4>
                <div style="display: flex; flex-direction: column; gap: 0.4rem; font-size: 0.8rem; color: var(--text-secondary);">
                  <div>Max Fix Attempts: <strong>${task.max_fix_attempts}</strong></div>
                  <div>Fix Attempts Made: <strong>${task.fix_attempt}</strong></div>
                  <div>Jobs Per Role: <strong>${task.max_jobs_per_role}</strong></div>
                </div>
              </div>
            </div>
          </div>
        `;

      case "jobs":
        if (!jobs || jobs.length === 0) {
          return `<div class="card"><div class="card-body" style="text-align: center; color: var(--text-muted); padding: 3rem;">No agent jobs dispatched yet.</div></div>`;
        }
        return `
          <div style="display: flex; flex-direction: column; gap: 1rem;">
            ${jobs.map((j) => {
              const job = j.job;
              const resp = j.response || {};
              return `
                <div class="card">
                  <div class="card-header">
                    <div style="display: flex; align-items: center; gap: 0.75rem;">
                      <span style="font-family: var(--font-mono); font-size: 0.82rem; font-weight: 700; color: #818cf8;">${job.job_id}</span>
                      <strong style="text-transform: capitalize;">${job.agent}</strong>
                      <span class="badge badge-neutral">${job.role}</span>
                      <span class="badge badge-${job.status.toLowerCase()}">${job.status}</span>
                    </div>
                    <span style="font-size: 0.75rem; color: var(--text-muted);">Attempt ${job.attempt}</span>
                  </div>
                  <div class="card-body">
                    ${resp.stdout || resp.stderr ? `
                      <h4 style="font-size: 0.78rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.5rem;">Agent Execution Console</h4>
                      <div class="terminal-block">${this.escapeHtml(resp.stdout || resp.stderr || "No output")}</div>
                    ` : `<div style="font-size: 0.8rem; color: var(--text-muted);">Job status: ${job.status}</div>`}
                  </div>
                </div>
              `;
            }).join("")}
          </div>
        `;

      case "diffs":
        const diffKeys = Object.keys(diffs);
        if (diffKeys.length === 0) {
          return `<div class="card"><div class="card-body" style="text-align: center; color: var(--text-muted); padding: 3rem;">No Git worktree diffs recorded for this task.</div></div>`;
        }
        return `
          <div style="display: flex; flex-direction: column; gap: 1rem;">
            ${diffKeys.map((k) => `
              <div class="card">
                <div class="card-header">
                  <span class="card-title">Diff for ${k}</span>
                </div>
                <div class="card-body" style="padding: 0;">
                  <div class="diff-container">
                    ${this.formatDiff(diffs[k])}
                  </div>
                </div>
              </div>
            `).join("")}
          </div>
        `;

      case "evidence":
        if (!evidence || evidence.length === 0) {
          return `<div class="card"><div class="card-body" style="text-align: center; color: var(--text-muted); padding: 3rem;">No test gate evidence collected yet.</div></div>`;
        }
        return `
          <div style="display: flex; flex-direction: column; gap: 1rem;">
            ${evidence.map((ev) => `
              <div class="card">
                <div class="card-header">
                  <div style="display: flex; align-items: center; gap: 0.5rem;">
                    <span class="badge badge-${ev.status === "passed" ? "ok" : "failed"}">${ev.status}</span>
                    <strong style="font-size: 0.85rem;">${ev.kind} Suite</strong>
                  </div>
                  <span style="font-size: 0.75rem; color: var(--text-muted); font-family: var(--font-mono);">Exit Code: ${ev.exit_code}</span>
                </div>
                <div class="card-body">
                  <div style="display: flex; gap: 1.5rem; margin-bottom: 1rem; font-size: 0.85rem;">
                    <div>Passed: <strong style="color: var(--color-success);">${ev.passed_count || 0}</strong></div>
                    <div>Failed: <strong style="color: var(--color-danger);">${ev.failed_count || 0}</strong></div>
                    <div>Errors: <strong style="color: var(--color-warning);">${ev.error_count || 0}</strong></div>
                  </div>
                  ${ev.output ? `
                    <div class="terminal-block">${this.escapeHtml(ev.output)}</div>
                  ` : ""}
                </div>
              </div>
            `).join("")}
          </div>
        `;

      case "findings":
        if (!findings || findings.length === 0) {
          return `<div class="card"><div class="card-body" style="text-align: center; color: var(--text-muted); padding: 3rem;">No independent review findings recorded. Clean bill of health!</div></div>`;
        }
        return `
          <div style="display: flex; flex-direction: column; gap: 0.75rem;">
            ${findings.map((f) => `
              <div class="card" style="border-left: 3px solid ${f.severity === "critical" || f.severity === "high" ? "var(--color-danger)" : "var(--color-warning)"};">
                <div class="card-header" style="padding: 0.75rem 1rem;">
                  <div style="display: flex; align-items: center; gap: 0.5rem;">
                    <span class="badge ${f.severity === "critical" || f.severity === "high" ? "badge-failed" : "badge-warning"}">${f.severity}</span>
                    <strong>${this.escapeHtml(f.title || "Code Review Finding")}</strong>
                  </div>
                  <span style="font-size: 0.75rem; font-family: var(--font-mono); color: var(--text-muted);">${f.file || ""}${f.line ? `:${f.line}` : ""}</span>
                </div>
                <div class="card-body" style="padding: 0.75rem 1rem; font-size: 0.85rem;">
                  <p style="color: var(--text-secondary); margin-bottom: 0.5rem;">${this.escapeHtml(f.description || "")}</p>
                  ${f.suggestion ? `
                    <div style="background: var(--bg-surface); padding: 0.5rem 0.75rem; border-radius: var(--radius-sm); border-left: 2px solid var(--color-info); font-size: 0.8rem;">
                      <strong>Remediation:</strong> ${this.escapeHtml(f.suggestion)}
                    </div>
                  ` : ""}
                </div>
              </div>
            `).join("")}
          </div>
        `;

      case "audit":
        return `
          <div class="card">
            <div class="card-body" style="padding: 0;">
              ${this.renderAuditTable(audit_events)}
            </div>
          </div>
        `;

      default:
        return "";
    }
  },

  formatDiff(diffText) {
    if (!diffText) return "<div>No changes</div>";
    const lines = diffText.split("\n");
    return lines.map((l) => {
      let className = "diff-line";
      if (l.startsWith("+") && !l.startsWith("+++")) className += " diff-add";
      else if (l.startsWith("-") && !l.startsWith("---")) className += " diff-del";
      else if (l.startsWith("@@") || l.startsWith("diff ")) className += " diff-header";
      return `<div class="${className}">${this.escapeHtml(l)}</div>`;
    }).join("");
  },

  // =========================================================================
  // View 4: Human Approvals Queue
  // =========================================================================
  async renderApprovals(showLoading = true) {
    const container = document.getElementById("main-view-container");
    if (showLoading) {
      container.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-muted);">Loading approval queue...</div>`;
    }

    try {
      const approvals = await API.getApprovals();
      const pending = approvals.filter((a) => a.state === "pending");
      const past = approvals.filter((a) => a.state !== "pending");

      container.innerHTML = `
        <div style="margin-bottom: 1.5rem;">
          <h3 style="font-size: 1.15rem; font-weight: 700; margin-bottom: 0.35rem;">Chief Architect Approval Queue</h3>
          <p style="font-size: 0.82rem; color: var(--text-muted);">
            High-impact mutations (plans, Git commits, worktree merges) require recorded human approval.
          </p>
        </div>

        <div style="margin-bottom: 2rem;">
          <h4 style="font-size: 0.85rem; font-weight: 700; text-transform: uppercase; color: var(--color-warning); margin-bottom: 0.75rem; display: flex; align-items: center; gap: 0.5rem;">
            <span>Pending Gates</span>
            <span class="badge badge-warning">${pending.length}</span>
          </h4>

          ${pending.length === 0 ? `
            <div class="card"><div class="card-body" style="padding: 2.5rem; text-align: center; color: var(--text-muted);">All approval gates clear. No pending human decisions.</div></div>
          ` : `
            <div style="display: flex; flex-direction: column; gap: 1rem;">
              ${pending.map((a) => `
                <div class="card" style="border-left: 3px solid var(--color-warning);">
                  <div class="card-header">
                    <div>
                      <span style="font-family: var(--font-mono); font-size: 0.8rem; font-weight: 700; color: #818cf8;">${a.approval_id}</span>
                      <strong style="margin-left: 0.5rem; font-size: 0.95rem;">${a.subject}</strong>
                      <span class="badge badge-warning" style="margin-left: 0.5rem;">Pending</span>
                    </div>
                    <span style="font-size: 0.75rem; color: var(--text-muted);">${a.requested_at ? new Date(a.requested_at).toLocaleTimeString() : ""}</span>
                  </div>
                  <div class="card-body">
                    <p style="font-size: 0.88rem; color: var(--text-primary); margin-bottom: 0.5rem;">${this.escapeHtml(a.reason || "Human confirmation requested.")}</p>
                    <div style="font-size: 0.78rem; color: var(--text-muted); font-family: var(--font-mono); margin-bottom: 1rem;">
                      Task: <a href="#" onclick="App.navigateTo('task-detail', '${a.task_id}')" style="color: #818cf8;">${a.task_id}</a> (${a.task_title})
                    </div>
                    <div style="display: flex; gap: 0.5rem;">
                      <button class="btn btn-success btn-sm" onclick="App.openApprovalModal('${a.task_id}', '${a.approval_id}', 'approve')">
                        Approve &amp; Advance
                      </button>
                      <button class="btn btn-danger btn-sm" onclick="App.openApprovalModal('${a.task_id}', '${a.approval_id}', 'reject')">
                        Reject
                      </button>
                    </div>
                  </div>
                </div>
              `).join("")}
            </div>
          `}
        </div>

        <div>
          <h4 style="font-size: 0.85rem; font-weight: 700; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.75rem;">
            Decided Approvals Log (${past.length})
          </h4>
          <div class="card">
            <div class="card-body" style="padding: 0;">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Approval ID</th>
                    <th>Task</th>
                    <th>Subject</th>
                    <th>State</th>
                    <th>Decided By</th>
                    <th>Note</th>
                  </tr>
                </thead>
                <tbody>
                  ${past.map((a) => `
                    <tr>
                      <td style="font-family: var(--font-mono);">${a.approval_id}</td>
                      <td><a href="#" onclick="App.navigateTo('task-detail', '${a.task_id}')" style="color: #818cf8;">${a.task_id}</a></td>
                      <td>${a.subject}</td>
                      <td><span class="badge badge-${a.state}">${a.state}</span></td>
                      <td>${a.approver || "human"}</td>
                      <td style="color: var(--text-muted); font-size: 0.78rem;">${this.escapeHtml(a.note || "—")}</td>
                    </tr>
                  `).join("")}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      `;
    } catch (e) {
      container.innerHTML = `<div style="color:var(--color-danger);padding:2rem;">Failed to load approvals: ${e.message}</div>`;
    }
  },

  // =========================================================================
  // View 5: Agent Fleet & Doctor
  // =========================================================================
  async renderAgents() {
    const container = document.getElementById("main-view-container");
    container.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-muted);">Inspecting agent fleet...</div>`;

    try {
      const agents = await API.getAgents();

      container.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; flex-wrap: wrap; gap: 1rem;">
          <div>
            <h3 style="font-size: 1.15rem; font-weight: 700; margin-bottom: 0.25rem;">Configured Agent Fleet (7 Supported Agents)</h3>
            <p style="font-size: 0.82rem; color: var(--text-muted);">
              Agents are workers. NAO discovers their native interfaces without hardcoding paths.
            </p>
          </div>
          <button class="btn btn-secondary btn-sm" onclick="App.runFleetDoctor()">
            ↻ Run Doctor Diagnostic
          </button>
        </div>

        <div class="agent-grid">
          ${agents.map((ag) => `
            <div class="agent-card">
              <div class="agent-card-header">
                <div style="display: flex; align-items: center; gap: 0.5rem;">
                  <span class="pulse-dot" style="background: ${ag.available ? "var(--color-success)" : "var(--text-muted)"}; box-shadow: none;"></span>
                  <span class="agent-name">${ag.agent}</span>
                </div>
                <span class="badge ${ag.available ? "badge-ok" : "badge-neutral"}">
                  ${ag.available ? "Available" : "Not Found"}
                </span>
              </div>

              <div style="font-size: 0.78rem; color: var(--text-muted);">
                ${ag.reason || "Ready for dispatch"}
              </div>

              ${ag.executable ? `
                <div style="font-size: 0.72rem; font-family: var(--font-mono); color: #818cf8; word-break: break-all; background: var(--bg-surface); padding: 0.35rem 0.5rem; border-radius: var(--radius-sm);">
                  ${ag.executable}
                </div>
              ` : ""}

              <div>
                <div style="font-size: 0.7rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.35rem;">Capabilities</div>
                <div class="agent-tags">
                  ${(ag.capabilities || ["cli"]).map((c) => `<span class="tag">${c}</span>`).join("")}
                </div>
              </div>
            </div>
          `).join("")}
        </div>
      `;
    } catch (e) {
      container.innerHTML = `<div style="color:var(--color-danger);padding:2rem;">Failed to load agents: ${e.message}</div>`;
    }
  },

  async runFleetDoctor() {
    this.showToast("Running doctor discovery across PATH and shims...", "info");
    try {
      await API.runAgentDoctor();
      this.showToast("Doctor diagnostic finished", "success");
      this.renderAgents();
    } catch (e) {
      this.showToast(`Doctor check failed: ${e.message}`, "error");
    }
  },

  // =========================================================================
  // View 6: Workflow Recipes (Inline Recipe Box Launcher)
  // =========================================================================
  async renderRecipes() {
    const container = document.getElementById("main-view-container");
    container.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-muted);">Loading recipes...</div>`;

    try {
      const recipes = await API.getRecipes();

      container.innerHTML = `
        <div style="margin-bottom: 1.5rem;">
          <h3 style="font-size: 1.15rem; font-weight: 700; margin-bottom: 0.25rem;">Built-in Workflow Recipes</h3>
          <p style="font-size: 0.82rem; color: var(--text-muted);">
            Workflows are data. Each recipe defines an ordered state machine sequence with independent reviewer isolation.
          </p>
        </div>

        <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1.25rem;">
          ${recipes.map((r) => {
            if (this.activeRecipeLauncher === r.name) {
              return `
                <div class="card recipe-active-card" style="border: 2px solid var(--color-primary); background: var(--bg-surface); box-shadow: 0 4px 20px rgba(99, 102, 241, 0.25);">
                  <div class="card-header" style="background: rgba(99, 102, 241, 0.12); border-bottom: 1px solid rgba(99, 102, 241, 0.25);">
                    <div>
                      <strong style="color: var(--color-primary); font-size: 0.95rem;">⚡ Run Recipe: ${this.escapeHtml(r.name)}</strong>
                      <div style="font-size: 0.72rem; color: var(--text-muted);">${r.step_count} Steps • ${r.mutating ? "Mutates Code" : "Read-Only"}</div>
                    </div>
                    <button class="btn btn-secondary btn-sm" onclick="App.cancelRecipeBox()" title="Close box">✕ Close</button>
                  </div>
                  <div class="card-body">
                    <form onsubmit="App.submitRecipeTask(event, '${r.name}')" style="display: flex; flex-direction: column; gap: 0.75rem;">
                      <div>
                        <label style="display: block; font-size: 0.78rem; font-weight: 700; color: var(--text-secondary); margin-bottom: 0.3rem;">
                          Describe What to Build or Fix with this Recipe *
                        </label>
                        <textarea id="recipe-prompt-${r.name}" class="form-input" rows="3" placeholder="Enter instructions or engineering goal for ${r.name}..." style="resize: vertical; font-size: 0.85rem;" required></textarea>
                      </div>
                      <div>
                        <label style="display: block; font-size: 0.78rem; font-weight: 700; color: var(--text-secondary); margin-bottom: 0.3rem;">
                          Verification / Test Command (Optional)
                        </label>
                        <input type="text" id="recipe-test-${r.name}" class="form-input" placeholder="e.g. pytest tests/ or npm test" style="font-size: 0.82rem;" />
                      </div>
                      <div style="display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 0.35rem;">
                        <button type="button" class="btn btn-secondary btn-sm" onclick="App.cancelRecipeBox()">Cancel</button>
                        <button type="submit" class="btn btn-primary btn-sm" style="font-weight: 700;">🚀 Launch Recipe</button>
                      </div>
                    </form>
                  </div>
                </div>
              `;
            }

            return `
              <div class="card">
                <div class="card-header">
                  <div>
                    <strong style="font-size: 0.95rem;">${r.name}</strong>
                    <div style="font-size: 0.72rem; color: var(--text-muted);">${r.step_count} Steps ${r.mutating ? "• Mutates Code" : "• Read-Only"}</div>
                  </div>
                  <button class="btn btn-primary btn-sm" onclick="App.activateRecipeBox('${r.name}')">➕ Use Recipe</button>
                </div>
                <div class="card-body">
                  <p style="font-size: 0.82rem; color: var(--text-secondary); margin-bottom: 1rem;">${this.escapeHtml(r.description)}</p>
                  
                  <h5 style="font-size: 0.72rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.4rem;">Pipeline Stages</h5>
                  <ol style="padding-left: 1.2rem; font-size: 0.78rem; color: var(--text-muted); display: flex; flex-direction: column; gap: 0.2rem;">
                    ${r.steps.map((s) => `
                      <li><strong>${s.kind}</strong> ${s.role ? `(${s.role})` : ""} ${s.approval ? `<span style="color:var(--color-warning);">[Approval Gate]</span>` : ""}</li>
                    `).join("")}
                  </ol>
                </div>
              </div>
            `;
          }).join("")}
        </div>
      `;
    } catch (e) {
      container.innerHTML = `<div style="color:var(--color-danger);padding:2rem;">Failed to load recipes: ${e.message}</div>`;
    }
  },

  activateRecipeBox(recipeName) {
    this.activeRecipeLauncher = recipeName;
    this.renderRecipes();
    setTimeout(() => {
      const el = document.getElementById(`recipe-prompt-${recipeName}`);
      if (el && typeof el.focus === "function") el.focus();
    }, 60);
  },

  cancelRecipeBox() {
    this.activeRecipeLauncher = null;
    this.renderRecipes();
  },

  async submitRecipeTask(e, recipeName) {
    e.preventDefault();
    const promptEl = document.getElementById(`recipe-prompt-${recipeName}`);
    const testEl = document.getElementById(`recipe-test-${recipeName}`);
    const prompt = promptEl ? promptEl.value.trim() : "";
    const testCmd = testEl ? testEl.value.trim() : "";

    if (!prompt) {
      this.showToast("Please enter task requirements for this recipe", "error");
      return;
    }

    try {
      this.showToast(`Launching ${recipeName} workflow...`, "info");
      const res = await API.createTask({
        title: prompt.slice(0, 70),
        description: prompt,
        recipe: recipeName,
        acceptance_criteria: ["Verify all stages pass", "Zero test regressions"],
        granted_permissions: ["read", "write", "shell", "git_read", "git_commit"],
        test_command: testCmd || null,
      });

      this.activeRecipeLauncher = null;
      this.newlyCreatedTaskId = res.task_id;
      this.showToast(`Task ${res.task_id} launched with recipe ${recipeName}!`, "success");
      this.navigateTo("task-detail", res.task_id);
    } catch (err) {
      this.showToast(`Failed to launch recipe: ${err.message}`, "error");
    }
  },

  // =========================================================================
  // View 7: Audit Stream
  // =========================================================================
  async renderAudit() {
    const container = document.getElementById("main-view-container");
    container.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-muted);">Streaming audit log...</div>`;

    try {
      const events = await API.getAuditEvents(100);

      container.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.25rem; flex-wrap: wrap; gap: 1rem;">
          <div>
            <h3 style="font-size: 1.15rem; font-weight: 700; margin-bottom: 0.25rem;">Append-Only Audit Stream</h3>
            <p style="font-size: 0.82rem; color: var(--text-muted);">
              Every significant orchestration step is durably recorded with zero prose and secret redaction.
            </p>
          </div>
          <button class="btn btn-secondary btn-sm" onclick="App.renderAudit()">↻ Refresh Events</button>
        </div>

        <div class="card">
          <div class="card-body" style="padding: 0;">
            ${this.renderAuditTable(events)}
          </div>
        </div>
      `;
    } catch (e) {
      container.innerHTML = `<div style="color:var(--color-danger);padding:2rem;">Failed to load audit stream: ${e.message}</div>`;
    }
  },

  renderAuditTable(events) {
    if (!events || events.length === 0) {
      return `<div style="padding: 2.5rem; text-align: center; color: var(--text-muted);">No audit events recorded.</div>`;
    }

    return `
      <div class="data-table-container">
        <table class="data-table">
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Event Type</th>
              <th>Task</th>
              <th>Agent / Role</th>
              <th>Status</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            ${events.map((ev) => `
              <tr>
                <td style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-muted); white-space: nowrap;">
                  ${ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : "—"}
                </td>
                <td>
                  <span class="badge badge-neutral" style="font-size: 0.7rem; font-family: var(--font-mono);">
                    ${ev.event || ev.event_type}
                  </span>
                </td>
                <td>
                  ${ev.task_id ? `
                    <a href="#" onclick="App.navigateTo('task-detail', '${ev.task_id}')" style="font-family: var(--font-mono); color: #818cf8; font-size: 0.78rem;">
                      ${ev.task_id}
                    </a>
                  ` : "—"}
                </td>
                <td style="font-size: 0.78rem;">
                  ${ev.agent ? `<strong>${ev.agent}</strong>` : ""} ${ev.role ? `(${ev.role})` : ""}
                </td>
                <td>
                  <span class="badge badge-${(ev.status || "").toLowerCase()}" style="font-size: 0.68rem;">
                    ${ev.status || "ok"}
                  </span>
                </td>
                <td style="font-size: 0.78rem; color: var(--text-secondary); max-width: 320px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                  ${this.escapeHtml(ev.message || "")}
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  },

  // =========================================================================
  // View 8: Git & Workspaces
  // =========================================================================
  async renderGit() {
    const container = document.getElementById("main-view-container");
    container.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-muted);">Inspecting Git repository...</div>`;

    try {
      const git = await API.getGitStatus();
      const state = git.state || {};

      container.innerHTML = `
        <div style="margin-bottom: 1.5rem;">
          <h3 style="font-size: 1.15rem; font-weight: 700; margin-bottom: 0.25rem;">Git Baseline &amp; Worktree Isolation</h3>
          <p style="font-size: 0.82rem; color: var(--text-muted);">
            NAO owns Git and isolates code mutation in dedicated worktrees before verifying and merging.
          </p>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 1.5rem;">
          <div class="card">
            <div class="card-header"><span class="card-title">Repository Baseline</span></div>
            <div class="card-body" style="display: flex; flex-direction: column; gap: 0.75rem; font-size: 0.85rem;">
              <div>Workspace: <strong style="font-family: var(--font-mono); font-size: 0.78rem;">Workspace: Orchestrator</strong></div>
              <div>Current Branch: <strong style="color: #818cf8;">${state.branch || "main"}</strong></div>
              <div>Head Commit: <span style="font-family: var(--font-mono); font-size: 0.78rem; color: var(--text-muted);">${state.head || "—"}</span></div>
              <div>
                Working Tree State: 
                <span class="badge ${state.dirty ? "badge-warning" : "badge-ok"}">
                  ${state.dirty ? "Dirty (Uncommitted Changes)" : "Clean"}
                </span>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><span class="card-title">Safety Guarantees</span></div>
            <div class="card-body" style="font-size: 0.82rem; color: var(--text-secondary); line-height: 1.6;">
              <ul style="padding-left: 1.2rem; display: flex; flex-direction: column; gap: 0.4rem;">
                <li>Implementation agents only run in isolated worktrees (<code>.nao-worktrees/</code>).</li>
                <li>Target tree is never touched before passing tests and review.</li>
                <li>Merging stops cleanly if the target repository has uncommitted changes.</li>
                <li>Commit and Merge approvals are strictly separate human gates.</li>
              </ul>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <span class="card-title">Uncommitted / Modified Files (${(state.modified || []).length + (state.untracked || []).length})</span>
          </div>
          <div class="card-body">
            ${(state.modified && state.modified.length > 0) || (state.untracked && state.untracked.length > 0) ? `
              <ul style="list-style: none; font-family: var(--font-mono); font-size: 0.8rem; display: flex; flex-direction: column; gap: 0.3rem;">
                ${(state.modified || []).map((f) => `<li style="color: var(--color-warning);">MODIFIED: ${f}</li>`).join("")}
                ${(state.untracked || []).map((f) => `<li style="color: var(--color-info);">UNTRACKED: ${f}</li>`).join("")}
              </ul>
            ` : `<div style="color: var(--text-muted); font-size: 0.82rem;">Working directory is fully committed and clean.</div>`}
          </div>
        </div>
      `;
    } catch (e) {
      container.innerHTML = `<div style="color:var(--color-danger);padding:2rem;">Failed to load Git status: ${e.message}</div>`;
    }
  },

  // =========================================================================
  // Actions: Run / Cancel Tasks
  // =========================================================================
  async triggerRunTask(taskId) {
    this.showToast(`Starting execution for ${taskId}...`, "info");
    try {
      const res = await API.runTask(taskId);
      if (res.success) {
        this.showToast(`Task ${taskId} is running!`, "success");
        if (this.currentView === "task-detail") {
          this.renderTaskDetail(taskId);
        } else {
          this.renderTasks();
        }
      } else {
        this.showToast(res.message || "Failed to start", "warning");
      }
    } catch (e) {
      this.showToast(`Error: ${e.message}`, "error");
    }
  },

  async triggerCancelTask(taskId) {
    if (!confirm(`Are you sure you want to cancel ${taskId}?`)) return;
    try {
      await API.cancelTask(taskId);
      this.showToast(`Task ${taskId} cancelled`, "info");
      this.renderTaskDetail(taskId);
    } catch (e) {
      this.showToast(`Cancel failed: ${e.message}`, "error");
    }
  },

  // =========================================================================
  // Modals & Forms: Create Task & Approval Decision
  // =========================================================================
  openCreateTaskModal(defaultRecipe = "feature_implementation") {
    const modal = document.getElementById("modal-create-task");
    if (!modal) return;
    const recipeSelect = document.getElementById("task-recipe-select");
    if (recipeSelect) recipeSelect.value = defaultRecipe;

    // Reset criteria container
    const criteriaContainer = document.getElementById("criteria-container");
    if (criteriaContainer) {
      criteriaContainer.innerHTML = `
        <div class="criterion-row" style="display: flex; gap: 0.5rem; margin-bottom: 0.4rem;">
          <input type="text" class="form-input criterion-input" placeholder="e.g. Unit tests pass with 0 regressions" />
        </div>
      `;
    }

    modal.classList.add("open");
    const titleInp = document.getElementById("task-title-input");
    if (titleInp) titleInp.focus();
  },

  addCriterionInput() {
    const container = document.getElementById("criteria-container");
    if (!container) return;
    const div = document.createElement("div");
    div.className = "criterion-row";
    div.style = "display: flex; gap: 0.5rem; margin-bottom: 0.4rem;";
    div.innerHTML = `
      <input type="text" class="form-input criterion-input" placeholder="e.g. Reject invalid moves" />
      <button type="button" class="btn btn-secondary btn-sm" onclick="this.parentElement.remove()">✕</button>
    `;
    container.appendChild(div);
  },

  async handleCreateTaskSubmit(e) {
    e.preventDefault();
    const title = document.getElementById("task-title-input").value.trim();
    const description = document.getElementById("task-desc-input").value.trim();
    const recipe = document.getElementById("task-recipe-select").value;
    const testCommand = document.getElementById("task-test-cmd-input").value.trim();

    const criteria = [];
    document.querySelectorAll(".criterion-input").forEach((inp) => {
      const val = inp.value.trim();
      if (val) criteria.push(val);
    });

    const permissions = [];
    document.querySelectorAll(".perm-checkbox:checked").forEach((cb) => {
      permissions.push(cb.value);
    });

    if (!description) {
      this.showToast("Task description is required", "error");
      return;
    }

    try {
      const result = await API.createTask({
        title,
        description,
        recipe,
        acceptance_criteria: criteria,
        granted_permissions: permissions,
        test_command: testCommand || null,
      });

      this.closeModal("modal-create-task");
      this.newlyCreatedTaskId = result.task_id;
      this.showToast(`Task ${result.task_id} added successfully!`, "success");

      if (this.currentView === "dashboard") {
        await this.renderDashboard();
      } else if (this.currentView === "tasks") {
        await this.renderTasks();
      } else {
        this.navigateTo("dashboard");
      }
    } catch (err) {
      this.showToast(`Failed to add task: ${err.message}`, "error");
    }
  },

  openApprovalModal(taskId, approvalId = null, defaultAction = "approve") {
    const modal = document.getElementById("modal-approval-decision");
    if (!modal) return;
    document.getElementById("decision-task-id").value = taskId;
    document.getElementById("decision-approval-id").value = approvalId || "";
    document.getElementById("decision-action-select").value = defaultAction;
    const byInput = document.getElementById("decision-by-input");
    if (byInput && this.currentUser) {
      byInput.value = this.currentUser.full_name || this.currentUser.username || "Admin";
    }
    modal.classList.add("open");
  },

  async handleDecisionSubmit(e) {
    e.preventDefault();
    const taskId = document.getElementById("decision-task-id").value;
    const approvalId = document.getElementById("decision-approval-id").value || null;
    const action = document.getElementById("decision-action-select").value;
    const by = document.getElementById("decision-by-input").value.trim() || (this.currentUser ? this.currentUser.full_name : "Admin");
    const note = document.getElementById("decision-note-input").value.trim();

    try {
      await API.decideApproval(taskId, {
        approval_id: approvalId,
        action,
        by,
        note,
      });
      this.closeModal("modal-approval-decision");
      this.showToast(`Decision recorded: ${action.toUpperCase()}`, "success");
      this.renderCurrentView();
    } catch (err) {
      this.showToast(`Approval decision failed: ${err.message}`, "error");
    }
  },

  closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.remove("open");
  },

  // =========================================================================
  // Utilities: Toast Notifications & HTML Escaping
  // =========================================================================
  showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    let icon = "ℹ";
    if (type === "success") icon = "✔";
    else if (type === "error") icon = "✖";
    else if (type === "warning") icon = "⚠";

    toast.innerHTML = `<span style="font-weight:bold;">${icon}</span><span>${this.escapeHtml(message)}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      setTimeout(() => toast.remove(), 200);
    }, 3200);
  },

  escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  },
};

window.App = App;
document.addEventListener("DOMContentLoaded", () => App.init());
