import { useState } from "react";
import toast from "react-hot-toast";
import { authApi, parseApiError } from "../../services/api";

export default function AuthScreen({ onAuth }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({
    name: "",
    email: "",
    password: ""
  });
  const [loading, setLoading] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setLoading(true);
    try {
      if (mode === "register") {
        await authApi.register(form);
        toast.success("Account created, please log in");
        setMode("login");
      } else {
        const { data } = await authApi.login(form);
        const token = data?.data?.accessToken || data?.accessToken || data?.token;
        if (!token) {
          throw new Error("No access token returned by server");
        }
        onAuth(token);
        toast.success("Signed in successfully");
      }
    } catch (error) {
      toast.error(parseApiError(error, "Authentication failed"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <h1>AI Analytics Suite X</h1>
        <p>Investigate your data with guided AI and deterministic analytics.</p>

        <div className="tabs">
          <button className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>Login</button>
          <button className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>Register</button>
        </div>

        <form onSubmit={submit}>
          {mode === "register" && (
            <label>
              Name
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
            </label>
          )}
          <label>
            Email
            <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
          </label>
          <label>
            Password
            <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
          </label>
          <button className="btn-primary" disabled={loading}>{loading ? "Please wait..." : mode === "login" ? "Sign in" : "Create account"}</button>
        </form>
      </div>
    </div>
  );
}
