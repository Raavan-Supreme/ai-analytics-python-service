import { BarChart3, Bot, Cable, FileStack, FolderUp, LogOut, SearchCheck, ShieldAlert } from "lucide-react";

const nav = [
  { id: "workspace", label: "Workspace", icon: Bot },
  { id: "upload", label: "Upload", icon: FolderUp },
  { id: "connectors", label: "Connectors", icon: Cable },
  { id: "rag", label: "RAG Library", icon: FileStack },
  { id: "investigation", label: "Investigation", icon: SearchCheck },
  { id: "dashboard", label: "Dashboard", icon: BarChart3 },
  { id: "reports", label: "Reports", icon: ShieldAlert }
];

export default function MainLayout({ tenant, activeView, onSelectView, onLogout, children }) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <h3>{tenant?.name || "Workspace"}</h3>
        <nav>
          {nav.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={activeView === item.id ? "active" : ""}
                onClick={() => onSelectView(item.id)}
              >
                <Icon size={16} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <button className="logout" onClick={onLogout}>
          <LogOut size={16} />
          <span>Logout</span>
        </button>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}
