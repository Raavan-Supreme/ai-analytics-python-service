import { useEffect, useMemo, useState } from "react";
import AuthScreen from "./components/auth/AuthScreen";
import TenantScreen from "./components/workspace/TenantScreen";
import MainLayout from "./components/layout/MainLayout";
import WorkspaceScreen from "./components/upload/WorkspaceScreen";
import UploadScreen from "./components/upload/UploadScreen";
import ConnectorSetup from "./components/connectors/ConnectorSetup";
import RagLibrary from "./components/rag/RagLibrary";
import InvestigationPanel from "./components/investigation/InvestigationPanel";
import DashboardScreen from "./components/dashboard/DashboardScreen";
import ReportsCenter from "./components/reports/ReportsCenter";

const views = {
  workspace: WorkspaceScreen,
  upload: UploadScreen,
  connectors: ConnectorSetup,
  rag: RagLibrary,
  investigation: InvestigationPanel,
  dashboard: DashboardScreen,
  reports: ReportsCenter
};

export default function App() {
  const [token, setToken] = useState(() => {
    const saved = localStorage.getItem("token");
    if (!saved || saved === "undefined" || saved === "null") {
      return null;
    }
    return saved;
  });
  const [tenant, setTenant] = useState(() => {
    const savedTenant = localStorage.getItem("tenant");
    if (!savedTenant || savedTenant === "undefined" || savedTenant === "null") {
      return null;
    }
    try {
      const parsed = JSON.parse(savedTenant);
      return parsed?.id ? parsed : null;
    } catch {
      localStorage.removeItem("tenant");
      return null;
    }
  });
  const [activeView, setActiveView] = useState("workspace");

  const selectTenant = (nextTenant) => {
    if (!nextTenant) return;
    localStorage.setItem("tenant", JSON.stringify(nextTenant));
    setTenant(nextTenant);
    setActiveView("workspace");
  };

  const isAuthenticated = Boolean(token);
  const ActiveView = useMemo(() => views[activeView] || WorkspaceScreen, [activeView]);

  useEffect(() => {
    const handleAuthExpired = () => {
      setToken(null);
      setTenant(null);
      setActiveView("workspace");
    };

    window.addEventListener("auth-expired", handleAuthExpired);
    return () => window.removeEventListener("auth-expired", handleAuthExpired);
  }, []);

  if (!isAuthenticated) {
    return (
      <AuthScreen
        onAuth={(nextToken) => {
          if (!nextToken) return;
          localStorage.setItem("token", nextToken);
          setToken(nextToken);
          const savedTenant = localStorage.getItem("tenant");
          if (savedTenant) {
            try {
              setTenant(JSON.parse(savedTenant));
            } catch {
              localStorage.removeItem("tenant");
            }
          }
        }}
      />
    );
  }

  if (!tenant) {
    return <TenantScreen onSelectTenant={selectTenant} />;
  }

  return (
    <MainLayout
      tenant={tenant}
      activeView={activeView}
      onSelectView={setActiveView}
      onLogout={() => {
        localStorage.removeItem("token");
        localStorage.removeItem("tenant");
        setTenant(null);
        setToken(null);
      }}
    >
      <ActiveView tenant={tenant} onSelectView={setActiveView} />
    </MainLayout>
  );
}
