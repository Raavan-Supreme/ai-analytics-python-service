import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { parseApiError, tenantApi } from "../../services/api";

export default function TenantScreen({ onSelectTenant }) {
  const [tenants, setTenants] = useState([]);
  const [name, setName] = useState("");

  const loadTenants = async () => {
    try {
      const { data } = await tenantApi.list();
      const tenantList = data?.data || data || [];
      setTenants(Array.isArray(tenantList) ? tenantList : []);
    } catch (error) {
      setTenants([]);
      toast.error(parseApiError(error, "Failed to load workspaces"));
    }
  };

  useEffect(() => {
    loadTenants();
  }, []);

  const createTenant = async (event) => {
    event.preventDefault();
    if (!name.trim()) return;
    try {
      const { data } = await tenantApi.create({ name });
      const createdTenant = data?.data || data;
      setName("");
      toast.success("Workspace created");
      localStorage.setItem("tenant", JSON.stringify(createdTenant));
      onSelectTenant(createdTenant);
    } catch (error) {
      toast.error(parseApiError(error, "Could not create workspace"));
    }
  };

  return (
    <div className="tenant-shell">
      <div className="tenant-card">
        <h2>Select Workspace</h2>
        <div className="tenant-list">
          {tenants.length === 0 ? <p>No workspaces yet.</p> : tenants.map((item) => (
            <button
              key={item.id}
              onClick={() => {
                localStorage.setItem("tenant", JSON.stringify(item));
                onSelectTenant(item);
              }}
            >
              {item.name}
            </button>
          ))}
        </div>

        <form onSubmit={createTenant}>
          <label>
            Create workspace
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme Analytics" />
          </label>
          <button className="btn-primary">Create and continue</button>
        </form>
      </div>
    </div>
  );
}
