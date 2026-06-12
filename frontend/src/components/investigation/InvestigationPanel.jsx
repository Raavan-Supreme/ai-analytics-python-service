import { useState } from "react";
import toast from "react-hot-toast";
import { parseApiError, queryApi } from "../../services/api";

export default function InvestigationPanel() {
  const [dataset, setDataset] = useState("");
  const [metric, setMetric] = useState("");
  const [result, setResult] = useState(null);

  const run = async (mode) => {
    try {
      const payload = { dataset, metric };
      const response = mode === "root" ? await queryApi.rootCause(payload)
        : mode === "anomaly" ? await queryApi.anomalies(payload)
        : await queryApi.forecast(payload);
      setResult(response.data);
    } catch (error) {
      toast.error(parseApiError(error, "Investigation failed"));
    }
  };

  return (
    <section className="stack">
      <div className="panel">
        <h2>Investigation Panel</h2>
        <div className="form-grid">
          <label>Dataset<input value={dataset} onChange={(e) => setDataset(e.target.value)} placeholder="sales_2026.csv" /></label>
          <label>Metric<input value={metric} onChange={(e) => setMetric(e.target.value)} placeholder="revenue" /></label>
        </div>
        <div className="button-row">
          <button className="btn-primary" onClick={() => run("root")}>Root Cause</button>
          <button className="btn-primary" onClick={() => run("anomaly")}>Anomalies</button>
          <button className="btn-primary" onClick={() => run("forecast")}>Forecast</button>
        </div>
      </div>
      <div className="panel">
        <h3>Output</h3>
        <pre>{result ? JSON.stringify(result, null, 2) : "No investigation run yet."}</pre>
      </div>
    </section>
  );
}
