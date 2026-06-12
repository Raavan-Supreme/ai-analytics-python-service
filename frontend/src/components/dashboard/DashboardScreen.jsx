import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const sample = [
  { name: "Mon", value: 120 },
  { name: "Tue", value: 98 },
  { name: "Wed", value: 132 },
  { name: "Thu", value: 169 },
  { name: "Fri", value: 144 }
];

export default function DashboardScreen() {
  return (
    <section className="stack">
      <div className="panel">
        <h2>Dashboard</h2>
        <p>Saved charts and KPI snapshots for your workspace.</p>
      </div>
      <div className="panel chart-panel">
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={sample}>
            <CartesianGrid strokeDasharray="4 4" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip />
            <Bar dataKey="value" fill="var(--accent)" radius={[8, 8, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
