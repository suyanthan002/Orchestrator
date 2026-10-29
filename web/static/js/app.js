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

  init() {
    this.bindEvents();
    this.loadSystemData();
    this.renderCurrentView();
    this.startAutoRefresh();
  },

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
    this.currentView = view;
    if (taskId) {
      this.selectedTaskId = taskId;
    }

    // Update nav active classes
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
      tasks: "Workflows & Tasks",
      "task-detail": `Task Inspector — ${this.selectedTaskId || ""}`,
      approvals: "Human Approval Gatekeeper",
      agents: "Agent Fleet & Diagnostics",
      recipes: "Workflow Recipes",
      audit: "Audit Trail Stream",
      git: "Git Repository & Worktrees",
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
      // Auto-refresh the current view if applicable
      if (this.currentView === "dashboard") {
        this.renderDashboard(false);
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

      // Update repo pill
      const repoEl = document.getElementById("header-repo-path");
      if (repoEl) {
        repoEl.textContent = data.default_repo_path || data.project_root || "NAO Target";
        repoEl.title = data.default_repo_path || data.project_root;
      }

      // Update RAM meter in sidebar
      if (data.resources && data.resources.available_ram_percent !== null) {
        const usedPercent = Math.max(0, Math.min(100, Math.round(100 - data.resources.available_ram_percent)));
        const ramFill = document.getElementById("sidebar-ram-fill");
        const ramText = document.getElementById("sidebar-ram-text");
        if (ramFill) ramFill.style.width = `${usedPercent}%`;
        if (ramText) ramText.textContent = `${usedPercent}% Used`;
      }

      // Update pending approvals count badge
      const approvals = await API.getApprovals();
      const pending = approvals.filter((a) => a.state === "pending");
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

    switch (this.currentView) {
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
          this.navigateTo("tasks");
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
        this.renderDashboard();
    }
  },

  // =========================================================================
  // View 1: Executive Dashboard
  // =========================================================================
  async renderDashboard(showLoading = true) {
    const container = document.getElementById("main-view-container");
    if (showLoading) {
      container.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-muted);">Loading dashboard analytics...</div>`;
    }

    try {
      const [tasks, agents, approvals, auditEvents] = await Promise.all([
        API.getTasks(),
        API.getAgents(),
        API.getApprovals(),
        API.getAuditEvents(10),
      ]);

      const activeTasks = tasks.filter((t) => t.is_running || ["implementing", "testing", "reviewing", "analyzing"].includes(t.state));
      const pendingApprovals = approvals.filter((a) => a.state === "pending");
      const availableAgents = agents.filter((a) => a.available);

      container.innerHTML = `
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
            <div class="metric-sub">Available & verified agents</div>
          </div>

          <div class="metric-card" style="--metric-color: var(--color-info);">
            <div class="metric-header">
              <span>Concurrency Gate</span>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/></svg>
            </div>
            <div class="metric-value">1 Max</div>
            <div class="metric-sub">RAM-gated execution safe</div>
          </div>
        </div>

        <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 1.5rem; margin-bottom: 1.75rem;">
          <!-- Recent Tasks -->
          <div class="card">
            <div class="card-header">
              <span class="card-title">Recent Tasks & Workflows</span>
              <button class="btn btn-secondary btn-sm" onclick="App.navigateTo('tasks')">View All</button>
            </div>
            <div class="card-body" style="padding: 0;">
              ${this.renderTasksTable(tasks.slice(0, 6))}
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
            <button class="btn btn-primary btn-sm" onclick="App.openCreateTaskModal()">+ New Task</button>
          </div>
        </div>

        <div class="card">
          <div class="card-body" style="padding: 0;" id="tasks-table-wrapper">
            ${this.renderTasksTable(tasks)}
          </div>
        </div>
      `;

      // Filter button handlers
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

      // Search input handler
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
      return `<div style="padding: 2.5rem; text-align: center; color: var(--text-muted);">No orchestration tasks found. Click <strong>+ New Task</strong> to create one.</div>`;
    }

    return `
      <div class="data-table-container">
        <table class="data-table">
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
              <tr style="cursor: pointer;" onclick="App.navigateTo('task-detail', '${t.task_id}')">
                <td>
                  <span style="font-family: var(--font-mono); font-size: 0.78rem; font-weight: 700; color: #818cf8;">
                    ${t.task_id}
                  </span>
                </td>
                <td>
                  <div style="font-weight: 600; color: var(--text-primary); max-width: 320px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                    ${this.escapeHtml(t.title || t.description || "Untitled Task")}
                  </div>
                  <div style="font-size: 0.72rem; color: var(--text-muted); font-family: var(--font-mono);">
                    ${t.repo_path || "Default Repository"}
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

      // Pipeline state nodes
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
              Repository: ${task.repo_path || "Default"}
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
              Review & Decide
            </button>
          </div>
        ` : ""}

        <!-- Detail Tabs Navigation -->
        <div class="tabs-nav">
          <button class="tab-btn ${this.activeDetailTab === "overview" ? "active" : ""}" onclick="App.setDetailTab('overview')">
            Overview & Criteria (${task.acceptance_criteria.length})
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
              <div class="card-header"><span class="card-title">Security & Permissions</span></div>
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
                        Approve & Advance
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
  // View 6: Workflow Recipes
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
          ${recipes.map((r) => `
            <div class="card">
              <div class="card-header">
                <div>
                  <strong style="font-size: 0.95rem;">${r.name}</strong>
                  <div style="font-size: 0.72rem; color: var(--text-muted);">${r.step_count} Steps ${r.mutating ? "• Mutates Code" : "• Read-Only"}</div>
                </div>
                <button class="btn btn-primary btn-sm" onclick="App.openCreateTaskModal('${r.name}')">Use</button>
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
          `).join("")}
        </div>
      `;
    } catch (e) {
      container.innerHTML = `<div style="color:var(--color-danger);padding:2rem;">Failed to load recipes: ${e.message}</div>`;
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
          <h3 style="font-size: 1.15rem; font-weight: 700; margin-bottom: 0.25rem;">Git Baseline & Worktree Isolation</h3>
          <p style="font-size: 0.82rem; color: var(--text-muted);">
            NAO owns Git and isolates code mutation in dedicated worktrees before verifying and merging.
          </p>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 1.5rem;">
          <div class="card">
            <div class="card-header"><span class="card-title">Repository Baseline</span></div>
            <div class="card-body" style="display: flex; flex-direction: column; gap: 0.75rem; font-size: 0.85rem;">
              <div>Path: <strong style="font-family: var(--font-mono); font-size: 0.78rem;">${state.repo_path || "—"}</strong></div>
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

    // Collect criteria
    const criteria = [];
    document.querySelectorAll(".criterion-input").forEach((inp) => {
      const val = inp.value.trim();
      if (val) criteria.push(val);
    });

    // Collect permissions
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
      this.showToast(`Task ${result.task_id} created successfully!`, "success");
      this.navigateTo("task-detail", result.task_id);
    } catch (err) {
      this.showToast(`Failed to create task: ${err.message}`, "error");
    }
  },

  openApprovalModal(taskId, approvalId = null, defaultAction = "approve") {
    const modal = document.getElementById("modal-approval-decision");
    if (!modal) return;
    document.getElementById("decision-task-id").value = taskId;
    document.getElementById("decision-approval-id").value = approvalId || "";
    document.getElementById("decision-action-select").value = defaultAction;
    modal.classList.add("open");
  },

  async handleDecisionSubmit(e) {
    e.preventDefault();
    const taskId = document.getElementById("decision-task-id").value;
    const approvalId = document.getElementById("decision-approval-id").value || null;
    const action = document.getElementById("decision-action-select").value;
    const by = document.getElementById("decision-by-input").value.trim() || "Chief Architect";
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
