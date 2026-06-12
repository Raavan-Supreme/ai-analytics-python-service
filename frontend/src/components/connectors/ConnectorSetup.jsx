import { useState } from "react";
import toast from "react-hot-toast";
import { connectorApi, parseApiError } from "../../services/api";

const CONNECTOR_CACHE_PREFIX = "connectors-cache-";

const cacheConnector = (tenantId, connector) => {
  if (!tenantId || !connector || typeof connector !== "object") return;
  const key = `${CONNECTOR_CACHE_PREFIX}${tenantId}`;
  let existing = [];
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || "[]");
    existing = Array.isArray(parsed) ? parsed : [];
  } catch {
    existing = [];
  }

  const next = [...existing, connector];
  const deduped = next.filter((item, idx, arr) => {
    const id = item?.id;
    if (id != null) {
      return arr.findIndex((candidate) => candidate?.id === id) === idx;
    }
    const fingerprint = `${item?.name || ""}|${item?.host || ""}|${item?.database || ""}`;
    return arr.findIndex((candidate) => `${candidate?.name || ""}|${candidate?.host || ""}|${candidate?.database || ""}` === fingerprint) === idx;
  });

  localStorage.setItem(key, JSON.stringify(deduped));
  console.log("[AISX][ConnectorSetup] cacheConnector", {
    tenantId,
    cachedCount: deduped.length,
    connector
  });
};

export default function ConnectorSetup({ tenant, onSelectView }) {
  const [form, setForm] = useState({
    name: "",
    type: "postgresql",
    host: "",
    port: "",
    database: "",
    username: "",
    password: ""
  });

  const save = async (event) => {
    event.preventDefault();
    console.log("[AISX][ConnectorSetup] save:start", {
      tenantId: tenant?.id,
      payload: { ...form, password: form.password ? "***" : "" }
    });
    if (!tenant?.id) {
      toast.error("Select a workspace before saving a connector");
      console.warn("[AISX][ConnectorSetup] save:blocked - missing tenant");
      return;
    }

    try {
      const response = await connectorApi.create({ ...form, tenantId: tenant.id });
      const created = response?.data?.data || response?.data || null;
      console.log("[AISX][ConnectorSetup] save:success", {
        tenantId: tenant.id,
        response: response?.data,
        created
      });
      toast.success("Connector saved");
      setForm({ ...form, name: "", host: "", port: "", database: "", username: "", password: "" });
      cacheConnector(tenant.id, created);
      onSelectView?.("workspace");
      setTimeout(() => {
        console.log("[AISX][ConnectorSetup] dispatch sources-updated", {
          tenantId: tenant.id,
          hasCreatedConnector: Boolean(created)
        });
        window.dispatchEvent(new CustomEvent("sources-updated", {
          detail: { tenantId: tenant.id, connectors: created ? [created] : [] }
        }));
      }, 0);
    } catch (error) {
      console.error("[AISX][ConnectorSetup] save:error", {
        tenantId: tenant?.id,
        error,
        response: error?.response?.data
      });
      toast.error(parseApiError(error, "Failed to save connector"));
    }
  };

  return (
    <section className="panel">
      <h2>Connector Setup</h2>
      <p>
        After saving a connector, go to the <strong>Workspace</strong> tab and ask your question in
        <strong> Ask Your Data</strong>. Example: "compare monthly revenue for the last 6 months".
      </p>
      <form onSubmit={save} className="form-grid">
        <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
        <label>Type
          <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
            <option value="postgresql">PostgreSQL</option>
            <option value="mysql">MySQL</option>
            <option value="snowflake">Snowflake</option>
            <option value="sqlserver">SQL Server</option>
          </select>
        </label>
        <label>Host<input value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} required /></label>
        <label>Port<input value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} required /></label>
        <label>Database<input value={form.database} onChange={(e) => setForm({ ...form, database: e.target.value })} required /></label>
        <label>User<input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required /></label>
        <label>Password<input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required /></label>
        <button className="btn-primary">Save connector</button>
      </form>
    </section>
  );
}
