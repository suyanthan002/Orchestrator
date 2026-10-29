/**
 * NAO Orchestrator — REST API Client Wrapper
 */

const API = {
  baseUrl: "/api",

  async request(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    const defaultHeaders = {
      "Content-Type": "application/json",
      "Accept": "application/json",
    };

    const config = {
      ...options,
      headers: {
        ...defaultHeaders,
        ...(options.headers || {}),
      },
    };

    try {
      const response = await fetch(url, config);
      if (!response.ok) {
        let errMessage = `HTTP Error ${response.status}`;
        try {
          const errData = await response.json();
          if (errData && errData.detail) {
            errMessage = typeof errData.detail === "string" ? errData.detail : JSON.stringify(errData.detail);
          }
        } catch (_) {}
        throw new Error(errMessage);
      }
      return await response.json();
    } catch (err) {
      console.error(`API Error on ${endpoint}:`, err);
      throw err;
    }
  },

  // System & Telemetry
  getSystem() {
    return this.request("/system");
  },

  // Agents
  getAgents() {
    return this.request("/agents");
  },

  runAgentDoctor() {
    return this.request("/agents/doctor", { method: "POST" });
  },

  // Recipes
  getRecipes() {
    return this.request("/recipes");
  },

  // Tasks
  getTasks(status = null) {
    const query = status ? `?status=${encodeURIComponent(status)}` : "";
    return this.request(`/tasks${query}`);
  },

  getTask(taskId) {
    return this.request(`/tasks/${encodeURIComponent(taskId)}`);
  },

  createTask(data) {
    return this.request("/tasks", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  runTask(taskId) {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/run`, {
      method: "POST",
    });
  },

  cancelTask(taskId) {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST",
    });
  },

  // Approvals
  getApprovals() {
    return this.request("/approvals");
  },

  decideApproval(taskId, payload) {
    return this.request(`/approvals/${encodeURIComponent(taskId)}/decide`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  // Git & Workspaces
  getGitStatus(repo = null) {
    const query = repo ? `?repo=${encodeURIComponent(repo)}` : "";
    return this.request(`/git/status${query}`);
  },

  // Audit Events
  getAuditEvents(limit = 100, taskId = null) {
    let query = `?limit=${limit}`;
    if (taskId) query += `&task_id=${encodeURIComponent(taskId)}`;
    return this.request(`/audit${query}`);
  },
};

window.API = API;
