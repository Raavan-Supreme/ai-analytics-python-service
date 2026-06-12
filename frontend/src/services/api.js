import axios from "axios";

const backend = import.meta.env.VITE_BACKEND_URL || "http://localhost:8090/api";
const python = import.meta.env.VITE_PYTHON_URL || "http://localhost:8000";

const api = axios.create({
  baseURL: backend
});

export function parseApiError(error, fallback = "Something went wrong") {
  if (error?.response?.status === 401) {
    return "Session expired. Please sign in again.";
  }
  const responseData = error?.response?.data;
  if (typeof responseData === "string" && responseData.trim()) {
    return responseData;
  }
  if (responseData?.message) {
    return responseData.message;
  }
  if (responseData?.detail) {
    return responseData.detail;
  }
  if (responseData?.error) {
    return responseData.error;
  }
  if (error?.message) {
    return error.message;
  }
  return fallback;
}

export function extractList(data, preferredKeys = []) {
  const isObject = (value) => value !== null && typeof value === "object";
  const walk = (payload) => {
    if (Array.isArray(payload)) return payload;
    if (!isObject(payload)) return null;

    const queue = [payload];
    const seen = new Set();
    let best = null;
    const keys = [
      ...preferredKeys,
      "data",
      "result",
      "payload",
      "content",
      "items",
      "records",
      "connectors",
      "files",
      "rows"
    ];

    while (queue.length > 0) {
      const current = queue.shift();
      if (!isObject(current) || seen.has(current)) continue;
      seen.add(current);

      for (const key of keys) {
        const value = current[key];
        if (Array.isArray(value) && (!best || value.length >= best.length)) {
          best = value;
        }
        if (isObject(value)) {
          queue.push(value);
        }
      }

      for (const value of Object.values(current)) {
        if (Array.isArray(value) && (!best || value.length > best.length)) {
          best = value;
        } else if (isObject(value)) {
          queue.push(value);
        }
      }
    }

    return best;
  };

  return walk(data) || [];
}

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem("token");
      localStorage.removeItem("tenant");
      window.dispatchEvent(new Event("auth-expired"));
    }
    return Promise.reject(error);
  }
);

const py = axios.create({
  baseURL: python
});

export const authApi = {
  login: (payload) => api.post("/auth/login", payload),
  register: (payload) => api.post("/auth/register", payload),
  me: () => api.get("/auth/me")
};

export const tenantApi = {
  list: () => api.get("/tenants"),
  create: (payload) => api.post("/tenants", payload)
};

export const fileApi = {
  uploadMany: (formData) => api.post("/files/upload-multiple", formData, {
    headers: { "Content-Type": "multipart/form-data" }
  }),
  uploadSingle: (formData) => api.post("/files/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" }
  }),
  list: (tenantId) => api.get("/files", { params: { tenantId } })
};

export const queryApi = {
  ask: (payload) => py.post("/nl-query", payload),
  rootCause: (payload) => py.post("/nl-root-cause", payload),
  anomalies: (payload) => py.post("/nl-anomalies", payload),
  forecast: (payload) => py.post("/nl-forecast", payload)
};

export const connectorApi = {
  list: (tenantId) => api.get("/connectors", { params: { tenantId } }),
  create: (payload) => api.post("/connectors", payload)
};

export const ragApi = {
  index: (payload) => py.post("/rag/index", payload),
  retrieve: (payload) => py.post("/rag/retrieve", payload),
  answer: (payload) => py.post("/rag/answer", payload)
};

export const actionApi = {
  trigger: (payload) => api.post("/actions/trigger", payload)
};

export const reportApi = {
  create: (payload) => api.post("/reports/generate", payload, { responseType: "blob" })
};
