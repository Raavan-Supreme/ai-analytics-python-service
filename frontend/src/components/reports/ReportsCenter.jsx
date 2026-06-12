import { useState } from "react";
import toast from "react-hot-toast";
import { actionApi, parseApiError, reportApi } from "../../services/api";

export default function ReportsCenter() {
  const [title, setTitle] = useState("Weekly Analysis Summary");

  const generatePdf = async () => {
    try {
      const { data } = await reportApi.create({ title });
      const blob = new Blob([data], { type: "application/pdf" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "analytics-report.pdf";
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success("Report generated");
    } catch (error) {
      toast.error(parseApiError(error, "Report generation failed"));
    }
  };

  const triggerAction = async (channel) => {
    try {
      await actionApi.trigger({ channel, message: `New report: ${title}` });
      toast.success(`${channel} action triggered`);
    } catch (error) {
      toast.error(parseApiError(error, "Action failed"));
    }
  };

  return (
    <section className="stack">
      <div className="panel">
        <h2>Reports Center</h2>
        <label>
          Report title
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <div className="button-row">
          <button className="btn-primary" onClick={generatePdf}>Generate PDF</button>
          <button className="btn-primary" onClick={() => triggerAction("slack")}>Send Slack</button>
          <button className="btn-primary" onClick={() => triggerAction("email")}>Send Email</button>
          <button className="btn-primary" onClick={() => triggerAction("webhook")}>Call Webhook</button>
        </div>
      </div>
    </section>
  );
}
